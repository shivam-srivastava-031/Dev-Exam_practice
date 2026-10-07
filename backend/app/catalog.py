"""Exam vocabulary and real SSC exam patterns.

A pattern describes how the computer-based test is laid out: its timed parts
(sectional timing where SSC uses it), the section order inside each part and the
marking scheme. Mocks built from a previous-year paper reuse the pattern's layout
but keep that paper's own questions and marks; random mocks fill every section
from the question pool and mark it with the pattern's scheme.

Marking and section counts were cross-checked against the dataset itself
(marks_pos / marks_neg per row and the subject mix of every paper).
"""
from __future__ import annotations

from typing import Any

SUBJECTS: dict[str, str] = {
    "REAS": "General Intelligence & Reasoning",
    "GK": "General Awareness",
    "MATH": "Quantitative Aptitude",
    "ENG": "English Language & Comprehension",
    "COMPUTER": "Computer Knowledge",
}

EXAMS: dict[str, str] = {
    "SSC-CGL": "SSC CGL",
    "SSC-CHSL": "SSC CHSL",
    "SSC-CPO": "SSC CPO",
    "SSC-GD": "SSC GD Constable",
    "SSC-MTS": "SSC MTS",
    "SSC-Selection-Post": "SSC Selection Post",
    "SSC-Stenographer": "SSC Stenographer",
}

_STAGE_NAMES: dict[tuple[str, str], str] = {
    ("SSC-CGL", "pre"): "Tier-I",
    ("SSC-CGL", "mains"): "Tier-II",
    ("SSC-CHSL", "pre"): "Tier-I",
    ("SSC-CHSL", "mains"): "Tier-II",
    ("SSC-CPO", "pre"): "Paper-I",
    ("SSC-CPO", "mains"): "Paper-II",
}


def stage_name(exam: str, stage: str) -> str:
    return _STAGE_NAMES.get((exam, stage), "CBT" if stage == "pre" else "Mains")


