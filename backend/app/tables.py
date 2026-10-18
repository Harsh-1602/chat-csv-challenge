"""Table browser: paginated, sortable, read-only access to a session's (cleaned) tables."""

from __future__ import annotations

from .databases import ReadOnlyDatabases
from .jobs import JobStore
from .profiling.sql import quote_ident
from .schemas import TableColumn, TablePage

MAX_PAGE_SIZE = 200


class TableNotFoundError(KeyError):
    """The session has no table (or column) with this name."""


class TablesNotReadyError(RuntimeError):
    """Tables are browsable only once profiling is done and the cleaning decision is made."""


class TableBrowser:
    def __init__(self, store: JobStore, databases: ReadOnlyDatabases) -> None:
        self._store = store
        self._databases = databases

    def page(
        self, session_id: str, table: str, offset: int = 0, limit: int = 50,
        sort: str | None = None, descending: bool = False,
    ) -> TablePage:
        job = self._store.get(session_id)
        if job is None:
            raise KeyError(session_id)
        if not job.ready_for_chat or job.profile is None:
            raise TablesNotReadyError("Tables can be viewed once the data-quality review is done.")
        # Only names known from the profile reach SQL, so identifiers can never be injected.
        profile = next((t for t in job.profile.tables if t.table_name == table), None)
        if profile is None:
            raise TableNotFoundError(f"Unknown table '{table}'.")
        columns = [TableColumn(name=c.name, data_type=c.data_type, role=c.role) for c in profile.columns]
        if sort is not None and sort not in {c.name for c in columns}:
            raise TableNotFoundError(f"Unknown column '{sort}'.")

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        name = quote_ident(table)
        order = "rowid"
        if sort is not None:
            order = f"{quote_ident(sort)} {'DESC' if descending else 'ASC'} NULLS LAST, rowid"
        executor = self._databases.get(session_id, job.database_path)
        total = executor.run(f"SELECT COUNT(*) FROM {name}").rows[0][0]
        select = ", ".join(quote_ident(c.name) for c in columns)
        result = executor.run(
            f"SELECT {select} FROM {name} ORDER BY {order} LIMIT {limit} OFFSET {offset}", max_rows=limit,
        )
        return TablePage(
            table_name=table, columns=columns, rows=result.rows, offset=offset, limit=limit,
            total_rows=int(total), sort=sort, descending=descending,
        )
