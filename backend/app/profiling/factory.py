"""Composition root for profiling: decides which strategies a run uses (Dependency Inversion)."""

from __future__ import annotations

from ..config import LLMSettings
from ..llm import LLMClient, TrackedLLM
from .interpreters import LLMTableInterpreter
from .judges import LLMRelationshipJudge
from .pipeline import HEURISTIC_STRATEGIES, ProfilingPipeline, Strategies
from .semantics import LLMSemanticModeler


class ProfilingPipelineFactory:
    def __init__(self, settings: LLMSettings, client: LLMClient | None) -> None:
        self._settings = settings
        self._client = client if settings.enabled else None

    @property
    def llm_enabled(self) -> bool:
        return self._client is not None

    def create(self) -> ProfilingPipeline:
        """A fresh pipeline per run, so token/call accounting is scoped to one upload."""
        if self._client is None:
            return ProfilingPipeline(HEURISTIC_STRATEGIES)
        llm = TrackedLLM(self._client)
        strategies = Strategies(
            interpreter=LLMTableInterpreter(llm),
            judge=LLMRelationshipJudge(llm),
            semantic=LLMSemanticModeler(llm),
        )
        return ProfilingPipeline(strategies, usage=llm, max_parallel_calls=self._settings.max_parallel_calls)
