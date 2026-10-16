"""Expected-value tests: every result is recomputed in plain Python straight from the CSV files.

Nothing here reuses the SQL under test. Each helper states the intent in Python (count, sum,
sort) so a wrong filter, direction, column or formula in the catalog changes the answer.
"""

import csv
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

import duckdb
import pytest

from intent_analyst.catalog import INTENTS, REFERENCE_DATE
from intent_analyst.db import run_intent, run_sql

DATA = Path(__file__).resolve().parent.parent / "data" / "synthea"
Row = dict[str, str]


@cache
def rows(table: str) -> list[Row]:
    with (DATA / f"{table}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ordered(
    items: dict[str, Any], by: Callable[[Any], Any], direction: str, limit: int
) -> list[tuple[str, Any]]:
    """Sort by a value in the given direction, ties broken by name ascending, then cut."""
    sign = -1 if direction == "desc" else 1
    ranked = sorted(items.items(), key=lambda kv: (sign * by(kv[1]), kv[0]))
    return ranked[:limit]


def text_order(items: list[str], direction: str) -> list[str]:
    return sorted(items, reverse=direction == "desc")


def money(x: float) -> float:
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def percent(part: int, whole: int) -> float:
    return float((Decimal(100 * part) / Decimal(whole)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def years_between(start: date, end: date) -> int:
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))


def vital_ok(patient: Row, vital: str) -> bool:
    dead = patient["DEATHDATE"] != ""
    return {"any": True, "alive": not dead, "deceased": dead}[vital]


GROUP_COLUMN = {
    "gender": "GENDER",
    "race": "RACE",
    "ethnicity": "ETHNICITY",
    "marital": "MARITAL",
    "county": "COUNTY",
}


def group_value(patient: Row, group: str) -> str:
    if group == "overall":
        return "all"
    return patient[GROUP_COLUMN[group]] or "unknown"


# ---- fixed facts about the slice ----


def test_slice_facts() -> None:
    patients = rows("patients")
    assert len(patients) == 53
    assert sum(1 for p in patients if p["DEATHDATE"]) == 3
    assert sum(1 for p in patients if not p["DEATHDATE"]) == 50


def test_no_event_after_the_end_date() -> None:
    limit = REFERENCE_DATE.isoformat()
    for table in ("encounters", "conditions", "medications", "procedures"):
        assert max(r["START"][:10] for r in rows(table)) < limit, table


# ---- the loaded database matches the CSV ----


def test_loaded_dates_match_the_csv(con: duckdb.DuckDBPyConnection) -> None:
    got = {
        r["id"]: (r["birthdate"], r["deathdate"])
        for r in run_sql(con, "SELECT id, birthdate, deathdate FROM patients", {})
    }
    want = {
        p["Id"]: (
            date.fromisoformat(p["BIRTHDATE"]),
            date.fromisoformat(p["DEATHDATE"]) if p["DEATHDATE"] else None,
        )
        for p in rows("patients")
    }
    assert got == want


# ---- count_patients ----


@pytest.mark.parametrize("vital", ["any", "alive", "deceased"])
@pytest.mark.parametrize("group", list(GROUP_COLUMN))
def test_count_patients(con: duckdb.DuckDBPyConnection, group: str, vital: str) -> None:
    counts = Counter(group_value(p, group) for p in rows("patients") if vital_ok(p, vital))
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    want = [{"bucket": k, "patients": n} for k, n in ranked]
    assert run_intent(con, "count_patients", {"group_by": group, "vital_status": vital}) == want


def test_count_patients_alive_and_deceased_sizes(con: duckdb.DuckDBPyConnection) -> None:
    alive = run_intent(con, "count_patients", {"group_by": "gender", "vital_status": "alive"})
    dead = run_intent(con, "count_patients", {"group_by": "gender", "vital_status": "deceased"})
    assert sum(r["patients"] for r in alive) == 50
    assert sum(r["patients"] for r in dead) == 3


# ---- patients_by_age_band ----


