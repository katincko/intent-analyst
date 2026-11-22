"""E3 and X1: a refused answer says in one sentence how to fix it, markup or a list of sources in
the text is refused, and the same answer refused twice in a row ends the question.

The real case is patient_encounters-01 of the run of 2026-10-28: the model wrote the answer, ended
the text with `</text>` and put the `sources` parameter inside it, so the call had no `sources`.
It sent the same call three times and the question ended in a protocol error. The loop of that run
(prompt v1) is kept for its recordings: its messages are checked unchanged below.
"""

import json
from typing import Any

import duckdb
import pytest
from fake_api import FakeMessagesApi, Reply, message, tool_use

from intent_analyst.agent import Outcome, ask
from intent_analyst.grounding import leaked_markup
from intent_analyst.prompts import get

MODEL = "claude-sonnet-5-5"
PATIENT = {"field": "patient_name", "text": "Paul232 Tremblay80"}
GENDER = {"intent": "count_patients", "params": {"group_by": "gender"}}
LEAKED_TEXT = 'The 5 latest encounters are listed in r2.</text>\n<parameter name="sources">["r2"]'


def run(
    con: duckdb.DuckDBPyConnection, replies: list[dict[str, Any]], version: str | None = None
) -> tuple[Outcome, FakeMessagesApi]:
    api = FakeMessagesApi([Reply(message([block])) for block in replies])
    outcome = ask(
        "Show the 5 latest encounters of Paul232 Tremblay80.",
        client=api.client(),
        con=con,
        model=MODEL,
        clock=api.clock,
        prompt=get(version) if version else None,
    )
    return outcome, api


def result_of(api: FakeMessagesApi, request: int) -> str:
    return str(api.requests[request]["messages"][-1]["content"][0]["content"])


def encounters() -> list[dict[str, Any]]:
    """The first two calls of patient_encounters-01: the id lookup, then the table (r2)."""
    patient = "9661a432-4630-2cd9-142c-386845fae837"
    return [
        tool_use("search_values", PATIENT, "t1"),
        tool_use(
            "run_intent",
            {"intent": "patient_encounters", "params": {"patient_id": patient, "limit": 5}},
            "t2",
        ),
    ]


# ---- the real case ----


