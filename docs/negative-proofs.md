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
