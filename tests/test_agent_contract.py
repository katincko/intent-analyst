"""Test 1 of the spec: the tool-use contract, against a fake Messages API in memory.

The fake (`tests/fake_api.py`) returns responses in the real Message format and rejects, like the
real API, any request that breaks the tool_use / tool_result rules. Formats and rules come from
https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
and https://platform.claude.com/docs/en/api/messages/create
"""

import json
from typing import Any

import anthropic
import duckdb
import httpx2
import pytest
from fake_api import FakeMessagesApi, Reply, contract_problem, message, text, tool_use

from intent_analyst.agent import Outcome, ask
from intent_analyst.catalog import INTENTS

GENDER = {"intent": "count_patients", "params": {"group_by": "gender"}}
TOTAL = "There are 27 male and 26 female patients."


def run(con: duckdb.DuckDBPyConnection, api: FakeMessagesApi, question: str = "Q?") -> Outcome:
    return ask(question, client=api.client(), con=con, model="claude-sonnet-5-5", clock=api.clock)


def answer_call(text_: str, sources: list[str]) -> dict[str, Any]:
    return tool_use("answer", {"text": text_, "sources": sources})


def results_of(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool_result blocks of the last message of a request."""
    last = request["messages"][-1]
    assert last["role"] == "user"
    return [b for b in last["content"] if b["type"] == "tool_result"]


def test_the_fake_rejects_a_broken_contract() -> None:
    """Guards the fake itself: without this the contract tests below could pass vacuously."""
    use = tool_use("list_intents", {}, id="toolu_a")
    head = [{"role": "user", "content": "Q?"}, {"role": "assistant", "content": [use]}]
    result = {"type": "tool_result", "tool_use_id": "toolu_a", "content": "ok"}
    note = {"type": "text", "text": "x"}
    assert contract_problem({"messages": [*head, {"role": "user", "content": [result]}]}) is None
    assert contract_problem({"messages": [*head, {"role": "user", "content": [note]}]})
    assert contract_problem({"messages": [*head, {"role": "user", "content": [note, result]}]})
    assert (
        contract_problem({"messages": [*head, {"role": "user", "content": [result, note]}]}) is None
    )
    assert contract_problem({"messages": head})
    wrong_id = {**result, "tool_use_id": "toolu_zzz"}
    assert contract_problem({"messages": [*head, {"role": "user", "content": [wrong_id]}]})
    api = FakeMessagesApi([])
    with pytest.raises(anthropic.BadRequestError):
        api.client().messages.create(
            model="claude-sonnet-5-5",
            max_tokens=10,
            messages=[*head, {"role": "user", "content": [note]}],  # type: ignore[list-item]
        )


def test_one_tool_use_gets_one_tool_result_with_the_same_id(
    con: duckdb.DuckDBPyConnection,
) -> None:
    use = tool_use("run_intent", GENDER, id="toolu_one")
    api = FakeMessagesApi(
        [
            Reply(message([text("Counting."), use])),
            Reply(message([answer_call(TOTAL, ["r1"])])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "answered"
    assert len(api.requests) == 2
    (result,) = results_of(api.requests[1])
    assert result["tool_use_id"] == "toolu_one"
    assert "is_error" not in result
    # the assistant turn is sent back whole, tool_use block included
    sent = api.requests[1]["messages"][1]
    assert sent["role"] == "assistant"
    assert [b["id"] for b in sent["content"] if b["type"] == "tool_use"] == ["toolu_one"]
    table = json.loads(result["content"])
    assert table["result_id"] == "r1"
    assert sum(row[1] for row in table["rows"]) == 53


def test_several_tool_uses_in_one_turn_get_all_results_in_one_message(
    con: duckdb.DuckDBPyConnection,
) -> None:
    calls = [
        tool_use("run_intent", GENDER, id="toolu_a"),
        tool_use("search_values", {"field": "condition", "text": "sinus"}, id="toolu_b"),
        tool_use("run_intent", {"intent": "patients_by_age_band"}, id="toolu_c"),
    ]
    api = FakeMessagesApi([Reply(message(calls)), Reply(message([answer_call(TOTAL, ["r1"])]))])
    outcome = run(con, api)
    assert outcome.status == "answered"
    results = results_of(api.requests[1])
    assert [r["tool_use_id"] for r in results] == ["toolu_a", "toolu_b", "toolu_c"]
    assert [json.loads(r["content"])["result_id"] for r in results] == ["r1", "r2", "r3"]
    last = api.requests[1]["messages"][-1]["content"]
    assert [b["type"] for b in last] == ["tool_result"] * 3  # one message, results only


def test_each_tool_result_carries_the_id_of_the_call_that_made_it(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The fake only compares the set of ids, so the pairing is checked here, by content."""
    calls = [
        tool_use("run_intent", GENDER, id="toolu_table"),
        tool_use("search_values", {"field": "condition", "text": "sinus"}, id="toolu_search"),
        tool_use("list_intents", {}, id="toolu_catalog"),
        tool_use("run_intent", {"intent": "no_such_intent"}, id="toolu_bad"),
    ]
    api = FakeMessagesApi([Reply(message(calls)), Reply(message([answer_call(TOTAL, ["r1"])]))])
    assert run(con, api).status == "answered"
    by_id = {r["tool_use_id"]: r for r in results_of(api.requests[1])}
    assert list(by_id) == ["toolu_table", "toolu_search", "toolu_catalog", "toolu_bad"]
    table = json.loads(by_id["toolu_table"]["content"])
    assert (table["result_id"], table["columns"][0]) == ("r1", "bucket")
    search = json.loads(by_id["toolu_search"]["content"])
    assert search["result_id"] == "r2"
    assert search["columns"] != table["columns"]
    catalog = json.loads(by_id["toolu_catalog"]["content"])
    assert [i["name"] for i in catalog] == list(INTENTS)
    assert by_id["toolu_bad"]["is_error"] is True
    assert "no_such_intent" in by_id["toolu_bad"]["content"]
    assert all("is_error" not in by_id[i] for i in ("toolu_table", "toolu_search", "toolu_catalog"))


def test_a_cut_turn_with_several_tool_uses_answers_each_one_by_its_own_id(
    con: duckdb.DuckDBPyConnection,
) -> None:
    cut = message(
        [
            text("Let me look."),
            tool_use("run_intent", GENDER, id="toolu_cut_a"),
            tool_use("list_intents", {}, id="toolu_cut_b"),
            tool_use("run_intent", GENDER, id="toolu_cut_c"),
        ],
        stop_reason="max_tokens",
    )
    api = FakeMessagesApi([Reply(cut), Reply(message([tool_use("refuse", {"reason": "No."})]))])
    assert run(con, api).status == "refused"
    results = results_of(api.requests[1])
    assert [r["tool_use_id"] for r in results] == ["toolu_cut_a", "toolu_cut_b", "toolu_cut_c"]
    assert all(r["is_error"] is True and "cut off" in r["content"] for r in results)


def test_a_tool_error_is_returned_as_an_error_result_and_the_loop_continues(
    con: duckdb.DuckDBPyConnection,
) -> None:
    bad = tool_use("run_intent", {"intent": "count_patients", "params": {"group_by": "ssn"}}, "t1")
    api = FakeMessagesApi(
        [
            Reply(message([bad])),
            Reply(message([tool_use("run_intent", GENDER, "t2")])),
            Reply(message([answer_call(TOTAL, ["r1"])])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "answered"
    assert outcome.tool_errors == 1
    (result,) = results_of(api.requests[1])
    assert result["tool_use_id"] == "t1"
    assert result["is_error"] is True
    assert "group_by" in result["content"]  # says what to fix
    assert [e.is_error for e in outcome.events] == [True, False, False]


def test_an_unknown_tool_name_gets_an_error_result(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("run_sql", {"sql": "DROP TABLE patients"}, "t1")])),
            Reply(message([tool_use("refuse", {"reason": "No SQL here."}, "t2")])),
        ]
    )
    outcome = run(con, api)
    (result,) = results_of(api.requests[1])
    assert (result["tool_use_id"], result["is_error"]) == ("t1", True)
    assert outcome.status == "refused"
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)


