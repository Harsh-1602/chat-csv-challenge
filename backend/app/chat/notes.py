"""Data-quality notes scoped to the tables/columns an answer's SQL actually touches."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DataNote:
    table: str
    column: str | None
    text: str


class DataNotes:
    def __init__(self, notes: list[DataNote] | None = None) -> None:
        self._notes = list(notes or [])

    def __len__(self) -> int:
        return len(self._notes)

    def relevant(self, sql: str | None) -> list[str]:
        if not sql or not self._notes:
            return []
        words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)}
        quoted = {w.lower() for w in re.findall(r'"((?:[^"]|"")+)"', sql)}
        names = words | quoted
        return [
            n.text for n in self._notes
            if n.table.lower() in names and (n.column is None or n.column.lower() in names)
        ]
