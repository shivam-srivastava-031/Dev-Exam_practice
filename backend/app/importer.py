"""Build the SQLite question bank from the subject-wise SSC PYQ dataset.

    python -m app.importer                 # clone the dataset if missing, import, index, train
    python -m app.importer --source DIR    # import from an existing checkout
    python -m app.importer --skip-ml       # questions only: no vector index or topic model

The dataset (github.com/akarohitmishra/repeatermock-subjectwise-db) is laid out as
<exam>/<pre|mains>/<SUBJECT>/questions.jsonl with one previous-year question per
line. Re-running is safe: questions are upserted on (qid, paper) so their ids, and
the attempts and mocks that point at them, stay valid.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import config, db
from .catalog import EXAMS, SUBJECTS

IMAGE_TAG = re.compile(r"\[IMAGE:\s*([^\]\s]+)\s*\]")
# Some solutions are cut off mid-tag in the source, so the closing '>' is optional.
IMG_HTML = re.compile(r"""<img\b[^>]*?\bsrc=["']?([^"'\s>]*)["']?[^>]*(?:>|$)""", re.IGNORECASE)

# Paper titles name their exam; one Stenographer paper is filed under SSC-GD in
# the source, so a title that clearly names another exam wins.
_TITLE_EXAMS = [
    ("SSC-Stenographer", re.compile(r"stenographer", re.I)),
    ("SSC-Selection-Post", re.compile(r"selection\s*post", re.I)),
    ("SSC-CHSL", re.compile(r"\bchsl\b", re.I)),
    ("SSC-CGL", re.compile(r"\bcgl\b", re.I)),
    ("SSC-CPO", re.compile(r"\bcpo\b", re.I)),
    ("SSC-GD", re.compile(r"\bgd\b", re.I)),
    ("SSC-MTS", re.compile(r"\bmts\b", re.I)),
]

# Decorative 180x180 badges ("Key Points", "Additional Information", ...) that the
# source pastes into ~130k solutions. Found by measuring the most frequent solution
# images; real diagrams are never square badges of these sizes.
ICON_HASHES = (
    "36f36cd5ece4186095afcf2ffbfb7b80cc04b6c2e3226e7477a419a9fbc96f85",
    "f13e2ad6582c16aa62a83672e2547ad20a01fddb61962a1acce42eb9a1a99caa",
    "ca81259519cadfb2d5350673c220e419e60378e9770ad26512fc8e826a8d93f1",
    "c76c13ba2d2ab46b47f8a931ae621dac081a0cb266b142dc23f58659fac4a466",
    "4f750a2730aad0987b5e719f277ddc90129c657928205c4d3b5aa23049023fa5",
    "e3594069eb4eac3d559af7c626aabe4ee4145967932cf8ad78e35476f134a4b8",
    "3e20068b61817b168623bbf4377a66602deb382bfb1e34ae29a5e35078dcefcb",
    "51867102c7c2f369f6e29d2efe025e3b3367c68261ec20262828d84ca5c8f854",
    "e085a7f17645c801966c030eb44d69fae944b96fdb4c4077f5fba1bc2c24bc71",
    "9074044cf96dc1d09ca89eac99180c3d7440ade1b364d09d47931959bf693cc8",
    "0a6c2db12945bba9d674d33112fce66b07f00093d75dae93749f9bdb7bd34e7a",
    "7a63139f3b57ffe3814d27f8dcaee8cb420142f7146b480d479d526074519671",
)

UPSERT_PAPER = """
INSERT INTO papers (id, exam, stage, title, year, held_on, shift, duration_min)
VALUES (:id, :exam, :stage, :title, :year, :held_on, :shift, :duration_min)
ON CONFLICT(id) DO UPDATE SET
    exam = excluded.exam, stage = excluded.stage, title = excluded.title, year = excluded.year,
    held_on = excluded.held_on, shift = excluded.shift, duration_min = excluded.duration_min
"""

UPSERT_QUESTION = """
INSERT INTO questions (qid, paper_id, exam, stage, subject, chapter, concept, year, n,
                       question, options, answer, solution, marks_pos, marks_neg)
VALUES (:qid, :paper_id, :exam, :stage, :subject, :chapter, :concept, :year, :n,
        :question, :options, :answer, :solution, :marks_pos, :marks_neg)
