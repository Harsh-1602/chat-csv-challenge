"""OpenAI adapter for LLMClient using Structured Outputs."""

from __future__ import annotations

import time

from openai import OpenAI, OpenAIError

from .base import Completion, LLMError, T

REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    return model.startswith(REASONING_MODEL_PREFIXES)


class OpenAIClient:
    def __init__(
        self,
        model: str,
        reasoning_effort: str = "medium",
        max_retries: int = 3,
        timeout_s: float = 120.0,
        client: OpenAI | None = None,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort if is_reasoning_model(model) else None
        # The SDK retries connection errors, 429s and 5xx with exponential backoff.
        self._client = client or OpenAI(max_retries=max_retries, timeout=timeout_s)

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_effort(self) -> str | None:
        return self._reasoning_effort

    def complete(self, system: str, user: str, schema: type[T]) -> Completion[T]:
        # Reasoning models reject `temperature`; classic models get deterministic sampling instead.
        sampling = {"reasoning_effort": self._reasoning_effort} if self._reasoning_effort else {"temperature": 0.0}
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.parse(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format=schema,
                **sampling,
            )
        except OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        message = response.choices[0].message
        if message.parsed is None:
            raise LLMError(f"Model returned no structured output (refusal: {message.refusal!r}).")
        usage = response.usage
        return Completion(
            output=message.parsed,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            latency_s=round(time.perf_counter() - started, 3),
        )
