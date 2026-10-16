"""Test 5 of the spec: an answer with a number that is not in a returned table is rejected.

First the check on its own, then through the loop with a fake Messages API (see fake_api.py for
the response format and its sources).
"""

import json
from datetime import date
from typing import Any

import duckdb
import pytest
from fake_api import FakeMessagesApi, Reply, message, tool_use

from intent_analyst.agent import MAX_TOOL_ERRORS, Outcome, ask
from intent_analyst.grounding import numbers_in, ungrounded_numbers

# Rows as a tool returns them: gender, patients, percent.
TABLE = [["M", 27, 50.9], ["F", 26, 49.1]]


def missing(text: str, question: str = "", table: list[list[Any]] | None = None) -> list[str]:
    return ungrounded_numbers(text, [table if table is not None else TABLE], question)


@pytest.mark.parametrize(
    "text",
    [
        "27 of the patients are M.",
        "M has 27 patients, 50.9 percent.",
        "50.9% are M and 49.1% are F.",
        "27.0 patients",  # equal value, other spelling
        "No patient matched.",  # no number at all
        "-",  # a dash is not a number
    ],
)
def test_numbers_from_the_table_pass(text: str) -> None:
    assert missing(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("There are 53 patients.", ["53"]),  # a sum the table does not have
        ("About 51% are M.", ["51%"]),  # a rounding
        ("27 are M and 28 are F.", ["28"]),
        ("There were 1,234 patients.", ["1,234"]),
        ("The change is -3.", ["-3"]),
    ],
)
def test_numbers_not_in_the_table_are_found(text: str, expected: list[str]) -> None:
    assert missing(text) == expected


def test_thousands_separator_and_decimals_are_read_as_values() -> None:
    assert numbers_in("1,234.5 and 7") == [1234.5, 7]
    assert missing("It cost 1,234.5", table=[[1234.5]]) == []


def test_a_number_from_the_question_may_be_repeated() -> None:
    assert missing("The 5 most common: M.", question="Show the 5 most common") == []
    assert missing("The 6 most common: M.", question="Show the 5 most common") == ["6"]


def test_a_verbatim_quote_of_a_text_cell_is_not_checked_for_numbers() -> None:
    table = [["albuterol 5 MG/ML Inhalation Solution", 44]]
    assert missing("albuterol 5 MG/ML Inhalation Solution was given 44 times.", table=table) == []


def test_digits_inside_a_quote_cannot_be_borrowed_by_another_claim() -> None:
    table = [["albuterol 5 MG/ML Inhalation Solution", 44]]
    assert missing("5 patients took albuterol 5 MG/ML Inhalation Solution.", table=table) == ["5"]


def test_a_year_or_date_cell_can_be_quoted() -> None:
    table = [["2020", 12], [date(2019, 10, 8), 3]]
    assert missing("In 2020 there were 12 encounters, and 3 on 2019-10-08.", table=table) == []
    assert missing("In 2021 there were 12 encounters.", table=table) == ["2021"]


def test_a_text_cell_does_not_make_its_digits_valid_numbers() -> None:
    table = [["Dose 250 MG", 1]]
    assert missing("There were 250 doses.", table=table) == ["250"]


def test_a_boolean_cell_is_not_a_number() -> None:
    assert missing("It is 1.", table=[[True]]) == ["1"]


# ---- through the loop ----

GENDER = {"intent": "count_patients", "params": {"group_by": "gender"}}


def run(con: duckdb.DuckDBPyConnection, api: FakeMessagesApi, question: str = "Q?") -> Outcome:
    return ask(question, client=api.client(), con=con, model="claude-sonnet-5-5", clock=api.clock)


def answer(text: str, sources: list[str] | None = None) -> dict[str, Any]:
    return tool_use("answer", {"text": text, "sources": sources or ["r1"]})


def test_an_answer_with_an_invented_number_is_rejected_and_can_be_fixed(
    con: duckdb.DuckDBPyConnection,
) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("run_intent", GENDER, "t1")])),
            Reply(message([answer("There are 53 patients, 27 of them M.", ["r1"])])),
            Reply(message([answer("27 patients are M and 26 are F.", ["r1"])])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "answered"
    assert outcome.message == "27 patients are M and 26 are F."
    assert outcome.tool_errors == 1
    rejection = api.requests[2]["messages"][-1]["content"][0]
    assert rejection["is_error"] is True
    assert "['53']" in rejection["content"]  # names the offending number only


def test_an_answer_that_never_gets_grounded_ends_the_question(
    con: duckdb.DuckDBPyConnection,
) -> None:
    script = [Reply(message([tool_use("run_intent", GENDER, "t1")]))]
    script += [
        Reply(message([answer("There are 53 patients.")])) for _ in range(MAX_TOOL_ERRORS + 1)
    ]
    api = FakeMessagesApi(script)
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "tool_errors")
    assert outcome.tables == []  # nothing ungrounded is handed back as an answer
    assert outcome.tool_errors == MAX_TOOL_ERRORS + 1


def test_only_the_cited_tables_count(con: duckdb.DuckDBPyConnection) -> None:
    ages = {"intent": "patients_by_age_band", "params": {"band_years": 20}}
    api = FakeMessagesApi(
        [
            Reply(
                message([tool_use("run_intent", GENDER, "t1"), tool_use("run_intent", ages, "t2")])
            ),
            Reply(message([answer("27 patients are M.", ["r2"])])),  # 27 is only in r1
            Reply(message([answer("27 patients are M.", ["r1"])])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "answered"
    assert outcome.tool_errors == 1
    assert [t.id for t in outcome.tables] == ["r1"]


def test_an_unknown_source_is_rejected(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("run_intent", GENDER, "t1")])),
            Reply(message([answer("27 patients are M.", ["r9"])])),
            Reply(message([answer("27 patients are M.", ["r1"])])),
        ]
    )
    outcome = run(con, api)
    rejection = api.requests[2]["messages"][-1]["content"][0]
    assert "r9" in rejection["content"] and "r1" in rejection["content"]
    assert outcome.status == "answered"


def test_the_answer_carries_the_tables_the_server_ran(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("run_intent", GENDER, "t1")])),
            Reply(message([answer("27 patients are M and 26 are F.")])),
        ]
    )
    (table,) = run(con, api).tables
    assert table.rows == [["M", 27], ["F", 26]]
    assert table.sql is not None and "GROUP BY" in table.sql
    assert json.loads(api.requests[1]["messages"][-1]["content"][0]["content"])["rows"] == [
        ["M", 27],
        ["F", 26],
    ]
