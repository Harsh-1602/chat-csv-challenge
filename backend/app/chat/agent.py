"""Chat orchestrator: fixed outer pipeline (understand -> SQL loop -> present -> narrate) around a bounded agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..llm import LLMError
from .context import SchemaContext
from .contracts import Presentation, Understanding
from .executor import QueryResult
from .grounding import ungrounded_numbers
from .memory import Turn
from .narration import Narrator, TemplateNarrator
from .notes import DataNotes
from .presentation import resolve_presentation
from .sql_agent import AgentStep, SQLAgent
from .understanding import PassThroughUnderstander, Understander

AnswerStatus = Literal["answered", "clarify", "refuse", "chitchat", "failed"]
HISTORY_PREVIEW_ROWS = 5


class ChatEvents(Protocol):
    def stage(self, name: str, detail: str = "") -> None: ...
    def step(self, step: AgentStep) -> None: ...


class NullChatEvents:
    def stage(self, name: str, detail: str = "") -> None:
        pass

    def step(self, step: AgentStep) -> None:
        pass


@dataclass
class ChatAnswer:
    status: AnswerStatus
    question: str
    message: str
    standalone_question: str | None = None
    sql: str | None = None
    result: QueryResult | None = None
    presentation: Presentation | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # degraded-mode notes for the UI/logs
    data_quality: list[str] = field(default_factory=list)  # unresolved data issues touching the answer's SQL

    def to_turn(self) -> Turn:
        return Turn(
            question=self.question,
            standalone_question=self.standalone_question,
            sql=self.sql,
            result_preview=self.result.preview(HISTORY_PREVIEW_ROWS) if self.result else None,
            answer=self.message,
        )


def _understanding_brief(u: Understanding) -> str:
    lines = ["INTERPRETATION (from the understanding step)"]
    if u.output != "auto":
        lines.append(f"- requested output: {u.output}")
    if u.time_window:
        lines.append(f"- time window: {u.time_window}")
    lines += [f"- assumption: {a}" for a in u.assumptions]
    return "\n".join(lines) if len(lines) > 1 else ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [i for i in items if not (i.lower() in seen or seen.add(i.lower()))]


class ChatAgent:
    def __init__(
        self,
        context: SchemaContext,
        understander: Understander,
        sql_agent: SQLAgent,
        narrator: Narrator,
        data_notes: DataNotes | None = None,
    ) -> None:
        self._context = context
        self._understander = understander
        self._sql_agent = sql_agent
        self._narrator = narrator
        self._data_notes = data_notes or DataNotes()

    def ask(self, question: str, history: list[Turn], events: ChatEvents | None = None) -> ChatAnswer:
        events = events or NullChatEvents()
        notes: list[str] = []

        events.stage("understanding")
        try:
            u = self._understander.understand(self._context, history, question)
        except LLMError:
            notes.append("Understanding step unavailable; answered the question as asked.")
            u = PassThroughUnderstander().understand(self._context, history, question)

        if u.intent != "data_question":
            status: AnswerStatus = {"clarification_needed": "clarify", "unanswerable": "refuse"}.get(u.intent, "chitchat")
            return ChatAnswer(status, question, u.reply or "Could you rephrase that?", u.standalone_question,
                              assumptions=u.assumptions, notes=notes)

        events.stage("querying", u.standalone_question)
        outcome = self._sql_agent.solve(
            self._context, history, u.standalone_question, _understanding_brief(u), observe=events.step
        )
        assumptions = _dedupe(u.assumptions + outcome.assumptions)
        if outcome.status != "answered" or outcome.result is None:
            fallback = "I couldn't build a query that answers this. Could you rephrase or narrow it down?"
            return ChatAnswer(outcome.status, question, outcome.message or fallback, u.standalone_question,
                              sql=outcome.sql, assumptions=assumptions, steps=outcome.steps, notes=notes)

        events.stage("presenting")
        presentation = resolve_presentation(outcome.presentation, outcome.result, u.output)
        warnings = list(outcome.warnings)

        data_quality = self._data_notes.relevant(outcome.sql)
        events.stage("narrating")
        try:
            message = self._narrator.narrate(u.standalone_question, outcome.result, assumptions, data_quality)
            missing = ungrounded_numbers(message, outcome.result, question, assumptions + data_quality)
            if missing:
                notes.append(f"Narration figures not found in the result: {', '.join(missing)}; used a plain summary.")
                message = TemplateNarrator().narrate(u.standalone_question, outcome.result, [], [])
        except LLMError:
            notes.append("Narration step unavailable; used a plain summary.")
            message = TemplateNarrator().narrate(u.standalone_question, outcome.result, [], [])

        return ChatAnswer(
            "answered", question, message, u.standalone_question, sql=outcome.sql, result=outcome.result,
            presentation=presentation, assumptions=assumptions, warnings=warnings, steps=outcome.steps, notes=notes,
            data_quality=data_quality,
        )
