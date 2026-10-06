"""Daily current affairs for the General Awareness section, built without any LLM call.

Sources, all free and keyless:
* Wikipedia's "Portal:Current events" page for the day: curated world news, available for any date.
* RSS from The Hindu (national, international, business, sci-tech, sport) and The Indian Express (India).
  Feeds reach back only one to eight days, so each refresh files what it sees under the day it was published.

Every story is sorted into a category and scored for exam relevance on the server:
* keyword rules for what SSC asks about (appointments, awards, schemes, summits, launches, sports titles)
  and against what it never asks (live blogs, price tickers, gadget reviews, crime, local politics);
* similarity, in the RAG index's embedding space, to the 2,700 current-affairs PYQs from real papers.
Kept stories are linked to the closest General Awareness PYQs. The day's quiz is those linked PYQs,
topped up with recent current-affairs PYQs; answers go through normal practice, so the learner model learns from them.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

import httpx
import numpy as np

from . import rag

IST = timezone(timedelta(hours=5, minutes=30))
KEEP_DAYS = 60        # the archive; older stories are pruned
FEED_REACH_DAYS = 8   # the longest any feed reaches back
MAX_PER_DAY = 40
MIN_SCORE = 0.4
FEEDS_EVERY = timedelta(minutes=30)
WIKI_EVERY = timedelta(hours=1)  # a day's page is still being edited for about two days
QUIZ_SIZE = 10
LINK_MIN_SIMILARITY = 0.6
USER_AGENT = "SSC-Practice/1.0 (https://github.com/shivam-srivastava-031/Dev-Exam_practice)"

CATEGORIES = {
    "national": "National", "international": "International", "economy": "Economy",
    "science": "Science & Tech", "defence": "Defence", "environment": "Environment",
    "sports": "Sports", "awards": "Awards", "people": "People in news",
}

FEEDS = [  # (name, url, source, default category)
    ("The Hindu: National", "https://www.thehindu.com/news/national/feeder/default.rss", "The Hindu", "national"),
    ("The Hindu: International", "https://www.thehindu.com/news/international/feeder/default.rss", "The Hindu",
     "international"),
    ("The Hindu: Business", "https://www.thehindu.com/business/feeder/default.rss", "The Hindu", "economy"),
    ("The Hindu: Sci-Tech", "https://www.thehindu.com/sci-tech/feeder/default.rss", "The Hindu", "science"),
    ("The Hindu: Sport", "https://www.thehindu.com/sport/feeder/default.rss", "The Hindu", "sports"),
    ("The Indian Express: India", "https://indianexpress.com/section/india/feed/", "The Indian Express", "national"),
]
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_PAGE = "https://en.wikipedia.org/wiki/"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")

# --------------------------------------------------------------------------- dates


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    """Days follow Indian Standard Time, whatever the server's clock zone."""
    return now().astimezone(IST).date()


def parse_day(text: str | None) -> date:
    """A day the archive can show: today (the default) or one of the previous KEEP_DAYS."""
    if not text:
        return today()
    day = date.fromisoformat(text)  # ValueError for anything but YYYY-MM-DD
    if not today() - timedelta(days=KEEP_DAYS) <= day <= today():
        raise ValueError(f"current affairs are kept for the last {KEEP_DAYS} days")
    return day


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse_utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- stories


@dataclass
class Story:
    title: str
    url: str
    source: str
    category: str              # the feed's or Wikipedia heading's default until categorise() refines it
    day: str                   # 'YYYY-MM-DD': IST publication day; for Wikipedia, the page's date
    summary: str = ""
    published: str | None = None  # UTC 'YYYY-MM-DD HH:MM:SS', when the source gives one
    section: str = ""          # the source's own label: a feed's state tag, a Wikipedia heading
    score: float = 0.0
    related: list = field(default_factory=list)  # [[question id, similarity], ...]

    @property
    def from_wikipedia(self) -> bool:
        return self.source.endswith("Wikipedia")

    @property
    def id(self) -> str:
        # A feed story is its URL; a Wikipedia event (whose sources can repeat) is its text on that day.
        key = f"{self.day}|{self.title}" if self.from_wikipedia else self.url
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def row(self) -> tuple:
        return (self.id, self.day, self.title, self.summary, self.url, self.source, self.section, self.category,
                self.published, round(self.score, 4), json.dumps(self.related))

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> Story:
        return cls(title=r["title"], url=r["url"], source=r["source"], category=r["category"], day=r["day"],
                   summary=r["summary"] or "", published=r["published"], section=r["section"] or "",
                   score=r["score"], related=json.loads(r["related"]))