def test_patient_encounters_01_gets_one_sentence_and_the_repeat_ends_the_question(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("answer", {"text": LEAKED_TEXT}, "t3")
    outcome, api = run(con, [*encounters(), bad, {**bad, "id": "t4"}, {**bad, "id": "t5"}])
    first = result_of(api, 3)
    assert "sources" in first and '["r2"]' in first and "</text>" in first
    assert first.count(".") <= 3  # one sentence of repair after the validation message
    assert (outcome.status, outcome.stop_reason) == ("stopped", "repeated_answer")
    assert outcome.steps == 4 and len(api.requests) == 4  # the fifth request was never sent
    assert "twice" in outcome.message and "sources" in outcome.message


def test_the_loop_of_the_paid_run_keeps_its_messages_and_does_not_stop_on_a_repeat(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("answer", {"text": LEAKED_TEXT}, "t3")
    repeats = [{**bad, "id": f"t{n}"} for n in range(4, 7)]
    outcome, api = run(con, [*encounters(), bad, *repeats], "v1")
    assert result_of(api, 3) == "Invalid input: sources: Field required"
    assert (outcome.stop_reason, len(api.requests)) == ("tool_errors", 6)


# ---- the hint on a refused answer ----


def test_an_answer_without_sources_is_told_to_cite_the_result_ids(
    con: duckdb.DuckDBPyConnection,
) -> None:
    ok = tool_use("answer", {"text": "27 patients are M and 26 are F.", "sources": ["r1"]})
    outcome, api = run(
        con,
        [
            tool_use("run_intent", GENDER, "t1"),
            tool_use("answer", {"text": "27 patients are M and 26 are F."}, "t2"),
            ok,
        ],
    )
    assert 'for example ["r1"]' in result_of(api, 2)
    assert outcome.status == "answered" and outcome.tool_errors == 1


def test_empty_sources_get_the_same_sentence(con: duckdb.DuckDBPyConnection) -> None:
    empty = tool_use("answer", {"text": "27 patients are M.", "sources": []})
    _, api = run(con, [tool_use("run_intent", GENDER, "t1"), empty])
    assert 'for example ["r1"]' in result_of(api, 2)


def test_before_any_table_the_sentence_says_to_run_an_intent_first(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _, api = run(con, [tool_use("answer", {"text": "Nothing yet."}, "t1")])
    assert "Run an intent first" in result_of(api, 1)


def test_a_different_second_answer_is_not_a_repeat(con: duckdb.DuckDBPyConnection) -> None:
    outcome, _ = run(
        con,
        [
            tool_use("run_intent", GENDER, "t1"),
            tool_use("answer", {"text": "There are 53 patients."}, "t2"),
            tool_use("answer", {"text": "There are 99 patients."}, "t3"),
            tool_use("answer", {"text": "27 patients are M.", "sources": ["r1"]}, "t4"),
        ],
    )
    assert outcome.status == "answered" and outcome.tool_errors == 2


def test_another_call_between_two_equal_refusals_resets_the_repeat(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("answer", {"text": "There are 53 patients.", "sources": ["r1"]}, "t2")
    failing = tool_use("run_intent", {"intent": "no_such_intent", "params": {}}, "t3")
    outcome, _ = run(
        con,
        [
            tool_use("run_intent", GENDER, "t1"),
            bad,
            failing,
            {**bad, "id": "t4"},
            tool_use("answer", {"text": "27 patients are M.", "sources": ["r1"]}, "t5"),
        ],
    )
    assert outcome.status == "answered" and outcome.tool_errors == 3


def test_the_same_numbers_refusal_twice_in_a_row_ends_the_question(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("answer", {"text": "There are 53 patients.", "sources": ["r1"]}, "t2")
    outcome, api = run(con, [tool_use("run_intent", GENDER, "t1"), bad, {**bad, "id": "t3"}])
    assert outcome.stop_reason == "repeated_answer" and len(api.requests) == 3
    assert "['53']" in outcome.message  # the reason of the last refusal is the stop reason


# ---- X1: markup and the list of sources in the text ----


@pytest.mark.parametrize(
    "text",
    [
        "27 patients are M.</text>",
        '27 patients are M.\n<parameter name="sources">["r1"]</parameter>',
        "27 patients are M. Sources: r1",
        'sources = ["r1"]',
        "27 patients are M. [r1]",
        '27 patients are M. <invoke name="answer">',
    ],
)
def test_a_text_with_tool_markup_or_the_list_of_sources_is_not_accepted(
    con: duckdb.DuckDBPyConnection, text: str
) -> None:
    bad = tool_use("answer", {"text": text, "sources": ["r1"]}, "t2")
    fixed = tool_use("answer", {"text": "27 patients are M.", "sources": ["r1"]}, "t3")
    outcome, api = run(con, [tool_use("run_intent", GENDER, "t1"), bad, fixed])
    refusal = result_of(api, 2)
    assert "only the answer" in refusal and "sources" in refusal
    assert outcome.status == "answered" and outcome.message == "27 patients are M."


def test_the_paid_run_loop_accepts_the_markup_as_before(con: duckdb.DuckDBPyConnection) -> None:
    bad = tool_use("answer", {"text": "27 patients are M.</text>", "sources": ["r1"]}, "t2")
    outcome, _ = run(con, [tool_use("run_intent", GENDER, "t1"), bad], "v1")
    assert outcome.status == "answered"


def test_the_leak_is_checked_before_the_numbers(con: duckdb.DuckDBPyConnection) -> None:
    bad = tool_use("answer", {"text": '27 are M. ["r1"]</text>', "sources": ["r1"]}, "t2")
    _, api = run(con, [tool_use("run_intent", GENDER, "t1"), bad])
    assert "Numbers not found" not in result_of(api, 2)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("done</text>", "</text>"),
        ('<parameter name="sources">', '<parameter name="sources">'),
        ("Sources: r1, r2", "Sources:"),
        ('["r2"]', '["r2'),
    ],
)
def test_leaked_markup_names_the_fragment(text: str, found: str) -> None:
    assert leaked_markup(text) == found


@pytest.mark.parametrize(
    "text",
    [
        "Age < 5 and > 3 years.",
        "NDA021457 200 ACTUAT albuterol 0.09 MG/ACTUAT Metered Dose Inhaler [ProAir]",
        "Resources: staff, rooms and the sources of the data are described in words.",
        "a < b and b > c",
        "",
    ],
)
def test_ordinary_text_is_not_markup(text: str) -> None:
    assert leaked_markup(text) is None


def test_no_text_cell_of_the_database_looks_like_markup(con: duckdb.DuckDBPyConnection) -> None:
    """A model that quotes a real cell must never be refused for it."""
    tables = ("patients", "encounters", "conditions", "medications", "procedures")
    for table in tables:
        columns = [c[0] for c in con.execute(f"DESCRIBE {table}").fetchall()]  # noqa: S608
        for column in columns:
            for (value,) in con.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall():  # noqa: S608
                assert leaked_markup(str(value)) is None, (table, column, value)


def test_what_the_loop_sends_back_is_json_the_model_can_read(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("answer", {"text": LEAKED_TEXT}, "t3")
    _, api = run(con, [*encounters(), bad])
    sent = api.requests[3]["messages"][-1]["content"][0]
    assert sent["is_error"] is True and json.dumps(sent)
