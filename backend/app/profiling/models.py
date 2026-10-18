"""Internal value objects passed between profiling steps (not part of the API contract)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import ColumnUnderstanding, Role, TableUnderstanding
from .sql import TypeFamily


@dataclass
class ColumnStats:
    name: str
    data_type: str
    family: TypeFamily
    non_null: int
    distinct: int
    null_count: int
    null_pct: float
    unique: bool
    min: Any = None
    max: Any = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    outlier_count: int | None = None
    has_fractional_values: bool | None = None
    avg_length: float | None = None
    castable_to: str | None = None
    top_values: list[tuple[Any, int]] | None = None


@dataclass
class TableStats:
    table: str
    file_name: str
    file_size: int
    row_count: int
    columns: list[ColumnStats]
    duplicate_rows: int
    sample_rows: list[list[Any]]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnStats:
        return next(c for c in self.columns if c.name == name)


@dataclass
class TableModel:
    """Statistics plus the (LLM or heuristic) interpretation and the SQL-verified key."""

    stats: TableStats
    understanding: TableUnderstanding
    primary_key: list[str]
    pk_source: str
    _by_name: dict[str, ColumnUnderstanding] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_name = {c.name: c for c in self.understanding.columns}

    @property
    def name(self) -> str:
        return self.stats.table

    def column_info(self, column: str) -> ColumnUnderstanding:
        return self._by_name[column]

    def role(self, column: str) -> Role:
        return self._by_name[column].role


@dataclass(frozen=True)
class Containment:
    containment: float
    child_distinct: int
    child_non_null_rows: int
    orphan_rows: int
    parent_coverage: float


@dataclass
class LinkCandidate:
    id: int
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    name_similarity: float
    child_column_unique: bool
    evidence: Containment

    @property
    def ref_from(self) -> str:
        return f"{self.child_table}.{self.child_column}"

    @property
    def ref_to(self) -> str:
        return f"{self.parent_table}.{self.parent_column}"


@dataclass
class Link:
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    evidence: Containment
    status: str
    source: str
    reason: str
    cardinality: str = "many-to-one"
    fan_out: float | None = None
    join_warning: str | None = None

    @property
    def ref_from(self) -> str:
        return f"{self.child_table}.{self.child_column}"

    @property
    def ref_to(self) -> str:
        return f"{self.parent_table}.{self.parent_column}"
