"""Export the PYQ bank as a supervised fine-tuning dataset.

Gemini model tuning is not enabled for the configured API key (the tunedModels API
returns 501) and there is no local GPU, so this writes ready-to-upload JSONL for
whichever route becomes available:

  vertex_gemini/{train,val}.jsonl  Vertex AI supervised tuning for Gemini
                                   ({"systemInstruction", "contents": [user, model]})
  chat/{train,val}.jsonl           {"messages": [...]} for Hugging Face TRL / Unsloth
                                   LoRA on open models (Gemma, Llama, Qwen)

Task taught: given an SSC question and its options, give the correct option and a
worked solution. Only text-only questions with a substantial solution are used.

    python -m app.finetune --out ../data/finetune --max 20000
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from . import config, db
from .catalog import EXAMS, SUBJECTS, chapter_label, stage_name

SYSTEM = ("You are an expert solver for Indian SSC exams. Given a multiple-choice question, state the correct "
          "option, then give a concise step-by-step solution with the fastest exam method.")
_FIGURE = re.compile(r"\[IMAGE:|<img", re.I)
_CLEAN = re.compile(r"<[^>]+>")


def _example(r: sqlite3.Row) -> dict | None:
    options = json.loads(r["options"])
    solution = _CLEAN.sub("", r["solution"] or "").strip()
    if _FIGURE.search(r["question"]) or any(_FIGURE.search(o) for o in options) or _FIGURE.search(solution):
        return None
    if not 80 <= len(solution) <= 4000:
        return None
    prompt = (f"{EXAMS[r['exam']]} {stage_name(r['exam'], r['stage'])} · {SUBJECTS[r['subject']]} · "
              f"{chapter_label(r['chapter'])}\n\n{r['question'].strip()}\n\n"
              + "\n".join(f"{i + 1}) {o}" for i, o in enumerate(options)))
    target = f"Answer: option {r['answer'] + 1} ({options[r['answer']]})\n\n{solution}"
    return {"id": r["id"], "subject": r["subject"], "prompt": prompt, "target": target}


def export(conn: sqlite3.Connection, out: Path, max_examples: int = 20000, seed: int = 7) -> dict:
    rows = conn.execute("SELECT id, exam, stage, subject, chapter, question, options, answer, solution "
                        "FROM questions WHERE origin = 'pyq'").fetchall()
    examples = [e for e in map(_example, rows) if e]
    eligible = len(examples)
    random.Random(seed).shuffle(examples)
    examples = examples[:max_examples]
    splits = {"train": [e for e in examples if e["id"] % 20 != 0], "val": [e for e in examples if e["id"] % 20 == 0]}
    for fmt in ("vertex_gemini", "chat"):
        (out / fmt).mkdir(parents=True, exist_ok=True)
        for split, items in splits.items():
            with (out / fmt / f"{split}.jsonl").open("w", encoding="utf-8") as fh:
                for e in items:
                    if fmt == "vertex_gemini":
                        rec = {"systemInstruction": {"role": "system", "parts": [{"text": SYSTEM}]},
                               "contents": [{"role": "user", "parts": [{"text": e["prompt"]}]},
                                            {"role": "model", "parts": [{"text": e["target"]}]}]}
                    else:
                        rec = {"messages": [{"role": "system", "content": SYSTEM},
                                            {"role": "user", "content": e["prompt"]},
                                            {"role": "assistant", "content": e["target"]}]}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    stats = {"exported_at": time.strftime("%Y-%m-%d %H:%M:%S"), "pyq_rows": len(rows), "eligible": eligible,
             "examples": len(examples), "train": len(splits["train"]), "val": len(splits["val"]),
             "by_subject": dict(Counter(e["subject"] for e in examples)), "out": str(out.resolve())}
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    return stats


def status(out: Path | None = None) -> dict:
    path = (out or config.DATA_DIR / "finetune") / "stats.json"
    return {"ready": path.is_file(), **(json.loads(path.read_text()) if path.is_file() else {})}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=config.DATA_DIR / "finetune")
    parser.add_argument("--max", type=int, default=20000)
    args = parser.parse_args()
    conn = db.connect(config.DB_PATH)
    db.init(conn)
    print(json.dumps(export(conn, args.out, args.max), indent=2))
