"""SQLite schema and connection helper.

`papers` and `questions` are rebuilt by the importer from the dataset; `attempts`,
`bookmarks`, `mocks`, `reviews` and `learner_params` hold the learner's own progress
and survive re-imports because questions are upserted on their stable dataset `qid`.
Questions the AI generator writes carry origin = 'ai' and never mix into PYQ papers.
"""
from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id             TEXT PRIMARY KEY,      -- dataset paper_meta.test_id
    exam           TEXT NOT NULL,         -- 'SSC-CGL', 'SSC-MTS', ...
    stage          TEXT NOT NULL,         -- 'pre' | 'mains'
    title          TEXT NOT NULL,
    year           INTEGER,
    held_on        TEXT,                  -- ISO date
    shift          TEXT,
    duration_min   INTEGER,
    question_count INTEGER NOT NULL DEFAULT 0,
    max_marks      REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_papers_exam ON papers(exam, stage, held_on);

CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY,
    qid         TEXT NOT NULL,
    paper_id    TEXT NOT NULL REFERENCES papers(id),
    exam        TEXT NOT NULL,
    stage       TEXT NOT NULL,
    subject     TEXT NOT NULL,            -- REAS | GK | MATH | ENG | COMPUTER
    chapter     TEXT,
    concept     TEXT,
    year        INTEGER,
    n           INTEGER,                  -- question number inside its paper section
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,            -- JSON array of option texts
    answer      INTEGER NOT NULL,         -- 0-based index into options
    solution    TEXT,
    marks_pos   REAL NOT NULL,
    marks_neg   REAL NOT NULL,
    origin      TEXT NOT NULL DEFAULT 'pyq',  -- 'pyq' (dataset) | 'ai' (generated, verified)
    UNIQUE (qid, paper_id)                -- one source qid is shared by two papers
);

-- Word search over question text; rebuilt by the importer (the only writer).
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
    question, content = 'questions', content_rowid = 'id', tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    chosen      INTEGER NOT NULL,
    is_correct  INTEGER NOT NULL,
    time_ms     INTEGER,
    mode        TEXT NOT NULL,            -- 'practice' | 'mock'
    mock_id     TEXT,
    predicted   REAL,                     -- learner model's P(correct) just before answering
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookmarks (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mocks (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,           -- 'paper' (real PYQ shift) | 'random' | 'adaptive'
    pattern_id   TEXT NOT NULL,
    paper_id     TEXT,
    title        TEXT NOT NULL,
    layout       TEXT NOT NULL,           -- JSON: parts -> sections -> question ids
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_at TEXT,
    elapsed_sec  INTEGER,
    responses    TEXT,                    -- JSON as submitted
    result       TEXT,                    -- JSON grading summary
    score        REAL,
    max_score    REAL,
    ai_review    TEXT
);

-- Self-learning learner model: one row per parameter ('g', 'sub:MATH',
-- 'ch:MATH/profit-and-loss', 'exam:SSC-CGL/pre', 'q:123'), with its update count.
CREATE TABLE IF NOT EXISTS learner_params (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL,
    n     INTEGER NOT NULL DEFAULT 0
);

-- Spaced-repetition queue for questions the learner got wrong.
CREATE TABLE IF NOT EXISTS reviews (
    question_id   INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    due           TEXT NOT NULL,          -- UTC 'YYYY-MM-DD HH:MM:SS'
    interval_days REAL NOT NULL,
    ease          REAL NOT NULL,
    reps          INTEGER NOT NULL DEFAULT 0,
    lapses        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS ix_q_pool    ON questions(exam, stage, subject, origin);
CREATE INDEX IF NOT EXISTS ix_q_subject ON questions(subject, chapter);
CREATE INDEX IF NOT EXISTS ix_q_paper   ON questions(paper_id, subject, n);
CREATE INDEX IF NOT EXISTS ix_q_year    ON questions(year);
CREATE INDEX IF NOT EXISTS ix_attempts_q ON attempts(question_id);
CREATE INDEX IF NOT EXISTS ix_attempts_time ON attempts(created_at);
CREATE INDEX IF NOT EXISTS ix_mocks_paper ON mocks(paper_id);
CREATE INDEX IF NOT EXISTS ix_reviews_due ON reviews(due);
"""


def text(value: str | bytes | None) -> str | None:
    """Solutions are zlib-compressed in the deployed question bank (half its size); read either form."""
    return zlib.decompress(value).decode() if isinstance(value, bytes) else value


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring databases built by earlier versions up to date without losing progress."""
    if "origin" not in _columns(conn, "questions"):
        conn.execute("ALTER TABLE questions ADD COLUMN origin TEXT NOT NULL DEFAULT 'pyq'")
        conn.execute("DROP INDEX IF EXISTS ix_q_pool")  # recreated below with origin
    if "predicted" not in _columns(conn, "attempts"):
        conn.execute("ALTER TABLE attempts ADD COLUMN predicted REAL")


def init(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.executescript(INDEXES)
    conn.commit()
