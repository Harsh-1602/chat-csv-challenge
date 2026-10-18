"""Structured outputs requested from the LLM. Heuristic fallbacks produce the same shapes,
so downstream steps never know which implementation ran (Liskov substitution)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Role = Literal["identifier", "measure", "time", "dimension", "text", "boolean"]


class ColumnUnderstanding(BaseModel):
    name: str
    role: Role
    semantic_type: str
    description: str
    unit: str | None
    references_entity: str | None


class TableUnderstanding(BaseModel):
    entity: str
    description: str
    grain: str
    primary_key: list[str]
    columns: list[ColumnUnderstanding]


class LinkDecision(BaseModel):
    candidate_id: int
    decision: Literal["accept", "reject", "uncertain"]
    reason: str


class ProposedLink(BaseModel):
    from_column: str
    to_column: str
    reason: str


class RelationshipReview(BaseModel):
    decisions: list[LinkDecision]
    missing_links: list[ProposedLink]


class MetricDraft(BaseModel):
    name: str
    table: str
    expression: str
    description: str


class SynonymDraft(BaseModel):
    term: str
    maps_to: str


class SemanticModelDraft(BaseModel):
    dataset_summary: str
    metrics: list[MetricDraft]
    synonyms: list[SynonymDraft]
    caveats: list[str]
    example_questions: list[str]
