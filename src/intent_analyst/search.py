"""`search_values`: find the exact strings that exist in the data, by a parameterized search.

The model needs them because several intents match an exact condition or reason. The field is a
closed enum that picks a whole SQL statement written here; the search text is only ever bound.
"""

from typing import Any

import duckdb

from intent_analyst.catalog import Limit, Params, SqlEnum, Text
from intent_analyst.db import run_sql


def _distinct(table: str, column: str) -> str:
    return (
        f"SELECT t.{column} AS value, count(*) AS matches FROM {table} t "  # noqa: S608
        f"WHERE contains(lower(t.{column}), lower($text)) "
        "GROUP BY 1 ORDER BY matches DESC, value LIMIT $limit"
    )


class SearchField(SqlEnum):
    condition = ("condition", _distinct("conditions", "description"))
    medication = ("medication", _distinct("medications", "description"))
    medication_reason = ("medication_reason", _distinct("medications", "reason_description"))
    procedure = ("procedure", _distinct("procedures", "description"))
    encounter = ("encounter", _distinct("encounters", "description"))
    patient_name = (
        "patient_name",
        "SELECT t.first_name || ' ' || t.last_name AS value, t.id AS patient_id "
        "FROM patients t WHERE contains(lower(t.first_name || ' ' || t.last_name), lower($text)) "
        "ORDER BY value LIMIT $limit",
    )


class SearchValues(Params):
    field: SearchField
    text: Text
    limit: Limit = 10


def search_values(con: duckdb.DuckDBPyConnection, params: SearchValues) -> list[dict[str, Any]]:
    """Run the statement of the chosen field with the text and limit bound."""
    return run_sql(con, params.field.sql, {"text": params.text, "limit": params.limit})