@pytest.mark.parametrize("vital", ["any", "alive", "deceased"])
@pytest.mark.parametrize("width", [5, 10, 20])
def test_patients_by_age_band(con: duckdb.DuckDBPyConnection, width: int, vital: str) -> None:
    bands: Counter[int] = Counter()
    for p in rows("patients"):
        if not vital_ok(p, vital):
            continue
        end = date.fromisoformat(p["DEATHDATE"]) if p["DEATHDATE"] else REFERENCE_DATE
        bands[years_between(date.fromisoformat(p["BIRTHDATE"]), end) // width * width] += 1
    want = [{"band_start": k, "patients": bands[k]} for k in sorted(bands)]
    got = run_intent(con, "patients_by_age_band", {"band_years": width, "vital_status": vital})
    assert got == want


def test_age_of_a_deceased_patient_is_taken_at_death(con: duckdb.DuckDBPyConnection) -> None:
    # 1924-12-17 to 1989-10-24 is 64 years; at the reference date it would be 101.
    got = run_intent(con, "patients_by_age_band", {"band_years": 5, "vital_status": "deceased"})
    assert {"band_start": 60, "patients": 1} in got
    assert all(r["band_start"] < 100 for r in got)


# ---- top_conditions ----

CATEGORY_SUFFIX = {
    "disorder": "(disorder)",
    "finding": "(finding)",
    "situation": "(situation)",
    "any": "",
}


def condition_stats(category: str, contains: str | None = None) -> dict[str, tuple[int, int]]:
    people: dict[str, set[str]] = defaultdict(set)
    count: Counter[str] = Counter()
    for c in rows("conditions"):
        d = c["DESCRIPTION"]
        if not d.endswith(CATEGORY_SUFFIX[category]):
            continue
        if contains is not None and contains.lower() not in d.lower():
            continue
        people[d].add(c["PATIENT"])
        count[d] += 1
    return {d: (len(people[d]), count[d]) for d in count}


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("order", ["patients", "occurrences"])
@pytest.mark.parametrize("category", list(CATEGORY_SUFFIX))
def test_top_conditions(
    con: duckdb.DuckDBPyConnection, category: str, order: str, direction: str
) -> None:
    index = 0 if order == "patients" else 1
    want = [
        {"condition": d, "patients": v[0], "occurrences": v[1]}
        for d, v in ordered(condition_stats(category), lambda v: v[index], direction, 50)
    ]
    got = run_intent(
        con,
        "top_conditions",
        {"category": category, "order_by": order, "direction": direction, "limit": 50},
    )
    assert got == want


def test_top_conditions_default_lists_only_disorders(con: duckdb.DuckDBPyConnection) -> None:
    got = run_intent(con, "top_conditions", {"limit": 50})
    assert got
    assert all(r["condition"].endswith("(disorder)") for r in got)
    assert [r["condition"] for r in got[:3]] == [
        "Gingivitis (disorder)",
        "Viral sinusitis (disorder)",
        "Acute viral pharyngitis (disorder)",
    ]
    findings = run_intent(con, "top_conditions", {"category": "finding", "limit": 50})
    assert all(r["condition"].endswith("(finding)") for r in findings)


def test_top_conditions_ranking_differs_by_patients_and_occurrences(
    con: duckdb.DuckDBPyConnection,
) -> None:
    by_patients = run_intent(con, "top_conditions", {"limit": 10, "order_by": "patients"})
    by_occurrences = run_intent(con, "top_conditions", {"limit": 10, "order_by": "occurrences"})
    assert by_patients != by_occurrences  # guards the data: the two orders must differ
    want = [d for d, _ in ordered(condition_stats("disorder"), lambda v: v[1], "desc", 10)]
    assert [r["condition"] for r in by_occurrences] == want


@pytest.mark.parametrize("word", ["SINUS", "bronch", "no such thing"])
def test_top_conditions_name_filter(con: duckdb.DuckDBPyConnection, word: str) -> None:
    want = [d for d, _ in ordered(condition_stats("disorder", word), lambda v: v[0], "desc", 50)]
    got = run_intent(con, "top_conditions", {"name_contains": word, "limit": 50})
    assert [r["condition"] for r in got] == want


# ---- condition_prevalence ----


@pytest.mark.parametrize("group", ["overall", "gender", "race", "ethnicity"])
@pytest.mark.parametrize(
    "condition",
    [
        "Viral sinusitis (disorder)",
        "Gingivitis (disorder)",
        "Anemia (disorder)",
        "No such (disorder)",
    ],
)
def test_condition_prevalence(con: duckdb.DuckDBPyConnection, condition: str, group: str) -> None:
    total: Counter[str] = Counter()
    bucket_of = {}
    for p in rows("patients"):
        bucket_of[p["Id"]] = group_value(p, group)
        total[bucket_of[p["Id"]]] += 1
    had: dict[str, set[str]] = defaultdict(set)
    for c in rows("conditions"):
        if c["DESCRIPTION"] == condition:
            had[bucket_of[c["PATIENT"]]].add(c["PATIENT"])
    want = [
        {
            "bucket": b,
            "patients": total[b],
            "with_condition": len(had[b]),
            "percent": percent(len(had[b]), total[b]),
        }
        for b in sorted(total)
    ]
    got = run_intent(con, "condition_prevalence", {"condition": condition, "group_by": group})
    assert got == want


def test_condition_prevalence_known_value(con: duckdb.DuckDBPyConnection) -> None:
    (row,) = run_intent(con, "condition_prevalence", {"condition": "Gingivitis (disorder)"})
    assert row == {"bucket": "all", "patients": 53, "with_condition": 47, "percent": 88.7}


# ---- encounters_summary ----


def encounter_stats(group: str) -> dict[str, tuple[int, float, float]]:
    n: Counter[str] = Counter()
    total: defaultdict[str, float] = defaultdict(float)
    for e in rows("encounters"):
        key = e["ENCOUNTERCLASS"] if group == "encounter_class" else e["START"][:4]
        n[key] += 1
        total[key] += float(e["TOTAL_CLAIM_COST"])
    return {k: (n[k], money(total[k]), money(total[k] / n[k])) for k in n}


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("order", ["bucket", "encounters", "total_cost", "avg_cost"])
@pytest.mark.parametrize("group", ["encounter_class", "year"])
def test_encounters_summary(
    con: duckdb.DuckDBPyConnection, group: str, order: str, direction: str
) -> None:
    stats = encounter_stats(group)
    if order == "bucket":
        want = [(k, stats[k]) for k in text_order(list(stats), direction)[:20]]
    else:
        index = {"encounters": 0, "total_cost": 1, "avg_cost": 2}[order]
        want = ordered(stats, lambda v: v[index], direction, 20)
    got = run_intent(
        con,
        "encounters_summary",
        {"group_by": group, "order_by": order, "direction": direction, "limit": 20},
    )
    assert [r["bucket"] for r in got] == [k for k, _ in want]
    for g, (_, v) in zip(got, want, strict=True):
        assert g["encounters"] == v[0]
        assert g["total_cost"] == pytest.approx(v[1], abs=0.011)
        assert g["avg_cost"] == pytest.approx(v[2], abs=0.011)


def test_average_cost_is_total_over_count(con: duckdb.DuckDBPyConnection) -> None:
    got = run_intent(con, "encounters_summary", {"group_by": "encounter_class"})
    assert len(got) > 3
    for r in got:
        costs = [
            float(e["TOTAL_CLAIM_COST"])
            for e in rows("encounters")
            if e["ENCOUNTERCLASS"] == r["bucket"]
        ]
        assert r["encounters"] == len(costs)
        assert r["avg_cost"] == pytest.approx(sum(costs) / len(costs), abs=0.006)


# ---- top_patients_by_cost ----


def patient_stats() -> dict[str, tuple[int, float]]:
    n: Counter[str] = Counter()
    total: defaultdict[str, float] = defaultdict(float)
    for e in rows("encounters"):
        n[e["PATIENT"]] += 1
        total[e["PATIENT"]] += float(e["TOTAL_CLAIM_COST"])
    return {k: (n[k], money(total[k])) for k in n}


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("order", ["total_cost", "encounters"])
def test_top_patients_by_cost(con: duckdb.DuckDBPyConnection, order: str, direction: str) -> None:
    index = 1 if order == "total_cost" else 0
    names = {p["Id"]: f"{p['FIRST']} {p['LAST']}" for p in rows("patients")}
    want = ordered(patient_stats(), lambda v: v[index], direction, 10)
    got = run_intent(con, "top_patients_by_cost", {"order_by": order, "direction": direction})
    assert [(r["patient_id"], r["name"], r["encounters"]) for r in got] == [
        (pid, names[pid], v[0]) for pid, v in want
    ]
    for g, (_, v) in zip(got, want, strict=True):
        assert g["total_cost"] == pytest.approx(v[1], abs=0.011)


# ---- top_medications ----


def medication_stats(contains: str | None) -> dict[str, tuple[int, int, float]]:
    people: dict[str, set[str]] = defaultdict(set)
    n: Counter[str] = Counter()
    total: defaultdict[str, float] = defaultdict(float)
    for m in rows("medications"):
        d = m["DESCRIPTION"]
        if contains is not None and contains.lower() not in d.lower():
            continue
        people[d].add(m["PATIENT"])
        n[d] += 1
        total[d] += float(m["TOTALCOST"])
    return {d: (n[d], len(people[d]), money(total[d])) for d in n}


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("order", ["prescriptions", "patients", "total_cost"])
@pytest.mark.parametrize("contains", [None, "INHAL"])
def test_top_medications(
    con: duckdb.DuckDBPyConnection, order: str, direction: str, contains: str | None
) -> None:
    index = {"prescriptions": 0, "patients": 1, "total_cost": 2}[order]
    want = ordered(medication_stats(contains), lambda v: v[index], direction, 50)
    got = run_intent(
        con,
        "top_medications",
        {"order_by": order, "direction": direction, "name_contains": contains, "limit": 50},
    )
    assert [(r["medication"], r["prescriptions"], r["patients"]) for r in got] == [
        (d, v[0], v[1]) for d, v in want
    ]
    for g, (_, v) in zip(got, want, strict=True):
        assert g["total_cost"] == pytest.approx(v[2], abs=0.011)


# ---- medications_for_condition ----


@pytest.mark.parametrize(
    "reason",
    [
        "Childhood asthma (disorder)",
        "Essential hypertension (disorder)",
        "Acute bronchitis (disorder)",
        "No such (disorder)",
    ],
)
def test_medications_for_condition(con: duckdb.DuckDBPyConnection, reason: str) -> None:
    people: dict[str, set[str]] = defaultdict(set)
    n: Counter[str] = Counter()
    for m in rows("medications"):
        if m["REASONDESCRIPTION"] == reason:
            people[m["DESCRIPTION"]].add(m["PATIENT"])
            n[m["DESCRIPTION"]] += 1
    want = [
        {"medication": d, "prescriptions": n[d], "patients": len(people[d])}
        for d in sorted(n, key=lambda d: (-n[d], d))[:50]
    ]
    assert run_intent(con, "medications_for_condition", {"reason": reason, "limit": 50}) == want


# ---- procedures_summary ----


def procedure_stats(year: int | None) -> dict[str, tuple[int, float, float]]:
    n: Counter[str] = Counter()
    total: defaultdict[str, float] = defaultdict(float)
    for p in rows("procedures"):
        if year is not None and p["START"][:4] != str(year):
            continue
        n[p["DESCRIPTION"]] += 1
        total[p["DESCRIPTION"]] += float(p["BASE_COST"])
    return {d: (n[d], money(total[d]), money(total[d] / n[d])) for d in n}


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("order", ["performed", "total_cost", "avg_cost"])
@pytest.mark.parametrize("year", [None, 2020, 2015, 1999])
def test_procedures_summary(
    con: duckdb.DuckDBPyConnection, year: int | None, order: str, direction: str
) -> None:
    index = {"performed": 0, "total_cost": 1, "avg_cost": 2}[order]
    want = ordered(procedure_stats(year), lambda v: v[index], direction, 50)
    got = run_intent(
        con,
        "procedures_summary",
        {"year": year, "order_by": order, "direction": direction, "limit": 50},
    )
    assert [(r["procedure"], r["performed"]) for r in got] == [(d, v[0]) for d, v in want]
    for g, (_, v) in zip(got, want, strict=True):
        assert g["total_cost"] == pytest.approx(v[1], abs=0.011)
        assert g["avg_cost"] == pytest.approx(v[2], abs=0.011)


def test_procedures_summary_example_is_not_a_tie(con: duckdb.DuckDBPyConnection) -> None:
    example = INTENTS["procedures_summary"].example_params
    performed = [r["performed"] for r in run_intent(con, "procedures_summary", example)]
    assert len(performed) == example["limit"]
    assert performed == sorted(set(performed), reverse=True)  # strictly decreasing: no ties
    got = run_intent(con, "procedures_summary", {"year": 2020, "limit": 5})
    assert [r["performed"] for r in got] == [79, 42, 37, 31, 30]
    assert got[0]["procedure"] == "Depression screening (procedure)"


def test_top_conditions_example_lists_diagnoses_only(con: duckdb.DuckDBPyConnection) -> None:
    example = INTENTS["top_conditions"].example_params
    got = run_intent(con, "top_conditions", example)
    assert [r["condition"] for r in got] == [
        "Gingivitis (disorder)",
        "Viral sinusitis (disorder)",
        "Acute viral pharyngitis (disorder)",
        "Acute bronchitis (disorder)",
        "Anemia (disorder)",
    ]
    patients = [r["patients"] for r in got]
    assert patients == sorted(set(patients), reverse=True)


# ---- patient_encounters ----


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_patient_encounters(con: duckdb.DuckDBPyConnection, direction: str) -> None:
    busiest = Counter(e["PATIENT"] for e in rows("encounters")).most_common(1)[0][0]
    mine = [e for e in rows("encounters") if e["PATIENT"] == busiest]
    by_id = sorted(mine, key=lambda e: e["Id"])
    mine = sorted(by_id, key=lambda e: e["START"][:10], reverse=direction == "desc")
    got = run_intent(
        con, "patient_encounters", {"patient_id": busiest, "direction": direction, "limit": 15}
    )
    assert [(str(r["date"]), r["description"]) for r in got] == [
        (e["START"][:10], e["DESCRIPTION"]) for e in mine[:15]
    ]
    assert len({r["date"] for r in got}) > 1
