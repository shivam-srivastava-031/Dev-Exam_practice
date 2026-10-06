"""Package the pre-built question bank and pre-trained models as GitHub Release assets.

Run locally after `python -m app.importer` has built everything:

    python deploy/package_release.py --tag data-v1      # writes ../release/ and deploy/release.json
    gh release create data-v1 ../release/* --title "..." --notes "..."

The deployment (deploy/fetch_artifacts.py) downloads exactly these files, checks
their SHA-256 against deploy/release.json, and never trains anything itself.

Assets:
  exam-bank.db.gz         question bank, search index tables, no learner data
  rag-index.tar.gz        int8 question vectors + float16 per-row scales
  topic-model.npz         trained topic classifier (plain NumPy)
  embedding-model.tar.gz  minishlab/potion-retrieval-32M (MIT), embeddings as float16
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
import zlib
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from app import config, rag  # noqa: E402

LEARNER_TABLES = ("attempts", "bookmarks", "mocks", "learner_params", "reviews", "settings")
MODEL_FILES = ("config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
               "special_tokens_map.json", "modules.json", "vocab.txt", "README.md")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def package_db(out: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "exam.db"
        src = sqlite3.connect(config.DB_PATH)
        dst = sqlite3.connect(clean)
        src.backup(dst)  # consistent copy even though the live file uses WAL
        src.close()
        dst.execute("PRAGMA journal_mode = DELETE")
        for qid, text in dst.execute("SELECT id, question FROM questions WHERE origin = 'ai'").fetchall():
            dst.execute("INSERT INTO questions_fts (questions_fts, rowid, question) VALUES ('delete', ?, ?)", (qid, text))
        dst.execute("DELETE FROM questions WHERE origin = 'ai'")
        dst.execute("DELETE FROM papers WHERE id LIKE 'ai-%'")
        for table in LEARNER_TABLES:
            dst.execute(f"DELETE FROM {table}")
        # Solutions are half the bank and are read one at a time: store them zlib-compressed
        # (122 -> 63 MB) so the deployed function stays under Vercel's 500 MB bundle limit.
        dst.executemany("UPDATE questions SET solution = ? WHERE id = ?",
                        [(zlib.compress(s.encode(), 9), qid) for qid, s in
                         dst.execute("SELECT id, solution FROM questions WHERE solution IS NOT NULL").fetchall()])
        dst.commit()
        dst.execute("VACUUM")
        counts = {"questions": dst.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
                  "papers": dst.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
                  "max_question_id": dst.execute("SELECT MAX(id) FROM questions").fetchone()[0]}
        dst.close()
        with clean.open("rb") as fin, gzip.open(out, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
    return counts


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def _npy(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    np.save(buf, array)
    return buf.getvalue()


def package_index(out: Path) -> dict:
    index_dir = config.INDEX_DIR
    vecs = np.load(index_dir / "vectors.npy").astype(np.float32)
    q8, scales = rag.quantize(vecs)
    meta = json.loads((index_dir / "meta.json").read_text())
    meta.update(quantized="int8 + float16 row scales", model=config.EMBED_MODEL)
    with tarfile.open(out, "w:gz") as tar:
        _add_bytes(tar, "vectors.npy", _npy(q8))
        _add_bytes(tar, "scales.npy", _npy(scales))
        _add_bytes(tar, "ids.npy", (index_dir / "ids.npy").read_bytes())
        _add_bytes(tar, "meta.json", json.dumps(meta, indent=2).encode())
    return {"vectors": int(q8.shape[0]), "dim": int(q8.shape[1])}


def package_embedding_model(out: Path) -> dict:
    from safetensors.numpy import load_file, save_file
    from huggingface_hub import snapshot_download

    folder = Path(snapshot_download(config.EMBED_MODEL, local_files_only=True))
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(out, "w:gz") as tar:
        tensors = {k: v.astype(np.float16) for k, v in load_file(folder / "model.safetensors").items()}
        half = Path(tmp) / "model.safetensors"
        save_file(tensors, half)  # float16 halves the size; query rankings are unchanged
        for name in MODEL_FILES:
            src = half if name == "model.safetensors" else folder / name
            if src.is_file():
                tar.add(src, arcname=name)
    return {"model": config.EMBED_MODEL, "license": "MIT", "dtype": "float16"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="GitHub Release tag, e.g. data-v1")
    parser.add_argument("--out", type=Path, default=BACKEND.parent / "release")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    steps = {
        "exam-bank.db.gz": package_db,
        "rag-index.tar.gz": package_index,
        "topic-model.npz": lambda out: shutil.copyfile(config.MODELS_DIR / "topic_model.npz", out) and {},
        "embedding-model.tar.gz": package_embedding_model,
    }
    assets = {}
    for name, build in steps.items():
        started = time.perf_counter()
        path = args.out / name
        info = build(path) or {}
        assets[name] = {"sha256": sha256(path), "bytes": path.stat().st_size, **info}
        print(f"{name:24s} {path.stat().st_size / 1e6:8.1f} MB  {time.perf_counter() - started:5.1f}s")

    manifest = {"repo": "shivam-srivastava-031/Dev-Exam_practice", "tag": args.tag,
                "created": time.strftime("%Y-%m-%d"), "assets": assets}
    (BACKEND / "deploy" / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote deploy/release.json for {args.tag}")


if __name__ == "__main__":
    main()
