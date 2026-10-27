# Negative proofs

Each test or guard below was proved by planting the bug, seeing the suite fail for the stated
reason, restoring the code and seeing it pass again. Command used for the red runs:
`uv run pytest tests/<file>.py -q` (file named explicitly). All runs were on the fixed slice
(seed 42, end date 2026-01-01).

## Result tests (`tests/test_results.py`)

Each expected value is recomputed in plain Python from the CSV files, never copied from the code
under test.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| M1 | `Direction.desc` renders `ASC` | 39 failed: rankings come back reversed | yes |
| M2 | `alive` filter written as `deathdate IS NOT NULL` | 9 failed: alive count 3 instead of 50 | yes |
| M3 | percent computed with `10.0 *` instead of `100.0 *` | 13 failed: 5.5 instead of 54.7 | yes |
| M4 | age band ignores the death date (`coalesce` removed) | 7 failed: deceased patients land in the wrong bands | yes |
| M5 | `order_by` patients and occurrences swapped | 13 failed: ranking by the other column | yes |
| M6 | `reason_description <> $reason` | 4 failed: medications of other reasons returned | yes |
| M7 | `year(start_date) <> $year` | 19 failed: procedures of other years returned | yes |
| M8a | procedures `avg_cost` computed with `sum` | 10 failed: 333472.2 instead of 431.4 | yes |
| M8b | encounters `avg_cost` computed with `sum` | 17 failed: 3398362.24 instead of 3067.11 | yes |
| M9 | `deathdate` loaded from `BIRTHDATE` | 22 failed: dates differ from the CSV | yes |
| N1 | default category filter replaced by `TRUE` | 8 failed: findings and situations in the diagnoses | yes |
| N2 | `finding` category matches `(situation)` | 5 failed: wrong rows for the category | yes |
| N3 | procedures example back to the tied 2015 case | 1 failed: `[1, 1, 1, 1, 1]` is not strictly decreasing | yes |
| N4 | a condition row dated 2026-03-01 | 1 failed: event after the end date | yes |

## Generation script (`tests/test_generation_script.py`)

| # | Planted bug | Red result | Green after restore |
|---|---|---|---|
| S1 | `-e` removed | `-e "\$END_DATE"` not in the command | yes |
| S2 | `-s` removed | `-s "\$SEED"` not in the command | yes |
| S3 | `-cs` removed | `-cs "\$CLINICIAN_SEED"` not in the command | yes |
| S4 | `-r` removed | `-r "\$REFERENCE_DATE"` not in the command | yes |
| S5 | `thread_pool_size=4` | `--generate\.thread_pool_size=1` not in the command | yes |
| S6 | image back to a mutable tag | digest pattern not found | yes |
| S7 | `END_DATE` set to another date | `END_DATE=$REFERENCE_DATE` not found | yes |
| S8 | jar checksum check replaced by `cat` | `sha256sum -c` missing | yes |

## DuckDB connection (`tests/test_database.py`)

| # | Planted bug | Red result | Green after restore |
|---|---|---|---|
| D1 | `memory_limit` not set | `25.0 GiB` instead of `244.1 MiB` | yes |
| D2 | `threads` not set | 8 instead of 2 | yes |
| D3 | autoload turned on | setting is `True`, expected `False` | yes |
| D4 | autoinstall turned on | setting is `True`, expected `False` | yes |
| D5 | `lock_configuration` removed | 7 failed: `SET` no longer refused as locked | yes |
| D6 | memory limit raised to 512MB | `488.2 MiB` instead of `244.1 MiB` | yes |
| D7 | `THREADS = 8` in `db.py` (the test used to import `THREADS`, so this survived; it now compares with the literal 2) | `assert 8 == 2` | yes |

## Loaded columns (`tests/test_results.py::test_every_loaded_column_matches_the_csv`)

Whole rows of each table are compared with the CSV recounted in Python. A column that is loaded
without an entry in the test table fails `test_every_loaded_column_has_an_expectation`.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| L1 | `city` read from `COUNTY` | 1 failed: the 53 patient rows differ in `city` | yes |
| L2 | `income` plus one | 1 failed: every income is off by one | yes |
| L3 | `conditions.start_date` read from `STOP` | 1 failed: empty dates instead of the start dates | yes |
| L4 | `conditions.encounter` read from `PATIENT` | 1 failed: the encounter ids are patient ids | yes |
| L5 | `first_name` read from `LAST` | 1 failed on patients, and the result tests that print names fail too | yes |

## Tie-breaks (`tests/test_results.py`)

