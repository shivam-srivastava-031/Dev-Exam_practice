import random
from datetime import datetime, timedelta, timezone

from app import db, engine, learner
from app.learner import GUESS, HParams, Learner

MATH_Q = {"id": 1, "subject": "MATH", "chapter": "profit-and-loss", "exam": "SSC-CGL", "stage": "pre"}


def test_prior_is_half_way_above_the_guessing_floor():
    assert Learner().predict(MATH_Q) == GUESS + (1 - GUESS) * 0.5


def test_updates_move_the_prediction_the_right_way_and_shrink():
    up, down = Learner(), Learner()
    first_step = up.update(MATH_Q, True)
    assert first_step == 0.625  # update() returns the prediction made before learning
    assert up.predict(MATH_Q) > 0.625
    down.update(MATH_Q, False)
    assert down.predict(MATH_Q) < 0.625
    # A different chapter of the same subject moves less than the chapter itself.
    other = {**MATH_Q, "id": 2, "chapter": "geometry"}
    assert 0.625 < up.predict(other) < up.predict({**MATH_Q, "id": 1})
    # Step sizes decay with evidence: the 50th update moves a term less than the 1st.
    lr = Learner(hp=HParams())
    deltas = []
    for _ in range(50):
        before = lr.value("ch:MATH/profit-and-loss")
        lr.update({**MATH_Q, "id": None}, True)
        deltas.append(lr.value("ch:MATH/profit-and-loss") - before)
    assert deltas[-1] < deltas[0]


def _conn(client):
    return db.connect(client.app.state.db_path)


def test_wrong_answers_enter_spaced_repetition(client):
    qid = client.get("/api/questions", params={"subject": "MATH", "exam": "SSC-CGL"}).json()["items"][0]["id"]
    client.post("/api/practice/answer", json={"question_id": qid, "chosen": 0})  # wrong
    conn = _conn(client)
    due, interval, lapses = conn.execute("SELECT due, interval_days, lapses FROM reviews WHERE question_id = ?", (qid,)).fetchone()
    due_at = datetime.strptime(due, "%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(hours=23) < due_at - now < timedelta(hours=25)
    assert (interval, lapses) == (1, 1)

    client.post("/api/practice/answer", json={"question_id": qid, "chosen": 1})  # right on review
    assert conn.execute("SELECT interval_days FROM reviews WHERE question_id = ?", (qid,)).fetchone()[0] == 3
    client.post("/api/practice/answer", json={"question_id": qid, "chosen": 1})
    assert conn.execute("SELECT interval_days FROM reviews WHERE question_id = ?", (qid,)).fetchone()[0] > 3
    # Every attempt stored the forecast made before the answer was known.
    assert conn.execute("SELECT COUNT(*) FROM attempts WHERE predicted IS NULL").fetchone()[0] == 0


def test_retrain_learns_a_learner_strong_in_maths_and_weak_in_english(client):
    conn = _conn(client)
    ids = {s: [r[0] for r in conn.execute("SELECT id FROM questions WHERE subject = ?", (s,))] for s in ("MATH", "ENG")}
    rng = random.Random(1)
    rows = []
    for _ in range(150):
        subject = rng.choice(["MATH", "ENG"])
        correct = rng.random() < (0.9 if subject == "MATH" else 0.3)
        rows.append((rng.choice(ids[subject]), 0, int(correct), "practice"))
    with conn:
        conn.executemany("INSERT INTO attempts (question_id, chosen, is_correct, mode) VALUES (?, ?, ?, ?)", rows)

    report = client.post("/api/learner/retrain").json()
    assert report["status"] == "trained" and report["attempts"] == 150
    assert report["candidates"] >= 9
    assert report["logloss"] < report["baseline_logloss"]  # beats always predicting the average
    model = learner.load(conn)
    maths = model.predict({**MATH_Q, "id": None})
    english = model.predict({"subject": "ENG", "chapter": "profit-and-loss", "exam": "SSC-CGL", "stage": "pre"})
    assert maths > 0.75 and english < 0.5


def test_overview_reports_mastery_score_and_calibration(client):
    qids = [i["id"] for i in client.get("/api/questions", params={"exam": "SSC-CGL"}).json()["items"]]
    for qid in qids[:6]:
        client.post("/api/practice/answer", json={"question_id": qid, "chosen": 1})
    ov = client.get("/api/learner").json()
    assert ov["target"] == {"exam": "SSC-CGL", "stage": "pre", "exam_name": "SSC CGL", "stage_name": "Tier-I",
                            "pattern_id": "cgl-pre"}
    assert ov["attempts"] == 6
    assert {c["subject"] for c in ov["mastery"]} == {"REAS", "GK", "MATH", "ENG"}
    assert ov["predicted_score"]["max"] == 200
    assert ov["calibration"]["n"] == 6
    # With +2/-0.5 marking a blind guess still earns 0.25*2 - 0.75*0.5 = +0.125 on average.
    assert ov["predicted_score"]["sections"][0]["guess_value"] == 0.125

    switched = client.put("/api/learner/target", json={"exam": "SSC-MTS", "stage": "pre"}).json()
    assert switched["target"]["pattern_id"] == "mts-pre"
    assert client.put("/api/learner/target", json={"exam": "SSC-GD", "stage": "mains"}).status_code == 422


def test_smart_practice_puts_due_reviews_first_and_explains_every_pick(client):
    qid = client.get("/api/questions", params={"subject": "GK", "exam": "SSC-CGL"}).json()["items"][0]["id"]
    client.post("/api/practice/answer", json={"question_id": qid, "chosen": 0})
    conn = _conn(client)
    with conn:
        conn.execute("UPDATE reviews SET due = '2000-01-01 00:00:00'")
    batch = client.get("/api/smart", params={"n": 6}).json()["items"]
    assert batch[0]["id"] == qid and batch[0]["kind"] == "review"
    assert all(item["reason"] for item in batch)
    assert len({i["id"] for i in batch}) == len(batch) <= 6
    # Picks other than the review are questions the learner has not attempted.
    assert all(i["last_correct"] is None for i in batch[1:])
    again = client.get("/api/smart", params={"n": 6, "exclude": ",".join(str(i["id"]) for i in batch)}).json()["items"]
    assert not {i["id"] for i in again} & {i["id"] for i in batch}


def test_personalised_mock_keeps_the_real_pattern(client):
    mock_id = client.post("/api/mocks", json={"pattern_id": "cgl-pre", "adaptive": True}).json()["id"]
    mock = client.get(f"/api/mocks/{mock_id}").json()
    assert mock["kind"] == "adaptive"
    sections = mock["parts"][0]["sections"]
    assert [s["subject"] for s in sections] == ["REAS", "GK", "MATH", "ENG"]
    assert all(len(s["question_ids"]) == 3 for s in sections)  # the fixture pool holds 3 per subject
    assert all(mock["questions"][str(q)]["exam"] == "SSC-CGL" for s in sections for q in s["question_ids"])


def test_engine_picks_only_unattempted_real_questions(client):
    conn = _conn(client)
    picks = engine.smart_batch(conn, 8)
    origins = {r[0] for r in conn.execute(
        f"SELECT origin FROM questions WHERE id IN ({','.join(str(p['id']) for p in picks)})")}
    assert origins == {"pyq"}
