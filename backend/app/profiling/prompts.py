"""Prompt text and payload builders. Kept verbatim from experiment exp01 (scored 1.0 on all eval datasets);
change PROMPT_VERSION and re-run experiments/profiling when editing."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .models import ColumnStats, LinkCandidate, Link, TableModel, TableStats

PROMPT_VERSION = "exp01-v1"
MAX_STR = 60
PROMPT_TOP_VALUES_MAX_DISTINCT = 12

TABLE_UNDERSTANDING_SYSTEM = """You are a meticulous data profiler. You receive statistics and sample rows for ONE table from a set of CSV files a user uploaded together, plus the names and columns of the other tables in the set.

Return:
- entity: the business entity one row represents (singular noun).
- description: one sentence describing the table.
- grain: "one row per ..." statement.
- primary_key: the minimal column set that uniquely identifies a row. Prefer a single surrogate id column when one is unique. If no single column is unique (see the `unique` flag), choose the smallest combination of columns that should be unique (e.g. a bridge table keyed by two foreign keys, or an order id plus a line number).
- columns: one entry per column, same names, same order, with:
  - role:
    - identifier: a key or code that identifies this row or references a row of another table (ids, *_key, foreign keys, self references such as manager id). Never aggregated.
    - measure: a numeric quantity it makes sense to sum or average (money, prices, quantities, durations, sizes, balances, rates).
    - time: a date or timestamp.
    - dimension: a low-cardinality attribute used for grouping or filtering (status, type, country, category, flags encoded as codes, small ordinal codes such as priority).
    - text: free text or high-cardinality descriptive values (names, titles, addresses, phone numbers, emails, comments).
    - boolean: true/false values.
  - semantic_type: a short specific type such as "email", "country", "currency amount", "duration", "foreign key".
  - description: what the column means.
  - unit: unit of a measure when it can be inferred (e.g. "USD", "cents", "milliseconds", "bytes", "fraction 0-1", "count"); null otherwise. Check whether money is stored in minor units: integer-only money values that are about 100x larger than plausible prices for the domain suggest cents.
  - references_entity: for identifiers that point to another table, the entity they reference (e.g. "customer"); null otherwise.
Base every judgement on the statistics and samples; do not invent columns."""

RELATIONSHIP_REVIEW_SYSTEM = """You decide which foreign-key relationships exist between tables a user uploaded together.

You receive each table's entity, grain, primary key and column roles/descriptions, and a list of candidate links found by SQL. Each candidate links a child column to a parent primary key with evidence:
- containment: share of distinct child values found in the parent key (1.0 = every value exists).
- parent_coverage: share of parent keys that the child references.
- name_similarity: 0-1 similarity of the child column name to the parent key / table name.
- child_column_unique: whether the child column is unique (one-to-one).

Containment alone is weak evidence: small integer ranges (1..N) are contained in many unrelated keys. Accept a candidate only if the child column semantically refers to the parent entity (by name, description, or the entity it references, e.g. user_id -> customers, ReportsTo -> employee). Each child column normally references at most one parent: when one column has several candidates, accept only the semantically right one. Reject links between measures, line numbers, counts, or codes that only coincidentally overlap. Use "uncertain" when evidence conflicts.

For every candidate return a decision with a short reason. In missing_links, list any relationship you believe exists but is absent from the candidates, as "table.column" -> "table.column" (parent must be a key)."""

SEMANTIC_MODEL_SYSTEM = """You write a compact semantic model that a text-to-SQL agent (DuckDB dialect) will use to answer questions about this dataset.

Return:
- dataset_summary: 2-3 sentences on what the data covers and how tables connect.
- metrics: 4-10 key business metrics. expression must be a DuckDB aggregate expression over columns of exactly one table (e.g. "SUM(amount) / 100.0", "COUNT(DISTINCT customer_id)"), using quoted identifiers if names have capitals. Apply unit conversions (e.g. cents to dollars) in the expression.
- synonyms: business terms users may say and the table.column or metric they map to.
- caveats: pitfalls an analyst must know (units, join fan-out / double counting, nullable keys, self references, derived totals, date ranges).
- example_questions: 5 questions this dataset can answer."""


def short(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    return text if len(text) <= MAX_STR else text[:MAX_STR] + "…"


def _column_payload(col: ColumnStats, rows: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": col.name, "type": col.data_type, "null_pct": col.null_pct,
        "distinct": col.distinct, "unique": col.unique,
    }
    if col.family in ("number", "time"):
        payload["min"], payload["max"] = short(col.min), short(col.max)
    if col.mean is not None:
        payload["mean"] = col.mean
        payload["has_fractional_values"] = col.has_fractional_values
    if col.avg_length is not None:
        payload["avg_length"] = col.avg_length
        if col.castable_to:
            payload["castable_to"] = col.castable_to
    if col.top_values and col.distinct <= PROMPT_TOP_VALUES_MAX_DISTINCT and col.distinct < rows:
        payload["top_values"] = [[short(v), n] for v, n in col.top_values]
    return payload


def table_understanding_payload(stats: TableStats, other_tables: dict[str, list[str]]) -> str:
    return json.dumps({
        "table": stats.table,
        "row_count": stats.row_count,
        "columns": [_column_payload(c, stats.row_count) for c in stats.columns],
        "sample_rows": {
            "columns": stats.column_names,
            "rows": [[short(v) for v in row] for row in stats.sample_rows],
        },
        "other_tables": other_tables,
    }, default=str)


def candidate_payload(candidate: LinkCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id, "from": candidate.ref_from, "to": candidate.ref_to,
        "name_similarity": candidate.name_similarity, "child_column_unique": candidate.child_column_unique,
        **asdict(candidate.evidence),
    }


def relationship_review_payload(tables: dict[str, TableModel], candidates: list[LinkCandidate]) -> str:
    return json.dumps({
        "tables": {
            name: {
                "entity": t.understanding.entity,
                "grain": t.understanding.grain,
                "primary_key": t.primary_key or None,
                "columns": {c: f"{t.role(c)}: {t.column_info(c).description}" for c in t.stats.column_names},
            }
            for name, t in tables.items()
        },
        "candidates": [candidate_payload(c) for c in candidates],
    }, default=str)


def semantic_model_payload(tables: dict[str, TableModel], links: list[Link]) -> str:
    out: dict[str, Any] = {}
    for name, t in tables.items():
        cols = {}
        for c in t.stats.columns:
            info = t.column_info(c.name)
            cols[c.name] = {k: v for k, v in {
                "type": c.data_type, "role": info.role, "unit": info.unit, "description": info.description,
                "min": short(c.min), "max": short(c.max), "null_pct": c.null_pct or None,
            }.items() if v is not None}
        out[name] = {
            "entity": t.understanding.entity, "grain": t.understanding.grain, "rows": t.stats.row_count,
            "primary_key": t.primary_key or None, "columns": cols,
        }
    rels = [
        {"from": l.ref_from, "to": l.ref_to, "cardinality": l.cardinality, "fan_out": l.fan_out, "status": l.status}
        for l in links
    ]
    return json.dumps({"tables": out, "relationships": rels}, default=str)