def test_max_tokens_with_a_tool_use_still_gets_its_result_and_one_retry(
    con: duckdb.DuckDBPyConnection,
) -> None:
    cut = message(
        [text("Let me look."), tool_use("run_intent", GENDER, "toolu_cut")],
        stop_reason="max_tokens",
    )
    api = FakeMessagesApi(
        [
            Reply(cut),
            Reply(message([tool_use("run_intent", GENDER, "t2")])),
            Reply(message([answer_call(TOTAL, ["r1"])])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "answered"  # r1 exists, so the cut call was not run
    assert outcome.protocol_failures == 1
    (result,) = results_of(api.requests[1])
    assert (result["tool_use_id"], result["is_error"]) == ("toolu_cut", True)
    assert "cut off" in result["content"]


def test_max_tokens_without_a_tool_use_gets_a_text_nudge(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([text("A very long thought")], stop_reason="max_tokens")),
            Reply(message([tool_use("refuse", {"reason": "Out of scope."})])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "refused"
    assert outcome.protocol_failures == 1
    nudge = api.requests[1]["messages"][-1]
    assert nudge["role"] == "user"
    assert "cut off" in nudge["content"][0]["text"]


def test_a_reply_without_a_tool_is_a_protocol_failure_with_one_new_attempt(
    con: duckdb.DuckDBPyConnection,
) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([text("There are 53 patients.")])),
            Reply(message([tool_use("refuse", {"reason": "Out of scope."})])),
        ]
    )
    outcome = run(con, api)
    assert outcome.status == "refused"
    assert outcome.protocol_failures == 1
    assert "Reply only by calling a tool" in api.requests[1]["messages"][-1]["content"][0]["text"]


def test_a_second_reply_without_a_tool_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([Reply(message([text("53.")])), Reply(message([text("Still 53.")]))])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "protocol")
    assert outcome.protocol_failures == 2
    assert "twice" in outcome.message
    assert len(api.requests) == 2


