"""P6: business semantic model (metrics, synonyms, caveats) for the chat agent, with SQL validation."""

from __future__ import annotations

from typing import Protocol

import duckdb

from ..llm import StructuredLLM
from ..schemas import Metric, SemanticModel, Synonym
from .contracts import SemanticModelDraft
from .models import Link, TableModel
from .prompts import SEMANTIC_MODEL_SYSTEM, semantic_model_payload
from .sql import first_error_line, quote_ident as q


class SemanticModeler(Protocol):
    def build(self, tables: dict[str, TableModel], links: list[Link]) -> SemanticModelDraft | None: ...


class NullSemanticModeler:
    """Without a model there is no business vocabulary to infer; the profile simply omits it."""

    def build(self, tables: dict[str, TableModel], links: list[Link]) -> SemanticModelDraft | None:
        return None


class LLMSemanticModeler:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def build(self, tables: dict[str, TableModel], links: list[Link]) -> SemanticModelDraft | None:
        return self._llm.parse(
            "semantic_model", SEMANTIC_MODEL_SYSTEM, semantic_model_payload(tables, links), SemanticModelDraft
        )


class MetricValidator:
    """Executes each metric expression so the chat agent only trusts SQL that actually runs."""

    def __init__(self, con: duckdb.DuckDBPyConnection, tables: set[str]) -> None:
        self._con = con
        self._tables = {t.lower(): t for t in tables}

    def validate(self, draft: SemanticModelDraft) -> SemanticModel:
        metrics = [self.check(m.name, m.table, m.expression, m.description) for m in draft.metrics]
        return SemanticModel(
            dataset_summary=draft.dataset_summary,
            metrics=metrics,
            synonyms=[Synonym(term=s.term, maps_to=s.maps_to) for s in draft.synonyms],
            caveats=draft.caveats,
            example_questions=draft.example_questions,
        )

    def revalidate(self, metrics: list[Metric]) -> list[Metric]:
        """Re-checks metrics after the data changed (e.g. cleaning converted a text column to numeric)."""
        return [self.check(m.name, m.table, m.expression, m.description) for m in metrics]

    def check(self, name: str, table_name: str, expression: str, description: str) -> Metric:
        table = self._tables.get(table_name.strip('"').lower())
        error = None
        if table is None:
            error = f"Unknown table {table_name!r}."
        else:
            try:
                self._con.execute(f"SELECT {expression} FROM {q(table)}").fetchone()
            except duckdb.Error as exc:
                error = first_error_line(exc, 200)
        return Metric(name=name, table=table or table_name, expression=expression,
                      description=description, valid=error is None, error=error)
