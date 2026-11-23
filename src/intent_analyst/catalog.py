"""The closed catalog of question intents and the SQL assembler.

An intent is a parameter model (Pydantic) plus one SQL template. Two kinds of parameters exist:

* Enum parameters pick a SQL fragment (a sort or group column). The fragment is a constant in
  this file, never text from the caller, so columns can only come from a closed set.
* Every other parameter is bound as a query parameter and never becomes part of the SQL text.

Nothing here writes data: every template is a single SELECT (proved by tests).
"""

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from itertools import product
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

# Must match REFERENCE_DATE in scripts/generate_data.sh (a test checks it): ages are taken at it.
REFERENCE_DATE = date(2026, 1, 1)


class SqlEnum(Enum):
    """An enum whose members carry the SQL fragment they stand for."""

    sql: str

    def __new__(cls, value: str, sql: str) -> Self:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.sql = sql
        return obj


class PatientGroup(SqlEnum):
    gender = ("gender", "p.gender")
    race = ("race", "p.race")
    ethnicity = ("ethnicity", "p.ethnicity")
    marital = ("marital", "p.marital")
    county = ("county", "p.county")


class PatientGroupOrAll(SqlEnum):
    overall = ("overall", "'all'")
    gender = ("gender", "p.gender")
    race = ("race", "p.race")
    ethnicity = ("ethnicity", "p.ethnicity")


class Vital(SqlEnum):
    any = ("any", "TRUE")
    alive = ("alive", "p.deathdate IS NULL")
    deceased = ("deceased", "p.deathdate IS NOT NULL")


class Direction(SqlEnum):
    asc = ("asc", "ASC")
    desc = ("desc", "DESC")


class ConditionCategory(SqlEnum):
    """Synthea conditions are SNOMED terms whose suffix says what they are. Only '(disorder)'
    terms are diagnoses; the others are findings and situations ('Medication review due')."""

    disorder = ("disorder", "ends_with(c.description, '(disorder)')")
    finding = ("finding", "ends_with(c.description, '(finding)')")
    situation = ("situation", "ends_with(c.description, '(situation)')")
    any = ("any", "TRUE")


class ConditionOrder(SqlEnum):
    patients = ("patients", "patients")
    occurrences = ("occurrences", "occurrences")


class EncounterGroup(SqlEnum):
    encounter_class = ("encounter_class", "e.encounter_class")
    year = ("year", "CAST(year(e.start_date) AS VARCHAR)")


class EncounterOrder(SqlEnum):
    bucket = ("bucket", "bucket")
    encounters = ("encounters", "encounters")
    total_cost = ("total_cost", "total_cost")
    avg_cost = ("avg_cost", "avg_cost")


class PatientCostOrder(SqlEnum):
    total_cost = ("total_cost", "total_cost")
    encounters = ("encounters", "encounters")


class MedicationOrder(SqlEnum):
    prescriptions = ("prescriptions", "prescriptions")
    patients = ("patients", "patients")
    total_cost = ("total_cost", "total_cost")


class ProcedureOrder(SqlEnum):
    performed = ("performed", "performed")
    total_cost = ("total_cost", "total_cost")
    avg_cost = ("avg_cost", "avg_cost")


