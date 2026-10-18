"""Composition root for the chat agent; experiment variants are configurations of the same parts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..llm import LLMClient, TrackedLLM
from ..schemas import DatasetProfile
from .agent import ChatAgent
from .context import SchemaContext, build_schema_context
from .executor import QueryExecutor
from .guard import SQLGuard
from .narration import LLMNarrator, Narrator, TemplateNarrator
from .notes import DataNotes
from .sql_agent import SQLAgent
from .understanding import LLMUnderstander, PassThroughUnderstander, Understander


class ChatUnavailableError(RuntimeError):
    """Chat needs a language model; raised when none is configured."""


@dataclass(frozen=True)
class ChatConfig:
    understand: bool = True
    max_steps: int = 4
    repair: bool = True
    narrate: bool = True
    max_rows: int = 1000
    timeout_s: float = 30.0


ONE_SHOT = ChatConfig(understand=False, max_steps=1, repair=False, narrate=False)
LOOP_ONLY = ChatConfig(understand=False)
HYBRID = ChatConfig()


@dataclass
class ChatSession:
    """Per-dataset chat resources. Close it to release the read-only database handle."""

    agent: ChatAgent
    llm: TrackedLLM
    executor: QueryExecutor
    context: SchemaContext

    def close(self) -> None:
        self.executor.close()


class ChatAgentFactory:
    def __init__(self, client: LLMClient | None, config: ChatConfig = HYBRID) -> None:
        self._client = client
        self._config = config

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def config(self) -> ChatConfig:
        return self._config

    def create(
        self,
        profile: DatasetProfile,
        database_path: Path,
        data_notes: DataNotes | None = None,
        executor: QueryExecutor | None = None,
    ) -> ChatSession:
        """Pass a shared `executor` to reuse one read-only connection per database (DuckDB requires one config per file)."""
        if self._client is None:
            raise ChatUnavailableError("Chat requires an LLM. Set OPENAI_API_KEY and LLM_ENABLED=true.")
        cfg = self._config
        llm = TrackedLLM(self._client)
        context = build_schema_context(profile)
        executor = executor or QueryExecutor(database_path, max_rows=cfg.max_rows, timeout_s=cfg.timeout_s)
        understander: Understander = LLMUnderstander(llm) if cfg.understand else PassThroughUnderstander()
        narrator: Narrator = LLMNarrator(llm) if cfg.narrate else TemplateNarrator()
        sql_agent = SQLAgent(
            llm, SQLGuard(context.columns, context.relationships), executor,
            max_steps=cfg.max_steps, repair=cfg.repair,
        )
        agent = ChatAgent(context, understander, sql_agent, narrator, data_notes)
        return ChatSession(agent, llm, executor, context)