ON CONFLICT(qid, paper_id) DO UPDATE SET
    exam = excluded.exam, stage = excluded.stage, subject = excluded.subject,
    chapter = excluded.chapter, concept = excluded.concept, year = excluded.year, n = excluded.n,
    question = excluded.question, options = excluded.options, answer = excluded.answer,
    solution = excluded.solution, marks_pos = excluded.marks_pos, marks_neg = excluded.marks_neg
"""


_SIMPLE = re.compile(r"^[\w.√²³]+$")
_SQRT = re.compile(r"(?<![A-Za-z])sqrt\{([^{}]*)\}")
_SQRT_BARE = re.compile(r"(?<![A-Za-z])sqrt\s*(?=\d|[a-z]\b)")
_DOUBLE_BRACES = re.compile(r"\{\{([^{}]*)\}\}")
_FRAC = re.compile(r"(?<![A-Za-z])frac\{([^{}]*)\}\{([^{}]*)\}")
_FRAC_LOOSE = re.compile(r"(?<![A-Za-z])frac\{([^{}]*)\}(\w)")
_TIMES = re.compile(r"(?<=[\d)√])\s*times\s*(?=[\d(√])")


def prettify_math(text: str) -> str:
    """Rewrite leftover LaTeX ('frac{sqrt{21} b}{2}', '2sqrt{3}') in the dataset's
    own plain style ('(√21 b)/(2)', '2√3'); ~1,700 questions carry these."""
    text = text.replace("​", "")
    if "sqrt" not in text and "frac" not in text:
        return text

    def wrap(s: str) -> str:
        return s if _SIMPLE.match(s) else f"({s})"

    for _ in range(12):  # innermost first, until nothing nested is left
        new = _DOUBLE_BRACES.sub(r"{\1}", text)
        new = _SQRT.sub(lambda m: "√" + wrap(m.group(1).strip()), new)
        new = _FRAC.sub(lambda m: f"{wrap(m.group(1).strip())}/{wrap(m.group(2).strip())}", new)
        new = _FRAC_LOOSE.sub(lambda m: f"{wrap(m.group(1).strip())}/{m.group(2)}", new)
        if new == text:
            break
        text = new
    return _TIMES.sub(" × ", _SQRT_BARE.sub("√", text))


def clean_markup(text: str | None, *, solution: bool = False) -> str:
    """Drop decorative badges and 1x1 tracking GIFs; fix mangled bullets and maths."""
    if not text:
        return ""
    text = prettify_math(text)

    def keep_image(m: re.Match) -> str:
        url = m.group(1)
        # Prefix match: truncated tags carry only part of the badge's hash.
        is_icon = url.startswith("data:") or any(h[:16] in url for h in ICON_HASHES)
        return "" if is_icon else m.group(0)

    text = IMG_HTML.sub(keep_image, IMAGE_TAG.sub(keep_image, text))
    if solution:
        text = text.replace("�", "•")
    return text.strip()


def parse_held_on(value: str | None) -> str | None:
    """'2023-10-26', '12 April 2022' and '5 Jul 2023' all occur; normalise to ISO."""
    if not value:
        return None
    value = re.sub(r"\bSept\b", "Sep", value.strip())
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def exam_from_title(title: str) -> str | None:
    named = {code for code, pattern in _TITLE_EXAMS if pattern.search(title)}
    return named.pop() if len(named) == 1 else None


def convert(row: dict) -> tuple[dict, dict] | str:
    """Map one dataset row to (paper, question) records, or return why it was skipped."""
    question = (row.get("question") or "").strip()
    options = row.get("options") or []
    if not question:
        return "empty question text"
    if len(options) < 2:
        return "fewer than two options"
    labels = [str(o.get("label")) for o in options]
    if len(set(labels)) != len(labels):
        return "duplicate option labels (answer is ambiguous)"
    correct = str(row.get("correct"))
    if correct not in labels:
        return "answer is not one of the options"
    if row.get("exam") not in EXAMS or row.get("subject") not in SUBJECTS:
        return "unknown exam or subject"

    meta = row.get("paper_meta") or {}
    paper_id = str(meta.get("test_id") or row["paper"])
    title = meta.get("title") or Path(row["paper"]).stem
    held_on = parse_held_on(row.get("held_on"))
    exam, year = row["exam"], row.get("year")
    titled = exam_from_title(title)
    if titled and titled != exam:
        exam, year = titled, int(held_on[:4]) if held_on else year
    paper = {
        "id": paper_id,
        "exam": exam,
        "stage": row["tier"],
        "title": title,
        "year": year,
        "held_on": held_on,
        "shift": row.get("shift") or meta.get("shift"),
        "duration_min": meta.get("duration_min"),
    }
    record = {
        "qid": row["qid"],
        "paper_id": paper_id,
        "exam": exam,
        "stage": row["tier"],
        "subject": row["subject"],
        "chapter": row.get("chapter"),
        "concept": None if row.get("concept") == "unclassified" else row.get("concept"),
        "year": year,
        "n": row.get("n"),
        "question": clean_markup(question),
        "options": json.dumps([clean_markup(o.get("text")) for o in options], ensure_ascii=False),
        "answer": labels.index(correct),
        "solution": clean_markup(row.get("solution"), solution=True),
        "marks_pos": float(row.get("marks_pos") or 0),
        "marks_neg": float(row.get("marks_neg") or 0),
    }
    return paper, record


def ensure_dataset(source: Path) -> None:
    if any(source.glob("*/*/*/questions.jsonl")):
        return
    print(f"Dataset not found in {source}; cloning {config.DATASET_REPO} ...")
    source.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", config.DATASET_REPO, str(source)], check=True)


def run_import(conn, source: Path) -> dict:
    files = sorted(source.glob("*/*/*/questions.jsonl"))
    if not files:
        raise SystemExit(f"No */*/*/questions.jsonl files under {source}")

    papers: dict[str, dict] = {}
    skipped: Counter[str] = Counter()
    imported: Counter[tuple[str, str, str]] = Counter()
    with conn:
        for path in files:
            batch = []
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    converted = convert(json.loads(line))
                    if isinstance(converted, str):
                        skipped[converted] += 1
                        continue
                    paper, record = converted
                    papers.setdefault(paper["id"], paper)
                    batch.append(record)
                    imported[(record["exam"], record["stage"], record["subject"])] += 1
            conn.executemany(UPSERT_PAPER, [papers[pid] for pid in {r["paper_id"] for r in batch}])
            conn.executemany(UPSERT_QUESTION, batch)
            print(f"  {path.relative_to(source).as_posix():45s} {len(batch):>7,}")
        conn.execute("""
            UPDATE papers SET
                question_count = (SELECT COUNT(*) FROM questions q WHERE q.paper_id = papers.id),
                max_marks      = (SELECT COALESCE(SUM(marks_pos), 0) FROM questions q WHERE q.paper_id = papers.id)
        """)
        conn.execute("INSERT INTO questions_fts (questions_fts) VALUES ('rebuild')")
    return {"papers": len(papers), "imported": imported, "skipped": skipped}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=config.DATASET_DIR, help="dataset checkout")
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help="SQLite file to build")
    parser.add_argument("--skip-ml", action="store_true", help="skip the RAG index and topic model")
    args = parser.parse_args(argv)

    ensure_dataset(args.source)
    started = time.perf_counter()
    conn = db.connect(args.db)
    db.init(conn)
    print(f"Importing {args.source} -> {args.db}")
    report = run_import(conn, args.source)
    conn.execute("PRAGMA optimize")
    if not args.skip_ml:
        from . import rag, topic_model  # numpy/sklearn only needed here
        print("Building the RAG vector index ...")
        rag.build_index(conn)
        print("Training the topic model ...")
        topic_model.train(conn)
    conn.close()

    total = sum(report["imported"].values())
    print(f"\nImported {total:,} questions from {report['papers']:,} papers "
          f"in {time.perf_counter() - started:.1f}s")
    for (exam, stage, subject), n in sorted(report["imported"].items()):
        print(f"  {exam:20s} {stage:6s} {subject:9s} {n:>7,}")
    if report["skipped"]:
        print(f"Skipped {sum(report['skipped'].values())} malformed rows:")
        for reason, n in report["skipped"].most_common():
            print(f"  {n:>5}  {reason}")


if __name__ == "__main__":
    sys.exit(main())
