/**
 * One chat as the page shows it: loaded once, then kept current by its
 * events (events.ts).
 *
 * The load and the stream race, so each piece of an answer says where it
 * goes (`contentAt`, in the page's own string length). A piece already in
 * hand is skipped, the next one is appended, and one that would leave a gap
 * means something was missed, so the chat is loaded again. Events that
 * arrive before the first load are held and applied to it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { watch } from "./events";
import type { ChatDetail, ChatEvent, Message, Progress } from "./types";

type Outcome = { detail: ChatDetail; gap: boolean };

/** `piece` into `text` at `at`: appended, already there, or a gap. */
export function place(
  text: string,
  piece: string | undefined,
  at: number | undefined,
): { text: string; gap: boolean } {
  if (!piece) return { text, gap: false };
  if (at === undefined || at === text.length) return { text: text + piece, gap: false };
  if (at + piece.length <= text.length) return { text, gap: false };
  if (at < text.length) return { text: text.slice(0, at) + piece, gap: false };
  return { text, gap: true };
}

export function applyEvent(detail: ChatDetail, event: ChatEvent): Outcome {
  const replace = (message: Message): ChatDetail => {
    const shown = detail.messages.find((m) => m.id === message.id);
    const found = shown !== undefined;
    // An event's message carries neither its files nor its place among its
    // versions; the ones already shown stay.
    const merged = {
      ...message,
      files: message.files ?? shown?.files,
      versions: message.versions ?? shown?.versions,
    };
    return {
      ...detail,
      chat: { ...detail.chat, running: message.status === "running" },
      messages: found
        ? detail.messages.map((m) => (m.id === message.id ? merged : m))
        : [...detail.messages, merged],
    };
  };
  switch (event.type) {
    case "answer":
    case "done":
      return { detail: replace(event.message), gap: false };
    case "delta": {
      let gap = false;
      const messages = detail.messages.map((m) => {
        if (m.id !== event.id) return m;
        const content = place(m.content, event.content, event.contentAt);
        const reasoning = place(m.reasoning, event.reasoning, event.reasoningAt);
        gap = content.gap || reasoning.gap;
        return {
          ...m,
          content: content.text,
          reasoning: reasoning.text,
          sources: event.sources ?? m.sources,
        };
      });
      const known = detail.messages.some((m) => m.id === event.id);
      return { detail: { ...detail, messages }, gap: gap || !known };
    }
    case "progress": {
      if (event.answerFrom === undefined) return { detail, gap: false };
      const messages = detail.messages.map((m) =>
        m.id === event.id
          ? { ...m, answerFrom: event.answerFrom, reasoningFrom: event.reasoningFrom }
          : m,
      );
      return { detail: { ...detail, messages }, gap: false };
    }
    default:
      return { detail, gap: false };
  }
}

/** What an event says about progress: a new value, cleared, or no change. */
export function progressAfter(event: ChatEvent): Progress | null | undefined {
  switch (event.type) {
    case "answer":
      return event.progress;
    case "progress":
      return event.progress;
    case "done":
      return null;
    case "delta":
      return event.content || event.reasoning ? null : undefined;
    default:
      return undefined;
  }
}

export function useChat(chatId: string | null) {
  const [detail, setDetailState] = useState<ChatDetail | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The chat as last shown, kept beside the state so an event can be
  // placed against it at once, rather than inside a React updater.
  const current = useRef<ChatDetail | null>(null);
  const held = useRef<ChatEvent[]>([]);
  // The owner's read-only view looks at another branch through this message,
  // which saves nothing (V5). Null is the person's own path.
  const via = useRef<string | null>(null);

  const setDetail = useCallback((next: ChatDetail | null) => {
    current.current = next;
    setDetailState(next);
  }, []);

  /** A change to the chat as it is now, never to a copy a callback closed
   * over: a reply that lands after an answer arrived must not undo it. */
  const update = useCallback(
    (change: (shown: ChatDetail) => ChatDetail) => {
      if (current.current) setDetail(change(current.current));
    },
    [setDetail],
  );

  const reload = useCallback(async () => {
    if (!chatId) return;
    try {
      const through = via.current ? `?via=${encodeURIComponent(via.current)}` : "";
      let next = await api<ChatDetail>(`/api/chats/${encodeURIComponent(chatId)}${through}`);
      for (const event of held.current) next = applyEvent(next, event).detail;
      held.current = [];
      setDetail(next);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [chatId, setDetail]);

  /** Show the branch through `id` without choosing it. */
  const look = useCallback(
    (id: string) => {
      via.current = id;
      void reload();
    },
    [reload],
  );

  useEffect(() => {
    setDetail(null);
    setProgress(null);
    setError(null);
    held.current = [];
    via.current = null;
    if (!chatId) return;
    const watching = watch(
      chatId,
      (event) => {
        // Another version is shown in some tab: every tab follows.
        if (event.type === "reload" || event.type === "path") {
          void reload();
          return;
        }
        const after = progressAfter(event);
        if (after !== undefined) setProgress(after);
        const shown = current.current;
        if (shown === null) {
          held.current.push(event);
          return;
        }
        const outcome = applyEvent(shown, event);
        setDetail(outcome.detail);
        if (outcome.gap) void reload();
      },
      () => void reload(),
    );
    void reload();
    return () => watching.close();
  }, [chatId, reload, setDetail]);

  return { detail, update, progress, error, reload, look };
}
