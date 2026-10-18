"""Applies approved fixes by rebuilding each affected table, then refreshes its profile statistics.

The cleaned table keeps the original name (so the schema context and semantic model stay valid); the raw data is
kept as `<table>__raw`, which is outside the chat schema context and therefore rejected by the SQL guard.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ..profiling.assembler import column_stats_fields, table_warnings
from ..profiling.semantics import MetricValidator
from ..profiling.sql import quote_ident as q, type_family
from ..profiling.statistics import StatisticsCollector
from ..schemas import ColumnProfile, DataIssue, DatasetProfile, SemanticModel, TableProfile
from .detector import IssueDetector, case_variant_map
from .sql import case_map_expr, date_expr, nullify_tokens, numeric_expr

COLUMN_FIX_ORDER = ("whitespace", "null_tokens", "case_variants", "numeric_text", "date_text")
RAW_SUFFIX = "__raw"


@dataclass
class CleaningResult:
    profile: DatasetProfile
    issues: list[DataIssue]
    applied: list[str]


def _post_cleaning_semantics(
    con: duckdb.DuckDBPyConnection, profile: DatasetProfile, applied: list[str]
) -> SemanticModel | None:
    """The semantic model was written for the raw data: re-check its metrics and record which fixes are done,
    so the chat agent neither trusts metrics that no longer run nor re-applies cleaning in its SQL."""
    semantic = profile.semantic_model
    if semantic is None:
        return None
    validator = MetricValidator(con, {t.table_name for t in profile.tables})
    note = ("Data cleaning was already applied; these issues are resolved in the tables, so do not re-clean them "
            "in SQL (no TRIM/UPPER/REPLACE/TRY_CAST or dedup for them): " + "; ".join(applied))
    return semantic.model_copy(update={
        "metrics": validator.revalidate(semantic.metrics),
        "caveats": [note, *semantic.caveats],
    })


class DataCleaner:
    def __init__(self, detector: IssueDetector | None = None) -> None:
        self._detector = detector or IssueDetector()

    def detect(self, database_path: Path, profile: DatasetProfile) -> list[DataIssue]:
        con = duckdb.connect(str(database_path), read_only=True)
        try:
            return self._detector.detect(con, profile)
        finally:
            con.close()

    def apply(
        self, database_path: Path, profile: DatasetProfile, issues: list[DataIssue], selected_ids: list[str]
    ) -> CleaningResult:
        selected = [i for i in issues if i.id in set(selected_ids) and i.fix is not None]
        if not selected:
            return CleaningResult(profile, issues, [])
        by_table: dict[str, list[DataIssue]] = defaultdict(list)
        for issue in selected:
            by_table[issue.table].append(issue)

        con = duckdb.connect(str(database_path))
        try:
            tables = []
            for table in profile.tables:
                if table.table_name in by_table:
                    self._rebuild(con, table, by_table[table.table_name])
                    table = self._refresh(con, table)
                tables.append(table)
            applied = [f"{i.table}: {i.fix.rstrip('.')} ({i.description})" for i in selected]
            refreshed = profile.model_copy(update={
                "tables": tables, "total_rows": sum(t.row_count for t in tables),
                "semantic_model": _post_cleaning_semantics(con, profile, applied),
            })
            remaining = self._detector.detect(con, refreshed)
        finally:
            con.close()
        return CleaningResult(refreshed, remaining, applied)

    def _rebuild(self, con: duckdb.DuckDBPyConnection, table: TableProfile, fixes: list[DataIssue]) -> None:
        name = table.table_name
        kinds_by_column: dict[str, set[str]] = defaultdict(set)
        for fix in fixes:
            if fix.column is not None:
                kinds_by_column[fix.column].add(fix.kind)
        distinct = any(f.kind == "duplicate_rows" for f in fixes)

        exprs = []
        for column in table.columns:
            expr = q(column.name)
            kinds = kinds_by_column.get(column.name, set())
            for kind in COLUMN_FIX_ORDER:
                if kind not in kinds:
                    continue
                if kind == "whitespace":
                    expr = f"trim({expr})"
                elif kind == "null_tokens":
                    expr = nullify_tokens(expr)
                elif kind == "case_variants":
                    mapping = case_variant_map(con, name, column.name)
                    expr = case_map_expr(expr, mapping) if mapping else expr
                elif kind == "numeric_text":
                    expr = self._numeric(con, name, expr)
                elif kind == "date_text":
                    expr = date_expr(expr)
            exprs.append(f"{expr} AS {q(column.name)}")

        clean, raw = q(f"{name}__clean"), q(f"{name}{RAW_SUFFIX}")
        select = "SELECT DISTINCT" if distinct else "SELECT"
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DROP TABLE IF EXISTS {clean}")
            con.execute(f"CREATE TABLE {clean} AS {select} {', '.join(exprs)} FROM {q(name)}")
            con.execute(f"DROP TABLE IF EXISTS {raw}")
            con.execute(f"ALTER TABLE {q(name)} RENAME TO {raw}")
            con.execute(f"ALTER TABLE {clean} RENAME TO {q(name)}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    @staticmethod
    def _numeric(con: duckdb.DuckDBPyConnection, table: str, expr: str) -> str:
        as_double, fractional, as_int = con.execute(
            f"SELECT count({numeric_expr(expr)}), "
            f"count(*) FILTER (WHERE {numeric_expr(expr)} <> round({numeric_expr(expr)})), "
            f"count({numeric_expr(expr, 'BIGINT')}) FROM {q(table)}"
        ).fetchone()
        return numeric_expr(expr, "BIGINT" if not fractional and as_int == as_double else "DOUBLE")

    @staticmethod
    def _refresh(con: duckdb.DuckDBPyConnection, table: TableProfile) -> TableProfile:
        stats = StatisticsCollector(con).collect(table.table_name, table.file_name, table.file_size_bytes)
        old = {c.name: c for c in table.columns}
        columns = []
        for col in stats.columns:
            before = old[col.name]
            role = before.role
            if type_family(col.data_type) != type_family(before.data_type):
                role = {"number": "measure" if role != "identifier" else role, "time": "time"}.get(col.family, role)
            columns.append(ColumnProfile(**{
                **before.model_dump(), **column_stats_fields(col), "role": role,
            }))
        roles = {c.name: c.role for c in columns}
        primary_key, source = table.primary_key, table.primary_key_source
        if not primary_key and stats.row_count:
            # Removing duplicates can make a key column unique again.
            unique = [c for c in columns if c.distinct_count == stats.row_count and c.null_count == 0]
            unique.sort(key=lambda c: c.role != "identifier")
            if unique:
                primary_key, source = [unique[0].name], "sql_fallback"
        return table.model_copy(update={
            "row_count": stats.row_count,
            "duplicate_row_count": stats.duplicate_rows,
            "columns": columns,
            "primary_key": primary_key,
            "primary_key_source": source,
            "warnings": table_warnings(stats, roles.__getitem__, primary_key),
        })
