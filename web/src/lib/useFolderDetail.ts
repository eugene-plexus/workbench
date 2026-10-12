/**
 * One folder with everything its last check left out (`GET /api/documents/folders/{id}`): read
 * when the list of what was left out is opened, and again whenever the folder says it was checked
 * (`again`), so the list never says what an older check found. A read that is overtaken by a later
 * one is dropped; one that fails keeps what is shown and says why beside it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { failureWords } from "./memory";
import type { DocumentFolderDetail } from "./types";

export function useFolderDetail(folderId: string, again: string) {
  const [detail, setDetail] = useState<DocumentFolderDetail | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reads, setReads] = useState(0);
  const latest = useRef(0);

  useEffect(() => {
    const mine = ++latest.current;
    api<DocumentFolderDetail>(`/api/documents/folders/${encodeURIComponent(folderId)}`)
      .then((found) => {
        if (mine !== latest.current) return;
        setDetail(found);
        setProblem(null);
      })
      .catch((failure: unknown) => {
        if (mine === latest.current) setProblem(failureWords(failure));
      });
  }, [folderId, again, reads]);

  const reload = useCallback(() => setReads((n) => n + 1), []);
  return { detail, problem, reload };
}