Literal expectations. In the encounter cases the id order differs from the CSV file order and
from the alphabetical order of the descriptions.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| T1 | `patient_encounters` orders ties by `e.id DESC` | 3 failed: same-day encounters come back swapped | yes |
| T2 | `patient_encounters` has no id tie-break | 1 failed (the ascending case). The order of ties is then left to the engine, so this guard catches it only when the engine happens to pick another order | yes |
| T3 | `medications_for_condition` orders ties by `medication DESC` | 3 failed: `albuterol` before `60 ACTUAT Fluticasone`, and the cut keeps the wrong medications | yes |
| T4 | `medications_for_condition` has no name tie-break | 3 failed: the three tied cases come back in engine order | yes |

## Conditions without a suffix (`tests/test_results.py`)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| U1 | `disorder` category defined as "not finding and not situation" | 1 failed: `Dental caries` appears under `disorder`, expected an empty list | yes |

## Tool-use loop (batch 2)

All runs used `.venv/Scripts/python.exe -m pytest <files> -q` with the files named. The fake
Messages API (`tests/fake_api.py`) enforces the tool_use / tool_result rules of
https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls, and its own
guard test (`test_the_fake_rejects_a_broken_contract`) proves it refuses a missing result, a
wrong id, text before results and a missing next message. When the loop breaks a rule the fake
answers 400 and the loop reports `api_error`, which is why several contract rows below show
`'stopped' == 'answered'`.

### Contract (`tests/test_agent_contract.py`, spec test 1)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| A1 | only the first `tool_result` of a turn is sent back | 3 failed: the fake rejects a turn with several `tool_use` and one result | yes |
| A2 | the error result carries a wrong `tool_use_id` | 4 failed: the fake rejects the unmatched id | yes |
| A3 | `is_error` never set | 5 failed: `KeyError: 'is_error'` on every error result | yes |
| A4 | a turn cut by `max_tokens` with a `tool_use` gets text instead of results | 1 failed: the fake rejects the missing result | yes |
| A5 | protocol retries unbounded | 1 failed: expected stop `protocol`, got a third request nobody scripted (`api_error`) | yes |
| A6 | `tool_choice={"type": "any"}` sent | 1 failed: `tool_choice` found in the request body | yes |
| A7 | terminal tool runs beside another tool | 1 failed: the `alone` error is missing, the call ran | yes |
| A17 | fourth intent allowed | 1 failed: last result is not an error | yes |

### Limits (`tests/test_limits.py`, spec test 6)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| A8 | step limit `>` instead of `>=` | 2 failed: 7 requests instead of 6 | yes |
| A9 | cost stops at exactly the limit | 1 failed: a question at exactly US$ 0.05 is stopped | yes |
| A9b | cost limit doubled | 3 failed: `refused` instead of `max_cost` | yes |
| A10 | time limit never fires | 2 failed: `refused` instead of `timeout` | yes |
| A11 | request timeout fixed at 30 s | 1 failed: `[30, 30, 30]` instead of `[30, 18, 6]` | yes |
| A12 | SDK retries left on | 2 failed: a 429 and a read timeout are retried, so the scripted replies run out | yes |
| A13 | `usage` cost never added | 4 failed: no limit ever fires | yes |
| P1 | sonnet output price doubled | 2 failed: 0.061 instead of 0.0605, and the literal cost table | yes |
| P2 | haiku long-prompt tier dropped | 1 failed: 0.0100001 instead of 0.0500005 | yes |
| P3 | cache reads billed as input | 2 failed: 2 instead of 0.10 | yes |
| P4 | unknown model costs nothing | 1 failed: `DID NOT RAISE KeyError` | yes |

### Grounding (`tests/test_grounding.py`, spec test 5)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| A14 | grounding check skipped | 3 failed: the answer with the invented 53 is accepted | yes |
| A15 | unknown sources not checked | 1 failed: `KeyError: 'r9'` | yes |
| A16 | every table counts, not only cited ones | 1 failed: an answer citing the wrong table is accepted | yes |
| A19 | tool error limit never fires | 1 failed: expected `tool_errors`, got a request nobody scripted | yes |
| G1 | quotes of text cells not masked | 3 failed: `5` from `albuterol 5 MG/ML` is reported, and the borrowed `5` is not | yes |
| G2 | numbers of the question not allowed | 1 failed: the repeated `5` is reported | yes |
| G3 | booleans count as numbers | 1 failed: `InvalidOperation` on a boolean cell | yes |
| G4 | rounded numbers accepted | 1 failed: `About 51%` passes | yes |

