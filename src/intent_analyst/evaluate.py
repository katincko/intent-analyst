"""Command line of the live evaluation: `python -m intent_analyst.evaluate`.

    --dry-run   rehearsal: everything runs against an in-memory worst-case stand-in for the API,
                no network, no key; it prints the most the real run can cost
    (default)   the real run: needs ANTHROPIC_API_KEY in the environment, spends real money, and
                asks for --yes after printing the most it can cost

The global cap (`--total-usd`, default 2.00) is never passed: see `evaluation.py`.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TextIO

import anthropic
import duckdb
import httpx2

from intent_analyst.agent import DEFAULT_LIMITS, MAX_TOKENS, Limits, Outcome, ask
from intent_analyst.db import build_database, open_readonly
from intent_analyst.evaluation import (
    OVERHEAD_TOKENS,
    Run,
    question_reserve,
    run_evaluation,
    select,
    worst_response_usd,
)
from intent_analyst.pricing import PRICES
from intent_analyst.questions import QUESTIONS_PATH, Question, load_questions

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_TOTAL_USD = Decimal("2.00")
KEY_VARIABLE = "ANTHROPIC_API_KEY"
ROOT = Path(__file__).resolve().parents[2]


def worst_case_client(
    model: str, limits: Limits, seen: list[bytes] | None = None
) -> anthropic.Anthropic:
    """A client whose every response is the most expensive one the limits allow: a harmless tool
    call that reports the largest input as cache writes and a full `max_tokens` of output.
    `seen` collects the body of every request, for tests."""
    counter = iter(range(1, 10**9))

    def handler(request: httpx2.Request) -> httpx2.Response:
        n = next(counter)
        if seen is not None:
            seen.append(request.content)
        body: dict[str, Any] = {
            "id": f"msg_dry_{n:020d}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_dry_{n:018d}",
                    "name": "list_intents",
                    "input": {},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": MAX_TOKENS,
                "cache_creation_input_tokens": limits.max_request_bytes + OVERHEAD_TOKENS,
                "cache_read_input_tokens": 0,
            },
        }
        return httpx2.Response(200, json=body)

    return anthropic.Anthropic(
        api_key="dry-run-not-a-key",
        base_url="http://dry-run.invalid",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def plan_lines(
    model: str, limits: Limits, total: Decimal, questions: Sequence[Question]
) -> list[str]:
    """What the run can cost at most, in words and numbers."""
    response = worst_response_usd(model, limits)
    reserve = question_reserve(model, limits)
    every = reserve * len(questions)
    return [
        f"model: {model}",
        f"questions selected: {len(questions)}",
        f"most one response can cost: ${response:.4f} "
        f"({limits.max_request_bytes + OVERHEAD_TOKENS} input tokens as cache writes, "
        f"{MAX_TOKENS} output tokens)",
        f"most one question can cost: ${reserve:.4f} (cap ${limits.max_usd} plus one response)",
        f"if every question hit its worst case: ${every:.2f}",
        f"global cap: ${total} (a question starts only if that much is left)",
        f"most the run can cost: ${min(every, total):.2f}",
    ]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m intent_analyst.evaluate", description=__doc__)
    p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES))
    p.add_argument("--total-usd", type=Decimal, default=DEFAULT_TOTAL_USD, help="global cap")
    p.add_argument("--question-usd", type=Decimal, default=DEFAULT_LIMITS.max_usd)
    p.add_argument("--per-block", type=int, help="stratified sample: this many per block")
    p.add_argument("--count", type=int, help="run at most this many questions, spread over blocks")
    p.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    p.add_argument("--dry-run", action="store_true", help="rehearse against a stand-in, no network")
    p.add_argument("--yes", action="store_true", help="spend real money without asking again")
    return p.parse_args(argv)


def _summary(run: Run) -> list[str]:
    lines = [
        f"questions run: {len(run.runs)}, not run: {len(run.not_run)}",
        f"counted spend: ${run.spent:.4f} of the ${run.total_cap} cap",
    ]
    if run.notice:
        lines.append(run.notice)
    return lines


def _with_database(work: Path) -> duckdb.DuckDBPyConnection:
    db_path = work / "synthea.duckdb"
    build_database(ROOT / "data" / "synthea", db_path)
    return open_readonly(db_path)


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    environ = os.environ if environ is None else environ
    out, err = out or sys.stdout, err or sys.stderr
    args = _parse(argv)
    key = environ.get(KEY_VARIABLE)
    if not args.dry_run and not key:
        print(
            f"Refusing to run: {KEY_VARIABLE} is not set in the environment. Nothing was sent. "
            "Use --dry-run to rehearse without a key.",
            file=err,
        )
        return 2
    try:
        limits = Limits(max_usd=args.question_usd)
        if args.total_usd <= 0 or args.question_usd <= 0:
            raise InvalidOperation
        questions = select(load_questions(args.questions), args.per_block, args.count)
    except (InvalidOperation, ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Bad arguments or question file: {error!r}", file=err)
        return 2
    print("\n".join(plan_lines(args.model, limits, args.total_usd, questions)), file=out)
    if not args.dry_run and not args.yes:
        print("This will spend real money. Run again with --yes to go ahead.", file=err)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        con = _with_database(Path(tmp))
        try:
            client = (
                worst_case_client(args.model, limits)
                if args.dry_run
                else anthropic.Anthropic(api_key=key, max_retries=0)
            )

            def ask_one(question: Question) -> Outcome:
                return ask(
                    question.question, client=client, con=con, model=args.model, limits=limits
                )

            started = time.monotonic()
            run = run_evaluation(
                questions, ask_one, model=args.model, limits=limits, total_cap=args.total_usd
            )
        finally:
            con.close()
    label = "rehearsal against the worst-case stand-in" if args.dry_run else "real run"
    print(f"{label}, {time.monotonic() - started:.1f} s", file=out)
    print("\n".join(_summary(run)), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
