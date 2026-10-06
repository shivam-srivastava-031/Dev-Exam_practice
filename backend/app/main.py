"""HTTP API for the SSC practice app; also serves the built frontend when present.

Run with:  uvicorn app.main:app --reload   (from backend/)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, AsyncIterator, Callable, Iterator, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import ai, artifacts, config, db, engine, finetune, generator, learner, mocks, persistence, rag, topic_model
from .catalog import EXAMS, PATTERNS, PATTERNS_BY_ID, SUBJECTS, chapter_label, pattern_summary, stage_name


def _warm_up() -> None:
    """Load the embedding model, vector index and topic model before the first search needs them."""
    try:
        if rag.get_index() is not None:
            rag.query_vector("warm up")
        topic_model.load()
    except Exception:  # noqa: BLE001 - warming is best-effort; requests load lazily anyway
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = Path(app.state.db_path)
    await run_in_threadpool(prepare_data, db_path)
    conn = db.connect(db_path)
    db.init(conn)
    conn.close()
    if config.TURSO_URL:
        syncer = persistence.configure(persistence.remote_from_config(), db_path)
        await run_in_threadpool(syncer.pull)
    elif config.ON_VERCEL:
        logging.getLogger(__name__).warning(
            "TURSO_DATABASE_URL is not set: progress lives in /tmp and is lost when this instance stops")
    threading.Thread(target=_warm_up, daemon=True).start()
    yield
    persistence.reset()


def prepare_data(db_path: Path) -> None:
    """Deployed: stream the question bank and models from the GitHub Release into temp storage."""
    if config.USE_BUNDLE and not db_path.exists():
        artifacts.fetch(config.DATA_DIR, log=logging.getLogger(__name__).warning)


app = FastAPI(title="SSC Exam Practice", lifespan=lifespan)
app.state.db_path = config.DB_PATH


@app.middleware("http")
async def sync_progress(request: Request, call_next):
    """With Turso configured: refresh before an API request, save after a change.

    Vercel spreads one browser's requests over several instances. Each response carries the
    progress version it reflects (X-Progress-Version) and the browser sends back the newest it
    has seen, so an instance that is behind catches up before it answers.
    """
    syncer = persistence.get()
    if syncer is None or not request.url.path.startswith("/api/"):
        return await call_next(request)
    seen = request.headers.get("x-progress-version", "")
    try:
        await run_in_threadpool(syncer.pull_if_stale, int(seen) if seen.isdigit() else None)
    except (persistence.TursoError, httpx.HTTPError) as e:
        return JSONResponse({"detail": f"Could not reach the progress database: {e}"}, status_code=503)
    response = await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and response.status_code < 400:
        try:
            await run_in_threadpool(syncer.push)
        except (persistence.TursoError, httpx.HTTPError) as e:
            return JSONResponse({"detail": f"Your change was made but could not be saved: {e}"}, status_code=503)
    if syncer.version is not None:
        response.headers["X-Progress-Version"] = str(syncer.version)
    return response


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _in(ids: list[int]) -> str:
    return ",".join("?" * len(ids))


def public_question(r: sqlite3.Row) -> dict:
    """A question as the learner sees it before answering: no key, no solution."""
    return {
        "id": r["id"], "exam": r["exam"], "stage": r["stage"], "subject": r["subject"],
        "chapter": r["chapter"], "chapter_label": chapter_label(r["chapter"]), "year": r["year"],
        "question": r["question"], "options": json.loads(r["options"]),
    }


# --------------------------------------------------------------------------- catalogue

_meta_cache: dict[tuple, dict] = {}


@app.get("/api/meta")
def meta(conn: Conn) -> dict:
    # The catalogue only changes on re-import, which changes the row count or max id.
    stamp = (str(app.state.db_path), *conn.execute("SELECT COUNT(*), MAX(id) FROM questions").fetchone())
    if stamp not in _meta_cache:
        _meta_cache.clear()
        _meta_cache[stamp] = _build_meta(conn)
    return {**_meta_cache[stamp], "ai_enabled": ai.enabled()}


def _build_meta(conn: sqlite3.Connection) -> dict:
    counts = conn.execute("SELECT exam, stage, subject, COUNT(*) n FROM questions WHERE origin = 'pyq' "
                          "GROUP BY 1, 2, 3").fetchall()
    papers = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT exam, stage, COUNT(*) FROM papers WHERE id NOT LIKE 'ai-%' GROUP BY 1, 2")}
    exams = []
    for code, name in EXAMS.items():
        stages = []
        for stage in ("pre", "mains"):
            subjects = {r["subject"]: r["n"] for r in counts if r["exam"] == code and r["stage"] == stage}
            if subjects:
                stages.append({"code": stage, "name": stage_name(code, stage), "subjects": subjects,
                               "questions": sum(subjects.values()), "papers": papers.get((code, stage), 0)})
        if stages:
            exams.append({"code": code, "name": name, "stages": stages})
    subject_totals = {s: sum(r["n"] for r in counts if r["subject"] == s) for s in SUBJECTS}
    chapters = [
        {"subject": r["subject"], "chapter": r["chapter"], "label": chapter_label(r["chapter"]), "n": r["n"]}
        for r in conn.execute(
            "SELECT subject, chapter, COUNT(*) n FROM questions WHERE chapter IS NOT NULL AND origin = 'pyq' "
            "GROUP BY 1, 2 ORDER BY 1, 3 DESC")
    ]
    return {
        "exams": exams,
        "subjects": [{"code": c, "name": n, "count": subject_totals[c]} for c, n in SUBJECTS.items()],
        "chapters": chapters,
        "years": [r[0] for r in conn.execute("SELECT DISTINCT year FROM questions WHERE year IS NOT NULL ORDER BY 1 DESC")],
        "total_questions": sum(r["n"] for r in counts),
        "total_papers": sum(papers.values()),
        "ai_questions": conn.execute("SELECT COUNT(*) FROM questions WHERE origin = 'ai'").fetchone()[0],
    }


@app.get("/api/patterns")
def patterns(conn: Conn) -> list[dict]:
    pool = {(r[0], r[1], r[2]): r[3] for r in
            conn.execute("SELECT exam, stage, subject, COUNT(*) FROM questions WHERE origin = 'pyq' GROUP BY 1, 2, 3")}
    out = []
    for p in PATTERNS:
        summary = pattern_summary(p)
        summary["pool"] = {sec["subject"]: pool.get((p["exam"], p["stage"], sec["subject"]), 0)
                           for part in p["parts"] for sec in part["sections"]}
        out.append(summary)
    return out


# Selection Post holds its Matric / Higher Secondary / Graduation papers in the same shift.
_LEVELS = (("matric", "matric"), ("higher secondary", "hsc"), ("hsc", "hsc"), ("graduat", "graduation"))
_slug_cache: dict[str, object] = {}


def paper_slugs(conn: sqlite3.Connection) -> tuple[dict[str, str], dict[str, str]]:
    """Readable, stable URL slugs for real papers, e.g. 'ssc-cgl-2023-07-18-shift-4'.

    Returns (id -> slug, slug -> id). Clashes left after adding the level get -2, -3
    in paper-id order, so a slug never changes while the question bank stays the same.
    """
    stamp = conn.execute("SELECT COUNT(*), MAX(id) FROM papers WHERE id NOT LIKE 'ai-%'").fetchone()
    if _slug_cache.get("stamp") != tuple(stamp):
        by_id: dict[str, str] = {}
        taken: dict[str, int] = {}
        rows = conn.execute("SELECT id, exam, stage, held_on, shift, title FROM papers "
                            "WHERE id NOT LIKE 'ai-%' ORDER BY id").fetchall()
        for r in rows:
            base = f"{r['exam'].lower()}{'-mains' if r['stage'] == 'mains' else ''}-{r['held_on'] or 'undated'}"
            if r["shift"]:
                base += f"-shift-{r['shift']}"
            if r["exam"] == "SSC-Selection-Post":
                level = next((tag for word, tag in _LEVELS if word in r["title"].lower()), None)
                base += f"-{level}" if level else ""
            taken[base] = taken.get(base, 0) + 1
            by_id[r["id"]] = base if taken[base] == 1 else f"{base}-{taken[base]}"
        _slug_cache.update(stamp=tuple(stamp), by_id=by_id, by_slug={v: k for k, v in by_id.items()})
    return _slug_cache["by_id"], _slug_cache["by_slug"]  # type: ignore[return-value]


@app.get("/api/papers")
def papers(conn: Conn, exam: str, stage: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM papers WHERE exam = ? AND id NOT LIKE 'ai-%'", [exam]
    if stage:
        sql += " AND stage = ?"
        params.append(stage)
    rows = conn.execute(sql + " ORDER BY held_on DESC, shift", params).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for m in conn.execute("SELECT id, paper_id, submitted_at, score, max_score FROM mocks "
                          "WHERE paper_id IS NOT NULL ORDER BY created_at"):
        latest[m["paper_id"]] = m
    slugs, _ = paper_slugs(conn)
    out = []
    for r in rows:
        m = latest.get(r["id"])
        out.append({**dict(r), "slug": slugs.get(r["id"]), "last_mock": dict(m) if m else None})
    return out


# --------------------------------------------------------------------------- practice

STATUS_SQL = {
    "all": "1 = 1",
    "unattempted": "NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.id)",
    "attempted": "EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.id)",
    # Latest attempt was wrong: the question still needs work.
    "incorrect": "q.id IN (SELECT question_id FROM attempts GROUP BY question_id "
                 "HAVING MAX(id) = MAX(CASE WHEN is_correct = 0 THEN id END))",
    "bookmarked": "q.id IN (SELECT question_id FROM bookmarks)",
}

# Keyset pagination keys. The random key is a seeded bijection of the id (multiply
# by a constant, mod a prime above 2^32), so pages never repeat or skip even while
# answering changes which rows match an 'unattempted' filter.
ORDER_KEYS = {
    "random": "((q.id * 2654435761 + :seed) % 4294967291)",
    "sequential": "q.id",
    # Section order of a real paper, then question number; the id keeps keys unique.
    "paper": "((CASE q.subject WHEN 'REAS' THEN 1 WHEN 'GK' THEN 2 WHEN 'MATH' THEN 3 WHEN 'ENG' THEN 4 "
             "ELSE 5 END) * 1000000000 + COALESCE(q.n, 0) * 1000000 + q.id)",
}


def practice_items(conn: sqlite3.Connection, ids: list[int], extra: dict[int, dict] | None = None) -> list[dict]:
    """Questions as practice cards, in the given order, with the learner model's prediction."""
    if not ids:
        return []
    rows = {r["id"]: r for r in conn.execute(f"""
        SELECT q.*, p.title AS paper_title,
               EXISTS (SELECT 1 FROM bookmarks b WHERE b.question_id = q.id) AS bookmarked,
               (SELECT is_correct FROM attempts a WHERE a.question_id = q.id ORDER BY a.id DESC LIMIT 1) AS last_correct
        FROM questions q JOIN papers p ON p.id = q.paper_id WHERE q.id IN ({_in(ids)})""", ids)}
    model = learner.load(conn)
    out = []
    for qid in ids:
        r = rows.get(qid)
        if r is None:
            continue
        out.append({**public_question(r), "paper_title": r["paper_title"], "bookmarked": bool(r["bookmarked"]),
                    "last_correct": None if r["last_correct"] is None else bool(r["last_correct"]),
                    "origin": r["origin"], "predicted": round(model.predict(dict(r)), 3),
                    **(extra or {}).get(qid, {})})
    return out


