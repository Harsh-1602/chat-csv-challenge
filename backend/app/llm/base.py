"""Provider-agnostic LLM contracts. Business code depends on these, never on a vendor SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when a structured completion cannot be produced (network, refusal, schema mismatch)."""


@dataclass(frozen=True)
class Completion(Generic[T]):
    output: T
    prompt_tokens: int
    completion_tokens: int
    latency_s: float


class LLMClient(Protocol):
    """A model that returns a validated pydantic object for a system + user prompt."""

    @property
    def model(self) -> str: ...

    @property
    def reasoning_effort(self) -> str | None: ...

    def complete(self, system: str, user: str, schema: type[T]) -> Completion[T]: ...


class StructuredLLM(Protocol):
    """The narrow interface pipeline steps need: a named step returning a parsed object."""

    def parse(self, step: str, system: str, user: str, schema: type[T]) -> T: ...
