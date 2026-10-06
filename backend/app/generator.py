"""Fresh SSC-style questions from Gemini, grounded in real PYQs and self-verified.

1. Retrieve: text-only PYQs of the requested chapter (this exam stage first, recent years
   first) become style and difficulty examples, the RAG step.
2. Generate: Gemini writes new questions under a JSON schema.
3. Verify: an independent second call solves them without seeing the key; a question
   survives only if both answers agree. The trained topic model must also place it in
   the right subject, and near-copies of a real PYQ (cosine >= 0.97) are dropped.
4. Store: survivors join the bank with origin = 'ai'. They can be practised, but never
   enter previous-year papers, random mocks or the RAG index.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import uuid

from . import ai, rag, topic_model
from .catalog import EXAMS, PATTERNS, SUBJECTS, chapter_label, stage_name

GEN_SYSTEM = """You are a senior question setter for Indian SSC exams. You write original multiple-choice
questions that match the real exam's style, difficulty and syllabus exactly, with one unambiguous correct answer."""

SOLVE_SYSTEM = """You are a meticulous SSC exam solver. Solve each question independently and carefully,
checking arithmetic twice. Answer with the 1-based number of the correct option."""

QUESTION_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "question": {"type": "STRING"},
            "options": {"type": "ARRAY", "items": {"type": "STRING"}},
            "answer": {"type": "INTEGER", "description": "1-based index of the correct option"},
            "solution": {"type": "STRING"},
        },
        "required": ["question", "options", "answer", "solution"],
    },
}

SOLVE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {"number": {"type": "INTEGER"}, "answer": {"type": "INTEGER"}},
        "required": ["number", "answer"],
    },
}


def _examples(conn: sqlite3.Connection, exam: str, stage: str, subject: str, chapter: str, k: int = 6) -> list[sqlite3.Row]:
    rows = conn.execute("""
        SELECT question, options, answer, solution FROM questions
        WHERE origin = 'pyq' AND subject = ? AND chapter = ?
          AND question NOT LIKE '%[IMAGE%' AND options NOT LIKE '%[IMAGE%'
        ORDER BY (exam = ? AND stage = ?) DESC, year DESC LIMIT 60""",
                        (subject, chapter, exam, stage)).fetchall()
    return random.sample(rows, min(k, len(rows)))


def _ai_paper(conn: sqlite3.Connection, exam: str, stage: str) -> str:
    paper_id = f"ai-{exam}-{stage}"
    conn.execute("INSERT OR IGNORE INTO papers (id, exam, stage, title) VALUES (?, ?, ?, ?)",
                  (paper_id, exam, stage, f"AI-generated practice · {EXAMS[exam]} {stage_name(exam, stage)}"))
    return paper_id


def _marks(exam: str, stage: str, subject: str) -> tuple[float, float]:
    for p in PATTERNS:
        if p["exam"] == exam and p["stage"] == stage and not p.get("legacy"):
            for part in p["parts"]:
                for sec in part["sections"]:
                    if sec["subject"] == subject:
                        return sec["correct"], sec["wrong"]
    return 2.0, 0.5


