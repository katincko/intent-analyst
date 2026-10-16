# intent-analyst

Answers questions about data by choosing intents from a closed catalog, never by writing free SQL.
Independent reimplementation, no employer code or data. Work in progress: this batch holds the
data, the intent catalog (`CATALOG.md`) and the SQL assembler. The LLM loop comes next.

## Data

`data/synthea/` is a 53-patient slice of synthetic [Synthea](https://github.com/synthetichealth/synthea)
v4.0.0 output (seed 42, reference and end date 2026-01-01), licensed Apache-2.0
([Synthea LICENSE](https://github.com/synthetichealth/synthea/blob/v4.0.0/LICENSE)).
The data is 100% synthetic: no real person is in it. The identification columns (SSN, driver's
license, passport, address, coordinates, zip, maiden name) are never loaded into the database.

Regenerate it with `scripts/generate_data.sh`. Generation depends on Docker (Java runs in a
container, pinned by image digest) and on network access (the Synthea jar and the image are
downloaded; the jar is checked against a pinned SHA-256). The output is byte-stable.

## Checks

```
uv run pytest tests -q
uv run ruff check . && uv run mypy
```
