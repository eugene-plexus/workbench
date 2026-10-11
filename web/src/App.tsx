import { Boxes, Brain, CircleAlert, Files, Info, Keyboard, Laptop, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ChatView } from "./components/ChatView";
import { Documents } from "./components/Documents";
import { JobSites } from "./components/JobSites";
import { Mascot } from "./components/Mascot";
import { Memory } from "./components/Memory";
import { type Handoff, Media } from "./components/Media";
import { Sidebar } from "./components/Sidebar";
import { SignIn } from "./components/SignIn";
import { Tools } from "./components/Tools";
import { api, onSignedOut, patch, post, SignedOut } from "./lib/api";
import { type Door, mediaFromPath, mediaPath } from "./lib/media";
import { saveDraft } from "./lib/conveniences";
import { forget, takeFragment } from "./lib/session";
import type { Chat, Me, Models } from "./lib/types";
import { modeChanged } from "./lib/words";

/** The chat an address names: `/chats/<id>`, or none. */
export function chatFromPath(path: string): string | null {
  const match = /^\/chats\/([A-Za-z0-9_-]+)\/?$/.exec(path);
  return match ? match[1]! : null;
}

/** Whether an address is the Memory page: `/memory`. */
export function memoryFromPath(path: string): boolean {
  return /^\/memory\/?$/.test(path);
}

/** Whether an address is the Documents screen: `/documents`. */
export function documentsFromPath(path: string): boolean {
  return /^\/documents\/?$/.test(path);
}

/** Whether this is an Apple keyboard: the shortcuts are Control and Option
 * there, and Ctrl and Alt elsewhere. A browser that will not say is not one. */
function onMac(): boolean {
  try {
    return /Mac|iPhone|iPad|iPod/i.test(navigator.platform ?? "");
  } catch {
    return false;
  }
}

/** The page a person is on, for the top bar, the tab's title and where focus goes. */
type View = "chat" | "home" | "tools" | "media" | "documents" | "memory" | "sites";

const PAGE_TITLES = {
  tools: "Toolbox · Tools",
  media: "Bins · Media",
  documents: "Documents",
  memory: "Memory",
  sites: "Job sites",
} as const;

/** Move focus to a heading (or any element), making it a target if it is not. */
function focusOn(target: HTMLElement | null): void {
  if (!target) return;
  if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
  target.focus();
}