### Search and network guard

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| S1 | search is case sensitive | 5 failed: upper case words find nothing | yes |
| S2 | no value tie-break | 3 failed: tied matches come back in another order | yes |
| S3 | limit ignored | 1 failed: 50 rows instead of 3 | yes |
| N1 | socket `connect` not guarded | 1 failed: a real `ConnectionRefusedError` instead of the guard | yes |
| N2 | `getaddrinfo` not guarded | 1 failed: `DID NOT RAISE AssertionError` | yes |

Weak spots, stated: A5 and A19 go red through the fake's "request nobody scripted" assertion
(the loop kept asking), which is the correct symptom of an unbounded retry or error limit but
not the loop's own stop reason. T2 above depends on the engine's tie order.

## Lot 4

Runs: `uv run pytest <file> -q`, files named. Bug planted, red copied, restored with `git checkout`, green again.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| Q1 | grounding scan back to the old rule (a number glued to a letter is not read as a number) | 9 failed: `assert [] == ['99k']`, same for `99x`, `1e3`, `2nd`, `v2`, and `['1.2'] == ['1.2.3']` | yes |
| Q2a | normal path: the ids of the first two results swapped | 2 failed (the new pairing test and the older ordering test): `['toolu_search', ...] == ['toolu_table', ...]` | yes |
| Q2b | normal path: every result carries the id of the first call | 4 failed: `'stopped' == 'answered'`, the fake answers 400 and the loop reports `api_error` | yes |
| Q2c | cut turn: results built from `reversed(calls)` | 1 failed (new, the only one that sees it): `['toolu_cut_c', ..., 'toolu_cut_a'] == ['toolu_cut_a', ..., 'toolu_cut_c']` | yes |
| Q2d | cut turn: every result carries the id of the first call | 1 failed: `'stopped' == 'refused'` (fake 400) | yes |
| Q3 | `agent.py` without the limit check right after a response is paid (the check before the next request stays) | 6 failed: `('answered', None) == ('stopped', 'max_cost')`, the same for `refused` and `clarification`, and the three time cases (`'timeout'`). The older limit tests stay green, because they use a non-final reply that the check before the next request still stops | yes |
| Q4a | post-response time check `>` changed to `>=` | 2 failed: a final reply at exactly 30.0 s is `stopped` instead of `refused`, and the non-final case gets "over the 30 s limit" instead of "no time left" | yes |
| Q4b | check before the next request `>=` changed to `>` | 1 failed: `('refused', None) == ('stopped', 'timeout')`, the loop sent a request with no time left | yes |
| Q5a | the loop rebuilds the assistant turn without its `thinking` and `redacted_thinking` blocks | 3 failed: `'stopped' == 'answered'` (normal path), and `'stopped' == 'refused'` for the cut turn and the tool-less turn; the fake answered 400 "cannot be modified" and the loop reported `api_error` | yes |
| Q5b | the loop drops only the `redacted_thinking` blocks | 2 failed: same 400, normal path and cut turn | yes |
| Q5c | the loop puts the `tool_use` block before the thinking blocks | 2 failed: `['tool_use', ...] == ['thinking', 'redacted_thinking', 'tool_use']` | yes |
| Q5d | the fake no longer checks thinking blocks | 7 failed: the seven `test_the_fake_rejects_a_changed_thinking_block` cases do not raise | yes |
| Q5e | stop message without the `request-id` header (test written first) | 1 failed before the fix: `'req_header_only' in "Stopped: ... Error code: 500 - {...}"`. The SDK message already holds the id when the body has it, so only a header-only id was missing | yes |

## Lot 5

Gates of `make verify`. Each one was proved by planting the failure, seeing it red, removing the
plant and seeing it green. Nothing planted was committed.

### Dash check (`scripts/check_dashes.py`, `tests/test_check_dashes.py`)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| D1 | an em dash (U+2014) appended to `README.md` | the script exits 1 with `README.md:87: em dash (U+2014)`, and `test_repository_has_no_dash` fails with `assert 1 == 0` | yes, `git checkout README.md` |
| D2 | `git ls-files` called without `--others --exclude-standard` | `test_untracked_unignored_file_is_checked` fails: the script exits 0 with `no em or en dash in 0 files` instead of 1 | yes, restored from a copy |
| D3 | a file that is not valid UTF-8 returns no problem, as before | `test_non_utf8_text_file_fails_with_message` fails: exit 0 with `no em or en dash in 1 files` instead of 1 | yes, restored from a copy |

### Secret scan (`scripts/gitleaks.sh`, gitleaks 8.30.1)

