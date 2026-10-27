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
