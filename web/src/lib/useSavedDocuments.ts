/**
 * The person's documents as the Documents screen shows them (`GET /api/documents`):
 * read when the screen opens and after every change, and, while any document is
 * still being read, again each second until none is (the server sends no event for
 * it). A read that is overtaken by a later one is dropped, so a slow answer never
 * puts back what a newer one replaced.
 *
 * A read that fails keeps what is shown and says why beside it, except when the
 * server says the documents cannot be used (a damaged search): then nothing that
 * might be out of date is shown, and the server's words and what can be done come
 * instead.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { blockedBy, FIRST_POLL_MS, isPending, POLL_MS, RETRY_MS } from "./savedDocuments";
import type { DocumentsBlocked, DocumentsPage } from "./types";

export function useSavedDocuments() {
  const [data, setData] = useState<DocumentsPage | null>(null);
  const [blocked, setBlocked] = useState<DocumentsBlocked | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const latest = useRef(0);
  const failing = useRef(false);

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = await api<DocumentsPage>("/api/documents");
      if (mine !== latest.current) return;
      setData(next);
      setBlocked(null);
      setProblem(null);
      failing.current = false;
    } catch (failure) {
      if (mine !== latest.current) return;
      failing.current = true;
      const why = blockedBy(failure);
      if (why) {
        setData(null);
        setBlocked(why);
        setProblem(null);
      } else {
        setProblem(failure instanceof Error ? failure.message : String(failure));
      }
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // While a document is being read, ask again until it is not.
  const watching = data?.documents.some(isPending) ?? false;
  useEffect(() => {
    if (!watching) return;
    let stopped = false;
    let timer: number | undefined;
    const next = (wait: number) => {
      timer = window.setTimeout(async () => {
        await reload();
        if (!stopped) next(failing.current ? RETRY_MS : POLL_MS);
      }, wait);
    };
    next(FIRST_POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [watching, reload]);

  return { data, blocked, problem, reload };
}