COLUMNS = "id, day, title, summary, url, source, section, category, published, score, related"

_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", html.unescape(text or ""))).strip()


def parse_feed(xml: bytes, source: str, category: str, fallback_day: date) -> list[Story]:
    """RSS items as stories, filed under their publication day in IST (undated items: `fallback_day`)."""
    out = []
    for item in ET.fromstring(xml).iter("item"):
        title, url = _clean(item.findtext("title")), (item.findtext("link") or "").strip()
        if not title or not url.startswith("http"):
            continue
        published = None
        try:
            published = parsedate_to_datetime((item.findtext("pubDate") or "").strip())
        except (TypeError, ValueError):
            pass
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        out.append(Story(title=title, url=url, source=source, category=category,
                         day=(published.astimezone(IST).date() if published else fallback_day).isoformat(),
                         summary=_clean(item.findtext("description"))[:400],
                         published=_utc(published) if published else None,
                         section=_clean(item.findtext("category"))))
    return out


class _WikiParser(HTMLParser):
    """Portal:Current events/<date> is '<p><b>Heading</b></p>' followed by nested lists: a topic's <li>
    holds a sub-list, and an event's <li> holds its text and the news reports it cites as external links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.heading = ""
        self.in_heading: list[str] | None = None
        self.items: list[dict] = []  # open <li>s, innermost last
        self.link: tuple[str, list[str]] | None = None  # open external link: (href, text)
        self.skip = 0
        self.events: list[tuple[str, str, list[tuple[str, str]]]] = []  # (heading, text, [(name, url)])

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "sup"):
            self.skip += 1
        elif tag == "b" and not self.items:
            self.in_heading = []
        elif tag == "li":
            self.items.append({"text": [], "links": [], "nested": False})
        elif tag == "ul" and self.items:
            self.items[-1]["nested"] = True
        elif tag == "a" and self.items and "external" in (a.get("class") or ""):
            self.link = (a.get("href") or "", [])

    def handle_endtag(self, tag):
        if tag in ("style", "script", "sup"):
            self.skip = max(0, self.skip - 1)
        elif tag == "b" and self.in_heading is not None:
            self.heading = _clean("".join(self.in_heading))
            self.in_heading = None
        elif tag == "a" and self.link is not None:
            href, text = self.link
            self.link = None
            if self.items and href.startswith("http"):
                self.items[-1]["links"].append((_clean("".join(text)).strip("() "), href))
        elif tag == "li" and self.items:
            li = self.items.pop()
            text = _clean("".join(li["text"]))
            # A topic heading ("2026 Brazilian general election") has a sub-list and cites nothing.
            if self.heading and text and (li["links"] or not li["nested"]):
                self.events.append((self.heading, text, li["links"]))

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_heading is not None:
            self.in_heading.append(data)
        elif self.link is not None:
            self.link[1].append(data)
        elif self.items:
            self.items[-1]["text"].append(data)


# Wikipedia headings to default categories. Conflicts and crime stay put: their wording ("army",
# "elected") would otherwise drag foreign wars into Defence or People.
WIKI_SECTIONS = {
    "armed conflicts": "international", "law and crime": "international", "business": "economy",
    "health and environment": "environment", "science": "science", "sports": "sports",
}
_FIXED_SECTIONS = ("armed conflicts", "law and crime")
_INDIA = re.compile(r"\b(India|Indian|Indians|India's|New Delhi|Lok Sabha|Rajya Sabha)\b")


def wiki_page(day: date) -> str:
    return f"Portal:Current_events/{day.year}_{MONTHS[day.month - 1]}_{day.day}"


def parse_wikipedia(page_html: str, day: date) -> list[Story]:
    parser = _WikiParser()
    parser.feed(page_html)
    out = []
    for heading, text, links in parser.events:
        if len(text) < 25:  # stray navigation ("edit", "history")
            continue
        default = next((c for prefix, c in WIKI_SECTIONS.items() if heading.lower().startswith(prefix)), "international")
        if default == "international" and _INDIA.search(text):
            default = "national"
        name, url = links[0] if links else ("", WIKI_PAGE + wiki_page(day))
        out.append(Story(title=text[:400], url=url, source=f"{name} via Wikipedia" if name else "Wikipedia",
                         category=default, day=day.isoformat(), section=heading))
    return out


# --------------------------------------------------------------------------- category and relevance

# First match wins, so "IAF chief appointed" is People, not Defence. Headlines come in sentence case
# and Title Case, so rules ignore case except where a word is only telling when capitalised.
CATEGORY_RULES = [
    ("awards", re.compile(r"\b(award(s|ed)?|prizes?|laureates?|honou?r(s|ed)? with|conferred|Nobel|Padma|Bharat Ratna|"
                          r"Booker|Oscars?|Grammy|Pulitzer)\b", re.I)),
    ("people", re.compile(r"\b(appoint(s|ed)?|appointment|takes? (over )?charge|sworn in|takes? oath|(re-?)?elect(s|ed)|"
                          r"(named|as|becomes) (the )?(new |next )?(chief|head|CEO|MD|chairman|chairperson|president|"
                          r"prime minister|chief minister|chief justice|director|captain|coach|governor|ambassador)|"
                          r"new (chief|head|chairman|chairperson|president|prime minister|chief minister|governor|CEO|"
                          r"MD|director|captain)|passes away|passed away|dies at|died at|obituary|resigns)\b", re.I)),
    ("sports", re.compile(r"\b(cricket|football|hockey|tennis|badminton|chess|wrestl\w+|boxing|athletics|archery|"
                          r"Olympics?|Paralympics?|Asian Games|Commonwealth Games|World Cup|Grand Slam|Wimbledon|"
                          r"championships?|tournament|medals?|ICC|FIFA|BCCI|IPL|T20|ODI)\b", re.I)),
    ("defence", re.compile(r"\b(army|navy|naval|air force|IAF|DRDO|missiles?|warships?|frigate|submarines?|"
                           r"(military|joint|naval|air) exercise|(?-i:[Ee]xercise [A-Z]\w+)|defen[cs]e)\b", re.I)),
    ("science", re.compile(r"\b(ISRO|NASA|satellites?|spacecraft|rocket|space station|lunar|(?-i:Moon|Mars|AI)|"
                           r"telescope|quantum|semiconductors?|artificial intelligence|vaccines?|genome|scientists?)\b",
                           re.I)),
    ("environment", re.compile(r"\b(climate|tigers?|wildlife|forests?|Ramsar|wetlands?|biodiversity|pollution|emissions?|"
                               r"COP\d*|national park|sanctuary|species|glaciers?|cyclone|earthquake|floods?|heatwave)\b",
                               re.I)),
    ("economy", re.compile(r"\b(GDP|inflation|RBI|repo rate|SEBI|(?<!West )banks?|banking|GST|tax(es)?|budget|exports?|"
                           r"imports?|trade (deal|pact|agreement)|FDI|FPIs?|econom(y|ic)|fiscal|rupee|Sensex|Nifty|IPO|"
                           r"merger)\b", re.I)),
]

# What current-affairs questions are made of.
SIGNALS = re.compile(
    r"\b(appoint\w*|named|takes? charge|sworn in|(re-?)?elected|award\w*|prizes?|honou?r\w*|launch(es|ed)?|"
    r"inaugurat\w*|unveil\w*|schemes?|yojana|mission|abhiyan|portal|policy|bill|act|ordinance|cabinet|approv\w*|"
    r"MoUs?|agreements?|pacts?|treaty|summit|bilateral|visit\w*|exercise|ISRO|DRDO|satellites?|missiles?|GDP|"
    r"inflation|repo|RBI|SEBI|index|ranking|ranked|report|record|first|largest|world'?s|UNESCO|heritage|Ramsar|"
    r"tiger reserve|national park|observed|anniversary|day|medals?|gold|silver|bronze|titles?|champions?\w*|trophy|"
    r"cup|Olympics?|Nobel|Padma|passes away|dies at|projection|growth|crore)\b", re.I)
NOISE = re.compile(r"\blive updates\b|\blive:|\bnews live\b|\bhighlights\b|\blive blog\b|\b(rate|rates|price|prices) today\b|"
                   r"\breview\s*[|:]|\bhoroscope\b|\bcrossword\b|\bphotos\b|\bin pics\b|\bwatch:|\bvideo:|\bpodcast\b|"
                   r"\|\s*\w[\w ]* writes\b|\bopinion\b|\beditorial\b|\btoday's paper\b", re.I)
CRIME = re.compile(r"\b(murder\w*|kill(s|ed|ing)?|rape\w*|assault\w*|arrest\w*|police|accused|stabb\w*|shot dead|"
                   r"fraud|scam|abduct\w*|suicide|brib\w*|raids?|seiz\w*|detain\w*|custody|FIR|CBI case|probe)\b", re.I)
RHETORIC = re.compile(r"\b(slams?|hits out|jibe|attacks?|accuses?|takes? a dig|lashes out|blames?|retorts?|row|spat|"
                      r"protests?|counters?|questions)\b|^[‘'\"“]|:\s*[‘'\"“]", re.I)
LOCAL = re.compile(r"\b(MLA|MLC|corporator|panchayat|ward|collector|district|taluk|municipal|civic body|inspects?)\b",
                   re.I)
PARTY_POLITICS = re.compile(r"\b(BJP|Congress|AAP|TMC|DMK|AIADMK|BRS|YSRCP|TDP|RJD|BSP|Shiv Sena|NCP|CPI\(M\)|LDF|UDF|"
                            r"Opposition|INDIA bloc|polls?|bypolls?|by-?elections?|candidates?|campaign\w*)\b")
MATCH_REPORT = re.compile(r"\b(vs?\.?|versus|semis?|semi-?finals?|quarter-?finals?|shines|preview|squad|"
                          r"playing XI)\b", re.I)
# The categories SSC asks factual questions about: who won, who was appointed, what was launched.
CATEGORY_BONUS = {"awards": 0.2, "people": 0.2, "science": 0.2, "defence": 0.2, "environment": 0.2, "sports": 0.15,
                  "economy": 0.1, "international": 0.1, "national": 0.0}
# Feed tags that are never current affairs, and the state and city tags that mark local news.
SKIP_TAGS = {"editorial", "opinion", "letters", "videos", "visual story", "gadgets", "races", "movies", "life & style",
             "children", "cartoon", "crossword", "horoscope"}
LOCAL_TAGS = set("""andhra-pradesh arunachal-pradesh assam bihar chhattisgarh goa gujarat haryana himachal-pradesh jharkhand
karnataka kerala keralam madhya-pradesh maharashtra manipur meghalaya mizoram nagaland odisha punjab rajasthan sikkim
tamil-nadu telangana tripura uttar-pradesh uttarakhand west-bengal delhi jammu-and-kashmir ladakh puducherry chandigarh
andaman-and-nicobar-islands lakshadweep bengaluru chennai hyderabad kochi mumbai kolkata coimbatore madurai mangaluru
thiruvananthapuram visakhapatnam vijayawada tiruchi tiruchirapalli kozhikode mysuru pune lucknow noida gurugram
""".split())


def categorise(s: Story) -> str:
    if s.from_wikipedia and s.section.lower().startswith(_FIXED_SECTIONS):
        return s.category
    text = f"{s.title} {s.summary[:160]}"
    return next((name for name, rule in CATEGORY_RULES if rule.search(text)), s.category)


def relevance(s: Story, semantic: float | None) -> float | None:
    """0-1 exam relevance of a categorised story; None for what is never worth keeping (live blogs, price
    tickers, reviews, opinion).

    The wording carries most of it. `semantic`, similarity to real current-affairs PYQs scaled to 0-1
    (None without the RAG index), weighs less: static embeddings read short headlines loosely.
    """
    head, tag = s.title, s.section.lower()
    if NOISE.search(head) or (not s.from_wikipedia and tag in SKIP_TAGS):
        return None
    in_head = {m.group(0).lower() for m in SIGNALS.finditer(head)}
    in_summary = {m.group(0).lower() for m in SIGNALS.finditer(s.summary[:200])} - in_head
    signals = len(in_head) + 0.5 * len(in_summary)
    score = (0.35 * (0.5 if semantic is None else semantic) + 0.45 * min(1.0, signals / 3)
             + CATEGORY_BONUS.get(s.category, 0.0))
    penalties = [(CRIME, 0.3), (RHETORIC, 0.2), (PARTY_POLITICS, 0.15), (LOCAL, 0.2)]
    if s.category == "sports":
        penalties.append((MATCH_REPORT, 0.15))
    score -= sum(weight for rule, weight in penalties if rule.search(head))
    if not s.from_wikipedia and tag.replace(" ", "-") in LOCAL_TAGS:
        score -= 0.2  # a state or city tag on a national feed
    return round(max(0.0, min(1.0, score)), 4)


# --------------------------------------------------------------------------- the RAG side

_reference: dict = {}


def _bank(conn: sqlite3.Connection):
    """The RAG index, the vectors of its current-affairs PYQs and a mask of the GK PYQs stories may link to."""
    index = rag.get_index()
    if index is None:
        return None
    if _reference.get("index") is not index:
        ca = [r[0] for r in conn.execute("SELECT id FROM questions WHERE origin = 'pyq' AND subject = 'GK' "
                                         "AND chapter = 'current-affairs'")]
        gk = [r[0] for r in conn.execute("SELECT id FROM questions WHERE origin = 'pyq' AND subject = 'GK' "
                                         "AND question NOT LIKE '%[IMAGE%' AND options NOT LIKE '%[IMAGE%'")]
        _reference.update(index=index, ca=index.vecs[np.isin(index.ids, ca)], gk=np.isin(index.ids, gk))
    return _reference["index"], _reference["ca"], _reference["gk"]


def _embed(stories: list[Story]) -> np.ndarray:
    return rag._normalise(rag.get_model().encode([f"{s.title}. {s.summary[:200]}" for s in stories]))


def _semantic(vecs: np.ndarray, ca: np.ndarray) -> np.ndarray:
    """Mean similarity to the five closest current-affairs PYQs, scaled so that 0.30 -> 0 and 0.55 -> 1."""
    if not len(ca):
        return np.full(len(vecs), 0.5)
    k = min(5, len(ca))
    top = np.sort(vecs @ ca.T, axis=1)[:, -k:].mean(axis=1)
    return np.clip((top - 0.30) / 0.25, 0, 1)


# Words that say nothing about what a story is about.
_GENERIC = rag._STOP | set("""india indian indias government minister ministry national state states first year years crore
lakh said says will after over more than with from into about under country world people today report news also amid
during between against across their other last next week month time high higher lower record major plan plans held
following given which what were have been being union cabinet centre central""".split())


def _terms(text: str) -> tuple[set[str], set[str]]:
    """The telling words of a text, lower-cased and singular: all of them, and those written capitalised
    (names, acronyms like RBI)."""
    words, names = set(), set()
    for w in re.findall(r"[A-Za-z][A-Za-z-]+", text):
        if len(w) < 3 or (len(w) == 3 and not w.isupper()):
            continue
        low = w.lower()
        low = low[:-1] if len(low) > 4 and low.endswith("s") and not low.endswith("ss") else low
        if low in _GENERIC:
            continue
        words.add(low)
        if w[0].isupper():
            names.add(low)
    return words, names


def link(conn: sqlite3.Connection, stories: list[Story], vecs: np.ndarray | None) -> None:
    """Attach up to two General Awareness PYQs to each story: close in meaning, and sharing a name or two words."""
    bank = _bank(conn)
    for j, s in enumerate(stories):
        words, names = _terms(s.title)  # the headline is what the story is about
        if bank is not None and vecs is not None:
            index, _, gk = bank
            sims = index.vecs @ vecs[j]
            sims[~gk] = -1.0
            top = np.argpartition(-sims, 8)[:8]
            candidates = [(int(index.ids[i]), round(float(sims[i]), 4)) for i in top[np.argsort(-sims[top])]
                          if sims[i] >= LINK_MIN_SIMILARITY]
        else:  # no vector index (fresh install, tests): keyword search only
            candidates = [(qid, None) for qid in rag.bm25(conn, s.title, 5, {"subject": "GK"})]
        if not candidates:
            s.related = []
            continue
        texts = {r[0]: f"{r[1]} {r[2]}" for r in conn.execute(
            f"SELECT id, question, options FROM questions WHERE id IN ({','.join('?' * len(candidates))})",
            [c[0] for c in candidates])}

        def shares(qid: int) -> bool:
            common = words & _terms(texts.get(qid, ""))[0]
            return bool(common & names) or len(common) >= 2
        s.related = [[qid, sim] for qid, sim in candidates if shares(qid)][:2]


def _dedupe(stories: list[Story], vecs: np.ndarray | None) -> list[int]:
    """Indices of stories to keep, best first, dropping retellings of one already kept."""
    order = sorted(range(len(stories)), key=lambda i: -stories[i].score)
    kept: list[int] = []
    titles: set[str] = set()
    for i in order:
        key = re.sub(r"\W+", " ", stories[i].title.lower()).strip()
        if key in titles:
            continue
        if vecs is not None and kept and float(np.max(vecs[kept] @ vecs[i])) >= 0.85:
            continue
        kept.append(i)
        titles.add(key)
        if len(kept) >= MAX_PER_DAY:
            break
    return kept


# --------------------------------------------------------------------------- storing a refresh


def _fetch_log(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = 'news.fetched'").fetchone()
    log = json.loads(row["value"]) if row else {}
    return {"feeds": log.get("feeds"), "wikipedia": log.get("wikipedia", {})}


def due(conn: sqlite3.Connection, day: date) -> tuple[bool, bool]:
    """Whether the feeds and the day's Wikipedia page should be fetched again."""
    log, current = _fetch_log(conn), now()
    feeds_at = log["feeds"]
    feeds = (today() - day).days <= FEED_REACH_DAYS and (
        feeds_at is None or current - _parse_utc(feeds_at) >= FEEDS_EVERY)
    wiki_at = log["wikipedia"].get(day.isoformat())
    settled = datetime.combine(day + timedelta(days=2), datetime.min.time(), timezone.utc)  # no more edits expected
    wiki = wiki_at is None or (_parse_utc(wiki_at) < settled and current - _parse_utc(wiki_at) >= WIKI_EVERY)
    return feeds, wiki


