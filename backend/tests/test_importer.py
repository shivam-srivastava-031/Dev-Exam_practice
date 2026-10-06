from app.importer import clean_markup, convert, exam_from_title, parse_held_on
from tests.conftest import make_row

ICON = "https://cdn.repeatermock.com/tb/36f36cd5ece4186095afcf2ffbfb7b80cc04b6c2e3226e7477a419a9fbc96f85.png"
DIAGRAM = "https://cdn.repeatermock.com/tb/4a5b7cf84abd4e09a63d504f404b2035f579e6a3a005e11d54e3ca50bd42a099.png"


def test_clean_markup_strips_badges_but_keeps_diagrams():
    text = f"Answer.\n[IMAGE: {ICON}] **Key Points** [IMAGE: {DIAGRAM}] � step"
    cleaned = clean_markup(text, solution=True)
    assert ICON not in cleaned
    assert DIAGRAM in cleaned
    assert "• step" in cleaned


def test_clean_markup_handles_html_and_truncated_badges():
    assert clean_markup(f'x <img height="26px" src="{ICON}"> y') == "x  y"
    # The source sometimes cuts a solution off in the middle of the badge URL.
    assert clean_markup('done\n<img height="26px" src="https://cdn.repeatermock.com/tb/36f36cd5ece41860') == "done"
    assert clean_markup("[IMAGE: data:image/gif;base64,R0lGOD]tail") == "tail"


def test_parse_held_on_formats():
    assert parse_held_on("2023-10-26") == "2023-10-26"
    assert parse_held_on("12 April 2022") == "2022-04-12"
    assert parse_held_on("5 Jul 2023") == "2023-07-05"
    assert parse_held_on("5 Sept 2023") == "2023-09-05"
    assert parse_held_on("garbage") is None


def test_exam_from_title():
    assert exam_from_title("SSC Stenographer 2025 Official Paper") == "SSC-Stenographer"
    assert exam_from_title("SSC GD Constable 2026 Official Paper") == "SSC-GD"
    assert exam_from_title("Mock paper") is None


def test_convert_maps_label_to_zero_based_answer():
    paper, q = convert(make_row("a", "SSC-CGL", "pre", "MATH", 4, paper="P", title="t", correct="3"))
    assert q["answer"] == 2
    assert q["concept"] is None  # 'unclassified' is dropped
    assert paper["held_on"] == "2023-07-18"


def test_convert_rejects_ambiguous_rows():
    row = make_row("a", "SSC-CGL", "pre", "MATH", 4, paper="P", title="t", correct="4")
    row["options"] = [{"label": "2", "text": "x"}, {"label": "4", "text": "y"}, {"label": "4", "text": "z"}]
    assert convert(row) == "duplicate option labels (answer is ambiguous)"
    row = make_row("a", "SSC-CGL", "pre", "MATH", 4, paper="P", title="t", correct="7")
    assert convert(row) == "answer is not one of the options"


def test_import_moves_misfiled_paper_to_its_titled_exam(client):
    papers = client.get("/api/papers", params={"exam": "SSC-Stenographer"}).json()
    assert [p["id"] for p in papers] == ["S1"]
    assert papers[0]["year"] == 2025
    assert client.get("/api/papers", params={"exam": "SSC-GD"}).json() == []


def test_import_counts(client):
    meta = client.get("/api/meta").json()
    assert meta["total_questions"] == 12 + 8 + 1  # the two malformed rows are skipped
    assert meta["total_papers"] == 4
    cgl = next(e for e in meta["exams"] if e["code"] == "SSC-CGL")
    assert cgl["stages"][0]["subjects"] == {"ENG": 3, "GK": 3, "MATH": 3, "REAS": 3}


def test_prettify_math_rewrites_leftover_latex():
    from app.importer import prettify_math
    assert prettify_math("frac{sqrt{20} b}{2}") == "(√20 b)/2"
    assert prettify_math("If sqrt{x} = (80)/(sqrt{4900})") == "If √x = (80)/(√4900)"
    assert prettify_math("(16sqrt3+24sqrt{35})") == "(16√3+24√35)"
    assert prettify_math("frac{sqrt{1.24} times sqrt{2.79}}{2}") == "(√1.24 × √2.79)/2"
    assert prettify_math("how many times did he run​") == "how many times did he run"
