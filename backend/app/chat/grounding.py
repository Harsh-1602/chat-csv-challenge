"""C7 number grounding: every figure in the narrated answer must be traceable to the verified result."""

from __future__ import annotations

import re

from .executor import QueryResult

_NUMBER = re.compile(r"(?<![\w.])[-+]?\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|k\b|K\b|M\b|million\b|billion\b|B\b)?")
_SCALE = {"k": 1e3, "K": 1e3, "M": 1e6, "million": 1e6, "B": 1e9, "billion": 1e9}


def extract_numbers(text: str) -> list[tuple[str, float, int]]:
    """(raw text, value, decimals shown) for each number in the text."""
    found = []
    for match in _NUMBER.finditer(text):
        whole, frac, suffix = match.group(1), match.group(2) or "", match.group(3)
        value = float(whole.replace(",", "") + frac)
        if suffix in _SCALE:
            value *= _SCALE[suffix]
        found.append((match.group(0).strip(), value, max(len(frac) - 1, 0)))
    return found


def _candidates(result: QueryResult) -> list[float]:
    values = [float(v) for row in result.rows for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)]
    values += [float(result.row_count), float(len(result.columns))]
    # Dates contribute their year/month/day so "in 2018" or "March 3" are grounded.
    for row in result.rows:
        for v in row:
            if isinstance(v, str):
                values += [float(p) for p in re.findall(r"\d+", v)]
    return values


def _matches(value: float, decimals: int, raw: str, candidate: float) -> bool:
    if round(candidate, decimals) == round(value, decimals):
        return True
    if raw.endswith("%") and round(candidate * 100, decimals) == round(value, decimals):
        return True
    # Abbreviated figures ("1.2M", "about 3,000") are accepted within 5%.
    tolerance = 0.05 if any(s in raw for s in _SCALE) else 0.005
    return candidate != 0 and abs(candidate - value) / abs(candidate) <= tolerance


def ungrounded_numbers(answer: str, result: QueryResult, question: str = "", context: list[str] | None = None) -> list[str]:
    """Figures from the question and from shown context (assumptions, data notes) count as grounded."""
    allowed = {v for text in [question, *(context or [])] for _, v, _ in extract_numbers(text)}
    candidates = _candidates(result)
    missing = []
    for raw, value, decimals in extract_numbers(answer):
        if value in allowed or (value.is_integer() and 0 <= value <= 10):
            continue
        if not any(_matches(value, decimals, raw, c) for c in candidates):
            missing.append(raw)
    return missing
