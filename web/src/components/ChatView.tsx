import {
  ArrowDown,
  Check,
  CircleAlert,
  Download,
  Eye,
  Pencil,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Fragment } from "react";

import { useAGUI } from "../lib/agui";
import { useInteractions } from "../lib/interactions";
import { AGUIConversation } from "./AGUIConversation";
import { InteractionSurfaces } from "./InteractionSurfaces";

import { del, patch } from "../lib/api";
import { exportChat, saveDraft } from "../lib/conveniences";
import { failureWords } from "../lib/memory";
import { hideHint, hintCount, hintHidden } from "../lib/savedDocuments";
import type { ChatSettings as Settings, Me, Message, Models } from "../lib/types";
import { useChat } from "../lib/useChat";
import { useChatDocuments } from "../lib/useChatDocuments";
import { useChatMemory } from "../lib/useChatMemory";
import { announce } from "../lib/words";
import { ChatSettings } from "./ChatSettings";
import { Composer } from "./Composer";
import { DocumentsChip, DocumentsDrawer, DocumentsHint } from "./DocumentsChip";
import { Mascot } from "./Mascot";
import { MemoryChip, MemoryDrawer } from "./MemoryChip";
import { MemoryToast, outcomeOf, type ToastOutcome } from "./MemoryToast";
import { MessageView } from "./MessageView";
import type { RememberProps } from "./RememberThis";

