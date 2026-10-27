"""Test 6 of the spec: steps, cost and time end a question with a clear message.

Cost is computed from the `usage` of each fake response and the price table of claude-sonnet-5-5
(2 dollars per million input tokens, 10 per million output tokens, see pricing.py), so 10,000
input tokens cost 0.02 dollars. The clock is injected: the fake API moves it by `Reply.takes`.
"""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import duckdb
import httpx2
import pytest
from fake_api import FakeMessagesApi, Reply, error_response, message, tool_use, usage

from intent_analyst.agent import Limits, Outcome, ask
from intent_analyst.pricing import PRICE_TABLE_VERSION, cost_usd

MODEL = "claude-sonnet-5-5"


def run(
    con: duckdb.DuckDBPyConnection, api: FakeMessagesApi, limits: Limits | None = None
) -> Outcome:
    return ask(
        "Q?",
        client=api.client(),
        con=con,
        model=MODEL,
        clock=api.clock,
        **({"limits": limits} if limits else {}),
    )


def looking(takes: float = 0.0, input_tokens: int = 100, output_tokens: int = 50) -> Reply:
    """A reply that keeps the loop going (a harmless tool call)."""
    body = message([tool_use("list_intents", {})], token_usage=usage(input_tokens, output_tokens))
    return Reply(body, takes)


def refusing(takes: float = 0.0, input_tokens: int = 100, output_tokens: int = 50) -> Reply:
    body = message(
        [tool_use("refuse", {"reason": "Out of scope."})],
        token_usage=usage(input_tokens, output_tokens),
    )
    return Reply(body, takes)


# ---- steps ----


def test_the_seventh_request_is_never_sent(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking() for _ in range(7)])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "max_steps")
    assert len(api.requests) == 6
    assert outcome.steps == 6
    assert "limit of 6 steps" in outcome.message
    assert len(api.script) == 1  # the seventh reply was never asked for


def test_a_final_answer_on_the_last_step_is_accepted(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([*[looking() for _ in range(5)], refusing()])
    outcome = run(con, api)
    assert (outcome.status, outcome.steps) == ("refused", 6)


def test_the_step_limit_is_configurable(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking() for _ in range(3)])
    outcome = run(con, api, Limits(max_steps=2))
    assert (outcome.stop_reason, len(api.requests)) == ("max_steps", 2)
    assert "limit of 2 steps" in outcome.message


# ---- cost ----


def test_a_response_over_the_cost_limit_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking(input_tokens=30_000), refusing()])  # 0.06 + 50 output tokens
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "max_cost")
    assert len(api.requests) == 1
    assert outcome.cost_usd == Decimal("0.0605")
    assert "$0.0605" in outcome.message and "$0.05" in outcome.message


def test_exactly_the_cost_limit_is_still_allowed(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([refusing(input_tokens=25_000, output_tokens=0)])  # exactly 0.05
    outcome = run(con, api)
    assert outcome.status == "refused"
    assert outcome.cost_usd == Decimal("0.05")


def test_cost_adds_up_over_the_steps(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking(input_tokens=10_000, output_tokens=0) for _ in range(4)])
    outcome = run(con, api)  # 0.02 per step: the third response reaches 0.06
    assert (outcome.stop_reason, len(api.requests)) == ("max_cost", 3)
    assert outcome.cost_usd == Decimal("0.06")


def test_the_cost_limit_is_configurable(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking(input_tokens=1_000, output_tokens=0)])  # 0.002
    outcome = run(con, api, Limits(max_usd=Decimal("0.001")))
    assert outcome.stop_reason == "max_cost"


def test_the_outcome_names_the_price_table(con: duckdb.DuckDBPyConnection) -> None:
    outcome = run(con, FakeMessagesApi([refusing()]))
    assert outcome.price_table == PRICE_TABLE_VERSION


def test_a_model_without_a_price_is_refused_before_any_request(
    con: duckdb.DuckDBPyConnection,
) -> None:
    api = FakeMessagesApi([])
    with pytest.raises(ValueError, match="no price"):
        ask("Q?", client=api.client(), con=con, model="claude-unpriced", clock=api.clock)
    assert api.requests == []


# ---- time ----


def test_a_response_over_the_time_limit_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([looking(takes=31.0), refusing()])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "timeout")
    assert len(api.requests) == 1
    assert "31.0 s" in outcome.message and "30 s" in outcome.message
    assert outcome.seconds == 31.0


