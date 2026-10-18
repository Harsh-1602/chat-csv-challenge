import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .chat.executor import QueryError
from .chat.factory import ChatAgentFactory
from .config import Settings, load_settings
from .conversations import ConversationNotReadyError, ConversationService
from .databases import ReadOnlyDatabases
from .jobs import CleaningConflictError, CopyConflictError, Job, JobStore
from .llm import build_llm_client
from .profiling import ProfilingPipelineFactory, safe_table_name
from .schemas import (
    ChatMessage, ChatRequest, CleaningRequest, FileProgress, SessionCreated, SessionStatus, SessionSummary, TablePage,
)
from .streaming import QueueChatEvents
from .tables import MAX_PAGE_SIZE, TableBrowser, TableNotFoundError, TablesNotReadyError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
CHUNK_SIZE = 1024 * 1024
MAX_QUESTION_CHARS = 2000


def create_app(
    settings: Settings | None = None,
    pipeline_factory: ProfilingPipelineFactory | None = None,
    chat_factory: ChatAgentFactory | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if pipeline_factory is None or chat_factory is None:
        client = build_llm_client(settings.llm)
        pipeline_factory = pipeline_factory or ProfilingPipelineFactory(settings.llm, client)
        chat_factory = chat_factory or ChatAgentFactory(client)
    store = JobStore(settings.storage_dir, pipeline_factory)
    databases = ReadOnlyDatabases(chat_factory.config.max_rows, chat_factory.config.timeout_s)
    conversations = ConversationService(chat_factory, store, databases=databases)
    tables = TableBrowser(store, databases)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        conversations.close_all()
        databases.close_all()

    app = FastAPI(title="CSV Chat Analyst API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    def require_job(session_id: str) -> Job:
        job = store.get(session_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found.")
        return job

    def status_of(snapshot: SessionStatus) -> SessionStatus:
        snapshot.chat_available = conversations.available
        return snapshot

    def require_chat(session_id: str) -> None:
        require_job(session_id)
        if not conversations.available:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Chat needs a language model. Set OPENAI_API_KEY and LLM_ENABLED=true.")
        try:
            conversations.ensure_ready(session_id)
        except ConversationNotReadyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/sessions", status_code=status.HTTP_202_ACCEPTED, response_model=SessionCreated)
    async def create_session(
        request: Request, files: list[UploadFile], background_tasks: BackgroundTasks
    ) -> SessionCreated:
        if not files:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload at least one CSV file.")
        if len(files) > settings.max_files:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Upload at most {settings.max_files} files.")
        for upload in files:
            if not (upload.filename or "").lower().endswith(".csv"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{upload.filename}' is not a .csv file.")

        session_id, directory = store.new_session_dir()
        table_names: set[str] = set()
        progress: list[FileProgress] = []
        paths: dict[str, Path] = {}
        try:
            for index, upload in enumerate(files):
                original_name = Path(upload.filename or f"file_{index}.csv").name
                table = safe_table_name(original_name, table_names)
                path = directory / "uploads" / f"{index:02d}_{table}.csv"
                size = 0
                with path.open("wb") as out:
                    while chunk := await upload.read(CHUNK_SIZE):
                        size += len(chunk)
                        if size > settings.max_file_size_bytes:
                            limit_mb = settings.max_file_size_bytes // (1024 * 1024)
                            raise HTTPException(
                                status.HTTP_413_CONTENT_TOO_LARGE,
                                f"'{original_name}' exceeds the {limit_mb} MB limit.",
                            )
                        out.write(chunk)
                if size == 0:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{original_name}' is empty.")
                progress.append(FileProgress(file_name=original_name, table_name=table, size_bytes=size, status="pending"))
                paths[table] = path
        except HTTPException:
            store.discard(directory)
            raise
        finally:
            for upload in files:
                await upload.close()

        store.create(session_id, directory, progress, paths)
        background_tasks.add_task(store.run, session_id)
        return SessionCreated(session_id=session_id, status_url=str(request.url_for("get_session", session_id=session_id).path))

    @app.get("/api/sessions", response_model=list[SessionSummary])
    def list_sessions() -> list[SessionSummary]:
        """All stored sessions, most recently active first."""
        return store.list_summaries()

    @app.get("/api/sessions/{session_id}", response_model=SessionStatus, name="get_session")
    def get_session(session_id: str) -> SessionStatus:
        return status_of(require_job(session_id).snapshot())

    @app.post("/api/sessions/{session_id}/copy", status_code=status.HTTP_201_CREATED, response_model=SessionCreated)
    async def copy_session(request: Request, session_id: str) -> SessionCreated:
        """New session with the same (profiled, cleaned) dataset and an empty conversation."""
        require_job(session_id)
        try:
            job = await run_in_threadpool(store.copy_dataset, session_id)
        except CopyConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return SessionCreated(session_id=job.session_id,
                              status_url=str(request.url_for("get_session", session_id=job.session_id).path))

    @app.post("/api/sessions/{session_id}/cleaning", response_model=SessionStatus)
    def decide_cleaning(session_id: str, body: CleaningRequest) -> SessionStatus:
        require_job(session_id)
        try:
            return status_of(store.decide_cleaning(session_id, body.issue_ids))
        except CleaningConflictError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/api/sessions/{session_id}/tables/{table_name}/rows", response_model=TablePage)
    def table_rows(
        session_id: str,
        table_name: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
        sort: str | None = None,
        desc: bool = False,
    ) -> TablePage:
        """A page of rows from one (cleaned) table, for the data viewer. Works without an LLM."""
        require_job(session_id)
        try:
            return tables.page(session_id, table_name, offset, limit, sort, desc)
        except TablesNotReadyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except TableNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, exc.args[0]) from exc
        except QueryError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    @app.get("/api/sessions/{session_id}/messages", response_model=list[ChatMessage])
    def list_messages(session_id: str) -> list[ChatMessage]:
        require_job(session_id)
        try:
            return conversations.messages(session_id)
        except ConversationNotReadyError:
            return []

    @app.post("/api/sessions/{session_id}/messages")
    async def post_message(session_id: str, body: ChatRequest) -> StreamingResponse:
        """Streams `stage` and `step` events while the agent works, then one `answer` (or `error`) event."""
        question = body.question.strip()
        if not question:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ask a question.")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Keep questions under {MAX_QUESTION_CHARS} characters.")
        require_chat(session_id)

        events = QueueChatEvents(asyncio.get_running_loop())

        async def work() -> None:
            try:
                answer = await run_in_threadpool(conversations.ask, session_id, question, events)
                events.emit("answer", answer)
            except Exception:
                logger.exception("Chat failed for session %s", session_id)
                events.emit("error", {"message": "Something went wrong while answering. Please try again."})
            finally:
                events.close()

        task = asyncio.create_task(work())

        async def stream():
            try:
                async for chunk in events.stream():
                    yield chunk
            finally:
                await task

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sessions/{session_id}/messages/{message_id}/download.csv")
    def download_result(session_id: str, message_id: str) -> Response:
        require_chat(session_id)
        try:
            content = conversations.download_csv(session_id, message_id)
        except KeyError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No result for this message.") from exc
        except QueryError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
        return Response(content, media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="result-{message_id[:8]}.csv"',
        })

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
