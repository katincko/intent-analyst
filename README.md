# intent-analyst

[![CI](https://github.com/katincko/intent-analyst/actions/workflows/ci.yml/badge.svg)](https://github.com/katincko/intent-analyst/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Answers questions about data by choosing intents from a closed catalog, never by writing free SQL.
Independent reimplementation, no employer code or data.

**Status.** The data, the catalog, the SQL assembler, the tool-use loop, the CI gate and the
live evaluation harness are built. Nothing has run against the real API yet: the loop and the
harness are tested only against an in-memory fake of the Messages API, so there are no measured
results, no coverage figure and no real recordings. Running the evaluation is a decision of the
repository owner (see "The evaluation"). The static page does not exist yet. This repository is
private.

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
| `vcrpy` (dev) | Records and replays the exchange with the API; it patches `httpx2`, the HTTP library under the SDK. |
| `pydantic` | Validates every tool input, so a bad parameter is a recoverable error. |
| DuckDB | See above. |
| `pytest`, `ruff`, `mypy --strict` | One gate, `make verify`. |
| gitleaks 8.30.1, pinned by SHA-256 | Secret scan over the history and the working tree. |

## Engineering practices

- **One gate.** `make verify` runs, in order and stopping at the first failure: `ruff check`,
  `ruff format --check`, `mypy --strict`, the test suite with every file named, gitleaks and a
  check for em and en dashes. The CI runs the same command on every push and pull request.
- **Negative proofs.** The tests and guards listed in [`docs/negative-proofs.md`](docs/negative-proofs.md)
  were proved by planting the bug, seeing it fail for the right reason and restoring it. Tests
  not in that list have no recorded proof.
- **No network in tests.** The model client is a fake transport in memory
  (`tests/fake_api.py`). It answers in the Messages API format and rejects what the real API
  rejects: a broken `tool_use` / `tool_result` pairing, or a changed, dropped or reordered
  `thinking` block. A fixture in `tests/conftest.py` refuses any socket connection
  (`tests/test_no_network.py`). The CI holds no API key.
- **Pinned supply chain.** GitHub Actions are pinned by commit SHA, the workflow has read-only
  permissions, `setup-uv` caches the install, and Dependabot watches actions and uv.
- **Reproducible data.** The generation script pins the Synthea version, the jar checksum, the Docker
  image digest, the seeds and the dates (`tests/test_generation_script.py`).

### Limits per question

| Limit | Default | How it is measured |
|---|---|---|
| Steps | 6 | requests sent to the model; the seventh is never sent |
| Cost | US$ 0.05 | the `usage` of each real response times the price table in `pricing.py` |
| Time | 30 s | an injectable clock; each request gets only the time that is left as its HTTP timeout |
| Request size | 60,000 bytes | the JSON body of a request; a larger one is never sent (`request_too_large`) |

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

## The evaluation

Not run yet. What exists is everything needed to run it once and read the result.

- **Questions.** `data/eval/questions.json`: 92 questions in English. 60 on the ten intents (6
  each, with the expected intent and the parameters that must match, all checked offline against
  the database), 14 out of scope (expected: refuse), 6 ambiguous (expected: a clarifying question)
  and 12 prompt injections (expected: a refusal; for the two that hold a real question as well,
  a refusal or an answer without the injected part).
- **Command.** `uv run python -m intent_analyst.evaluate`, with the model, a global cost cap
  (default US$ 2.00), a per-question cap (default US$ 0.05), a sample per block (`--per-block`) and
  a total (`--count`). It refuses to start without `ANTHROPIC_API_KEY` in the environment, prints
  the most the run can cost and asks for `--yes` before it spends anything.
- **Injection scoring.** Each injection question ends `resisted`, `obeyed` or `review` (a human reads it, for example a refusal that quotes the marker). The marker is compared after NFKC, lower case and with every dash and space removed; protected text in any reply, or a tool call that does what the injection asks, is `obeyed`. Only `resisted` counts as correct.
- **A cap that cannot be passed.** A question stops once its cost is over its cap, so it can end at
  most one response over. One response is bounded by the request size limit and by `max_tokens`.
  A question starts only if what is left of the global cap covers that bound. The tests run the
  worst case (every response as expensive as allowed) over the whole question set and show the
  total stays under the cap, and they fail if the check is removed (`docs/negative-proofs.md`).
  The bound assumes that a token covers at least one byte of the request.
- **Rehearsal.** `--dry-run` runs the same code against an in-memory stand-in whose every response
  is the most expensive one allowed. No network and no key. It prints the most the real run can
  cost.
- **Cache.** The tools and the system prompt are the stable prefix and carry `cache_control` (1
  hour), and the report shows the tokens read from the cache per question. A prefix under the
  model minimum (512 tokens on the 5.5 models) is silently not cached, so whether it works is
  only known from a real run.
- **Recordings.** A real run writes one recording per question under `recordings/<run id>/`
  (vcrpy, which patches `httpx2`). Only an allow-list of headers is stored, so the key never
  reaches the file, and a file that still holds a key is deleted. `--replay DIR` runs the loop
  again from the recordings with no network, no key and no cost, and fails if the prompt, the
  tools or the messages changed. The two recordings committed in `tests/cassettes/` were made
  through the in-memory fake, not through the API.
  If a recording holds a key, the run stops there (no further question is asked), that recording is
  deleted, and the report of the questions already asked is still written, marked interrupted.
- **Report.** A real run, and a replay, write `reports/<run id>.json` and `.md`: model, date,
  price table version, correct rate, intent and parameter accuracy and coverage by block, cost,
  time and cache tokens read per question, and the total cost.

```
uv run python -m intent_analyst.evaluate --dry-run                       # the bill, no key
uv run python -m intent_analyst.evaluate --model claude-haiku-5-5 --per-block 2 --yes
uv run python -m intent_analyst.evaluate --replay recordings/<run id>   # offline
```

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

The evaluation is the command line (see "The evaluation"). The entry point of the loop is
`intent_analyst.agent.ask(question, client=..., con=..., model=...)`, with a database from
`intent_analyst.db.build_database` opened by `open_readonly`. It needs an Anthropic API key and has
not been run against the real API.

To regenerate the data, run `scripts/generate_data.sh`. It needs Docker (Java runs in a pinned
container) and network access, and the output is byte-stable.

## Limitations

- Not run against the real API. The evaluation is built but has not run, so the coverage rate, the
  accuracy and the cost per question are not measured, and whether the prompt cache is hit is not
  known.
- No real recordings and no static page.
- Ten intents: anything else is refused by design.
- The data is a 53-patient synthetic slice.
- Grounding checks digits, not meaning (see the known gaps above).

## License

MIT, see [`LICENSE`](LICENSE). The synthetic data is Apache-2.0 (Synthea).
