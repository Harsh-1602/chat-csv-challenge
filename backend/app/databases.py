"""One read-only executor per session database, shared by every reader (chat agent, table browser).

DuckDB allows a single configuration per database file within a process, so all readers of a
session must go through the same read-only connection.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .chat.executor import QueryExecutor


class ReadOnlyDatabases:
    def __init__(self, max_rows: int = 1000, timeout_s: float = 30.0) -> None:
        self._max_rows = max_rows
        self._timeout_s = timeout_s
        self._executors: dict[str, QueryExecutor] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str, database_path: Path) -> QueryExecutor:
        with self._lock:
            executor = self._executors.get(session_id)
            if executor is None:
                executor = self._executors[session_id] = QueryExecutor(
                    database_path, max_rows=self._max_rows, timeout_s=self._timeout_s,
                )
            return executor

    def close_all(self) -> None:
        with self._lock:
            for executor in self._executors.values():
                executor.close()
            self._executors.clear()
