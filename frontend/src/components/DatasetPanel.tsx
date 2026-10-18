import { useState } from "react";
import { formatBytes, formatCount, formatValue } from "../format";
import type { AnalysisInfo, ColumnProfile, DatasetProfile, KeySource, Relationship, SemanticModel, TableProfile } from "../types";

const KEY_SOURCE_LABEL: Record<KeySource, string> = {
  llm_verified: "AI-proposed, verified unique",
  llm_minimised: "AI-proposed, reduced to a minimal unique key",
  sql_fallback: "detected from the data",
  none: "no unique key",
};

function columnDetail(column: ColumnProfile): string {
  if (column.role === "measure") {
    return `${formatValue(column.min)} – ${formatValue(column.max)} · mean ${formatValue(column.mean)}`;
  }
  if (column.role === "time" || column.role === "identifier") {
    return `${formatValue(column.min)} – ${formatValue(column.max)}`;
  }
  if (column.top_values?.length) {
    return column.top_values.map((v) => `${formatValue(v.value)} (${formatCount(v.count)})`).join(", ");
  }
  return `${formatCount(column.distinct_count)} distinct values`;
}

function ColumnRow({ column }: { column: ColumnProfile }) {
  return (
    <li className="column-row">
      <div className="column-head">
        <span className="column-name">{column.name}</span>
        <span className={`role-badge ${column.role}`} title={column.semantic_type ?? undefined}>
          {column.role}
          {column.unit && ` · ${column.unit}`}
        </span>
      </div>
      {column.description && <div className="column-detail">{column.description}</div>}
      <div className="column-detail muted">
        {column.data_type} · {formatCount(column.distinct_count)} distinct
        {column.null_count > 0 && <span className="warn-text"> · {column.null_pct}% missing</span>}
      </div>
      <div className="column-detail muted">{columnDetail(column)}</div>
    </li>
  );
}

function TableCard({ table, onView }: { table: TableProfile; onView?: (table: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="table-card">
      <div className="table-head">
        <button className="table-summary" onClick={() => setOpen(!open)} aria-expanded={open}>
          <span className="chevron">{open ? "▾" : "▸"}</span>
          <span className="table-name">{table.table_name}</span>
          <span className="muted">
            {formatCount(table.row_count)} × {table.column_count}
          </span>
          {table.warnings.length > 0 && (
            <span className="warning-badge" title={table.warnings.join("\n")}>
              {table.warnings.length}
            </span>
          )}
        </button>
        {onView && (
          <button className="view-rows-button" onClick={() => onView(table.table_name)} title={`View rows of ${table.table_name}`}>
            View
          </button>
        )}
      </div>
      {open && (
        <div className="table-body">
          {table.description && <p className="small">{table.description}</p>}
          <p className="muted small">
            {table.grain ? `Grain: ${table.grain}` : table.entity} · {table.file_name} · {formatBytes(table.file_size_bytes)}
          </p>
          <p className="muted small">
            Key:{" "}
            {table.primary_key.length ? (
              <code>{table.primary_key.join(" + ")}</code>
            ) : (
              <span className="warn-text">none</span>
            )}{" "}
            ({KEY_SOURCE_LABEL[table.primary_key_source]})
          </p>
          {table.warnings.length > 0 && (
            <ul className="warning-list">
              {table.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <ul className="column-list">
            {table.columns.map((c) => (
              <ColumnRow key={c.name} column={c} />
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function RelationshipRow({ rel }: { rel: Relationship }) {
  return (
    <li title={rel.reason}>
      <code>
        {rel.from_table}.{rel.from_column}
      </code>
      <span className="arrow">→</span>
      <code>
        {rel.to_table}.{rel.to_column}
      </code>
      {rel.status === "needs_review" && <span className="status-chip review">needs review</span>}
      {rel.source === "llm_proposed" && <span className="status-chip ai">AI-found</span>}
      <span className="muted small">
        {rel.cardinality} · {(rel.match_rate * 100).toFixed(1)}% match
        {rel.orphan_count > 0 && <span className="warn-text"> · {formatCount(rel.orphan_count)} orphans</span>}
      </span>
      {rel.join_warning && <span className="warn-text small">{rel.join_warning}</span>}
    </li>
  );
}

function SemanticSection({ model }: { model: SemanticModel }) {
  const metrics = model.metrics.filter((m) => m.valid);
  return (
    <section>
      <h3>Business metrics ({metrics.length})</h3>
      {model.dataset_summary && <p className="small">{model.dataset_summary}</p>}
      <ul className="metric-list">
        {metrics.map((m) => (
          <li key={`${m.table}.${m.name}`} title={m.description}>
            <strong>{m.name}</strong>
            <code className="small">{m.expression}</code>
          </li>
        ))}
      </ul>
      {model.caveats.length > 0 && (
        <details className="analysis-notes">
          <summary>
            Notes for analysis (AI) · {model.caveats.length}
          </summary>
          <p className="small">
            Pitfalls the AI found in this data, such as joins that double-count or which date column to use. The chat
            agent uses these when writing queries.
          </p>
          <ul className="note-list">
            {model.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function AnalysisNotice({ analysis }: { analysis: AnalysisInfo }) {
  if (!analysis.llm_enabled) {
    return <p className="analysis-notice">Rule-based profiling only (AI analysis is disabled).</p>;
  }
  return (
    <p className={`analysis-notice ${analysis.degraded ? "degraded" : ""}`} title={analysis.notes.join("\n")}>
      AI-assisted profiling · {analysis.model} · {analysis.llm_calls} calls
      {analysis.degraded && " · some steps fell back to rules"}
    </p>
  );
}

interface DatasetPanelProps {
  profile: DatasetProfile;
  /** Opens the row viewer; omitted while the data is not browsable yet (cleaning decision pending). */
  onViewTable?: (table: string) => void;
}

export default function DatasetPanel({ profile, onViewTable }: DatasetPanelProps) {
  const accepted = profile.relationships.filter((r) => r.status === "accepted");
  const review = profile.relationships.filter((r) => r.status === "needs_review");
  return (
    <aside className="dataset-panel">
      <AnalysisNotice analysis={profile.analysis} />

      <section>
        <h3>Tables ({profile.table_count})</h3>
        {!onViewTable && <p className="muted small">Choose a cleaning option to browse table rows.</p>}
        <ul className="table-list">
          {profile.tables.map((t) => (
            <TableCard key={t.table_name} table={t} onView={onViewTable} />
          ))}
        </ul>
      </section>

      <section>
        <h3>Relationships ({accepted.length})</h3>
        {profile.relationships.length === 0 ? (
          <p className="muted small">No relationships detected.</p>
        ) : (
          <ul className="relationship-list">
            {[...accepted, ...review].map((r) => (
              <RelationshipRow key={`${r.from_table}.${r.from_column}->${r.to_table}.${r.to_column}`} rel={r} />
            ))}
          </ul>
        )}
      </section>

      {profile.semantic_model && <SemanticSection model={profile.semantic_model} />}

      {profile.failed_files.length > 0 && (
        <section>
          <h3>Failed files</h3>
          <ul className="warning-list">
            {profile.failed_files.map((f) => (
              <li key={f.file_name}>
                <strong>{f.file_name}</strong>: {f.error}
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}
