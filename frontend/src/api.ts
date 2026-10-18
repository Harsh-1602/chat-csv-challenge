import type { ChatMessage, ChatStreamEvent, SessionCreated, SessionStatus, SessionSummary, TablePage } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return fallback;
}

export function uploadFiles(
  files: File[],
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<SessionCreated> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));

    // XHR is used because fetch does not expose upload progress.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/api/sessions`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status === 202) resolve(xhr.response as SessionCreated);
      else reject(new ApiError(errorMessage(xhr.response, `Upload failed (HTTP ${xhr.status}).`), xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Could not reach the server. Is the backend running?"));
    xhr.onabort = () => reject(new ApiError("Upload cancelled."));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}

export async function getSession(statusUrl: string, signal?: AbortSignal): Promise<SessionStatus> {
  const response = await fetch(`${API_BASE}${statusUrl}`, { signal });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(errorMessage(body, `Status check failed (HTTP ${response.status}).`), response.status);
  return body as SessionStatus;
}

async function json<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(errorMessage(body, `${fallback} (HTTP ${response.status}).`), response.status);
  return body as T;
}

export async function decideCleaning(sessionId: string, issueIds: string[]): Promise<SessionStatus> {
  const response = await fetch(`${API_BASE}/api/sessions/${sessionId}/cleaning`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ issue_ids: issueIds }),
  });
  return json<SessionStatus>(response, "Could not apply the cleaning choice");
}

export function sessionStatusUrl(sessionId: string): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}`;
}

/** Starts a new session on the same (already profiled and cleaned) dataset, with an empty conversation. */
export async function copySession(sessionId: string): Promise<SessionCreated> {
  const response = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/copy`, { method: "POST" });
  return json<SessionCreated>(response, "Could not start a new chat on this data");
}

export async function listSessions(signal?: AbortSignal): Promise<SessionSummary[]> {
  return json<SessionSummary[]>(await fetch(`${API_BASE}/api/sessions`, { signal }), "Could not load sessions");
}

export async function getMessages(sessionId: string): Promise<ChatMessage[]> {
  return json<ChatMessage[]>(await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`), "Could not load messages");
}

export interface TablePageQuery {
  offset: number;
  limit: number;
  sort?: string | null;
  descending?: boolean;
}

export async function getTableRows(
  sessionId: string,
  table: string,
  query: TablePageQuery,
  signal?: AbortSignal,
): Promise<TablePage> {
  const params = new URLSearchParams({ offset: String(query.offset), limit: String(query.limit) });
  if (query.sort) {
    params.set("sort", query.sort);
    params.set("desc", String(Boolean(query.descending)));
  }
  const url = `${API_BASE}/api/sessions/${sessionId}/tables/${encodeURIComponent(table)}/rows?${params}`;
  return json<TablePage>(await fetch(url, { signal }), "Could not load table rows");
}

export function downloadUrl(sessionId: string, messageId: string): string {
  return `${API_BASE}/api/sessions/${sessionId}/messages/${messageId}/download.csv`;
}

function parseEvent(block: string): ChatStreamEvent | null {
  let type = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) type = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return null;
  return { type, data: JSON.parse(data.join("\n")) } as ChatStreamEvent;
}

/** POSTs a question and invokes `onEvent` for each server-sent event until the stream ends. */
export async function askQuestion(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok || !response.body) {
    await json(response, "Could not send the question");
    throw new ApiError("The server returned an empty response.");
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const event = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
      if (event) onEvent(event);
    }
  }
  const tail = parseEvent(buffer.trim());
  if (tail) onEvent(tail);
}