The binary is downloaded from the release page and its SHA-256 is compared with the value pinned in
the script before it runs. The script scans the history (`gitleaks git`) and the working tree
(`gitleaks dir`), so an uncommitted file is caught too.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| L1 | an untracked `planted_secret.txt` holding a fake AWS access key id (`AKIA` plus 16 characters) | the history scan says `no leaks found`, the tree scan says `leaks found: 1`, the script exits 1 | yes, file deleted, never added |

A first plant with a 9 in the key was not flagged, because the AWS rule only accepts the
characters A to Z and 2 to 7 after `AKIA`. The plant above matches the rule.

## Lot 6

Dependencies moved to `anthropic` 2.3.0 and `duckdb` 2.0.0. The proofs were repeated on the new
versions: bug planted, red copied, restored, green again. Test files named on the command line.

### DuckDB connection after 2.0.0 (`tests/test_database.py`)

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| D1 | `memory_limit` not set | 1 failed: `25.0 GiB` instead of `244.1 MiB` | yes |
| D2 | `threads` not set | 1 failed: 8 instead of 2 | yes |
| D3 | autoload turned on | 1 failed: setting is `True`, expected `False` | yes |
| D4 | autoinstall turned on | 1 failed: setting is `True`, expected `False` | yes |
| D5 | `lock_configuration` removed | 7 failed: `SET` no longer refused as locked | yes |
| D6 | memory limit raised to 512MB | 1 failed: `488.2 MiB` instead of `244.1 MiB` | yes |
| D7 | `THREADS = 8` | 1 failed: `assert 8 == 2` | yes |
| D8 | `enable_external_access` turned on | 9 failed: file and network reads no longer refused | yes |
| D9 | `read_only=False` | 5 failed: writes no longer refused | yes |

The new tests `test_a_failed_query_does_not_poison_the_connection` and
`test_the_poisoning_exists_inside_an_explicit_transaction` pin the 2.0 rule that a failed
`execute()` aborts its transaction: the first shows the agent's autocommit connection keeps
working after a failed tool query, the second shows the same failure does poison a connection
inside `BEGIN`.

### Tool-use contract and limits after `anthropic` 2.3.0

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| A1 | only the first `tool_result` of a turn is sent back | 4 failed in `tests/test_agent_contract.py`: the fake rejects a turn with several `tool_use` and one result | yes |
| A12 | SDK retries left on (`max_retries=0` removed) | 2 failed in `tests/test_limits.py`: `'refused' != 'stopped'`, the scripted replies run out | yes |
| A11 | request timeout fixed at 30 s | 1 failed: `30.0` instead of `6.0` for the third request | yes |
| A13 | `usage` cost never added | 7 failed in `tests/test_limits.py`: no cost limit ever fires | yes |
| A9b | cost limit doubled | 6 failed in `tests/test_limits.py` | yes |
| A8 | step limit `>` instead of `>=` | 2 failed in `tests/test_limits.py` | yes |

## Lot 7

The live evaluation is assembled and never run here: every proof below is against the in-memory
fake. Same method: bug planted, red copied, restored, green again.

### V1 price table (`tests/test_pricing.py`)

Read on 2026-10-27 at https://platform.claude.com/docs/en/about-claude/pricing. Every price in
`pricing.py` matched the page, including the cache read of `claude-sonnet-5-5` ($0.10, which is
0.05x the $2 input; the page text says "$0.10 USD on Claude Sonnet 5.5"). The $0.20 seen in an
SDK reference is the Opus 5.5 value. Nothing changed, so `PRICE_TABLE_VERSION` stays.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| V1 | Sonnet 5.5 cache read set to 0.20 | 3 failed: the pinned table, the 0.05x rule and the cost of 100,000 cache-read tokens | yes |

### V2 question set (`data/eval/questions.json`, `tests/test_questions.py`)

92 questions: 60 on the ten intents (6 each), 14 out of scope, 6 ambiguous, 12 injections. The
expected values are real: conditions, medication reasons, years and patient ids were read from the
database. The offline test runs every expected intent through the assembler against the database
and requires a non-empty table.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| Q1 | `Gingivitis (disorder)` misspelled in the expected params of `condition_prevalence-01` | 1 failed: `with_condition` sums to 0, so the expected answer does not exist | yes |
| Q2 | `group_by` written `groupby` in `count_patients-01` | 1 failed: pydantic `ValidationError` for `CountPatients` (unknown and missing field) | yes |
| Q3 | `name_contains` set to `insulinn` in `top_medications-02` | 1 failed: the table is empty | yes |

### V4 cache (`tests/test_cache.py`)

