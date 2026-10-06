"""Self-learning learner model: knowledge tracing that updates on every answer.

The model is a 3-parameter-logistic IRT model fitted online, Elo-style:

    P(correct) = c + (1 - c) * sigmoid(θ_global + θ_subject + θ_chapter - β_exam - δ_question)

c = 0.25, because a blind guess among four options is right a quarter of the time.
θ terms are the learner's ability (overall, per subject, per chapter), β is how hard
an exam stage's papers are, and δ is how hard one specific question is for this learner.
After each answer every term takes a gradient step on the log-likelihood, with a step
size that shrinks as that term collects evidence (like Glicko's rating deviation) but
never to zero, so the model keeps tracking a learner who is improving.

How it improves itself:
  * every prediction is stored on the attempt *before* the outcome is known, so the
    model's own calibration (Brier score, log-loss vs. a no-skill baseline) is measured
    on genuinely unseen data, continuously;
  * `retrain()` replays the whole answer history under a grid of step sizes, keeps the
    setting with the best prequential log-loss and rebuilds every parameter from it.
    It runs automatically after every 100 new answers.

Missed questions also enter a spaced-repetition queue (SM-2 style growing intervals).
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

from .catalog import EXAMS, SUBJECTS, chapter_label, stage_name

GUESS = 0.25
RETRAIN_EVERY = 100
MIN_HISTORY = 30


@dataclass(frozen=True)
class HParams:
    k_global: float = 0.04
    k_subject: float = 0.08
    k_chapter: float = 0.30
    k_exam: float = 0.06
    k_question: float = 0.60
    half_life: float = 15.0   # updates after which a term's step size has halved
    floor: float = 0.2        # step never drops below this share of its start value
    shrink: float = 0.001     # pull towards the prior (0) on every update

    def scaled(self, m: float, half_life: float) -> "HParams":
        return replace(self, k_global=self.k_global * m, k_subject=self.k_subject * m,
                       k_chapter=self.k_chapter * m, k_exam=self.k_exam * m,
                       k_question=self.k_question * m, half_life=half_life)


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def _terms(q: dict) -> list[tuple[str, str, int]]:
    """(parameter key, step-size field, sign) for every term that explains a question."""
    terms = [("g", "k_global", 1), (f"sub:{q['subject']}", "k_subject", 1),
             (f"ch:{q['subject']}/{q['chapter']}", "k_chapter", 1),
             (f"exam:{q['exam']}/{q['stage']}", "k_exam", -1)]
    if q.get("id") is not None:
        terms.append((f"q:{q['id']}", "k_question", -1))
    return terms


class Learner:
    def __init__(self, params: dict[str, list] | None = None, hp: HParams = HParams()):
        self.params = params if params is not None else {}   # key -> [value, n]
        self.hp = hp
        self.dirty: set[str] = set()

    def value(self, key: str) -> float:
        return self.params.get(key, (0.0, 0))[0]

    def count(self, key: str) -> int:
        return self.params.get(key, (0.0, 0))[1]

    def predict(self, q: dict) -> float:
        z = sum(sign * self.value(key) for key, _, sign in _terms(q))
        return GUESS + (1 - GUESS) * _sigmoid(z)

    def update(self, q: dict, correct: bool) -> float:
        """Learn from one answer; returns the prediction made *before* seeing it."""
        terms = _terms(q)
        s = _sigmoid(sum(sign * self.value(key) for key, _, sign in terms))
        p = GUESS + (1 - GUESS) * s
        # d log-likelihood / d logit for the guessing-floor link.
        grad = ((1.0 if correct else 0.0) - p) / (p * (1 - p)) * (1 - GUESS) * s * (1 - s)
        for key, field, sign in terms:
            value, n = self.params.get(key, (0.0, 0))
            k0 = getattr(self.hp, field)
            k = max(k0 * self.hp.floor, k0 / (1 + n / self.hp.half_life))
            self.params[key] = [value * (1 - self.hp.shrink) + sign * k * grad, n + 1]
            self.dirty.add(key)
        return p


# --------------------------------------------------------------------------- persistence

def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))


def hparams(conn: sqlite3.Connection) -> HParams:
    stored = get_setting(conn, "learner.hparams")
    return HParams(**stored) if stored else HParams()


def load(conn: sqlite3.Connection) -> Learner:
    params = {r[0]: [r[1], r[2]] for r in conn.execute("SELECT key, value, n FROM learner_params")}
    return Learner(params, hparams(conn))


def save(conn: sqlite3.Connection, learner: Learner) -> None:
    conn.executemany(
        "INSERT INTO learner_params (key, value, n) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, n = excluded.n",
        [(k, *learner.params[k]) for k in learner.dirty])
    learner.dirty.clear()


QUESTION_FIELDS = "q.id, q.subject, q.chapter, q.exam, q.stage"


def question_info(conn: sqlite3.Connection, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = conn.execute(f"SELECT {QUESTION_FIELDS} FROM questions q WHERE q.id IN ({','.join('?' * len(ids))})", ids)
    return {r[0]: {"id": r[0], "subject": r[1], "chapter": r[2], "exam": r[3], "stage": r[4]} for r in rows}


# --------------------------------------------------------------------------- learning from answers

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def schedule_review(conn: sqlite3.Connection, qid: int, correct: bool, now: datetime) -> None:
    """SM-2 style: a miss comes back tomorrow; each later success stretches the gap."""
    row = conn.execute("SELECT interval_days, ease, reps, lapses FROM reviews WHERE question_id = ?", (qid,)).fetchone()
    if not correct:
        ease = max(1.3, (row[1] if row else 2.5) - 0.2)
        conn.execute(
            "INSERT INTO reviews (question_id, due, interval_days, ease, reps, lapses) VALUES (?, ?, 1, ?, 0, 1) "
            "ON CONFLICT(question_id) DO UPDATE SET due = excluded.due, interval_days = 1, ease = excluded.ease, "
            "reps = 0, lapses = reviews.lapses + 1", (qid, _fmt(now + timedelta(days=1)), ease))
    elif row:
        reps = row[2] + 1
        interval = 3.0 if reps == 1 else round(row[0] * row[1], 1)
        if interval > 60:  # recalled across two months of gaps: treat as learned
            conn.execute("DELETE FROM reviews WHERE question_id = ?", (qid,))
        else:
            conn.execute("UPDATE reviews SET due = ?, interval_days = ?, ease = ?, reps = ? WHERE question_id = ?",
                         (_fmt(now + timedelta(days=interval)), interval, min(3.0, row[1] + 0.05), reps, qid))


def observe(conn: sqlite3.Connection, answers: list[tuple[int, bool]]) -> list[float]:
    """Update the model with answers given in order; returns the pre-answer predictions.

    The caller stores each prediction on its attempt row, which is what lets the model
    be graded on forecasts it made before knowing the outcome.
    """
    info = question_info(conn, [qid for qid, _ in answers])
    learner = load(conn)
    now = _utcnow()
    predictions = []
    for qid, correct in answers:
        predictions.append(learner.update(info[qid], correct))
        schedule_review(conn, qid, correct, now)
    save(conn, learner)
    return predictions


def maybe_retrain(conn: sqlite3.Connection) -> dict | None:
    total = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    if total - get_setting(conn, "learner.trained_on", 0) >= RETRAIN_EVERY:
        return retrain(conn)
    return None


# --------------------------------------------------------------------------- self-tuning

def _history(conn: sqlite3.Connection) -> list[tuple[dict, bool]]:
    rows = conn.execute(f"""SELECT {QUESTION_FIELDS}, a.is_correct FROM attempts a
                            JOIN questions q ON q.id = a.question_id ORDER BY a.id""").fetchall()
    return [({"id": r[0], "subject": r[1], "chapter": r[2], "exam": r[3], "stage": r[4]}, bool(r[5])) for r in rows]


def _replay(history: list[tuple[dict, bool]], hp: HParams, warmup: int) -> tuple[Learner, float, float]:
    """Prequential evaluation: predict each answer from everything before it, then learn it."""
    learner = Learner({}, hp)
    loss = brier = 0.0
    for i, (q, y) in enumerate(history):
        p = min(max(learner.update(q, y), 1e-6), 1 - 1e-6)
        if i >= warmup:
            loss -= math.log(p if y else 1 - p)
            brier += (p - y) ** 2
    scored = max(1, len(history) - warmup)
    return learner, loss / scored, brier / scored


def retrain(conn: sqlite3.Connection) -> dict:
    history = _history(conn)
    if len(history) < MIN_HISTORY:
        return {"status": "waiting", "attempts": len(history), "needed": MIN_HISTORY}
    started = time.perf_counter()
    warmup = min(20, len(history) // 3)
    current = hparams(conn)
    candidates = {current} | {HParams().scaled(m, hl) for m in (0.5, 1.0, 2.0) for hl in (8.0, 15.0, 30.0)}
    results = [(hp, *_replay(history, hp, warmup)) for hp in candidates]
    best_hp, learner, best_loss, best_brier = min(results, key=lambda r: r[2])
    _, _, current_loss, _ = next(r for r in results if r[0] == current)

    scored = [y for _, y in history[warmup:]]
    rate = min(max(sum(scored) / len(scored), 1e-3), 1 - 1e-3)
    baseline_loss = -(rate * math.log(rate) + (1 - rate) * math.log(1 - rate))
    baseline_brier = rate * (1 - rate)

    with conn:
        conn.execute("DELETE FROM learner_params")
        conn.executemany("INSERT INTO learner_params (key, value, n) VALUES (?, ?, ?)",
                         [(k, v, n) for k, (v, n) in learner.params.items()])
        report = {
            "status": "trained", "trained_at": _fmt(_utcnow()), "attempts": len(history),
            "candidates": len(candidates), "seconds": round(time.perf_counter() - started, 2),
            "logloss": round(best_loss, 4), "brier": round(best_brier, 4),
            "previous_logloss": round(current_loss, 4),
            "baseline_logloss": round(baseline_loss, 4), "baseline_brier": round(baseline_brier, 4),
            "skill_vs_baseline": round(1 - best_loss / baseline_loss, 4) if baseline_loss else 0.0,
            "hparams": asdict(best_hp),
        }
        set_setting(conn, "learner.hparams", asdict(best_hp))
        set_setting(conn, "learner.trained_on", len(history))
        set_setting(conn, "learner.report", report)
    return report


def calibration(conn: sqlite3.Connection) -> dict:
    """How well the stored, pre-answer predictions matched what actually happened."""
    rows = conn.execute("SELECT predicted, is_correct FROM attempts WHERE predicted IS NOT NULL ORDER BY id").fetchall()
    if not rows:
        return {"n": 0, "buckets": []}
    edges = [0.25, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]
    buckets = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [(p, y) for p, y in rows if lo <= p < hi]
        if sel:
            buckets.append({"lo": lo, "hi": min(hi, 1.0), "n": len(sel),
                            "predicted": round(sum(p for p, _ in sel) / len(sel), 4),
                            "actual": round(sum(y for _, y in sel) / len(sel), 4)})
    recent = rows[-200:]
    clip = lambda p: min(max(p, 1e-6), 1 - 1e-6)  # noqa: E731
    return {
        "n": len(rows), "buckets": buckets,
        "brier": round(sum((p - y) ** 2 for p, y in recent) / len(recent), 4),
        "logloss": round(-sum(math.log(clip(p) if y else 1 - clip(p)) for p, y in recent) / len(recent), 4),
        "mean_predicted": round(sum(p for p, _ in recent) / len(recent), 4),
        "mean_actual": round(sum(y for _, y in recent) / len(recent), 4),
        "window": len(recent),
    }


# --------------------------------------------------------------------------- what the learner knows

_syllabus_cache: dict[tuple, list[dict]] = {}


def syllabus(conn: sqlite3.Connection, exam: str, stage: str) -> list[dict]:
    """Each chapter's share of an exam stage's previous-year questions (its weightage)."""
    key = (exam, stage, conn.execute("SELECT MAX(id) FROM questions").fetchone()[0])
    if key not in _syllabus_cache:
        rows = conn.execute("SELECT subject, chapter, COUNT(*) FROM questions WHERE origin = 'pyq' AND exam = ? "
                            "AND stage = ? AND chapter IS NOT NULL GROUP BY 1, 2", (exam, stage)).fetchall()
        per_subject: dict[str, int] = {}
        for s, _, n in rows:
            per_subject[s] = per_subject.get(s, 0) + n
        _syllabus_cache[key] = [{"subject": s, "chapter": c, "n": n, "share": n / per_subject[s]} for s, c, n in rows]
    return _syllabus_cache[key]


def mastery(conn: sqlite3.Connection, exam: str, stage: str) -> list[dict]:
    learner = load(conn)
    done = {(r[0], r[1]): (r[2], r[3]) for r in conn.execute(
        "SELECT q.subject, q.chapter, COUNT(*), SUM(a.is_correct) FROM attempts a "
        "JOIN questions q ON q.id = a.question_id GROUP BY 1, 2")}
    out = []
    for ch in syllabus(conn, exam, stage):
        p = learner.predict({"subject": ch["subject"], "chapter": ch["chapter"], "exam": exam, "stage": stage})
        n, correct = done.get((ch["subject"], ch["chapter"]), (0, 0))
        status = "new" if n == 0 else "strong" if p >= 0.8 else "ok" if p >= 0.6 else "weak"
        # Spend time where the exam asks a lot and the learner is unsure.
        priority = ch["share"] ** 0.6 * (1 - p) + (0.15 if n < 3 else 0.0)
        out.append({**ch, "label": chapter_label(ch["chapter"]), "subject_name": SUBJECTS[ch["subject"]],
                    "predicted": round(p, 4), "attempts": n, "correct": correct or 0,
                    "status": status, "confident": n >= 5, "priority": round(priority, 4)})
    return sorted(out, key=lambda c: -c["priority"])


def predicted_score(conn: sqlite3.Connection, pattern: dict) -> dict:
    """Expected marks on the pattern if every question were attempted today."""
    by_subject: dict[str, list[dict]] = {}
    for ch in mastery(conn, pattern["exam"], pattern["stage"]):
        by_subject.setdefault(ch["subject"], []).append(ch)
    sections, expected, maximum = [], 0.0, 0.0
    for part in pattern["parts"]:
        for sec in part["sections"]:
            chapters = by_subject.get(sec["subject"], [])
            total_share = sum(c["share"] for c in chapters) or 1
            p = sum(c["share"] * c["predicted"] for c in chapters) / total_share if chapters else GUESS
            exp = sec["count"] * (p * sec["correct"] - (1 - p) * sec["wrong"])
            sections.append({"name": sec["name"], "subject": sec["subject"], "count": sec["count"],
                             "p_correct": round(p, 4), "expected": round(exp, 1),
                             "max": sec["count"] * sec["correct"],
                             # Expected marks of a blind guess: positive means guessing pays.
                             "guess_value": round(GUESS * sec["correct"] - (1 - GUESS) * sec["wrong"], 3)})
            expected += exp
            maximum += sec["count"] * sec["correct"]
    return {"pattern_id": pattern["id"], "name": pattern["name"], "expected": round(expected, 1),
            "max": maximum, "sections": sections}


def target(conn: sqlite3.Connection) -> tuple[str, str]:
    t = get_setting(conn, "target", {"exam": "SSC-CGL", "stage": "pre"})
    return t["exam"], t["stage"]


def overview(conn: sqlite3.Connection, pattern: dict) -> dict:
    exam, stage = pattern["exam"], pattern["stage"]
    now = _fmt(_utcnow())
    learner = load(conn)
    return {
        "target": {"exam": exam, "stage": stage, "exam_name": EXAMS[exam], "stage_name": stage_name(exam, stage),
                   "pattern_id": pattern["id"]},
        "attempts": conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
        "reviews_due": conn.execute("SELECT COUNT(*) FROM reviews WHERE due <= ?", (now,)).fetchone()[0],
        "reviews_total": conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
        "ability": {"global": round(learner.value("g"), 3),
                    "subjects": {s: round(learner.value(f"sub:{s}"), 3) for s in SUBJECTS}},
        "mastery": mastery(conn, exam, stage),
        "predicted_score": predicted_score(conn, pattern),
        "calibration": calibration(conn),
        "training": get_setting(conn, "learner.report"),
        "hparams": asdict(learner.hp),
    }


def retrain_due(conn: sqlite3.Connection) -> bool:
    total = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    return total >= MIN_HISTORY and total - get_setting(conn, "learner.trained_on", 0) >= RETRAIN_EVERY
