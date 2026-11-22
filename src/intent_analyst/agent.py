"""The tool-use loop: one question in, one `Outcome` out.

The model only chooses tools. Every tool runs here, on validated input, against the locked
database. The loop is written by hand on the official SDK (no framework) so each limit and each
protocol rule is a few visible lines.

Limits per question (all checked after every response, before it is acted on):
* steps: at most `max_steps` requests to the model;
* cost: at most `max_usd`, from the `usage` of each response and the versioned price table. The
  cap is checked after the spend, so a question can end up to one response over it;
* size: no request is sent whose JSON body is over `max_request_bytes`. A token covers at least one
  byte, so this bounds the input of one response, and with it the cost of one response, which is
  what lets an evaluation prove that its total can never pass its global cap;
* time: at most `max_seconds` on an injectable clock (exactly the limit is still allowed for a
  reply, but no new request starts with no time left), and the HTTP timeout of each request is
  the time that is left.

No `tool_choice` is sent (current models reject a forced tool): the model is steered by the
system prompt (a versioned text, see `prompts.py`), and a reply without a tool call is a protocol
failure with one new attempt.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, cast

import anthropic
import duckdb
from anthropic.types import MessageParam, OutputConfigParam
from pydantic import BaseModel, ValidationError

from intent_analyst.catalog import INTENTS
from intent_analyst.grounding import ungrounded_numbers
from intent_analyst.pricing import PRICE_TABLE_VERSION, PRICES, cost_usd
from intent_analyst.prompts import PromptVersion, get, system_blocks
from intent_analyst.search import SearchValues
from intent_analyst.tools import (
    INPUT_MODELS,
    MAX_INTENTS_PER_QUESTION,
    TERMINAL_TOOLS,
    Answer,
    AskClarification,
    Refuse,
    RunIntent,
    Table,
    catalog_listing,
    run_catalog_intent,
    run_search,
    tool_definitions,
)

MAX_TOKENS = 2048
_OUTPUT_CONFIG: OutputConfigParam = {"effort": "low"}  # shallow thinking keeps a step cheap
MAX_TOOL_ERRORS = 3  # recoverable tool errors tolerated per question
MAX_PROTOCOL_RETRIES = 1  # replies without a tool call that get a new attempt

Status = Literal["answered", "clarification", "refused", "stopped"]
StopReason = Literal[
    "max_steps",
    "max_cost",
    "request_too_large",
    "timeout",
    "protocol",
    "tool_errors",
    "api_error",
    "refusal",
    "unexpected",
    "secret_found",  # set by the evaluation, never by the loop
]


@dataclass(frozen=True)
class Limits:
    max_steps: int = 6
    max_usd: Decimal = Decimal("0.05")
    max_seconds: float = 30.0
    max_request_bytes: int = 60_000


@dataclass(frozen=True)
class Event:
    """One tool call of the loop, kept for the report."""

    step: int
    tool: str
    input: dict[str, Any]
    is_error: bool
    result_id: str | None


@dataclass
class Outcome:
    status: Status
    message: str  # the answer, the clarifying question, the refusal reason or the stop notice
    tables: list[Table]  # tables behind an answer (empty otherwise)
    stop_reason: StopReason | None
    steps: int
    cost_usd: Decimal
    seconds: float
    protocol_failures: int
    tool_errors: int
    events: list[Event] = field(default_factory=list)
    price_table: str = PRICE_TABLE_VERSION
    prompt_version: str = ""
    input_tokens: int = 0  # summed over the responses of the question
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


DEFAULT_LIMITS = Limits()


def ask(
    question: str,
    *,
    client: anthropic.Anthropic,
    con: duckdb.DuckDBPyConnection,
    model: str,
    limits: Limits = DEFAULT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
    prompt: PromptVersion | None = None,
) -> Outcome:
    """`prompt` is the system prompt version; None is the latest."""
    if model not in PRICES:
        raise ValueError(f"no price for model {model!r}: add it to pricing.PRICES first")
    return _Question(question, client, con, model, limits, clock, prompt or get(None)).run()


def _api_error_notice(error: anthropic.APIError) -> str:
    """The stop message of a failed call, with the `request-id` header for the support ticket."""
    notice = f"Stopped: the API call failed ({type(error).__name__}): {error}"
    request_id = error.request_id if isinstance(error, anthropic.APIStatusError) else None
    if request_id and request_id not in notice:  # the body of an API error already has it
        notice += f" [request_id: {request_id}]"
    return notice


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in error.errors()
    )


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _table_json(table: Table) -> str:
    return json.dumps(
        {"result_id": table.id, "columns": table.columns, "rows": table.rows}, default=str
    )


def _json_bytes(value: Any) -> int:
    """Size of the JSON of a request part. SDK content blocks (pydantic models) are dumped."""

    def default(o: Any) -> Any:
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        raise TypeError(type(o))

    return len(json.dumps(value, default=default))


class _Question:
    def __init__(
        self,
        question: str,
        client: anthropic.Anthropic,
        con: duckdb.DuckDBPyConnection,
        model: str,
        limits: Limits,
        clock: Callable[[], float],
        prompt: PromptVersion,
    ) -> None:
        self.question = question
        self.prompt = prompt
        self.system = system_blocks(prompt)
        # the tools and the system prompt are sent with every request
        self.prefix_bytes = _json_bytes([tool_definitions(), self.system])
        self.client = client
        self.con = con
        self.model = model
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.steps = 0
        self.cost = Decimal(0)
        self.tokens = [0, 0, 0, 0]  # input, output, cache write, cache read
        self.protocol_failures = 0
        self.tool_errors = 0
        self.intents_run = 0
        self.tables: dict[str, Table] = {}
        self.events: list[Event] = []

    # ---- results ----

    def _outcome(
        self,
        status: Status,
        message: str,
        stop_reason: StopReason | None = None,
        tables: list[Table] | None = None,
    ) -> Outcome:
        return Outcome(
            status,
            message,
            tables or [],
            stop_reason,
            self.steps,
            self.cost,
            self.clock() - self.started,
            self.protocol_failures,
            self.tool_errors,
            self.events,
            PRICE_TABLE_VERSION,
            self.prompt.name,
            *self.tokens,
        )

    def _stop(self, reason: StopReason, message: str) -> Outcome:
        return self._outcome("stopped", message, reason)

    def _limit_hit(self, before_request: bool = False) -> Outcome | None:
        """Time and cost are maxima: a response that lands exactly on a limit is accepted.

        Before the next request, though, a time budget that is used up (elapsed == limit) leaves
        nothing to send it with, so that check also stops at equality.
        """
        if self.cost > self.limits.max_usd:
            return self._stop(
                "max_cost",
                f"Stopped: the question cost ${self.cost:.4f}, over the "
                f"${self.limits.max_usd} limit.",
            )
        elapsed = self.clock() - self.started
        if elapsed > self.limits.max_seconds:
            return self._stop(
                "timeout",
                f"Stopped: {elapsed:.1f} s elapsed, over the {self.limits.max_seconds:g} s limit.",
            )
        if before_request and elapsed >= self.limits.max_seconds:
            return self._stop(
                "timeout",
                f"Stopped: {elapsed:.1f} s elapsed, no time left for another request "
                f"(limit {self.limits.max_seconds:g} s).",
            )
        return None

    # ---- the loop ----

    def run(self) -> Outcome:
        messages: list[dict[str, Any]] = [{"role": "user", "content": self.question}]
        while True:
            if self.steps >= self.limits.max_steps:
                return self._stop(
                    "max_steps",
                    f"Stopped: the limit of {self.limits.max_steps} steps was reached "
                    "without a final answer.",
                )
            if (hit := self._limit_hit(before_request=True)) is not None:
                return hit
            size = self.prefix_bytes + _json_bytes(messages)
            if size > self.limits.max_request_bytes:
                return self._stop(
                    "request_too_large",
                    f"Stopped: the next request would be {size} bytes, over the "
                    f"{self.limits.max_request_bytes} byte limit.",
                )
            remaining = self.limits.max_seconds - (self.clock() - self.started)
            self.steps += 1
            try:
                response = self.client.with_options(
                    timeout=remaining, max_retries=0
                ).messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=self.system,
                    tools=tool_definitions(),
                    messages=cast("list[MessageParam]", messages),
                    output_config=_OUTPUT_CONFIG,
                )
            except anthropic.APITimeoutError:
                return self._stop("timeout", "Stopped: a request to the model timed out.")
            except anthropic.APIError as error:
                return self._stop("api_error", _api_error_notice(error))
            self.cost += cost_usd(self.model, response.usage)
            usage = response.usage
            for i, n in enumerate(
                (
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cache_creation_input_tokens,
                    usage.cache_read_input_tokens,
                )
            ):
                self.tokens[i] += n or 0
            if (hit := self._limit_hit()) is not None:
                return hit
            if response.stop_reason == "refusal":
                return self._stop("refusal", "Stopped: the model declined the request.")
            if response.stop_reason not in ("end_turn", "tool_use", "max_tokens"):
                return self._stop(
                    "unexpected", f"Stopped: unexpected stop_reason {response.stop_reason!r}."
                )
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "max_tokens" or not calls:
                self.protocol_failures += 1
                if self.protocol_failures > MAX_PROTOCOL_RETRIES:
                    return self._stop(
                        "protocol", "Stopped: the model did not reply through a tool, twice."
                    )
                self._append_assistant(messages, response.content)
                cut = response.stop_reason == "max_tokens"
                messages.append({"role": "user", "content": self._retry_content(cut, calls)})
                continue
            done, results = self._run_calls(calls)
            if done is not None:
                return done
            if self.tool_errors > MAX_TOOL_ERRORS:
                return self._stop(
                    "tool_errors", f"Stopped: more than {MAX_TOOL_ERRORS} tool errors."
                )
            self._append_assistant(messages, response.content)
            messages.append({"role": "user", "content": results})

    @staticmethod
    def _append_assistant(messages: list[dict[str, Any]], content: Any) -> None:
        if content:  # the API refuses an empty assistant turn
            messages.append({"role": "assistant", "content": content})

    @staticmethod
    def _retry_content(cut: bool, calls: list[Any]) -> list[dict[str, Any]]:
        """Every tool_use of a cut turn still gets its tool_result; a tool-less turn gets text."""
        why = "Your reply was cut off at the token limit, so this call was not run. " if cut else ""
        nudge = "Reply only by calling a tool. To finish, call answer, ask_clarification or refuse."
        if calls:
            return [_tool_result(c.id, why + nudge, is_error=True) for c in calls]
        return [{"type": "text", "text": why + nudge}]

    # ---- tools ----

    def _run_calls(self, calls: list[Any]) -> tuple[Outcome | None, list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        finished: Outcome | None = None
        for call in calls:
            result, outcome = self._run_call(call, solo=len(calls) == 1)
            results.append(result)
            finished = finished or outcome
        return finished, results

    def _error(self, call: Any, text: str) -> dict[str, Any]:
        self.tool_errors += 1
        self.events.append(Event(self.steps, call.name, dict(call.input), True, None))
        return _tool_result(call.id, text, is_error=True)

    def _ok(self, call: Any, text: str, result_id: str | None = None) -> dict[str, Any]:
        self.events.append(Event(self.steps, call.name, dict(call.input), False, result_id))
        return _tool_result(call.id, text)

    def _run_call(self, call: Any, solo: bool) -> tuple[dict[str, Any], Outcome | None]:
        model = INPUT_MODELS.get(call.name)
        if model is None:
            return self._error(
                call, f"Unknown tool {call.name!r}. Tools: {list(INPUT_MODELS)}."
            ), None
        try:
            parsed = model.model_validate(call.input)
        except ValidationError as error:
            return self._error(call, f"Invalid input: {_describe(error)}"), None
        if call.name in TERMINAL_TOOLS and not solo:
            text = f"{call.name} was not run: call it alone, after the results of the other tools."
            return self._error(call, text), None
        if isinstance(parsed, AskClarification):
            return self._ok(call, "asked"), self._outcome("clarification", parsed.question)
        if isinstance(parsed, Refuse):
            return self._ok(call, "refused"), self._outcome("refused", parsed.reason)
        if isinstance(parsed, Answer):
            return self._answer(call, parsed)
        if isinstance(parsed, RunIntent):
            return self._run_intent(call, parsed), None
        if isinstance(parsed, SearchValues):
            return self._search(call, parsed), None
        return self._ok(call, json.dumps(catalog_listing())), None  # list_intents

    def _run_intent(self, call: Any, parsed: RunIntent) -> dict[str, Any]:
        if parsed.intent not in INTENTS:
            return self._error(call, f"Unknown intent {parsed.intent!r}. Intents: {list(INTENTS)}.")
        if self.intents_run >= MAX_INTENTS_PER_QUESTION:
            return self._error(
                call, f"Limit reached: at most {MAX_INTENTS_PER_QUESTION} intents per question."
            )
        try:
            table = run_catalog_intent(self.con, f"r{len(self.tables) + 1}", parsed)
        except ValidationError as error:
            return self._error(call, f"Invalid params: {_describe(error)}")
        except duckdb.Error as error:
            return self._error(call, f"The query failed: {error}")
        self.intents_run += 1
        self.tables[table.id] = table
        return self._ok(call, _table_json(table), table.id)

    def _search(self, call: Any, parsed: SearchValues) -> dict[str, Any]:
        try:
            table = run_search(self.con, f"r{len(self.tables) + 1}", parsed)
        except duckdb.Error as error:
            return self._error(call, f"The search failed: {error}")
        self.tables[table.id] = table
        return self._ok(call, _table_json(table), table.id)

    def _answer(self, call: Any, parsed: Answer) -> tuple[dict[str, Any], Outcome | None]:
        unknown = [s for s in parsed.sources if s not in self.tables]
        if unknown:
            text = f"Unknown sources {unknown}. Known result_ids: {list(self.tables)}."
            return self._error(call, text), None
        cited = [self.tables[s] for s in parsed.sources]
        missing = ungrounded_numbers(
            parsed.text, [t.rows for t in cited], self.question, self.prompt.guided
        )
        if missing:
            text = (
                f"Numbers not found in the cited tables: {missing}. Quote values exactly as they "
                "appear, or cite the table that has them. Do no arithmetic or rounding."
            )
            return self._error(call, text), None
        return self._ok(call, "accepted"), self._outcome("answered", parsed.text, tables=cited)
