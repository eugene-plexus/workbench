/**
 * A chat's notes as the Memory chip and its drawer show them
 * (`GET /api/chats/{id}/memory`): read when the chat opens, again whenever
 * `stamp` changes (the model, or a setting that decides whether notes go),
 * and whenever the chat says so (`reload`, after a message is sent or a note
 * is kept or forgotten).
 *
 * A read that is overtaken by a later one is dropped, so a slow answer never
 * puts back what a newer one already replaced. A failed read keeps the view
 * already shown and says why beside it: the chip never goes blank, and never
 * claims what it did not read.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { ChatMemoryView } from "./types";

export function useChatMemory(chatId: string, stamp: string) {
  const [view, setView] = useState<ChatMemoryView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = await api<ChatMemoryView | undefined>(
        `/api/chats/${encodeURIComponent(chatId)}/memory`,
      );
      if (mine !== latest.current) return;
      setView(next ?? null);
      setError(null);
    } catch (failure) {
      if (mine !== latest.current) return;
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, [chatId]);

  /** Take a view the server just answered with (a refresh) as the one shown. */
  const adopt = useCallback((next: ChatMemoryView) => {
    latest.current += 1;
    setView(next);
    setError(null);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload, stamp]);

  return { view, error, reload, adopt };
}
