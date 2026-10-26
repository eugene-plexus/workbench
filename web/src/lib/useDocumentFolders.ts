/**
 * The person's folders as the Documents screen shows them (`GET /api/documents/folders`): read
 * when the screen opens and after every change, and then again by itself for as long as the
 * screen is open, because the server sends no event for any of it. How soon depends on what is
 * going on (`foldersWait`): each second while a check is under way or due, or while files are
 * still being read (so the counts are seen to move), and otherwise each half minute, or sooner
 * when a check is due before then, so the screen sees it begin. A read that is overtaken by a
 * later one is dropped, so a slow answer never puts back what a newer one replaced.
 *
 * A read that fails keeps what is shown and says why beside it. An answer that is not a list of
 * folders is a failure too: the page tells nothing of folders it did not read.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, serverNow } from "./api";
import { foldersWait, readFolders } from "./documentFolders";
import { failureWords } from "./memory";
import type { DocumentFolders } from "./types";

const NOT_UNDERSTOOD = "Workbench's answer about your folders was not one this page understands.";

export function useDocumentFolders(projectId?: string) {
  const [data, setData] = useState<DocumentFolders | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // How many reads have ended, so each one (whether it worked or not) starts the wait for the next.
  const [reads, setReads] = useState(0);
  const latest = useRef(0);
  const failing = useRef(false);

  const reload = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const next = readFolders(
        await api<unknown>(
          `/api/documents/folders${projectId ? `?projectId=${encodeURIComponent(projectId)}` : ""}`,
        ),
      );
      if (mine !== latest.current) return;
      if (!next) throw new Error(NOT_UNDERSTOOD);
      setData(next);
      setProblem(null);
      failing.current = false;
    } catch (failure) {
      if (mine !== latest.current) return;
      failing.current = true;
      setProblem(failureWords(failure));
    }
    setReads((n) => n + 1);
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Ask again, as soon as what is going on says to. Nothing is asked again before the first read
  // has worked: a person who sees it failed asks with Try again.
  useEffect(() => {
    if (!data) return;
    const timer = window.setTimeout(
      () => void reload(),
      foldersWait(data, serverNow(), failing.current),
    );
    return () => window.clearTimeout(timer);
  }, [data, reads, reload]);

  return { data, problem, reload };
}
