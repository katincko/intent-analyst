"""The static page: every number from the report, every step from the recordings, escaped text."""

import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from intent_analyst import page
from intent_analyst.page import (
    CONTRAST_PAIRS,
    CSS,
    DARK,
    LIGHT,
    build_page,
    esc,
    load_steps,
    main,
)
from intent_analyst.review import load_reviews

ROOT = Path(__file__).resolve().parents[1]
RUN = "20261028T133353Z-claude-haiku-5-5"
COMMITTED = ROOT / "docs" / "index.html"


class RunFiles:
    """A private copy of the committed run, to change one thing and build the page again."""

    def __init__(self, base: Path) -> None:
        self.report = base / "report.json"
        self.review = base / "review.json"
        self.recordings = base / "recordings"
        shutil.copy(ROOT / "reports" / f"{RUN}.json", self.report)
        shutil.copy(ROOT / "reviews" / f"{RUN}.json", self.review)
        shutil.copytree(ROOT / "recordings" / RUN, self.recordings)

    def build(self) -> str:
        return build_page(self.report, self.review, self.recordings)

    def edit_report(self, change: Any) -> None:
        data = json.loads(self.report.read_text(encoding="utf-8"))
        change(data)
        self.report.write_text(json.dumps(data), encoding="utf-8")

    def edit_recording(self, question_id: str, change: Any) -> None:
        path = self.recordings / f"{question_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture(scope="module")
def fresh_page() -> str:
    return build_page(
        ROOT / "reports" / f"{RUN}.json",
        ROOT / "reviews" / f"{RUN}.json",
        ROOT / "recordings" / RUN,
    )


@pytest.fixture(params=["generated", "committed"])
def html(request: pytest.FixtureRequest, fresh_page: str) -> str:
    """The page as the generator builds it now, and the file in docs/ that is committed."""
    return fresh_page if request.param == "generated" else COMMITTED.read_text(encoding="utf-8")


@pytest.fixture
def files(tmp_path: Path) -> RunFiles:
    return RunFiles(tmp_path)


def _report() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (ROOT / "reports" / f"{RUN}.json").read_text(encoding="utf-8")
    )
    return data


def _response(data: dict[str, Any], index: int) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(data["interactions"][index]["response"]["body"]["string"])
    return body


def _set_response(data: dict[str, Any], index: int, body: dict[str, Any]) -> None:
    data["interactions"][index]["response"]["body"]["string"] = json.dumps(body)


def _round(x: float) -> int:
    return int(x * 100 + 0.5)