# The dataset's slugs carry tagging jargon ("-static", "subtypes-not-to-miss");
# these read better under a plain name.
_CHAPTER_NAMES = {
    "puzzle-subtypes-not-to-miss": "Puzzles",
    "other-edge-cases-possible-ssc-gk-gs-questions": "Other GK",
    "miscellaneous-other-possibilities": "Miscellaneous",
    "government-schemes-and-welfare-static-semi-static": "Government Schemes and Welfare",
    "data-interpretation-old": "Data Interpretation (older)",
    "awards-static": "Awards",
    "books-and-authors-static": "Books and Authors",
    "sports-static-gk": "Sports",
    "input-output-machine-type-reasoning": "Input-Output",
    "computer-technology-overlap": "Computers & Technology",
    "logical-statement-questions": "Statement and Conclusion",
    # Sub-topic (concept) slugs that spell out formulas, dates or truncated words.
    "di-table-read": "DI: Tables",
    "di-bar-graph-read": "DI: Bar Graphs",
    "di-pie-chart-read": "DI: Pie Charts",
    "di-line-graph-read": "DI: Line Graphs",
    "di-transitive-verbs": "Ditransitive Verbs",
    "cubic-formula-a-3-b-3-c-3-3abc": "Cubic Identity (a³ + b³ + c³ − 3abc)",
    "conditional-identities-a-b-c-0": "Conditional Identities (a + b + c = 0)",
    "symmetric-identities-x-1-x-k": "Symmetric Identities (x + 1/x = k)",
    "square-root-of-surds-sqrt-a-pm-2-sqrt-b": "Square Root of Surds (√(a ± 2√b))",
    "non-annual-compounding-half-yearly-quarterly-8-monthly-10-mo": "Half-yearly, Quarterly and Other Compounding",
    "finding-time-period-or-capital-share-when-profit-ratio-is-gi": "Time or Capital from a Given Profit Ratio",
    "sum-becomes-a1-in-t1-years-and-a2-in-t2-years": "Sum Becomes A₁ in T₁ Years and A₂ in T₂ Years",
    "overlapping-averages-first-n1-and-last-n2": "Overlapping Averages (First n₁, Last n₂)",
    "present-age-ratio-ratio-after-before-n-years": "Age Ratios Now and n Years Before or After",
    "sp-differential-equations-if-cp-were-x-less-and-sp-y-more": "If CP Were Less and SP More",
    "cp-of-x-articles-sp-of-y-articles": "CP of x Articles = SP of y Articles",
    "bought-at-x-for-y-and-sold-at-a-for-b": "Bought at x for y, Sold at a for b",
    "basic-s-d-t-unit-conversions": "Speed, Distance and Time Basics",
    "unit-digit-ten-s-digit-rules": "Unit and Tens Digit Rules",
    "divisibility-rules-72-88-99": "Divisibility Rules (72, 88, 99)",
    "type-1-fill-empty-completely": "Filling or Emptying a Tank",
    "type-6-taps-closed-during": "Taps Closed Midway",
    "ab-rule-constant-product-pattern": "AB Rule (Constant Product)",
    "perimeter-area-volume-changes-2d-3d-geometry-net-change": "Change in Perimeter, Area and Volume",
    "mean-median-mode-est": "Mean, Median and Mode",
    "mughal-empire-1526-1857-ad": "Mughal Empire (1526–1857)",
    "delhi-sultanate-1206-1526-ad": "Delhi Sultanate (1206–1526)",
    "gandhian-era-1917-1947": "Gandhian Era (1917–1947)",
    "gupta-dynasty-post-gupta-era": "Gupta and Post-Gupta Era",
    "emerging-technologies-and-industry-4-0": "Emerging Technologies and Industry 4.0",
    "unclassified-miscellaneous-experimental": "Miscellaneous",
}
_ACRONYMS = {"gk": "GK", "gs": "GS", "ssc": "SSC", "hcf": "HCF", "lcm": "LCM", "ms": "MS", "2d": "2D", "3d": "3D",
             "si": "SI", "ci": "CI", "cp": "CP", "sp": "SP", "mp": "MP", "ap": "AP", "gp": "GP", "hp": "HP",
             "bodmas": "BODMAS", "mdh": "MDH", "rbi": "RBI", "dpsp": "DPSP", "unesco": "UNESCO", "dns": "DNS",
             "osi": "OSI", "ram": "RAM", "rom": "ROM"}


def chapter_label(slug: str | None) -> str:
    if not slug:
        return "Unclassified"
    if slug in _CHAPTER_NAMES:
        return _CHAPTER_NAMES[slug]
    small = {"and", "of", "the", "in", "on", "to", "or", "for", "vs"}
    words = slug.replace("_", "-").split("-")
    return " ".join(_ACRONYMS.get(w) or (w if (w in small and i) else w.capitalize())
                    for i, w in enumerate(words) if w)


def _part(name: str, minutes: int, sections: list[tuple[str, int]], correct: float, wrong: float) -> dict:
    return {
        "name": name,
        "minutes": minutes,
        "sections": [
            {"subject": s, "name": SUBJECTS[s], "count": n, "correct": correct, "wrong": wrong}
            for s, n in sections
        ],
    }


_FOUR_BY_25 = [("REAS", 25), ("GK", 25), ("MATH", 25), ("ENG", 25)]