def save(conn: sqlite3.Connection, fetched: list[Story], feeds: bool, wiki_day: date | None) -> None:
    """File freshly fetched stories under their days: score the new ones, drop repeats, keep each day's best."""
    last, first = today(), today() - timedelta(days=KEEP_DAYS)
    by_day: dict[str, list[Story]] = {}
    for s in fetched:
        if first.isoformat() <= s.day <= last.isoformat():
            by_day.setdefault(s.day, []).append(s)
    wiki_key = wiki_day.isoformat() if wiki_day else None
    bank = _bank(conn)
    with conn:
        conn.execute("DELETE FROM news WHERE day < ?", (first.isoformat(),))
        taken = {r[0] for r in conn.execute("SELECT id FROM news")}  # a story is filed under one day only
        for day in sorted(set(by_day) | ({wiki_key} if wiki_key else set())):
            stored = [Story.from_row(r) for r in conn.execute(f"SELECT {COLUMNS} FROM news WHERE day = ?", (day,))]
            if day == wiki_key:  # the page was just read in full
                taken -= {s.id for s in stored if s.from_wikipedia}
                stored = [s for s in stored if not s.from_wikipedia]
            new = list({s.id: s for s in by_day.get(day, []) if s.id not in taken}.values())
            if not new and day != wiki_key:
                continue
            if new:
                semantic = _semantic(_embed(new), bank[1]) if bank else [None] * len(new)
                for s, sem in zip(new, semantic):
                    s.category = categorise(s)
                    score = relevance(s, None if sem is None else float(sem))
                    s.score = -1.0 if score is None else score
                new = [s for s in new if s.score >= MIN_SCORE]
            pool = stored + new
            vecs = _embed(pool) if bank and pool else None
            keep = _dedupe(pool, vecs)
            fresh = [i for i in keep if i >= len(stored)]  # only new stories still need their PYQs
            link(conn, [pool[i] for i in fresh], vecs[fresh] if vecs is not None else None)
            conn.execute("DELETE FROM news WHERE day = ?", (day,))
            conn.executemany(f"INSERT INTO news ({COLUMNS}) VALUES ({', '.join('?' * 11)})",
                             [pool[i].row() for i in keep])
            taken |= {pool[i].id for i in keep}
        log = _fetch_log(conn)
        stamp = _utc(now())
        if feeds:
            log["feeds"] = stamp
        if wiki_day:
            log["wikipedia"][wiki_day.isoformat()] = stamp
        log["wikipedia"] = {d: t for d, t in log["wikipedia"].items() if d >= first.isoformat()}
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('news.fetched', ?)", (json.dumps(log),))


