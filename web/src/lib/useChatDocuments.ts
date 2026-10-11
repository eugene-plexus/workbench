/**
 * A chat's Documents as the chip, its drawer and the hint show them
 * (`GET /api/chats/{id}/documents`): read when the chat opens, again whenever
 * `stamp` changes (the model, or a setting that decides what is searched), and
 * whenever the chat says so (`reload`, after a message is sent). While any of the
 * documents it would search is still being read, it asks again every two seconds,
 * so the count on the chip is the count that can be searched now.
 *
 * A read that is overtaken by a later one is dropped, so a slow answer never puts
 * back what a newer one already replaced. A failed read keeps the view already
 * shown and says why beside it: the chip never goes blank, and never claims what
 * it did not read.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { readView } from "./savedDocuments";
import type { ChatDocumentsView } from "./types";

/** How often a chat asks again while documents are being read. */
export const WAIT_MS = 2000;

export function useChatDocuments(chatId: string, stamp: string) {
  const [view, setView] = useState<ChatDocumentsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  // How many reads have ended, so each one (whether it worked or not) can start the wait for the next.
  const [reads, setReads] = useState(0);
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = await api<unknown>(`/api/chats/${encodeURIComponent(chatId)}/documents`);
      if (mine !== latest.current) return;
      setView(readView(next));
      setError(null);
      setReads((n) => n + 1);
    } catch (failure) {
      if (mine !== latest.current) return;
      setError(failure instanceof Error ? failure.message : String(failure));
      setReads((n) => n + 1);
    }
  }, [chatId]);

  /** Take a view the server just answered with as the one shown. */
  const adopt = useCallback((next: ChatDocumentsView) => {
    latest.current += 1;
    setView(next);
    setError(null);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, stamp]);

  // Documents still being read are not searched yet: ask again until none is.
  const waiting =
    view && !view.readOnly && view.counts.library.waiting + view.counts.chat.waiting > 0;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setTimeout(() => void reload(), WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [waiting, reads, reload]);

  return { view, error, reload, adopt };
}
