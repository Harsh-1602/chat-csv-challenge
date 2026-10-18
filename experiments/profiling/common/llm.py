"""Thin OpenAI wrapper for profiling experiments: structured outputs, disk cache, retries, usage log."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
DEFAULT_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium")
CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache"

# USD per 1M tokens (input, output). Used only for cost estimates in experiment reports.
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o-mini": (0.15, 0.60),
    # Third-party price trackers, 2026-10; not verified against openai.com. Reasoning tokens bill as output.
    "gpt-5.6-terra": (2.00, 12.00),
}

T = TypeVar("T", bound=BaseModel)


def is_reasoning_model(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


@dataclass
class CallRecord:
    step: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cached: bool
    error: str | None = None


@dataclass
class LLM:
    model: str = DEFAULT_MODEL
    use_cache: bool = True
    temperature: float = 0.0
    # Only sent to reasoning models (gpt-5*, o-series); those reject `temperature`.
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    max_retries: int = 3
    calls: list[CallRecord] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._client = OpenAI()
        self._lock = threading.Lock()

    def parse(self, step: str, system: str, user: str, schema: type[T], prompt_version: str) -> T:
        reasoning = is_reasoning_model(self.model)
        model_key = f"{self.model}@{self.reasoning_effort}" if reasoning else self.model
        key = hashlib.sha256(
            json.dumps([model_key, prompt_version, schema.__name__, system, user]).encode()
        ).hexdigest()[:32]
        sampling = {"reasoning_effort": self.reasoning_effort} if reasoning else {"temperature": self.temperature}
        cache_file = CACHE_DIR / f"{key}.json"
        if self.use_cache and cache_file.exists():
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            self._record(CallRecord(step, self.model, payload["pt"], payload["ct"], 0.0, True), system, user, payload["out"])
            return schema.model_validate(payload["out"])

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            started = time.perf_counter()
            try:
                response = self._client.chat.completions.parse(
                    model=self.model,
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format=schema,
                    **sampling,
                )
                parsed = response.choices[0].message.parsed
                if parsed is None:
                    raise RuntimeError(f"model refused or returned no parse: {response.choices[0].message.refusal}")
                latency = time.perf_counter() - started
                pt, ct = response.usage.prompt_tokens, response.usage.completion_tokens
                out = parsed.model_dump()
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"pt": pt, "ct": ct, "out": out}), encoding="utf-8")
                self._record(CallRecord(step, self.model, pt, ct, latency, False), system, user, out)
                return parsed
            except Exception as exc:  # noqa: BLE001 - retried, then surfaced to the caller
                last_error = exc
                time.sleep(2**attempt)
        self._record(CallRecord(step, self.model, 0, 0, 0.0, False, str(last_error)), system, user, None)
        raise RuntimeError(f"LLM step {step} failed: {last_error}") from last_error

    def _record(self, record: CallRecord, system: str, user: str, output: dict | None) -> None:
        with self._lock:
            self.calls.append(record)
            self.log.append({**record.__dict__, "system": system, "user": user, "output": output})

    def usage(self) -> dict:
        prompt = sum(c.prompt_tokens for c in self.calls)
        completion = sum(c.completion_tokens for c in self.calls)
        price = PRICES.get(self.model)
        return {
            "model": self.model,
            "reasoning_effort": self.reasoning_effort if is_reasoning_model(self.model) else None,
            "calls": len(self.calls),
            "cached_calls": sum(c.cached for c in self.calls),
            "failed_calls": sum(c.error is not None for c in self.calls),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            # None when the model has no entry in PRICES (cost unknown, not zero).
            "est_cost_usd": round(prompt / 1e6 * price[0] + completion / 1e6 * price[1], 5) if price else None,
        }
