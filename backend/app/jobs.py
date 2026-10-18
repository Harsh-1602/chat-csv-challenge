"""Session/job registry backed by a SessionRepository. Profiling runs in a background task and reports through a ProgressSink."""

import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .cleaning import DataCleaner
from .persistence import JsonSessionRepository, SessionRepository, StoredSession
from .profiling import ProfilingPipelineFactory, SourceFile
from .schemas import (
    CleaningState, DataIssue, DatasetProfile, FileProgress, FileState, SessionState, SessionStatus, SessionSummary,
)

logger = logging.getLogger(__name__)

INTERRUPTED_ERROR = "The analysis was interrupted by a server restart. Please upload the files again."
TITLE_MAX_CHARS = 80


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def files_title(files: list[FileProgress]) -> str:
    names = [f.file_name for f in files]
    return ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")


class CleaningConflictError(RuntimeError):
    """Raised when cleaning is requested for a session that is not waiting for a cleaning decision."""


class CopyConflictError(RuntimeError):
    """Raised when a session's dataset cannot be copied yet (still profiling, failed, or being cleaned)."""


@dataclass
class Job:
    session_id: str
    directory: Path
    files: list[FileProgress]
    paths: dict[str, Path]
    status: SessionState = "queued"
    stage: str = "Waiting to start"
    progress: float = 0.0
    profile: DatasetProfile | None = None
    error: str | None = None
    issues: list[DataIssue] = field(default_factory=list)
    cleaning: CleaningState = "not_needed"
    applied_fixes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = ""
    title: str = ""
    question_count: int = 0
    cleaning_busy: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.updated_at = self.updated_at or self.created_at
        self.title = self.title or files_title(self.files)

    @property
    def database_path(self) -> Path:
        return self.directory / "dataset.duckdb"

    @property
    def ready_for_chat(self) -> bool:
        return self.status == "completed" and self.cleaning != "pending"

    def snapshot(self) -> SessionStatus:
        with self.lock:
            return SessionStatus(
                session_id=self.session_id,
                status=self.status,
                progress=round(min(self.progress, 1.0), 4),
                stage=self.stage,
                files=[f.model_copy() for f in self.files],
                profile=self.profile,
                error=self.error,
                issues=list(self.issues),
                cleaning=self.cleaning,
                applied_fixes=list(self.applied_fixes),
                title=self.title,
                created_at=self.created_at,
            )

    def summary(self) -> SessionSummary:
        with self.lock:
            return SessionSummary(
                session_id=self.session_id, title=self.title, created_at=self.created_at,
                updated_at=self.updated_at, status=self.status, cleaning=self.cleaning,
                file_names=[f.file_name for f in self.files],
                table_count=self.profile.table_count if self.profile else 0,
                total_rows=self.profile.total_rows if self.profile else 0,
                question_count=self.question_count,
            )

    def to_record(self) -> StoredSession:
        with self.lock:
            return StoredSession(
                session_id=self.session_id, created_at=self.created_at, updated_at=self.updated_at,
                title=self.title, question_count=self.question_count, status=self.status, stage=self.stage,
                progress=self.progress, error=self.error, files=[f.model_copy() for f in self.files],
                upload_paths={t: p.relative_to(self.directory).as_posix() for t, p in self.paths.items()},
                profile=self.profile, issues=list(self.issues), cleaning=self.cleaning,
                applied_fixes=list(self.applied_fixes),
            )

    @classmethod
    def from_record(cls, record: StoredSession, directory: Path) -> "Job":
        job = cls(
            session_id=record.session_id, directory=directory, files=record.files,
            paths={t: directory / p for t, p in record.upload_paths.items()}, status=record.status,
            stage=record.stage, progress=record.progress, profile=record.profile, error=record.error,
            issues=record.issues, cleaning=record.cleaning, applied_fixes=record.applied_fixes,
            created_at=record.created_at, updated_at=record.updated_at, title=record.title,
            question_count=record.question_count,
        )
        if job.status in ("queued", "profiling"):
            job.status, job.stage, job.error = "failed", "Failed", INTERRUPTED_ERROR
        return job


class JobProgressSink:
    """Adapts pipeline progress events onto a Job's observable state."""

    def __init__(self, job: Job) -> None:
        self._job = job
        self._by_table = {f.table_name: f for f in job.files}

    def file_status(self, table: str, status: FileState, error: str | None = None) -> None:
        with self._job.lock:
            entry = self._by_table[table]
            entry.status = status
            entry.error = error

    def progress(self, fraction: float, stage: str) -> None:
        with self._job.lock:
            self._job.progress = max(self._job.progress, fraction)
            self._job.stage = stage


