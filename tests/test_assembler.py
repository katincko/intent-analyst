import re
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from intent_analyst.catalog import (
    INTENTS,
    REFERENCE_DATE,
    SqlEnum,
    assemble,
    enum_variants,
)
from intent_analyst.catalog_doc import render_catalog
from intent_analyst.db import TABLE_COLUMNS, run_intent

ROOT = Path(__file__).resolve().parent.parent
INJECTION = "x'); DROP TABLE patients; --"
# (intent, free-text parameter) pairs: every parameter that carries caller text into a query.
TEXT_PARAMS = [
    ("top_conditions", "name_contains"),
    ("top_medications", "name_contains"),
    ("condition_prevalence", "condition"),
    ("medications_for_condition", "reason"),
]


def test_catalog_has_ten_intents() -> None:
    assert len(INTENTS) == 10


@pytest.mark.parametrize(("intent", "field"), TEXT_PARAMS)
def test_malicious_value_does_not_change_the_query(intent: str, field: str) -> None:
    benign = assemble(intent, {**INTENTS[intent].example_params, field: "plain"})
    evil = assemble(intent, {**INTENTS[intent].example_params, field: INJECTION})
    assert evil.sql == benign.sql
    assert evil.params[field] == INJECTION
    assert INJECTION not in evil.sql


@pytest.mark.parametrize(("intent", "field"), TEXT_PARAMS)
def test_malicious_value_runs_as_plain_data(
    con: duckdb.DuckDBPyConnection, intent: str, field: str
) -> None:
    rows = run_intent(con, intent, {**INTENTS[intent].example_params, field: INJECTION})
    assert all(r.get("with_condition", 0) == 0 for r in rows)  # nothing matched the text
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)  # and nothing dropped


def test_patient_id_must_be_a_uuid() -> None:
    with pytest.raises(ValidationError):
        assemble("patient_encounters", {"patient_id": "' OR 1=1 --"})


def _enum_fields() -> list[tuple[str, str]]:
    return [
        (name, field)
        for name, intent in INTENTS.items()
        for field, info in intent.params.model_fields.items()
        if isinstance(info.annotation, type) and issubclass(info.annotation, SqlEnum)
    ]


@pytest.mark.parametrize(("intent", "field"), _enum_fields())
@pytest.mark.parametrize("bad", ["ssn", "p.ssn", "1; DROP TABLE patients", "gender, ssn"])
def test_column_outside_the_enum_is_refused(intent: str, field: str, bad: str) -> None:
    params = {**INTENTS[intent].example_params, field: bad}
    with pytest.raises(ValidationError):
        assemble(intent, params)


def test_unknown_parameter_is_refused() -> None:
    with pytest.raises(ValidationError):
        assemble("count_patients", {"group_by": "gender", "order_sql": "ssn"})


@pytest.mark.parametrize("name", list(INTENTS))
def test_every_parameter_reaches_the_query(name: str) -> None:
    """A parameter the template ignores would be accepted and silently do nothing."""
    intent = INTENTS[name]
    for field in intent.params.model_fields:
        assert "{" + field + "}" in intent.sql or "$" + field in intent.sql, field


@pytest.mark.parametrize("name", list(INTENTS))
def test_no_query_writes(name: str) -> None:
    """Every enum combination of every intent is exactly one SELECT statement."""
    scanner = duckdb.connect()
    variants = enum_variants(name)
    assert variants
    for params in variants:
        statements = scanner.extract_statements(assemble(name, params).sql)
        assert [s.type for s in statements] == [duckdb.StatementType.SELECT], params


@pytest.mark.parametrize("name", list(INTENTS))
def test_declared_tables_and_columns_exist(name: str) -> None:
    intent = INTENTS[name]
    sql = assemble(name, intent.example_params).sql
    for table, columns in intent.uses.items():
        assert re.search(rf"\b{table}\b", sql), f"{table} not in the SQL"
        assert set(columns) <= set(TABLE_COLUMNS[table])


def test_catalog_md_is_current() -> None:
    assert (ROOT / "CATALOG.md").read_text(encoding="utf-8") == render_catalog()


def test_reference_date_matches_the_generation_script() -> None:
    script = (ROOT / "scripts" / "generate_data.sh").read_text(encoding="utf-8")
    match = re.search(r"^REFERENCE_DATE=(\d{8})", script, re.MULTILINE)
    assert match
    assert match.group(1) == REFERENCE_DATE.strftime("%Y%m%d")
