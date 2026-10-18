"""Maps internal profiling models onto the public API schema and derives user-facing warnings."""

from __future__ import annotations

from typing import Callable

from ..schemas import (
    AnalysisInfo, ColumnProfile, DatasetProfile, FailedFile, Relationship, SemanticModel, TableProfile, ValueCount,
)
from .models import ColumnStats, Link, TableModel, TableStats


def column_warnings(col: ColumnStats, role: str, rows: int) -> list[str]:
    warnings = []
    if col.null_count and rows:
        warnings.append(f"{col.name}: {col.null_count:,} missing values ({col.null_count / rows:.1%}).")
    if rows > 1 and col.distinct == 1:
        warnings.append(f"{col.name}: contains a single distinct value.")
    if col.outlier_count and role == "measure":
        warnings.append(f"{col.name}: {col.outlier_count:,} potential outliers by the IQR rule; review before excluding.")
    return warnings


def table_warnings(stats: TableStats, role_of: Callable[[str], str], primary_key: list[str]) -> list[str]:
    warnings: list[str] = []
    for col in stats.columns:
        warnings += column_warnings(col, role_of(col.name), stats.row_count)
    if stats.duplicate_rows:
        warnings.insert(0, f"{stats.duplicate_rows:,} exact duplicate rows.")
    if stats.row_count == 0:
        warnings.insert(0, "The file contains no data rows.")
    if not primary_key and stats.row_count:
        warnings.append("No column combination uniquely identifies a row.")
    return warnings


def column_stats_fields(col: ColumnStats) -> dict:
    """The measured (non-semantic) ColumnProfile fields for a column."""
    return dict(
        name=col.name, data_type=col.data_type,
        non_null_count=col.non_null, null_count=col.null_count, null_pct=col.null_pct,
        distinct_count=col.distinct, min=col.min, max=col.max, mean=col.mean, median=col.median,
        std=col.std, outlier_count=col.outlier_count,
        top_values=[ValueCount(value=v, count=n) for v, n in col.top_values] if col.top_values else None,
    )


def table_profile(model: TableModel) -> TableProfile:
    s, u = model.stats, model.understanding
    columns = []
    for col in s.columns:
        info = model.column_info(col.name)
        columns.append(ColumnProfile(
            role=info.role, semantic_type=info.semantic_type or None, description=info.description or None,
            unit=info.unit, references_entity=info.references_entity, **column_stats_fields(col),
        ))
    warnings = table_warnings(s, lambda name: model.column_info(name).role, model.primary_key)
    return TableProfile(
        table_name=s.table, file_name=s.file_name, file_size_bytes=s.file_size, row_count=s.row_count,
        column_count=len(s.columns), duplicate_row_count=s.duplicate_rows,
        entity=u.entity, description=u.description, grain=u.grain,
        primary_key=model.primary_key, primary_key_source=model.pk_source,
        columns=columns, warnings=warnings,
    )


def relationship(link: Link) -> Relationship:
    return Relationship(
        from_table=link.child_table, from_column=link.child_column,
        to_table=link.parent_table, to_column=link.parent_column,
        status=link.status, source=link.source, reason=link.reason, cardinality=link.cardinality,
        match_rate=link.evidence.containment, orphan_count=link.evidence.orphan_rows,
        parent_coverage=link.evidence.parent_coverage, fan_out=link.fan_out, join_warning=link.join_warning,
    )


def dataset_profile(
    tables: list[TableModel],
    links: list[Link],
    semantic_model: SemanticModel | None,
    failed: list[FailedFile],
    analysis: AnalysisInfo,
) -> DatasetProfile:
    order = {"accepted": 0, "needs_review": 1}
    return DatasetProfile(
        table_count=len(tables),
        total_rows=sum(t.stats.row_count for t in tables),
        tables=[table_profile(t) for t in tables],
        relationships=[relationship(l) for l in sorted(links, key=lambda l: (order[l.status], l.ref_from))],
        semantic_model=semantic_model,
        failed_files=failed,
        analysis=analysis,
    )
