"""Daily current affairs: parsing the sources, scoring stories, the refresh cycle and the quiz. No network."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app import current_affairs as ca
from app import db

NOW = datetime(2026, 10, 6, 6, 30, tzinfo=timezone.utc)  # 12:00 IST

FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>National</title>
<item><title><![CDATA[Air Marshal Ashutosh Dixit appointed new Chief of Indian Air Force]]></title>
  <link><![CDATA[https://news.example/iaf-chief]]></link>
  <description><![CDATA[<p>He takes over from Air Chief Marshal A.P. Singh &amp; will serve three years.</p>]]></description>
  <category><![CDATA[India]]></category><pubDate><![CDATA[Tue, 06 Oct 2026 10:00:00 +0530]]></pubDate></item>
<item><title>Gold Rate Today, October 6: Check 22 and 24 carat prices in Chennai</title>
  <link>https://news.example/gold-rate</link><pubDate>Tue, 06 Oct 2026 08:00:00 +0530</pubDate></item>
<item><title>Minister inspects road widening works</title><link>https://news.example/road</link>
  <category>Andhra Pradesh</category><pubDate>Tue, 06 Oct 2026 09:00:00 +0530</pubDate></item>
<item><title>India wins gold medal in hockey at the Asian Games</title><link>https://news.example/hockey</link>
  <category>Sport</category><pubDate>Mon, 05 Oct 2026 20:00:00 +0000</pubDate></item>
<item><title>Union Cabinet approves new scheme for semiconductor manufacturing</title><link>https://news.example/chips</link>
  <category>India</category><pubDate>Fri, 02 Oct 2026 09:00:00 +0530</pubDate></item>
</channel></rss>"""

EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>x</title></channel></rss>"""

WIKI_HTML = """<div class="mw-parser-output"><style>.current-events{color:red}</style>
<div class="current-events-heading"><ul><li><a class="external text"
  href="https://en.wikipedia.org/w/index.php?title=Portal:Current_events&amp;action=edit">edit</a></li></ul></div>
<p><b>Science and technology</b></p>
<ul><li>Karl Deisseroth, Peter Hegemann and Georg Nagel are jointly awarded the
  <a href="/wiki/Nobel_Prize_in_Physiology_or_Medicine">Nobel Prize in Medicine</a> for optogenetics.<sup>[1]</sup>
  <a rel="nofollow" class="external text" href="https://www.reuters.com/nobel-medicine">(Reuters)</a></li></ul>
<p><b>Politics and elections</b></p>
<ul><li><a href="/wiki/Laos">2026 Laotian government formation</a>
  <ul><li>The National Assembly of Laos approves Saleumxay Kommasith as the new Prime Minister.
    <a rel="nofollow" class="external text" href="https://laotiantimes.com/new-pm">(The Laotian Times)</a>
    <a rel="nofollow" class="external text" href="https://apnews.com/laos">(AP)</a></li></ul></li>
  <li>Prime Minister of India Narendra Modi opens the new Parliament library in New Delhi.
    <a rel="nofollow" class="external text" href="https://pib.gov.in/library">(PIB)</a></li></ul>
<p><b>Armed conflicts and attacks</b></p>
<ul><li>The army of Ruritania launches an offensive against rebels in the north.
  <a rel="nofollow" class="external text" href="https://apnews.com/ruritania">(AP)</a></li></ul>
</div>"""


def serve_news(monkeypatch, feed: bytes = FEED, wiki: str = WIKI_HTML, fail: set[str] = frozenset()) -> list[str]:
    """Answer every source from memory; returns the URLs asked for, in order."""
    calls: list[str] = []
    national = ca.FEEDS[0][1]

    async def fake_get(client, url, params=None):
        calls.append(url)
        if any(name in url for name in fail):
            raise httpx.ConnectError("unreachable")
        if url == ca.WIKI_API:
            return json.dumps({"parse": {"title": params["page"], "text": wiki}}).encode()
        return feed if url == national else EMPTY_FEED
    monkeypatch.setattr(ca, "_get", fake_get)
    monkeypatch.setattr(ca, "now", lambda: NOW)
    return calls


@pytest.fixture()
def bank(client):
    """Make the fixture's General Awareness questions current-affairs PYQs, one of them about the IAF chief."""
    conn = db.connect(client.app.state.db_path)
    with conn:
        conn.execute("UPDATE questions SET chapter = 'current-affairs' WHERE subject = 'GK'")
        conn.execute("UPDATE questions SET question = 'Who became the Chief of the Air Staff of the Indian Air Force "
                     "in September 2024?' WHERE qid = 'cgl-GK-1'")
        conn.execute("INSERT INTO questions_fts (questions_fts) VALUES ('rebuild')")
    iaf = conn.execute("SELECT id FROM questions WHERE qid = 'cgl-GK-1'").fetchone()[0]
    conn.close()
    return {"iaf": iaf}


