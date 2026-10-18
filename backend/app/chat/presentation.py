"""C6 presentation: validate the model's requested view against the actual result shape (Show Me-style rules)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .contracts import OutputKind, Presentation
from .executor import QueryResult

MAX_CHART_ROWS = 500
MAX_CATEGORICAL_POINTS = 40
MAX_PIE_SLICES = 6


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_temporal(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 7:
        return False
    try:
        dt.date.fromisoformat(value[:10] if len(value) >= 10 else value + "-01")
        return True
    except ValueError:
        return False


def column_kinds(result: QueryResult) -> dict[str, str]:
    """'number', 'time' or 'category' per column, from the non-null values."""
    kinds: dict[str, str] = {}
    for i, name in enumerate(result.columns):
        values = [row[i] for row in result.rows if row[i] is not None]
        if values and all(_is_number(v) for v in values):
            kinds[name] = "number"
        elif values and all(_is_temporal(v) for v in values):
            kinds[name] = "time"
        else:
            kinds[name] = "category"
    return kinds


def _text() -> Presentation:
    return Presentation(kind="text", chart_type=None, x=None, y=[], series=None, title=None)


def _table(title: str | None = None) -> Presentation:
    return Presentation(kind="table", chart_type=None, x=None, y=[], series=None, title=title)


def _auto_chart(result: QueryResult, kinds: dict[str, str], title: str | None) -> Presentation | None:
    numbers = [c for c in result.columns if kinds[c] == "number"]
    others = [c for c in result.columns if kinds[c] != "number"]
    if not numbers or len(others) != 1 or len(numbers) > 3:
        return None
    x = others[0]
    if kinds[x] == "time" and result.row_count >= 3:
        return Presentation(kind="chart", chart_type="line", x=x, y=numbers, series=None, title=title)
    if kinds[x] == "category" and 2 <= result.row_count <= 20:
        return Presentation(kind="chart", chart_type="bar", x=x, y=numbers, series=None, title=title)
    return None


def _valid_chart(p: Presentation, result: QueryResult, kinds: dict[str, str]) -> bool:
    if p.chart_type is None or not p.x or not p.y:
        return False
    columns = set(result.columns)
    if p.x not in columns or any(y not in columns or kinds[y] != "number" for y in p.y):
        return False
    if p.series and p.series not in columns:
        return False
    if not 2 <= result.row_count <= MAX_CHART_ROWS:
        return False
    if p.chart_type in ("bar", "horizontal_bar", "stacked_bar") and kinds[p.x] == "category":
        return result.row_count <= MAX_CATEGORICAL_POINTS * (3 if p.series else 1)
    if p.chart_type == "pie":
        return result.row_count <= MAX_PIE_SLICES and len(p.y) == 1 and all(
            (row[result.columns.index(p.y[0])] or 0) >= 0 for row in result.rows
        )
    if p.chart_type == "scatter":
        return kinds[p.x] == "number"
    return True


def resolve_presentation(requested: Presentation | None, result: QueryResult, preference: OutputKind = "auto") -> Presentation:
    """Honour explicit user preference, then the model's choice if it fits the data, else a safe default."""
    kinds = column_kinds(result)
    title = requested.title if requested else None
    single_value = result.row_count <= 1 and len(result.columns) <= 2

    if preference == "table":
        return _table(title)
    if preference == "text" and single_value:
        return _text()

    wants_chart = preference == "chart" or (requested is not None and requested.kind == "chart")
    if wants_chart and requested is not None and requested.kind == "chart" and _valid_chart(requested, result, kinds):
        return requested
    # In auto mode a dimension + measure breakdown reads best as a chart (the table stays available in the UI).
    if wants_chart or preference == "auto":
        auto = _auto_chart(result, kinds, title)
        if auto:
            return auto

    if single_value:
        return _text()
    return _table(title)
