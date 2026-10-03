import { useCallback, useEffect, useState } from "react";

import { ChatView } from "./components/ChatView";
import { Mascot } from "./components/Mascot";
import { Sidebar } from "./components/Sidebar";
import { SignIn } from "./components/SignIn";
import { Tools } from "./components/Tools";
import { api, onSignedOut, post, SignedOut } from "./lib/api";
import { takeFragment } from "./lib/session";
import type { Chat, Me, Models } from "./lib/types";

/** The chat an address names: `/chats/<id>`, or none. */
export function chatFromPath(path: string): string | null {
  const match = /^\/chats\/([A-Za-z0-9_-]+)\/?$/.exec(path);
  return match ? match[1]! : null;
}

type Phase =
  | { kind: "loading" }
  | { kind: "signed-out"; message: string | null; unavailable: string | null }
  | { kind: "ready"; me: Me };

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [chatId, setChatId] = useState<string | null>(() => chatFromPath(window.location.pathname));
  const [chats, setChats] = useState<Chat[]>([]);
  const [models, setModels] = useState<Models | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [showChats, setShowChats] = useState(false);

  const open = useCallback((id: string | null) => {
    const path = id ? `/chats/${id}` : "/";
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    setChatId(id);
    setShowTools(false);
    setShowChats(false);
  }, []);

  useEffect(() => {
    const back = () => setChatId(chatFromPath(window.location.pathname));
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, []);

  useEffect(() => {
    const fragment = takeFragment();
    let cancelled = false;
    const stop = onSignedOut((error) => {
      setPhase({
        kind: "signed-out",
        message: error.reason === "none" ? null : error.message,
        unavailable: null,
      });
    });
    (async () => {
      try {
        const me = await api<Me>("/api/me");
        if (!cancelled) setPhase({ kind: "ready", me });
      } catch (error) {
        if (cancelled) return;
        let unavailable: string | null = null;
        try {
          const status = await api<{ signIn: { available: boolean; reason: string | null } }>(
            "/api/status",
          );
          unavailable = status.signIn.available ? null : status.signIn.reason;
        } catch {
          // The status is a courtesy; the sign-in button still works.
        }
        const message =
          fragment.error ??
          (error instanceof SignedOut && error.reason !== "none" ? error.message : null) ??
          (error instanceof SignedOut ? null : String(error));
        setPhase({ kind: "signed-out", message, unavailable });
      }
    })();
    return () => {
      cancelled = true;
      stop();
    };
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats((await api<{ chats: Chat[] }>("/api/chats")).chats);
    } catch {
      // A sign-out is handled by its listener; anything else waits for the next try.
    }
  }, []);

  const refreshModels = useCallback(async () => {
    try {
      setModels(await api<Models>("/api/models"));
      setModelsError(null);
    } catch (error) {
      if (!(error instanceof SignedOut)) {
        setModelsError(error instanceof Error ? error.message : String(error));
      }
    }
  }, []);

  useEffect(() => {
    if (phase.kind !== "ready") return;
    void refreshChats();
    void refreshModels();
    const every = window.setInterval(() => {
      void refreshChats();
      void refreshModels();
    }, 20000);
    return () => window.clearInterval(every);
  }, [phase.kind, refreshChats, refreshModels]);

  const newChat = useCallback(async () => {
    const chat = await post<Chat>("/api/chats", {});
    setChats((current) => [chat, ...current]);
    open(chat.id);
  }, [open]);

  if (phase.kind === "loading") return null;
  if (phase.kind === "signed-out") {
    return <SignIn message={phase.message} unavailable={phase.unavailable} />;
  }

  return (
    <div className="flex h-full">
      <div className={`${showChats ? "flex" : "hidden"} w-full shrink-0 md:flex md:w-auto`}>
        <Sidebar
          me={phase.me}
          chats={chats}
          current={chatId}
          onOpen={open}
          onClose={() => setShowChats(false)}
          onNew={() => void newChat()}
          onSignOut={async () => {
            await post("/api/signout").catch(() => undefined);
            setPhase({ kind: "signed-out", message: "You signed out.", unavailable: null });
          }}
        />
      </div>
      <main className={`${showChats ? "hidden md:flex" : "flex"} min-w-0 flex-1 flex-col`}>
        <div
          className={`flex justify-between border-b border-line px-4 py-2 ${showTools ? "md:hidden" : ""}`}
        >
          <button
            onClick={() => setShowChats(true)}
            className="text-sm text-accent md:hidden"
            aria-controls="chat-navigation"
          >
            Chats
          </button>
          {!showTools && (
            <button onClick={() => setShowTools(true)} className="text-sm text-accent">
              Toolbox · Tools
            </button>
          )}
        </div>
        {showTools ? (
          <Tools owner={phase.me.owner} onClose={() => setShowTools(false)} />
        ) : chatId ? (
          <ChatView
            key={chatId}
            chatId={chatId}
            me={phase.me}
            models={models}
            modelsError={modelsError}
            onChanged={refreshChats}
            onDeleted={() => {
              open(null);
              void refreshChats();
            }}
          />
        ) : (
          <Empty
            hasChats={chats.length > 0}
            modelsError={modelsError}
            noModels={models !== null && models.models.length === 0}
            onNew={() => void newChat()}
          />
        )}
      </main>
    </div>
  );
}

function Empty({
  hasChats,
  modelsError,
  noModels,
  onNew,
}: {
  hasChats: boolean;
  modelsError: string | null;
  noModels: boolean;
  onNew: () => void;
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <Mascot pose={modelsError ? "curious" : "guide"} />
        <h2 className="text-xl font-semibold">
          {hasChats ? "Pick a chat, or start one" : "Start your first chat"}
        </h2>
        {modelsError ? (
          <p role="alert" className="text-error">
            {modelsError}
          </p>
        ) : noModels ? (
          <p className="text-muted">
            Eugene is not serving any model you can chat with yet. The owner can start one in
            Eugene&apos;s console.
          </p>
        ) : (
          <p className="text-muted">Ask anything. Answers keep going even if you close this tab.</p>
        )}
        <button
          type="button"
          data-testid="new-chat-empty"
          onClick={onNew}
          className="rounded-plexus bg-accent px-4 py-2 font-medium text-on-accent hover:opacity-90"
        >
          New chat
        </button>
      </div>
    </div>
  );
}