def test_just_under_the_time_limit_is_allowed(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([refusing(takes=29.9)])
    assert run(con, api).status == "refused"


def test_time_adds_up_and_each_request_gets_only_the_time_that_is_left(
    con: duckdb.DuckDBPyConnection,
) -> None:
    api = FakeMessagesApi([looking(takes=12.0) for _ in range(4)])
    outcome = run(con, api)
    assert (outcome.stop_reason, len(api.requests)) == ("timeout", 3)  # 36 s at the third reply
    assert [t["read"] for t in api.timeouts] == pytest.approx([30.0, 18.0, 6.0])


def test_a_request_that_times_out_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([httpx2.ReadTimeout("slow")])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "timeout")
    assert "timed out" in outcome.message
    assert len(api.requests) == 1  # no hidden retry that would spend more time


# ---- a final reply over a cap ----

GENDER = {"intent": "count_patients", "params": {"group_by": "gender"}}
FINAL_CALLS: dict[str, dict[str, Any]] = {
    "answer": {"text": "27 patients are M.", "sources": ["r1"]},
    "refuse": {"reason": "Out of scope."},
    "ask_clarification": {"question": "Which year?"},
}


def final_reply(tool: str, takes: float = 0.0, input_tokens: int = 100) -> Reply:
    body = message([tool_use(tool, FINAL_CALLS[tool])], token_usage=usage(input_tokens, 50))
    return Reply(body, takes)


def first_table() -> Reply:
    return Reply(message([tool_use("run_intent", GENDER)], token_usage=usage(100, 50)))


@pytest.mark.parametrize("tool", list(FINAL_CALLS))
def test_a_final_reply_over_the_cost_limit_is_not_accepted(
    con: duckdb.DuckDBPyConnection, tool: str
) -> None:
    api = FakeMessagesApi([first_table(), final_reply(tool, input_tokens=30_000)])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "max_cost")
    assert outcome.tables == []  # nothing from the rejected reply is handed back
    assert "limit" in outcome.message and "$0.05" in outcome.message
    assert [e.tool for e in outcome.events] == ["run_intent"]  # the final tool never ran


@pytest.mark.parametrize("tool", list(FINAL_CALLS))
def test_a_final_reply_over_the_time_limit_is_not_accepted(
    con: duckdb.DuckDBPyConnection, tool: str
) -> None:
    api = FakeMessagesApi([first_table(), final_reply(tool, takes=31.0)])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "timeout")
    assert outcome.tables == []
    assert "31.0 s" in outcome.message and "30 s" in outcome.message
    assert [e.tool for e in outcome.events] == ["run_intent"]


# ---- other ways to end ----


def test_an_api_error_is_not_retried_and_is_reported(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([error_response(429, "rate_limit_error", "slow down"), refusing()])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "api_error")
    assert "RateLimitError" in outcome.message and "slow down" in outcome.message
    assert len(api.requests) == 1


def test_a_refusal_stop_reason_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([Reply(message([], stop_reason="refusal"))])
    outcome = run(con, api)
    assert (outcome.status, outcome.stop_reason) == ("stopped", "refusal")
    assert "declined" in outcome.message


def test_an_unexpected_stop_reason_ends_the_question(con: duckdb.DuckDBPyConnection) -> None:
    api = FakeMessagesApi([Reply(message([], stop_reason="pause_turn"))])
    outcome = run(con, api)
    assert (outcome.stop_reason, "pause_turn" in outcome.message) == ("unexpected", True)


# ---- pricing ----


def usage_of(
    input_tokens: int, output_tokens: int, created: int | None = None, read: int | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=created,
        cache_read_input_tokens=read,
    )


@pytest.mark.parametrize(
    ("model", "used", "dollars"),
    [
        ("claude-sonnet-5-5", usage_of(1000, 500), "0.007"),
        ("claude-sonnet-5-5", usage_of(0, 0, created=1_000_000), "4"),
        ("claude-sonnet-5-5", usage_of(0, 0, read=1_000_000), "0.10"),
        ("claude-opus-5-5", usage_of(1_000_000, 1_000_000), "24"),
        ("claude-haiku-5-5", usage_of(100_000, 0), "0.01"),
        ("claude-haiku-5-5", usage_of(100_001, 0), "0.0500005"),  # long-prompt rate
        ("claude-haiku-5-5", usage_of(50_000, 50_000, read=1), "0.03000001"),
    ],
)
def test_cost_from_usage(model: str, used: SimpleNamespace, dollars: str) -> None:
    assert cost_usd(model, used) == Decimal(dollars)


def test_cost_of_an_unknown_model_raises() -> None:
    with pytest.raises(KeyError, match="no price"):
        cost_usd("claude-unpriced", usage_of(1, 1))
