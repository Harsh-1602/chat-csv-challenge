"""Structured-output contracts for the chat LLM steps (strict JSON schema: every field required, nullable)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["data_question", "clarification_needed", "unanswerable", "chitchat"]
OutputKind = Literal["auto", "text", "table", "chart"]
ChartType = Literal["bar", "horizontal_bar", "stacked_bar", "line", "area", "pie", "scatter"]
ActionKind = Literal["run_sql", "final_sql", "clarify", "refuse"]


class Understanding(BaseModel):
    """C2: resolve the turn into a standalone, unambiguous request (or decide not to query)."""

    standalone_question: str = Field(description="The question rewritten to be self-contained, using prior turns.")
    intent: Intent
    output: OutputKind = Field(description="What the user explicitly asked for; 'auto' if they did not say.")
    time_window: str | None = Field(description="Resolved absolute date range if the question implies one, else null.")
    assumptions: list[str] = Field(description="Interpretations chosen for ambiguous terms; shown to the user.")
    reply: str | None = Field(description="Message to the user when intent is not data_question, else null.")


class Presentation(BaseModel):
    kind: Literal["text", "table", "chart"]
    chart_type: ChartType | None
    x: str | None = Field(description="Result column for the x axis / categories.")
    y: list[str] = Field(description="Result column(s) with the numeric values.")
    series: str | None = Field(description="Optional result column that splits values into series.")
    title: str | None


class AgentAction(BaseModel):
    """C3: one step of the bounded SQL loop."""

    thought: str = Field(description="Brief reasoning for this step (not shown to the user).")
    action: ActionKind
    sql: str | None = Field(description="DuckDB SELECT for run_sql/final_sql, else null.")
    message: str | None = Field(description="Clarifying question or refusal reason, else null.")
    assumptions: list[str] = Field(description="Assumptions baked into final_sql (units, filters, time window).")
    presentation: Presentation | None = Field(description="How to show the final result; required for final_sql.")


class Narration(BaseModel):
    """C7: the user-facing answer, written only from the verified result."""

    answer: str
