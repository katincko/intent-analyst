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
