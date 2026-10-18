"""Public API contract. Internal profiling models live in app.profiling.models."""

from typing import Literal

from pydantic import BaseModel

Scalar = str | int | float | bool | None
ColumnRole = Literal["identifier", "measure", "time", "dimension", "text", "boolean"]
SessionState = Literal["queued", "profiling", "completed", "failed"]
FileState = Literal["pending", "loading", "profiling", "completed", "failed"]
KeySource = Literal["llm_verified", "llm_minimised", "sql_fallback", "none"]
RelationshipStatus = Literal["accepted", "needs_review"]
RelationshipSource = Literal["candidate", "llm_proposed"]


class ValueCount(BaseModel):
    value: Scalar
    count: int


class ColumnProfile(BaseModel):
    name: str
    data_type: str
    role: ColumnRole
    semantic_type: str | None = None
    description: str | None = None
    unit: str | None = None
    references_entity: str | None = None
    non_null_count: int
    null_count: int
    null_pct: float
    distinct_count: int
    min: Scalar = None
    max: Scalar = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    outlier_count: int | None = None
    top_values: list[ValueCount] | None = None


class TableProfile(BaseModel):
    table_name: str
    file_name: str
    file_size_bytes: int
    row_count: int
    column_count: int
    duplicate_row_count: int
    entity: str
    description: str
    grain: str
    primary_key: list[str]
    primary_key_source: KeySource
    columns: list[ColumnProfile]
    warnings: list[str]


class Relationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    status: RelationshipStatus
    source: RelationshipSource
    reason: str
    cardinality: Literal["many-to-one", "one-to-one"]
    match_rate: float
    orphan_count: int
    parent_coverage: float
    fan_out: float | None = None
    join_warning: str | None = None


class Metric(BaseModel):
    name: str
    table: str
    expression: str
    description: str
    valid: bool
    error: str | None = None


class Synonym(BaseModel):
    term: str
    maps_to: str


class SemanticModel(BaseModel):
    dataset_summary: str
    metrics: list[Metric]
    synonyms: list[Synonym]
    caveats: list[str]
    example_questions: list[str]


class AnalysisInfo(BaseModel):
    """How the profile was produced: which model ran and whether any AI step fell back to heuristics."""

    llm_enabled: bool
    model: str | None = None
    reasoning_effort: str | None = None
    degraded: bool = False
    notes: list[str] = []
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timings_s: dict[str, float] = {}


class FailedFile(BaseModel):
    file_name: str
    error: str


class DatasetProfile(BaseModel):
    table_count: int
    total_rows: int
    tables: list[TableProfile]
    relationships: list[Relationship]
    semantic_model: SemanticModel | None = None
    failed_files: list[FailedFile]
    analysis: AnalysisInfo


class FileProgress(BaseModel):
    file_name: str
    table_name: str
    size_bytes: int
    status: FileState
    error: str | None = None


IssueKind = Literal[
    "duplicate_rows", "whitespace", "null_tokens", "numeric_text", "date_text", "case_variants",
    "missing_values", "outliers",
]
CleaningState = Literal["not_needed", "pending", "applied", "skipped"]


class DataIssue(BaseModel):
    """A data-quality issue found after profiling. `fix` is None for informational issues."""

    id: str
    table: str
    column: str | None
    kind: IssueKind
    affected_rows: int
    description: str
    fix: str | None
    examples: list[str] = []


class SessionStatus(BaseModel):
    session_id: str
    status: SessionState
    progress: float
    stage: str
    files: list[FileProgress]
    profile: DatasetProfile | None = None
    error: str | None = None
    issues: list[DataIssue] = []
    cleaning: CleaningState = "not_needed"
    applied_fixes: list[str] = []
    chat_available: bool = False
    title: str = ""
    created_at: str | None = None


class SessionSummary(BaseModel):
    """One row of the session history list."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: SessionState
    cleaning: CleaningState
    file_names: list[str]
    table_count: int
    total_rows: int
    question_count: int


class CleaningRequest(BaseModel):
    """Issue ids to fix; an empty list means 'use the data as-is'."""

    issue_ids: list[str]


ChatStatus = Literal["answered", "clarify", "refuse", "chitchat", "failed"]
PresentationKind = Literal["text", "table", "chart"]


class ChatRequest(BaseModel):
    question: str


class TableColumn(BaseModel):
    name: str
    data_type: str
    role: ColumnRole


class TablePage(BaseModel):
    """One page of a dataset table for the data viewer."""

    table_name: str
    columns: list[TableColumn]
    rows: list[list[Scalar]]
    offset: int
    limit: int
    total_rows: int
    sort: str | None = None
    descending: bool = False


class ResultTable(BaseModel):
    columns: list[str]
    rows: list[list[Scalar]]
    row_count: int
    truncated: bool


class ChartSpec(BaseModel):
    chart_type: str
    x: str
    y: list[str]
    series: str | None = None
    title: str | None = None


class AgentStepInfo(BaseModel):
    index: int
    action: str
    outcome: str
    detail: str
    sql: str | None = None


class ChatMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str
    status: ChatStatus | None = None
    standalone_question: str | None = None
    sql: str | None = None
    view: PresentationKind | None = None
    table: ResultTable | None = None
    chart: ChartSpec | None = None
    assumptions: list[str] = []
    warnings: list[str] = []
    data_quality: list[str] = []
    notes: list[str] = []
    steps: list[AgentStepInfo] = []
    elapsed_s: float | None = None
    llm_calls: int | None = None


class SessionCreated(BaseModel):
    session_id: str
    status_url: str
