/**
 * The files a chat's next message carries, as the composer shows them (MR2).
 *
 * What the page holds is only what a person attached here; what the server
 * says of each (how it was read, and what a request to the picked model would
 * do with it) is read from `GET /api/chats/{id}/files?model=`. That is read:
 *
 * - every second while any document is still being read, and not again once none
 *   is (the server sends no event for it);
 * - again when the model is changed, because every decision is for a model;
 * - after a choice, a removal, or a refused send, because the files are decided
 *   together, in order, as one message.
 *
 * A read that is overtaken by a later one is dropped, so a slow answer never
 * puts back what a newer one replaced. A read that fails keeps what is shown and
 * says why beside it. An unsent file the server has and the page does not (the
 * tab was reloaded after attaching) is added, so the page never hides a file the
 * server counts and Send carries.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { api, put } from "./api";
import {
  announceWords,
  type ChipState,
  chipState,
  type Facts,
  isDocument,
  isReading,
  refusalFor,
  type RefusedFile,
} from "./documents";
import type { ChatFile, ChatFiles, FilesModel, ReadingChoice } from "./types";

/** The first read comes soon, because most files are read in a moment. */
export const FIRST_POLL_MS = 500;
export const POLL_MS = 1000;
/** After a read that failed, a pause before the next. */
export const RETRY_MS = 3000;

/** A sentence a chip shows beside its file: what was refused, and why. */
export interface Problem {
  title: string;
  text: string;
}

const without = <T>(rows: Record<string, T>, id: string): Record<string, T> => {
  const rest = { ...rows };
  delete rest[id];
  return rest;
};

/** The server's answer put into the files the page holds: its version of each
 * file wins, a file it does not list yet is kept, one it says was sent is gone,
 * and an unsent file the page has never seen is added (once: `known` remembers
 * every file the page has held, removed or sent, so none comes back). */
export function merge(current: ChatFile[], server: ChatFile[], known: Set<string>): ChatFile[] {
  const theirs = new Map(server.map((file) => [file.id, file]));
  const next: ChatFile[] = [];
  for (const mine of current) {
    const found = theirs.get(mine.id);
    if (!found) next.push(mine);
    else if (!found.sent) next.push({ ...mine, ...found });
  }
  const held = new Set(current.map((file) => file.id));
  for (const file of server) {
    if (file.sent || held.has(file.id) || known.has(file.id)) continue;
    known.add(file.id);
    next.push(file);
  }
  return next;
}

/** Why a refused send names a file, in the file's own words. */
function refusedWords(refused: RefusedFile): string {
  const decision = refused.decision;
  if (!decision || decision.kind === "pending") {
    return "This file was still being read. Try again in a moment.";
  }
  return decision.message;
}

