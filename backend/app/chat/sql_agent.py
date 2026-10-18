"""C3-C5 bounded SQL loop: the model explores, proposes a final query, and repairs it from guard/DB feedback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from ..llm import LLMError, StructuredLLM
from .context import SchemaContext
from .contracts import AgentAction, Presentation
from .executor import QueryError, QueryExecutor, QueryResult
from .guard import SQLGuard
from .memory import Turn
from .prompts import sql_system_prompt, sql_user_prompt

OutcomeStatus = Literal["answered", "clarify", "refuse", "failed"]


@dataclass(frozen=True)
class AgentStep:
    index: int
    action: str
    sql: str | None
    outcome: Literal["ok", "error", "rejected", "needs_check", "final", "stopped"]
    detail: str


@dataclass
class SQLOutcome:
    status: OutcomeStatus
    sql: str | None = None
    result: QueryResult | None = None
    presentation: Presentation | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str | None = None
    steps: list[AgentStep] = field(default_factory=list)


StepObserver = Callable[[AgentStep], None]


def _norm(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";").lower()


class SQLAgent:
    def __init__(
        self,
        llm: StructuredLLM,
        guard: SQLGuard,
        executor: QueryExecutor,
        max_steps: int = 4,
        repair: bool = True,
        preview_rows: int = 20,
    ) -> None:
        self._llm = llm
        self._guard = guard
        self._executor = executor
        self._max_steps = max(1, max_steps)
        self._repair = repair
        self._preview_rows = preview_rows

    def solve(
        self,
        context: SchemaContext,
        history: list[Turn],
        question: str,
        understanding: str = "",
        observe: StepObserver | None = None,
    ) -> SQLOutcome:
        run = _Run(observe)
        system = sql_system_prompt(context, self._preview_rows)
        acknowledged: set[str] = set()
        pending: SQLOutcome | None = None  # a final query that ran but was sent back for a sanity check

        for index in range(1, self._max_steps + 1):
            remaining = self._max_steps - index + 1
            user = sql_user_prompt(history, question, understanding, run.transcript, remaining)
            try:
                action = self._llm.parse("AgentAction", system, user, AgentAction)
            except LLMError as exc:
                run.step(index, "llm", None, "error", str(exc))
                return run.finish(pending or SQLOutcome("failed", message="The model call failed."))

            if action.action in ("clarify", "refuse"):
                run.step(index, action.action, None, "stopped", action.message or "")
                return run.finish(SQLOutcome(action.action, message=action.message, assumptions=action.assumptions))

            sql = (action.sql or "").strip()
            final = action.action == "final_sql" or remaining == 1
            checked = self._guard.check(sql)
            if not checked.ok:
                run.step(index, action.action, sql, "rejected", " ".join(checked.errors))
                if not self._repair:
                    break
                continue

            if not final:
                try:
                    preview = self._executor.run(checked.sql, max_rows=self._preview_rows)
                    run.step(index, "run_sql", checked.sql, "ok", preview.preview(self._preview_rows))
                except QueryError as exc:
                    run.step(index, "run_sql", checked.sql, "error", str(exc))
                continue

            try:
                result = self._executor.run(checked.sql)
            except QueryError as exc:
                run.step(index, "final_sql", checked.sql, "error", str(exc))
                if not self._repair:
                    break
                continue

            issues = list(checked.warnings)
            if result.row_count == 0:
                issues.append("The query returned no rows. Check filters and literal values (case, spelling, dates).")
            outcome = SQLOutcome(
                "answered", sql=checked.sql, result=result, presentation=action.presentation,
                assumptions=action.assumptions, warnings=issues,
            )
            key = _norm(checked.sql)
            if issues and self._repair and remaining > 1 and key not in acknowledged:
                acknowledged.add(key)
                pending = outcome
                detail = (
                    result.preview(5) + "\nCHECK: " + " ".join(issues)
                    + "\nIf the query is correct as is, resubmit the same final_sql; otherwise fix it."
                )
                run.step(index, "final_sql", checked.sql, "needs_check", detail)
                continue
            run.step(index, "final_sql", checked.sql, "final", result.preview(5))
            return run.finish(outcome)

        return run.finish(pending or SQLOutcome("failed", message="Could not produce a working query."))


class _Run:
    def __init__(self, observe: StepObserver | None) -> None:
        self._observe = observe
        self.steps: list[AgentStep] = []
        self.transcript: list[str] = []

    def step(self, index: int, action: str, sql: str | None, outcome: str, detail: str) -> None:
        record = AgentStep(index, action, sql, outcome, detail)  # type: ignore[arg-type]
        self.steps.append(record)
        label = {"ok": "RESULT", "final": "RESULT", "error": "DATABASE ERROR", "rejected": "REJECTED",
                 "needs_check": "RESULT", "stopped": "STOPPED"}[outcome]
        self.transcript.append(f"[step {index}] {action}\nSQL: {sql or '-'}\n{label}:\n{detail}")
        if self._observe:
            self._observe(record)

    def finish(self, outcome: SQLOutcome) -> SQLOutcome:
        outcome.steps = self.steps
        return outcome