type Phase =
  | { kind: "loading" }
  | { kind: "signed-out"; message: string | null; unavailable: string | null }
  | { kind: "ready"; me: Me };

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [chatId, setChatId] = useState<string | null>(() => chatFromPath(window.location.pathname));
  const [media, setMedia] = useState(() => mediaFromPath(window.location.pathname));
  const [memoryOpen, setMemoryOpen] = useState(() => memoryFromPath(window.location.pathname));
  const [documentsOpen, setDocumentsOpen] = useState(() =>
    documentsFromPath(window.location.pathname),
  );
  // The owner reading someone else's bins (M3); null is one's own.
  const [mediaPerson, setMediaPerson] = useState<{ sub: string; name: string } | null>(null);
  // An image sent to a new chat waits in that chat's composer (M7).
  const [handoff, setHandoff] = useState<{ chatId: string; attachments: Handoff[] } | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [chatsLoaded, setChatsLoaded] = useState(false);
  const [models, setModels] = useState<Models | null>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [showSites, setShowSites] = useState(false);
  const [showChats, setShowChats] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [chatsError, setChatsError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const newPending = useRef(false);
  const focusTarget = useRef<string | null>(null);
  const shortcutsButton = useRef<HTMLButtonElement>(null);
  const view: View = showSites
    ? "sites"
    : showTools
      ? "tools"
      : media
        ? "media"
        : documentsOpen
          ? "documents"
          : memoryOpen
            ? "memory"
            : chatId
              ? "chat"
              : "home";
  const lastView = useRef(view);

  useEffect(() => {
    if (focusTarget.current) {
      document.getElementById(focusTarget.current)?.focus();
      focusTarget.current = null;
    }
  });

  // The tab says which page it holds, as it does for a chat (ChatView).
  useEffect(() => {
    if (
      phase.kind !== "ready" ||
      (view !== "tools" &&
        view !== "media" &&
        view !== "documents" &&
        view !== "memory" &&
        view !== "sites")
    )
      return;
    document.title = `${PAGE_TITLES[view]} · Workbench`;
    return () => {
      document.title = "Workbench";
    };
  }, [phase.kind, view]);

  // Six pages do not fit a phone's one row: the bar scrolls sideways there, and the
  // page a person is on is brought into view, whether they opened it or the address did.
  useEffect(() => {
    if (phase.kind !== "ready") return;
    document
      .querySelector<HTMLElement>('nav[aria-label="Workbench"] [aria-current="page"]')
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [phase.kind, view]);

  // Opening a page moves focus to its heading, so a screen reader says where it
  // is and the keyboard starts at the top of it, not back in the sidebar.
  useEffect(() => {
    const before = lastView.current;
    lastView.current = view;
    if (before === view || phase.kind !== "ready") return;
    if (
      view === "tools" ||
      view === "media" ||
      view === "documents" ||
      view === "memory" ||
      view === "sites"
    ) {
      focusOn(document.getElementById("view-heading") ?? document.querySelector("main h1"));
    }
  }, [phase.kind, view]);

  const open = useCallback((id: string | null) => {
    const path = id ? `/chats/${id}` : "/";
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    setChatId(id);
    setMedia(null);
    setMemoryOpen(false);
    setDocumentsOpen(false);
    setMediaPerson(null);
    setShowTools(false);
    setShowSites(false);
    setShowChats(false);
  }, []);

  const openMedia = useCallback(
    (door: Door, person: { sub: string; name: string } | null = null) => {
      const path = mediaPath(door);
      if (window.location.pathname !== path) window.history.pushState(null, "", path);
      setMedia({ door, id: null });
      setMemoryOpen(false);
      setDocumentsOpen(false);
      setMediaPerson(person);
      setShowTools(false);
      setShowSites(false);
      setShowChats(false);
    },
    [],
  );

  const openMemory = useCallback(() => {
    if (window.location.pathname !== "/memory") window.history.pushState(null, "", "/memory");
    setMemoryOpen(true);
    setDocumentsOpen(false);
    setMedia(null);
    setMediaPerson(null);
    setShowTools(false);
    setShowSites(false);
    setShowChats(false);
  }, []);

  const openDocuments = useCallback(() => {
    if (window.location.pathname !== "/documents") {
      window.history.pushState(null, "", "/documents");
    }
    setDocumentsOpen(true);
    setMemoryOpen(false);
    setMedia(null);
    setMediaPerson(null);
    setShowTools(false);
    setShowSites(false);
    setShowChats(false);
  }, []);

  useEffect(() => {
    const back = () => {
      setChatId(chatFromPath(window.location.pathname));
      setMedia(mediaFromPath(window.location.pathname));
      setMemoryOpen(memoryFromPath(window.location.pathname));
      setDocumentsOpen(documentsFromPath(window.location.pathname));
      setMediaPerson(null);
      setShowTools(false);
      setShowSites(false);
      setShowChats(false);
    };
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
      setChatsLoaded(true);
      setChatsError(null);
    } catch (error) {
      if (!(error instanceof SignedOut))
        setChatsError(error instanceof Error ? error.message : String(error));
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
    if (newPending.current) return;
    newPending.current = true;
    setCreating(true);
    setActionError(null);
    try {
      const chat = await post<Chat>("/api/chats", {});
      setChats((current) => [chat, ...current]);
      open(chat.id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      newPending.current = false;
      setCreating(false);
    }
  }, [open]);

  /** A new chat with Documents on, for asking about the person's own documents. */
  const askAboutDocuments = useCallback(async () => {
    if (newPending.current) return;
    newPending.current = true;
    setActionError(null);
    try {
      const made = await post<Chat>("/api/chats", {});
      let chat = made;
      try {
        chat = await patch<Chat>(`/api/chats/${made.id}`, { settings: { documents: "on" } });
      } catch (error) {
        setActionError(
          `The chat was made, but Documents could not be turned on in it: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      setChats((current) => [chat, ...current]);
      open(chat.id);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      newPending.current = false;
    }
  }, [open]);

  useEffect(() => {
    if (phase.kind !== "ready") return;
    const shortcuts = (event: KeyboardEvent) => {
      if (event.isComposing || event.repeat || event.getModifierState("AltGraph")) return;
      if (event.key === "Escape") {
        // Closing the panel from inside it hands focus back to its button.
        if (document.activeElement?.closest("#keyboard-shortcuts")) {
          shortcutsButton.current?.focus();
        }
        setShowShortcuts(false);
        return;
      }
      if (!event.ctrlKey || !event.altKey || event.metaKey || event.shiftKey) return;
      const key = event.code;
      if (key === "KeyN") {
        event.preventDefault();
        void newChat();
      }
      if (key === "KeyF") {
        event.preventDefault();
        focusTarget.current = "chat-search";
        setShowChats(true);
        document.getElementById("chat-search")?.focus();
      }
      if (key === "KeyM") {
        event.preventDefault();
        focusTarget.current = "message-composer";
        setShowChats(false);
        setShowTools(false);
        document.getElementById("message-composer")?.focus();
      }
    };
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [phase.kind, newChat]);

  if (phase.kind === "loading") {
    return (
      <p role="status" className="p-6 text-muted">
        Loading Workbench…
      </p>
    );
  }
  if (phase.kind === "signed-out") {
    return <SignIn message={phase.message} unavailable={phase.unavailable} />;
  }

  return (
    <div className="relative flex h-full">
      {/* The first stop: past the sidebar to what the page is about. A button
          and not a link, because a history change closes the pages. */}
      <button
        type="button"
        className="skip-link btn btn-primary"
        onClick={() =>
          focusOn(
            document.getElementById("conversation") ??
              document.getElementById("view-heading") ??
              document.querySelector("main h1"),
          )
        }
      >
        {view === "chat" ? "Skip to the conversation" : "Skip to the page"}
      </button>
      <div className={`${showChats ? "flex" : "hidden"} w-full shrink-0 md:flex md:w-auto`}>
        <Sidebar
          me={phase.me}
          chats={chats}
          chatsLoaded={chatsLoaded}
          current={chatId}
          onOpen={open}
          onOpenMedia={(person) => openMedia("images", person)}
          onClose={() => setShowChats(false)}
          onNew={() => void newChat()}
          creating={creating}
          error={chatsError}
          onRetry={() => void refreshChats()}
          onSignOut={async () => {
            await post("/api/signout").catch(() => undefined);
            forget();
            setChats([]);
            setChatsLoaded(false);
            setPhase({ kind: "signed-out", message: "You signed out.", unavailable: null });
          }}
        />
      </div>
      <main className={`${showChats ? "hidden md:flex" : "flex"} min-w-0 flex-1 flex-col`}>
        <div className="relative">
          {/* Every page is always there, and the one you are on is marked, so
              nothing moves when you change page. On a phone the row scrolls
              sideways; `relative` makes it the box that holds a tab's visually
              hidden words, so they scroll with it and never widen the page. */}
          <nav
            aria-label="Workbench"
            className="relative flex flex-wrap items-center border-b border-line px-2 py-1 max-sm:flex-nowrap max-sm:overflow-x-auto max-sm:[&>*]:shrink-0 sm:gap-x-1 sm:px-3"
          >
            <button
              type="button"
              onClick={() => setShowChats(true)}
              aria-controls="chat-navigation"
              className="nav-tab md:hidden"
            >
              Chats
            </button>
            <button
              type="button"
              aria-current={view === "tools" ? "page" : undefined}
              className="nav-tab"
              onClick={() => {
                if (view === "tools") return;
                setShowSites(false);
                setShowTools(true);
              }}
            >
              <Wrench size={14} aria-hidden />
              Toolbox · Tools
            </button>
            <button
              type="button"
              aria-current={view === "media" ? "page" : undefined}
              className="nav-tab"
              data-testid="open-media"
              onClick={() => {
                if (view === "media") return;
                openMedia("images");
              }}
            >
              <Boxes size={14} aria-hidden />
              Bins · Media
            </button>
            <button
              type="button"
              aria-current={view === "documents" ? "page" : undefined}
              className="nav-tab"
              data-testid="open-documents"
              onClick={() => {
                if (view === "documents") return;
                openDocuments();
              }}
            >
              <Files size={14} aria-hidden />
              Documents
            </button>
            <button
              type="button"
              aria-current={view === "memory" ? "page" : undefined}
              className="nav-tab"
              data-testid="open-memory"
              onClick={() => {
                if (view === "memory") return;
                openMemory();
              }}
            >
              <Brain size={14} aria-hidden />
              Memory
            </button>
            {!phase.me.owner && (
              <button
                type="button"
                aria-current={view === "sites" ? "page" : undefined}
                className="nav-tab"
                onClick={() => {
                  if (view === "sites") return;
                  setShowTools(false);
                  setShowSites(true);
                }}
              >
                <Laptop size={14} aria-hidden />
                Job sites <span className="max-sm:sr-only">(your machines)</span>
              </button>
            )}
            <button
              ref={shortcutsButton}
              type="button"
              onClick={() => setShowShortcuts((shown) => !shown)}
              aria-expanded={showShortcuts}
              aria-controls="keyboard-shortcuts"
              className="nav-tab nav-keys ml-auto text-muted"
            >
              <Keyboard size={14} aria-hidden />
              Keyboard shortcuts
            </button>
          </nav>
          {showShortcuts && (
            // An overlay under the bar, not a push that moves the chat down.
            <aside
              id="keyboard-shortcuts"
              aria-label="Keyboard shortcuts"
              className="absolute right-4 top-full z-20 max-h-[calc(100dvh-5rem)] w-[min(30rem,calc(100vw-2rem))] overflow-y-auto rounded-plexus border border-line bg-panel p-4 shadow-lg"
            >
              <Shortcuts />
              <button
                type="button"
                className="btn btn-quiet -ml-2 mt-3"
                onClick={() => {
                  setShowShortcuts(false);
                  shortcutsButton.current?.focus();
                }}
              >
                Close shortcuts
              </button>
            </aside>
          )}
        </div>
        {phase.me.installModeNotice && phase.me.installMode && phase.me.installModeChangedAt && (
          <aside
            role="status"
            data-testid="install-mode-notice"
            className="notice notice--warn notice--bar"
          >
            <Info size={16} aria-hidden />
            {modeChanged(phase.me.installMode, phase.me.installModeChangedAt)}{" "}
            <button
              className="btn btn-quiet -my-1 ml-auto shrink-0"
              onClick={async () => {
                await post("/api/me/mode-seen").catch(() => undefined);
                setPhase({ kind: "ready", me: { ...phase.me, installModeNotice: false } });
              }}
            >
              OK
            </button>
          </aside>
        )}
        {showSites ? (
          <JobSites onClose={() => setShowSites(false)} sub={phase.me.sub} />
        ) : showTools ? (
          <Tools owner={phase.me.owner} onClose={() => setShowTools(false)} />
        ) : media ? (
          <Media
            key={`${media.door}:${mediaPerson?.sub ?? ""}`}
            me={phase.me}
            door={media.door}
            focus={media.id}
            person={mediaPerson}
            onClose={() => open(chatId)}
            onDoor={(door) => openMedia(door, mediaPerson)}
            onToChat={(id, attachment) => {
              setHandoff({ chatId: id, attachments: [attachment] });
              open(id);
              void refreshChats();
            }}
            onTextToChat={async (text) => {
              // A transcript waits as the new chat's unsent text (M7).
              try {
                const chat = await post<Chat>("/api/chats", {});
                saveDraft(phase.me.sub, chat.id, text);
                setChats((current) => [chat, ...current]);
                open(chat.id);
              } catch (error) {
                setActionError(error instanceof Error ? error.message : String(error));
              }
            }}
          />
        ) : documentsOpen ? (
          <Documents onClose={() => open(chatId)} onAsk={() => void askAboutDocuments()} />
        ) : memoryOpen ? (
          <Memory
            chats={chats}
            chatsLoaded={chatsLoaded}
            onOpenChat={open}
            onClose={() => open(chatId)}
          />
        ) : chatId ? (
          <ChatView
            key={chatId}
            chatId={chatId}
            attachments={handoff?.chatId === chatId ? handoff.attachments : undefined}
            me={phase.me}
            models={models}
            modelsError={modelsError}
            onChanged={refreshChats}
            onDeleted={() => {
              open(null);
              void refreshChats();
            }}
            onOpenMemory={openMemory}
            onOpenDocuments={openDocuments}
          />
        ) : (
          <Empty
            hasChats={chats.length > 0}
            modelsError={modelsError}
            noModels={models !== null && models.models.length === 0}
            onNew={() => void newChat()}
            creating={creating}
          />
        )}
      </main>
      {actionError && (
        <div
          role="alert"
          className="notice notice--error notice--float absolute inset-x-3 bottom-3 z-10 shadow-lg"
        >
          <CircleAlert size={16} aria-hidden />
          {actionError}{" "}
          <button
            className="btn btn-quiet -my-1 ml-auto shrink-0"
            onClick={() => setActionError(null)}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}

/** The keys as keys: one box each, with a plus between. */
function Keys({ keys }: { keys: string[] }) {
  return (
    <>
      {keys.map((key, i) => (
        <span key={`${i}:${key}`} className="contents">
          {i > 0 && <span className="text-muted">+</span>}
          <kbd>{key}</kbd>
        </span>
      ))}
    </>
  );
}

/** What the keyboard does, grouped by where it works. */
function Shortcuts() {
  const [ctrl, alt] = onMac() ? ["Control", "Option"] : ["Ctrl", "Alt"];
  const save = onMac() ? "Cmd" : "Ctrl";
  const groups: { title: string; rows: [string[], string][] }[] = [
    {
      title: "Anywhere",
      rows: [
        [[ctrl, alt, "N"], "New chat"],
        [[ctrl, alt, "F"], "Search chats"],
        [[ctrl, alt, "M"], "Write a message"],
      ],
    },
    {
      title: "Message box",
      rows: [
        [["Enter"], "Send"],
        [["Shift", "Enter"], "New line"],
      ],
    },
    {
      title: "Chat search",
      rows: [
        [["Enter"], "Open the first match"],
        [["Esc"], "Clear the search"],
      ],
    },
    {
      title: "Editing a message",
      rows: [
        [[save, "Enter"], "Save"],
        [["Esc"], "Cancel"],
      ],
    },
  ];
  return (
    // One pair of columns for every group (a subgrid), so the keys end and the
    // words begin at the same x all the way down.
    <div className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-5 gap-y-4">
      {groups.map((group) => (
        <section key={group.title} className="col-span-2 grid grid-cols-subgrid">
          <h3 className="eyebrow col-span-2 mb-2 text-muted">{group.title}</h3>
          <dl className="keys col-span-2 grid-cols-subgrid">
            {group.rows.map(([keys, what]) => (
              <div key={what} className="contents">
                <dt>
                  <Keys keys={keys} />
                </dt>
                <dd>{what}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}

function Empty({
  hasChats,
  modelsError,
  noModels,
  onNew,
  creating,
}: {
  hasChats: boolean;
  modelsError: string | null;
  noModels: boolean;
  onNew: () => void;
  creating: boolean;
}) {
  return (
    <div className="flex flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="flex max-w-md flex-col items-center gap-4 text-center">
        <Mascot pose={modelsError ? "curious" : "guide"} />
        <h1 className="text-balance text-2xl font-semibold tracking-[-0.015em]">
          {hasChats ? "Pick a chat, or start one" : "Start your first chat"}
        </h1>
        {modelsError ? (
          <p role="alert" className="notice notice--error text-left">
            <CircleAlert size={16} aria-hidden />
            {modelsError}
          </p>
        ) : noModels ? (
          <p className="notice text-left">
            <Info size={16} aria-hidden />
            Eugene is not serving any model you can chat with yet. The owner can start one in
            Eugene&apos;s console.
          </p>
        ) : (
          <p className="text-muted">
            <span className="text-fg">Ask anything.</span> Answers keep going even if you close this
            tab.
          </p>
        )}
        <button
          type="button"
          data-testid="new-chat-empty"
          onClick={onNew}
          disabled={creating}
          className="btn btn-primary min-h-11 px-5 text-base"
        >
          {creating ? "Creating…" : "New chat"}
        </button>
      </div>
    </div>
  );
}
