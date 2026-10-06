def _layout_summary(mock: dict) -> list[tuple]:
    return [(part["name"], part["minutes"], [(s["subject"], len(s["question_ids"]), s["correct"], s["wrong"])
                                             for s in part["sections"]]) for part in mock["parts"]]


def test_paper_mock_follows_real_section_order_and_numbering(client):
    mock_id = client.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
    mock = client.get(f"/api/mocks/{mock_id}").json()
    assert mock["pattern"]["id"] == "cgl-pre"
    assert _layout_summary(mock) == [
        ("Tier-I", 60, [("REAS", 3, 2.0, 0.5), ("GK", 3, 2.0, 0.5), ("MATH", 3, 2.0, 0.5), ("ENG", 3, 2.0, 0.5)])]
    reas_ids = mock["parts"][0]["sections"][0]["question_ids"]
    assert [mock["questions"][str(i)]["question"] for i in reas_ids] == [
        "Question cgl-REAS-1?", "Question cgl-REAS-2?", "Question cgl-REAS-3?"]
    # The answer key must not reach the browser before submission.
    assert "answer" not in mock["questions"][str(reas_ids[0])]
    assert "solution" not in mock["questions"][str(reas_ids[0])]


def test_mts_papers_pick_old_or_new_pattern_by_marks(client):
    new = client.get(f"/api/mocks/{client.post('/api/mocks', json={'paper_id': 'M2'}).json()['id']}").json()
    assert new["pattern"]["id"] == "mts-pre"
    assert _layout_summary(new) == [
        ("Session-I", 45, [("MATH", 1, 3.0, 0.0), ("REAS", 1, 3.0, 0.0)]),
        ("Session-II", 45, [("GK", 1, 3.0, 1.0), ("ENG", 1, 3.0, 1.0)]),
    ]
    old = client.get(f"/api/mocks/{client.post('/api/mocks', json={'paper_id': 'M1'}).json()['id']}").json()
    assert old["pattern"]["id"] == "mts-pre-2022"
    assert [s["subject"] for s in old["parts"][0]["sections"]] == ["REAS", "MATH", "ENG", "GK"]


def test_submit_grades_with_negative_marking_and_records_attempts(client):
    mock_id = client.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
    mock = client.get(f"/api/mocks/{mock_id}").json()
    ids = [q for s in mock["parts"][0]["sections"] for q in s["question_ids"]]
    # Every fixture answer is option 2 (index 1): 5 right, 3 wrong, 1 marked-and-answered right, rest skipped.
    responses = {str(q): {"chosen": 1, "ms": 1000} for q in ids[:5]}
    responses |= {str(q): {"chosen": 0, "ms": 2000} for q in ids[5:8]}
    responses[str(ids[8])] = {"chosen": 1, "marked": True}
    responses[str(ids[9])] = {"chosen": 9}  # out of range counts as unanswered
    out = client.post(f"/api/mocks/{mock_id}/submit", json={"responses": responses, "elapsed_sec": 600}).json()
    assert out["score"] == 6 * 2 - 3 * 0.5
    assert out["max_score"] == 24
    assert client.post(f"/api/mocks/{mock_id}/submit", json={"responses": {}}).status_code == 409

    result = client.get(f"/api/mocks/{mock_id}/result").json()
    r = result["result"]
    assert (r["correct"], r["wrong"], r["skipped"], r["attempted"]) == (6, 3, 3, 9)
    assert result["questions"][str(ids[0])]["answer"] == 1
    assert "Key Points" in result["questions"][str(ids[0])]["solution"]
    stats = client.get("/api/stats").json()
    assert stats["totals"]["attempts"] == 9 and stats["totals"]["correct"] == 6


def test_random_mock_has_no_duplicates_and_respects_pool(client):
    mock_id = client.post("/api/mocks", json={"pattern_id": "cgl-pre"}).json()["id"]
    mock = client.get(f"/api/mocks/{mock_id}").json()
    ids = [q for s in mock["parts"][0]["sections"] for q in s["question_ids"]]
    assert len(ids) == len(set(ids)) == 12  # pool only has 3 per subject
    assert {mock["questions"][str(i)]["exam"] for i in ids} == {"SSC-CGL"}


def test_practice_flow_and_status_filters(client):
    page = client.get("/api/questions", params={"exam": "SSC-CGL", "subject": "MATH", "seed": 7}).json()
    assert page["total"] == 3
    qid = page["items"][0]["id"]
    assert "answer" not in page["items"][0]

    wrong = client.post("/api/practice/answer", json={"question_id": qid, "chosen": 0}).json()
    # With no history the learner model predicts its prior: 0.25 + 0.75 * sigmoid(0).
    assert wrong == {"answer": 1, "solution": wrong["solution"], "is_correct": False, "predicted": 0.625}
    incorrect = client.get("/api/questions", params={"status": "incorrect"}).json()
    assert [i["id"] for i in incorrect["items"]] == [qid]
    assert incorrect["items"][0]["last_correct"] is False

    client.post("/api/practice/answer", json={"question_id": qid, "chosen": 1})
    assert client.get("/api/questions", params={"status": "incorrect"}).json()["total"] == 0
    assert client.get("/api/questions", params={"subject": "MATH", "exam": "SSC-CGL",
                                                "status": "unattempted"}).json()["total"] == 2
    assert client.post("/api/practice/answer", json={"question_id": qid, "chosen": 7}).status_code == 422


