"""C4 SQL guard: only a single read-only query over known tables may reach DuckDB.

Hard failures (`errors`) block execution. `warnings` are soft signals (e.g. a likely join fan-out)
fed back to the SQL agent so it can double-check, never silently "fixed".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope

from ..schemas import Relationship

DIALECT = "duckdb"

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command", "Copy",
    "Pragma", "Set", "Use", "Attach", "Detach", "Transaction", "Commit", "Rollback", "Install", "Load",
    "Export", "Into",
)
FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))

FORBIDDEN_FUNCTIONS = {
    "read_csv", "read_csv_auto", "read_parquet", "parquet_scan", "read_json", "read_json_auto",
    "read_ndjson", "read_text", "read_blob", "glob", "sqlite_scan", "postgres_scan", "mysql_scan",
    "query", "query_table", "getenv", "current_setting",
}


@dataclass(frozen=True)
class GuardResult:
    sql: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class SQLGuard:
    def __init__(self, columns: dict[str, frozenset[str]], relationships: tuple[Relationship, ...] = ()) -> None:
        self._columns = {t.lower(): {c.lower() for c in cols} for t, cols in columns.items()}
        self._relationships = [r for r in relationships if r.status == "accepted"]

    def check(self, sql: str) -> GuardResult:
        text = sql.strip().rstrip(";").strip()
        if not text:
            return GuardResult(sql, ["Empty query."])
        try:
            statements = [s for s in sqlglot.parse(text, read=DIALECT) if s is not None]
        except ParseError as exc:
            return GuardResult(text, [f"SQL could not be parsed: {str(exc).splitlines()[0]}"])
        if len(statements) != 1:
            return GuardResult(text, ["Exactly one SQL statement is allowed."])
        tree = statements[0]
        if not isinstance(tree, exp.Query):
            return GuardResult(text, ["Only SELECT queries are allowed."])

        errors = self._forbidden(tree) + self._unknown_tables(tree)
        warnings = [] if errors else self._fan_out_warnings(tree)
        return GuardResult(text, errors, warnings)

    def _forbidden(self, tree: exp.Expression) -> list[str]:
        errors: list[str] = []
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                errors.append(f"Statement type {type(node).__name__.upper()} is not allowed.")
            elif isinstance(node, exp.Func):
                name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
                if name in FORBIDDEN_FUNCTIONS:
                    errors.append(f"Function {name}() is not allowed.")
        return sorted(set(errors))

    def _unknown_tables(self, tree: exp.Expression) -> list[str]:
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        unknown = {
            t.name
            for t in tree.find_all(exp.Table)
            if t.name and t.name.lower() not in self._columns and t.name.lower() not in ctes
        }
        return [f"Unknown table: {name}." for name in sorted(unknown)]

    def _fan_out_warnings(self, tree: exp.Expression) -> list[str]:
        """Flags SUM/AVG of a one-side column in a scope that also joins a many-side child table."""
        warnings: set[str] = set()
        try:
            scopes = traverse_scope(tree)
        except Exception:  # scope analysis is best-effort
            return []
        for scope in scopes:
            tables = {
                alias.lower(): source.name.lower()
                for alias, source in scope.sources.items()
                if isinstance(source, exp.Table)
            }
            if len(tables) < 2:
                continue
            present = set(tables.values())
            for agg in scope.expression.find_all(exp.Sum, exp.Avg):
                if agg.find(exp.Distinct):
                    continue
                for column in agg.find_all(exp.Column):
                    owner = self._owner(column, tables)
                    if owner is None:
                        continue
                    for r in self._relationships:
                        child = r.from_table.lower()
                        if r.to_table.lower() == owner and child in present and child != owner:
                            warnings.add(
                                f"Possible join fan-out: {agg.sql(dialect=DIALECT)} aggregates {owner}.{column.name} "
                                f"while {child} (many rows per {owner} row) is joined in the same query. "
                                f"Aggregate {child} in a subquery first, or confirm the grain is correct."
                            )
        return sorted(warnings)

    def _owner(self, column: exp.Column, tables: dict[str, str]) -> str | None:
        if column.table:
            return tables.get(column.table.lower())
        owners = [t for t in set(tables.values()) if column.name.lower() in self._columns.get(t, set())]
        return owners[0] if len(owners) == 1 else None
