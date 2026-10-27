"""Running the evaluation questions under one global cost cap that can never be passed.

How the cap holds. A question stops as soon as its cost is over `limits.max_usd`, and the check
comes after a response, so a question ends at most one response over its cap. One response costs
at most `worst_response_usd`: the loop never sends a body over `limits.max_request_bytes` and a
token covers at least one byte, so the input is bounded, and `max_tokens` bounds the output. A
question therefore costs at most `question_reserve`. Before each question the runner checks that
what is left of the global cap is at least that reserve, and stops if it is not. Spent money is
counted from the `usage` the API reports; a request that ended without a usable reply (a timeout or
an API error) is charged at the worst case of one response, because the API may have billed it.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from intent_analyst.agent import MAX_TOKENS, Limits, Outcome
from intent_analyst.pricing import worst_case_cost
from intent_analyst.questions import Question

# Tokens the request carries that its JSON does not: the tool-use system prompt (286 tokens for the
# 5.5 models, per the pricing page) and the role markup. Added on top of the byte bound.
OVERHEAD_TOKENS = 1_000
MAX_CONSECUTIVE_API_ERRORS = 3
UNREPORTED = ("timeout", "api_error")


def worst_response_usd(model: str, limits: Limits) -> Decimal:
    """The most one response can cost under these limits."""
    return worst_case_cost(model, limits.max_request_bytes + OVERHEAD_TOKENS, MAX_TOKENS)


def question_reserve(model: str, limits: Limits) -> Decimal:
    """The most one question can cost: its cap plus one response, or every step at the worst."""
    response = worst_response_usd(model, limits)
    return min(limits.max_usd + response, limits.max_steps * response)


def select(
    questions: Sequence[Question], per_block: int | None, count: int | None
) -> list[Question]:
    """A stratified sample: `per_block` questions of each block, evenly spaced through it, then at
    most `count` of them taken round-robin over the blocks, so a cut keeps every block."""
    blocks: dict[str, list[Question]] = defaultdict(list)
    for q in questions:
        blocks[q.block].append(q)
    if per_block is not None:
        for name, items in blocks.items():
            n = len(items)
            if per_block < n:
                blocks[name] = [items[(j * n) // per_block] for j in range(per_block)]
    ordered: list[Question] = []
    depth = max((len(items) for items in blocks.values()), default=0)
    for level in range(depth):
        ordered.extend(items[level] for items in blocks.values() if level < len(items))
    return ordered if count is None else ordered[:count]


@dataclass(frozen=True)
class QuestionRun:
    question: Question
    outcome: Outcome
    charged: Decimal  # what the cap counts: the reported cost, plus a worst case if unreported


@dataclass
class Run:
    model: str
    total_cap: Decimal
    reserve: Decimal
    runs: list[QuestionRun] = field(default_factory=list)
    not_run: list[Question] = field(default_factory=list)
    spent: Decimal = Decimal(0)
    notice: str | None = None  # why the run ended before the last question, if it did
    interrupted: bool = False  # a recording held an API key: the run stopped for that


def run_evaluation(
    questions: Sequence[Question],
    ask_one: Callable[[Question], Outcome],
    *,
    model: str,
    limits: Limits,
    total_cap: Decimal,
) -> Run:
    """Ask the questions in order, never letting the counted spend pass `total_cap`."""
    reserve = question_reserve(model, limits)
    unreported = worst_response_usd(model, limits)
    run = Run(model, total_cap, reserve)
    api_errors = 0
    for i, question in enumerate(questions):
        left = total_cap - run.spent
        if left < reserve:
            run.notice = (
                f"Stopped before {question.id}: ${left:.4f} of the ${total_cap} cap is left, "
                f"under the ${reserve:.4f} that one question can cost at most."
            )
        elif api_errors >= MAX_CONSECUTIVE_API_ERRORS:
            run.notice = (
                f"Stopped before {question.id}: {api_errors} questions in a row ended in an API "
                "error or a timeout."
            )
        if run.notice:
            run.not_run = list(questions[i:])
            break
        outcome = ask_one(question)
        charged = outcome.cost_usd + (unreported if outcome.stop_reason in UNREPORTED else 0)
        run.runs.append(QuestionRun(question, outcome, charged))
        run.spent += charged
        if outcome.stop_reason == "secret_found":
            run.interrupted = True
            run.notice = (
                f"Interrupted after {question.id}: its recording held an API key and was deleted. "
                "No further question was asked."
            )
            run.not_run = list(questions[i + 1 :])
            break
        api_errors = api_errors + 1 if outcome.stop_reason in UNREPORTED else 0
    return run