def test_a_terminal_tool_beside_another_tool_is_not_run(con: duckdb.DuckDBPyConnection) -> None:
    both = [tool_use("run_intent", GENDER, "t1"), answer_call("53 patients.", ["r1"])]
    api = FakeMessagesApi([Reply(message(both)), Reply(message([answer_call(TOTAL, ["r1"])]))])
    outcome = run(con, api)
    first, second = results_of(api.requests[1])
    assert "is_error" not in first
    assert second["is_error"] is True
    assert "alone" in second["content"]
    assert outcome.status == "answered"


def test_the_request_has_the_expected_shape(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([Reply(message([tool_use("refuse", {"reason": "No."})]))])
    run(con, api, "Show me the SSNs")
    (body,) = api.requests
    assert body["model"] == "claude-sonnet-5-5"
    assert "tool_choice" not in body  # automatic mode: current models refuse forced tool use
    assert body["messages"] == [{"role": "user", "content": "Show me the SSNs"}]
    assert [t["name"] for t in body["tools"]] == [
        "list_intents",
        "search_values",
        "run_intent",
        "ask_clarification",
        "answer",
        "refuse",
    ]
    assert all(t["input_schema"]["type"] == "object" for t in body["tools"])
    assert body["max_tokens"] > 0
    assert "no SQL" in body["system"]


def test_list_intents_returns_the_whole_catalog(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("list_intents", {}, "t1")])),
            Reply(message([tool_use("refuse", {"reason": "No."})])),
        ]
    )
    run(con, api)
    (result,) = results_of(api.requests[1])
    assert [i["name"] for i in json.loads(result["content"])] == list(INTENTS)


def test_the_fourth_intent_is_refused(con: duckdb.DuckDBPyConnection) -> None:
    four = [tool_use("run_intent", GENDER, f"t{i}") for i in range(4)]
    api = FakeMessagesApi([Reply(message(four)), Reply(message([answer_call(TOTAL, ["r1"])]))])
    outcome = run(con, api)
    results = results_of(api.requests[1])
    assert [r.get("is_error", False) for r in results] == [False, False, False, True]
    assert "at most 3" in results[3]["content"]
    assert outcome.status == "answered"


def test_a_failed_api_call_ends_the_question_with_a_message(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([httpx2.ConnectError("boom")])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "api_error")
    assert "APIConnectionError" in outcome.message
