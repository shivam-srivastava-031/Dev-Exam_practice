"""Topic classifier trained on the labelled PYQs: the model we fine-tune ourselves.

Gemini tuning is not available to the configured key and there is no GPU here, so
the supervised model trained on this dataset is a linear classifier over TF-IDF
features: it trains on ~130k questions in under a minute on CPU. It predicts
subject + chapter for any question text, which powers:
  * "paste a question" in Search (detect the topic, then retrieve similar PYQs),
  * a sanity check on AI-generated questions,
  * an audit of dataset rows whose label the model confidently disagrees with.

Training uses scikit-learn, but the saved model is plain NumPy (vocabulary, idf,
float16 weights) and inference re-implements the TfidfVectorizer + SGD
log-loss pipeline exactly, so the deployed server needs neither scikit-learn nor
SciPy (~160 MB lighter).

    python -m app.topic_model train          # fit, evaluate on a 10% hold-out, save
    python -m app.topic_model audit          # list probable mislabels
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
from collections import Counter

import numpy as np

from . import config, db
from .catalog import SUBJECTS, chapter_label


def model_path():
    return config.MODELS_DIR / "topic_model.npz"


_MARKUP = re.compile(r"\[IMAGE:[^\]]*\]|<[^>]+>|\*\*|__")
_TOKEN = re.compile(r"(?u)\b\w\w+\b")  # scikit-learn's default token_pattern


def model_text(question: str, options: list[str]) -> str:
    return _MARKUP.sub(" ", question + " " + " ".join(options))


def _load_rows(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT id, subject, chapter, question, options FROM questions "
        "WHERE origin = 'pyq' AND chapter IS NOT NULL").fetchall()


def train(conn: sqlite3.Connection, log=print) -> dict:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import SGDClassifier
    from sklearn.metrics import accuracy_score, f1_score

    rows = _load_rows(conn)
    texts = [model_text(r[3], json.loads(r[4])) for r in rows]
    labels = np.array([f"{r[1]}/{r[2]}" for r in rows])
    # Deterministic hold-out: every 10th question id is never trained on.
    test = np.array([r[0] % 10 == 0 for r in rows])
    started = time.perf_counter()
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                                 max_features=150_000, dtype=np.float32, strip_accents="unicode")
    x_train = vectorizer.fit_transform([t for t, te in zip(texts, test) if not te])
    clf = SGDClassifier(loss="log_loss", alpha=1e-6, max_iter=20, tol=None, n_jobs=-1, random_state=0)
    clf.fit(x_train, labels[~test])

    model = {
        "vocab": {term: int(i) for term, i in vectorizer.vocabulary_.items()},
        "idf": vectorizer.idf_.astype(np.float32),
        "coef": clf.coef_.astype(np.float16),  # 107 x 150k: float16 keeps it ~30 MB
        "intercept": clf.intercept_.astype(np.float32),
        "classes": [str(c) for c in clf.classes_],
    }
    # Evaluate the exported NumPy model itself, so the metrics describe what ships.
    test_texts = [t for t, te in zip(texts, test) if te]
    proba = _predict_proba(model, test_texts)
    classes = np.array(model["classes"])
    top3 = classes[np.argsort(-proba, axis=1)[:, :3]]
    pred = top3[:, 0]
    truth = labels[test]
    subj = lambda arr: np.array([a.split("/")[0] for a in arr])  # noqa: E731
    per_subject = {
        s: round(float((pred[subj(truth) == s] == truth[subj(truth) == s]).mean()), 4)
        for s in SUBJECTS if (subj(truth) == s).any()
    }
    confusions = Counter((t, p) for t, p in zip(truth, pred) if t != p).most_common(8)
    model["metrics"] = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.perf_counter() - started, 1),
        "train_size": int((~test).sum()), "test_size": int(test.sum()), "classes": len(classes),
        "chapter_accuracy": round(float(accuracy_score(truth, pred)), 4),
        "chapter_top3_accuracy": round(float(np.mean([t in row for t, row in zip(truth, top3)])), 4),
        "chapter_macro_f1": round(float(f1_score(truth, pred, average="macro")), 4),
        "subject_accuracy": round(float(accuracy_score(subj(truth), subj(pred))), 4),
        "chapter_accuracy_by_subject": per_subject,
        "top_confusions": [{"true": chapter_label(t.split("/")[1]), "predicted": chapter_label(p.split("/")[1]), "count": n}
                           for (t, p), n in confusions],
    }
    save(model)
    m = model["metrics"]
    log(f"Topic model: subject accuracy {m['subject_accuracy']:.1%}, chapter accuracy {m['chapter_accuracy']:.1%} "
        f"(top-3 {m['chapter_top3_accuracy']:.1%}) on {m['test_size']:,} held-out questions; trained in {m['seconds']}s")
    return m


def save(model: dict) -> None:
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    vocab = sorted(model["vocab"], key=model["vocab"].get)  # position == feature index
    np.savez_compressed(
        model_path(), idf=model["idf"], coef=model["coef"], intercept=model["intercept"],
        vocab=np.frombuffer("\n".join(vocab).encode(), dtype=np.uint8),
        classes=np.frombuffer(json.dumps(model["classes"]).encode(), dtype=np.uint8),
        metrics=np.frombuffer(json.dumps(model["metrics"]).encode(), dtype=np.uint8))
    _cache.clear()


_cache: dict[str, dict] = {}
_lock = threading.Lock()


def load() -> dict | None:
    with _lock:
        path = model_path()
        if not path.is_file():
            return None
        key = f"{path}:{path.stat().st_mtime_ns}"
        if key not in _cache:
            _cache.clear()
            with np.load(path) as z:
                vocab = z["vocab"].tobytes().decode().split("\n")
                _cache[key] = {
                    "vocab": {term: i for i, term in enumerate(vocab)},
                    "idf": z["idf"], "coef": z["coef"], "intercept": z["intercept"],
                    "classes": json.loads(z["classes"].tobytes()), "metrics": json.loads(z["metrics"].tobytes()),
                }
        return _cache[key]


def _strip_accents(text: str) -> str:
    """scikit-learn's strip_accents='unicode'."""
    if text.isascii():
        return text
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _features(model: dict, text: str) -> tuple[np.ndarray, np.ndarray]:
    """TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, norm='l2') for one text."""
    tokens = _TOKEN.findall(_strip_accents(text.lower()))
    grams = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
    counts = Counter(model["vocab"][g] for g in grams if g in model["vocab"])
    if not counts:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
    idx = np.fromiter(counts, dtype=np.int64, count=len(counts))
    tf = np.array([1 + math.log(counts[i]) for i in idx], dtype=np.float32)
    vals = tf * model["idf"][idx]
    return idx, vals / np.linalg.norm(vals)


