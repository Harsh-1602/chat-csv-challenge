// Mirrors backend/app/schemas.py — keep the two in sync.
export type ColumnRole = "identifier" | "measure" | "time" | "dimension" | "text" | "boolean";
export type KeySource = "llm_verified" | "llm_minimised" | "sql_fallback" | "none";
export type RelationshipStatus = "accepted" | "needs_review";
export type RelationshipSource = "candidate" | "llm_proposed";
export type SessionState = "queued" | "profiling" | "completed" | "failed";
export type FileState = "pending" | "loading" | "profiling" | "completed" | "failed";
export type Scalar = string | number | boolean | null;

export interface ValueCount {
  value: Scalar;
  count: number;
}

export interface ColumnProfile {
  name: string;
  data_type: string;
  role: ColumnRole;
  semantic_type: string | null;
  description: string | null;
  unit: string | null;
  references_entity: string | null;
  non_null_count: number;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  min: Scalar;
  max: Scalar;
  mean: number | null;
  median: number | null;
  std: number | null;
  outlier_count: number | null;
  top_values: ValueCount[] | null;
}

export interface TableProfile {
  table_name: string;
  file_name: string;
  file_size_bytes: number;
  row_count: number;
  column_count: number;
  duplicate_row_count: number;
  entity: string;
  description: string;
  grain: string;
  primary_key: string[];
  primary_key_source: KeySource;
  columns: ColumnProfile[];
  warnings: string[];
}

export interface Relationship {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  status: RelationshipStatus;
  source: RelationshipSource;
  reason: string;
  cardinality: "many-to-one" | "one-to-one";
  match_rate: number;
  orphan_count: number;
  parent_coverage: number;
  fan_out: number | null;
  join_warning: string | null;
}

export interface Metric {
  name: string;
  table: string;
  expression: string;
  description: string;
  valid: boolean;
  error: string | null;
}

export interface Synonym {
  term: string;
  maps_to: string;
}

export interface SemanticModel {
  dataset_summary: string;
  metrics: Metric[];
  synonyms: Synonym[];
  caveats: string[];
  example_questions: string[];
}

export interface AnalysisInfo {
  llm_enabled: boolean;
  model: string | null;
  reasoning_effort: string | null;
  degraded: boolean;
  notes: string[];
  llm_calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  timings_s: Record<string, number>;
}

export interface FailedFile {
  file_name: string;
  error: string;
}

export interface DatasetProfile {
  table_count: number;
  total_rows: number;
  tables: TableProfile[];
  relationships: Relationship[];
  semantic_model: SemanticModel | null;
  failed_files: FailedFile[];
  analysis: AnalysisInfo;
}

export interface FileProgress {
  file_name: string;
  table_name: string;
  size_bytes: number;
  status: FileState;
  error: string | null;
}

export type IssueKind =
  | "duplicate_rows"
  | "whitespace"
  | "null_tokens"
  | "numeric_text"
  | "date_text"
  | "case_variants"
  | "missing_values"
  | "outliers";
export type CleaningState = "not_needed" | "pending" | "applied" | "skipped";

export interface DataIssue {
  id: string;
  table: string;
  column: string | null;
  kind: IssueKind;
  affected_rows: number;
  description: string;
  fix: string | null;
  examples: string[];
}

export interface SessionStatus {
  session_id: string;
  title: string;
  created_at: string | null;
  status: SessionState;
  progress: number;
  stage: string;
  files: FileProgress[];
  profile: DatasetProfile | null;
  error: string | null;
  issues: DataIssue[];
  cleaning: CleaningState;
  applied_fixes: string[];
  chat_available: boolean;
}

export interface SessionSummary {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  status: SessionState;
  cleaning: CleaningState;
  file_names: string[];
  table_count: number;
  total_rows: number;
  question_count: number;
}

export type ChatStatus = "answered" | "clarify" | "refuse" | "chitchat" | "failed";
export type PresentationKind = "text" | "table" | "chart";

export interface TableColumn {
  name: string;
  data_type: string;
  role: ColumnRole;
}

export interface TablePage {
  table_name: string;
  columns: TableColumn[];
  rows: Scalar[][];
  offset: number;
  limit: number;
  total_rows: number;
  sort: string | null;
  descending: boolean;
}

export interface ResultTable {
  columns: string[];
  rows: Scalar[][];
  row_count: number;
  truncated: boolean;
}

export interface ChartSpec {
  chart_type: string;
  x: string;
  y: string[];
  series: string | null;
  title: string | null;
}

export interface AgentStepInfo {
  index: number;
  action: string;
  outcome: string;
  detail: string;
  sql: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  status: ChatStatus | null;
  standalone_question: string | null;
  sql: string | null;
  view: PresentationKind | null;
  table: ResultTable | null;
  chart: ChartSpec | null;
  assumptions: string[];
  warnings: string[];
  data_quality: string[];
  notes: string[];
  steps: AgentStepInfo[];
  elapsed_s: number | null;
  llm_calls: number | null;
}

export interface StageEvent {
  name: string;
  label: string;
  detail: string;
}

export type ChatStreamEvent =
  | { type: "stage"; data: StageEvent }
  | { type: "step"; data: AgentStepInfo }
  | { type: "answer"; data: ChatMessage }
  | { type: "error"; data: { message: string } };

export interface SessionCreated {
  session_id: string;
  status_url: string;
}
