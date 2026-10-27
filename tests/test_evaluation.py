"""V3: the executor never passes its global cap, refuses to run without a key, and samples by block.

Everything runs against the in-memory worst-case stand-in `evaluate.worst_case_client`, whose every
response costs the most the limits allow. No test opens a socket (see conftest.py).
"""

import io
from decimal import Decimal

import anthropic
import duckdb
import pytest

from intent_analyst.agent import Limits, Outcome, ask
from intent_analyst.catalog import INTENTS
from intent_analyst.evaluate import KEY_VARIABLE, main, plan_lines, worst_case_client
from intent_analyst.evaluation import (
    MAX_CONSECUTIVE_API_ERRORS,
    Run,
    question_reserve,
    run_evaluation,
    select,
    worst_response_usd,
)
from intent_analyst.questions import Question, load_questions

QUESTIONS = load_questions()
MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
SONNET = "claude-sonnet-5-5"
CAP = Decimal("2.00")


def worst_case_run(
    con: duckdb.DuckDBPyConnection,
    model: str,
    limits: Limits,
    cap: Decimal = CAP,
    questions: list[Question] | None = None,
) -> Run:
    client = worst_case_client(model, limits)

    def ask_one(q: Question) -> Outcome:
        return ask(q.question, client=client, con=con, model=model, limits=limits)

    return run_evaluation(
        questions if questions is not None else QUESTIONS,
        ask_one,
        model=model,
        limits=limits,
        total_cap=cap,
    )


# ---- the global cap ----


@pytest.mark.parametrize("model", MODELS)
def test_the_worst_case_over_the_whole_set_stays_under_the_global_cap(
    con: duckdb.DuckDBPyConnection, model: str
) -> None:
    run = worst_case_run(con, model, Limits())
    assert run.runs, "the run must start"
    assert sum((r.outcome.cost_usd for r in run.runs), Decimal(0)) == run.spent
    assert run.spent <= CAP
    assert run.notice is not None  # the set is far more than the cap pays for in the worst case


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    "limits",
    [
        Limits(),
        Limits(max_usd=Decimal("0.01")),
        Limits(max_usd=Decimal("0.5"), max_steps=2),
        Limits(max_steps=1),
        Limits(max_request_bytes=10_000),
    ],
)
def test_no_question_costs_more_than_its_reserve(
    con: duckdb.DuckDBPyConnection, model: str, limits: Limits
) -> None:
    run = worst_case_run(con, model, limits, cap=Decimal("1000"), questions=QUESTIONS[:12])
    assert len(run.runs) == 12
    assert max(r.outcome.cost_usd for r in run.runs) <= question_reserve(model, limits)


def test_the_cap_is_not_passed_at_the_exact_edge(con: duckdb.DuckDBPyConnection) -> None:
    limits = Limits()
    reserve = question_reserve(SONNET, limits)
    # Room for two worst-case questions and a bit less than a third reserve: the third must not
    # start, even though more than the per-question cap is left.
    cap = reserve * 2 + worst_response_usd(SONNET, limits) / 2
    run = worst_case_run(con, SONNET, limits, cap=cap)
    assert len(run.runs) == 2
    assert run.spent <= cap
    assert run.notice and run.not_run[0] is QUESTIONS[2]


def test_a_cap_under_one_reserve_runs_nothing(con: duckdb.DuckDBPyConnection) -> None:
    limits = Limits()
    run = worst_case_run(con, SONNET, limits, cap=question_reserve(SONNET, limits) / 2)
    assert not run.runs and len(run.not_run) == len(QUESTIONS)
    assert run.notice and QUESTIONS[0].id in run.notice


def test_a_request_that_may_have_been_billed_is_charged_at_the_worst_case() -> None:
    limits = Limits()
    worst = worst_response_usd(SONNET, limits)

    def timed_out(q: Question) -> Outcome:
        return Outcome("stopped", "timed out", [], "timeout", 1, Decimal(0), 1.0, 0, 0)

    run = run_evaluation(QUESTIONS, timed_out, model=SONNET, limits=limits, total_cap=CAP)
    assert [r.charged for r in run.runs] == [worst] * MAX_CONSECUTIVE_API_ERRORS
    assert run.notice and "in a row" in run.notice
    assert run.spent == worst * MAX_CONSECUTIVE_API_ERRORS


# ---- the request size bound the cap rests on ----


