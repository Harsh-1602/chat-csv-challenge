"""P2: what each table and column means. LLM and heuristic implementations are interchangeable."""

from __future__ import annotations

import re
from typing import Protocol

from ..llm import StructuredLLM
from .contracts import ColumnUnderstanding, Role, TableUnderstanding
from .models import ColumnStats, TableStats
from .prompts import TABLE_UNDERSTANDING_SYSTEM, table_understanding_payload

IDENTIFIER_NAME = re.compile(r"(^id$|_id$|^id_|[a-z]Id$|_key$)")
DIMENSION_MAX_DISTINCT = 50


class TableInterpreter(Protocol):
    def interpret(self, stats: TableStats, other_tables: dict[str, list[str]]) -> TableUnderstanding: ...


def singular(name: str) -> str:
    n = re.sub(r"^(raw|stg|dim|fct|fact)_", "", name.lower())
    if n.endswith("ies"):
        return n[:-3] + "y"
    if n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def heuristic_role(col: ColumnStats, rows: int) -> Role:
    if IDENTIFIER_NAME.search(col.name) or IDENTIFIER_NAME.search(col.name.lower()):
        return "identifier"
    if col.family == "number":
        return "measure"
    if col.family == "time":
        return "time"
    if col.family == "bool":
        return "boolean"
    if col.distinct <= DIMENSION_MAX_DISTINCT and col.distinct < max(rows * 0.5, 2):
        return "dimension"
    return "text"


def heuristic_column(col: ColumnStats, rows: int) -> ColumnUnderstanding:
    return ColumnUnderstanding(
        name=col.name,
        role=heuristic_role(col, rows),
        semantic_type=col.data_type.lower(),
        description="",
        unit=None,
        references_entity=None,
    )


class HeuristicTableInterpreter:
    """Rule-based roles from names, types and cardinality. Used when AI is off or fails."""

    def interpret(self, stats: TableStats, other_tables: dict[str, list[str]]) -> TableUnderstanding:
        entity = singular(stats.table)
        return TableUnderstanding(
            entity=entity,
            description=f"{stats.row_count:,} rows from {stats.file_name}.",
            grain=f"one row per {entity}",
            primary_key=[],
            columns=[heuristic_column(c, stats.row_count) for c in stats.columns],
        )


class LLMTableInterpreter:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def interpret(self, stats: TableStats, other_tables: dict[str, list[str]]) -> TableUnderstanding:
        understanding = self._llm.parse(
            f"table_understanding:{stats.table}",
            TABLE_UNDERSTANDING_SYSTEM,
            table_understanding_payload(stats, other_tables),
            TableUnderstanding,
        )
        return align_columns(stats, understanding)


def align_columns(stats: TableStats, understanding: TableUnderstanding) -> TableUnderstanding:
    """Maps model output onto the real columns: exact names, file order, heuristic gap-filling."""
    by_name = {c.name.lower(): c for c in understanding.columns}
    columns = []
    for col in stats.columns:
        found = by_name.get(col.name.lower())
        columns.append(found.model_copy(update={"name": col.name}) if found else heuristic_column(col, stats.row_count))
    return understanding.model_copy(update={"columns": columns})
