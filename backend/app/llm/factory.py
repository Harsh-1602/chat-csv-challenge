from __future__ import annotations

import logging

from ..config import LLMSettings
from .base import LLMClient

logger = logging.getLogger(__name__)


def build_llm_client(settings: LLMSettings) -> LLMClient | None:
    """Returns None when AI is disabled or unconfigured; callers then use the heuristic path."""
    if not settings.enabled:
        logger.info("LLM disabled; profiling runs in heuristic (SQL-only) mode.")
        return None
    from .openai_client import OpenAIClient

    return OpenAIClient(
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_retries=settings.max_retries,
        timeout_s=settings.timeout_s,
    )
