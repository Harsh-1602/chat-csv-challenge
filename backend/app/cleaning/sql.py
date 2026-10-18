"""SQL fragments shared by issue detection and cleaning so both see exactly the same transformation."""

from __future__ import annotations

from ..profiling.sql import quote_ident

NULL_TOKENS = ("", "null", "n/a", "na", "none", "nan", "-", "--", "?", "nil", "missing", "undefined")
DATE_FORMATS = ("%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d, %Y", "%d-%b-%Y")
CASTABLE_SHARE = 0.98
CASE_VARIANT_MAX_DISTINCT = 100


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def tokens_list() -> str:
    return ", ".join(literal(t) for t in NULL_TOKENS)


def nullify_tokens(expr: str) -> str:
    return f"CASE WHEN lower(trim({expr})) IN ({tokens_list()}) THEN NULL ELSE {expr} END"


def numeric_expr(expr: str, target: str = "DOUBLE") -> str:
    return f"TRY_CAST(regexp_replace(trim({expr}), '[$,\\s]', '', 'g') AS {target})"


def date_expr(expr: str) -> str:
    formats = ", ".join(literal(f) for f in DATE_FORMATS)
    return f"coalesce(TRY_CAST(trim({expr}) AS DATE), TRY_CAST(try_strptime(trim({expr}), [{formats}]) AS DATE))"


def case_map_expr(expr: str, mapping: dict[str, str]) -> str:
    """Rewrites values whose lower(trim()) key has a canonical spelling; other values pass through."""
    whens = " ".join(f"WHEN {literal(k)} THEN {literal(v)}" for k, v in mapping.items())
    return f"CASE lower(trim({expr})) {whens} ELSE {expr} END"


def base_text(column: str) -> str:
    """Column with null tokens removed: the population used to judge numeric/date castability."""
    return nullify_tokens(quote_ident(column))
