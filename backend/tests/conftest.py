"""Build a tiny dataset in the real on-disk layout and import it into a temp DB."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, db, importer
from app.main import app


def make_row(qid: str, exam: str, tier: str, subject: str, n: int, *, paper: str, title: str,
             held_on: str = "18 Jul 2023", marks: tuple[float, float] = (2.0, 0.5), correct: str = "2",
             **extra) -> dict:
    row = {
        "qid": qid, "exam": exam, "tier": tier, "subject": subject, "n": n, "year": 2023,
        "paper": f"{exam}/x/{paper}.json", "held_on": held_on, "shift": "1",
        "question": f"Question {qid}?", "correct": correct,
        "options": [{"label": str(i), "text": f"opt {i}"} for i in range(1, 5)],
        "solution": "Because. [IMAGE: https://cdn.repeatermock.com/tb/"
                    "36f36cd5ece4186095afcf2ffbfb7b80cc04b6c2e3226e7477a419a9fbc96f85.png] **Key Points** � done",
        "chapter": "profit-and-loss", "concept": "unclassified",
        "marks_pos": marks[0], "marks_neg": marks[1], "type": "mcq",
        "paper_meta": {"test_id": paper, "title": title, "duration_min": 60},
    }
    row.update(extra)
    return row


def write_dataset(root: Path, rows: list[dict]) -> None:
    for row in rows:
        path = root / row["exam"] / row["tier"] / row["subject"] / "questions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


@pytest.fixture()
def dataset_rows() -> list[dict]:
    rows = []
    # One complete CGL Tier-I paper: 4 sections x 3 questions, stored out of order.
    for subject in ("ENG", "MATH", "GK", "REAS"):
        for n in (3, 1, 2):
            rows.append(make_row(f"cgl-{subject}-{n}", "SSC-CGL", "pre", subject, n,
                                 paper="P1", title="SSC CGL 2023 Tier-I (Held On: 18 Jul 2023 Shift 1)"))
    # Old-pattern and new-pattern MTS papers, told apart by their marks.
    for subject in ("REAS", "MATH", "ENG", "GK"):
        rows.append(make_row(f"mts-old-{subject}", "SSC-MTS", "pre", subject, 1, paper="M1",
                             title="SSC MTS 2019 Official Paper", held_on="2019-08-02", marks=(1.0, 0.25)))
        neg = 0.0 if subject in ("MATH", "REAS") else 1.0
        rows.append(make_row(f"mts-new-{subject}", "SSC-MTS", "pre", subject, 1, paper="M2",
                             title="SSC MTS 2024 Official Paper", held_on="2024-10-15", marks=(3.0, neg)))
    # A Stenographer paper misfiled under GD in the source.
    rows.append(make_row("steno-1", "SSC-GD", "pre", "ENG", 1, paper="S1",
                         title="SSC Stenographer 2025 Official Paper (Held On: 07 Aug, 2025 Shift 1)",
                         held_on="2025-08-07", marks=(1.0, 0.25)))
    # Malformed rows the importer must skip.
    bad = make_row("bad-dup", "SSC-CGL", "pre", "ENG", 9, paper="P1", title="x")
    bad["options"] = [{"label": "1", "text": "a"}, {"label": "4", "text": "b"}, {"label": "4", "text": "c"}]
    rows.append(bad)
    rows.append(make_row("bad-empty", "SSC-CGL", "pre", "ENG", 10, paper="P1", title="x", question=" "))
    return rows


@pytest.fixture()
def client(tmp_path: Path, dataset_rows: list[dict], monkeypatch):
    # Never touch the real vector index, trained models or exports.
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "INDEX_DIR", tmp_path / "data" / "index")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "data" / "models")
    source = tmp_path / "source"
    write_dataset(source, dataset_rows)
    conn = db.connect(tmp_path / "test.db")
    db.init(conn)
    importer.run_import(conn, source)
    conn.close()
    app.state.db_path = tmp_path / "test.db"
    with TestClient(app) as c:
        yield c
