import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, checked, headers } from "./api";
import { words, type Json, type ObjectValue } from "./interactions";

export interface Agent {
  id: string;
  name: string;
  url: string;
  subjects: string[];
}
export interface Run {
  threadId: string;
  runId: string;
  direction: "serve" | "connect";
  agentId: string | null;
  status: string;
  cursor: number;
  messages: ObjectValue[];
  state: Json;
  answerId: string | null;
  outcome: { type: string; interrupts?: ObjectValue[] } | null;
}
export const aguiPath = (chat: string) => `/api/v1/chats/${encodeURIComponent(chat)}/ag-ui`;
export function runInput(
  chat: string,
  previous: Run | null,
  content: string,
  resume?: ObjectValue[],
): ObjectValue {
  return {
    threadId: chat,
    runId: crypto.randomUUID(),
    ...(previous ? { parentRunId: previous.runId } : {}),
    ...(previous?.state === null ? {} : { state: previous?.state ?? {} }),
    messages: [
      ...(previous?.messages ?? []),
      ...(resume ? [] : [{ id: crypto.randomUUID(), role: "user", content }]),
    ],
    ...(resume ? { resume } : {}),
    tools: [],
    context: [],
    forwardedProps: {},
  };
}

/** The backend owns the reducer and replay ledger. Reconnection only reads it. */
export function useAGUI(chat: string, enabled: boolean, subject: string) {
  const [run, setRun] = useState<Run | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [unclear, setUnclear] = useState(false);
  const current = useRef<Run | null>(null);
  const reading = useRef(0);
  const mounted = useRef(false);
  const pendingKey = `agui-start:${subject}:${chat}`;
  const reload = useCallback(async () => {
    const sequence = ++reading.current;
    let next: Run | null;
    try {
      next = (await api<Run | null>(aguiPath(chat))) ?? null;
    } catch (error) {
      if (sequence === reading.current) throw error;
      return;
    }
    if (sequence !== reading.current) return;
    if (next !== null && typeof next.runId !== "string")
      throw new Error("Workbench returned an invalid agent run.");
    if (!mounted.current) return;
    current.current = next;
    setRun(next);
    setLoaded(true);
    const pending = sessionStorage.getItem(pendingKey);
    if (pending && next?.runId === pending) sessionStorage.removeItem(pendingKey);
    setUnclear(sessionStorage.getItem(pendingKey) !== null);
    setError(null);
  }, [chat, pendingKey]);
  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await reload();
      } catch (error) {
        if (mounted.current) {
          setError(words(error));
          setLoaded(false);
        }
      }
      if (mounted.current) timer = setTimeout(() => void poll(), 1000);
    };
    void poll();
    return () => {
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [enabled, reload]);

  async function start(source: string, content: string, resume?: ObjectValue[]) {
    if (sending || !loaded || unclear) throw new Error("Wait for the saved run to load.");
    setSending(true);
    setError(null);
    const payload = runInput(chat, current.current, content, resume);
    sessionStorage.setItem(pendingKey, String(payload.runId));
    try {
      const url =
        source === "native"
          ? `${aguiPath(chat)}/run`
          : `${aguiPath(chat)}/agents/${encodeURIComponent(source)}/run`;
      const response = await checked(
        await fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: headers({ "Content-Type": "application/json" }),
          body: JSON.stringify(payload),
        }),
      );
      // The POST has durably claimed the run. Closing this response does not stop it.
      await response.body?.cancel();
      await reload();
    } catch (error) {
      if (error instanceof ApiError) sessionStorage.removeItem(pendingKey);
      setUnclear(sessionStorage.getItem(pendingKey) !== null);
      setError(words(error));
      throw error;
    } finally {
      setSending(false);
    }
  }
  return { run, loaded, error, sending, unclear, start, reload };
}
