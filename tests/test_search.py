"""`search_values`: real values from the data by a parameterized search."""

import csv
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from intent_analyst.search import SearchField, SearchValues, search_values

DATA = Path(__file__).resolve().parent.parent / "data" / "synthea"
INJECTION = "x'); DROP TABLE patients; --"


def find(con: duckdb.DuckDBPyConnection, **params: Any) -> list[dict[str, Any]]:
    return search_values(con, SearchValues.model_validate(params))


def rows(table: str) -> list[dict[str, str]]:
    with (DATA / f"{table}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize(
    ("field", "table", "column", "word"),
    [
        ("condition", "conditions", "DESCRIPTION", "ITIS"),
        ("medication", "medications", "DESCRIPTION", "INHAL"),
        ("medication_reason", "medications", "REASONDESCRIPTION", "ITIS"),
        ("procedure", "procedures", "DESCRIPTION", "ITIS"),
        ("encounter", "encounters", "DESCRIPTION", "VISIT"),
    ],
)
def test_search_matches_a_recount_from_the_csv(
    con: duckdb.DuckDBPyConnection, field: str, table: str, column: str, word: str
) -> None:
    # the word is upper case, the data is not: the search ignores case
    counts = Counter(r[column] for r in rows(table) if word.lower() in r[column].lower())
    want = [
        {"value": v, "matches": n} for v, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ]
    assert want, field  # the data has matches for this word
    assert find(con, field=field, text=word, limit=50) == want


def test_search_limit_cuts_the_list(con: duckdb.DuckDBPyConnection) -> None:
    assert len(find(con, field="condition", text="a", limit=3)) == 3


def test_search_finds_a_patient_id_by_name(con: duckdb.DuckDBPyConnection) -> None:
    first = rows("patients")[0]
    got = find(con, field="patient_name", text=first["FIRST"].lower())
    assert {"value": f"{first['FIRST']} {first['LAST']}", "patient_id": first["Id"]} in got


def test_search_with_no_match_is_an_empty_list(con: duckdb.DuckDBPyConnection) -> None:
    assert find(con, field="condition", text="no such condition at all") == []


@pytest.mark.parametrize("field", [f.value for f in SearchField])
def test_injection_text_is_bound_not_executed(con: duckdb.DuckDBPyConnection, field: str) -> None:
    assert find(con, field=field, text=INJECTION) == []
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)


@pytest.mark.parametrize("field", ["ssn", "patients.ssn", "1; DROP TABLE patients", ""])
def test_a_field_outside_the_enum_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        SearchValues.model_validate({"field": field, "text": "x"})


@pytest.mark.parametrize("params", [{"text": ""}, {"text": "x" * 201}, {"text": "x", "limit": 51}])
def test_search_input_is_bounded(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SearchValues.model_validate({"field": "condition", **params})


@pytest.mark.parametrize("field", list(SearchField))
def test_every_search_is_one_select(field: SearchField) -> None:
    scanner = duckdb.connect()
    statements = scanner.extract_statements(field.sql)
    assert [s.type for s in statements] == [duckdb.StatementType.SELECT]
