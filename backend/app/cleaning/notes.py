"""Turns unresolved data issues into chat data-quality notes."""

from __future__ import annotations

from ..chat.notes import DataNote, DataNotes
from ..schemas import DataIssue


def data_notes(issues: list[DataIssue]) -> DataNotes:
    return DataNotes([DataNote(i.table, i.column, f"{i.table}: {i.description}") for i in issues])
