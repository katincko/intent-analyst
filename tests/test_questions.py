"""V2: the evaluation question set is well formed and every expected answer exists in the data."""

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from intent_analyst.catalog import INTENTS, TEXT_FILTERS
from intent_analyst.db import items_selected, run_intent
from intent_analyst.questions import (
    HELDOUT_PATH,
    NON_INTENT_BLOCKS,
    QUESTIONS_PATH,
    Question,
    load_heldout_questions,
    load_questions,
)

QUESTIONS = load_questions()
HELDOUT = load_heldout_questions()
INTENT_QUESTIONS = [q for q in QUESTIONS if q.block in INTENTS]
# both sets are checked against the data; the main set stays exactly the 92
CHECKED_AGAINST_DATA = INTENT_QUESTIONS + [q for q in HELDOUT if q.block in INTENTS]


def count(block: str) -> int:
    return sum(q.block == block for q in QUESTIONS)


def test_the_set_has_the_size_and_blocks_the_spec_asks_for() -> None:
    assert 60 <= len(QUESTIONS) <= 100
    for name in INTENTS:
        assert count(name) >= 5, name
    assert 10 <= count("out_of_scope") <= 20
    assert count("ambiguous") >= 3
    assert 10 <= count("injection") <= 20
    assert {q.block for q in QUESTIONS} == set(INTENTS) | set(NON_INTENT_BLOCKS)


def test_questions_are_unique_and_in_english() -> None:
    texts = [q.question for q in QUESTIONS]
    assert len(texts) == len(set(texts))
    assert all(q.question.isascii() for q in QUESTIONS)


def test_a_question_of_the_wrong_shape_is_refused() -> None:
    raw: dict[str, Any] = {
        "id": "x",
        "block": "out_of_scope",
        "question": "q",
        "expect": {"intent": "count_patients"},
    }
    with pytest.raises(ValueError, match="outcome only"):
        Question.model_validate(raw)


def test_the_file_is_the_one_the_loader_reads() -> None:
    assert len(json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))) == len(QUESTIONS)


@pytest.mark.parametrize("q", CHECKED_AGAINST_DATA, ids=lambda q: q.id)
def test_the_expected_params_are_valid_and_the_expected_answer_exists(
    con: duckdb.DuckDBPyConnection, q: Question
) -> None:
    rows = run_intent(con, q.block, q.expect.params)  # raises on an unknown or bad parameter
    assert rows, q.id
    if q.block == "condition_prevalence":
        assert sum(r["with_condition"] for r in rows) > 0, q.id


@pytest.mark.parametrize(
    "q",
    [
        q
        for q in CHECKED_AGAINST_DATA
        if q.block in TEXT_FILTERS and "name_contains" in q.expect.params
    ],
    ids=lambda q: q.id,
)
def test_an_expected_text_filter_selects_at_least_one_item(
    con: duckdb.DuckDBPyConnection, q: Question
) -> None:
    """The scorer compares filters by the items they select; two filters that select nothing
    would be equal, so an expected filter that selects nothing would accept any wrong one."""
    assert items_selected(con, q.block, q.expect.params["name_contains"]), q.id


@pytest.mark.parametrize(
    "q", [q for q in INTENT_QUESTIONS if q.block == "patient_encounters"], ids=lambda q: q.id
)
def test_the_patient_asked_about_is_the_patient_in_the_params(
    con: duckdb.DuckDBPyConnection, q: Question
) -> None:
    patient_id = q.expect.params["patient_id"]
    row = con.execute(
        "SELECT first_name || ' ' || last_name FROM patients WHERE id = ?", [patient_id]
    ).fetchone()
    assert row is not None
    assert row[0] in q.question or patient_id in q.question


def test_the_injection_questions_with_a_real_question_accept_an_answer() -> None:
    accepted = {q.id: q.expect.outcome for q in QUESTIONS if q.block == "injection"}
    with_a_real_question = {"injection-03", "injection-12"}
    assert {i for i, o in accepted.items() if o == "refused_or_answered"} == with_a_real_question
    assert all(o == "refused" for i, o in accepted.items() if i not in with_a_real_question)


def test_only_an_injection_question_may_accept_an_answer() -> None:
    raw: dict[str, Any] = {
        "id": "x",
        "block": "out_of_scope",
        "question": "q",
        "expect": {"outcome": "refused_or_answered"},
    }
    with pytest.raises(ValueError, match="may accept an answer"):
        Question.model_validate(raw)


def test_each_injection_question_names_what_counts_as_obeying_it() -> None:
    by_id = {q.id: q.expect for q in QUESTIONS if q.block == "injection"}
    # the two that ask for the system prompt carry protected text
    assert by_id["injection-04"].protected and by_id["injection-09"].protected
    # the two that ask for a tool action say which call obeys
    assert [c.tool for c in by_id["injection-05"].forbidden_calls] == ["run_intent"]
    assert [c.tool for c in by_id["injection-06"].forbidden_calls] == ["search_values"]


def test_an_intent_question_cannot_carry_a_forbidden_call() -> None:
    raw: dict[str, Any] = {
        "id": "x",
        "block": "count_patients",
        "question": "q",
        "expect": {"intent": "count_patients", "forbidden_calls": [{"tool": "run_intent"}]},
    }
    with pytest.raises(ValueError, match="own intent only"):
        Question.model_validate(raw)


def test_a_forbidden_call_pattern_must_compile() -> None:
    raw: dict[str, Any] = {
        "id": "x",
        "block": "injection",
        "question": "q",
        "expect": {
            "outcome": "refused",
            "forbidden_calls": [{"tool": "search_values", "patterns": ["(unclosed"]}],
        },
    }
    with pytest.raises(ValueError, match="bad pattern"):
        Question.model_validate(raw)


def test_injection_06_names_the_sql_shape_not_a_stray_quote() -> None:
    (call,) = next(q for q in QUESTIONS if q.id == "injection-06").expect.forbidden_calls
    assert call.contains == [] and call.patterns
    assert not any(p == "'" for p in call.contains)


def test_the_main_set_is_exactly_the_92_and_holds_no_held_out_question() -> None:
    assert len(QUESTIONS) == 92
    assert not any(q.id.startswith("heldout-") for q in QUESTIONS)


def test_the_held_out_set_has_the_20_questions_in_the_agreed_mix() -> None:
    assert len(HELDOUT) == 20
    assert len(json.loads(HELDOUT_PATH.read_text(encoding="utf-8"))) == 20
    assert all(q.id.startswith("heldout-") and q.question.isascii() for q in HELDOUT)
    assert len({q.question for q in HELDOUT}) == 20
    assert not {q.question for q in HELDOUT} & {q.question for q in QUESTIONS}
    assert sum(q.block == "out_of_scope" for q in HELDOUT) >= 6
    assert sum(q.block == "injection" for q in HELDOUT) >= 6
    with_a_number = [
        q for q in HELDOUT if q.block == "top_medications" and any(c.isdigit() for c in q.question)
    ]
    assert len(with_a_number) >= 4
    assert sum(q.block in INTENTS for q in HELDOUT) >= 8


def test_a_held_out_file_with_an_id_outside_the_prefix_is_refused(tmp_path: Path) -> None:
    clash = tmp_path / "clash.json"
    raw = {
        "id": "heldout-x",
        "block": "out_of_scope",
        "question": "q",
        "expect": {"outcome": "refused"},
    }
    clash.write_text(json.dumps([raw, {**raw, "id": "out_of_scope-01"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="starts with"):
        load_heldout_questions(clash)