# `marks_pos` picks between two patterns of the same exam: previous-year papers
# carry their own per-question marks, which tells old and new MTS/GD papers apart.
# `legacy` patterns are only used to replay old papers, never for random mocks.
PATTERNS: list[dict[str, Any]] = [
    {"id": "cgl-pre", "exam": "SSC-CGL", "stage": "pre", "name": "SSC CGL Tier-I",
     "parts": [_part("Tier-I", 60, _FOUR_BY_25, 2, 0.5)]},
    {"id": "cgl-mains", "exam": "SSC-CGL", "stage": "mains", "name": "SSC CGL Tier-II (Paper-I)",
     "parts": [
         _part("Section I", 60, [("MATH", 30), ("REAS", 30)], 3, 1),
         _part("Section II", 60, [("ENG", 45), ("GK", 25)], 3, 1),
         _part("Section III", 15, [("COMPUTER", 20)], 3, 1),
     ]},
    {"id": "chsl-pre", "exam": "SSC-CHSL", "stage": "pre", "name": "SSC CHSL Tier-I",
     "parts": [_part("Tier-I", 60, _FOUR_BY_25, 2, 0.5)]},
    {"id": "chsl-mains", "exam": "SSC-CHSL", "stage": "mains", "name": "SSC CHSL Tier-II",
     "parts": [
         _part("Section I", 60, [("MATH", 30), ("REAS", 30)], 3, 1),
         _part("Section II", 60, [("ENG", 40), ("GK", 20)], 3, 1),
         _part("Section III", 15, [("COMPUTER", 15)], 3, 1),
     ]},
    {"id": "cpo-pre", "exam": "SSC-CPO", "stage": "pre", "name": "SSC CPO Paper-I",
     "parts": [_part("Paper-I", 120, [("REAS", 50), ("GK", 50), ("MATH", 50), ("ENG", 50)], 1, 0.25)]},
    {"id": "cpo-mains", "exam": "SSC-CPO", "stage": "mains", "name": "SSC CPO Paper-II",
     "parts": [_part("Paper-II", 120, [("ENG", 200)], 1, 0.25)]},
    {"id": "gd-pre", "exam": "SSC-GD", "stage": "pre", "name": "SSC GD Constable CBE", "marks_pos": 2,
     "parts": [_part("CBE", 60, [("REAS", 20), ("GK", 20), ("MATH", 20), ("ENG", 20)], 2, 0.25)]},
    {"id": "gd-pre-2021", "exam": "SSC-GD", "stage": "pre", "name": "SSC GD Constable CBE (till 2021)",
     "marks_pos": 1, "legacy": True,
     "parts": [_part("CBE", 90, _FOUR_BY_25, 1, 0.25)]},
    {"id": "mts-pre", "exam": "SSC-MTS", "stage": "pre", "name": "SSC MTS CBT", "marks_pos": 3,
     "parts": [
         _part("Session-I", 45, [("MATH", 20), ("REAS", 20)], 3, 0),
         _part("Session-II", 45, [("GK", 25), ("ENG", 25)], 3, 1),
     ]},
    {"id": "mts-pre-2022", "exam": "SSC-MTS", "stage": "pre", "name": "SSC MTS Paper-I (till 2022)",
     "marks_pos": 1, "legacy": True,
     "parts": [_part("Paper-I", 90, [("REAS", 25), ("MATH", 25), ("ENG", 25), ("GK", 25)], 1, 0.25)]},
    {"id": "sp-pre", "exam": "SSC-Selection-Post", "stage": "pre", "name": "SSC Selection Post CBE",
     "parts": [_part("CBE", 60, _FOUR_BY_25, 2, 0.5)]},
    {"id": "steno-pre", "exam": "SSC-Stenographer", "stage": "pre", "name": "SSC Stenographer CBT",
     "parts": [_part("CBT", 120, [("REAS", 50), ("GK", 50), ("ENG", 100)], 1, 0.25)]},
]

PATTERNS_BY_ID = {p["id"]: p for p in PATTERNS}


def pattern_summary(pattern: dict) -> dict:
    sections = [s for part in pattern["parts"] for s in part["sections"]]
    return {
        **pattern,
        "exam_name": EXAMS[pattern["exam"]],
        "stage_name": stage_name(pattern["exam"], pattern["stage"]),
        "legacy": bool(pattern.get("legacy")),
        "minutes": sum(p["minutes"] for p in pattern["parts"]),
        "questions": sum(s["count"] for s in sections),
        "max_marks": sum(s["count"] * s["correct"] for s in sections),
    }


def pattern_for_paper(exam: str, stage: str, marks_pos: float | None) -> dict:
    candidates = [p for p in PATTERNS if p["exam"] == exam and p["stage"] == stage]
    if not candidates:
        raise KeyError(f"no exam pattern for {exam} {stage}")
    for p in candidates:
        if "marks_pos" in p and marks_pos is not None and float(p["marks_pos"]) == float(marks_pos):
            return p
    return next((p for p in candidates if not p.get("legacy")), candidates[0])
