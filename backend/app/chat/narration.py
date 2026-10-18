"""C7 narration: turn a verified result into a short answer. LLM version plus a deterministic fallback."""

from __future__ import annotations

from typing import Protocol

from ..llm import StructuredLLM
from .contracts import Narration
from .executor import QueryResult
from .prompts import NARRATE_SYSTEM, NARRATE_USER

NARRATION_PREVIEW_ROWS = 50


class Narrator(Protocol):
    def narrate(self, question: str, result: QueryResult, assumptions: list[str], notes: list[str]) -> str: ...


class TemplateNarrator:
    def narrate(self, question: str, result: QueryResult, assumptions: list[str], notes: list[str]) -> str:
        if result.row_count == 1 and len(result.columns) <= 2:
            value = result.rows[0][-1]
            text = f"{result.columns[-1].replace('_', ' ')}: {value}."
        else:
            more = "+" if result.truncated else ""
            text = f"Here are the results ({result.row_count}{more} rows)."
        if assumptions:
            text += " Assumptions: " + "; ".join(assumptions)
        return text


class LLMNarrator:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def narrate(self, question: str, result: QueryResult, assumptions: list[str], notes: list[str]) -> str:
        user = NARRATE_USER.format(
            question=question,
            assumptions="\n".join(f"- {a}" for a in assumptions) or "(none)",
            notes="\n".join(f"- {n}" for n in notes) or "(none)",
            row_count=result.row_count,
            truncated=", truncated" if result.truncated else "",
            preview=result.preview(NARRATION_PREVIEW_ROWS),
        )
        return self._llm.parse("Narration", NARRATE_SYSTEM, user, Narration).answer.strip()
