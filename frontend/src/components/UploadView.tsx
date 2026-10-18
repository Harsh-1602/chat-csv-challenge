import { useRef, useState, type DragEvent } from "react";
import { formatBytes } from "../format";
import SessionList from "./SessionList";

interface Props {
  files: File[];
  error?: string | null;
  onFilesChange: (files: File[]) => void;
  onSubmit: () => void;
  onOpenSession: (sessionId: string) => void;
  onNewChat: (sessionId: string) => Promise<void>;
}

const isCsv = (file: File) => file.name.toLowerCase().endsWith(".csv");
const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`;

export default function UploadView({ files, error, onFilesChange, onSubmit, onOpenSession, onNewChat }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const list = Array.from(incoming);
    setRejected(list.filter((f) => !isCsv(f)).map((f) => f.name));
    const known = new Set(files.map(fileKey));
    onFilesChange([...files, ...list.filter((f) => isCsv(f) && !known.has(fileKey(f)))]);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  };

  const totalSize = files.reduce((sum, f) => sum + f.size, 0);

  return (
    <main className="upload-screen">
      <header className="hero">
        <h1>CSV Chat Analyst</h1>
        <p>Upload one or more related CSV files. We'll profile them, find how they connect, and then you can ask questions in chat.</p>
      </header>

      <section
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
        aria-label="Choose CSV files"
      >
        <div className="dropzone-icon" aria-hidden>⬆</div>
        <strong>Drag & drop CSV files here</strong>
        <span>or click to browse</span>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          multiple
          hidden
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </section>

      {error && <p className="notice error">Could not open that session: {error}</p>}
      {rejected.length > 0 && <p className="notice error">Skipped non-CSV files: {rejected.join(", ")}</p>}

      {files.length > 0 && (
        <section className="card file-list">
          <div className="file-list-header">
            <span>
              {files.length} file{files.length === 1 ? "" : "s"} · {formatBytes(totalSize)}
            </span>
            <button className="link-button" onClick={() => onFilesChange([])}>
              Clear all
            </button>
          </div>
          <ul>
            {files.map((file) => (
              <li key={fileKey(file)}>
                <span className="file-name">{file.name}</span>
                <span className="muted">{formatBytes(file.size)}</span>
                <button
                  className="icon-button"
                  aria-label={`Remove ${file.name}`}
                  onClick={() => onFilesChange(files.filter((f) => f !== file))}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <button className="primary-button" disabled={files.length === 0} onClick={onSubmit}>
        Analyze {files.length > 0 ? `${files.length} file${files.length === 1 ? "" : "s"}` : "files"}
      </button>

      <section className="card recent-sessions">
        <h2>Recent sessions</h2>
        <SessionList onOpen={onOpenSession} onNewChat={onNewChat} emptyText="Sessions you start will appear here." />
      </section>
    </main>
  );
}
