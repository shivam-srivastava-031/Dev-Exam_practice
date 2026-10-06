"""Build mock tests and grade them.

A mock's layout mirrors the real CBT: timed parts, each holding ordered sections of
question ids with that section's marking scheme. Two ways to fill it:

* paper    - replay one real previous-year shift, in its original question order
* random   - draw a fresh paper from the exam's pool, section by section
* adaptive - the same pattern, weighted to the learner's weak chapters (see engine.py)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter

from .catalog import EXAMS, SUBJECTS, pattern_for_paper, stage_name


def _section(subject: str, ids: list[int], correct: float, wrong: float) -> dict:
    return {"subject": subject, "name": SUBJECTS[subject], "question_ids": ids,
            "correct": correct, "wrong": wrong}


def build_paper_layout(conn: sqlite3.Connection, paper_id: str) -> dict:
    paper = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if paper is None:
        raise LookupError("paper not found")
    rows = conn.execute(
        "SELECT id, subject, marks_pos, marks_neg FROM questions WHERE paper_id = ? ORDER BY n, id",
        (paper_id,),
    ).fetchall()
    by_subject: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_subject.setdefault(r["subject"], []).append(r)

    common_marks = Counter(r["marks_pos"] for r in rows).most_common(1)
    pattern = pattern_for_paper(paper["exam"], paper["stage"], common_marks[0][0] if common_marks else None)

    parts = []
    for part in pattern["parts"]:
        sections = []
        for sec in part["sections"]:
            qs = by_subject.pop(sec["subject"], [])
            if qs:
                # Marks are uniform within a section of a real paper; take the
                # most common value so one odd row cannot skew the scheme.
                pos = Counter(r["marks_pos"] for r in qs).most_common(1)[0][0]
                neg = Counter(r["marks_neg"] for r in qs).most_common(1)[0][0]
                sections.append(_section(sec["subject"], [r["id"] for r in qs], pos, neg))
        if sections:
            parts.append({"name": part["name"], "minutes": part["minutes"], "sections": sections})
    # A subject the pattern does not list still belongs to the paper: keep it.
    if by_subject and not parts:
        parts.append({"name": pattern["parts"][0]["name"], "minutes": pattern["parts"][0]["minutes"], "sections": []})
    for subject, qs in by_subject.items():
        parts[-1]["sections"].append(
            _section(subject, [r["id"] for r in qs], qs[0]["marks_pos"], qs[0]["marks_neg"]))
    if len(pattern["parts"]) == 1 and paper["duration_min"]:
        parts[0]["minutes"] = paper["duration_min"]

    return {"kind": "paper", "pattern_id": pattern["id"], "paper_id": paper_id,
            "title": paper["title"], "parts": parts}


def build_random_layout(conn: sqlite3.Connection, pattern: dict, fresh_only: bool) -> dict:
    taken: set[int] = set()
    parts = []
    for part in pattern["parts"]:
        sections = []
        for sec in part["sections"]:
            ids = _draw(conn, pattern["exam"], pattern["stage"], sec["subject"], sec["count"], taken, fresh_only)
            taken.update(ids)
            sections.append(_section(sec["subject"], ids, sec["correct"], sec["wrong"]))
        parts.append({"name": part["name"], "minutes": part["minutes"], "sections": sections})
    title = f"{EXAMS[pattern['exam']]} {stage_name(pattern['exam'], pattern['stage'])} - Full Mock"
    return {"kind": "random", "pattern_id": pattern["id"], "paper_id": None, "title": title, "parts": parts}


def _draw(conn, exam: str, stage: str, subject: str, count: int, taken: set[int], fresh_only: bool) -> list[int]:
    """Random questions from the exam's own pool, preferring ones never attempted.

    If the learner has exhausted the fresh pool, fall back to seen questions rather
    than serve a short section.
    """
    picked: list[int] = []
    for only_fresh in ([True, False] if fresh_only else [False]):
        need = count - len(picked)
        if need <= 0:
            break
        exclude = taken | set(picked)
        sql = "SELECT id FROM questions WHERE exam = ? AND stage = ? AND subject = ? AND origin = 'pyq'"
        params: list = [exam, stage, subject]
        if exclude:
            sql += f" AND id NOT IN ({','.join('?' * len(exclude))})"
            params += list(exclude)
        if only_fresh:
            sql += " AND id NOT IN (SELECT question_id FROM attempts)"
        sql += " ORDER BY RANDOM() LIMIT ?"
        picked += [r[0] for r in conn.execute(sql, [*params, need])]
    return picked


def create_mock(conn: sqlite3.Connection, layout: dict) -> str:
    mock_id = uuid.uuid4().hex[:12]
    with conn:
        conn.execute(
            "INSERT INTO mocks (id, kind, pattern_id, paper_id, title, layout) VALUES (?, ?, ?, ?, ?, ?)",
            (mock_id, layout["kind"], layout["pattern_id"], layout["paper_id"], layout["title"],
             json.dumps({"parts": layout["parts"]})),
        )
    return mock_id


def question_ids(layout: dict) -> list[int]:
    return [qid for part in layout["parts"] for sec in part["sections"] for qid in sec["question_ids"]]


def grade(conn: sqlite3.Connection, layout: dict, responses: dict[str, dict]) -> dict:
    """Score a submission exactly as SSC does: +correct, -wrong, 0 for unanswered.

    'Answered & marked for review' counts as answered, as in the real exam.
    """
    ids = question_ids(layout)
    keys = {r["id"]: r for r in conn.execute(
        f"SELECT id, answer, options, chapter FROM questions WHERE id IN ({','.join('?' * len(ids))})", ids)}

    per_question: dict[int, dict] = {}
    sections_out = []
    for part in layout["parts"]:
        for sec in part["sections"]:
            stats = Counter()
            for qid in sec["question_ids"]:
                resp = responses.get(str(qid)) or {}
                chosen = resp.get("chosen")
                n_options = len(json.loads(keys[qid]["options"]))
                if not isinstance(chosen, int) or not 0 <= chosen < n_options:
                    chosen = None
                answer = keys[qid]["answer"]
                if chosen is None:
                    status, marks = "skipped", 0.0
                elif chosen == answer:
                    status, marks = "correct", sec["correct"]
                else:
                    status, marks = "wrong", -sec["wrong"]
                ms = max(0, int(resp.get("ms") or 0))
                stats[status] += 1
                stats["score"] += marks
                stats["ms"] += ms
                per_question[qid] = {"chosen": chosen, "answer": answer, "status": status, "marks": marks,
                                     "ms": ms, "marked": bool(resp.get("marked"))}
            total = len(sec["question_ids"])
            attempted = stats["correct"] + stats["wrong"]
            sections_out.append({
                "part": part["name"], "subject": sec["subject"], "name": sec["name"], "total": total,
                "attempted": attempted, "correct": stats["correct"], "wrong": stats["wrong"],
                "skipped": stats["skipped"], "score": round(stats["score"], 2),
                "max_score": round(total * sec["correct"], 2), "ms": stats["ms"],
                "accuracy": round(stats["correct"] / attempted, 4) if attempted else None,
            })

    def total(key: str) -> float:
        return sum(s[key] for s in sections_out)

    attempted = total("attempted")
    return {
        "score": round(total("score"), 2),
        "max_score": round(total("max_score"), 2),
        "total": total("total"), "attempted": attempted, "correct": total("correct"),
        "wrong": total("wrong"), "skipped": total("skipped"),
        "accuracy": round(total("correct") / attempted, 4) if attempted else None,
        "sections": sections_out,
        "questions": per_question,
    }
