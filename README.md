# intent-analyst

Answers questions about data by choosing intents from a closed catalog, never by writing free SQL.
Independent reimplementation, no employer code or data. Work in progress: this batch holds the
data, the intent catalog (`CATALOG.md`), the SQL assembler and the tool-use loop. The loop has
been tested only against an in-memory fake of the Messages API. It has not run against the real
API yet, and the live evaluation is not built.

## How a question is answered

The model never sees SQL. It gets six tools and the code runs each of them:

| Tool | What it does |
|---|---|
| `list_intents` | Returns the catalog with parameters and an example per intent. |
| `search_values` | Finds real names in the data (condition, medication, procedure, encounter, patient name) by a bound, case-insensitive search. |
| `run_intent` | Validates the parameters, assembles the SQL, runs it on the locked read-only database and returns a table with a `result_id`. Up to 3 per question. |
| `ask_clarification` | Ends the turn with a question to the user. |
| `answer` | Ends the turn with a text and the `result_id`s it rests on. |
| `refuse` | Ends the turn when the catalog cannot answer. |

The loop is hand written on the official `anthropic` SDK (no framework), in `agent.py`. The model
runs in automatic mode: no `tool_choice` is sent, because current models reject a forced tool.
A reply without a tool call is a protocol failure and gets one new attempt, counted in the
outcome. A tool error (bad parameters, unknown intent, a query that fails) goes back to the model
as a `tool_result` with `is_error`, at most 3 per question.

### Limits per question

| Limit | Default | How it is measured |
|---|---|---|
| Steps | 6 | requests sent to the model; the seventh is never sent |
| Cost | US$ 0.05 | the `usage` of each real response times the versioned price table in `pricing.py` |
| Time | 30 s | an injectable clock; each request gets only the time that is left as its HTTP timeout |

Each limit ends the question with a clear message and a `stop_reason`. The cost and time limits
are checked after a response arrives, so a question can finish at most one response over them.
SDK retries are off, so a failed call is reported once and cannot spend more money or time in
the background.

### Grounding

Every number in an `answer` must be a cell of a table that answer cites, or a number the user
typed in the question. A rejected answer goes back to the model as a tool error naming the
numbers, so it can quote the table or say it in words. Verbatim quotes of text cells (a
medication such as `albuterol 5 MG/ML Inhalation Solution`, a year bucket, a date) are masked
before the scan, so the digits inside them cannot be borrowed by another claim.

Known gaps: numbers written as words ("three") are not checked; a number the user typed may be
repeated even if it does not describe the result; a parameter the model copies from one table
into the next call (a patient id) is validated by the catalog but not traced to its origin.

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

`make test` runs the same suite but names every test file on the command line (no directory
argument), and `make lint` runs ruff and mypy.

The suite makes no network call. The model client in the tests is a fake transport in memory
(`tests/fake_api.py`) that answers in the real Messages API format and rejects, like the real
API, a request that breaks the tool_use / tool_result rules. A guard in `tests/conftest.py`
refuses any socket connection. `docs/negative-proofs.md` lists, for every guard, the bug that was
planted and the failure it caused.
