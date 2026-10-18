"""Finds fixable data-quality issues (and informational ones) with SQL over the loaded tables."""

from __future__ import annotations

from collections import defaultdict

import duckdb

from ..profiling.sql import quote_ident as q
from ..schemas import ColumnProfile, DataIssue, DatasetProfile, TableProfile
from .sql import (
    CASE_VARIANT_MAX_DISTINCT, CASTABLE_SHARE, base_text, case_map_expr, date_expr, numeric_expr, tokens_list,
)

EXAMPLES = 3


def case_variant_map(con: duckdb.DuckDBPyConnection, table: str, column: str) -> dict[str, str]:
    """Maps lower(trim(value)) -> most frequent spelling, for keys that have more than one spelling."""
    rows = con.execute(
        f"SELECT lower(trim({q(column)})) k, trim({q(column)}) v, count(*) n FROM {q(table)} "
        f"WHERE {base_text(column)} IS NOT NULL GROUP BY 1, 2 ORDER BY 1, n DESC, 2"
    ).fetchall()
    spellings: dict[str, list[str]] = defaultdict(list)
    for key, value, _ in rows:
        spellings[key].append(value)
    return {k: v[0] for k, v in spellings.items() if len(v) > 1}


class IssueDetector:
    def detect(self, con: duckdb.DuckDBPyConnection, profile: DatasetProfile) -> list[DataIssue]:
        issues: list[DataIssue] = []
        for table in profile.tables:
            if table.row_count == 0:
                continue
            if table.duplicate_row_count:
                issues.append(DataIssue(
                    id=f"{table.table_name}.duplicate_rows", table=table.table_name, column=None,
                    kind="duplicate_rows", affected_rows=table.duplicate_row_count,
                    description=f"{table.duplicate_row_count:,} exact duplicate rows.",
                    fix="Remove duplicate rows, keeping one copy of each.",
                ))
            for column in table.columns:
                if column.data_type.upper() == "VARCHAR":
                    issues += self._text_issues(con, table, column)
            for column in table.columns:
                issues += self._informational(table, column)
        return issues

    def _text_issues(self, con: duckdb.DuckDBPyConnection, table: TableProfile, column: ColumnProfile) -> list[DataIssue]:
        t, name, c, base = table.table_name, column.name, q(column.name), base_text(column.name)
        ws, tok, nn, num, date, lead_zero = con.execute(
            f"SELECT count(*) FILTER (WHERE {c} <> trim({c})), "
            f"count(*) FILTER (WHERE lower(trim({c})) IN ({tokens_list()})), "
            f"count({base}), count({numeric_expr(base)}), count({date_expr(base)}), "
            f"count(*) FILTER (WHERE regexp_matches(trim({c}), '^0[0-9]')) "
            f"FROM {q(t)}"
        ).fetchone()

        def examples(condition: str) -> list[str]:
            rows = con.execute(f"SELECT DISTINCT {c} FROM {q(t)} WHERE {condition} LIMIT {EXAMPLES}").fetchall()
            return [repr(r[0]) for r in rows]

        def issue(kind: str, affected: int, description: str, fix: str, ex: list[str]) -> DataIssue:
            return DataIssue(id=f"{t}.{name}.{kind}", table=t, column=name, kind=kind, affected_rows=affected,
                             description=f"{name}: {description}", fix=fix, examples=ex)

        found: list[DataIssue] = []
        if ws:
            found.append(issue("whitespace", ws, f"{ws:,} values have leading or trailing spaces.",
                               "Trim the spaces.", examples(f"{c} <> trim({c})")))
        if tok:
            found.append(issue("null_tokens", tok, f"{tok:,} values are placeholders for missing data.",
                               "Treat placeholders as missing (NULL).",
                               examples(f"lower(trim({c})) IN ({tokens_list()})")))

        typed = False
        if nn and column.role != "identifier":
            if not lead_zero and num / nn >= CASTABLE_SHARE:
                bad = nn - num
                found.append(issue(
                    "numeric_text", nn, "numbers stored as text.",
                    "Convert to a numeric column" + (f"; {bad:,} unparseable values become NULL." if bad else "."),
                    examples(f"{base} IS NOT NULL AND TRY_CAST({c} AS DOUBLE) IS NULL"),
                ))
                typed = True
            elif date / nn >= CASTABLE_SHARE:
                bad = nn - date
                found.append(issue(
                    "date_text", nn, "dates stored as text (possibly in mixed formats).",
                    "Convert to a date column" + (f"; {bad:,} unparseable values become NULL." if bad else "."),
                    examples(f"{base} IS NOT NULL AND TRY_CAST({c} AS DATE) IS NULL"),
                ))
                typed = True

        if not typed and column.distinct_count <= CASE_VARIANT_MAX_DISTINCT:
            mapping = case_variant_map(con, t, name)
            if mapping:
                affected = con.execute(
                    f"SELECT count(*) FROM {q(t)} WHERE {c} IS NOT NULL AND {c} <> {case_map_expr(c, mapping)}"
                ).fetchone()[0]
                if affected:
                    shown = [f"{v!r}" for v in list(mapping.values())[:EXAMPLES]]
                    found.append(issue(
                        "case_variants", affected,
                        f"{len(mapping)} value(s) are spelled with different casing or spacing.",
                        "Merge the spellings into the most common form.", shown,
                    ))
        return found

    @staticmethod
    def _informational(table: TableProfile, column: ColumnProfile) -> list[DataIssue]:
        t, rows = table.table_name, table.row_count
        found = []
        if column.null_count:
            found.append(DataIssue(
                id=f"{t}.{column.name}.missing_values", table=t, column=column.name, kind="missing_values",
                affected_rows=column.null_count, fix=None,
                description=f"{column.name}: {column.null_count:,} missing values ({column.null_count / rows:.1%}).",
            ))
        if column.outlier_count and column.role == "measure":
            found.append(DataIssue(
                id=f"{t}.{column.name}.outliers", table=t, column=column.name, kind="outliers",
                affected_rows=column.outlier_count, fix=None,
                description=f"{column.name}: {column.outlier_count:,} potential outliers (IQR rule); kept as-is.",
            ))
        return found