def test_random_order_pages_never_overlap(client):
    seen, after = [], None
    while True:
        params = {"seed": 123, "limit": 5, **({"after": after} if after is not None else {})}
        page = client.get("/api/questions", params=params).json()
        seen += [i["id"] for i in page["items"]]
        after = page["next_after"]
        if after is None:
            break
    assert len(seen) == len(set(seen)) == 21


def test_bookmark_toggle(client):
    qid = client.get("/api/questions").json()["items"][0]["id"]
    assert client.post(f"/api/bookmarks/{qid}").json() == {"bookmarked": True}
    assert client.get("/api/questions", params={"status": "bookmarked"}).json()["total"] == 1
    assert client.post(f"/api/bookmarks/{qid}").json() == {"bookmarked": False}


def test_discard_only_unsubmitted_mocks(client):
    mock_id = client.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
    assert client.delete(f"/api/mocks/{mock_id}").json() == {"deleted": True}
    assert client.get(f"/api/mocks/{mock_id}").status_code == 404


def test_ai_endpoints_report_when_disabled(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    qid = client.get("/api/questions").json()["items"][0]["id"]
    res = client.post("/api/ai/explain", json={"question_id": qid, "messages": [{"role": "user", "text": "why?"}]})
    assert res.status_code == 503


def test_word_search_matches_whole_words_and_last_word_prefix(client):
    assert client.get("/api/questions", params={"search": "cgl-MATH-2"}).json()["total"] == 1
    assert client.get("/api/questions", params={"search": "Question cgl REA"}).json()["total"] == 3
    assert client.get("/api/questions", params={"search": "nonexistentword"}).json()["total"] == 0
    assert client.get("/api/questions", params={"search": '"); DROP'}).json()["total"] == 0


def test_compressed_solutions_read_back_as_text(client):
    """The deployed bank stores solutions zlib-compressed; every reader gets plain text."""
    import zlib

    from app import db
    conn = db.connect(client.app.state.db_path)
    with conn:
        conn.execute("UPDATE questions SET solution = ?", (zlib.compress("Use the shortcut: 2 + 2 = 4.".encode()),))
    qid = client.get("/api/questions").json()["items"][0]["id"]
    assert client.post("/api/practice/answer", json={"question_id": qid, "chosen": 1}).json()["solution"] \
        == "Use the shortcut: 2 + 2 = 4."
    mock_id = client.post("/api/mocks", json={"paper_id": "P1"}).json()["id"]
    client.post(f"/api/mocks/{mock_id}/submit", json={"responses": {}})
    review = client.get(f"/api/mocks/{mock_id}/result").json()["questions"]
    assert {q["solution"] for q in review.values()} == {"Use the shortcut: 2 + 2 = 4."}


def test_papers_have_readable_slugs_usable_everywhere(client):
    papers = client.get("/api/papers", params={"exam": "SSC-CGL"}).json()
    assert [p["slug"] for p in papers] == ["ssc-cgl-2023-07-18-shift-1"]
    by_slug = client.get("/api/questions", params={"paper": "ssc-cgl-2023-07-18-shift-1", "order": "paper"}).json()
    by_id = client.get("/api/questions", params={"paper": "P1", "order": "paper"}).json()
    assert by_slug["total"] == by_id["total"] == 12
    mock_id = client.post("/api/mocks", json={"paper_id": "ssc-cgl-2023-07-18-shift-1"}).json()["id"]
    assert client.get(f"/api/mocks/{mock_id}").json()["pattern"]["id"] == "cgl-pre"
    # Slugs carry the exam, date and shift (clashes on the same day and shift get -2, -3).
    mts = {p["id"]: p["slug"] for p in client.get("/api/papers", params={"exam": "SSC-MTS"}).json()}
    assert mts == {"M1": "ssc-mts-2019-08-02-shift-1", "M2": "ssc-mts-2024-10-15-shift-1"}


def test_browser_errors_are_kept_newest_first_and_trimmed(client):
    assert client.get("/api/client-errors").json() == []
    for i in range(32):
        res = client.post("/api/client-errors", json={"message": f"TypeError: boom {i}", "url": "/mocks", "stack": "x" * 5000})
        assert res.status_code == 204
    kept = client.get("/api/client-errors").json()
    assert len(kept) == 30
    assert kept[0]["message"] == "TypeError: boom 31" and kept[-1]["message"] == "TypeError: boom 2"
    assert len(kept[0]["stack"]) == 2000 and kept[0]["url"] == "/mocks" and kept[0]["at"]


def test_missing_build_files_are_404_not_the_page(client, tmp_path, monkeypatch):
    from app import config
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "index-new.js").write_text("console.log(1)")
    monkeypatch.setattr(config, "FRONTEND_DIST", tmp_path)
    assert client.get("/assets/index-new.js").status_code == 200
    assert client.get("/assets/index-old.js").status_code == 404
    assert "root" in client.get("/mocks/ssc-cgl").text
