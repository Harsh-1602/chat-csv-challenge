"""Progress reporting: the pipeline emits events, a tracker converts them to a weighted fraction,
and a sink (e.g. the job store) stores them. The pipeline never knows how progress is displayed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

FileStatus = Literal["loading", "profiling", "completed", "failed"]
FILE_UNITS = 2  # loading + statistics


class ProgressSink(Protocol):
    def file_status(self, table: str, status: FileStatus, error: str | None = None) -> None: ...

    def progress(self, fraction: float, stage: str) -> None: ...


@dataclass(frozen=True)
class StageSpec:
    key: str
    label: str
    weight: float


DATASET_STAGES = (
    StageSpec("understanding", "Interpreting tables and columns", 3),
    StageSpec("relationships", "Finding candidate relationships", 1),
    StageSpec("review", "Verifying relationships", 2),
    StageSpec("semantic", "Building business glossary and metrics", 2),
)


class NullSink:
    def file_status(self, table: str, status: FileStatus, error: str | None = None) -> None:
        pass

    def progress(self, fraction: float, stage: str) -> None:
        pass


class ProgressTracker:
    def __init__(self, sink: ProgressSink, file_count: int, stages: tuple[StageSpec, ...] = DATASET_STAGES) -> None:
        self._sink = sink
        self._stages = {s.key: s for s in stages}
        self._order = [s.key for s in stages]
        self._total = file_count * FILE_UNITS + sum(s.weight for s in stages)
        self._file_units = 0.0

    def file_status(self, table: str, status: FileStatus, error: str | None = None) -> None:
        self._sink.file_status(table, status, error)

    def file_step(self, label: str) -> None:
        self._file_units += 1
        self._sink.progress(self._fraction(self._file_units), label)

    def files_finished(self, file_count: int) -> None:
        # Failed files skip their statistics unit; snap to the end of the file phase.
        self._file_units = file_count * FILE_UNITS

    def stage(self, key: str, done: int = 0, total: int = 1) -> None:
        spec = self._stages[key]
        finished = sum(self._stages[k].weight for k in self._order[: self._order.index(key)])
        share = spec.weight * (done / total if total else 1.0)
        label = f"{spec.label} ({done}/{total})" if total > 1 else spec.label
        self._sink.progress(self._fraction(self._file_units + finished + share), label)

    def _fraction(self, units: float) -> float:
        return round(min(units / self._total, 0.99), 4) if self._total else 0.0
