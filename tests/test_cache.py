"""V4: the stable prefix (tools and system prompt) carries the cache breakpoint and is the same
bytes for every question, so the API can read it from the cache.

Docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching. The cache covers the
prompt in the order tools, system, messages, up to the block that has `cache_control`; a prefix
under the model minimum (512 tokens for Sonnet 5.5, Haiku 5.5 and Opus 5.5) is silently skipped.
"""

import json
from typing import Any

import duckdb
from fake_api import FakeMessagesApi, Reply, message, tool_use, usage

from intent_analyst.agent import SYSTEM_BLOCKS, SYSTEM_PROMPT, ask
from intent_analyst.tools import tool_definitions

MODEL = "claude-sonnet-5-5"
BREAKPOINT = {"type": "ephemeral", "ttl": "1h"}


def refuse_reply(token_usage: dict[str, Any] | None = None) -> Reply:
    return Reply(message([tool_use("refuse", {"reason": "No."})], token_usage=token_usage))


def requests_for(con: duckdb.DuckDBPyConnection, questions: list[str]) -> list[dict[str, Any]]:
    api = FakeMessagesApi([refuse_reply() for _ in questions])
    for question in questions:
        ask(question, client=api.client(), con=con, model=MODEL, clock=api.clock)
    return api.requests


def test_the_system_block_carries_the_one_breakpoint(con: duckdb.DuckDBPyConnection) -> None:
    (request,) = requests_for(con, ["How many patients?"])
    assert request["system"] == [
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": BREAKPOINT}
    ]
    assert request["system"] == SYSTEM_BLOCKS
    assert "cache_control" not in json.dumps(request["tools"])
    assert "cache_control" not in json.dumps(request["messages"])
    assert "cache_control" not in request  # no top-level automatic caching


def test_the_prefix_is_the_same_bytes_for_every_question(con: duckdb.DuckDBPyConnection) -> None:
    first, second, third = requests_for(
        con, ["How many patients?", "Delete everything.", "What is the weather in Boston?"]
    )

    def prefix(request: dict[str, Any]) -> bytes:
        return json.dumps([request["tools"], request["system"]], sort_keys=False).encode()

    assert prefix(first) == prefix(second) == prefix(third)
    assert prefix(first) == json.dumps([tool_definitions(), SYSTEM_BLOCKS]).encode()
    assert first["messages"] != second["messages"]  # only the messages differ


def test_the_prefix_is_long_enough_to_be_cached_by_a_rough_count() -> None:
    # No token count is available without a call to the API, so this is only a floor: at 6
    # characters per token (JSON and English are denser than that) the prefix is over 512 tokens.
    # The evaluation report shows the tokens really read from the cache for each question.
    size = len(json.dumps([tool_definitions(), SYSTEM_BLOCKS]))
    assert size / 6 > 512, size


def test_the_outcome_reports_the_tokens_written_and_read_from_the_cache(
    con: duckdb.DuckDBPyConnection,
) -> None:
    cache = usage(100, 50, cache_creation_input_tokens=900, cache_read_input_tokens=0)
    read = usage(100, 50, cache_creation_input_tokens=0, cache_read_input_tokens=900)
    api = FakeMessagesApi([refuse_reply(cache), refuse_reply(read)])
    first = ask("Q1?", client=api.client(), con=con, model=MODEL, clock=api.clock)
    second = ask("Q2?", client=api.client(), con=con, model=MODEL, clock=api.clock)
    assert (first.cache_write_tokens, first.cache_read_tokens) == (900, 0)
    assert (second.cache_write_tokens, second.cache_read_tokens) == (0, 900)
    assert (second.input_tokens, second.output_tokens) == (100, 50)
