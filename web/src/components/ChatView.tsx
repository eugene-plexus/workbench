import { Check, Pencil, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { del, patch } from "../lib/api";
import type { ChatSettings as Settings, Me, Message, Models } from "../lib/types";
import { useChat } from "../lib/useChat";
import { ChatSettings } from "./ChatSettings";
import { Composer } from "./Composer";
import { Mascot } from "./Mascot";
import { MessageView } from "./MessageView";

export function ChatView({
  chatId,
  me,
  models,
  modelsError,
  onChanged,
  onDeleted,
}: {
  chatId: string;
  me: Me;
  models: Models | null;
  modelsError: string | null;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const { detail, update, progress, error, reload } = useChat(chatId);
  const [showSettings, setShowSettings] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  // Follow the bottom until the reader scrolls up, and again once they
  // scroll back down. Growth alone never unpins: an answer arriving, or an
  // image loading late, moves the bottom without the reader doing anything.
  const pinned = useRef(true);
  const lastTop = useRef(0);
  const running = detail?.messages.some((m) => m.status === "running") ?? false;
  const wasRunning = useRef(running);
  const loaded = detail !== null;

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

  if (error && !detail) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
        <Mascot pose="curious" size={120} />
        <p role="alert">{error}</p>
      </div>
    );
  }
  if (!detail) return <div className="flex-1" />;

  const { chat, messages } = detail;

  async function saveSettings(next: Settings) {
    const updated = await patch<typeof chat>(`/api/chats/${chat.id}`, { settings: next });
    update((shown) => ({ ...shown, chat: { ...updated, running: shown.chat.running } }));
  }

  async function rename(title: string) {
    const updated = await patch<typeof chat>(`/api/chats/${chat.id}`, { title });
    update((shown) => ({ ...shown, chat: { ...updated, running: shown.chat.running } }));
    setRenaming(null);
    onChanged();
  }

  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-2 border-b border-line px-4 py-2">
        {renaming !== null ? (
          <form
            className="flex flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void rename(renaming);
            }}
          >
            <input
              autoFocus
              aria-label="Chat name"
              value={renaming}
              onChange={(e) => setRenaming(e.target.value)}
              className="flex-1 rounded-plexus border border-line bg-soft px-2 py-1"
            />
            <button type="submit" aria-label="Save the name" className="p-1 hover:text-accent">
              <Check size={16} />
            </button>
            <button
              type="button"
              aria-label="Keep the old name"
              onClick={() => setRenaming(null)}
              className="p-1"
            >
              <X size={16} />
            </button>
          </form>
        ) : (
          <h1 className="flex-1 truncate text-base font-semibold" data-testid="chat-title">
            {chat.title}
          </h1>
        )}
        {!chat.readOnly && renaming === null && (
          <>
            <button
              type="button"
              title="Rename this chat"
              aria-label="Rename this chat"
              onClick={() => setRenaming(chat.title)}
              className="rounded-plexus p-1.5 text-muted hover:bg-hover"
            >
              <Pencil size={16} />
            </button>
            <button
              type="button"
              title="This chat's settings"
              aria-label="This chat's settings"
              aria-expanded={showSettings}
              onClick={() => setShowSettings((v) => !v)}
              className="rounded-plexus p-1.5 text-muted hover:bg-hover"
            >
              <SlidersHorizontal size={16} />
            </button>
            {confirmDelete ? (
              <span className="flex items-center gap-2 text-sm">
                Delete this chat and its files?
                <button
                  type="button"
                  data-testid="confirm-delete"
                  onClick={async () => {
                    await del(`/api/chats/${chat.id}`);
                    onDeleted();
                  }}
                  className="rounded-plexus border border-error-line bg-error-bg px-2 py-0.5 text-error"
                >
                  Delete
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="px-2 py-0.5"
                >
                  Keep it
                </button>
              </span>
            ) : (
              <button
                type="button"
                title="Delete this chat"
                aria-label="Delete this chat"
                onClick={() => setConfirmDelete(true)}
                className="rounded-plexus p-1.5 text-muted hover:bg-hover"
              >
                <Trash2 size={16} />
              </button>
            )}
          </>
        )}
      </header>
      {chat.readOnly && (
        <p
          data-testid="read-only"
          className="border-b border-warn-line bg-warn-bg px-4 py-2 text-sm text-warn"
        >
          {detail.ownerName ? `${detail.ownerName}'s chat.` : "Someone else's chat."} You can read
          it, because this Workbench lets its owner read people&apos;s chats. You cannot change it.
        </p>
      )}
      {showSettings && !chat.readOnly && (
        <ChatSettings
          settings={chat.settings}
          onSave={saveSettings}
          onClose={() => setShowSettings(false)}
        />
      )}
      <div
        ref={box}
        onScroll={(e) => {
          const outer = e.currentTarget;
          const atBottom = outer.scrollHeight - outer.scrollTop - outer.clientHeight < 80;
          if (atBottom) pinned.current = true;
          else if (outer.scrollTop < lastTop.current) pinned.current = false;
          lastTop.current = outer.scrollTop;
        }}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-4"
        data-testid="messages"
      >
        <div ref={content} className="mx-auto flex max-w-3xl flex-col gap-5">
          {messages.length === 0 && (
            <p className="py-10 text-center text-muted">Ask anything to start.</p>
          )}
          {messages.map((message: Message) => (
            <MessageView
              key={message.id}
              chatId={chat.id}
              message={message}
              progress={message.status === "running" ? progress : null}
              last={message.id === lastAnswer?.id}
              busy={running}
              readOnly={chat.readOnly}
              onChanged={() => {
                void reload();
                onChanged();
              }}
            />
          ))}
        </div>
      </div>
      {!chat.readOnly && (
        <Composer
          chat={chat}
          me={me}
          models={models}
          modelsError={modelsError}
          running={running}
          onSent={(user, answer) => {
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
          }}
          onChat={(next) =>
            update((shown) => ({ ...shown, chat: { ...next, running: shown.chat.running } }))
          }
        />
      )}
    </div>
  );
}
