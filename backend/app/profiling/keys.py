"""P2 verification: SQL decides whether a proposed primary key is actually unique."""

from __future__ import annotations

import itertools
import re

import duckdb

from .contracts import Role
from .models import TableStats
from .sql import is_unique

MAX_COMPOSITE_KEY = 3


class PrimaryKeyResolver:
    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self._con = con

    def resolve(self, stats: TableStats, proposed: list[str], roles: dict[str, Role]) -> tuple[list[str], str]:
        """Returns (key columns, source). Source is llm_verified | llm_minimised | sql_fallback | none."""
        lookup = {n.lower(): n for n in stats.column_names}
        proposed = list(dict.fromkeys(lookup[p.lower()] for p in proposed if p.lower() in lookup))
        if proposed and self._unique(stats, proposed):
            minimal = self._minimise(stats, proposed)
            return (minimal, "llm_minimised") if minimal != proposed else (proposed, "llm_verified")

        singles = [c.name for c in stats.columns if c.unique and c.family != "time"]
        position = {n: i for i, n in enumerate(stats.column_names)}
        singles.sort(key=lambda n: (roles.get(n) != "identifier", not _names_table(n, stats.table), position[n]))
        if singles:
            return [singles[0]], "sql_fallback"

        pool = [n for n in stats.column_names if roles.get(n) == "identifier"] or stats.column_names
        for size in range(2, MAX_COMPOSITE_KEY + 1):
            for combo in itertools.combinations(pool, size):
                if self._unique(stats, list(combo)):
                    return list(combo), "sql_fallback"
        return [], "none"

    def _minimise(self, stats: TableStats, columns: list[str]) -> list[str]:
        for size in range(1, len(columns)):
            for combo in itertools.combinations(columns, size):
                if self._unique(stats, list(combo)):
                    return list(combo)
        return columns

    def _unique(self, stats: TableStats, columns: list[str]) -> bool:
        return is_unique(self._con, stats.table, columns, stats.row_count)


def _names_table(column: str, table: str) -> bool:
    """`payment_id` / `PaymentId` / `id` in table `payment(s)` look like that table's own key."""
    core = re.sub(r"_?id$", "", column.lower()).strip("_")
    table = table.lower()
    return core in ("", table, table.rstrip("s"), table.removesuffix("es"))
