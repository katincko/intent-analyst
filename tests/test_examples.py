import duckdb
import pytest

from intent_analyst.catalog import INTENTS
from intent_analyst.db import run_intent


@pytest.mark.parametrize("name", list(INTENTS))
def test_example_question_returns_rows(con: duckdb.DuckDBPyConnection, name: str) -> None:
    rows = run_intent(con, name, INTENTS[name].example_params)
    assert rows, f"{name}: {INTENTS[name].example_question}"


def test_counts_add_up_to_the_population(con: duckdb.DuckDBPyConnection) -> None:
    assert con.execute("SELECT count(*) FROM patients").fetchone() == (53,)
    by_gender = run_intent(con, "count_patients", {"group_by": "gender"})
    assert sum(r["patients"] for r in by_gender) == 53
    bands = run_intent(con, "patients_by_age_band", {"band_years": 20})
    assert sum(r["patients"] for r in bands) == 53


def test_limit_is_respected(con: duckdb.DuckDBPyConnection) -> None:
    assert len(run_intent(con, "top_conditions", {"limit": 3})) == 3
