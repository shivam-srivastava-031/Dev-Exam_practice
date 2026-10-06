"""Keep the learner's own data in Turso when the server's disk is ephemeral.

On Vercel the question bank ships with the deployment and is copied to /tmp at
cold start, but /tmp disappears with the instance. Everything the learner creates
therefore lives in a Turso database (hosted SQLite, spoken to over its HTTP API):

    attempts, bookmarks, mocks, learner_params, reviews, settings,
    the AI-generated questions with their 'paper' rows, and the current-affairs archive.

* pull(): on cold start, and whenever another instance has written since (a version
  counter in Turso says so), these local tables are replaced with Turso's copy.
* push(): after a request changed something, the rows that differ from the last
  synced snapshot are upserted or deleted in Turso in one transaction, and the
  version counter is bumped.

Everything else keeps using plain SQLite. Without TURSO_* settings none of this runs.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass

import httpx

from . import db

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spec:
    name: str   # table in Turso
    local: str  # local table the rows live in
    where: str  # which local rows belong to the learner
    key: str    # primary key column


# Insert order matters locally: AI papers before AI questions before rows pointing at them.
SPECS = (
    Spec("ai_papers", "papers", "id LIKE 'ai-%'", "id"),
    Spec("ai_questions", "questions", "origin = 'ai'", "id"),
    Spec("attempts", "attempts", "1 = 1", "id"),
    Spec("bookmarks", "bookmarks", "1 = 1", "question_id"),
    Spec("mocks", "mocks", "1 = 1", "id"),
    Spec("learner_params", "learner_params", "1 = 1", "key"),
    Spec("reviews", "reviews", "1 = 1", "question_id"),
    Spec("settings", "settings", "1 = 1", "key"),
    # Feeds only reach back a few days, so the archive is kept like progress (60 days, at most 40 a day).
    Spec("news", "news", "1 = 1", "id"),
)


class TursoError(RuntimeError):
    pass


def _encode(v) -> dict:
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode().rstrip("=")}
    return {"type": "text", "value": str(v)}


def _decode(v: dict):
    kind = v["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(v["value"])
    if kind == "float":
        return float(v["value"])
    if kind == "blob":
        raw = v["base64"]
        return base64.b64decode(raw + "=" * (-len(raw) % 4))
    return v["value"]


def _stmt(sql: str, args=()) -> dict:
    return {"sql": sql, "args": [_encode(a) for a in args]}


class Turso:
    """Minimal client for Turso's Hrana-over-HTTP v2 pipeline API."""

    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        base = url.strip().replace("libsql://", "https://", 1).rstrip("/")
        self.endpoint = f"{base}/v2/pipeline"
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=10))

    def _pipeline(self, requests: list[dict]) -> list[dict]:
        resp = self.client.post(self.endpoint, json={"requests": [*requests, {"type": "close"}]}, headers=self.headers)
        if resp.status_code != 200:
            raise TursoError(f"Turso HTTP {resp.status_code}: {resp.text[:200]}")
        results = resp.json()["results"][: len(requests)]
        for r in results:
            if r["type"] == "error":
                raise TursoError(r["error"].get("message", "unknown Turso error"))
        return [r["response"] for r in results]

    @staticmethod
    def _rows(result: dict) -> list[dict]:
        cols = [c["name"] for c in result["cols"]]
        return [dict(zip(cols, map(_decode, row))) for row in result["rows"]]

    def query(self, sql: str, args=()) -> list[dict]:
        return self._rows(self._pipeline([{"type": "execute", "stmt": _stmt(sql, args)}])[0]["result"])

    def query_many(self, sqls: list[str]) -> list[list[dict]]:
        """Several SELECTs in one round trip."""
        out = self._pipeline([{"type": "execute", "stmt": _stmt(sql)} for sql in sqls])
        return [self._rows(r["result"]) for r in out]

    def transaction(self, statements: list[tuple[str, tuple]], then_query: str | None = None) -> list[dict]:
        """Run statements atomically (BEGIN ... COMMIT, ROLLBACK on any failure)."""
        steps = [{"stmt": _stmt("BEGIN")}]
        for sql, args in statements:
            steps.append({"condition": {"type": "ok", "step": len(steps) - 1}, "stmt": _stmt(sql, args)})
        commit = len(steps)
        steps.append({"condition": {"type": "ok", "step": commit - 1}, "stmt": _stmt("COMMIT")})
        steps.append({"condition": {"type": "not", "cond": {"type": "ok", "step": commit}}, "stmt": _stmt("ROLLBACK")})
        if then_query:
            steps.append({"condition": {"type": "ok", "step": commit}, "stmt": _stmt(then_query)})
        result = self._pipeline([{"type": "batch", "batch": {"steps": steps}}])[0]["result"]
        errors = [e for e in result["step_errors"] if e]
        if errors:
            raise TursoError(errors[0].get("message", "transaction failed"))
        last = result["step_results"][-1]
        return self._rows(last) if then_query and last else []


