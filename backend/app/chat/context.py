"""C1 schema context (profiling-design P8): a compact, prompt-ready description of the dataset.

Format follows DAIL-SQL / M-Schema: CREATE TABLE-like blocks with keys, roles, units, ranges and
sample values, then the relationship graph, validated metrics, synonyms and caveats.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schemas import ColumnProfile, DatasetProfile, Relationship, TableProfile

MAX_SAMPLE_VALUES = 8
MAX_VALUE_CHARS = 40


@dataclass(frozen=True)
class TimeRange:
    table: str
    column: str
    min: str
    max: str


@dataclass(frozen=True)
class SchemaContext:
    text: str
    tables: frozenset[str]
    columns: dict[str, frozenset[str]]
    time_ranges: tuple[TimeRange, ...]
    relationships: tuple[Relationship, ...]

    @property
    def latest_date(self) -> str | None:
        return max((r.max for r in self.time_ranges), default=None)


def _value(value: object) -> str:
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        text = text[: MAX_VALUE_CHARS - 1] + "…"
    return f"'{text}'" if isinstance(value, str) else text


def _foreign_keys(profile: DatasetProfile) -> dict[tuple[str, str], Relationship]:
    return {(r.from_table, r.from_column): r for r in profile.relationships if r.status == "accepted"}


def _column_line(table: TableProfile, column: ColumnProfile, fks: dict[tuple[str, str], Relationship]) -> str:
    head = f"  {column.name} {column.data_type}"
    if column.name in table.primary_key and len(table.primary_key) == 1:
        head += " PRIMARY KEY"
    notes: list[str] = [column.role]
    if column.description:
        notes.append(column.description.rstrip("."))
    if column.unit:
        notes.append(f"unit: {column.unit}")
    fk = fks.get((table.table_name, column.name))
    if fk:
        notes.append(f"FK -> {fk.to_table}.{fk.to_column}")
    if column.role in ("measure", "time") and column.min is not None:
        notes.append(f"range {column.min} .. {column.max}")
    if column.role in ("dimension", "boolean") and column.top_values:
        shown = ", ".join(_value(v.value) for v in column.top_values[:MAX_SAMPLE_VALUES])
        more = column.distinct_count - min(len(column.top_values), MAX_SAMPLE_VALUES)
        notes.append(f"values: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    elif column.role == "text" and column.top_values:
        notes.append("e.g. " + ", ".join(_value(v.value) for v in column.top_values[:3]))
    if column.null_pct > 0:
        notes.append(f"{column.null_pct:.1f}% null")
    if column.outlier_count:
        notes.append(f"{column.outlier_count} outliers")
    return f"{head}  -- " + "; ".join(notes)


def _table_block(table: TableProfile, fks: dict[tuple[str, str], Relationship]) -> list[str]:
    lines = [f"TABLE {table.table_name}  -- {table.description.rstrip('.')}. Grain: {table.grain}. {table.row_count} rows."]
    lines += [_column_line(table, c, fks) for c in table.columns]
    if len(table.primary_key) > 1:
        lines.append(f"  PRIMARY KEY ({', '.join(table.primary_key)})")
    return lines


def _relationship_line(r: Relationship) -> str:
    line = f"  {r.from_table}.{r.from_column} -> {r.to_table}.{r.to_column} ({r.cardinality}"
    if r.fan_out:
        line += f", avg {r.fan_out:g} {r.from_table} rows per {r.to_table} row"
    line += ")"
    if r.status != "accepted":
        line += " [unconfirmed]"
    if r.join_warning:
        line += f"  WARNING: {r.join_warning}"
    return line


def build_schema_context(profile: DatasetProfile) -> SchemaContext:
    fks = _foreign_keys(profile)
    lines: list[str] = []
    semantic = profile.semantic_model
    if semantic and semantic.dataset_summary:
        lines += [f"DATASET: {semantic.dataset_summary}", ""]

    for table in profile.tables:
        lines += _table_block(table, fks) + [""]

    if profile.relationships:
        lines.append("RELATIONSHIPS (many side -> one side):")
        lines += [_relationship_line(r) for r in profile.relationships]
        lines.append("")

    time_ranges = tuple(
        TimeRange(t.table_name, c.name, str(c.min), str(c.max))
        for t in profile.tables
        for c in t.columns
        if c.role == "time" and c.min is not None and c.max is not None
    )
    if time_ranges:
        lines.append("DATA TIME RANGE:")
        lines += [f"  {r.table}.{r.column}: {r.min} .. {r.max}" for r in time_ranges]
        lines.append("")

    if semantic:
        metrics = [m for m in semantic.metrics if m.valid]
        if metrics:
            lines.append("METRICS (validated SQL expressions):")
            lines += [f"  {m.name} = {m.expression}  [table {m.table}] -- {m.description}" for m in metrics]
            lines.append("")
        if semantic.synonyms:
            lines.append("SYNONYMS:")
            lines += [f"  '{s.term}' means {s.maps_to}" for s in semantic.synonyms]
            lines.append("")
        if semantic.caveats:
            lines.append("CAVEATS:")
            lines += [f"  - {c}" for c in semantic.caveats]
            lines.append("")

    return SchemaContext(
        text="\n".join(lines).strip(),
        tables=frozenset(t.table_name for t in profile.tables),
        columns={t.table_name: frozenset(c.name for c in t.columns) for t in profile.tables},
        time_ranges=time_ranges,
        relationships=tuple(profile.relationships),
    )
