"""The database: built once from the Synthea CSV subset, then opened locked down."""

from pathlib import Path
from typing import Any

import duckdb

from intent_analyst.catalog import assemble

# One definition per table: the CSV columns kept and the name and type they get in the database.
# Direct identifiers in the CSV (SSN, address, passport...) are deliberately never loaded.
_TABLES: dict[str, dict[str, str]] = {
    "patients": {
        "id": "Id",
        "birthdate": "CAST(BIRTHDATE AS DATE)",
        "deathdate": "CAST(NULLIF(DEATHDATE, '') AS DATE)",
        "first_name": "FIRST",
        "last_name": "LAST",
        "marital": "COALESCE(NULLIF(MARITAL, ''), 'unknown')",
        "race": "RACE",
        "ethnicity": "ETHNICITY",
        "gender": "GENDER",
        "city": "CITY",
        "county": "COUNTY",
        "income": "CAST(INCOME AS INTEGER)",
    },
    "encounters": {
        "id": "Id",
        "start_date": "CAST(substr(START, 1, 10) AS DATE)",
        "patient": "PATIENT",
        "encounter_class": "ENCOUNTERCLASS",
        "description": "DESCRIPTION",
        "total_claim_cost": "CAST(TOTAL_CLAIM_COST AS DOUBLE)",
        "reason_description": "NULLIF(REASONDESCRIPTION, '')",
    },
    "conditions": {
        "start_date": "CAST(START AS DATE)",
        "patient": "PATIENT",
        "encounter": "ENCOUNTER",
        "description": "DESCRIPTION",
    },
    "medications": {
        "start_date": "CAST(substr(START, 1, 10) AS DATE)",
        "patient": "PATIENT",
        "description": "DESCRIPTION",
        "total_cost": "CAST(TOTALCOST AS DOUBLE)",
        "reason_description": "NULLIF(REASONDESCRIPTION, '')",
    },
    "procedures": {
        "start_date": "CAST(substr(START, 1, 10) AS DATE)",
        "patient": "PATIENT",
        "description": "DESCRIPTION",
        "base_cost": "CAST(BASE_COST AS DOUBLE)",
    },
}

TABLE_COLUMNS: dict[str, list[str]] = {t: list(cols) for t, cols in _TABLES.items()}


def build_database(csv_dir: Path, db_path: Path) -> None:
    """Load the CSV files into a fresh database file. This is the only connection that writes."""
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        for table, cols in _TABLES.items():
            select = ", ".join(f"{expr} AS {name}" for name, expr in cols.items())
            csv = (csv_dir / f"{table}.csv").as_posix()
            con.execute(
                f"CREATE TABLE {table} AS SELECT {select} "  # noqa: S608 (constants above)
                "FROM read_csv(?, header = true, all_varchar = true)",
                [csv],
            )
    finally:
        con.close()


def open_readonly(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Read-only, no file or network access, configuration frozen (it cannot be unfrozen)."""
    return duckdb.connect(
        str(db_path),
        read_only=True,
        config={
            "enable_external_access": False,
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
            "lock_configuration": True,
        },
    )


def run_sql(
    con: duckdb.DuckDBPyConnection, sql: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run one already-assembled query and return rows as dicts."""
    cur = con.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def run_intent(
    con: duckdb.DuckDBPyConnection, name: str, raw_params: dict[str, Any]
) -> list[dict[str, Any]]:
    """The one entry point for answering: validate, assemble, run."""
    query = assemble(name, raw_params)
    return run_sql(con, query.sql, query.params)
