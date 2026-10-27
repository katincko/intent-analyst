from pathlib import Path

import duckdb
import pytest

from intent_analyst.db import TABLE_COLUMNS

ROOT = Path(__file__).resolve().parent.parent
CSV = (ROOT / "data" / "synthea" / "patients.csv").as_posix()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO patients (id) VALUES ('x')",
        "DELETE FROM patients",
        "UPDATE patients SET gender = 'X'",
        "CREATE TABLE evil (a INTEGER)",
        "DROP TABLE patients",
    ],
)
def test_database_is_read_only(con: duckdb.DuckDBPyConnection, statement: str) -> None:
    with pytest.raises(duckdb.Error, match="read-only"):
        con.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        f"SELECT * FROM read_csv('{CSV}')",
        "SELECT * FROM read_text('README.md')",
        "SELECT * FROM glob('*')",
        "SELECT * FROM read_csv('https://example.com/x.csv')",
        "ATTACH 'other.duckdb' AS other",
        "INSTALL httpfs",
        "LOAD httpfs",
    ],
)
def test_no_file_or_network_access(con: duckdb.DuckDBPyConnection, statement: str) -> None:
    with pytest.raises(duckdb.Error):
        con.execute(statement)


def test_export_to_a_file_is_refused(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    target = tmp_path / "leak.csv"
    with pytest.raises(duckdb.Error):
        con.execute(f"COPY patients TO '{target.as_posix()}'")
    assert not target.exists()


@pytest.mark.parametrize(
    "statement",
    ["SET enable_external_access = true", "SET threads = 1", "RESET enable_external_access"],
)
def test_configuration_cannot_change(con: duckdb.DuckDBPyConnection, statement: str) -> None:
    with pytest.raises(duckdb.Error, match="locked"):
        con.execute(statement)
    with pytest.raises(duckdb.Error):  # and external access is still shut
        con.execute(f"SELECT * FROM read_csv('{CSV}')")


def test_no_direct_identifier_is_loaded() -> None:
    forbidden = {"ssn", "drivers", "passport", "address", "lat", "lon", "zip", "maiden"}
    loaded = {c for cols in TABLE_COLUMNS.values() for c in cols}
    assert not forbidden & loaded


def _setting(con: duckdb.DuckDBPyConnection, name: str) -> object:
    row = con.execute("SELECT current_setting(?)", [name]).fetchone()
    assert row
    return row[0]


def test_resource_caps_are_applied(con: duckdb.DuckDBPyConnection) -> None:
    # Literals on purpose: comparing with the constants from the code would pass for any value.
    assert _setting(con, "threads") == 2
    assert _setting(con, "memory_limit") == "244.1 MiB"  # 256MB as DuckDB reports it


@pytest.mark.parametrize("statement", ["SET memory_limit = '8GB'", "SET threads = 64"])
def test_resource_caps_cannot_be_raised(con: duckdb.DuckDBPyConnection, statement: str) -> None:
    before = (_setting(con, "memory_limit"), _setting(con, "threads"))
    with pytest.raises(duckdb.Error, match="locked"):
        con.execute(statement)
    assert (_setting(con, "memory_limit"), _setting(con, "threads")) == before


@pytest.mark.parametrize("flag", ["autoload_known_extensions", "autoinstall_known_extensions"])
def test_extension_autoload_and_autoinstall_are_off(
    con: duckdb.DuckDBPyConnection, flag: str
) -> None:
    assert _setting(con, flag) is False
    with pytest.raises(duckdb.Error, match="locked"):
        con.execute(f"SET {flag} = true")


def test_a_failed_query_does_not_poison_the_connection(con: duckdb.DuckDBPyConnection) -> None:
    """DuckDB 2.0: a failed `execute()` aborts the transaction it ran in, and later statements
    raise TransactionException until a rollback. The agent reuses one connection after a failed
    tool query, so it must stay in autocommit (never BEGIN) for the next query to run.
    https://duckdb.org/docs/2.0/clients/python/migration.html
    """
    with pytest.raises(duckdb.CatalogException):
        con.execute("SELECT * FROM no_such_table")
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)


def test_the_poisoning_exists_inside_an_explicit_transaction(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Keeps the test above honest: with BEGIN the same failure does poison the connection."""
    con.execute("BEGIN")
    with pytest.raises(duckdb.CatalogException):
        con.execute("SELECT * FROM no_such_table")
    with pytest.raises(duckdb.TransactionException):
        con.execute("SELECT count(*) FROM patients")
    con.execute("ROLLBACK")
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)
