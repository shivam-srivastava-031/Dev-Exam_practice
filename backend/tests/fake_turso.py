"""An in-memory stand-in for Turso's Hrana-over-HTTP v2 pipeline API, backed by SQLite.

Implements the subset the app uses: `execute`, `batch` with ok/not conditions, and
`close`, with Hrana's typed values. Plug it into the real client via httpx.MockTransport.
"""
from __future__ import annotations

import base64
import json
import sqlite3

import httpx


def _decode(v):
    kind = v["type"]
    if kind == "null":
        return None
    if kind == "integer":
        return int(v["value"])
    if kind == "float":
        return float(v["value"])
    if kind == "blob":
        return base64.b64decode(v["base64"] + "=" * (-len(v["base64"]) % 4))
    return v["value"]


def _encode(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, bytes):
        return {"type": "blob", "base64": base64.b64encode(v).decode()}
    return {"type": "text", "value": v}


class FakeTurso:
    def __init__(self, token: str = "secret"):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.token = token
        self.requests = 0
        self.fail_next_write = False

    def _execute(self, stmt: dict) -> dict:
        sql = stmt["sql"]
        if self.fail_next_write and sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.fail_next_write = False
            raise sqlite3.OperationalError("simulated outage")
        cur = self.conn.execute(sql, [_decode(a) for a in stmt.get("args", [])])
        rows = cur.fetchall() if cur.description else []
        cols = [{"name": d[0], "decltype": None} for d in (cur.description or [])]
        return {"cols": cols, "rows": [[_encode(v) for v in row] for row in rows],
                "affected_row_count": cur.rowcount if cur.rowcount > 0 else 0, "last_insert_rowid": None}

    def _batch(self, steps: list[dict]) -> dict:
        results, errors = [], []

        def holds(cond):
            if cond is None:
                return True
            if cond["type"] == "ok":
                return results[cond["step"]] is not None
            if cond["type"] == "not":
                return not holds(cond["cond"])
            raise ValueError(cond)

        for step in steps:
            if not holds(step.get("condition")):
                results.append(None)
                errors.append(None)
                continue
            try:
                results.append(self._execute(step["stmt"]))
                errors.append(None)
            except sqlite3.Error as e:
                results.append(None)
                errors.append({"message": str(e)})
        return {"step_results": results, "step_errors": errors}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if request.url.path != "/v2/pipeline":
            return httpx.Response(404)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, text="unauthorized")
        out = []
        for req in json.loads(request.content)["requests"]:
            if req["type"] == "close":
                out.append({"type": "ok", "response": {"type": "close"}})
            elif req["type"] == "execute":
                try:
                    out.append({"type": "ok", "response": {"type": "execute", "result": self._execute(req["stmt"])}})
                except sqlite3.Error as e:
                    out.append({"type": "error", "error": {"message": str(e)}})
            elif req["type"] == "batch":
                out.append({"type": "ok", "response": {"type": "batch", "result": self._batch(req["batch"]["steps"])}})
        return httpx.Response(200, json={"baton": None, "base_url": None, "results": out})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