class JobStore:
    def __init__(
        self,
        storage_dir: Path,
        pipeline_factory: ProfilingPipelineFactory,
        cleaner: DataCleaner | None = None,
        repository: SessionRepository | None = None,
    ) -> None:
        self.sessions_dir = storage_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._factory = pipeline_factory
        self._cleaner = cleaner or DataCleaner()
        self.repository = repository or JsonSessionRepository(self.sessions_dir)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._restore()

    def _restore(self) -> None:
        for record in self.repository.load_sessions():
            job = Job.from_record(record, self.sessions_dir / record.session_id)
            self._jobs[job.session_id] = job
            if record.status != job.status:
                self._persist(job)
        if self._jobs:
            logger.info("Restored %d session(s) from %s", len(self._jobs), self.sessions_dir)

    def _persist(self, job: Job) -> None:
        try:
            self.repository.save_session(job.to_record())
        except Exception:
            logger.exception("Could not save session %s", job.session_id)

    def new_session_dir(self) -> tuple[str, Path]:
        session_id = uuid.uuid4().hex
        directory = self.sessions_dir / session_id
        (directory / "uploads").mkdir(parents=True)
        return session_id, directory

    def discard(self, directory: Path) -> None:
        shutil.rmtree(directory, ignore_errors=True)

    def create(self, session_id: str, directory: Path, files: list[FileProgress], paths: dict[str, Path]) -> Job:
        job = Job(session_id=session_id, directory=directory, files=files, paths=paths)
        with self._lock:
            self._jobs[session_id] = job
        self._persist(job)
        return job

    def copy_dataset(self, session_id: str) -> Job:
        """Starts a new session on an existing session's dataset: the uploads, profiled DuckDB file, profile
        and cleaning decision are copied; the conversation starts empty. No re-upload or re-profiling."""
        source = self.get(session_id)
        if source is None:
            raise KeyError(session_id)
        new_id, directory = self.new_session_dir()
        try:
            # Holding the source lock keeps cleaning from rewriting the database mid-copy.
            with source.lock:
                if source.status != "completed" or source.profile is None or source.cleaning_busy:
                    raise CopyConflictError("Only a fully analysed dataset can be reused for a new chat.")
                paths = {}
                for table, path in source.paths.items():
                    target = directory / path.relative_to(source.directory)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    paths[table] = target
                for name in (source.database_path.name, source.database_path.name + ".wal"):
                    if (source.directory / name).exists():
                        shutil.copy2(source.directory / name, directory / name)
                job = Job(
                    session_id=new_id, directory=directory, files=[f.model_copy() for f in source.files],
                    paths=paths, status="completed", stage=source.stage, progress=1.0,
                    profile=source.profile.model_copy(deep=True), issues=list(source.issues),
                    cleaning=source.cleaning, applied_fixes=list(source.applied_fixes),
                )
        except BaseException:
            self.discard(directory)
            raise
        with self._lock:
            self._jobs[new_id] = job
        self._persist(job)
        return job

    def get(self, session_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(session_id)

    def list_summaries(self) -> list[SessionSummary]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted((j.summary() for j in jobs), key=lambda s: s.updated_at, reverse=True)

    def record_question(self, session_id: str, question: str) -> None:
        """Bumps the session's activity; the first question becomes its title."""
        job = self.get(session_id)
        if job is None:
            return
        with job.lock:
            if job.question_count == 0:
                job.title = question if len(question) <= TITLE_MAX_CHARS else question[: TITLE_MAX_CHARS - 1] + "…"
            job.question_count += 1
            job.updated_at = utc_now()
        self._persist(job)

    def run(self, session_id: str) -> None:
        job = self.get(session_id)
        if job is None:
            return
        with job.lock:
            job.status = "profiling"
            job.stage = "Starting analysis"
        sources = [SourceFile(f.file_name, f.table_name, job.paths[f.table_name], f.size_bytes) for f in job.files]
        try:
            profile = self._factory.create().run(job.database_path, sources, JobProgressSink(job))
        except Exception:
            logger.exception("Profiling failed for session %s", session_id)
            with job.lock:
                job.status = "failed"
                job.error = "Unexpected error while profiling the files."
                job.stage = "Failed"
                job.updated_at = utc_now()
            self._persist(job)
            return

        issues = self._detect_issues(job, profile) if profile.table_count else []
        with job.lock:
            job.profile = profile
            job.progress = 1.0
            job.issues = issues
            job.cleaning = "pending" if any(i.fix for i in issues) else "not_needed"
            if profile.table_count == 0:
                job.status = "failed"
                job.error = "None of the uploaded files could be read as CSV."
                job.stage = "Failed"
            else:
                job.status = "completed"
                job.stage = "Analysis complete"
            job.updated_at = utc_now()
        self._persist(job)

    def _detect_issues(self, job: Job, profile: DatasetProfile) -> list[DataIssue]:
        try:
            return self._cleaner.detect(job.database_path, profile)
        except Exception:
            logger.exception("Issue detection failed for session %s", job.session_id)
            return []

    def decide_cleaning(self, session_id: str, issue_ids: list[str]) -> SessionStatus:
        """Applies the selected fixes (an empty list keeps the data as-is). Allowed once, before chat starts."""
        job = self.get(session_id)
        if job is None:
            raise KeyError(session_id)
        with job.lock:
            if job.status != "completed" or job.cleaning != "pending" or job.profile is None or job.cleaning_busy:
                raise CleaningConflictError("This dataset is not waiting for a cleaning decision.")
            profile, issues = job.profile, list(job.issues)
            job.cleaning_busy = True
            job.stage = "Cleaning data"
        try:
            result = self._cleaner.apply(job.database_path, profile, issues, issue_ids)
        except Exception:
            logger.exception("Cleaning failed for session %s", session_id)
            with job.lock:
                job.cleaning_busy = False
                job.stage = "Analysis complete"
            raise
        with job.lock:
            job.cleaning_busy = False
            job.profile = result.profile
            job.issues = result.issues
            job.applied_fixes = result.applied
            job.cleaning = "applied" if result.applied else "skipped"
            job.stage = "Ready to chat"
            job.updated_at = utc_now()
        self._persist(job)
        return job.snapshot()