export function ChatView({
  chatId,
  attachments,
  me,
  models,
  modelsError,
  onChanged,
  onDeleted,
  onOpenMemory,
  onOpenDocuments,
}: {
  chatId: string;
  /** Files already in this chat, waiting in its composer: an image sent from the media area (M7). */
  attachments?: { id: string; name: string; mediaType: string }[];
  me: Me;
  models: Models | null;
  modelsError: string | null;
  onChanged: () => void;
  onDeleted: () => void;
  /** Open the Memory page (from the drawer, and from a toast's Manage). */
  onOpenMemory: () => void;
  /** Open the Documents screen (from the drawer, and from an answer's search that went wrong). */
  onOpenDocuments: () => void;
}) {
  const { detail, update, progress, error, reload, look } = useChat(chatId);
  const interactive = useInteractions(
    chatId,
    detail?.chat.readOnly ? detail.messages.at(-1)?.id : undefined,
  );
  const agui = useAGUI(chatId, detail !== null && !detail.chat.readOnly, me.sub);
  const [agentSource, setAgentSource] = useState("ordinary");
  const agentMode = agui.run !== null || agentSource !== "ordinary";
  const agentCursor = agui.run ? `${agui.run.runId}:${agui.run.cursor}:${agui.run.status}` : "";
  useEffect(() => {
    if (agentCursor) void reload();
  }, [agentCursor, reload]);
  // The settings, the memory drawer and the documents drawer are all panels above
  // the chat, and only one is open at a time (on a phone each covers the whole window).
  const [panel, setPanel] = useState<"settings" | "memory" | "documents" | null>(null);
  const showSettings = panel === "settings";
  const [toast, setToast] = useState<{ id: number; outcome: ToastOutcome } | null>(null);
  const toasts = useRef(0);
  // The notes this chat's next message carries. It is read again when the
  // model or a memory setting of the chat changes, after a send, and after a
  // note is kept or forgotten from here.
  const memory = useChatMemory(
    chatId,
    `${detail?.chat.model ?? ""}|${detail?.chat.settings.memory ?? ""}|${detail?.chat.settings.memoryExternal ?? ""}`,
  );
  // What this chat does with the person's documents: read when the chat opens, when its
  // model or a documents setting changes, after a send, and while documents are read.
  const documents = useChatDocuments(
    chatId,
    `${detail?.chat.model ?? ""}|${detail?.chat.settings.documents ?? ""}|${detail?.chat.settings.documentsOnly ?? ""}|${detail?.chat.settings.documentsExternal ?? ""}`,
  );
  // The hint in the composer: hidden for good by this person, or while it turns Documents on.
  const [hintGone, setHintGone] = useState(() => hintHidden(me.sub));
  const [turningOn, setTurningOn] = useState(false);
  const [hintProblem, setHintProblem] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [away, setAway] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  // Follow the bottom until the reader scrolls up, and again once they
  // scroll back down. Growth alone never unpins: an answer arriving, or an
  // image loading late, moves the bottom without the reader doing anything.
  const pinned = useRef(true);
  const lastTop = useRef(0);
  const running =
    (detail?.messages.some((m) => m.status === "running") ?? false) ||
    agui.run?.status === "running";
  const wasRunning = useRef(running);
  const loaded = detail !== null;

  // What a screen reader is told when the newest answer changes phase. What is
  // on the page when the chat opens is not news, so the first phrase is only
  // remembered; a change after it is said.
  const phase = announce(detail?.messages[detail.messages.length - 1]);
  const [said, setSaid] = useState("");
  const lastPhase = useRef<string | null>(null);
  useEffect(() => {
    if (!loaded) return;
    if (lastPhase.current !== null && lastPhase.current !== phase) setSaid(phase);
    lastPhase.current = phase;
  }, [loaded, phase]);

  useEffect(() => {
    const inner = content.current;
    const outer = box.current;
    if (!inner || !outer) return;
    const follow = () => {
      if (pinned.current) outer.scrollTop = outer.scrollHeight;
    };
    follow();
    const observer = new ResizeObserver(follow);
    observer.observe(inner);
    return () => observer.disconnect();
  }, [loaded]);

  useEffect(() => {
    // An answer that has just ended changes the dot in the chat list.
    if (wasRunning.current && !running) onChanged();
    wasRunning.current = running;
  }, [running, onChanged]);

  // The tab says which chat it holds, so several open tabs can be told apart.
  const title = detail?.chat.title;
  useEffect(() => {
    if (!title) return;
    document.title = `${title} · Workbench`;
    return () => {
      document.title = "Workbench";
    };
  }, [title]);

  if (error && !detail) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <Mascot pose="curious" size={120} />
        <p role="alert" className="notice notice--error max-w-measure text-left">
          <CircleAlert size={16} aria-hidden />
          {error}
        </p>
        <button onClick={() => void reload()} className="btn">
          Try loading this chat again
        </button>
      </div>
    );
  }
  if (!detail)
    return (
      <div role="status" className="flex-1 p-6 text-muted">
        Loading chat…
      </div>
    );

  const { chat, messages } = detail;

  function say(outcome: ToastOutcome | null) {
    if (outcome) setToast({ id: ++toasts.current, outcome });
  }

  // Remember this on a message: not in someone else's chat, and not where the
  // owner has turned Memory off. Where the person has, it says so when pressed.
  const code = memory.view && !memory.view.readOnly ? memory.view.reasonCode : null;
  const remember: RememberProps | undefined =
    chat.readOnly || code === "install_off"
      ? undefined
      : {
          off: code === "person_off" ? "you" : null,
          onSaved: (note, created) => {
            say({ kind: "remembered", note, created });
            void memory.reload();
          },
          onManage: onOpenMemory,
        };

  async function saveSettings(next: Settings) {
    const updated = await patch<typeof chat>(`/api/chats/${chat.id}`, { settings: next });
    update((shown) => ({ ...shown, chat: { ...updated, running: shown.chat.running } }));
  }

  async function turnOnDocuments() {
    setTurningOn(true);
    setHintProblem(null);
    try {
      await saveSettings({ documents: "on" });
      await documents.reload();
    } catch (failure) {
      setHintProblem(failureWords(failure));
    } finally {
      setTurningOn(false);
    }
  }

  const aboutDocuments = hintGone ? null : hintCount(documents.view);

  async function rename(title: string) {
    if (!title.trim() || acting) return;
    setActing(true);
    setActionError(null);
    try {
      const updated = await patch<typeof chat>(`/api/chats/${chat.id}`, { title: title.trim() });
      update((shown) => ({ ...shown, chat: { ...updated, running: shown.chat.running } }));
      setRenaming(null);
      onChanged();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setActing(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* The header's content sits on the thread's own frame, so the title
          starts where the messages do and the buttons end where they end. */}
      <header className="border-b border-line px-4 py-1.5">
        <div className="mx-auto flex w-full max-w-3xl flex-wrap items-center gap-2">
          {renaming !== null ? (
            <form
              className="flex min-w-0 flex-1 items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void rename(renaming);
              }}
            >
              <input
                autoFocus
                aria-label="Chat name"
                value={renaming}
                maxLength={200}
                disabled={acting}
                onKeyDown={(e) => {
                  if (e.key === "Escape" && !acting) setRenaming(null);
                }}
                onChange={(e) => setRenaming(e.target.value)}
                className="control min-w-0 flex-1"
              />
              <button
                type="submit"
                disabled={acting || !renaming.trim()}
                aria-label="Save the name"
                className="icon-btn"
              >
                <Check size={16} />
              </button>
              <button
                type="button"
                aria-label="Keep the old name"
                disabled={acting}
                onClick={() => setRenaming(null)}
                className="icon-btn"
              >
                <X size={16} />
              </button>
            </form>
          ) : (
            <h1
              title={chat.title}
              // The title has its own row where the two chips and the four buttons
              // would squeeze it to a few letters: below 1024 px, on a phone and on a
              // tablet, where the sidebar also takes 288 px of the window.
              className="min-w-0 flex-1 truncate text-xl font-semibold tracking-[-0.01em] max-lg:basis-full"
              data-testid="chat-title"
            >
              {chat.title}
            </h1>
          )}
          {renaming === null && !confirmDelete && (
            <DocumentsChip
              view={documents.view}
              failed={documents.error !== null}
              open={panel === "documents"}
              onToggle={() => setPanel((now) => (now === "documents" ? null : "documents"))}
            />
          )}
          {renaming === null && !confirmDelete && (
            <MemoryChip
              view={memory.view}
              failed={memory.error !== null && memory.view === null}
              open={panel === "memory"}
              onToggle={() => setPanel((now) => (now === "memory" ? null : "memory"))}
            />
          )}
          {renaming === null && !confirmDelete && (
            <button
              type="button"
              title="Export chat as Markdown (attachment contents are not included)"
              aria-label="Export chat as Markdown"
              onClick={() => {
                try {
                  exportChat(detail);
                } catch (error) {
                  setActionError(error instanceof Error ? error.message : String(error));
                }
              }}
              className="icon-btn max-lg:ml-auto"
            >
              <Download size={16} />
            </button>
          )}
          {!chat.readOnly && renaming === null && !confirmDelete && (
            <>
              <button
                type="button"
                title="Rename this chat"
                aria-label="Rename this chat"
                onClick={() => setRenaming(chat.title)}
                className="icon-btn"
              >
                <Pencil size={16} />
              </button>
              <button
                type="button"
                title="This chat's settings"
                aria-label="This chat's settings"
                aria-expanded={showSettings}
                onClick={() => setPanel((now) => (now === "settings" ? null : "settings"))}
                className="icon-btn"
              >
                <SlidersHorizontal size={16} />
              </button>
              <button
                type="button"
                title="Delete this chat"
                aria-label="Delete this chat"
                onClick={() => setConfirmDelete(true)}
                className="icon-btn icon-btn--danger"
              >
                <Trash2 size={16} />
              </button>
            </>
          )}
          {confirmDelete && !chat.readOnly && (
            // The sentence, then the safe answer first and focused, then the
            // one that cannot be undone. Escape backs out.
            <div
              role="group"
              aria-label="Delete this chat?"
              className="notice notice--error w-full flex-wrap items-center"
              onKeyDown={(e) => {
                if (e.key === "Escape" && !acting) setConfirmDelete(false);
              }}
            >
              <TriangleAlert size={16} aria-hidden />
              {`Delete "${chat.title}" and the files attached to it?`}
              {/* Focus starts on the safe answer: Enter on a reflex keeps the chat. */}
              <button
                type="button"
                autoFocus
                data-testid="keep-chat"
                onClick={() => setConfirmDelete(false)}
                disabled={acting}
                className="btn"
              >
                Keep it
              </button>
              <button
                type="button"
                data-testid="confirm-delete"
                disabled={acting}
                onClick={async () => {
                  if (acting) return;
                  setActing(true);
                  setActionError(null);
                  try {
                    await del(`/api/chats/${chat.id}`);
                    saveDraft(me.sub, chat.id, "");
                    onDeleted();
                  } catch (error) {
                    setActionError(error instanceof Error ? error.message : String(error));
                  } finally {
                    setActing(false);
                  }
                }}
                className="btn btn-danger"
              >
                Delete chat
              </button>
            </div>
          )}
        </div>
      </header>
      {(actionError || error) && (
        <p role="alert" className="notice notice--error notice--bar">
          <CircleAlert size={16} aria-hidden />
          {actionError ?? error}{" "}
          {actionError ? (
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="btn btn-quiet -my-1 ml-auto shrink-0"
            >
              Dismiss
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void reload()}
              className="btn btn-quiet -my-1 ml-auto shrink-0"
            >
              Reload chat
            </button>
          )}
        </p>
      )}
      {chat.readOnly && (
        <p data-testid="read-only" className="notice notice--warn notice--bar">
          <Eye size={16} aria-hidden />
          {detail.ownerName ? `${detail.ownerName}'s chat.` : "Someone else's chat."} You can read
          it, because this Workbench lets its owner read people&apos;s chats. You cannot change it.
        </p>
      )}
      {showSettings && !chat.readOnly && (
        <ChatSettings
          settings={chat.settings}
          onSave={saveSettings}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "documents" && (
        <DocumentsDrawer
          chatId={chat.id}
          view={documents.view}
          error={documents.error}
          onChat={(next) =>
            update((shown) => ({ ...shown, chat: { ...next, running: shown.chat.running } }))
          }
          onReload={documents.reload}
          onManage={onOpenDocuments}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "memory" && (
        <MemoryDrawer
          chatId={chat.id}
          view={memory.view}
          error={memory.error}
          onChat={(next) =>
            update((shown) => ({ ...shown, chat: { ...next, running: shown.chat.running } }))
          }
          onReload={memory.reload}
          onAdopt={memory.adopt}
          onManage={onOpenMemory}
          onClose={() => setPanel(null)}
        />
      )}
      {/* The pill floats over the thread, so showing it no longer resizes the
          pane. */}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div aria-live="polite" aria-atomic="true" className="sr-only" data-testid="announcer">
          {said}
        </div>
        <div
          ref={box}
          onScroll={(e) => {
            const outer = e.currentTarget;
            const atBottom = outer.scrollHeight - outer.scrollTop - outer.clientHeight < 80;
            if (atBottom) pinned.current = true;
            else if (outer.scrollTop < lastTop.current) pinned.current = false;
            setAway(!pinned.current);
            lastTop.current = outer.scrollTop;
          }}
          id="conversation"
          // A named, focusable region, so PageUp and the arrow keys scroll it
          // from the keyboard. Relative, so words hidden for a screen reader
          // deep in the thread (a footnotes heading) stay inside it, rather
          // than reaching below the window and scrolling the whole page.
          role="region"
          aria-label="Conversation"
          tabIndex={0}
          className="relative min-h-0 flex-1 overflow-y-auto px-4 pb-8 pt-6 [scrollbar-gutter:stable_both-edges] focus-visible:-outline-offset-2"
          data-testid="messages"
        >
          <div ref={content} className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages.length === 0 && (
              <p className="py-10 text-center text-muted">Ask anything to start.</p>
            )}
            {messages.map((message: Message) => (
              <Fragment key={message.id}>
                <MessageView
                  key={message.id}
                  chatId={chat.id}
                  message={message}
                  progress={message.status === "running" ? progress : null}
                  busy={running}
                  readOnly={chat.readOnly}
                  onChanged={() => {
                    void reload();
                    onChanged();
                  }}
                  onLook={chat.readOnly ? look : undefined}
                  memory={remember}
                  documents={chat.readOnly ? undefined : { onManage: onOpenDocuments }}
                />
                <InteractionSurfaces
                  chat={chat.id}
                  message={message.id}
                  snapshot={interactive.snapshot}
                  refresh={interactive.refresh}
                />
              </Fragment>
            ))}
            {interactive.error && (
              <p className="text-sm text-muted" role="status">
                Interactive content is unavailable: {interactive.error}
              </p>
            )}
          </div>
        </div>
        {away && (
          <button
            className="btn absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full shadow-lg"
            onClick={() => {
              pinned.current = true;
              setAway(false);
              if (box.current) box.current.scrollTop = box.current.scrollHeight;
            }}
          >
            <ArrowDown size={14} aria-hidden className="text-accent" />
            Jump to latest
          </button>
        )}
        {toast && (
          // Over the thread, above where the person is typing; above the jump
          // pill when that is showing, so the two never cover each other.
          <div
            className={`absolute inset-x-4 z-10 mx-auto max-w-xl ${away ? "bottom-16" : "bottom-4"}`}
          >
            <MemoryToast
              key={toast.id}
              outcome={toast.outcome}
              onManage={onOpenMemory}
              onChanged={() => void memory.reload()}
              onClose={() => setToast(null)}
            />
          </div>
        )}
      </div>
      {!chat.readOnly && (
        <AGUIConversation
          chat={chat.id}
          me={me}
          empty={messages.length === 0}
          selected={agentSource}
          onSelect={setAgentSource}
          controller={agui}
          model={chat.model}
          models={models}
          onChanged={() => {
            void reload();
            onChanged();
          }}
        />
      )}
      {!chat.readOnly && !agentMode && (
        <div className="contents" inert={!agui.loaded}>
          <Composer
            chat={chat}
            initialAttachments={attachments}
            me={me}
            models={models}
            modelsError={modelsError}
            running={running}
            documentsHint={
              aboutDocuments === null ? undefined : (
                <DocumentsHint
                  documents={aboutDocuments}
                  busy={turningOn}
                  problem={hintProblem}
                  onTurnOn={() => void turnOnDocuments()}
                  onHide={() => {
                    hideHint(me.sub);
                    setHintGone(true);
                  }}
                />
              )
            }
            onSent={(user, answer, notes) => {
              pinned.current = true;
              setAway(false);
              update((shown) => ({
                ...shown,
                chat: { ...shown.chat, running: true },
                // The answer's own events may already have put it here.
                messages: [
                  ...shown.messages.filter((m) => m.id !== user.id && m.id !== answer.id),
                  user,
                  shown.messages.find((m) => m.id === answer.id) ?? answer,
                ],
              }));
              onChanged();
              // "Remember that ..." has kept a note by now, and the request has
              // taken the chat's copy of them: the toast and the chip say so.
              say(outcomeOf(notes));
              void memory.reload();
              void documents.reload();
            }}
            onChat={(next) =>
              update((shown) => ({ ...shown, chat: { ...next, running: shown.chat.running } }))
            }
          />
        </div>
      )}
    </div>
  );
}