def test_feed_items_are_filed_under_their_day_in_ist():
    stories = ca.parse_feed(FEED, "The Hindu", "national", date(2026, 10, 6))
    by_url = {s.url: s for s in stories}
    iaf = by_url["https://news.example/iaf-chief"]
    assert iaf.day == "2026-10-06" and iaf.published == "2026-10-06 04:30:00" and iaf.section == "India"
    assert iaf.summary == "He takes over from Air Chief Marshal A.P. Singh & will serve three years."
    # 20:00 UTC on the 5th is 01:30 IST on the 6th.
    assert by_url["https://news.example/hockey"].day == "2026-10-06"
    assert by_url["https://news.example/chips"].day == "2026-10-02"


def test_wikipedia_events_keep_their_heading_and_first_source():
    stories = ca.parse_wikipedia(WIKI_HTML, date(2026, 10, 6))
    assert [s.section for s in stories] == ["Science and technology", "Politics and elections",
                                           "Politics and elections", "Armed conflicts and attacks"]
    nobel, laos, india, war = stories
    assert nobel.title.startswith("Karl Deisseroth") and "[1]" not in nobel.title and "(Reuters)" not in nobel.title
    assert (nobel.source, nobel.url) == ("Reuters via Wikipedia", "https://www.reuters.com/nobel-medicine")
    assert laos.source == "The Laotian Times via Wikipedia"
    assert [ca.categorise(s) for s in stories] == ["awards", "people", "national", "international"]
    assert war.id != ca.parse_wikipedia(WIKI_HTML, date(2026, 10, 5))[3].id  # an event belongs to its day


@pytest.mark.parametrize("title, section, kept", [
    ("Air Marshal Ashutosh Dixit appointed new Chief of Indian Air Force", "India", True),
    ("Union Cabinet approves new scheme for semiconductor manufacturing", "India", True),
    ("Gold Rate Today, October 6: Check 22 and 24 carat prices in Chennai", "India", False),
    ("Vivo V80 Review | Preserves the strengths of V series", "Gadgets", False),
    ("India News Live Updates, 6 October 2026", "India", False),
    ("Minister inspects road widening works", "Andhra Pradesh", False),
    ("Man arrested for killing neighbour over parking dispute", "India", False),
    ("Stop daily drama, learn from Modi: BJP to Congress", "India", False),
])
def test_relevance_keeps_exam_material_and_drops_the_rest(title, section, kept):
    s = ca.Story(title=title, url="https://x", source="The Hindu", category="national", day="2026-10-06",
                 section=section)
    s.category = ca.categorise(s)
    score = ca.relevance(s, None)
    assert (score is not None and score >= ca.MIN_SCORE) == kept


def test_refresh_stores_scored_stories_and_links_pyqs(client, bank, monkeypatch):
    calls = serve_news(monkeypatch)
    before = client.get("/api/current-affairs").json()
    assert before["day"] == before["today"] == "2026-10-06"
    assert before["stories"] == [] and before["needs_refresh"] is True

    out = client.post("/api/current-affairs/refresh").json()
    assert out["failed_sources"] == [] and out["needs_refresh"] is False and out["updated_at"]
    assert len(calls) == len(ca.FEEDS) + 1
    titles = [s["title"] for s in out["stories"]]
    assert "Air Marshal Ashutosh Dixit appointed new Chief of Indian Air Force" in titles
    assert not any("Gold Rate" in t or "inspects" in t for t in titles)
    assert any(t.startswith("Karl Deisseroth") for t in titles)          # today's Wikipedia page
    assert not any("semiconductor" in t for t in titles)                  # filed under 2 October
    assert out["stories"] == sorted(out["stories"], key=lambda s: -s["score"])
    iaf = next(s for s in out["stories"] if "Air Force" in s["title"])
    assert iaf["category"] == "people" and iaf["source"] == "The Hindu"
    assert [r["id"] for r in iaf["related"]] == [bank["iaf"]] and "answer" not in iaf["related"][0]
    assert {c["code"]: c["count"] for c in out["categories"]}["people"] >= 1
    assert out["quiz_linked"] == 1

    earlier = client.get("/api/current-affairs", params={"day": "2026-10-02"}).json()
    assert [s["title"] for s in earlier["stories"]] == ["Union Cabinet approves new scheme for semiconductor manufacturing"]
    assert {d["day"] for d in out["days"]} == {"2026-10-06", "2026-10-02"}