export function useChatFiles(chatId: string, model: string | null, handed: ChatFile[] = []) {
  const [files, setFiles] = useState<ChatFile[]>(handed);
  const [facts, setFacts] = useState<Facts | null>(null);
  const [problems, setProblems] = useState<Record<string, Problem>>({});
  const [choosing, setChoosing] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The list as last set, so a change applies to it and never to a copy that a
  // callback closed over before an upload or a read landed.
  const current = useRef(files);
  const known = useRef(new Set(handed.map((file) => file.id)));
  const latest = useRef(0);
  const alive = useRef(true);
  const failing = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const commit = useCallback((next: ChatFile[]) => {
    current.current = next;
    setFiles(next);
  }, []);

  const base = `/api/chats/${encodeURIComponent(chatId)}/files`;
  const query = model ? `?model=${encodeURIComponent(model)}` : "";

  const reload = useCallback(
    async (options: { checking?: boolean } = {}) => {
      const mine = ++latest.current;
      if (options.checking) setChecking(true);
      try {
        const got = await api<ChatFiles | undefined>(`${base}${query}`);
        if (!alive.current || mine !== latest.current) return;
        // A server that answered with something else tells us nothing.
        if (!got || !Array.isArray(got.files)) return;
        setFacts({ model: got.model, request: got.request ?? null });
        commit(merge(current.current, got.files, known.current));
        failing.current = false;
        setError(null);
      } catch (failure) {
        if (!alive.current || mine !== latest.current) return;
        failing.current = true;
        setError(failure instanceof Error ? failure.message : String(failure));
      } finally {
        if (alive.current && mine === latest.current) setChecking(false);
      }
    },
    [base, query, commit],
  );

  // While a document is being read, ask again until it is not.
  const watching = files.some((file) => isDocument(file) && isReading(file));
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

  // Every decision is for a model: a different one, and they are asked again.
  const asked = useRef(model);
  useEffect(() => {
    if (asked.current === model) return;
    asked.current = model;
    if (current.current.some(isDocument)) void reload({ checking: true });
  }, [model, reload]);

  /** A file that was just uploaded. */
  const add = useCallback(
    (file: ChatFile) => {
      known.current.add(file.id);
      commit([...current.current.filter((held) => held.id !== file.id), file]);
    },
    [commit],
  );

  /** A file that was taken back: the ones after it have more room. */
  const drop = useCallback(
    (id: string) => {
      known.current.add(id);
      commit(current.current.filter((file) => file.id !== id));
      setProblems((now) => without(now, id));
      if (current.current.some(isDocument)) void reload();
    },
    [commit, reload],
  );

  /** The message was sent: its files are the chat's, and not this message's. */
  const clear = useCallback(() => {
    for (const file of current.current) known.current.add(file.id);
    commit([]);
    setProblems({});
    setFacts(null);
  }, [commit]);

  const dismiss = useCallback((id: string) => setProblems((now) => without(now, id)), []);

  /** Choose how much of a document to read (`null` clears the choice). The server
   * keeps it with the file; a refusal says why, and nothing changes. */
  const choose = useCallback(
    async (file: ChatFile, choice: ReadingChoice | null) => {
      setChoosing(file.id);
      setProblems((now) => without(now, file.id));
      try {
        const item = await put<ChatFile | undefined>(
          `${base}/${encodeURIComponent(file.id)}/reading${query}`,
          choice ?? { mode: null },
        );
        if (item && typeof item.id === "string") {
          commit(
            current.current.map((held) => (held.id === item.id ? { ...held, ...item } : held)),
          );
        }
        await reload();
      } catch (failure) {
        const refused = refusalFor(failure, "choice");
        const text =
          refused?.message ?? (failure instanceof Error ? failure.message : String(failure));
        setProblems((now) => ({ ...now, [file.id]: { title: "Your choice was not kept", text } }));
        void reload();
      } finally {
        setChoosing(null);
      }
    },
    [base, query, commit, reload],
  );

  /** A send was refused for these files: each says why, on its own chip. */
  const refuse = useCallback(
    (refused: RefusedFile[]) => {
      commit(
        current.current.map((file) => {
          const named = refused.find((row) => row.id === file.id);
          return named?.decision ? { ...file, decision: named.decision } : file;
        }),
      );
      setProblems((now) => {
        const next = { ...now };
        for (const row of refused) {
          next[row.id] = { title: "Your message was not sent", text: refusedWords(row) };
        }
        return next;
      });
      void reload();
    },
    [commit, reload],
  );

  return {
    files,
    facts,
    problems,
    choosing,
    checking,
    error,
    reload,
    add,
    drop,
    clear,
    choose,
    refuse,
    dismiss,
  };
}

/** What a screen reader is told as files change: a file that has been read (or
 * that the model picked now cannot take), with what it comes to. A file that
 * only appeared and is still being read is not news; the upload says so. */
export function useFileAnnouncements(files: ChatFile[], model: FilesModel | null): string {
  const [said, setSaid] = useState("");
  const seen = useRef(new Map<string, ChipState>());
  useEffect(() => {
    const now = new Map<string, ChipState>();
    const lines: string[] = [];
    for (const file of files) {
      if (!isDocument(file)) continue;
      const state = chipState(file);
      now.set(file.id, state);
      if (state !== "reading" && seen.current.get(file.id) !== state) {
        lines.push(announceWords(file, model));
      }
    }
    seen.current = now;
    if (lines.length > 0) setSaid(lines.join(" "));
  }, [files, model]);
  return said;
}
