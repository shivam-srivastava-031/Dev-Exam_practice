"""Progress survives the loss of the server's disk when Turso is configured."""
import shutil

import pytest
from fastapi.testclient import TestClient

from app import config, db, persistence
from app.main import app
from tests.fake_turso import FakeTurso


@pytest.fixture()
def turso():
    return FakeTurso()


@pytest.fixture()
def deployed(client, tmp_path, turso, monkeypatch):
    """The app as on Vercel: a pristine question bank copied to a temp path, progress in Turso."""
    seed = tmp_path / "seed.db"
    shutil.copyfile(client.app.state.db_path, seed)
    monkeypatch.setattr(config, "SEED_DB", seed)
    monkeypatch.setattr(config, "TURSO_URL", "libsql://exam-practice.turso.io")
    monkeypatch.setattr(config, "TURSO_TOKEN", "secret")
    monkeypatch.setattr(persistence, "remote_from_config",
                        lambda: persistence.Turso(config.TURSO_URL, config.TURSO_TOKEN, turso.client()))

    def boot(name: str) -> TestClient:
        """A fresh instance: empty /tmp, so the bank is re-seeded and progress pulled."""
        app.state.db_path = tmp_path / name / "exam.db"
        return TestClient(app)
    return boot


def test_progress_survives_a_cold_start(deployed, turso):
    with deployed("instance-1") as c:
        qid = c.get("/api/questions", params={"exam": "SSC-CGL", "subject": "MATH"}).json()["items"][0]["id"]
        c.post("/api/practice/answer", json={"question_id": qid, "chosen": 0})
        c.post(f"/api/bookmarks/{qid}")
        c.put("/api/learner/target", json={"exam": "SSC-MTS", "stage": "pre"})
        mock_id = c.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
        assert c.get("/api/lab").json()["storage"] == "turso"

    remote = turso.conn
    assert remote.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
    assert remote.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 1
    assert remote.execute("SELECT COUNT(*) FROM learner_params").fetchone()[0] > 0
    assert remote.execute("SELECT v FROM _sync").fetchone()[0] >= 4

    with deployed("instance-2") as c:  # nothing on this instance's disk yet
        stats = c.get("/api/stats").json()
        assert stats["totals"]["attempts"] == 1
        assert c.get("/api/questions", params={"status": "bookmarked"}).json()["total"] == 1
        assert c.get("/api/learner").json()["target"]["exam"] == "SSC-MTS"
        assert c.get(f"/api/mocks/{mock_id}").status_code == 200
        assert c.get("/api/questions", params={"status": "incorrect"}).json()["items"][0]["id"] == qid


def test_writes_from_another_instance_are_picked_up(deployed, turso):
    with deployed("a") as c:
        qid = c.get("/api/questions").json()["items"][0]["id"]
        assert c.get("/api/questions", params={"status": "bookmarked"}).json()["total"] == 0
        # Another instance saves a bookmark straight to Turso and bumps the version.
        turso.conn.execute("INSERT INTO bookmarks (question_id, created_at) VALUES (?, '2026-01-01 00:00:00')", (qid,))
        turso.conn.execute("UPDATE _sync SET v = v + 1")
        assert c.get("/api/questions", params={"status": "bookmarked"}).json()["total"] == 0  # within 2 s: cached
        persistence.get().checked_at = 0  # the freshness window has passed
        assert c.get("/api/questions", params={"status": "bookmarked"}).json()["total"] == 1


def test_deletes_and_updates_are_synced(deployed, turso):
    with deployed("x") as c:
        qid = c.get("/api/questions").json()["items"][0]["id"]
        c.post(f"/api/bookmarks/{qid}")
        c.post(f"/api/bookmarks/{qid}")  # toggled off again
        mock_id = c.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
        c.delete(f"/api/mocks/{mock_id}")
    assert turso.conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 0
    assert turso.conn.execute("SELECT COUNT(*) FROM mocks").fetchone()[0] == 0


def test_a_failed_save_is_reported_and_retried(deployed, turso):
    with deployed("y") as c:
        qid = c.get("/api/questions").json()["items"][0]["id"]
        turso.fail_next_write = True
        res = c.post(f"/api/bookmarks/{qid}")
        assert res.status_code == 503 and "could not be saved" in res.json()["detail"]
        assert turso.conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 0
        c.post("/api/practice/answer", json={"question_id": qid, "chosen": 1})  # the next write carries both
    assert turso.conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0] == 1
    assert turso.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_ai_generated_questions_are_restored_with_their_search_entries(deployed, turso):
    with deployed("gen") as c:
        conn = db.connect(c.app.state.db_path)
        with conn:
            conn.execute("INSERT INTO papers (id, exam, stage, title) VALUES ('ai-SSC-CGL-pre', 'SSC-CGL', 'pre', 'AI')")
            cur = conn.execute(
                "INSERT INTO questions (qid, paper_id, exam, stage, subject, chapter, question, options, answer, "
                "solution, marks_pos, marks_neg, origin) VALUES ('ai-1', 'ai-SSC-CGL-pre', 'SSC-CGL', 'pre', 'MATH', "
                "'profit-and-loss', 'A zebra-striped kite costs 40 rupees', '[\"1\",\"2\",\"3\",\"4\"]', 0, 's', 2, 0.5, 'ai')")
            conn.execute("INSERT INTO questions_fts (rowid, question) VALUES (?, ?)",
                         (cur.lastrowid, "A zebra-striped kite costs 40 rupees"))
        conn.close()
        c.post("/api/practice/answer", json={"question_id": cur.lastrowid, "chosen": 0})
    with deployed("gen-2") as c:
        found = c.get("/api/questions", params={"origin": "ai", "search": "zebra kite"}).json()
        assert found["total"] == 1
        assert c.get("/api/stats").json()["totals"]["attempts"] == 1


def test_local_mode_does_not_touch_turso(client):
    assert persistence.get() is None
    assert client.get("/api/lab").json()["storage"] == "local"