The breakpoint sits on the system block, which caches the tools and the system prompt together
(the cache covers `tools`, `system`, `messages` in that order, up to the marked block; see
https://platform.claude.com/docs/en/build-with-claude/prompt-caching, read on 2026-10-27). The
minimum cacheable prefix is 512 tokens for the 5.5 models. The prefix is about 3,800 characters;
no token count was made, because that needs a call to the API. The report shows the tokens read
from the cache per question, which is the real answer.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| C1 | `cache_control` removed from the system block | 1 failed: the request system block has no breakpoint | yes |
| C2 | the system text gets the clock reading appended on every request | 2 failed: the breakpoint test and the byte-for-byte prefix test, the prefix differs between questions | yes |
| C3 | a second breakpoint on the user message | 1 failed: `cache_control` found in the messages | yes |
| C4 | token counters never summed | 1 failed: the outcome reports 0 cache tokens instead of 900 | yes |

### V3 executor and global cap (`tests/test_evaluation.py`)

Why the cap holds: a question stops when its cost is over its cap, and the check follows a
response, so it ends at most one response over. One response is bounded by the 60,000 byte request
limit (a token covers at least one byte, plus 1,000 tokens of overhead) and by `max_tokens`. The
runner starts a question only if what is left of the global cap is at least that bound plus the
question cap. The first plant is the spec's own item "global cap removed".

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| T1 | the check against what is left of the global cap removed | 8 failed: the worst-case rehearsal goes over US$ 2.00 for sonnet and opus, the edge test and the cap-under-one-reserve test | yes |
| T2 | the check uses the question cap (US$ 0.05) instead of the reserve | 6 failed: the same rehearsal passes US$ 2.00, the next question started with US$ 0.155 left | yes |
| T3 | the missing-key check removed | 1 failed: a client is built without a key | yes |
| T4 | the request byte limit not applied in the loop | 2 failed: a 100 byte limit still sends the request, and the growing request is never stopped | yes |
| T5 | a timeout or API error charged at 0 | 1 failed: the charge is 0 instead of the worst case of one response | yes |
| T6 | the per-block sample ignored | 3 failed: 92 questions instead of 26, wrong spread, wrong plan total | yes |

### V5 recording and replay (`src/intent_analyst/recording.py`, `tests/test_recording.py`)

`vcrpy` 9.2.0 was chosen after reading its source: `vcr/patch.py` patches the transports of
`httpx2`, the HTTP library under `anthropic` 2.3.0. A transport of our own was not needed. Only an
allow-list of headers is kept in a recording (`content-type`, `anthropic-version`, and
`request-id` in the response), so every authentication header is dropped by default; the SDK 2.3.0
sends the key as `Authorization: Bearer`, not as `x-api-key`, and a test shows it is sent. A
replay matches on method, URL and the parsed JSON body. The two committed recordings in
`tests/cassettes/` were made through the in-memory fake, so they test the machinery and say nothing
about the model.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| R1 | the header allow-list removed (every header kept) | 7 failed: the key and the bearer token are written, headers outside the list, and the executor's recordings hold the key | yes |
| R2 | the final scan for a key not called | 2 failed: a response that echoes a key (known or only key-shaped) leaves a recording that holds it | yes |
| R3 | the JSON body left out of the matching | 5 failed: a changed prompt, tool, message or model still finds its recording | yes |
| R4 | the real system prompt changed by a few words | 3 failed: the committed recordings no longer match the request, so the replay is red until it is remade | yes |
| R5 | the "recording fully played" check removed | 5 failed: the SDK turns the unmatched request into an `api_error` outcome and the mismatch goes unseen | yes |
| R6 | `decode_compressed_response` off | 1 failed: a gzip response is stored with its `content-encoding` removed and cannot be replayed | yes |

### V6 report (`src/intent_analyst/report.py`, `tests/test_report.py`)

One JSON file and one Markdown table per run, named by run id (`reports/<date>T<time>Z-<model>`):
model, date, price table version and the date its prices were read, hashes of the question file and
of the system prompt, correct rate and coverage by block, cost, time and cache tokens read per
question, total cost (reported by the API, and as counted against the cap). Nothing has been
written to `reports/` yet, because the live evaluation has not run.

| # | Planted bug | Red result and reason | Green after restore |
|---|---|---|---|
| S1 | parameters never compared | 3 failed: a wrong `vital_status` and a wrong `category` score as right | yes |
| S2 | an injection scored right whenever the question did not stop | 2 failed: a reply that repeats the injected text passes | yes |
| S3 | a failed `run_intent` counted as the intent run | 1 failed: the failed call scores as right | yes |
| S4 | coverage taken from correct answers instead of answers | 2 failed: coverage of the block and the table in the Markdown file | yes |
