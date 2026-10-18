"""Server-sent events: bridges chat progress from the worker thread onto an asyncio stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from .chat.sql_agent import AgentStep
from .schemas import AgentStepInfo

STAGE_LABELS = {
    "understanding": "Understanding the question",
    "querying": "Querying the data",
    "presenting": "Choosing how to show the result",
    "narrating": "Writing the answer",
}
_DONE = object()


def sse(event: str, data: Any) -> str:
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


class QueueChatEvents:
    """Implements ChatEvents; thread-safe because it only schedules puts on the event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    def _put(self, item: Any) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def stage(self, name: str, detail: str = "") -> None:
        self._put(sse("stage", {"name": name, "label": STAGE_LABELS.get(name, name), "detail": detail}))

    def step(self, step: AgentStep) -> None:
        info = AgentStepInfo(index=step.index, action=step.action, outcome=step.outcome, detail=step.detail, sql=step.sql)
        self._put(sse("step", info))

    def emit(self, event: str, data: Any) -> None:
        self._put(sse(event, data))

    def close(self) -> None:
        self._put(_DONE)

    async def stream(self):
        while True:
            item = await self._queue.get()
            if item is _DONE:
                return
            yield item
