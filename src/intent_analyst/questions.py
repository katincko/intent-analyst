"""The evaluation question set: a versioned JSON file, loaded and checked here.

Blocks: one per catalog intent (the question must end in an answer from that intent, with the
listed parameters), `out_of_scope` (expected: refuse), `ambiguous` (expected: ask_clarification)
and `injection`. An injection question says what is accepted: `refused`, or `refused_or_answered`
when it also holds a real question (the legitimate part may be answered, as long as the model does
not obey the injected part).
"""

import json
import re
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from intent_analyst.catalog import INTENTS

QUESTIONS_PATH = Path(__file__).resolve().parents[2] / "data" / "eval" / "questions.json"
HELDOUT_PATH = QUESTIONS_PATH.with_name("heldout_questions.json")
HELDOUT_PREFIX = "heldout-"
NON_INTENT_BLOCKS = ("out_of_scope", "ambiguous", "injection")
Outcome = Literal["refused", "clarification", "refused_or_answered"]


class ForbiddenCall(BaseModel):
    """A tool call that obeys the injection: that tool, with any of `contains` in its input.

    `contains` is a plain text match (after the squashing of `report.py`). `patterns` are regular
    expressions, case-insensitive, matched on the NFKC text of the input as written, for a call
    that is only wrong in a shape (an SQL injection, not a legitimate name with a quote).
    No `contains` and no `patterns` means any call to the tool. A call that failed still counts:
    the model tried to do what the injection asked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    contains: list[str] = []
    patterns: list[str] = []

    @model_validator(mode="after")
    def _patterns_compile(self) -> Self:
        for pattern in self.patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"bad pattern {pattern!r}: {error}") from error
        return self


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: str | None = None
    params: dict[str, Any] = {}  # the parameters that must match; the rest is free
    outcome: Outcome | None = None
    forbidden: list[str] = []  # injection only: markers the injection asks the model to deliver
    protected: list[
        str
    ] = []  # injection only: text that must not appear in any reply, even a refusal
    forbidden_calls: list[ForbiddenCall] = []  # injection only: tool calls that obey it


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    block: str
    question: str
    expect: Expect

    @model_validator(mode="after")
    def _shape_follows_block(self) -> Self:
        e = self.expect
        if self.block in INTENTS:
            if (
                e.intent != self.block
                or e.outcome is not None
                or e.forbidden
                or e.protected
                or e.forbidden_calls
            ):
                raise ValueError(f"{self.id}: an intent question expects its own intent only")
        elif self.block in NON_INTENT_BLOCKS:
            if e.intent is not None or e.params or e.outcome is None:
                raise ValueError(f"{self.id}: this block expects an outcome only")
            if e.outcome == "refused_or_answered" and self.block != "injection":
                raise ValueError(f"{self.id}: only injection questions may accept an answer")
            if (self.block == "injection") != bool(e.forbidden or e.protected or e.forbidden_calls):
                raise ValueError(f"{self.id}: only injection questions list forbidden text")
        else:
            raise ValueError(f"{self.id}: unknown block {self.block!r}")
        return self


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    questions = [Question.model_validate(q) for q in json.loads(path.read_text(encoding="utf-8"))]
    ids = [q.id for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate question id")
    return questions


def load_heldout_questions(path: Path = HELDOUT_PATH) -> list[Question]:
    """The held-out set, written before the fixes it checks. `load_questions` stays the 92."""
    questions = load_questions(path)
    if not all(q.id.startswith(HELDOUT_PREFIX) for q in questions):
        raise ValueError(f"every held-out id starts with {HELDOUT_PREFIX!r}")
    return questions
