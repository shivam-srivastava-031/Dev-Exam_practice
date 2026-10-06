"""Adaptive exam engine: chooses what to practise next from the learner model.

* Smart practice: due spaced-repetition reviews first, then questions from chapters
  ranked by (exam weightage x how unsure the model is about you), with a bonus for
  chapters you have not touched yet. Every pick carries the reason it was chosen.
* Personalised mock: the real exam pattern (sections, counts, timing, marking), with
  each section's questions spread over chapters by half exam weightage, half weakness.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timezone

from . import learner
from .catalog import EXAMS, stage_name
from .mocks import _draw, _section


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _pick(conn: sqlite3.Connection, subject: str, chapter: str, exam: str, stage: str,
          exclude: set[int], rng: random.Random, any_exam: bool = True) -> int | None:
    """An unattempted question from the chapter: this exam's recent papers first.

    Practice may borrow the chapter from other SSC exams; a mock (any_exam=False) may not.
    """
    base = ("SELECT id FROM questions WHERE origin = 'pyq' AND subject = ? AND chapter = ? "
            "AND id NOT IN (SELECT question_id FROM attempts)")
    tries = [(base + " AND exam = ? AND stage = ? AND year >= 2022", [exam, stage]),
             (base + " AND exam = ? AND stage = ?", [exam, stage])]
    if any_exam:
        tries.append((base, []))
    for sql, extra in tries:
        ids = [r[0] for r in conn.execute(sql + " ORDER BY RANDOM() LIMIT 40", [subject, chapter, *extra])
               if r[0] not in exclude]
        if ids:
            return rng.choice(ids)
    return None


def smart_batch(conn: sqlite3.Connection, n: int = 10, subject: str | None = None,
                exclude: set[int] | None = None) -> list[dict]:
    exclude = set(exclude or ())
    exam, stage = learner.target(conn)
    rng = random.Random()
    picks: list[dict] = []

    sql = ("SELECT r.question_id, r.lapses, q.chapter FROM reviews r JOIN questions q ON q.id = r.question_id "
           "WHERE r.due <= ?")
    params: list = [_now()]
    if subject:
        sql += " AND q.subject = ?"
        params.append(subject)
    for qid, lapses, _ in conn.execute(sql + " ORDER BY r.due LIMIT ?", [*params, max(1, n * 3 // 10)]):
        if qid not in exclude:
            picks.append({"id": qid, "kind": "review",
                          "reason": "Review: you missed this before" + (f" ({lapses}×)" if lapses > 1 else "")})
            exclude.add(qid)

    chapters = [c for c in learner.mastery(conn, exam, stage) if not subject or c["subject"] == subject]
    per_chapter: dict[str, int] = {}
    guard = 0
    while len(picks) < n and chapters and guard < n * 6:
        guard += 1
        ch = rng.choices(chapters, weights=[max(c["priority"], 1e-4) for c in chapters])[0]
        key = f"{ch['subject']}/{ch['chapter']}"
        if per_chapter.get(key, 0) >= 3:  # keep a session varied
            continue
        qid = _pick(conn, ch["subject"], ch["chapter"], exam, stage, exclude, rng)
        if qid is None:
            chapters.remove(ch)
            continue
        per_chapter[key] = per_chapter.get(key, 0) + 1
        exclude.add(qid)
        if ch["status"] == "new":
            reason, kind = f"New topic for you: {ch['label']}", "new"
        elif ch["status"] == "weak":
            reason, kind = f"Weak topic: {ch['label']} (model predicts {ch['predicted']:.0%})", "weak"
        else:
            reason, kind = (f"High weightage: {ch['label']} is {ch['share']:.0%} of "
                            f"{EXAMS[exam]} {stage_name(exam, stage)} {ch['subject_name']}"), "weightage"
        picks.append({"id": qid, "kind": kind, "reason": reason})
    return picks


def build_adaptive_layout(conn: sqlite3.Connection, pattern: dict) -> dict:
    chapters = learner.mastery(conn, pattern["exam"], pattern["stage"])
    rng = random.Random()
    taken: set[int] = set()
    parts = []
    for part in pattern["parts"]:
        sections = []
        for sec in part["sections"]:
            subj = [c for c in chapters if c["subject"] == sec["subject"]]
            total_share = sum(c["share"] for c in subj) or 1
            total_pri = sum(c["priority"] for c in subj) or 1
            weights = [0.5 * c["share"] / total_share + 0.5 * c["priority"] / total_pri for c in subj]
            # Largest-remainder apportionment of the section's question count.
            raw = [w * sec["count"] for w in weights]
            alloc = [int(r) for r in raw]
            for i in sorted(range(len(raw)), key=lambda i: raw[i] - alloc[i], reverse=True)[:sec["count"] - sum(alloc)]:
                alloc[i] += 1
            ids: list[int] = []
            for c, k in zip(subj, alloc):
                for _ in range(k):
                    qid = _pick(conn, c["subject"], c["chapter"], pattern["exam"], pattern["stage"], taken, rng,
                                any_exam=False)
                    if qid is not None:
                        ids.append(qid)
                        taken.add(qid)
            if len(ids) < sec["count"]:
                extra = _draw(conn, pattern["exam"], pattern["stage"], sec["subject"], sec["count"] - len(ids), taken, True)
                ids += extra
                taken.update(extra)
            rng.shuffle(ids)
            sections.append(_section(sec["subject"], ids, sec["correct"], sec["wrong"]))
        parts.append({"name": part["name"], "minutes": part["minutes"], "sections": sections})
    title = f"{EXAMS[pattern['exam']]} {stage_name(pattern['exam'], pattern['stage'])} - Personalised Mock"
    return {"kind": "adaptive", "pattern_id": pattern["id"], "paper_id": None, "title": title, "parts": parts}