_LATEX = [
    (re.compile(r"\\left|\\right"), ""),
    (re.compile(r"\\(?:text|mathrm|mathbf|textbf)\{([^{}]*)\}"), r"\1"),
    (re.compile(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}"), r"(\1)/(\2)"),
    (re.compile(r"\\sqrt\{([^{}]*)\}"), r"√(\1)"),
    (re.compile(r"\\times"), "×"), (re.compile(r"\\div"), "÷"), (re.compile(r"\\cdot"), "·"),
    (re.compile(r"\\(?:rightarrow|to)\b"), "→"), (re.compile(r"\\%"), "%"),
    (re.compile(r"\^\{?2\}?(?!\d)"), "²"), (re.compile(r"\^\{?3\}?(?!\d)"), "³"),
    (re.compile(r"\$\$?"), ""),
]


def delatex(text: str) -> str:
    """Generated text is stored, so stray LaTeX is rewritten into plain Unicode first."""
    for _ in range(3):
        for pattern, rep in _LATEX:
            text = pattern.sub(rep, text)
    return text.strip()


def _valid(item: dict) -> str | None:
    opts = [str(o).strip() for o in item.get("options") or []]
    if len(opts) != 4 or any(not o for o in opts):
        return "needs exactly four non-empty options"
    if len({o.lower() for o in opts}) != 4:
        return "options repeat"
    if not isinstance(item.get("answer"), int) or not 1 <= item["answer"] <= 4:
        return "answer out of range"
    if len(str(item.get("question", "")).strip()) < 15:
        return "question too short"
    return None


async def generate(conn: sqlite3.Connection, exam: str, stage: str, subject: str, chapter: str,
                   count: int = 5) -> dict:
    examples = _examples(conn, exam, stage, subject, chapter)
    if len(examples) < 2:
        raise ValueError("not enough text-only PYQs in this chapter to learn its style from")
    label = chapter_label(chapter)
    shots = []
    for i, ex in enumerate(examples, 1):
        opts = json.loads(ex["options"])
        shots.append(f"Example {i}:\n{ex['question']}\n" + "\n".join(f"{j + 1}) {o}" for j, o in enumerate(opts))
                     + f"\nAnswer: {ex['answer'] + 1}\nSolution: {(ex['solution'] or '')[:500]}")
    prompt = (
        f"Exam: {EXAMS[exam]} {stage_name(exam, stage)}. Subject: {SUBJECTS[subject]}. Topic: {label}.\n\n"
        "Real previous-year questions on this topic, for style and difficulty only:\n\n" + "\n\n".join(shots) +
        f"\n\nWrite {count} NEW questions on this topic for this exam, at the same difficulty.\n"
        "Rules: do not copy or lightly reword the examples (change the scenario, names and numbers); "
        "exactly 4 options with exactly one correct and distractors that reflect common mistakes; "
        "double-check every calculation; text only, no figures; maths in plain Unicode (no LaTeX); "
        "'answer' is the 1-based number of the correct option; 'solution' is a concise worked solution "
        "that ends by naming the correct option."
    )
    items = await ai.generate_json(GEN_SYSTEM, prompt, QUESTION_SCHEMA, temperature=0.9)
    items = items if isinstance(items, list) else []

    dropped, candidates = [], []
    for item in items[:count]:
        if isinstance(item, dict):
            item.update(question=delatex(str(item.get("question", ""))), solution=delatex(str(item.get("solution", ""))),
                        options=[delatex(str(o)) for o in item.get("options") or []])
        reason = _valid(item)
        if reason:
            dropped.append({"question": str(item.get("question", ""))[:120], "reason": reason})
        else:
            candidates.append(item)

    if candidates:
        listing = "\n\n".join(f"Question {i}:\n{c['question']}\n" + "\n".join(f"{j + 1}) {o}" for j, o in enumerate(c["options"]))
                              for i, c in enumerate(candidates, 1))
        solved = await ai.generate_json(SOLVE_SYSTEM, "Solve these questions:\n\n" + listing, SOLVE_SCHEMA, temperature=0.0)
        verdicts = {s.get("number"): s.get("answer") for s in solved if isinstance(s, dict)} if isinstance(solved, list) else {}
    else:
        verdicts = {}

    index = rag.get_index()
    topics = topic_model.predict([topic_model.model_text(c["question"], c["options"]) for c in candidates]) if candidates else None
    kept = []
    for i, c in enumerate(candidates, 1):
        if verdicts.get(i) != c["answer"]:
            dropped.append({"question": c["question"][:120],
                            "reason": f"independent solver chose option {verdicts.get(i)}, generator said {c['answer']}"})
            continue
        if topics and topics[i - 1][0]["subject"] != subject and topics[i - 1][0]["probability"] >= 0.6:
            dropped.append({"question": c["question"][:120], "reason": f"topic model says {topics[i - 1][0]['subject']}, not {subject}"})
            continue
        if index is not None:
            nearest = index.top(rag.query_vector(rag.embed_text(c["question"], c["options"])), 1)
            if nearest and nearest[0][1] >= 0.97:
                dropped.append({"question": c["question"][:120], "reason": "too close to an existing PYQ"})
                continue
        kept.append(c)

    ids = []
    with conn:
        paper_id = _ai_paper(conn, exam, stage)
        pos, neg = _marks(exam, stage, subject)
        for c in kept:
            cur = conn.execute(
                "INSERT INTO questions (qid, paper_id, exam, stage, subject, chapter, question, options, answer, "
                "solution, marks_pos, marks_neg, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai')",
                (f"ai-{uuid.uuid4().hex[:16]}", paper_id, exam, stage, subject, chapter, c["question"].strip(),
                 json.dumps([str(o).strip() for o in c["options"]], ensure_ascii=False), c["answer"] - 1,
                 c["solution"].strip(), pos, neg))
            ids.append(cur.lastrowid)
            conn.execute("INSERT INTO questions_fts (rowid, question) VALUES (?, ?)", (cur.lastrowid, c["question"].strip()))
        conn.execute("UPDATE papers SET question_count = question_count + ?, max_marks = max_marks + ? WHERE id = ?",
                     (len(kept), len(kept) * pos, paper_id))
    return {"requested": count, "generated": len(items), "verified": len(kept), "ids": ids,
            "dropped": dropped, "examples_used": len(examples), "topic": label}