def test_refresh_is_throttled_per_source(client, bank, monkeypatch):
    calls = serve_news(monkeypatch)
    client.post("/api/current-affairs/refresh")
    client.post("/api/current-affairs/refresh")
    assert len(calls) == len(ca.FEEDS) + 1  # the second visit found nothing due

    monkeypatch.setattr(ca, "now", lambda: NOW + timedelta(minutes=31))
    assert client.get("/api/current-affairs").json()["needs_refresh"] is True
    client.post("/api/current-affairs/refresh")
    assert calls[-len(ca.FEEDS):] == [url for _, url, _, _ in ca.FEEDS] and len(calls) == 2 * len(ca.FEEDS) + 1

    # Weeks later the feeds no longer reach the day, and its Wikipedia page, last read while it was
    # still being edited, is read one final time.
    monkeypatch.setattr(ca, "now", lambda: NOW + timedelta(days=20))
    day = {"day": "2026-10-06"}
    assert client.get("/api/current-affairs", params=day).json()["needs_refresh"] is True
    seen = len(calls)
    client.post("/api/current-affairs/refresh", params=day)
    assert calls[seen:] == [ca.WIKI_API]
    old = client.get("/api/current-affairs", params=day).json()
    assert old["needs_refresh"] is False and any("Air Force" in s["title"] for s in old["stories"])


def test_unreachable_sources_are_reported_and_retried(client, bank, monkeypatch):
    serve_news(monkeypatch, fail={"thehindu.com/sport", "wikipedia.org"})
    out = client.post("/api/current-affairs/refresh").json()
    assert out["failed_sources"] == ["The Hindu: Sport (ConnectError)", "Wikipedia (ConnectError)"]
    assert any("Air Force" in s["title"] for s in out["stories"])
    assert out["needs_refresh"] is True  # Wikipedia was not read, so the next visit tries again

    calls = serve_news(monkeypatch)
    client.post("/api/current-affairs/refresh")
    assert calls == [ca.WIKI_API]


def test_quiz_links_news_then_adds_recent_pyqs_and_stays_put(client, bank, monkeypatch):
    serve_news(monkeypatch)
    client.post("/api/current-affairs/refresh")
    quiz = client.get("/api/current-affairs/quiz").json()
    items = quiz["items"]
    assert quiz["day"] == "2026-10-06"
    assert items[0]["id"] == bank["iaf"] and items[0]["kind"] == "news"
    assert items[0]["reason"] == "In the news: Air Marshal Ashutosh Dixit appointed new Chief of Indian Air Force"
    assert {i["kind"] for i in items[1:]} == {"recent"} and len(items) == 5  # every GK question in the fixture
    assert len({i["id"] for i in items}) == len(items) and "answer" not in items[1]

    # Answering during the day does not reshuffle the quiz.
    client.post("/api/practice/answer", json={"question_id": items[2]["id"], "chosen": 1})
    conn = db.connect(client.app.state.db_path)
    with conn:
        conn.execute("UPDATE attempts SET created_at = '2026-10-06 05:00:00'")  # 10:30 IST
    conn.close()
    again = client.get("/api/current-affairs/quiz").json()["items"]
    assert [(i["id"], i["kind"]) for i in again] == [(i["id"], i["kind"]) for i in items]
    # The next day's quiz leaves out what was tried before it began.
    monkeypatch.setattr(ca, "now", lambda: NOW + timedelta(days=1))
    assert items[2]["id"] not in {i["id"] for i in client.get("/api/current-affairs/quiz").json()["items"]}


@pytest.mark.parametrize("day", ["2026-10-07", "2026-08-01", "06-10-2026", "yesterday"])
def test_days_outside_the_archive_are_rejected(client, monkeypatch, day):
    serve_news(monkeypatch)
    assert client.get("/api/current-affairs", params={"day": day}).status_code == 422
    assert client.get("/api/current-affairs/quiz", params={"day": day}).status_code == 422
