"""Conversation memory: what later turns need from earlier ones (question, resolved intent, SQL, result shape)."""

from __future__ import annotations

from dataclasses import dataclass

MAX_HISTORY_TURNS = 6


@dataclass(frozen=True)
class Turn:
    question: str
    standalone_question: str | None = None
    sql: str | None = None
    result_preview: str | None = None
    answer: str | None = None


def render_history(history: list[Turn], max_turns: int = MAX_HISTORY_TURNS) -> str:
    if not history:
        return "(no earlier turns)"
    blocks = []
    for i, turn in enumerate(history[-max_turns:], 1):
        lines = [f"[turn {i}] user: {turn.question}"]
        if turn.standalone_question and turn.standalone_question != turn.question:
            lines.append(f"  interpreted as: {turn.standalone_question}")
        if turn.sql:
            lines.append(f"  sql: {' '.join(turn.sql.split())}")
        if turn.result_preview:
            lines.append("  result:\n" + "\n".join(f"    {line}" for line in turn.result_preview.splitlines()))
        if turn.answer:
            lines.append(f"  assistant: {turn.answer}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)
