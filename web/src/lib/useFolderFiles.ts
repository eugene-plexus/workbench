/**
 * One page of a folder's files (`GET /api/documents/folders/{id}/files`): by state, by the reason
 * a file was skipped or failed, and by a word in its path. It is read again whenever the folder
 * says something changed (`again`: its counts and the time of its last check), so the list is
 * seen to move while the files are read. A page that is overtaken by a later one is dropped.
 * What is shown stays until the next page arrives, so a filter that is chosen does not blank the
 * list, and a read that fails keeps it and says why beside it.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import { type FileFilter, type FileRequest, fileRequests } from "./documentFolders";
import { failureWords } from "./memory";
import type { FolderCounts, FolderFile, FolderFiles } from "./types";

export interface FilesQuery {
  filter: FileFilter;
  /** A reason a file was skipped or failed for, or none. */
  code: string | null;
  /** A word in the path, or none. */
  q: string;
  offset: number;
  limit: number;
}

export interface FilesShown {
  files: FolderFile[];
  total: number;
}

export function useFolderFiles(
  folderId: string,
  counts: FolderCounts,
  query: FilesQuery,
  again: string,
) {
  const [shown, setShown] = useState<FilesShown | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [fetching, setFetching] = useState(true);
  const [reads, setReads] = useState(0);
  const latest = useRef(0);
  // What is asked, as text: the requests depend on the counts, and a read is made again only when
  // what is asked or what the folder says has changed.
  const requests = JSON.stringify(fileRequests(query.filter, counts, query.offset, query.limit));
  const { code, q } = query;

  useEffect(() => {
    const mine = ++latest.current;
    const asked = JSON.parse(requests) as FileRequest[];
    const path = (request: FileRequest): string => {
      const params = new URLSearchParams();
      if (request.state) params.set("state", request.state);
      if (code) params.set("code", code);
      if (q.trim()) params.set("q", q.trim());
      params.set("offset", String(request.offset));
      params.set("limit", String(request.limit));
      return `/api/documents/folders/${encodeURIComponent(folderId)}/files?${params}`;
    };
    setFetching(true);
    Promise.all(asked.map((request) => api<FolderFiles>(path(request))))
      .then((pages) => {
        if (mine !== latest.current) return;
        setShown({
          files: pages.flatMap((page) => page.files),
          total: pages.reduce((sum, page) => sum + page.total, 0),
        });
        setProblem(null);
      })
      .catch((failure: unknown) => {
        if (mine === latest.current) setProblem(failureWords(failure));
      })
      .finally(() => {
        if (mine === latest.current) setFetching(false);
      });
  }, [folderId, requests, code, q, again, reads]);

  const reload = useCallback(() => setReads((n) => n + 1), []);
  return { shown, problem, fetching, reload };
}
