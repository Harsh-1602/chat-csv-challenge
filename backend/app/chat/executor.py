"""C5 read-only execution: no writes, no file/network access, bounded rows and time."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from ..profiling.sql import first_error_line, json_scalar

READ_ONLY_CONFIG = {"enable_external_access": False}


class QueryError(RuntimeError):
    """The database rejected or aborted a query; the message is safe to show to the model and user."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_s: float

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def preview(self, max_rows: int = 20) -> str:
        """Plain-text rendering for prompts (model feedback and narration)."""
        if not self.columns:
            return "(no columns)"
        lines = [" | ".join(self.columns)]
        lines += [" | ".join("NULL" if v is None else str(v) for v in row) for row in self.rows[:max_rows]]
        shown = min(max_rows, self.row_count)
        suffix = "+" if self.truncated else ""
        lines.append(f"({self.row_count}{suffix} rows{'' if shown == self.row_count else f', first {shown} shown'})")
        return "\n".join(lines)


class QueryExecutor:
    def __init__(self, database_path: Path, max_rows: int = 1000, timeout_s: float = 30.0) -> None:
        self._path = database_path
        self._max_rows = max_rows
        self._timeout_s = timeout_s
        self._con: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()

    def _connection(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            if self._con is None:
                self._con = duckdb.connect(str(self._path), read_only=True, config=READ_ONLY_CONFIG)
            return self._con

    def run(self, sql: str, max_rows: int | None = None) -> QueryResult:
        limit = max_rows or self._max_rows
        cursor = self._connection().cursor()
        timer = threading.Timer(self._timeout_s, cursor.interrupt)
        started = time.perf_counter()
        timer.start()
        try:
            cursor.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            raw = cursor.fetchmany(limit + 1)
        except duckdb.InterruptException as exc:
            raise QueryError(f"Query exceeded the {self._timeout_s:g}s time limit.") from exc
        except duckdb.Error as exc:
            raise QueryError(first_error_line(exc, 500)) from exc
        finally:
            timer.cancel()
            cursor.close()
        rows = [[json_scalar(v) for v in row] for row in raw[:limit]]
        return QueryResult(columns, rows, len(raw) > limit, round(time.perf_counter() - started, 4))

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                self._con.close()
                self._con = None
