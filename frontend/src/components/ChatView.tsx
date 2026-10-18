import { useCallback, useEffect, useRef, useState } from "react";
import { askQuestion, decideCleaning, getMessages } from "../api";
import { formatCount } from "../format";
import type { AgentStepInfo, ChatMessage, DatasetProfile, SessionStatus, StageEvent } from "../types";
import { AssistantMessage, PendingMessage } from "./AssistantMessage";
import CleaningCard from "./CleaningCard";
import DatasetPanel from "./DatasetPanel";
import SessionList from "./SessionList";
import TableViewer from "./TableViewer";

function summarize(profile: DatasetProfile): string[] {
  const lines: string[] = [];
  if (profile.semantic_model?.dataset_summary) lines.push(profile.semantic_model.dataset_summary);
  lines.push(
    `I loaded ${profile.table_count} table${profile.table_count === 1 ? "" : "s"} with ${formatCount(profile.total_rows)} rows in total.`,
  );
  const accepted = profile.relationships.filter((r) => r.status === "accepted").length;
  const review = profile.relationships.length - accepted;
  if (accepted) {
    lines.push(
      `I verified ${accepted} relationship${accepted === 1 ? "" : "s"} between tables, so questions can combine data across files.`,
    );
  }
  if (review) {
    lines.push(`${review} possible link${review === 1 ? " is" : "s are"} ambiguous and marked “needs review”.`);
  }
  const warningCount = profile.tables.reduce((sum, t) => sum + t.warnings.length, 0);
  lines.push(
    warningCount
      ? `There ${warningCount === 1 ? "is" : "are"} ${warningCount} data-quality note${warningCount === 1 ? "" : "s"} — expand a table on the left to review them.`
      : "No missing values or duplicate rows were found.",
  );
  if (profile.failed_files.length) {
    lines.push(`${profile.failed_files.length} file(s) could not be read and were skipped.`);
  }
  return lines;
}

const MAX_QUESTION = 2000;
// Mirrors the backend rule: a session is titled after its first question.
const TITLE_MAX = 80;

interface ChatViewProps {
  session: SessionStatus;
  onNewUpload: () => void;
  onOpenSession: (sessionId: string) => void;
  onNewChat: (sessionId: string) => Promise<void>;
}

