"""Frozen v1 rule-based profiler (pre-LLM). Kept only as the experiment baseline; the app uses app.profiling."""

import datetime as dt
import math
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from .v1_schemas import (
    ColumnProfile,
    ColumnRole,
    DatasetProfile,
    FailedFile,
    Relationship,
    Scalar,
    TableProfile,
    ValueCount,
)

NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL",
}
DATETIME_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_S", "TIMESTAMP_MS", "TIMESTAMP_NS"}
IDENTIFIER_NAME = re.compile(r"(^id$|_id$|^id_|[a-z]Id$)", re.IGNORECASE)
MAX_CATEGORICAL_DISTINCT = 50
MAX_TOP_VALUES = 5
MIN_RELATIONSHIP_MATCH_RATE = 0.5


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def safe_table_name(file_name: str, existing: set[str]) -> str:
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


def _base_type(data_type: str) -> str:
    return data_type.upper().split("(")[0].strip()


def _json_scalar(value: Any) -> Scalar:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return str(value)


def _float(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)


def load_csv(con: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    # The path is server-generated, so only quote escaping is needed for the SQL literal.
    literal = str(path).replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE TABLE {quote_ident(table)} AS "
        f"SELECT * FROM read_csv('{literal}', header = true, auto_detect = true, sample_size = -1)"
    )


def _initial_role(name: str, data_type: str) -> ColumnRole:
    base = _base_type(data_type)
    if IDENTIFIER_NAME.search(name):
        return "identifier"
    if base == "BOOLEAN":
        return "boolean"
    if base in NUMERIC_TYPES:
        return "numeric"
    if base in DATETIME_TYPES:
        return "datetime"
    return "text"


def profile_table(con: duckdb.DuckDBPyConnection, table: str, file_name: str, file_size: int) -> TableProfile:
    qt = quote_ident(table)
    schema = con.execute(f"DESCRIBE {qt}").fetchall()
    columns = [(row[0], row[1]) for row in schema]
    row_count = con.execute(f"SELECT count(*) FROM {qt}").fetchone()[0]

    select_parts: list[str] = []
    for i, (name, data_type) in enumerate(columns):
        qc = quote_ident(name)
        role = _initial_role(name, data_type)
        select_parts += [f"count({qc}) AS nn_{i}", f"count(DISTINCT {qc}) AS d_{i}"]
        if role == "numeric":
            select_parts += [
                f"min({qc}) AS min_{i}", f"max({qc}) AS max_{i}",
                f"avg({qc}::DOUBLE) AS mean_{i}", f"stddev_samp({qc}::DOUBLE) AS std_{i}",
                f"quantile_cont({qc}::DOUBLE, 0.25) AS q1_{i}", f"quantile_cont({qc}::DOUBLE, 0.5) AS q2_{i}",
                f"quantile_cont({qc}::DOUBLE, 0.75) AS q3_{i}",
            ]
        elif role in ("datetime", "identifier"):
            select_parts += [f"min({qc}) AS min_{i}", f"max({qc}) AS max_{i}"]

    stats: dict[str, Any] = {}
    if columns:
        cursor = con.execute(f"SELECT {', '.join(select_parts)} FROM {qt}")
        stats = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))

    outlier_bounds: dict[int, tuple[float, float]] = {}
    for i, (name, data_type) in enumerate(columns):
        if _initial_role(name, data_type) == "numeric" and stats.get(f"q1_{i}") is not None:
            q1, q3 = float(stats[f"q1_{i}"]), float(stats[f"q3_{i}"])
            iqr = q3 - q1
            if iqr > 0:
                outlier_bounds[i] = (q1 - 1.5 * iqr, q3 + 1.5 * iqr)

    outliers: dict[str, Any] = {}
    if outlier_bounds:
        parts = [
            f"count(*) FILTER (WHERE {quote_ident(columns[i][0])}::DOUBLE < {lo!r} "
            f"OR {quote_ident(columns[i][0])}::DOUBLE > {hi!r}) AS o_{i}"
            for i, (lo, hi) in outlier_bounds.items()
        ]
        cursor = con.execute(f"SELECT {', '.join(parts)} FROM {qt}")
        outliers = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))

    profiles: list[ColumnProfile] = []
    warnings: list[str] = []
    for i, (name, data_type) in enumerate(columns):
        role = _initial_role(name, data_type)
        non_null = stats[f"nn_{i}"]
        distinct = stats[f"d_{i}"]
        null_count = row_count - non_null
        if role == "text" and distinct <= MAX_CATEGORICAL_DISTINCT and distinct < max(row_count * 0.5, 2):
            role = "categorical"

        top_values = None
        if role in ("categorical", "boolean"):
            rows = con.execute(
                f"SELECT {quote_ident(name)} AS v, count(*) AS n FROM {qt} WHERE {quote_ident(name)} IS NOT NULL "
                f"GROUP BY v ORDER BY n DESC, v LIMIT {MAX_TOP_VALUES}"
            ).fetchall()
            top_values = [ValueCount(value=_json_scalar(v), count=n) for v, n in rows]

        outlier_count = outliers.get(f"o_{i}", 0) if role == "numeric" else None
        profiles.append(
            ColumnProfile(
                name=name,
                data_type=data_type,
                role=role,
                non_null_count=non_null,
                null_count=null_count,
                null_pct=round(null_count / row_count * 100, 2) if row_count else 0.0,
                distinct_count=distinct,
                min=_json_scalar(stats.get(f"min_{i}")),
                max=_json_scalar(stats.get(f"max_{i}")),
                mean=_float(stats.get(f"mean_{i}")),
                median=_float(stats.get(f"q2_{i}")),
                std=_float(stats.get(f"std_{i}")),
                outlier_count=outlier_count,
                top_values=top_values,
            )
        )

        if null_count:
            warnings.append(f"{name}: {null_count:,} missing values ({null_count / row_count:.1%}).")
        if row_count > 1 and distinct == 1:
            warnings.append(f"{name}: contains a single distinct value.")
        if outlier_count:
            warnings.append(f"{name}: {outlier_count:,} potential outliers by the IQR rule; review before excluding.")

    duplicate_rows = 0
    if row_count:
        duplicate_rows = row_count - con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {qt})").fetchone()[0]
    if duplicate_rows:
        warnings.insert(0, f"{duplicate_rows:,} exact duplicate rows.")
    if row_count == 0:
        warnings.insert(0, "The file contains no data rows.")

    key_candidates = [c for c in profiles if c.role == "identifier" and c.null_count == 0 and c.distinct_count == row_count]
    primary_key = key_candidates[0].name if key_candidates and row_count else None

    return TableProfile(
        table_name=table,
        file_name=file_name,
        file_size_bytes=file_size,
        row_count=row_count,
        column_count=len(columns),
        duplicate_row_count=duplicate_rows,
        primary_key=primary_key,
        columns=profiles,
        warnings=warnings,
    )


