/**
 * Watching a chat: its answers as they arrive (workbench-v1.md W1).
 *
 * Server-sent events read with `fetch` rather than `EventSource`, because
 * `EventSource` cannot send the request secret (session.ts). A stream that
 * drops is opened again after a pause, and the page reloads the chat
 * first, so nothing that arrived meanwhile is missed.
 */

import { announce, checked, headers, SignedOut } from "./api";
import type { ChatEvent } from "./types";

export interface Watching {
  close(): void;
}

/** Splits a text stream into SSE `data:` payloads. */
export function parseFrames(buffer: string): { events: string[]; rest: string } {
  const events: string[] = [];
  let rest = buffer.replace(/\r\n/g, "\n");
  let end = rest.indexOf("\n\n");
  while (end !== -1) {
    const frame = rest.slice(0, end);
    rest = rest.slice(end + 2);
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (data) events.push(data);
    end = rest.indexOf("\n\n");
  }
  return { events, rest };
}

export function watch(
  chatId: string,
  onEvent: (event: ChatEvent) => void,
  onReopen: () => void,
): Watching {
  return watchStream<ChatEvent>(
    `/api/chats/${encodeURIComponent(chatId)}/events`,
    onEvent,
    onReopen,
  );
}

/** Any of Workbench's event streams: a chat's, or a person's media. */
export function watchStream<E extends { type: string }>(
  url: string,
  onEvent: (event: E) => void,
  onReopen: () => void,
): Watching {
  const controller = new AbortController();
  let closed = false;

  async function run(first: boolean): Promise<void> {
    if (!first) onReopen();
    try {
      const response = await checked(
        await fetch(url, {
          credentials: "same-origin",
          headers: headers(),
          signal: controller.signal,
        }),
      );
      const reader = response.body!.pipeThrough(new TextDecoderStream()).getReader();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        const parsed = parseFrames(buffer + value);
        buffer = parsed.rest;
        for (const data of parsed.events) {
          const event = JSON.parse(data) as
            E | { type: "signed-out"; message: string; reason: string };
          if (event.type === "signed-out") {
            const out = event as { message: string; reason: string };
            announce(new SignedOut(out.message, out.reason));
            return;
          }
          onEvent(event as E);
        }
      }
    } catch (error) {
      if (closed || error instanceof SignedOut) return;
    }
    if (!closed) window.setTimeout(() => void run(false), 1500);
  }

  void run(true);
  return {
    close() {
      closed = true;
      controller.abort();
    },
  };
}
