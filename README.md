# intent-analyst

Answers questions about data by choosing intents from a closed catalog, never by writing free SQL.
Independent reimplementation, no employer code or data. Work in progress: this batch holds the
data, the intent catalog (`CATALOG.md`) and the SQL assembler. The LLM loop comes next.

## Data

`data/synthea/` is a 53-patient slice of synthetic [Synthea](https://github.com/synthetichealth/synthea)
v4.0.0 output (seed 42, reference date 2026-01-01), licensed Apache-2.0 (see the LICENSE of the official repository).
Regenerate it with `scripts/generate_data.sh` (needs Docker; Java runs in a container).

## Checks

```
uv run pytest tests -q
uv run ruff check . && uv run mypy
```