def detect_relationships(con: duckdb.DuckDBPyConnection, tables: list[TableProfile]) -> list[Relationship]:
    relationships: list[Relationship] = []
    for parent in tables:
        if not parent.primary_key:
            continue
        pk = parent.primary_key
        for child in tables:
            if child.table_name == parent.table_name:
                continue
            for column in child.columns:
                if column.name.lower() != pk.lower() or column.non_null_count == 0:
                    continue
                child_col, parent_col = quote_ident(column.name), quote_ident(pk)
                try:
                    total, orphans = con.execute(
                        f"SELECT count(c.{child_col}), count(c.{child_col}) FILTER (WHERE p.k IS NULL) "
                        f"FROM {quote_ident(child.table_name)} c "
                        f"LEFT JOIN (SELECT DISTINCT {parent_col} AS k FROM {quote_ident(parent.table_name)}) p "
                        f"ON c.{child_col} = p.k"
                    ).fetchone()
                except duckdb.Error:
                    continue
                match_rate = (total - orphans) / total if total else 0.0
                if match_rate < MIN_RELATIONSHIP_MATCH_RATE:
                    continue
                relationships.append(
                    Relationship(
                        from_table=child.table_name,
                        from_column=column.name,
                        to_table=parent.table_name,
                        to_column=pk,
                        cardinality="one-to-one" if column.distinct_count == column.non_null_count else "many-to-one",
                        match_rate=round(match_rate, 4),
                        orphan_count=orphans,
                    )
                )
    return relationships


def profile_dataset(
    database_path: Path,
    files: list[tuple[str, str, Path, int]],
    on_progress: Callable[[str, str, str | None], None],
) -> DatasetProfile:
    """Profile (file_name, table_name, path, size) entries; progress events are (table, phase, error)."""
    tables: list[TableProfile] = []
    failures: list[FailedFile] = []
    with duckdb.connect(str(database_path)) as con:
        for file_name, table, path, size in files:
            try:
                on_progress(table, "loading", None)
                load_csv(con, table, path)
                on_progress(table, "profiling", None)
                tables.append(profile_table(con, table, file_name, size))
                on_progress(table, "completed", None)
            except duckdb.Error as exc:
                message = str(exc).splitlines()[0][:300]
                failures.append(FailedFile(file_name=file_name, error=message))
                on_progress(table, "failed", message)
        on_progress("", "relationships", None)
        relationships = detect_relationships(con, tables)

    return DatasetProfile(
        table_count=len(tables),
        total_rows=sum(t.row_count for t in tables),
        tables=tables,
        relationships=relationships,
        failed_files=failures,
    )
