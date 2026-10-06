"""RAG index, topic model, question generator and fine-tune export on the fixture bank."""
import json

import pytest

from app import ai, config, db, finetune, generator, rag, topic_model


def _conn(client):
    return db.connect(client.app.state.db_path)


@pytest.fixture()
def indexed(client):
    """The fixture bank with a real (tiny) vector index built by the cached embedding model."""
    try:
        rag.build_index(_conn(client), log=lambda *_: None)
    except OSError as e:  # model not downloaded and no network
        pytest.skip(f"embedding model unavailable: {e}")
    return client


def test_index_covers_every_real_question(indexed):
    index = rag.get_index()
    assert index.meta["count"] == 21 and index.vecs.shape == (21, index.meta["dim"])
    assert rag.status()["ready"] is True


def test_hybrid_search_fuses_keyword_and_meaning(indexed):
    res = indexed.get("/api/search", params={"q": "cgl-MATH-2"}).json()
    top = res["results"][0]
    assert top["question"] == "Question cgl-MATH-2?"
    assert set(top["via"]) == {"meaning", "keyword"}
    assert res["dense_index"] is True
    filtered = indexed.get("/api/search", params={"q": "Question", "exam": "SSC-MTS"}).json()["results"]
    assert filtered and {r["exam"] for r in filtered} == {"SSC-MTS"}


def test_similar_and_ranked_practice_modes(indexed):
    qid = indexed.get("/api/search", params={"q": "cgl-MATH-2"}).json()["results"][0]["id"]
    similar = indexed.get(f"/api/questions/{qid}/similar", params={"k": 5}).json()
    assert len(similar) == 5 and qid not in {s["id"] for s in similar}
    page = indexed.get("/api/questions", params={"similar": qid, "limit": 3}).json()
    assert page["next_after"] == 3 and len(page["items"]) == 3
    ordered = indexed.get("/api/questions", params={"ids": f"{similar[2]['id']},{similar[0]['id']}"}).json()
    assert [i["id"] for i in ordered["items"]] == [similar[2]["id"], similar[0]["id"]]


def test_topic_model_trains_predicts_and_audits(client):
    metrics = topic_model.train(_conn(client), log=lambda *_: None)
    assert metrics["train_size"] + metrics["test_size"] == 21
    assert (config.MODELS_DIR / "topic_model.joblib").is_file()
    guess = client.post("/api/lab/classify", json={"text": "Question cgl-ENG-1?"}).json()
    assert len(guess["predictions"]) >= 1 and 0 < guess["predictions"][0]["probability"] <= 1
    assert isinstance(topic_model.audit(_conn(client)), list)
    assert client.get("/api/lab").json()["topic_model"]["ready"] is True


def test_generator_keeps_only_verified_questions_and_isolates_them(client, monkeypatch):
    # Make every fixture question text-only and richer so the chapter has style examples.
    conn = _conn(client)
    good = {"question": "A shop sells a pen at 20% profit. If the cost is 50, what is the price?",
            "options": ["55", "60", "65", "70"], "answer": 2, "solution": "50 x 1.2 = 60, option 2."}
    disputed = {"question": "A trader marks goods 25% above cost and gives 10% off. Find the profit percent.",
                "options": ["10%", "12.5%", "15%", "20%"], "answer": 1, "solution": "1.25 x 0.9 = 1.125."}
    malformed = {"question": "Broken question with three options only?", "options": ["a", "b", "c"],
                 "answer": 1, "solution": "n/a"}
    calls = []

    async def fake_generate_json(system, prompt, schema, temperature=0.7):
        calls.append(prompt)
        if len(calls) == 1:
            return [good, disputed, malformed]
        return [{"number": 1, "answer": 2}, {"number": 2, "answer": 2}]  # solver disagrees on #2

    monkeypatch.setattr(ai, "generate_json", fake_generate_json)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-key")
    report = client.post("/api/ai/generate", json={"exam": "SSC-CGL", "stage": "pre", "subject": "MATH",
                                                    "chapter": "profit-and-loss", "count": 3}).json()
    assert report["verified"] == 1 and len(report["ids"]) == 1
    reasons = " | ".join(d["reason"] for d in report["dropped"])
    assert "four non-empty options" in reasons and "independent solver" in reasons
    assert "Real previous-year questions" in calls[0]  # the generation prompt carried retrieved PYQs

    new_id = report["ids"][0]
    origin, answer, paper = conn.execute("SELECT origin, answer, paper_id FROM questions WHERE id = ?", (new_id,)).fetchone()
    assert (origin, answer, paper) == ("ai", 1, "ai-SSC-CGL-pre")
    # AI questions are practisable on request but never mixed into PYQ views or mocks.
    assert client.get("/api/questions", params={"origin": "ai"}).json()["total"] == 1
    assert new_id not in {i["id"] for i in client.get("/api/questions", params={"limit": 100}).json()["items"]}
    assert "ai-SSC-CGL-pre" not in {p["id"] for p in client.get("/api/papers", params={"exam": "SSC-CGL"}).json()}
    assert client.get("/api/meta").json()["ai_questions"] == 1
    mock = client.get(f"/api/mocks/{client.post('/api/mocks', json={'pattern_id': 'cgl-pre'}).json()['id']}").json()
    assert str(new_id) not in mock["questions"]
    # The search index sees the new text too (keyword side).
    assert client.get("/api/questions", params={"origin": "ai", "search": "pen profit"}).json()["total"] == 1


def test_generated_text_is_stripped_of_latex():
    raw = r"$$\text{Speed} = \frac{600}{30} \times \frac{18}{5} = 72\text{ km/h}$$, x^2 and \frac{\sqrt{3}}{2}"
    assert generator.delatex(raw) == "Speed = (600)/(30) × (18)/(5) = 72 km/h, x² and (√(3))/(2)"


def test_finetune_export_writes_both_formats(client, tmp_path):
    conn = _conn(client)
    with conn:  # give the fixture questions solutions long enough to qualify
        conn.execute("UPDATE questions SET solution = 'Step 1: read the question carefully. Step 2: compute the "
                     "value. Step 3: compare with every option and pick option 2.'")
    stats = finetune.export(conn, tmp_path / "ft", max_examples=100)
    assert stats["examples"] == 21 and stats["train"] + stats["val"] == 21
    first = json.loads((tmp_path / "ft" / "vertex_gemini" / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert [c["role"] for c in first["contents"]] == ["user", "model"]
    assert first["contents"][1]["parts"][0]["text"].startswith("Answer: option 2")
    chat = json.loads((tmp_path / "ft" / "chat" / "train.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert [m["role"] for m in chat["messages"]] == ["system", "user", "assistant"]
