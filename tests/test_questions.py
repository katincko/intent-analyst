"""V2: the evaluation question set is well formed and every expected answer exists in the data."""

import json
from typing import Any

import duckdb
import pytest

from intent_analyst.catalog import INTENTS
from intent_analyst.db import run_intent
from intent_analyst.questions import (
    NON_INTENT_BLOCKS,
    QUESTIONS_PATH,
    Question,
    load_questions,
)

QUESTIONS = load_questions()
INTENT_QUESTIONS = [q for q in QUESTIONS if q.block in INTENTS]


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


@pytest.mark.parametrize("q", INTENT_QUESTIONS, ids=lambda q: q.id)
def test_the_expected_params_are_valid_and_the_expected_answer_exists(
    con: duckdb.DuckDBPyConnection, q: Question
) -> None:
    rows = run_intent(con, q.block, q.expect.params)  # raises on an unknown or bad parameter
    assert rows, q.id
    if q.block == "condition_prevalence":
        assert sum(r["with_condition"] for r in rows) > 0, q.id


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
