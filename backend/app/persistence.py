"""Durable session storage. Each session directory holds `session.json` (dataset state) and `conversation.json`."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from .schemas import ChatMessage, CleaningState, DataIssue, DatasetProfile, FileProgress, SessionState

logger = logging.getLogger(__name__)

SESSION_FILE = "session.json"
CONVERSATION_FILE = "conversation.json"


class StoredSession(BaseModel):
    session_id: str
    created_at: str
    updated_at: str
    title: str
    question_count: int = 0
    status: SessionState
    stage: str
    progress: float
    error: str | None = None
    files: list[FileProgress]
    upload_paths: dict[str, str]  # table name -> path relative to the session directory
    profile: DatasetProfile | None = None
    issues: list[DataIssue] = []
    cleaning: CleaningState = "not_needed"
    applied_fixes: list[str] = []


class StoredTurn(BaseModel):
    question: str
    standalone_question: str | None = None
    sql: str | None = None
    result_preview: str | None = None
    answer: str | None = None


class StoredConversation(BaseModel):
    messages: list[ChatMessage] = []
    turns: list[StoredTurn] = []


class SessionRepository(Protocol):
    def save_session(self, record: StoredSession) -> None: ...

    def load_sessions(self) -> list[StoredSession]: ...

    def save_conversation(self, session_id: str, record: StoredConversation) -> None: ...

    def load_conversation(self, session_id: str) -> StoredConversation | None: ...


class JsonSessionRepository:
    """Stores session records as JSON next to the session's uploads and DuckDB file."""

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._lock = threading.Lock()

    def _write(self, path: Path, model: BaseModel) -> None:
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(model.model_dump_json(), encoding="utf-8")
            os.replace(tmp, path)

    @staticmethod
    def _read(path: Path, model: type[BaseModel]):
        try:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            logger.warning("Ignoring unreadable session file %s", path, exc_info=True)
            return None

    def save_session(self, record: StoredSession) -> None:
        self._write(self._dir / record.session_id / SESSION_FILE, record)

    def load_sessions(self) -> list[StoredSession]:
        records = []
        for path in sorted(self._dir.glob(f"*/{SESSION_FILE}")):
            record = self._read(path, StoredSession)
            if record is not None and record.session_id == path.parent.name:
                records.append(record)
        return records

    def save_conversation(self, session_id: str, record: StoredConversation) -> None:
        self._write(self._dir / session_id / CONVERSATION_FILE, record)

    def load_conversation(self, session_id: str) -> StoredConversation | None:
        path = self._dir / session_id / CONVERSATION_FILE
        return self._read(path, StoredConversation) if path.exists() else None