def _predict_proba(model: dict, texts: list[str]) -> np.ndarray:
    """SGDClassifier(loss='log_loss') one-vs-rest probabilities, as predict_proba gives them."""
    out = np.empty((len(texts), len(model["classes"])), dtype=np.float32)
    for row, text in enumerate(texts):
        idx, vals = _features(model, text)
        decision = model["coef"][:, idx].astype(np.float32) @ vals + model["intercept"]
        prob = 1 / (1 + np.exp(-decision))
        out[row] = prob / prob.sum()
    return out


def predict(texts: list[str], top: int = 3) -> list[list[dict]] | None:
    """Top-`top` (subject, chapter, probability) guesses per text, best first."""
    model = load()
    if model is None:
        return None
    classes = model["classes"]
    out = []
    for row in _predict_proba(model, texts):
        best = np.argsort(-row)[:top]
        out.append([{"subject": classes[i].split("/")[0], "chapter": classes[i].split("/", 1)[1],
                     "label": chapter_label(classes[i].split("/", 1)[1]), "probability": round(float(row[i]), 4)}
                    for i in best])
    return out


def audit(conn: sqlite3.Connection, threshold: float = 0.9, limit: int = 200) -> list[dict]:
    """Questions whose stored subject the model confidently contradicts."""
    rows = _load_rows(conn)
    preds = predict([model_text(r[3], json.loads(r[4])) for r in rows], top=1) or []
    flagged = [
        {"id": r[0], "labelled": f"{r[1]}/{r[2]}", "predicted": f"{p[0]['subject']}/{p[0]['chapter']}",
         "probability": p[0]["probability"], "question": r[3][:120]}
        for r, p in zip(rows, preds) if p[0]["subject"] != r[1] and p[0]["probability"] >= threshold
    ]
    return sorted(flagged, key=lambda f: -f["probability"])[:limit]


def status() -> dict:
    model = load()
    return {"ready": model is not None, **(model["metrics"] if model else {})}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["train", "audit"])
    args = parser.parse_args(argv)
    conn = db.connect(config.DB_PATH)
    db.init(conn)
    if args.command == "train":
        print(json.dumps(train(conn), indent=2))
    else:
        flagged = audit(conn)
        print(f"{len(flagged)} questions whose subject label the model contradicts with >=90% confidence:")
        for f in flagged[:40]:
            print(f"  #{f['id']:<7} {f['labelled']:38s} -> {f['predicted']:38s} {f['probability']:.0%}  {f['question'][:60]!r}")


if __name__ == "__main__":
    main()