@app.get("/api/questions")
def list_questions(
    conn: Conn,
    exam: str | None = None,
    stage: str | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    year: int | None = None,
    paper: str | None = None,
    status: Literal["all", "unattempted", "attempted", "incorrect", "bookmarked"] = "all",
    search: Annotated[str | None, Query(max_length=200)] = None,
    order: Literal["random", "sequential", "paper"] = "random",
    origin: Literal["pyq", "ai", "all"] = "pyq",
    semantic: Annotated[str | None, Query(max_length=500)] = None,
    similar: int | None = None,
    ids: Annotated[str | None, Query(max_length=2000)] = None,
    seed: Annotated[int, Query(ge=0, le=2**31)] = 0,
    after: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    # Ranked modes: an explicit id list, nearest neighbours of a question, or a RAG search.
    # They page by position in the ranking ("after" = how many were already returned).
    ranked: list[int] | None = None
    filters = {k: v for k, v in (("exam", exam), ("stage", stage), ("subject", subject), ("chapter", chapter),
                                 ("year", year)) if v}
    if ids:
        ranked = [int(x) for x in ids.split(",") if x.strip().isdigit()][:200]
    elif similar is not None:
        ranked = [h["id"] for h in rag.similar(conn, similar, 60, filters)]
    elif semantic and semantic.strip():
        ranked = [h["id"] for h in rag.hybrid_search(conn, semantic, 100, filters)]
    if ranked is not None:
        keep = {r[0] for r in conn.execute(
            f"SELECT q.id FROM questions q WHERE q.id IN ({_in(ranked)}) AND {STATUS_SQL[status]}", ranked)} if ranked else set()
        ranked = [i for i in ranked if i in keep]
        start = after or 0
        return {"total": len(ranked), "items": practice_items(conn, ranked[start:start + limit]),
                "next_after": start + limit if start + limit < len(ranked) else None}

    if paper:
        paper = paper_slugs(conn)[1].get(paper, paper)
    where, params = [STATUS_SQL[status]], {"seed": seed, "limit": limit}
    if origin != "all":
        where.append("q.origin = :origin")
        params["origin"] = origin
    for col, val in (("exam", exam), ("stage", stage), ("subject", subject), ("chapter", chapter), ("paper_id", paper)):
        if val:
            where.append(f"q.{col} = :{col}")
            params[col] = val
    if year:
        where.append("q.year = :year")
        params["year"] = year
    words = re.findall(r"\w+", search or "")
    if words:
        # Every word must appear; the last one may be a prefix of a longer word.
        where.append("q.id IN (SELECT rowid FROM questions_fts WHERE questions_fts MATCH :search)")
        params["search"] = " ".join(f'"{w}"' for w in words[:-1]) + f' "{words[-1]}"*'
    cond = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM questions q WHERE {cond}", params).fetchone()[0]

    key = ORDER_KEYS[order]
    page_cond = cond
    if after is not None:
        page_cond += f" AND {key} > :after"
        params["after"] = after
    rows = conn.execute(f"SELECT q.id, {key} AS k FROM questions q WHERE {page_cond} ORDER BY k LIMIT :limit",
                        params).fetchall()
    return {"total": total, "items": practice_items(conn, [r["id"] for r in rows]),
            "next_after": rows[-1]["k"] if len(rows) == limit else None}


class AnswerIn(BaseModel):
    question_id: int
    chosen: int | None = None  # None = just reveal the answer, record nothing
    time_ms: int | None = Field(default=None, ge=0)


@app.post("/api/practice/answer")
def practice_answer(body: AnswerIn, conn: Conn) -> dict:
    row = conn.execute("SELECT answer, options, solution FROM questions WHERE id = ?", (body.question_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "question not found")
    is_correct = predicted = None
    if body.chosen is not None:
        if not 0 <= body.chosen < len(json.loads(row["options"])):
            raise HTTPException(422, "chosen option out of range")
        is_correct = body.chosen == row["answer"]
        with conn:
            # The model's forecast is stored before it learns the outcome: that is
            # what its calibration is later measured against.
            predicted = learner.observe(conn, [(body.question_id, is_correct)])[0]
            conn.execute(
                "INSERT INTO attempts (question_id, chosen, is_correct, time_ms, mode, predicted) "
                "VALUES (?, ?, ?, ?, 'practice', ?)",
                (body.question_id, body.chosen, int(is_correct), body.time_ms, predicted))
        _retrain_if_due(conn)
    return {"answer": row["answer"], "solution": db.text(row["solution"]), "is_correct": is_correct,
            "predicted": None if predicted is None else round(predicted, 3)}


_retrain_lock = threading.Lock()


def _retrain_if_due(conn: sqlite3.Connection) -> None:
    """Self-tuning runs off the request path once enough new answers have arrived."""
    if not learner.retrain_due(conn):
        return
    db_path = app.state.db_path

    def run() -> None:
        if not _retrain_lock.acquire(blocking=False):
            return
        try:
            c = db.connect(db_path)
            learner.retrain(c)
            c.close()
            persistence.push_quietly()
        finally:
            _retrain_lock.release()
    threading.Thread(target=run, daemon=True).start()


@app.post("/api/bookmarks/{question_id}")
def toggle_bookmark(question_id: int, conn: Conn) -> dict:
    with conn:
        if conn.execute("DELETE FROM bookmarks WHERE question_id = ?", (question_id,)).rowcount:
            return {"bookmarked": False}
        if not conn.execute("SELECT 1 FROM questions WHERE id = ?", (question_id,)).fetchone():
            raise HTTPException(404, "question not found")
        conn.execute("INSERT INTO bookmarks (question_id) VALUES (?)", (question_id,))
    return {"bookmarked": True}


# --------------------------------------------------------------------------- mock tests

class MockIn(BaseModel):
    pattern_id: str | None = None
    paper_id: str | None = None
    fresh_only: bool = True
    adaptive: bool = False  # personalised: weighted to the learner's weak chapters


@app.post("/api/mocks")
def create_mock(body: MockIn, conn: Conn) -> dict:
    if body.paper_id:
        try:
            layout = mocks.build_paper_layout(conn, paper_slugs(conn)[1].get(body.paper_id, body.paper_id))
        except LookupError:
            raise HTTPException(404, "paper not found")
    elif body.pattern_id in PATTERNS_BY_ID and body.adaptive:
        layout = engine.build_adaptive_layout(conn, PATTERNS_BY_ID[body.pattern_id])
    elif body.pattern_id in PATTERNS_BY_ID:
        layout = mocks.build_random_layout(conn, PATTERNS_BY_ID[body.pattern_id], body.fresh_only)
    else:
        raise HTTPException(422, "give a paper_id or a known pattern_id")
    if not mocks.question_ids(layout):
        raise HTTPException(409, "no questions available for this exam")
    return {"id": mocks.create_mock(conn, layout)}


def _get_mock(conn: sqlite3.Connection, mock_id: str) -> sqlite3.Row:
    m = conn.execute("SELECT * FROM mocks WHERE id = ?", (mock_id,)).fetchone()
    if m is None:
        raise HTTPException(404, "mock not found")
    return m


def _pattern_info(pattern_id: str) -> dict:
    p = PATTERNS_BY_ID[pattern_id]
    return {"id": p["id"], "name": p["name"], "exam": p["exam"], "stage": p["stage"]}


@app.get("/api/mocks")
def list_mocks(conn: Conn) -> list[dict]:
    rows = conn.execute("SELECT id, kind, pattern_id, paper_id, title, created_at, submitted_at, score, max_score "
                        "FROM mocks ORDER BY created_at DESC LIMIT 200").fetchall()
    return [{**dict(r), "exam": PATTERNS_BY_ID[r["pattern_id"]]["exam"]} for r in rows]


@app.get("/api/mocks/{mock_id}")
def get_mock(mock_id: str, conn: Conn) -> dict:
    m = _get_mock(conn, mock_id)
    layout = json.loads(m["layout"])
    ids = mocks.question_ids(layout)
    rows = conn.execute(f"SELECT * FROM questions WHERE id IN ({_in(ids)})", ids).fetchall()
    return {
        "id": m["id"], "title": m["title"], "kind": m["kind"], "pattern": _pattern_info(m["pattern_id"]),
        "parts": layout["parts"], "created_at": m["created_at"], "submitted": m["submitted_at"] is not None,
        "questions": {r["id"]: public_question(r) for r in rows},
    }


class ResponseIn(BaseModel):
    chosen: int | None = None
    marked: bool = False
    ms: int = Field(default=0, ge=0)


class SubmitIn(BaseModel):
    responses: dict[str, ResponseIn]
    elapsed_sec: int = Field(default=0, ge=0)


@app.post("/api/mocks/{mock_id}/submit")
def submit_mock(mock_id: str, body: SubmitIn, conn: Conn) -> dict:
    m = _get_mock(conn, mock_id)
    if m["submitted_at"]:
        raise HTTPException(409, "already submitted")
    responses = {k: v.model_dump() for k, v in body.responses.items()}
    result = mocks.grade(conn, json.loads(m["layout"]), responses)
    with conn:
        updated = conn.execute(
            "UPDATE mocks SET submitted_at = datetime('now'), elapsed_sec = ?, responses = ?, result = ?, "
            "score = ?, max_score = ? WHERE id = ? AND submitted_at IS NULL",
            (body.elapsed_sec, json.dumps(responses), json.dumps(result), result["score"], result["max_score"], mock_id),
        ).rowcount
        if not updated:
            raise HTTPException(409, "already submitted")
        answered = [(int(qid), q) for qid, q in result["questions"].items() if q["chosen"] is not None]
        predictions = learner.observe(conn, [(qid, q["status"] == "correct") for qid, q in answered])
        conn.executemany(
            "INSERT INTO attempts (question_id, chosen, is_correct, time_ms, mode, mock_id, predicted) "
            "VALUES (?, ?, ?, ?, 'mock', ?, ?)",
            [(qid, q["chosen"], int(q["status"] == "correct"), q["ms"], mock_id, p)
             for (qid, q), p in zip(answered, predictions)],
        )
    _retrain_if_due(conn)
    return {"id": mock_id, "score": result["score"], "max_score": result["max_score"]}


@app.get("/api/mocks/{mock_id}/result")
def mock_result(mock_id: str, conn: Conn) -> dict:
    m = _get_mock(conn, mock_id)
    if not m["submitted_at"]:
        raise HTTPException(409, "mock not submitted yet")
    layout = json.loads(m["layout"])
    ids = mocks.question_ids(layout)
    rows = conn.execute(f"""
        SELECT q.*, p.title AS paper_title,
               EXISTS (SELECT 1 FROM bookmarks b WHERE b.question_id = q.id) AS bookmarked
        FROM questions q JOIN papers p ON p.id = q.paper_id WHERE q.id IN ({_in(ids)})""", ids).fetchall()
    questions = {r["id"]: {**public_question(r), "answer": r["answer"], "solution": db.text(r["solution"]),
                           "paper_title": r["paper_title"], "bookmarked": bool(r["bookmarked"])} for r in rows}
    return {
        "id": m["id"], "title": m["title"], "kind": m["kind"], "pattern": _pattern_info(m["pattern_id"]),
        "paper_id": m["paper_id"], "parts": layout["parts"], "submitted_at": m["submitted_at"], "elapsed_sec": m["elapsed_sec"],
        "minutes": sum(p["minutes"] for p in layout["parts"]),
        "result": json.loads(m["result"]), "questions": questions, "ai_review": m["ai_review"],
    }


@app.delete("/api/mocks/{mock_id}")
def discard_mock(mock_id: str, conn: Conn) -> dict:
    m = _get_mock(conn, mock_id)
    if m["submitted_at"]:
        raise HTTPException(409, "submitted mocks are kept for your history")
    with conn:
        conn.execute("DELETE FROM mocks WHERE id = ?", (mock_id,))
    return {"deleted": True}


# --------------------------------------------------------------------------- analytics

@app.get("/api/stats")
def stats(conn: Conn) -> dict:
    t = conn.execute("SELECT COUNT(*) attempts, COALESCE(SUM(is_correct), 0) correct, "
                     "COUNT(DISTINCT question_id) questions FROM attempts").fetchone()
    subjects = [
        {"subject": r["subject"], "name": SUBJECTS[r["subject"]], "attempts": r["attempts"], "correct": r["correct"]}
        for r in conn.execute("SELECT q.subject, COUNT(*) attempts, SUM(a.is_correct) correct FROM attempts a "
                              "JOIN questions q ON q.id = a.question_id GROUP BY 1 ORDER BY 2 DESC")
    ]
    weak = [
        {**dict(r), "label": chapter_label(r["chapter"]), "subject_name": SUBJECTS[r["subject"]]}
        for r in conn.execute("""
            SELECT q.subject, q.chapter, COUNT(*) attempts, SUM(a.is_correct) correct
            FROM attempts a JOIN questions q ON q.id = a.question_id
            WHERE q.chapter IS NOT NULL GROUP BY 1, 2 HAVING COUNT(*) >= 5
            ORDER BY 1.0 * SUM(a.is_correct) / COUNT(*), COUNT(*) DESC LIMIT 8""")
    ]
    activity = [dict(r) for r in conn.execute("""
        SELECT date(created_at, 'localtime') day, COUNT(*) attempts, SUM(is_correct) correct
        FROM attempts WHERE created_at >= datetime('now', '-30 days') GROUP BY 1 ORDER BY 1""")]
    mock_rows = [
        {**dict(r), "exam": PATTERNS_BY_ID[r["pattern_id"]]["exam"]}
        for r in conn.execute("SELECT id, title, kind, pattern_id, submitted_at, score, max_score FROM mocks "
                              "WHERE submitted_at IS NOT NULL ORDER BY submitted_at")
    ]
    today = conn.execute("SELECT COUNT(*) FROM attempts WHERE date(created_at, 'localtime') = date('now', 'localtime')").fetchone()[0]
    return {"totals": {**dict(t), "today": today, "mocks": len(mock_rows)},
            "subjects": subjects, "weak_chapters": weak, "activity": activity, "mocks": mock_rows}


# --------------------------------------------------------------------------- AI tutor

class ChatMsg(BaseModel):
    role: Literal["user", "model"]
    text: str = Field(min_length=1, max_length=4000)


class ExplainIn(BaseModel):
    question_id: int
    chosen: int | None = None
    messages: list[ChatMsg] = Field(min_length=1, max_length=24)


def _stream(gen: AsyncIterator[str], on_done: Callable[[str], None] | None = None) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        chunks: list[str] = []
        try:
            async for text in gen:
                chunks.append(text)
                yield text
        except ai.AIError as e:
            yield f"\n\n**AI error:** {e}"
            return
        except httpx.HTTPError as e:
            yield f"\n\n**AI error:** could not reach Gemini ({e.__class__.__name__})."
            return
        if on_done and chunks:
            on_done("".join(chunks))
    return StreamingResponse(body(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _require_ai() -> None:
    if not ai.enabled():
        raise HTTPException(503, "AI tutor is off: set GEMINI_API_KEY in backend/.env")


@app.post("/api/ai/explain")
def ai_explain(body: ExplainIn, conn: Conn) -> StreamingResponse:
    _require_ai()
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (body.question_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "question not found")
    q = {**dict(row), "options": json.loads(row["options"]), "solution": db.text(row["solution"])}
    q["related"] = _sources(conn, [h["id"] for h in rag.similar(conn, row["id"], 3)]) if rag.get_index() else []
    chosen = body.chosen if body.chosen is not None and 0 <= body.chosen < len(q["options"]) else None
    return _stream(ai.explain(q, chosen, [m.model_dump() for m in body.messages]))


def _sources(conn: sqlite3.Connection, ids: list[int]) -> list[dict]:
    """Retrieved PYQs with their keys, numbered for citation, as context for Gemini."""
    if not ids:
        return []
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT q.*, p.title AS paper_title FROM questions q JOIN papers p ON p.id = q.paper_id "
        f"WHERE q.id IN ({_in(ids)})", ids)}
    out = []
    for qid in ids:
        r = rows.get(qid)
        if r is None:
            continue
        options = json.loads(r["options"])
        out.append({"n": len(out) + 1, "id": qid, "source": r["paper_title"], "question": r["question"],
                    "options": options, "answer": r["answer"], "answer_text": options[r["answer"]],
                    "solution": db.text(r["solution"])})
    return out


@app.post("/api/ai/mock-review/{mock_id}")
def ai_mock_review(mock_id: str, conn: Conn, refresh: bool = False):
    m = _get_mock(conn, mock_id)
    if not m["submitted_at"]:
        raise HTTPException(409, "mock not submitted yet")
    if m["ai_review"] and not refresh:
        return PlainTextResponse(m["ai_review"])
    _require_ai()
    result = json.loads(m["result"])
    layout = json.loads(m["layout"])
    ids = mocks.question_ids(layout)
    chapter_of = {r[0]: (r[1], r[2]) for r in
                  conn.execute(f"SELECT id, subject, chapter FROM questions WHERE id IN ({_in(ids)})", ids)}
    agg: dict[tuple, dict] = {}
    for qid, q in result["questions"].items():
        subject, chapter = chapter_of[int(qid)]
        a = agg.setdefault((subject, chapter), {"subject": subject, "chapter": chapter,
                                                "correct": 0, "wrong": 0, "skipped": 0, "ms": 0, "n": 0})
        a[q["status"]] += 1
        a["ms"] += q["ms"]
        a["n"] += 1
    chapters = sorted(agg.values(), key=lambda a: (-(a["wrong"] + a["skipped"]), -a["n"]))[:25]
    for a in chapters:
        a["avg_sec"] = round(a["ms"] / a["n"] / 1000)
    minutes = sum(p["minutes"] for p in layout["parts"])
    db_path = app.state.db_path

    def save(text: str) -> None:
        c = db.connect(db_path)
        with c:
            c.execute("UPDATE mocks SET ai_review = ? WHERE id = ?", (text, mock_id))
        c.close()
        persistence.push_quietly()  # streamed responses finish after the sync middleware ran

    return _stream(ai.review_mock(m["title"], minutes, m["elapsed_sec"], result, chapters), on_done=save)


# --------------------------------------------------------------------------- RAG search

@app.get("/api/search")
def search(
    conn: Conn,
    q: Annotated[str, Query(min_length=2, max_length=1000)],
    exam: str | None = None,
    stage: str | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    year: int | None = None,
    k: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    filters = {key: v for key, v in (("exam", exam), ("stage", stage), ("subject", subject), ("chapter", chapter),
                                     ("year", year)) if v}
    hits = rag.hybrid_search(conn, q, k, filters)
    extra = {h["id"]: {"via": h["via"], "similarity": h["similarity"]} for h in hits}
    # A long query is usually a pasted question: report the topic the trained model sees.
    classification = topic_model.predict([q])[0] if len(q) >= 40 else None
    return {"query": q, "results": practice_items(conn, [h["id"] for h in hits], extra),
            "classification": classification, "dense_index": rag.get_index() is not None}


@app.get("/api/questions/{question_id}/similar")
def similar_questions(question_id: int, conn: Conn, k: Annotated[int, Query(ge=1, le=30)] = 6) -> list[dict]:
    hits = rag.similar(conn, question_id, k)
    return practice_items(conn, [h["id"] for h in hits], {h["id"]: {"similarity": h["similarity"]} for h in hits})


class AskIn(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    source_ids: list[int] = Field(default_factory=list, max_length=12)
    messages: list[ChatMsg] = Field(default_factory=list, max_length=16)


@app.post("/api/ai/ask")
def ai_ask(body: AskIn, conn: Conn) -> StreamingResponse:
    _require_ai()
    ids = body.source_ids or [h["id"] for h in rag.hybrid_search(conn, body.query, 8)]
    return _stream(ai.ask(body.query, _sources(conn, ids), [m.model_dump() for m in body.messages]))


class GenerateIn(BaseModel):
    exam: str
    stage: Literal["pre", "mains"]
    subject: str
    chapter: str
    count: int = Field(default=5, ge=1, le=10)


@app.post("/api/ai/generate")
async def ai_generate(body: GenerateIn, conn: Conn) -> dict:
    _require_ai()
    if body.exam not in EXAMS or body.subject not in SUBJECTS:
        raise HTTPException(422, "unknown exam or subject")
    try:
        return await generator.generate(conn, body.exam, body.stage, body.subject, body.chapter, body.count)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except ai.AIError as e:
        raise HTTPException(503, str(e))


# --------------------------------------------------------------------------- adaptive engine + learner model

@app.get("/api/smart")
def smart_practice(
    conn: Conn,
    n: Annotated[int, Query(ge=1, le=30)] = 10,
    subject: str | None = None,
    exclude: Annotated[str, Query(max_length=4000)] = "",
) -> dict:
    picks = engine.smart_batch(conn, n, subject, {int(x) for x in exclude.split(",") if x.strip().isdigit()})
    items = practice_items(conn, [p["id"] for p in picks],
                           {p["id"]: {"reason": p["reason"], "kind": p["kind"]} for p in picks})
    exam, stage = learner.target(conn)
    return {"items": items, "target": {"exam": exam, "stage": stage}}


def _target_pattern(conn: sqlite3.Connection) -> dict:
    exam, stage = learner.target(conn)
    return next(p for p in PATTERNS if p["exam"] == exam and p["stage"] == stage and not p.get("legacy"))


@app.get("/api/learner")
def learner_overview(conn: Conn) -> dict:
    return learner.overview(conn, _target_pattern(conn))


class TargetIn(BaseModel):
    exam: str
    stage: Literal["pre", "mains"]


@app.put("/api/learner/target")
def set_target(body: TargetIn, conn: Conn) -> dict:
    if not any(p["exam"] == body.exam and p["stage"] == body.stage and not p.get("legacy") for p in PATTERNS):
        raise HTTPException(422, "no exam pattern for that exam and stage")
    with conn:
        learner.set_setting(conn, "target", {"exam": body.exam, "stage": body.stage})
    return learner.overview(conn, _target_pattern(conn))


@app.post("/api/learner/retrain")
def retrain_learner(conn: Conn) -> dict:
    with _retrain_lock:
        return learner.retrain(conn)


# --------------------------------------------------------------------------- AI lab (model status)

@app.get("/api/lab")
def lab(conn: Conn) -> dict:
    return {
        "rag": rag.status(),
        "topic_model": topic_model.status(),
        "learner": learner.get_setting(conn, "learner.report"),
        "finetune": finetune.status(),
        "ai_questions": conn.execute("SELECT COUNT(*) FROM questions WHERE origin = 'ai'").fetchone()[0],
        "gemini": {"enabled": ai.enabled(), "models": config.GEMINI_MODELS},
        "storage": "turso" if persistence.get() else ("ephemeral" if config.ON_VERCEL else "local"),
    }


class ClassifyIn(BaseModel):
    text: str = Field(min_length=5, max_length=4000)


@app.post("/api/lab/classify")
def classify(body: ClassifyIn, conn: Conn) -> dict:
    predictions = topic_model.predict([body.text], top=5)
    if predictions is None:
        raise HTTPException(503, "topic model not trained yet: run  python -m app.topic_model train")
    hits = rag.hybrid_search(conn, body.text, 5)
    return {"predictions": predictions[0], "similar": practice_items(conn, [h["id"] for h in hits])}


class ExportIn(BaseModel):
    max_examples: int = Field(default=20000, ge=100, le=200000)


@app.post("/api/lab/finetune")
def export_finetune(body: ExportIn, conn: Conn) -> dict:
    return finetune.export(conn, config.DATA_DIR / "finetune", body.max_examples)


# --------------------------------------------------------------------------- browser error reports

CLIENT_ERRORS_KEPT = 30


class ClientErrorIn(BaseModel):
    message: str
    stack: str = ""
    component: str = ""
    url: str = ""
    ua: str = ""
    build: str = ""


@app.post("/api/client-errors", status_code=204)
def report_client_error(body: ClientErrorIn, conn: Conn) -> None:
    """A crash or failed script load in a visitor's browser, kept (newest first) so it can be
    diagnosed without access to that browser's console."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'client_errors'").fetchone()
    kept = json.loads(row["value"]) if row else []
    limits = {"message": 500, "stack": 2000, "component": 1500, "url": 300, "ua": 300, "build": 100}
    entry = {"at": conn.execute("SELECT datetime('now')").fetchone()[0],
             **{k: getattr(body, k)[:n] for k, n in limits.items()}}
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('client_errors', ?)",
                     (json.dumps([entry, *kept][:CLIENT_ERRORS_KEPT]),))


@app.get("/api/client-errors")
def client_errors(conn: Conn) -> list[dict]:
    row = conn.execute("SELECT value FROM settings WHERE key = 'client_errors'").fetchone()
    return json.loads(row["value"]) if row else []


# --------------------------------------------------------------------------- frontend

@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    dist = config.FRONTEND_DIST.resolve()
    file = (dist / path).resolve()
    if path and file.is_file() and dist in file.parents:
        return FileResponse(file)
    if path.startswith("assets/"):  # a file from another build: a 404 the page can react to, not HTML
        raise HTTPException(404)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "Frontend not built yet: run `npm run build` in frontend/, or use `npm run dev`.")