class Syncer:
    def __init__(self, remote: Turso, db_path, freshness_seconds: float = 2.0):
        self.remote = remote
        self.db_path = db_path
        self.freshness = freshness_seconds
        self.lock = threading.RLock()
        self.version: int | None = None
        self.snapshot: dict[str, dict] = {}
        self.checked_at = 0.0
        self.schema_ready = False

    # ----------------------------------------------------------------- local side

    @staticmethod
    def _columns(conn, table: str) -> list[tuple[str, str]]:
        return [(r[1], r[2] or "") for r in conn.execute(f"PRAGMA table_info({table})")]

    def _local_rows(self, conn, spec: Spec) -> dict:
        cols = [c for c, _ in self._columns(conn, spec.local)]
        key = cols.index(spec.key)
        return {row[key]: tuple(row) for row in conn.execute(f"SELECT {', '.join(cols)} FROM {spec.local} WHERE {spec.where}")}

    # ----------------------------------------------------------------- remote schema

    def _ensure_schema(self, conn) -> None:
        if self.schema_ready:
            return
        statements = [("CREATE TABLE IF NOT EXISTS _sync (k TEXT PRIMARY KEY, v INTEGER NOT NULL)", ()),
                      ("INSERT OR IGNORE INTO _sync (k, v) VALUES ('version', 0)", ())]
        existing = self.remote.query_many([f"PRAGMA table_info({s.name})" for s in SPECS])
        for spec, remote_cols in zip(SPECS, existing):
            local_cols = self._columns(conn, spec.local)
            if not remote_cols:
                defs = ", ".join(f'"{c}" {t}' for c, t in local_cols)
                statements.append((f'CREATE TABLE IF NOT EXISTS {spec.name} ({defs}, PRIMARY KEY ("{spec.key}"))', ()))
            else:  # the app added a column since Turso's table was created
                have = {r["name"] for r in remote_cols}
                statements += [(f'ALTER TABLE {spec.name} ADD COLUMN "{c}" {t}', ()) for c, t in local_cols if c not in have]
        self.remote.transaction(statements)
        self.schema_ready = True

    # ----------------------------------------------------------------- sync

    def pull(self) -> None:
        """Replace the learner's local tables with Turso's copy."""
        with self.lock:
            conn = db.connect(self.db_path)
            try:
                self._ensure_schema(conn)
                reads = self.remote.query_many(
                    ["SELECT v FROM _sync WHERE k = 'version'"] + [f"SELECT * FROM {s.name}" for s in SPECS])
                with conn:
                    # questions_fts is an external-content index: drop entries before their rows go.
                    for qid, text in conn.execute("SELECT id, question FROM questions WHERE origin = 'ai'").fetchall():
                        conn.execute("INSERT INTO questions_fts (questions_fts, rowid, question) VALUES ('delete', ?, ?)",
                                     (qid, text))
                    for spec in reversed(SPECS):
                        conn.execute(f"DELETE FROM {spec.local} WHERE {spec.where}")
                    for spec, rows in zip(SPECS, reads[1:]):
                        local_cols = [c for c, _ in self._columns(conn, spec.local)]
                        for row in rows:
                            cols = [c for c in local_cols if c in row]
                            conn.execute(f"INSERT OR REPLACE INTO {spec.local} ({', '.join(cols)}) "
                                         f"VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
                    for qid, text in conn.execute("SELECT id, question FROM questions WHERE origin = 'ai'").fetchall():
                        conn.execute("INSERT INTO questions_fts (rowid, question) VALUES (?, ?)", (qid, text))
                self.snapshot = {spec.name: self._local_rows(conn, spec) for spec in SPECS}
                self.version = reads[0][0]["v"] if reads[0] else 0
                self.checked_at = time.monotonic()
            finally:
                conn.close()

    def push(self) -> bool:
        """Send rows changed since the last sync to Turso. Returns whether anything changed."""
        with self.lock:
            conn = db.connect(self.db_path)
            try:
                current = {spec.name: self._local_rows(conn, spec) for spec in SPECS}
                columns = {spec.name: [c for c, _ in self._columns(conn, spec.local)] for spec in SPECS}
            finally:
                conn.close()
            statements: list[tuple[str, tuple]] = []
            for spec in SPECS:
                old, new = self.snapshot.get(spec.name, {}), current[spec.name]
                changed = [row for k, row in new.items() if old.get(k) != row]
                deleted = [k for k in old if k not in new]
                cols = columns[spec.name]
                per_stmt = max(1, 500 // len(cols))  # stay well under SQLite's bound-parameter limit
                for i in range(0, len(changed), per_stmt):
                    chunk = changed[i:i + per_stmt]
                    values = ", ".join(f"({', '.join('?' * len(cols))})" for _ in chunk)
                    statements.append((f"INSERT OR REPLACE INTO {spec.name} ({', '.join(cols)}) VALUES {values}",
                                       tuple(v for row in chunk for v in row)))
                for i in range(0, len(deleted), 500):
                    chunk = deleted[i:i + 500]
                    statements.append((f'DELETE FROM {spec.name} WHERE "{spec.key}" IN ({", ".join("?" * len(chunk))})',
                                       tuple(chunk)))
            if not statements:
                return False
            statements.append(("UPDATE _sync SET v = v + 1 WHERE k = 'version'", ()))
            rows = self.remote.transaction(statements, then_query="SELECT v FROM _sync WHERE k = 'version'")
            self.snapshot = current
            self.version = rows[0]["v"] if rows else (self.version or 0) + 1
            self.checked_at = time.monotonic()
            return True

    def pull_if_stale(self, seen: int | None = None) -> None:
        """Cheap freshness check; reload only if another instance wrote in the meantime.

        `seen` is the newest version the browser has been sent. An instance behind it checks
        Turso straight away instead of trusting its freshness window, so a page never misses
        a change the learner made a moment ago on another instance (opening a just-created
        mock, say).
        """
        behind = seen is not None and (self.version is None or seen > self.version)
        if not behind and self.version is not None and time.monotonic() - self.checked_at < self.freshness:
            return
        with self.lock:
            remote_version = self.remote.query("SELECT v FROM _sync WHERE k = 'version'")[0]["v"]
            self.checked_at = time.monotonic()
            if remote_version != self.version:
                self.push()  # keep any local change that has not reached Turso yet
                self.pull()


_syncer: Syncer | None = None


def remote_from_config() -> Turso:
    from . import config
    return Turso(config.TURSO_URL, config.TURSO_TOKEN)


def configure(remote: Turso, db_path) -> Syncer:
    global _syncer
    _syncer = Syncer(remote, db_path)
    return _syncer


def get() -> Syncer | None:
    return _syncer


def reset() -> None:
    global _syncer
    _syncer = None


def push_quietly() -> None:
    """Push from places outside a request (background retraining, streamed AI reviews)."""
    if _syncer is None:
        return
    try:
        _syncer.push()
    except (TursoError, httpx.HTTPError) as e:  # the next request retries the push
        log.warning("could not sync progress to Turso: %s", e)
