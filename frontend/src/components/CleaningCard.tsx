import { useState } from "react";
import { formatCount } from "../format";
import type { DataIssue } from "../types";

interface Props {
  issues: DataIssue[];
  busy: boolean;
  error: string | null;
  onDecide: (issueIds: string[]) => void;
}

function IssueLine({ issue }: { issue: DataIssue }) {
  return (
    <>
      <span className="issue-title">
        <code>{issue.column ? `${issue.table}.${issue.column}` : issue.table}</code> {issue.description}
      </span>
      {issue.fix && <span className="muted small">Fix: {issue.fix}</span>}
      {issue.examples.length > 0 && (
        <span className="muted small">e.g. {issue.examples.slice(0, 4).join(", ")}</span>
      )}
    </>
  );
}

/** Asks the user which detected data-quality fixes to apply before chatting (C0 cleaning gate). */
export default function CleaningCard({ issues, busy, error, onDecide }: Props) {
  const fixable = issues.filter((i) => i.fix);
  const informational = issues.filter((i) => !i.fix);
  const [selected, setSelected] = useState<Set<string>>(() => new Set(fixable.map((i) => i.id)));

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="cleaning-card">
      <p>
        <strong>I found {fixable.length} data-quality issue{fixable.length === 1 ? "" : "s"} I can fix.</strong>{" "}
        Cleaning first makes answers more reliable. The original data is kept, so nothing is lost.
      </p>
      <ul className="issue-list">
        {fixable.map((issue) => (
          <li key={issue.id}>
            <label>
              <input type="checkbox" checked={selected.has(issue.id)} onChange={() => toggle(issue.id)} disabled={busy} />
              <span className="issue-text">
                <IssueLine issue={issue} />
              </span>
              <span className="muted small issue-count">{formatCount(issue.affected_rows)} rows</span>
            </label>
          </li>
        ))}
      </ul>
      {informational.length > 0 && (
        <details className="analysis-notes">
          <summary>{informational.length} more note{informational.length === 1 ? "" : "s"} (no fix needed)</summary>
          <ul className="note-list">
            {informational.map((issue) => (
              <li key={issue.id}>
                <IssueLine issue={issue} />
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <p className="warn-text small">{error}</p>}
      <div className="actions">
        <button className="primary-button" disabled={busy || selected.size === 0} onClick={() => onDecide([...selected])}>
          {busy ? "Applying…" : `Apply ${selected.size} fix${selected.size === 1 ? "" : "es"}`}
        </button>
        <button className="secondary-button" disabled={busy} onClick={() => onDecide([])}>
          Use data as-is
        </button>
      </div>
    </div>
  );
}
