"""DuckDB helpers shared by every profiling step."""

from __future__ import annotations

import datetime as dt
import math
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb

TypeFamily = Literal["number", "time", "bool", "string"]

NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
    "UBIGINT", "UHUGEINT", "FLOAT", "DOUBLE", "REAL", "DECIMAL",
}
TEMPORAL_TYPES = {
    "DATE", "TIME", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS",
}


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def safe_table_name(file_name: str, existing: set[str]) -> str:
    """Derives a unique, SQL-safe table name from a file name and reserves it in `existing`."""
    stem = Path(file_name).stem.lower()
    base = re.sub(r"[^a-z0-9]+", "_", stem).strip("_") or "table"
    if base[0].isdigit():
        base = f"t_{base}"
    name, suffix = base, 2
    while name in existing:
        name = f"{base}_{suffix}"
        suffix += 1
    existing.add(name)
    return name


def load_csv(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    # The path is server-generated, so only quote escaping is needed for the SQL literal.
    literal = str(path).replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TABLE {quote_ident(table)} AS "
        f"SELECT * FROM read_csv('{literal}', header = true, auto_detect = true, sample_size = -1)"
    )


def type_family(data_type: str) -> TypeFamily:
    base = data_type.upper().split("(")[0].strip()
    if base in NUMERIC_TYPES:
        return "number"
    if base in TEMPORAL_TYPES:
        return "time"
    if base == "BOOLEAN":
        return "bool"
    return "string"


def json_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return str(value)


def is_unique(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], row_count: int) -> bool:
    """True when the column set has no NULLs and no duplicate combinations."""
    if not columns or row_count == 0:
        return False
    cols = ", ".join(quote_ident(c) for c in columns)
    nulls = " OR ".join(f"{quote_ident(c)} IS NULL" for c in columns)
    distinct, null_rows = con.execute(
        f"SELECT (SELECT count(*) FROM (SELECT DISTINCT {cols} FROM {quote_ident(table)})), "
        f"(SELECT count(*) FROM {quote_ident(table)} WHERE {nulls})"
    ).fetchone()
    return distinct == row_count and null_rows == 0


def first_error_line(exc: Exception, limit: int = 300) -> str:
    return str(exc).splitlines()[0][:limit] if str(exc) else type(exc).__name__