export default function ChatView({ session: initial, onNewUpload, onOpenSession, onNewChat }: ChatViewProps) {
  const [session, setSession] = useState(initial);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<{ stage: StageEvent | null; steps: AgentStepInfo[] } | null>(null);
  const [cleaningBusy, setCleaningBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSessions, setShowSessions] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const closeViewer = useCallback(() => setViewing(null), []);
  const bottom = useRef<HTMLDivElement>(null);
  const profile = session.profile!;
  const sessionId = session.session_id;
  const cleaningPending = session.cleaning === "pending";

  useEffect(() => {
    setViewing(null);
    getMessages(sessionId).then(setMessages).catch(() => undefined);
  }, [sessionId]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  const decide = async (issueIds: string[]) => {
    setCleaningBusy(true);
    setError(null);
    try {
      setSession(await decideCleaning(sessionId, issueIds));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cleaning failed.");
    } finally {
      setCleaningBusy(false);
    }
  };

  const newChat = async () => {
    setCopying(true);
    setError(null);
    try {
      await onNewChat(sessionId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start a new chat.");
      setCopying(false);
    }
  };

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || pending) return;
    const local: ChatMessage = {
      id: `local-${Date.now()}`, role: "user", content: q, created_at: new Date().toISOString(), status: null,
      standalone_question: null, sql: null, view: null, table: null, chart: null, assumptions: [], warnings: [],
      data_quality: [], notes: [], steps: [], elapsed_s: null, llm_calls: null,
    };
    setMessages((m) => [...m, local]);
    if (!messages.some((m) => m.role === "user")) {
      setSession((s) => ({ ...s, title: q.length <= TITLE_MAX ? q : `${q.slice(0, TITLE_MAX - 1)}…` }));
    }
    setQuestion("");
    setError(null);
    setPending({ stage: null, steps: [] });
    let answered = false;
    try {
      await askQuestion(sessionId, q, (event) => {
        if (event.type === "stage") setPending((p) => p && { ...p, stage: event.data });
        else if (event.type === "step") setPending((p) => p && { ...p, steps: [...p.steps, event.data] });
        else if (event.type === "answer") {
          answered = true;
          setMessages((m) => [...m, event.data]);
        } else if (event.type === "error") setError(event.data.message);
      });
      if (!answered) setError((e) => e ?? "The answer stream ended unexpectedly. Please try again.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not get an answer.");
    } finally {
      setPending(null);
    }
  };

  const examples = profile.semantic_model?.example_questions ?? [];
  const disabledReason = !session.chat_available
    ? "Chat needs an LLM — set OPENAI_API_KEY on the server."
    : cleaningPending
      ? "Choose how to handle the data-quality issues above first."
      : null;
  const canAsk = !disabledReason && !pending;

  return (
    <div className="chat-layout">
      <header className="topbar">
        <strong>CSV Chat Analyst</strong>
        <span className="topbar-title" title={session.title}>
          {session.title}
        </span>
        <span className="muted small">
          {profile.table_count} tables · {formatCount(profile.total_rows)} rows
        </span>
        <button
          className="secondary-button"
          aria-expanded={showSessions}
          onClick={() => setShowSessions((open) => !open)}
        >
          Sessions
        </button>
        <button
          className="secondary-button"
          onClick={newChat}
          disabled={copying || cleaningBusy}
          title="Start a fresh conversation on these same tables — no re-upload or re-analysis"
        >
          {copying ? "Starting…" : "New chat"}
        </button>
        <button className="secondary-button" onClick={onNewUpload}>
          New upload
        </button>
      </header>

      {showSessions && (
        <div className="sessions-overlay" onClick={() => setShowSessions(false)}>
          <aside className="sessions-drawer" aria-label="Previous sessions" onClick={(e) => e.stopPropagation()}>
            <div className="sessions-drawer-header">
              <h2>Sessions</h2>
              <button className="icon-button" aria-label="Close sessions" onClick={() => setShowSessions(false)}>
                ✕
              </button>
            </div>
            <SessionList
              activeId={sessionId}
              onOpen={(id) => {
                setShowSessions(false);
                if (id !== sessionId) onOpenSession(id);
              }}
              onNewChat={onNewChat}
            />
          </aside>
        </div>
      )}

      <DatasetPanel profile={profile} onViewTable={cleaningPending ? undefined : setViewing} />
      {viewing && <TableViewer sessionId={sessionId} table={viewing} onClose={closeViewer} />}

      <main className="chat-main">
        <div className="messages">
          <div className="message assistant">
            <div className="avatar" aria-hidden>
              AI
            </div>
            <div className="bubble">
              <p>
                <strong>Your data is ready.</strong>
              </p>
              {summarize(profile).map((line) => (
                <p key={line}>{line}</p>
              ))}
              {cleaningPending && (
                <CleaningCard issues={session.issues} busy={cleaningBusy} error={error} onDecide={decide} />
              )}
              {session.cleaning === "applied" && (
                <p className="success-text">
                  Applied {session.applied_fixes.length} fix{session.applied_fixes.length === 1 ? "" : "es"}. The original
                  data is kept alongside the cleaned tables.
                </p>
              )}
              {session.cleaning === "skipped" && <p className="muted">Using the data as uploaded.</p>}
              {!cleaningPending && examples.length > 0 && messages.length === 0 && (
                <>
                  <p>Try asking:</p>
                  <div className="example-chips">
                    {examples.map((q) => (
                      <button key={q} className="chip" disabled={!canAsk} onClick={() => send(q)}>
                        {q}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="message user">
                <div className="bubble user-bubble">{m.content}</div>
              </div>
            ) : (
              <div key={m.id} className="message assistant">
                <div className="avatar" aria-hidden>
                  AI
                </div>
                <AssistantMessage message={m} sessionId={sessionId} />
              </div>
            ),
          )}

          {pending && (
            <div className="message assistant">
              <div className="avatar" aria-hidden>
                AI
              </div>
              <PendingMessage stage={pending.stage} steps={pending.steps} />
            </div>
          )}
          {error && !cleaningPending && <div className="notice error chat-error"><p>{error}</p></div>}
          <div ref={bottom} />
        </div>

        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            send(question);
          }}
        >
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={MAX_QUESTION}
            placeholder={disabledReason ?? "Ask a question about your data…"}
            disabled={!!disabledReason}
            aria-label="Ask a question"
          />
          <button className="primary-button" type="submit" disabled={!canAsk || !question.trim()}>
            {pending ? "Working…" : "Send"}
          </button>
        </form>
      </main>
    </div>
  );
}
