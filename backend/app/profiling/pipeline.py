"""Orchestrates profiling: SQL gathers evidence, a strategy (LLM or heuristic) interprets it, SQL verifies.

P1 load + statistics -> P2 table understanding (parallel) + key verification -> P3 candidates
-> P4 relationship review + verification -> P5 join structure -> P6 semantic model + metric validation.

Every AI step has a heuristic fallback, so a model outage degrades quality instead of failing the upload.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

import duckdb

from ..schemas import AnalysisInfo, DatasetProfile, FailedFile, SemanticModel
from . import assembler
from .interpreters import HeuristicTableInterpreter, TableInterpreter
from .judges import HeuristicRelationshipJudge, RelationshipJudge
from .keys import PrimaryKeyResolver
from .models import Link, TableModel, TableStats
from .progress import ProgressSink, ProgressTracker
from .relationships import CandidateFinder, LinkVerifier, StructureAnnotator
from .semantics import MetricValidator, NullSemanticModeler, SemanticModeler
from .sql import first_error_line, load_csv
from .statistics import StatisticsCollector

logger = logging.getLogger(__name__)
R = TypeVar("R")


@dataclass(frozen=True)
class SourceFile:
    file_name: str
    table: str
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class Strategies:
    """The judgement steps of the pipeline; swap implementations without touching orchestration."""

    interpreter: TableInterpreter
    judge: RelationshipJudge
    semantic: SemanticModeler


HEURISTIC_STRATEGIES = Strategies(HeuristicTableInterpreter(), HeuristicRelationshipJudge(), NullSemanticModeler())


class UsageReport(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def reasoning_effort(self) -> str | None: ...

    @property
    def calls(self) -> list: ...

    @property
    def prompt_tokens(self) -> int: ...

    @property
    def completion_tokens(self) -> int: ...


@dataclass
class _RunState:
    degraded: bool = False
    notes: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


class ProfilingPipeline:
    def __init__(
        self,
        primary: Strategies,
        fallback: Strategies = HEURISTIC_STRATEGIES,
        usage: UsageReport | None = None,
        max_parallel_calls: int = 8,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._usage = usage
        self._max_parallel = max(1, max_parallel_calls)

    @property
    def uses_llm(self) -> bool:
        return self._primary is not self._fallback

    def run(self, database_path: Path, files: list[SourceFile], sink: ProgressSink) -> DatasetProfile:
        state = _RunState()
        tracker = ProgressTracker(sink, len(files))
        con = duckdb.connect(str(database_path))
        try:
            stats, failed = self._load_and_measure(con, files, tracker, state)
            tables = self._understand(con, stats, tracker, state)
            links = self._relationships(con, tables, tracker, state)
            semantic = self._semantic_model(con, tables, links, tracker, state)
        finally:
            con.close()
        return assembler.dataset_profile(list(tables.values()), links, semantic, failed, self._analysis(state))

    # ------------------------------------------------------------------ P1

    def _load_and_measure(
        self, con: duckdb.DuckDBPyConnection, files: list[SourceFile], tracker: ProgressTracker, state: _RunState
    ) -> tuple[list[TableStats], list[FailedFile]]:
        stats: list[TableStats] = []
        failed: list[FailedFile] = []
        with _timed(state, "load_and_statistics"):
            for f in files:
                tracker.file_status(f.table, "loading")
                tracker.file_step(f"Loading {f.file_name}")
                try:
                    load_csv(con, f.table, f.path)
                    tracker.file_status(f.table, "profiling")
                    stats.append(StatisticsCollector(con).collect(f.table, f.file_name, f.size_bytes))
                    tracker.file_step(f"Profiling {f.file_name}")
                    tracker.file_status(f.table, "completed")
                except duckdb.Error as exc:
                    logger.info("Could not profile %s: %s", f.file_name, exc)
                    error = first_error_line(exc)
                    tracker.file_status(f.table, "failed", error)
                    failed.append(FailedFile(file_name=f.file_name, error=error))
            tracker.files_finished(len(files))
        return stats, failed

    # ------------------------------------------------------------------ P2

    def _understand(
        self, con: duckdb.DuckDBPyConnection, stats: list[TableStats], tracker: ProgressTracker, state: _RunState
    ) -> dict[str, TableModel]:
        columns = {s.table: s.column_names for s in stats}
        done = 0
        tracker.stage("understanding", 0, len(stats))

        def interpret(s: TableStats):
            others = {t: c for t, c in columns.items() if t != s.table}
            return self._attempt(
                state, f"table understanding for {s.table}",
                lambda: self._primary.interpreter.interpret(s, others),
                lambda: self._fallback.interpreter.interpret(s, others),
            )

        resolver = PrimaryKeyResolver(con)
        tables: dict[str, TableModel] = {}
        with _timed(state, "understanding"), ThreadPoolExecutor(self._max_parallel) as pool:
            for s, understanding in zip(stats, pool.map(interpret, stats)):
                roles = {c.name: c.role for c in understanding.columns}
                key, source = resolver.resolve(s, understanding.primary_key, roles)
                tables[s.table] = TableModel(s, understanding, key, source)
                done += 1
                tracker.stage("understanding", done, len(stats))
        return tables

    # ------------------------------------------------------------------ P3-P5

    def _relationships(
        self, con: duckdb.DuckDBPyConnection, tables: dict[str, TableModel], tracker: ProgressTracker, state: _RunState
    ) -> list[Link]:
        tracker.stage("relationships")
        with _timed(state, "candidates"):
            candidates = CandidateFinder(con).find(tables)
        tracker.stage("review")
        with _timed(state, "relationship_review"):
            review = self._attempt(
                state, "relationship review",
                lambda: self._primary.judge.review(tables, candidates),
                lambda: self._fallback.judge.review(tables, candidates),
            )
            links = LinkVerifier(con).verify(review, candidates, tables)
            StructureAnnotator(con).annotate(links)
        return links

    # ------------------------------------------------------------------ P6

    def _semantic_model(
        self,
        con: duckdb.DuckDBPyConnection,
        tables: dict[str, TableModel],
        links: list[Link],
        tracker: ProgressTracker,
        state: _RunState,
    ) -> SemanticModel | None:
        if not tables:
            return None
        tracker.stage("semantic")
        with _timed(state, "semantic_model"):
            draft = self._attempt(
                state, "semantic model",
                lambda: self._primary.semantic.build(tables, links),
                lambda: self._fallback.semantic.build(tables, links),
            )
            return MetricValidator(con, set(tables)).validate(draft) if draft else None

    # ------------------------------------------------------------------ helpers

    def _attempt(self, state: _RunState, step: str, primary: Callable[[], R], fallback: Callable[[], R]) -> R:
        if not self.uses_llm:
            return fallback()
        try:
            return primary()
        except Exception as exc:  # any AI failure must degrade, never fail the upload
            logger.warning("AI step '%s' failed, using heuristics: %s", step, exc)
            state.degraded = True
            state.notes.append(f"AI {step} failed ({type(exc).__name__}); used rule-based fallback.")
            return fallback()

    def _analysis(self, state: _RunState) -> AnalysisInfo:
        usage = self._usage if self.uses_llm else None
        return AnalysisInfo(
            llm_enabled=usage is not None,
            model=usage.model if usage else None,
            reasoning_effort=usage.reasoning_effort if usage else None,
            degraded=state.degraded,
            notes=state.notes,
            llm_calls=len(usage.calls) if usage else 0,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            timings_s=state.timings,
        )


@contextmanager
def _timed(state: _RunState, key: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        state.timings[key] = round(time.perf_counter() - started, 2)