# --------------------------------------------------------------------------- fetching


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> bytes:
    resp = await client.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


async def _fetch_feeds(client: httpx.AsyncClient) -> tuple[list[Story], list[str]]:
    results = await asyncio.gather(*(_get(client, url) for _, url, _, _ in FEEDS), return_exceptions=True)
    stories, failed = [], []
    for (name, _, source, category), result in zip(FEEDS, results):
        try:
            if isinstance(result, BaseException):
                raise result
            stories += parse_feed(result, source, category, today())
        except (httpx.HTTPError, ET.ParseError) as e:
            failed.append(f"{name} ({e.__class__.__name__})")
    return stories, failed


async def _fetch_wikipedia(client: httpx.AsyncClient, day: date) -> tuple[list[Story], list[str]]:
    try:
        data = json.loads(await _get(client, WIKI_API, {"action": "parse", "page": wiki_page(day), "prop": "text",
                                                        "format": "json", "formatversion": "2", "redirects": "1"}))
    except (httpx.HTTPError, ValueError) as e:
        return [], [f"Wikipedia ({e.__class__.__name__})"]
    if "error" in data:
        if data["error"].get("code") == "missingtitle":  # nothing posted for that day yet
            return [], []
        return [], [f"Wikipedia ({data['error'].get('code', 'error')})"]
    return parse_wikipedia(data["parse"]["text"], day), []


