import { useEffect, useState } from "react";
import { getTableRows } from "../api";
import { formatCount, formatValue } from "../format";
import type { TablePage } from "../types";

const PAGE_SIZES = [25, 50, 100, 200];

interface Props {
  sessionId: string;
  table: string;
  onClose: () => void;
}

interface Sort {
  column: string;
  descending: boolean;
}

export default function TableViewer({ sessionId, table, onClose }: Props) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [sort, setSort] = useState<Sort | null>(null);
  const [page, setPage] = useState<TablePage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getTableRows(sessionId, table, { offset, limit, sort: sort?.column, descending: sort?.descending }, controller.signal)
      .then(setPage)
      .catch((e: Error) => {
        if (e.name !== "AbortError") setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [sessionId, table, offset, limit, sort]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const toggleSort = (column: string) => {
    setOffset(0);
    setSort((current) => {
      if (current?.column !== column) return { column, descending: false };
      return current.descending ? null : { column, descending: true };
    });
  };

  const total = page?.total_rows ?? 0;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);

  return (
    <div className="viewer-backdrop" onClick={onClose}>
      <div
        className="viewer"
        role="dialog"
        aria-modal="true"
        aria-label={`Rows of ${table}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="viewer-header">
          <div>
            <h2>{table}</h2>
            <p className="muted small">
              {page ? `${formatCount(total)} rows × ${page.columns.length} columns` : "Loading…"}
              {sort && ` · sorted by ${sort.column} ${sort.descending ? "↓" : "↑"}`}
            </p>
          </div>
          <button className="icon-button viewer-close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        {error ? (
          <div className="notice error">
            <p>{error}</p>
          </div>
        ) : (
          <div className={`viewer-grid ${loading ? "loading" : ""}`}>
            {page && (
              <table className="result-table">
                <thead>
                  <tr>
                    <th className="row-number">#</th>
                    {page.columns.map((c) => (
                      <th key={c.name} aria-sort={sort?.column === c.name ? (sort.descending ? "descending" : "ascending") : "none"}>
                        <button className="sort-button" onClick={() => toggleSort(c.name)} title="Sort by this column">
                          <span>{c.name}</span>
                          <span className="sort-indicator">
                            {sort?.column === c.name ? (sort.descending ? "↓" : "↑") : "↕"}
                          </span>
                        </button>
                        <span className="column-type">
                          <span className={`role-badge ${c.role}`}>{c.role}</span> {c.data_type}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {page.rows.map((row, r) => (
                    <tr key={offset + r}>
                      <td className="row-number">{formatCount(offset + r + 1)}</td>
                      {row.map((value, i) => (
                        <td
                          key={i}
                          className={value === null ? "null" : typeof value === "number" ? "num" : undefined}
                        >
                          {formatValue(value)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        <footer className="viewer-footer">
          <label className="small">
            Rows per page{" "}
            <select
              value={limit}
              onChange={(event) => {
                setLimit(Number(event.target.value));
                setOffset(0);
              }}
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <span className="muted small">
            {formatCount(first)}–{formatCount(last)} of {formatCount(total)}
          </span>
          <div className="viewer-pager">
            <button className="secondary-button compact" disabled={loading || offset === 0} onClick={() => setOffset(0)}>
              « First
            </button>
            <button className="secondary-button compact" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>
              ‹ Prev
            </button>
            <button className="secondary-button compact" disabled={loading || last >= total} onClick={() => setOffset(offset + limit)}>
              Next ›
            </button>
            <button
              className="secondary-button compact"
              disabled={loading || last >= total}
              onClick={() => setOffset(Math.max(0, Math.floor((total - 1) / limit) * limit))}
            >
              Last »
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