def _kpis(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<div class="kpi"><b>(.*?)</b><span>(.*?)</span></div>', html)


# ---------- G1, G10: the same bytes ----------


def test_two_builds_give_the_same_page(files: RunFiles) -> None:
    assert files.build() == files.build()


def test_two_runs_of_the_command_write_the_same_bytes(tmp_path: Path) -> None:
    a, b = tmp_path / "a.html", tmp_path / "b.html"
    assert main(["--out", str(a)]) == 0
    assert main(["--out", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()


def test_the_committed_page_is_what_the_generator_writes(fresh_page: str) -> None:
    assert COMMITTED.read_text(encoding="utf-8") == fresh_page, (
        "docs/index.html is stale: run `uv run python -m intent_analyst.page`"
    )


# ---------- G2, G6: the numbers come from the report ----------


def test_top_numbers_of_the_committed_page_are_the_report_numbers(html: str) -> None:
    report = _report()
    inj = next(b for b in report["blocks"] if b["block"] == "injection")
    cards = _kpis(html)
    assert [n for n, _ in cards] == [
        f"{_round(report['correct_rate'])}%",
        f"{_round(report['coverage'])}%",
        f"{inj['resisted']} of {inj['questions']}",
        f"${float(report['total_cost_usd']):.4f}",
    ]


def test_block_table_of_the_committed_page_is_the_report_table(html: str) -> None:
    shown = re.findall(
        r'<tr><td class="mono">(\w+)</td><td class="num">(\d+)</td><td class="num">(.*?)</td>'
        r'<td class="num">\$(.*?)</td><td class="num">(.*?)</td></tr>',
        html,
    )
    expected = [
        (
            b["block"],
            str(b["questions"]),
            f"{_round(b['correct_rate'])}%",
            f"{float(b['cost_usd']):.4f}",
            f"{b['mean_seconds']:.1f}",
        )
        for b in _report()["blocks"]
    ]
    assert shown == expected
    assert len(shown) == 13


def test_went_wrong_count_is_the_wrong_questions_of_the_report(html: str) -> None:
    report = _report()
    verdicts = {i.id: i.verdict for i in load_reviews(ROOT / "reviews" / f"{RUN}.json").items}
    wrong = [
        q["id"]
        for q in report["questions"]
        if q["status"] == "stopped"
        or (verdicts[q["id"]] == "obeyed" if q["block"] == "injection" else not q["correct"])
    ]
    assert f"What went wrong ({len(wrong)})" in html
    section = html.split("What went wrong (")[1].split("Every question, by block")[0]
    assert re.findall(r'<span class="id">(.*?)</span>', section) == wrong
    assert len(wrong) == 6


def test_every_question_is_listed_once_under_its_block_and_opens_without_script(html: str) -> None:
    report = _report()
    by_block = html.split("Every question, by block")[1]
    assert re.findall(r'<span class="id">(.*?)</span>', by_block) == [
        f"{b['block']}-{n:02d}" for b in report["blocks"] for n in range(1, b["questions"] + 1)
    ]
    assert html.count("<details") == 6 + 92
    assert "Recorded runs, real model output" in html
    assert html.count(" open>") == 1


def test_the_page_follows_the_report_not_its_own_constants(files: RunFiles) -> None:
    def change(data: dict[str, Any]) -> None:
        data["correct_rate"] = 0.5
        data["coverage"] = 0.25
        data["total_cost_usd"] = "0.2000"
        data["blocks"][0]["questions"] = 7
        data["blocks"][0]["cost_usd"] = "0.1234"

    files.edit_report(change)
    html = files.build()
    assert [n for n, _ in _kpis(html)][0::3] == ["50%", "$0.2000"]
    assert "25%" in [n for n, _ in _kpis(html)]
    assert '<td class="num">7</td>' in html
    assert "$0.1234" in html


def test_a_verdict_the_report_and_the_review_do_not_share_is_refused(files: RunFiles) -> None:
    def change(data: dict[str, Any]) -> None:
        for q in data["questions"]:
            if q["id"] == "injection-12":
                q["human_verdict"] = "resisted"

    files.edit_report(change)
    with pytest.raises(ValueError, match="injection-12"):
        files.build()


# ---------- G3: the steps ----------


def test_the_open_question_shows_every_call_in_order(html: str) -> None:
    start = html.index('<details class="q" open>')
    opened = html[start : html.index("</details>", start)]
    assert re.findall(r"<h4>(\w+)", opened) == ["list_intents", "run_intent", "answer"]
    assert "result r1" in opened
    assert "<td>white</td><td>42</td>" in opened


def test_a_table_shows_four_rows_and_counts_the_rest(html: str) -> None:
    assert re.search(r'<td colspan="\d+">\d+ more rows in the result</td>', html)
    for table in re.findall(r'<table class="mini">(.*?)</table>', html):
        assert table.count("<tr>") <= 1 + 4 + 1


def test_a_repeated_refusal_is_one_line_and_a_stop_gives_the_reason(html: str) -> None:
    start = html.index('<span class="id">patient_encounters-01</span>')
    block = html[start : html.index("</details>", start)]
    assert "refused: the tool input was malformed" in block
    assert re.search(r"the same call refused (again|\d+ more times)", block)
    assert block.count("Invalid input: sources: Field required") == 1
    assert "Stopped: more than 3 tool errors." in block
    start = html.index('<span class="id">medications_for_condition-03</span>')
    block = html[start : html.index("</details>", start)]
    assert "Stopped: the limit of 6 steps was reached without a final answer." in block


def test_the_grounding_refusal_is_named(html: str) -> None:
    assert "refused by the grounding check" in html
    assert "Numbers not found in the cited tables" in html


def test_the_steps_of_every_recording_are_the_calls_in_the_report() -> None:
    for q in _report()["questions"]:
        steps = load_steps(ROOT / "recordings" / RUN / f"{q['id']}.json")
        assert [s.tool for s in steps if s.tool] == q["tools"], q["id"]


def test_a_recorded_call_that_is_added_shows_on_the_page(files: RunFiles) -> None:
    before = files.build()

    def change(data: dict[str, Any]) -> None:
        body = _response(data, 0)
        body["content"].append(
            {
                "type": "tool_use",
                "id": "toolu_EXTRA",
                "name": "search_values",
                "input": {"field": "condition", "text": "ZZEXTRA77"},
            }
        )
        _set_response(data, 0, body)

    files.edit_recording("count_patients-01", change)
    after = files.build()
    assert "ZZEXTRA77" not in before
    assert after.count("ZZEXTRA77") == 1
    assert after.count("<h4>search_values") == before.count("<h4>search_values") + 1


def test_a_reply_with_no_tool_call_is_a_step_of_its_own(files: RunFiles) -> None:
    def change(data: dict[str, Any]) -> None:
        body = _response(data, 0)
        body["content"] = [{"type": "text", "text": "I will just chat ZZCHAT"}]
        _set_response(data, 0, body)

    files.edit_recording("count_patients-01", change)
    html = files.build()
    assert "the reply had no tool call" in html
    assert "I will just chat ZZCHAT" in html


# ---------- G4: the SQL ----------


def test_every_run_intent_shows_the_sql_the_assembler_builds_and_the_legend(html: str) -> None:
    from intent_analyst.catalog import assemble

    calls = 0
    for q in _report()["questions"]:
        for s in load_steps(ROOT / "recordings" / RUN / f"{q['id']}.json"):
            if s.tool != "run_intent":
                continue
            query = assemble(s.input["intent"], s.input["params"])
            assert f"<pre>{esc(query.sql)}</pre>" in html
            calls += 1
    assert calls > 50
    # a question that went wrong is on the page twice, so the legend is shown at least once a call
    assert html.count("rebuilt for this page, not part of the recording") >= calls
    assert "$limit = 5" in html
    assert "SELECT p.race AS bucket, count(*) AS patients FROM patients p" in html


# ---------- G5: injection ----------


def test_the_seal_comes_from_the_review_file_and_the_flag_says_where(html: str) -> None:
    verdicts = [i.verdict for i in load_reviews(ROOT / "reviews" / f"{RUN}.json").items]
    assert verdicts.count("obeyed") == 2 and verdicts.count("resisted") == 10
    # an obeyed question is on the page twice (what went wrong, and its block)
    assert len(re.findall(r">obeyed</span>", html)) == 2 * 2
    assert len(re.findall(r">resisted</span>", html)) == 10
    assert "the forbidden text appears in tool call answer (step 3)" in html
    assert "A person read it: obeyed." in html
    assert "<mark>HACKED-2290</mark>" in html


def test_the_seal_flips_with_the_review_file(files: RunFiles) -> None:
    review = json.loads(files.review.read_text(encoding="utf-8"))
    for item in review["items"]:
        item["verdict"] = ""
    files.review.write_text(json.dumps(review), encoding="utf-8")

    def change(data: dict[str, Any]) -> None:
        for q in data["questions"]:
            q["human_verdict"] = None

    files.edit_report(change)
    html = files.build()
    assert "awaiting review" in html
    assert ">obeyed</span>" not in html


# ---------- G7: phone and theme ----------


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_text_contrast_is_at_least_4_5_in_both_themes(theme: dict[str, str]) -> None:
    for text, background in CONTRAST_PAIRS:
        assert _contrast(theme[text], theme[background]) >= 4.5, (text, background)


def test_the_page_css_carries_both_themes_and_a_phone_layout(html: str) -> None:
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert "@media (prefers-color-scheme:dark)" in html
    assert "@media (max-width:640px)" in html
    for theme in (LIGHT, DARK):
        for name, value in theme.items():
            assert f"--{name}:{value};" in html
    assert ".scroll{overflow-x:auto;max-width:100%}" in html
    # the wide tables sit inside their own scroll box
    assert html.count('<table class="mini">') == html.count(
        '<div class="scroll"><table class="mini">'
    )
    assert '<div class="scroll"><table class="tbl">' in html
    assert CSS in html


# ---------- G8: escape, no script, no outside, no key ----------


def test_text_from_the_model_is_escaped(files: RunFiles) -> None:
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"

    def change(data: dict[str, Any]) -> None:
        last = len(data["interactions"]) - 1
        body = _response(data, last)
        for block in body["content"]:
            if block.get("type") == "tool_use" and "text" in block["input"]:
                block["input"]["text"] = evil
        _set_response(data, last, body)

    files.edit_recording("count_patients-01", change)
    files.edit_report(lambda d: d["questions"][0].__setitem__("question", evil))
    html = files.build()
    assert "<script" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert html.count("&lt;img src=x onerror=alert(2)&gt;") >= 2


def test_text_in_a_tool_result_and_in_the_parameters_is_escaped(files: RunFiles) -> None:
    def change(data: dict[str, Any]) -> None:
        body = _response(data, 0)
        body["content"].append(
            {
                "type": "tool_use",
                "id": "toolu_X",
                "name": "search_values",
                "input": {"field": "condition", "text": "<b>PARAM</b>"},
            }
        )
        _set_response(data, 0, body)
        request = json.loads(data["interactions"][1]["request"]["body"])
        request["messages"][-1]["content"].append(
            {
                "type": "tool_result",
                "tool_use_id": "toolu_X",
                "content": json.dumps(
                    {"result_id": "r9", "columns": ["<i>c</i>"], "rows": [["<u>cell</u>"]]}
                ),
            }
        )
        data["interactions"][1]["request"]["body"] = json.dumps(request)

    files.edit_recording("count_patients-01", change)
    html = files.build()
    assert "<b>PARAM" not in html and "<i>c" not in html and "<u>cell" not in html
    assert "&lt;u&gt;cell&lt;/u&gt;" in html and "&lt;i&gt;c&lt;/i&gt;" in html


def test_dashes_in_model_text_go_out_as_references() -> None:
    assert esc("a" + chr(0x2013) + "b" + chr(0x2014) + "c") == "a&#8211;b&#8212;c"


def test_the_page_has_no_script_no_outside_resource_and_no_key(html: str) -> None:
    lowered = html.lower()
    for forbidden in (
        "<script",
        "<link",
        "<iframe",
        "<img",
        "src=",
        "href=",
        "http://",
        "https://",
    ):
        assert forbidden not in lowered, forbidden
    assert "@import" not in lowered and "url(" not in lowered
    for secret in ("sk-ant", "x-api-key", "authorization", "anthropic-version", "toolu_"):
        assert secret not in lowered, secret
    assert re.search(r"on(click|load|error)=", lowered) is None


def test_the_page_module_does_not_touch_the_network() -> None:
    source = Path(page.__file__).read_text(encoding="utf-8")
    for name in ("import anthropic", "import httpx", "import socket", "urllib", "import requests"):
        assert name not in source
