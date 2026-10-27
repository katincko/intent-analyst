"""The price table is pinned to the values read from the official pricing page.

Source: https://platform.claude.com/docs/en/about-claude/pricing (read on 2026-10-27). A change
to a price must change these numbers on purpose, together with PRICE_TABLE_VERSION.
"""

from decimal import Decimal
from types import SimpleNamespace

from intent_analyst.pricing import PRICE_TABLE_VERSION, PRICES, PRICES_READ_ON, cost_usd

# input, 1 hour cache write, cache read, output (US dollars per million tokens).
EXPECTED = {
    "claude-opus-5-5": ("4", "8", "0.20", "20"),
    "claude-sonnet-5-5": ("2", "4", "0.10", "10"),
    "claude-haiku-5-5": ("0.10", "0.20", "0.01", "0.50"),
}


def _usage(
    input_tokens: int = 0, output: int = 0, created: int = 0, read: int = 0
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output,
        cache_creation_input_tokens=created,
        cache_read_input_tokens=read,
    )


def test_every_price_is_the_one_on_the_official_page() -> None:
    assert set(PRICES) == set(EXPECTED)
    for model, (i, w, r, o) in EXPECTED.items():
        price = PRICES[model]
        assert (price.input, price.cache_write, price.cache_read, price.output) == (
            Decimal(i),
            Decimal(w),
            Decimal(r),
            Decimal(o),
        ), model


def test_cache_read_is_five_percent_of_input_on_opus_and_sonnet() -> None:
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        assert PRICES[model].cache_read == PRICES[model].input * Decimal("0.05")


def test_tokens_of_each_kind_cost_the_listed_price() -> None:
    # 100,000 tokens is the largest prompt still on the base rate of Haiku, so use a tenth.
    tenth = 100_000
    for model, (i, w, r, o) in EXPECTED.items():
        assert cost_usd(model, _usage(input_tokens=tenth)) == Decimal(i) / 10
        assert cost_usd(model, _usage(created=tenth)) == Decimal(w) / 10
        assert cost_usd(model, _usage(read=tenth)) == Decimal(r) / 10
        assert cost_usd(model, _usage(output=tenth)) == Decimal(o) / 10


def test_haiku_prompt_over_100k_tokens_pays_the_long_rate_with_cache_counted() -> None:
    at_limit = _usage(input_tokens=50_000, read=50_000, output=1_000_000)
    assert cost_usd("claude-haiku-5-5", at_limit) == Decimal("0.005") + Decimal("0.0005") + Decimal(
        "0.50"
    )
    over = _usage(input_tokens=50_000, read=50_001, output=1_000_000)
    expected = Decimal("0.025") + Decimal("50001") * Decimal("0.05") / 1_000_000 + Decimal("2.50")
    assert cost_usd("claude-haiku-5-5", over) == expected


def test_the_table_records_where_and_when_it_was_read() -> None:
    assert PRICE_TABLE_VERSION == "2026-10-16"
    assert PRICES_READ_ON == "2026-10-27"
