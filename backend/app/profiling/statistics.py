"""P1: column statistics, top values and a deterministic row sample, all computed in DuckDB."""

from __future__ import annotations

from typing import Any

import duckdb

from .models import ColumnStats, TableStats
from .sql import json_scalar, quote_ident as q, type_family

SAMPLE_ROWS = 12
TOP_VALUES_LIMIT = 6
TOP_VALUES_MAX_DISTINCT = 50
CASTABLE_SHARE = 0.98


class StatisticsCollector:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def collect(self, table: str, file_name: str, file_size: int) -> TableStats:
        con = self._con
        described = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {q(table)}").fetchall()]
        rows = con.execute(f"SELECT count(*) FROM {q(table)}").fetchone()[0]
        aggregates = self._aggregates(table, described) if described else {}
        columns = [self._column(table, i, name, dtype, rows, aggregates) for i, (name, dtype) in enumerate(described)]
        duplicates = 0
        if rows:
            distinct_rows = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {q(table)})").fetchone()[0]
            duplicates = rows - distinct_rows
        sample = con.execute(
            f"SELECT * FROM {q(table)} USING SAMPLE {SAMPLE_ROWS} ROWS (reservoir, 42)"
        ).fetchall()
        return TableStats(
            table=table,
            file_name=file_name,
            file_size=file_size,
            row_count=rows,
            columns=columns,
            duplicate_rows=duplicates,
            sample_rows=[list(r) for r in sample],
        )

    def _aggregates(self, table: str, described: list[tuple[str, str]]) -> dict[str, Any]:
        parts: list[str] = []
        for i, (name, dtype) in enumerate(described):
            c, fam = q(name), type_family(dtype)
            parts += [f"count({c}) AS nn{i}", f"count(DISTINCT {c}) AS d{i}"]
            if fam in ("number", "time"):
                parts += [f"min({c}) AS mn{i}", f"max({c}) AS mx{i}"]
            if fam == "number":
                parts += [
                    f"avg({c}::DOUBLE) AS av{i}",
                    f"median({c}::DOUBLE) AS md{i}",
                    f"stddev_samp({c}::DOUBLE) AS sd{i}",
                    f"quantile_cont({c}::DOUBLE, 0.25) AS q1{i}",
                    f"quantile_cont({c}::DOUBLE, 0.75) AS q3{i}",
                    f"count(*) FILTER (WHERE {c} <> round({c}::DOUBLE)) AS frac{i}",
                ]
            if fam == "string":
                parts += [
                    f"avg(length({c})) AS len{i}",
                    f"count(TRY_CAST({c} AS DOUBLE)) AS cnum{i}",
                    f"count(TRY_CAST({c} AS DATE)) AS cdate{i}",
                ]
        cur = self._con.execute(f"SELECT {', '.join(parts)} FROM {q(table)}")
        return dict(zip([d[0] for d in cur.description], cur.fetchone()))

    def _column(self, table: str, i: int, name: str, dtype: str, rows: int, s: dict[str, Any]) -> ColumnStats:
        fam = type_family(dtype)
        nn, distinct = s[f"nn{i}"], s[f"d{i}"]
        col = ColumnStats(
            name=name,
            data_type=dtype,
            family=fam,
            non_null=nn,
            distinct=distinct,
            null_count=rows - nn,
            null_pct=round((rows - nn) / rows * 100, 2) if rows else 0.0,
            unique=rows > 0 and nn == rows and distinct == rows,
        )
        if f"mn{i}" in s:
            col.min, col.max = json_scalar(s[f"mn{i}"]), json_scalar(s[f"mx{i}"])
        if s.get(f"av{i}") is not None:
            col.mean = round(float(s[f"av{i}"]), 3)
            col.median = _round(s[f"md{i}"])
            col.std = _round(s[f"sd{i}"])
            col.has_fractional_values = bool(s[f"frac{i}"])
            col.outlier_count = self._outliers(table, name, s[f"q1{i}"], s[f"q3{i}"])
        if s.get(f"len{i}") is not None:
            col.avg_length = round(float(s[f"len{i}"]), 1)
            if nn and s[f"cnum{i}"] / nn >= CASTABLE_SHARE:
                col.castable_to = "number"
            elif nn and s[f"cdate{i}"] / nn >= CASTABLE_SHARE:
                col.castable_to = "date"
        if distinct <= TOP_VALUES_MAX_DISTINCT and distinct < rows:
            top = self._con.execute(
                f"SELECT {q(name)}, count(*) n FROM {q(table)} WHERE {q(name)} IS NOT NULL "
                f"GROUP BY 1 ORDER BY n DESC, 1 LIMIT {TOP_VALUES_LIMIT}"
            ).fetchall()
            col.top_values = [(json_scalar(v), n) for v, n in top]
        return col

    def _outliers(self, table: str, column: str, q1: float | None, q3: float | None) -> int:
        if q1 is None or q3 is None:
            return 0
        iqr = q3 - q1
        low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return self._con.execute(
            f"SELECT count(*) FROM {q(table)} WHERE {q(column)}::DOUBLE < ? OR {q(column)}::DOUBLE > ?",
            [low, high],
        ).fetchone()[0]


def _round(value: Any) -> float | None:
    return round(float(value), 3) if value is not None else None
