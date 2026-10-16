# Intent catalog

Generated from `src/intent_analyst/catalog.py` by `uv run python -m intent_analyst.catalog_doc`.
A test fails if this file is stale. The model may only pick one of these intents.
Sort and group columns are closed enums. No intent writes data.

## `count_patients`

Count patients, split by one demographic attribute.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `group_by` | one of `gender`, `race`, `ethnicity`, `marital`, `county` | required |
| `vital_status` | one of `any`, `alive`, `deceased` | any |

Reads: `patients`: `gender`, `race`, `ethnicity`, `marital`, `county`, `deathdate`.

Example: "How many patients are there of each gender?" with `{"group_by": "gender"}`.

## `patients_by_age_band`

Count patients per age band, with ages taken at the reference date (or at death).

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `band_years` | one of `5`, `10`, `20` | 10 |
| `vital_status` | one of `any`, `alive`, `deceased` | any |

Reads: `patients`: `birthdate`, `deathdate`.

Example: "How many patients are in each 10-year age band?" with `{"band_years": 10}`.

## `top_conditions`

Most common diagnoses, optionally filtered by a word in the condition name.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `limit` | integer, min `1`, max `50` | 10 |
| `name_contains` | string, max length `200` or null | None |
| `order_by` | one of `patients`, `occurrences` | patients |
| `direction` | one of `asc`, `desc` | desc |

Reads: `conditions`: `description`, `patient`.

Example: "What are the 5 most common diagnoses?" with `{"limit": 5}`.

## `condition_prevalence`

Share of patients that ever had one exact condition, overall or by demographic group.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `condition` | string, max length `200` | required |
| `group_by` | one of `overall`, `gender`, `race`, `ethnicity` | overall |

Reads: `conditions`: `description`, `patient`; `patients`: `id`, `gender`, `race`, `ethnicity`.

Example: "What percent of patients have had Viral sinusitis (disorder), by gender?" with `{"condition": "Viral sinusitis (disorder)", "group_by": "gender"}`.

## `encounters_summary`

Encounter count and claim cost, grouped by encounter class or by year.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `group_by` | one of `encounter_class`, `year` | required |
| `order_by` | one of `bucket`, `encounters`, `total_cost`, `avg_cost` | encounters |
| `direction` | one of `asc`, `desc` | desc |
| `limit` | integer, min `1`, max `50` | 20 |

Reads: `encounters`: `encounter_class`, `start_date`, `total_claim_cost`.

Example: "Which encounter class has the highest average claim cost?" with `{"group_by": "encounter_class", "order_by": "avg_cost"}`.

## `top_patients_by_cost`

Patients with the highest total claim cost or the most encounters.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `order_by` | one of `total_cost`, `encounters` | total_cost |
| `direction` | one of `asc`, `desc` | desc |
| `limit` | integer, min `1`, max `50` | 10 |

Reads: `encounters`: `patient`, `total_claim_cost`; `patients`: `id`, `first_name`, `last_name`.

Example: "Who are the 3 patients with the highest total claim cost?" with `{"limit": 3}`.

## `top_medications`

Most prescribed medications, optionally filtered by a word in the medication name.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `limit` | integer, min `1`, max `50` | 10 |
| `name_contains` | string, max length `200` or null | None |
| `order_by` | one of `prescriptions`, `patients`, `total_cost` | prescriptions |
| `direction` | one of `asc`, `desc` | desc |

Reads: `medications`: `description`, `patient`, `total_cost`.

Example: "Which medications are prescribed most often?" with `{"limit": 5}`.

## `medications_for_condition`

Medications prescribed for one exact condition (the recorded reason).

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `reason` | string, max length `200` | required |
| `limit` | integer, min `1`, max `50` | 10 |

Reads: `medications`: `description`, `patient`, `reason_description`.

Example: "Which medications are prescribed for Acute bronchitis (disorder)?" with `{"reason": "Acute bronchitis (disorder)"}`.

## `procedures_summary`

Most common procedures with their cost, optionally for one year.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `year` | integer, min `1900`, max `2100` or null | None |
| `order_by` | one of `performed`, `total_cost`, `avg_cost` | performed |
| `direction` | one of `asc`, `desc` | desc |
| `limit` | integer, min `1`, max `50` | 10 |

Reads: `procedures`: `description`, `start_date`, `base_cost`.

Example: "What were the most common procedures in 2015?" with `{"year": 2015, "limit": 5}`.

## `patient_encounters`

The encounters of one patient (by id), newest or oldest first.

| Parameter | Type and allowed values | Required or default |
|---|---|---|
| `patient_id` | string, pattern `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` | required |
| `direction` | one of `asc`, `desc` | desc |
| `limit` | integer, min `1`, max `50` | 20 |

Reads: `encounters`: `id`, `patient`, `start_date`, `encounter_class`, `description`, `total_claim_cost`.

Example: "Show the 5 latest encounters of patient 9661a432-4630-2cd9-142c-386845fae837." with `{"patient_id": "9661a432-4630-2cd9-142c-386845fae837", "limit": 5}`.
