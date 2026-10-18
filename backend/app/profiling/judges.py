"""P4 judgement: which SQL candidates are real foreign keys."""

from __future__ import annotations

from typing import Protocol

from ..llm import StructuredLLM
from .contracts import LinkDecision, RelationshipReview
from .models import LinkCandidate, TableModel
from .prompts import RELATIONSHIP_REVIEW_SYSTEM, relationship_review_payload
from .relationships import ACCEPT_CONTAINMENT, NAME_SIM_STRONG


class RelationshipJudge(Protocol):
    def review(self, tables: dict[str, TableModel], candidates: list[LinkCandidate]) -> RelationshipReview: ...


class HeuristicRelationshipJudge:
    """Accepts only links where the column name and the data both agree."""

    def review(self, tables: dict[str, TableModel], candidates: list[LinkCandidate]) -> RelationshipReview:
        best: dict[str, float] = {}
        for c in candidates:
            best[c.ref_from] = max(best.get(c.ref_from, 0.0), c.name_similarity)
        decisions = []
        for c in candidates:
            named = c.name_similarity >= NAME_SIM_STRONG
            if named and c.name_similarity < best[c.ref_from]:
                decisions.append(LinkDecision(candidate_id=c.id, decision="reject",
                                              reason="Another parent key matches this column's name more closely."))
            elif named and c.evidence.containment >= ACCEPT_CONTAINMENT:
                decisions.append(LinkDecision(candidate_id=c.id, decision="accept",
                                              reason="Column name matches the parent key and all values exist in it."))
            elif named:
                decisions.append(LinkDecision(candidate_id=c.id, decision="uncertain",
                                              reason="Column name matches the parent key but some values are missing from it."))
            else:
                decisions.append(LinkDecision(candidate_id=c.id, decision="reject",
                                              reason="Values overlap but the column name does not refer to the parent."))
        return RelationshipReview(decisions=decisions, missing_links=[])


class LLMRelationshipJudge:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def review(self, tables: dict[str, TableModel], candidates: list[LinkCandidate]) -> RelationshipReview:
        return self._llm.parse(
            "relationship_review",
            RELATIONSHIP_REVIEW_SYSTEM,
            relationship_review_payload(tables, candidates),
            RelationshipReview,
        )
