"""Hybrid retrieval over the question bank: the R in RAG.

Dense: every PYQ is embedded locally with a model2vec static embedding model
(minishlab/potion-retrieval-32M, 512-d). The whole bank encodes in ~20 s on a laptop
CPU, with no API quota, and a query embeds in about a millisecond.
Sparse: BM25 from the SQLite FTS5 index the importer already maintains.
Both rankings are merged with Reciprocal Rank Fusion, so exact keyword hits
("Harappa") and paraphrases ("who built the Red Fort") both surface.

    python -m app.rag            # (re)build the vector index
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config, db

# The embedding model downloads into data/models, not the user's global HF cache.
os.environ.setdefault("HF_HOME", str(config.MODELS_DIR / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

_MARKUP = re.compile(r"\[IMAGE:[^\]]*\]|\[NOTE:|<[^>]+>|\*\*|__")
_STOP = frozenset("""a an the of in on at to for from by with and or is are was were be been being which what who
whom whose when where why how this that these those it its as into than then there their them they he she his her
you your i me my we our not no do does did can could will would shall should may might must following given select
option options statement statements correct incorrect true false find value choose identify most best one""".split())

RRF_K = 60


def embed_text(question: str, options: list[str]) -> str:
    """What gets embedded: the question plus its text options, without markup."""
    opts = " ; ".join(o for o in options if "[IMAGE" not in o)
    return _MARKUP.sub(" ", f"{question} || {opts}")[:1200]


_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            from model2vec import StaticModel  # heavy import, deferred until first use
            _model = StaticModel.from_pretrained(config.EMBED_MODEL)
        return _model


def _normalise(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def build_index(conn: sqlite3.Connection, index_dir: Path | None = None, log=print) -> dict:
    index_dir = index_dir or config.INDEX_DIR
    rows = conn.execute("SELECT id, question, options FROM questions WHERE origin = 'pyq' ORDER BY id").fetchall()
    if not rows:
        raise RuntimeError("no questions to index; run the importer first")
    model = get_model()
    started = time.perf_counter()
    texts = [embed_text(r[1], json.loads(r[2])) for r in rows]
    vecs = _normalise(model.encode(texts, batch_size=1024)).astype(np.float16)
    index_dir.mkdir(parents=True, exist_ok=True)
    np.save(index_dir / "vectors.npy", vecs)
    np.save(index_dir / "ids.npy", np.array([r[0] for r in rows], dtype=np.int64))
    meta = {"model": config.EMBED_MODEL, "count": len(rows), "dim": int(vecs.shape[1]),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.perf_counter() - started, 1)}
    (index_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    log(f"Indexed {len(rows):,} questions ({meta['dim']}-d, {config.EMBED_MODEL}) in {meta['seconds']}s")
    _cache.clear()
    return meta


@dataclass
class DenseIndex:
    ids: np.ndarray          # sorted int64 question ids
    vecs: np.ndarray         # float32 (stored as float16 on disk), row-aligned with ids
    meta: dict

    def position(self, qid: int) -> int | None:
        i = int(np.searchsorted(self.ids, qid))
        return i if i < len(self.ids) and self.ids[i] == qid else None

    def vector(self, qid: int) -> np.ndarray | None:
        i = self.position(qid)
        return None if i is None else self.vecs[i]

    def top(self, qvec: np.ndarray, k: int, mask: np.ndarray | None = None) -> list[tuple[int, float]]:
        scores = self.vecs @ qvec.astype(np.float32)
        if mask is not None:
            scores[~mask] = -np.inf
        k = min(k, int(np.isfinite(scores).sum()))
        if k <= 0:
            return []
        part = np.argpartition(-scores, k - 1)[:k]
        best = part[np.argsort(-scores[part])]
        return [(int(self.ids[i]), float(scores[i])) for i in best]


_cache: dict[str, DenseIndex] = {}


def get_index(index_dir: Path | None = None) -> DenseIndex | None:
    index_dir = index_dir or config.INDEX_DIR
    meta_path = index_dir / "meta.json"
    if not meta_path.is_file():
        return None
    key = f"{index_dir}:{meta_path.stat().st_mtime_ns}"
    if key not in _cache:
        _cache.clear()
        _cache[key] = DenseIndex(
            ids=np.load(index_dir / "ids.npy"),
            # ~300 MB as float32: one BLAS matrix-vector product per query (~20 ms).
            vecs=np.load(index_dir / "vectors.npy").astype(np.float32),
            meta=json.loads(meta_path.read_text()),
        )
    return _cache[key]


def query_vector(text: str) -> np.ndarray:
    return _normalise(get_model().encode([text])[0])


# --------------------------------------------------------------------------- hybrid search

FILTER_COLUMNS = ("exam", "stage", "subject", "chapter", "year")


def _filter_sql(filters: dict) -> tuple[str, list]:
    where, params = ["q.origin = 'pyq'"], []
    for col in FILTER_COLUMNS:
        if filters.get(col):
            where.append(f"q.{col} = ?")
            params.append(filters[col])
    return " AND ".join(where), params


def _allowed_mask(conn, index: DenseIndex, filters: dict) -> np.ndarray | None:
    if not any(filters.get(c) for c in FILTER_COLUMNS):
        return None
    cond, params = _filter_sql(filters)
    allowed = np.fromiter((r[0] for r in conn.execute(f"SELECT q.id FROM questions q WHERE {cond}", params)),
                          dtype=np.int64)
    return np.isin(index.ids, allowed, assume_unique=True)


def bm25(conn, query: str, k: int, filters: dict) -> list[int]:
    words = [w for w in re.findall(r"\w+", query.lower()) if w not in _STOP and len(w) > 1][:16]
    if not words:
        return []
    cond, params = _filter_sql(filters)
    match = " OR ".join(f'"{w}"' for w in words)
    rows = conn.execute(f"""
        SELECT q.id FROM questions_fts JOIN questions q ON q.id = questions_fts.rowid
        WHERE questions_fts MATCH ? AND {cond}
        ORDER BY bm25(questions_fts) LIMIT ?""", [match, *params, k]).fetchall()
    return [r[0] for r in rows]


def hybrid_search(conn, query: str, k: int = 20, filters: dict | None = None) -> list[dict]:
    """Rank PYQs for a free-text query. Each hit says which retriever found it."""
    filters = filters or {}
    sparse = bm25(conn, query, 100, filters)
    dense: list[tuple[int, float]] = []
    index = get_index()
    if index is not None:
        dense = index.top(query_vector(query), 100, _allowed_mask(conn, index, filters))
    fused: dict[int, dict] = {}
    for rank, (qid, sim) in enumerate(dense):
        fused[qid] = {"id": qid, "score": 1 / (RRF_K + rank), "similarity": round(sim, 4), "via": ["meaning"]}
    for rank, qid in enumerate(sparse):
        hit = fused.setdefault(qid, {"id": qid, "score": 0.0, "similarity": None, "via": []})
        hit["score"] += 1 / (RRF_K + rank)
        hit["via"].append("keyword")
    return sorted(fused.values(), key=lambda h: -h["score"])[:k]


def similar(conn, qid: int, k: int = 10, filters: dict | None = None) -> list[dict]:
    """Nearest PYQs to a question by meaning (falls back to keywords without an index)."""
    filters = filters or {}
    index = get_index()
    if index is None:
        row = conn.execute("SELECT question FROM questions WHERE id = ?", (qid,)).fetchone()
        ids = [i for i in bm25(conn, row[0], k + 1, filters) if i != qid] if row else []
        return [{"id": i, "similarity": None} for i in ids[:k]]
    vec = index.vector(qid)
    if vec is None:  # e.g. an AI-generated question: embed it on the fly
        row = conn.execute("SELECT question, options FROM questions WHERE id = ?", (qid,)).fetchone()
        if row is None:
            return []
        vec = query_vector(embed_text(row[0], json.loads(row[1])))
    hits = index.top(vec, k + 1, _allowed_mask(conn, index, filters))
    return [{"id": i, "similarity": round(s, 4)} for i, s in hits if i != qid][:k]


def status() -> dict:
    index = get_index()
    return {"ready": index is not None, **(index.meta if index else {"model": config.EMBED_MODEL})}


if __name__ == "__main__":
    conn = db.connect(config.DB_PATH)
    db.init(conn)
    build_index(conn)
    sys.exit(0)
