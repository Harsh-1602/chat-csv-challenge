"""Conversation service: one chat session per dataset, its history, and API-shaped messages."""

from __future__ import annotations

import csv
import io
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .chat.agent import ChatAnswer, ChatEvents
from .chat.factory import ChatAgentFactory, ChatSession
from .chat.memory import Turn
from .cleaning import data_notes
from .databases import ReadOnlyDatabases
from .jobs import JobStore
from .persistence import SessionRepository, StoredConversation, StoredTurn
from .schemas import AgentStepInfo, ChartSpec, ChatMessage, ResultTable

logger = logging.getLogger(__name__)
DOWNLOAD_MAX_ROWS = 100_000


class ConversationNotReadyError(RuntimeError):
    """The dataset is still being profiled, failed, or is waiting for a cleaning decision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_message(answer: ChatAnswer, elapsed_s: float, llm_calls: int) -> ChatMessage:
    table = chart = None
    view = None
    if answer.result is not None:
        r = answer.result
        table = ResultTable(columns=r.columns, rows=r.rows, row_count=r.row_count, truncated=r.truncated)
        view = "table"
    p = answer.presentation
    if p is not None:
        view = p.kind
        if p.kind == "chart" and p.chart_type and p.x and p.y:
            chart = ChartSpec(chart_type=p.chart_type, x=p.x, y=p.y, series=p.series, title=p.title)
        elif p.kind == "chart":
            view = "table"
    return ChatMessage(
        id=uuid.uuid4().hex, role="assistant", content=answer.message, created_at=_now(), status=answer.status,
        standalone_question=answer.standalone_question, sql=answer.sql, view=view, table=table, chart=chart,
        assumptions=answer.assumptions, warnings=answer.warnings, data_quality=answer.data_quality,
        notes=answer.notes, elapsed_s=round(elapsed_s, 2), llm_calls=llm_calls,
        steps=[AgentStepInfo(index=s.index, action=s.action, outcome=s.outcome, detail=s.detail, sql=s.sql)
               for s in answer.steps],
    )


@dataclass
class Conversation:
    chat: ChatSession
    turns: list[Turn] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)  # one question at a time per dataset


class ConversationService:
    def __init__(
        self,
        chat_factory: ChatAgentFactory,
        store: JobStore,
        repository: SessionRepository | None = None,
        databases: ReadOnlyDatabases | None = None,
    ) -> None:
        self._factory = chat_factory
        self._store = store
        self._repository = repository or store.repository
        self._databases = databases or ReadOnlyDatabases(chat_factory.config.max_rows, chat_factory.config.timeout_s)
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._factory.available

    def ensure_ready(self, session_id: str) -> None:
        job = self._store.get(session_id)
        if job is None:
            raise KeyError(session_id)
        if not job.ready_for_chat:
            if job.status == "completed":
                raise ConversationNotReadyError("Review the data-quality fixes before chatting.")
            raise ConversationNotReadyError("The dataset is not ready for chat.")

    def _stored(self, session_id: str) -> StoredConversation:
        return self._repository.load_conversation(session_id) or StoredConversation()

    def _conversation(self, session_id: str) -> Conversation:
        self.ensure_ready(session_id)
        with self._lock:
            conversation = self._conversations.get(session_id)
            if conversation is None:
                job = self._store.get(session_id)
                assert job is not None and job.profile is not None
                chat = self._factory.create(
                    job.profile, job.database_path, data_notes=data_notes(job.issues),
                    executor=self._databases.get(session_id, job.database_path),
                )
                stored = self._stored(session_id)
                conversation = self._conversations[session_id] = Conversation(
                    chat, turns=[Turn(**t.model_dump()) for t in stored.turns], messages=list(stored.messages),
                )
            return conversation

    def _save(self, session_id: str, conversation: Conversation) -> None:
        record = StoredConversation(
            messages=conversation.messages, turns=[StoredTurn(**asdict(t)) for t in conversation.turns],
        )
        try:
            self._repository.save_conversation(session_id, record)
        except Exception:
            logger.exception("Could not save the conversation for session %s", session_id)

    def ask(self, session_id: str, question: str, events: ChatEvents | None = None) -> ChatMessage:
        conversation = self._conversation(session_id)
        with conversation.lock:
            conversation.messages.append(
                ChatMessage(id=uuid.uuid4().hex, role="user", content=question, created_at=_now())
            )
            self._store.record_question(session_id, question)
            try:
                calls_before = len(conversation.chat.llm.calls)
                started = time.perf_counter()
                answer = conversation.chat.agent.ask(question, list(conversation.turns), events)
                elapsed = time.perf_counter() - started
                message = to_message(answer, elapsed, len(conversation.chat.llm.calls) - calls_before)
                conversation.turns.append(answer.to_turn())
                conversation.messages.append(message)
                return message
            finally:
                self._save(session_id, conversation)

    def messages(self, session_id: str) -> list[ChatMessage]:
        self.ensure_ready(session_id)
        with self._lock:
            conversation = self._conversations.get(session_id)
        if conversation is not None:
            return list(conversation.messages)
        return list(self._stored(session_id).messages)

    def download_csv(self, session_id: str, message_id: str) -> str:
        """Re-runs the message's verified SQL with a higher row cap and renders it as CSV."""
        conversation = self._conversation(session_id)
        message = next((m for m in conversation.messages if m.id == message_id and m.sql), None)
        if message is None or message.sql is None:
            raise KeyError(message_id)
        result = conversation.chat.executor.run(message.sql, max_rows=DOWNLOAD_MAX_ROWS)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(result.columns)
        writer.writerows(result.rows)
        return buffer.getvalue()

    def close_all(self) -> None:
        with self._lock:
            for conversation in self._conversations.values():
                conversation.chat.close()
            self._conversations.clear()
