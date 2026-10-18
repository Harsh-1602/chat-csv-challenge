"""Per-run usage accounting, layered over a shared LLMClient (decorator)."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .base import LLMClient, LLMError, T

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallRecord:
    step: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    error: str | None = None


class TrackedLLM:
    """Implements StructuredLLM; records every call so one profiling run can report its own usage."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client
        self._lock = threading.Lock()
        self.calls: list[CallRecord] = []

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def reasoning_effort(self) -> str | None:
        return self._client.reasoning_effort

    def parse(self, step: str, system: str, user: str, schema: type[T]) -> T:
        try:
            completion = self._client.complete(system, user, schema)
        except LLMError as exc:
            self._record(CallRecord(step, 0, 0, 0.0, str(exc)))
            logger.warning("LLM step %s failed: %s", step, exc)
            raise
        self._record(CallRecord(step, completion.prompt_tokens, completion.completion_tokens, completion.latency_s))
        return completion.output

    def _record(self, record: CallRecord) -> None:
        with self._lock:
            self.calls.append(record)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)
