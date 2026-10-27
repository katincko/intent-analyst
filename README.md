# intent-analyst

[![CI](https://github.com/katincko/intent-analyst/actions/workflows/ci.yml/badge.svg)](https://github.com/katincko/intent-analyst/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Answers questions about data by choosing intents from a closed catalog, never by writing free SQL.
Independent reimplementation, no employer code or data.

**Status.** The data, the catalog, the SQL assembler, the tool-use loop and the CI gate are built.
The loop has only been tested against an in-memory fake of the Messages API: it has not run
against the real API. The live evaluation, the recorded runs, the coverage table and the static
page do not exist yet. This repository is private.

## The problem

Letting an LLM write free SQL is the most common way to "chat with your data", and the most fragile:
injection, a wrong query, an invented number and no ceiling on cost. Here the model only picks
intents from a catalog of ten, finds real values in the data, and composes up to three intents per
question. The code builds and runs every query, and no number reaches the user unless it is in a
returned table.

## The solution

The model never sees SQL. It gets six tools, and the code runs each of them:

| Tool | What it does |
|---|---|
| `list_intents` | Returns the catalog with parameters and an example per intent. |
| `search_values` | Finds real names in the data (condition, medication, medication reason, procedure, encounter, patient name) by a bound, case-insensitive search. |
| `run_intent` | Validates the parameters, assembles the SQL, runs it on the locked read-only database and returns a table with a `result_id`. Up to 3 per question. |
| `ask_clarification` | Ends the turn with a question to the user. |
| `answer` | Ends the turn with a text and the `result_id`s it rests on. |
| `refuse` | Ends the turn when the catalog cannot answer. |

```
question
   |
   v
tool-use loop (official anthropic SDK, agent.py)
   |   limits per question: 6 steps, US$ 0.05, 30 s
   |
   +--> list_intents / search_values --> catalog and bound search
   |
   +--> run_intent --> Pydantic validation (closed enums for sort and group columns)
   |                     --> SQL template with bound parameters (catalog.py)
   |                     --> DuckDB: read only, no external access, locked configuration (db.py)
   |
   +--> answer --> grounding check: every number must be in a cited table (grounding.py)
                    rejected --> back to the model as a tool error
                    accepted --> text and tables
```

The catalog is in [`CATALOG.md`](CATALOG.md), generated from `catalog.py`; a test fails if it is stale.

## Decisions and trade-offs

**Why not text-to-SQL.** Free SQL from a model can be steered by the text it reads into
a query nobody reviewed. With a closed catalog, a malicious value is a bound
parameter and a sort column is a member of an enum, so the query shape is fixed by the code.
`tests/test_assembler.py` plants hostile values and columns outside the enum, and checks that no
intent writes. The price is coverage: a question outside the ten intents ends in `refuse`. The
coverage rate is meant to be measured by the live evaluation, which has not run.

**Why a hand-written loop.** The loop is about 400 lines on the official SDK, with no framework.
That keeps the three things that matter in plain sight: every `tool_use` gets a `tool_result` with
the same id (`tests/test_agent_contract.py`), the limits are checked in one place
(`tests/test_limits.py`), and the cost comes from the `usage` the API reports times a versioned
price table (`pricing.py`). SDK retries are off, so a failed call is reported once and cannot spend
more money or time in the background.

**Why DuckDB.** It runs inside the process on a file, so there is no server to secure. It can be
opened read-only, with external access off, memory and threads capped, extension loading off and
the configuration locked (`db.py`). `tests/test_database.py` checks each of these, including that
the caps cannot be raised from SQL.

**Known cost of the design.** `top_conditions` leaves out condition names without a SNOMED
suffix (see the first section of `CATALOG.md`). Grounding does not check numbers written as words.
Both are stated in this repository on purpose.

## Stack

| Piece | Why |
|---|---|
| Python 3.12 with `uv` and a committed `uv.lock` | Reproducible installs, the same command locally and in CI. |
| `anthropic` SDK | The official client; the loop is written on top of it. |
| `pydantic` | Validates every tool input, so a bad parameter is a recoverable error. |
| DuckDB | See above. |
| `pytest`, `ruff`, `mypy --strict` | One gate, `make verify`. |
| gitleaks 8.30.1, pinned by SHA-256 | Secret scan over the history and the working tree. |

## Engineering practices

- **One gate.** `make verify` runs, in order and stopping at the first failure: `ruff check`,
  `ruff format --check`, `mypy --strict`, the test suite with every file named, gitleaks and a
  check for em and en dashes. The CI runs the same command on every push and pull request.
- **Negative proofs.** Every test or guard was proved by planting the bug, seeing it fail for the
  right reason and restoring it. The list is in [`docs/negative-proofs.md`](docs/negative-proofs.md).
- **No network in tests.** The model client is a fake transport in memory
  (`tests/fake_api.py`). It answers in the Messages API format and rejects what the real API
  rejects: a broken `tool_use` / `tool_result` pairing, or a changed, dropped or reordered
  `thinking` block. A fixture in `tests/conftest.py` refuses any socket connection
  (`tests/test_no_network.py`). The CI holds no API key.
- **Pinned supply chain.** GitHub Actions are pinned by commit SHA, the workflow has read-only
  permissions, `setup-uv` caches the install, and Dependabot watches actions and pip.
- **Reproducible data.** The generation script pins the Synthea version, the jar checksum, the Docker
  image digest, the seeds and the dates (`tests/test_generation_script.py`).

### Limits per question

| Limit | Default | How it is measured |
|---|---|---|
| Steps | 6 | requests sent to the model; the seventh is never sent |
| Cost | US$ 0.05 | the `usage` of each real response times the price table in `pricing.py` |
| Time | 30 s | an injectable clock; each request gets only the time that is left as its HTTP timeout |

Each limit ends the question with a clear message and a `stop_reason`. Cost and time are checked
after a response arrives, so a question can end at most one response over them. Cost and time are
maxima: a final reply that lands exactly on US$ 0.05 or on 30.0 s is accepted, and 30.1 s is not. A
reply that is not final and lands exactly on 30.0 s ends the question before the next request.
A reply without a tool call is a protocol failure and gets one new attempt, counted in the outcome.
A tool error goes back to the model as a `tool_result` with `is_error`, at most 3 per question.

### Grounding

Every number in an `answer` must be a cell of a table that answer cites, or a number the user typed
in the question. A rejected answer goes back to the model as a tool error naming the numbers, so it
can quote the table or say it in words. Verbatim quotes of text cells (such as
`albuterol 5 MG/ML Inhalation Solution`) are masked before the scan, so their digits cannot be
borrowed by another claim. A number glued to a letter (`99k`, `1e3`, `2nd`) is rejected whole.
The tests are in `tests/test_grounding.py`.

Known gaps: numbers written as words ("three") are not checked; a number the user typed may be
repeated even if it does not describe the result; a patient id copied from one table into the next
call is validated by the catalog but not traced to its origin.

## Data and privacy

`data/synthea/` is a 53-patient slice of synthetic [Synthea](https://github.com/synthetichealth/synthea)
v4.0.0 output (seed 42, reference and end date 2026-01-01), licensed Apache-2.0
([Synthea LICENSE](https://github.com/synthetichealth/synthea/blob/v4.0.0/LICENSE)). The data is
100% synthetic: no real person is in it. The identification columns (SSN, driver license,
passport, address, coordinates, zip, maiden name) are never loaded into the database
(`tests/test_database.py`).

With real health data this would need much more, and none of it is built: a legal basis and
consent handling (HIPAA or GDPR, depending on where the patients are), access control and an audit
log per question, de-identification before the data reaches any model, a review of what each tool
result sends to a hosted API, small-cell suppression so that a count of one cannot identify a
person, and a retention rule for the questions. The closed catalog helps, because every possible
query is known and reviewable in advance, but it is not a substitute for those controls.

## How to run

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12.

```
uv sync --locked
make verify
```

`make` is only a wrapper. Without it, run the same steps by hand:

```
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/test_agent_contract.py tests/test_assembler.py ... -q   # every tests/test_*.py, named
bash scripts/gitleaks.sh
uv run python scripts/check_dashes.py
```

`make test` names every `tests/test_*.py` file on the command line instead of passing the
directory. The suite makes no network call and needs no API key.

There is no command line yet. The entry point is `intent_analyst.agent.ask(question, client=...,
con=..., model=...)`, with a database from `intent_analyst.db.build_database` opened by
`open_readonly`. It needs an Anthropic API key and has not been run against the real API.

To regenerate the data, run `scripts/generate_data.sh`. It needs Docker (Java runs in a pinned
container) and network access, and the output is byte-stable.

## Limitations

- Not run against the real API. No evaluation exists, so the coverage rate is not measured.
- No command line, no recorded runs, no static page.
- Ten intents: anything else is refused by design.
- The data is a 53-patient synthetic slice.
- Grounding checks digits, not meaning (see the known gaps above).

## License

MIT, see [`LICENSE`](LICENSE). The synthetic data is Apache-2.0 (Synthea).