async def _nothing() -> tuple[list[Story], list[str]]:
    return [], []


_refreshing = asyncio.Lock()


async def refresh(conn: sqlite3.Connection, day: date) -> list[str]:
    """Fetch whatever is due for `day` and store it. Returns the sources that could not be reached."""
    async with _refreshing:  # a second tab waits, then finds nothing due
        feeds, wiki = due(conn, day)
        if not feeds and not wiki:
            return []
        async with httpx.AsyncClient() as client:
            (stories, failed), (wiki_stories, wiki_failed) = await asyncio.gather(
                _fetch_feeds(client) if feeds else _nothing(), _fetch_wikipedia(client, day) if wiki else _nothing())
        # A failed Wikipedia read is not logged, so the next visit tries again.
        await asyncio.to_thread(save, conn, stories + wiki_stories, feeds, day if wiki and not wiki_failed else None)
        return failed + wiki_failed


# --------------------------------------------------------------------------- reading


def digest(conn: sqlite3.Connection, day: date) -> dict:
    rows = conn.execute(f"SELECT {COLUMNS} FROM news WHERE day = ? ORDER BY score DESC", (day.isoformat(),)).fetchall()
    stories = [Story.from_row(r) for r in rows]
    ids = sorted({qid for s in stories for qid, _ in s.related})
    questions = {r["id"]: r for r in conn.execute(
        f"SELECT q.id, q.question, q.year, p.title AS paper_title FROM questions q JOIN papers p ON p.id = q.paper_id "
        f"WHERE q.id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    counts: dict[str, int] = {}
    for s in stories:
        counts[s.category] = counts.get(s.category, 0) + 1
    log = _fetch_log(conn)
    stamps = [t for t in (log["feeds"] if (today() - day).days <= FEED_REACH_DAYS else None,
                          log["wikipedia"].get(day.isoformat())) if t]
    feeds, wiki = due(conn, day)
    return {
        "day": day.isoformat(),
        "today": today().isoformat(),
        "oldest": (today() - timedelta(days=KEEP_DAYS)).isoformat(),
        "stories": [{
            "id": s.id, "title": s.title, "summary": s.summary, "url": s.url, "source": s.source,
            "category": s.category, "published": s.published, "score": s.score,
            "related": [{"id": qid, "question": questions[qid]["question"], "year": questions[qid]["year"],
                         "paper_title": questions[qid]["paper_title"]} for qid, _ in s.related if qid in questions],
        } for s in stories],
        "categories": [{"code": c, "name": n, "count": counts[c]} for c, n in CATEGORIES.items() if counts.get(c)],
        "days": [{"day": r[0], "stories": r[1]} for r in conn.execute(
            "SELECT day, COUNT(*) FROM news GROUP BY day ORDER BY day DESC LIMIT ?", (KEEP_DAYS + 1,))],
        "updated_at": max(stamps) if stamps else None,
        "needs_refresh": feeds or wiki,
        "quiz_linked": min(QUIZ_SIZE, len({s.related[0][0] for s in stories if s.related})),
        "quiz_size": QUIZ_SIZE,
    }


def quiz(conn: sqlite3.Connection, day: date, n: int = QUIZ_SIZE) -> list[dict]:
    """The day's quiz: PYQs linked to its top stories, then recent current-affairs PYQs not tried before that day.

    Nothing answered during the day changes which questions it holds, so a reload keeps the same quiz.
    """
    picks: list[dict] = []
    seen: set[int] = set()
    for title, related in conn.execute("SELECT title, related FROM news WHERE day = ? ORDER BY score DESC",
                                       (day.isoformat(),)):
        linked = json.loads(related)
        if linked and linked[0][0] not in seen and len(picks) < n:
            seen.add(linked[0][0])
            short = title if len(title) <= 140 else title[:137].rsplit(" ", 1)[0] + "…"
            picks.append({"id": linked[0][0], "kind": "news", "reason": f"In the news: {short}"})
    if len(picks) < n:
        start = _utc(datetime.combine(day, datetime.min.time(), IST))
        rows = conn.execute(f"""
            SELECT q.id, q.year FROM questions q
            WHERE q.origin = 'pyq' AND q.subject = 'GK' AND q.chapter = 'current-affairs'
              AND q.question NOT LIKE '%[IMAGE%' AND q.options NOT LIKE '%[IMAGE%'
              AND NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id = q.id AND a.created_at < ?)
              {f"AND q.id NOT IN ({','.join('?' * len(seen))})" if seen else ""}
            ORDER BY q.year DESC, (q.id * 2654435761 + ?) % 4294967291 LIMIT ?""",
                            [start, *seen, int(day.strftime("%Y%m%d")), n - len(picks)]).fetchall()
        picks += [{"id": r["id"], "kind": "recent",
                   "reason": f"Recent current affairs: asked in an SSC paper in {r['year']}"} for r in rows]
    return picks
