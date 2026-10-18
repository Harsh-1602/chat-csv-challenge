"""C2 understand step: standalone rewrite, intent routing, output preference and assumptions."""

from __future__ import annotations

from typing import Protocol

from ..llm import StructuredLLM
from .context import SchemaContext
from .contracts import Understanding
from .memory import Turn
from .prompts import understand_prompts


class Understander(Protocol):
    def understand(self, context: SchemaContext, history: list[Turn], question: str) -> Understanding: ...


class PassThroughUnderstander:
    """No LLM call: the SQL step sees the raw question plus history (used for ablations and fallback)."""

    def understand(self, context: SchemaContext, history: list[Turn], question: str) -> Understanding:
        return Understanding(
            standalone_question=question, intent="data_question", output="auto",
            time_window=None, assumptions=[], reply=None,
        )


class LLMUnderstander:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def understand(self, context: SchemaContext, history: list[Turn], question: str) -> Understanding:
        system, user = understand_prompts(context, history, question)
        return self._llm.parse("Understanding", system, user, Understanding)
