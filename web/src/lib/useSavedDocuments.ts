/**
 * The person's documents as the Documents screen shows them (`GET /api/documents`):
 * read when the screen opens and after every change, and then again by itself for as
 * long as the screen is open, because the server sends no event for any of it. How soon
 * depends on what is going on (`watching`): each second while a document is being read
 * or given its meaning, every few seconds while meaning waits for the search model or
 * the key's limit, and each half minute when nothing is, because the header is a live
 * look at the search model and a person who started or stopped one is told so. A read
 * that is overtaken by a later one is dropped, so a slow answer never puts back what a
 * newer one replaced.
 *
 * A read that fails keeps what is shown and says why beside it, except when the
 * server says the documents cannot be used (a damaged search): then nothing that
 * might be out of date is shown, and the server's words and what can be done come
 * instead.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import {
  blockedBy,
  FIRST_POLL_MS,
  IDLE_POLL_MS,
  POLL_MS,
  RETRY_MS,
  WAITING_POLL_MS,
  watching,
} from "./savedDocuments";
import type { DocumentsBlocked, DocumentsPage } from "./types";

export function useSavedDocuments() {
  const [data, setData] = useState<DocumentsPage | null>(null);
  const [blocked, setBlocked] = useState<DocumentsBlocked | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // How many reads have ended, so each one (whether it worked or not) starts the wait for the next.
  const [reads, setReads] = useState(0);
  const latest = useRef(0);
  const failing = useRef(false);
  // Whether the last read found something going on, and whether this is the first that did: the
  // first look comes soon, because most files are read in a moment.
  const busy = useRef(false);
  const first = useRef(false);

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = await api<DocumentsPage>("/api/documents");
      if (mine !== latest.current) return;
      const now = watching(next) === "busy";
      first.current = now && !busy.current;
      busy.current = now;
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
    setReads((n) => n + 1);
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Ask again, as soon as what is going on says to.
  useEffect(() => {
    if (!data) return;
    const going = watching(data);
    // A read that failed while something was going on is asked again sooner than one in a
    // quiet moment, which is only the half-minute look.
    const wait =
      going === "idle"
        ? IDLE_POLL_MS
        : going === "waiting"
          ? WAITING_POLL_MS
          : failing.current
            ? RETRY_MS
            : first.current
              ? FIRST_POLL_MS
              : POLL_MS;
    const timer = window.setTimeout(() => void reload(), wait);
    return () => window.clearTimeout(timer);
  }, [data, reads, reload]);

  return { data, blocked, problem, reload };
}
