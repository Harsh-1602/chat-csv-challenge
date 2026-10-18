import { useEffect, useState } from "react";
import { getSession, sessionStatusUrl, uploadFiles } from "../api";
import { formatBytes } from "../format";
import type { FileState, SessionStatus } from "../types";

interface Props {
  /** Files to upload; omit together with `sessionId` to watch an existing session instead. */
  files?: File[];
  sessionId?: string;
  onCreated?: (sessionId: string) => void;
  onComplete: (session: SessionStatus) => void;
  onBack: () => void;
}

const UPLOAD_WEIGHT = 0.2;
const POLL_MS = 500;
const COMPLETE_PAUSE_MS = 600;

const STATUS_LABEL: Record<FileState | "uploading", string> = {
  uploading: "Uploading",
  pending: "Queued",
  loading: "Loading",
  profiling: "Profiling",
  completed: "Done",
  failed: "Failed",
};

const NO_FILES: File[] = [];

export default function ProcessingView({ files = NO_FILES, sessionId, onCreated, onComplete, onBack }: Props) {
  const [uploadFraction, setUploadFraction] = useState(sessionId ? 1 : 0);
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const watching = Boolean(sessionId);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    setUploadFraction(watching ? 1 : 0);
    setSession(null);
    setError(null);

    const poll = async (statusUrl: string) => {
      try {
        const status = await getSession(statusUrl, controller.signal);
        setSession(status);
        if (status.status === "completed") {
          timer = window.setTimeout(() => onComplete(status), COMPLETE_PAUSE_MS);
        } else if (status.status === "failed") {
          setError(status.error ?? "Profiling failed.");
        } else {
          timer = window.setTimeout(() => poll(statusUrl), POLL_MS);
        }
      } catch (err) {
        if (!controller.signal.aborted) setError((err as Error).message);
      }
    };

    // Starting on the next tick lets StrictMode's throwaway mount cancel before any request is sent.
    const startTimer = window.setTimeout(() => {
      if (sessionId) {
        poll(sessionStatusUrl(sessionId));
        return;
      }
      uploadFiles(files, setUploadFraction, controller.signal)
        .then((created) => {
          setUploadFraction(1);
          onCreated?.(created.session_id);
          return poll(created.status_url);
        })
        .catch((err: Error) => {
          if (!controller.signal.aborted) setError(err.message);
        });
    }, 0);

    return () => {
      controller.abort();
      window.clearTimeout(startTimer);
      window.clearTimeout(timer);
    };
    // Callbacks are stable for the lifetime of this screen; re-run only on new input or retry.
  }, [files, sessionId, attempt]);

  const uploading = session === null;
  const progress = uploading ? uploadFraction * UPLOAD_WEIGHT : UPLOAD_WEIGHT + session.progress * (1 - UPLOAD_WEIGHT);
  const percent = Math.round(progress * 100);
  const stage = error
    ? "Something went wrong"
    : uploading
      ? watching
        ? "Loading session…"
        : `Uploading ${files.length} file${files.length === 1 ? "" : "s"}…`
      : session.status === "completed"
        ? "Analysis complete — opening chat…"
        : session.stage;

  const rows = session
    ? session.files.map((f) => ({ name: f.file_name, size: f.size_bytes, status: f.status as FileState | "uploading", error: f.error }))
    : files.map((f) => ({ name: f.name, size: f.size, status: "uploading" as const, error: null }));

  return (
    <main className="processing-screen">
      <section className="card processing-card">
        <h2>Preparing your data</h2>
        <p className="muted">
          We load each CSV, profile every column, let AI interpret the tables and their links, and verify every
          finding against the data.
        </p>

        <div className="progress-header">
          <span className="stage" aria-live="polite">
            {stage}
          </span>
          <span className="percent">{percent}%</span>
        </div>
        <div
          className={`progress-track ${error ? "has-error" : ""}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div className={`progress-fill ${!error && percent < 100 ? "animated" : ""}`} style={{ width: `${percent}%` }} />
        </div>

        <ul className="file-status-list">
          {rows.map((row, i) => (
            <li key={`${row.name}-${i}`}>
              <span className={`status-dot ${row.status}`} aria-hidden />
              <span className="file-name">{row.name}</span>
              <span className="muted">{formatBytes(row.size)}</span>
              <span className={`status-label ${row.status}`}>{STATUS_LABEL[row.status]}</span>
              {row.error && <span className="file-error">{row.error}</span>}
            </li>
          ))}
        </ul>

        {error && (
          <div className="notice error">
            <p>{error}</p>
            <div className="actions">
              <button className="secondary-button" onClick={onBack}>
                {watching ? "Back to start" : "Back to files"}
              </button>
              {(!watching || session?.status !== "failed") && (
                <button className="primary-button" onClick={() => setAttempt((n) => n + 1)}>
                  Retry
                </button>
              )}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
