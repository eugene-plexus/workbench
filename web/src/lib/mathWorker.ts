/**
 * The formula worker: KaTeX, off the page, so a formula that takes too long
 * is stopped by ending the worker (mathClient.ts) and the chat never waits
 * on it. It says when it is ready, then answers one formula at a time.
 */

import { typeset } from "./mathTypeset";

type Scope = {
  postMessage(message: unknown): void;
  onmessage: ((event: MessageEvent<{ id: number; tex: string; display: boolean }>) => void) | null;
};

const scope = self as unknown as Scope;

scope.onmessage = (event) => {
  const { id, tex, display } = event.data;
  scope.postMessage({ id, ...typeset(String(tex), Boolean(display)) });
};
scope.postMessage({ ready: true });
