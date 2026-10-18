import { useCallback, useEffect, useState } from "react";
import { copySession, getSession, sessionStatusUrl } from "./api";
import ChatView from "./components/ChatView";
import LandingView from "./components/LandingView";
import ProcessingView from "./components/ProcessingView";
import UploadView from "./components/UploadView";
import type { SessionStatus } from "./types";

type Screen =
  | { name: "landing" }
  | { name: "upload" }
  | { name: "loading"; sessionId: string }
  | { name: "processing"; files?: File[]; sessionId?: string }
  | { name: "chat"; session: SessionStatus };

const SESSION_HASH = /^#\/session\/([\w-]+)$/;

function sessionFromUrl(): string | null {
  return SESSION_HASH.exec(window.location.hash)?.[1] ?? null;
}

/** Keeps the open session in the URL so reloads, bookmarks and back/forward restore it. */
function setUrl(sessionId: string | null, replace = false) {
  const url = sessionId ? `#/session/${sessionId}` : "#/app";
  if (replace) window.history.replaceState(null, "", url);
  else window.history.pushState(null, "", url);
}

function screenFromUrl(): Screen {
  const id = sessionFromUrl();
  if (id) return { name: "loading", sessionId: id };
  return window.location.hash === "#/app" ? { name: "upload" } : { name: "landing" };
}

export default function App() {
  const [screen, setScreen] = useState<Screen>(screenFromUrl);
  const [selected, setSelected] = useState<File[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // Resolves a session id into the right screen: chat when ready, otherwise the progress view.
  useEffect(() => {
    if (screen.name !== "loading") return;
    const controller = new AbortController();
    getSession(sessionStatusUrl(screen.sessionId), controller.signal)
      .then((status) =>
        setScreen(
          status.status === "completed"
            ? { name: "chat", session: status }
            : { name: "processing", sessionId: status.session_id },
        ),
      )
      .catch((e: Error) => {
        if (controller.signal.aborted) return;
        setLoadError(e.message);
        setUrl(null, true);
        setScreen({ name: "upload" });
      });
    return () => controller.abort();
  }, [screen]);

  useEffect(() => {
    const onPop = () => {
      setLoadError(null);
      setScreen(screenFromUrl());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const openSession = useCallback((sessionId: string) => {
    setLoadError(null);
    setUrl(sessionId);
    setScreen({ name: "loading", sessionId });
  }, []);

  const newChatFrom = useCallback(
    async (sessionId: string) => openSession((await copySession(sessionId)).session_id),
    [openSession],
  );

  const goHome = useCallback(() => {
    setSelected([]);
    setUrl(null);
    setScreen({ name: "upload" });
  }, []);

  const backToFiles = useCallback(() => {
    setUrl(null, true);
    setScreen({ name: "upload" });
  }, []);

  return (
    <div className="app">
      {screen.name === "landing" && (
        <LandingView
          onStart={() => {
            setUrl(null);
            setScreen({ name: "upload" });
          }}
        />
      )}
      {screen.name === "upload" && (
        <UploadView
          files={selected}
          error={loadError}
          onFilesChange={setSelected}
          onSubmit={() => {
            setLoadError(null);
            setScreen({ name: "processing", files: selected });
          }}
          onOpenSession={openSession}
          onNewChat={newChatFrom}
        />
      )}
      {screen.name === "loading" && (
        <main className="processing-screen">
          <p className="muted">Loading session…</p>
        </main>
      )}
      {screen.name === "processing" && (
        <ProcessingView
          files={screen.files}
          sessionId={screen.sessionId}
          onCreated={(id) => setUrl(id)}
          onComplete={(session) => setScreen({ name: "chat", session })}
          onBack={screen.sessionId ? goHome : backToFiles}
        />
      )}
      {screen.name === "chat" && (
        <ChatView
          key={screen.session.session_id}
          session={screen.session}
          onNewUpload={goHome}
          onOpenSession={openSession}
          onNewChat={newChatFrom}
        />
      )}
    </div>
  );
}