def test_a_request_over_the_byte_limit_is_never_sent(con: duckdb.DuckDBPyConnection) -> None:
    sent: list[bytes] = []
    client = worst_case_client(SONNET, Limits(), sent)
    outcome = ask("Q?", client=client, con=con, model=SONNET, limits=Limits(max_request_bytes=100))
    assert (outcome.status, outcome.stop_reason) == ("stopped", "request_too_large")
    assert sent == [] and outcome.steps == 0
    assert "100 byte limit" in outcome.message


def test_the_request_grows_with_each_tool_result_until_the_limit_stops_it(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # The stand-in keeps calling list_intents, whose result is the whole catalog (about 10 KB), so
    # a 20,000 byte limit stops the question after a few requests, long before 6 steps.
    limits = Limits(max_usd=Decimal("100"), max_request_bytes=20_000)
    outcome = ask(
        "Q?", client=worst_case_client(SONNET, limits), con=con, model=SONNET, limits=limits
    )
    assert outcome.stop_reason == "request_too_large"
    assert 1 < outcome.steps < 6


# ---- the command line ----


def run_main(args: list[str], environ: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return main(args, environ, out, err), out.getvalue(), err.getvalue()


def forbid_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("a client was built")

    monkeypatch.setattr(anthropic, "Anthropic", forbidden)


def test_without_a_key_it_refuses_and_builds_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_clients(monkeypatch)
    code, out, err = run_main(["--model", SONNET], {})
    assert code == 2
    assert KEY_VARIABLE in err and "Nothing was sent" in err
    assert out == ""


def test_with_a_key_but_no_yes_it_prints_the_bill_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forbid_clients(monkeypatch)
    code, out, err = run_main([], {KEY_VARIABLE: "sk-ant-not-a-real-key"})
    assert code == 2
    assert "most the run can cost: $2.00" in out
    assert "--yes" in err


@pytest.mark.parametrize("model", [SONNET, "claude-haiku-5-5"])
@pytest.mark.parametrize("sample", [[], ["--per-block", "2"]])
def test_the_rehearsal_needs_no_key_and_stays_under_the_cap(model: str, sample: list[str]) -> None:
    code, out, err = run_main(["--dry-run", "--model", model, *sample], {})
    assert (code, err) == (0, "")
    assert "rehearsal against the worst-case stand-in" in out
    spent = Decimal(out.split("counted spend: $")[1].split(" ")[0])
    assert 0 < spent <= CAP


def test_the_plan_states_the_most_the_run_can_cost() -> None:
    limits = Limits()
    assert "most the run can cost: $2.00" in plan_lines(SONNET, limits, CAP, QUESTIONS)
    few = plan_lines("claude-haiku-5-5", limits, CAP, select(QUESTIONS, 1, None))
    blocks = len({q.block for q in QUESTIONS})
    expected = (question_reserve("claude-haiku-5-5", limits) * blocks).quantize(Decimal("0.01"))
    assert few[-1] == f"most the run can cost: ${expected}"


@pytest.mark.parametrize("bad", [["--total-usd", "0"], ["--question-usd", "-1"]])
def test_bad_amounts_are_refused(bad: list[str]) -> None:
    code, _, err = run_main(["--dry-run", *bad], {})
    assert code == 2 and "Bad arguments" in err


# ---- the stratified sample ----


def test_a_sample_per_block_takes_that_many_of_every_block() -> None:
    chosen = select(QUESTIONS, 2, None)
    blocks = {q.block for q in QUESTIONS}
    assert len(chosen) == 2 * len(blocks)
    for block in blocks:
        assert sum(q.block == block for q in chosen) == 2
    assert select(QUESTIONS, 2, None) == chosen  # deterministic


def test_the_sample_is_spread_through_each_block() -> None:
    chosen = [q.id for q in select(QUESTIONS, 3, None) if q.block == "count_patients"]
    assert chosen == ["count_patients-01", "count_patients-03", "count_patients-05"]


def test_a_count_cut_keeps_every_block_while_it_can() -> None:
    blocks = {q.block for q in QUESTIONS}
    assert {q.block for q in select(QUESTIONS, None, len(blocks))} == blocks
    assert len(select(QUESTIONS, None, 5)) == 5
    assert len(select(QUESTIONS, None, None)) == len(QUESTIONS)
    assert len(select(QUESTIONS, 1000, None)) == len(QUESTIONS)


def test_every_intent_is_in_the_set() -> None:
    assert {q.block for q in QUESTIONS} >= set(INTENTS)


def test_the_stand_in_is_a_client_with_no_network() -> None:
    assert isinstance(worst_case_client(SONNET, Limits()), anthropic.Anthropic)
