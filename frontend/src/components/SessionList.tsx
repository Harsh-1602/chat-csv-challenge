import { useEffect, useState } from "react";
import { listSessions } from "../api";
import { formatCount } from "../format";
import type { SessionSummary } from "../types";

interface Props {
  activeId?: string;
  onOpen: (sessionId: string) => void;
  /** Starts a fresh conversation on a ready session's dataset (no re-upload or re-profiling). */
  onNewChat?: (sessionId: string) => Promise<void>;
  emptyText?: string;
}

const STATUS_TEXT: Record<SessionSummary["status"], string> = {
  queued: "Queued",
  profiling: "Profiling…",
  completed: "Ready",
  failed: "Failed",
};

function relativeTime(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  const units: [number, string][] = [[86400, "day"], [3600, "hour"], [60, "minute"]];
  for (const [size, unit] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size);
      return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
    }
  }
  return "";
}

function detail(s: SessionSummary): string {
  if (s.status !== "completed") return STATUS_TEXT[s.status];
  const parts = [`${s.table_count} table${s.table_count === 1 ? "" : "s"}`, `${formatCount(s.total_rows)} rows`];
  parts.push(`${s.question_count} question${s.question_count === 1 ? "" : "s"}`);
  if (s.cleaning === "pending") parts.push("cleaning pending");
  return parts.join(" · ");
}

/** Lists stored sessions (newest activity first) and lets the user reopen one or start a new chat on its data. */
export default function SessionList({ activeId, onOpen, onNewChat, emptyText = "No previous sessions yet." }: Props) {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copying, setCopying] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listSessions(controller.signal)
      .then(setSessions)
      .catch((e: Error) => !controller.signal.aborted && setError(e.message));
    return () => controller.abort();
  }, []);

  const newChat = async (sessionId: string) => {
    if (!onNewChat || copying) return;
    setCopying(sessionId);
    setError(null);
    try {
      await onNewChat(sessionId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start a new chat.");
    } finally {
      setCopying(null);
    }
  };

  if (sessions === null) return <p className="muted small">{error ?? "Loading sessions…"}</p>;
  if (sessions.length === 0) return <p className="muted small">{emptyText}</p>;

  return (
    <>
      {error && <p className="muted small">{error}</p>}
      <ul className="session-list">
        {sessions.map((s) => (
          <li key={s.session_id} className="session-row">
            <button
              className={`session-item ${s.session_id === activeId ? "active" : ""}`}
              onClick={() => onOpen(s.session_id)}
              aria-current={s.session_id === activeId ? "true" : undefined}
              title={s.file_names.join(", ")}
            >
              <span className="session-title">{s.title || s.file_names.join(", ")}</span>
              <span className="session-meta">
                <span className={`session-status ${s.status}`}>{detail(s)}</span>
                <span>{relativeTime(s.updated_at)}</span>
              </span>
            </button>
            {onNewChat && s.status === "completed" && (
              <button
                className="secondary-button compact session-new-chat"
                onClick={() => newChat(s.session_id)}
                disabled={copying !== null}
                title={`New chat on the same data: ${s.file_names.join(", ")}`}
              >
                {copying === s.session_id ? "Starting…" : "New chat"}
              </button>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}