Limit = Annotated[int, Field(ge=1, le=50)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CountPatients(Params):
    group_by: PatientGroup
    vital_status: Vital = Vital.any


class PatientsByAgeBand(Params):
    band_years: Literal[5, 10, 20] = 10
    vital_status: Vital = Vital.any


class TopConditions(Params):
    category: ConditionCategory = ConditionCategory.disorder
    limit: Limit = 10
    name_contains: Text | None = None
    order_by: ConditionOrder = ConditionOrder.patients
    direction: Direction = Direction.desc


class ConditionPrevalence(Params):
    condition: Text
    group_by: PatientGroupOrAll = PatientGroupOrAll.overall


class EncountersSummary(Params):
    group_by: EncounterGroup
    order_by: EncounterOrder = EncounterOrder.encounters
    direction: Direction = Direction.desc
    limit: Limit = 20


class TopPatientsByCost(Params):
    order_by: PatientCostOrder = PatientCostOrder.total_cost
    direction: Direction = Direction.desc
    limit: Limit = 10


class TopMedications(Params):
    limit: Limit = 10
    name_contains: Text | None = None
    order_by: MedicationOrder = MedicationOrder.prescriptions
    direction: Direction = Direction.desc


class MedicationsForCondition(Params):
    reason: Text
    limit: Limit = 10


class ProceduresSummary(Params):
    year: Annotated[int, Field(ge=1900, le=2100)] | None = None
    order_by: ProcedureOrder = ProcedureOrder.performed
    direction: Direction = Direction.desc
    limit: Limit = 10


class PatientEncounters(Params):
    patient_id: Annotated[str, Field(pattern=UUID_PATTERN)]
    direction: Direction = Direction.desc
    limit: Limit = 20


def name_filter(column: str) -> str:
    """The one SQL condition of the `name_contains` parameter, for the intents and for the scoring
    of what a filter selects (`db.items_selected`)."""
    return f"($name_contains IS NULL OR contains(lower({column}), lower($name_contains)))"


# Intent -> (table, column) the `name_contains` filter reads. Scoring compares the items a filter
# selects there, not its text (report.py). A test checks it holds every intent with the parameter.
TEXT_FILTERS: dict[str, tuple[str, str]] = {
    "top_conditions": ("conditions", "description"),
    "top_medications": ("medications", "description"),
}


@dataclass(frozen=True)
class Intent:
    name: str
    summary: str
    params: type[Params]
    sql: str
    uses: dict[str, list[str]]  # table -> columns read
    example_question: str
    example_params: dict[str, Any]


INTENTS: dict[str, Intent] = {
    i.name: i
    for i in [
        Intent(
            "count_patients",
            "Count patients, split by one demographic attribute.",
            CountPatients,
            "SELECT {group_by} AS bucket, count(*) AS patients FROM patients p "
            "WHERE {vital_status} GROUP BY 1 ORDER BY patients DESC, bucket",
            {"patients": ["gender", "race", "ethnicity", "marital", "county", "deathdate"]},
            "How many patients are there of each gender?",
            {"group_by": "gender"},
        ),
        Intent(
            "patients_by_age_band",
            "Count patients per age band, with ages taken at the reference date (or at death).",
            PatientsByAgeBand,
            "SELECT (age // $band_years) * $band_years AS band_start, count(*) AS patients "
            "FROM (SELECT date_sub('year', p.birthdate, coalesce(p.deathdate, $reference_date)) "
            "AS age FROM patients p WHERE {vital_status}) GROUP BY 1 ORDER BY 1",
            {"patients": ["birthdate", "deathdate"]},
            "How many patients are in each 10-year age band?",
            {"band_years": 10},
        ),
        Intent(
            "top_conditions",
            "Most common diagnoses (SNOMED disorders by default; findings and situations are "
            "separate categories), optionally filtered by a word in the condition name.",
            TopConditions,
            "SELECT c.description AS condition, count(DISTINCT c.patient) AS patients, "  # noqa: S608
            "count(*) AS occurrences FROM conditions c WHERE {category} AND "
            + name_filter("c.description")
            + " "
            "GROUP BY 1 ORDER BY {order_by} {direction}, condition LIMIT $limit",
            {"conditions": ["description", "patient"]},
            "What are the 5 most common diagnoses (disorders)?",
            {"limit": 5},
        ),
        Intent(
            "condition_prevalence",
            "Share of patients that ever had one exact condition, overall or by demographic group.",
            ConditionPrevalence,
            "SELECT pop.bucket, pop.total AS patients, coalesce(hit.n, 0) AS with_condition, "
            "round(100.0 * coalesce(hit.n, 0) / pop.total, 1) AS percent "
            "FROM (SELECT {group_by} AS bucket, count(*) AS total FROM patients p GROUP BY 1) pop "
            "LEFT JOIN (SELECT {group_by} AS bucket, count(DISTINCT p.id) AS n "
            "FROM conditions c JOIN patients p ON p.id = c.patient "
            "WHERE c.description = $condition GROUP BY 1) hit USING (bucket) ORDER BY bucket",
            {
                "conditions": ["description", "patient"],
                "patients": ["id", "gender", "race", "ethnicity"],
            },
            "What percent of patients have had Viral sinusitis (disorder), by gender?",
            {"condition": "Viral sinusitis (disorder)", "group_by": "gender"},
        ),
        Intent(
            "encounters_summary",
            "Encounter count and claim cost, grouped by encounter class or by year.",
            EncountersSummary,
            "SELECT {group_by} AS bucket, count(*) AS encounters, "
            "round(sum(e.total_claim_cost), 2) AS total_cost, "
            "round(avg(e.total_claim_cost), 2) AS avg_cost FROM encounters e "
            "GROUP BY 1 ORDER BY {order_by} {direction}, bucket LIMIT $limit",
            {"encounters": ["encounter_class", "start_date", "total_claim_cost"]},
            "Which encounter class has the highest average claim cost?",
            {"group_by": "encounter_class", "order_by": "avg_cost"},
        ),
        Intent(
            "top_patients_by_cost",
            "Patients with the highest total claim cost or the most encounters.",
            TopPatientsByCost,
            "SELECT p.id AS patient_id, p.first_name || ' ' || p.last_name AS name, "
            "count(*) AS encounters, round(sum(e.total_claim_cost), 2) AS total_cost "
            "FROM encounters e JOIN patients p ON p.id = e.patient GROUP BY 1, 2 "
            "ORDER BY {order_by} {direction}, patient_id LIMIT $limit",
            {
                "encounters": ["patient", "total_claim_cost"],
                "patients": ["id", "first_name", "last_name"],
            },
            "Who are the 3 patients with the highest total claim cost?",
            {"limit": 3},
        ),
        Intent(
            "top_medications",
            "Most prescribed medications, optionally filtered by a word in the medication name.",
            TopMedications,
            "SELECT m.description AS medication, count(*) AS prescriptions, "  # noqa: S608
            "count(DISTINCT m.patient) AS patients, round(sum(m.total_cost), 2) AS total_cost "
            "FROM medications m WHERE " + name_filter("m.description") + " "
            "GROUP BY 1 ORDER BY {order_by} {direction}, medication LIMIT $limit",
            {"medications": ["description", "patient", "total_cost"]},
            "Which medications are prescribed most often?",
            {"limit": 5},
        ),
        Intent(
            "medications_for_condition",
            "Medications prescribed for one exact condition (the recorded reason).",
            MedicationsForCondition,
            "SELECT m.description AS medication, count(*) AS prescriptions, "  # noqa: S608
            "count(DISTINCT m.patient) AS patients FROM medications m "
            "WHERE m.reason_description = $reason GROUP BY 1 "
            "ORDER BY prescriptions DESC, medication LIMIT $limit",
            {"medications": ["description", "patient", "reason_description"]},
            "Which medications are prescribed for Acute bronchitis (disorder)?",
            {"reason": "Acute bronchitis (disorder)"},
        ),
        Intent(
            "procedures_summary",
            "Most common procedures with their cost, optionally for one year.",
            ProceduresSummary,
            "SELECT pr.description AS procedure, count(*) AS performed, "
            "round(sum(pr.base_cost), 2) AS total_cost, round(avg(pr.base_cost), 2) AS avg_cost "
            "FROM procedures pr WHERE ($year IS NULL OR year(pr.start_date) = $year) "
            "GROUP BY 1 ORDER BY {order_by} {direction}, procedure LIMIT $limit",
            {"procedures": ["description", "start_date", "base_cost"]},
            "What were the most common procedures in 2020?",
            {"year": 2020, "limit": 5},
        ),
        Intent(
            "patient_encounters",
            "The encounters of one patient (by id), newest or oldest first.",
            PatientEncounters,
            "SELECT e.start_date AS date, e.encounter_class AS class, "
            "e.description AS description, e.total_claim_cost AS claim_cost "
            "FROM encounters e WHERE e.patient = $patient_id "
            "ORDER BY e.start_date {direction}, e.id LIMIT $limit",
            {
                "encounters": [
                    "id",
                    "patient",
                    "start_date",
                    "encounter_class",
                    "description",
                    "total_claim_cost",
                ]
            },
            "Show the 5 latest encounters of patient 9661a432-4630-2cd9-142c-386845fae837.",
            {"patient_id": "9661a432-4630-2cd9-142c-386845fae837", "limit": 5},
        ),
    ]
}

_PLACEHOLDER = re.compile(r"\$([a-z_]+)")
_CONSTANTS: dict[str, Any] = {"reference_date": REFERENCE_DATE}


@dataclass(frozen=True)
class Query:
    """An assembled query: the SQL text and the values to bind."""

    sql: str
    params: dict[str, Any]


def assemble(name: str, raw_params: dict[str, Any]) -> Query:
    """Validate raw params against the intent and build the SQL. Raises on anything unknown."""
    intent = INTENTS[name]
    model = intent.params.model_validate(raw_params)
    fragments: dict[str, str] = {}
    values: dict[str, Any] = {}
    for field in type(model).model_fields:
        value = getattr(model, field)
        if isinstance(value, SqlEnum):
            fragments[field] = value.sql
        else:
            values[field] = value
    sql = intent.sql.format(**fragments)
    bound = {**values, **_CONSTANTS}
    return Query(sql, {k: bound[k] for k in set(_PLACEHOLDER.findall(sql))})


def enum_variants(name: str) -> list[dict[str, Any]]:
    """Example params of an intent with every combination of its enum parameters."""
    intent = INTENTS[name]
    enum_fields: dict[str, list[str]] = {}
    for field, info in intent.params.model_fields.items():
        ann = info.annotation
        if isinstance(ann, type) and issubclass(ann, SqlEnum):
            enum_fields[field] = [m.value for m in ann]
    return [
        {**intent.example_params, **dict(zip(enum_fields, combo, strict=True))}
        for combo in product(*enum_fields.values())
    ]
