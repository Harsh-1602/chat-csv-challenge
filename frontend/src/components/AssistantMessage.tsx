import { lazy, Suspense, useState } from "react";
import { downloadUrl } from "../api";
import { formatCount } from "../format";
import type { AgentStepInfo, ChatMessage, ResultTable, Scalar, StageEvent } from "../types";
const ResultChart = lazy(() => import("./ResultChart"));

const PREVIEW_ROWS = 50;

function cell(value: Scalar): string {
  if (value === null) return "—";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(value);
}

function DataTable({ table }: { table: ResultTable }) {
  const rows = table.rows.slice(0, PREVIEW_ROWS);
  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {table.columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((value, i) => (
                <td key={i} className={typeof value === "number" ? "num" : undefined}>
                  {cell(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {(table.rows.length > PREVIEW_ROWS || table.truncated) && (
        <p className="muted small">
          Showing {rows.length} of {formatCount(table.row_count)}
          {table.truncated ? "+" : ""} rows — download the CSV for everything.
        </p>
      )}
    </div>
  );
}

export function StepList({ steps }: { steps: AgentStepInfo[] }) {
  return (
    <ol className="step-list">
      {steps.map((s) => (
        <li key={s.index} className={`step ${s.outcome}`}>
          <span className="step-outcome">{s.outcome.replace("_", " ")}</span> {s.detail}
          {s.sql && <pre className="sql">{s.sql}</pre>}
        </li>
      ))}
    </ol>
  );
}

function NoteList({ title, items, className }: { title: string; items: string[]; className?: string }) {
  if (!items.length) return null;
  return (
    <div className={`answer-notes ${className ?? ""}`}>
      <span className="small">{title}</span>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

/** A finished assistant answer: narrative, chart/table, SQL, caveats and download. */
export function AssistantMessage({ message, sessionId }: { message: ChatMessage; sessionId: string }) {
  const [view, setView] = useState(message.view);
  const table = message.table;
  const canChart = Boolean(message.chart && table && table.rows.length);

  return (
    <div className={`bubble answer ${message.status ?? ""}`}>
      <p className="answer-text">{message.content}</p>

      {table && table.columns.length > 0 && (
        <div className="answer-result">
          {canChart && (
            <div className="view-toggle" role="group" aria-label="Result view">
              <button className={view === "chart" ? "active" : ""} onClick={() => setView("chart")}>Chart</button>
              <button className={view !== "chart" ? "active" : ""} onClick={() => setView("table")}>Table</button>
            </div>
          )}
          {view === "chart" && canChart ? (
            <Suspense fallback={<p className="muted small">Loading chart…</p>}>
              <ResultChart spec={message.chart!} table={table} />
            </Suspense>
          ) : (
            (view !== "text" || table.rows.length > 1) && <DataTable table={table} />
          )}
        </div>
      )}

      <NoteList title="Assumptions" items={message.assumptions} />
      <NoteList title="Data-quality notes" items={message.data_quality} className="quality" />
      <NoteList title="Warnings" items={[...message.warnings, ...message.notes]} className="warning" />

      <div className="answer-footer">
        {message.sql && (
          <details>
            <summary>Show SQL</summary>
            <pre className="sql">{message.sql}</pre>
          </details>
        )}
        {message.steps.length > 0 && (
          <details>
            <summary>How I got this ({message.steps.length} step{message.steps.length === 1 ? "" : "s"})</summary>
            <StepList steps={message.steps} />
          </details>
        )}
        <span className="muted small answer-meta">
          {message.elapsed_s != null && `${message.elapsed_s.toFixed(1)}s`}
          {message.llm_calls != null && ` · ${message.llm_calls} LLM call${message.llm_calls === 1 ? "" : "s"}`}
        </span>
        {message.sql && table && table.rows.length > 0 && (
          <a className="secondary-button download" href={downloadUrl(sessionId, message.id)} download>
            Download CSV
          </a>
        )}
      </div>
    </div>
  );
}

/** Live progress while an answer is being produced. */
export function PendingMessage({ stage, steps }: { stage: StageEvent | null; steps: AgentStepInfo[] }) {
  return (
    <div className="bubble pending">
      <p className="pending-stage">
        <span className="spinner" aria-hidden />
        {stage?.label ?? "Thinking"}…{stage?.detail ? <span className="muted small"> {stage.detail}</span> : null}
      </p>
      {steps.length > 0 && <StepList steps={steps} />}
    </div>
  );
}
