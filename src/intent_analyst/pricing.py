"""Versioned price table. The cost cap is computed from the `usage` the API reports, never from
an estimate of the prompt."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

# Bump when a price below changes, and say where it was read.
PRICE_TABLE_VERSION = "2026-10-16"
# Source: https://platform.claude.com/docs/en/about-claude/pricing (read on the version date).

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens."""

    input: Decimal
    cache_write: Decimal  # the 1 hour price, so a cache write is never under-counted
    cache_read: Decimal
    output: Decimal


def _price(input_: str, cache_write: str, cache_read: str, output: str) -> Price:
    return Price(Decimal(input_), Decimal(cache_write), Decimal(cache_read), Decimal(output))


PRICES: dict[str, Price] = {
    "claude-opus-5-5": _price("4", "8", "0.20", "20"),
    "claude-sonnet-5-5": _price("2", "4", "0.10", "10"),
    "claude-haiku-5-5": _price("0.10", "0.20", "0.01", "0.50"),
}
# Haiku 5.5 prompts over 100,000 tokens are billed at a higher rate.
_LONG_PROMPT = 100_000
_HAIKU_LONG = _price("0.50", "1", "0.05", "2.50")


class Usage(Protocol):
    """The fields of the Messages API `usage` object that the cost needs."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


def cost_usd(model: str, usage: Usage) -> Decimal:
    """Cost of one response. An unknown model raises: a missing price must not mean free."""
    if model not in PRICES:
        raise KeyError(f"no price for model {model!r} in table {PRICE_TABLE_VERSION}")
    created = usage.cache_creation_input_tokens or 0
    read = usage.cache_read_input_tokens or 0
    price = PRICES[model]
    if model == "claude-haiku-5-5" and usage.input_tokens + created + read > _LONG_PROMPT:
        price = _HAIKU_LONG
    total = (
        usage.input_tokens * price.input
        + created * price.cache_write
        + read * price.cache_read
        + usage.output_tokens * price.output
    )
    return total / _MILLION
