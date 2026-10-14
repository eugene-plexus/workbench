/**
 * Formulas laid out in the background (workbench-rich-content-roadmap.md
 * RC1). Each formula on the page asks for its layout once; the answer is
 * kept, so an answer being written does not lay out its finished formulas
 * again on every chunk, and the same formula in another answer is free.
 *
 * KaTeX runs in a worker, one formula at a time. A formula that takes longer
 * than its limit is stopped by ending the worker, says so, and a new worker
 * takes the next one: the page never waits on a layout. A formula nobody on
 * the page still shows is skipped when its turn comes. A worker that would
 * not start, or stopped, fails the formula for now: shown again, it is tried
 * again.
 */

import { useCallback, useSyncExternalStore, type ReactNode } from "react";
import type { Typeset } from "./mathTypeset";

export type { Typeset };

/** What laying out a formula came to; `again` when the next try may differ. */
export type Outcome = Typeset | { error: string; again: true };

/** Lays out one formula; null when it was skipped because nobody wants it. */
export interface Engine {
  typeset(tex: string, display: boolean, wanted?: () => boolean): Promise<Outcome | null>;
}

/** How long one formula may take, once the worker is running. */
export const FORMULA_MS = 1_500;
/** How long the worker may take to start (KaTeX is read once per worker). */
export const START_MS = 10_000;

type Job = {
  id: number;
  tex: string;
  display: boolean;
  wanted?: () => boolean;
  resolve: (result: Outcome | null) => void;
};

type Reply = { ready?: boolean; id?: number; markup?: string; error?: string };

function seconds(ms: number): string {
  return `${(ms / 1000).toLocaleString("en")} s`;
}

/** The engine on a worker made by `make`, with its limits. */
export function workerEngine(
  make: () => Worker,
  limits: { formula: number; start: number } = { formula: FORMULA_MS, start: START_MS },
): Engine {
  const queue: Job[] = [];
  let worker: Worker | null = null;
  let ready = false;
  let running: Job | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let next = 1;

  function end(): void {
    clearTimeout(timer);
    worker?.terminate();
    worker = null;
    ready = false;
  }

  /**
   * The running formula fails with `reason` (`again` if the worker, not the
   * formula, was at fault); a new worker takes the rest.
   */
  function failRunning(reason: string, again: boolean): void {
    const job = running;
    running = null;
    end();
    job?.resolve(again ? { error: reason, again } : { error: reason });
    pump();
  }

  /** The worker never started: every formula waiting fails with `reason`, for now. */
  function failStart(reason: string): void {
    end();
    for (const job of queue.splice(0)) job.resolve({ error: reason, again: true });
  }

  function start(): void {
    try {
      worker = make();
    } catch (error) {
      failStart(`formulas cannot be laid out in this browser: ${String(error)}`);
      return;
    }
    worker.onmessage = (event: MessageEvent<Reply>) => {
      const reply = event.data;
      if (reply.ready) {
        clearTimeout(timer);
        ready = true;
        pump();
        return;
      }
      const job = running;
      if (!job || reply.id !== job.id) return;
      clearTimeout(timer);
      running = null;
      job.resolve(
        typeof reply.markup === "string"
          ? { markup: reply.markup }
          : { error: String(reply.error ?? "no reason was given") },
      );
      pump();
    };
    worker.onerror = (event: ErrorEvent) => {
      event.preventDefault();
      const reason = `the formula worker stopped: ${event.message || "no reason was given"}`;
      if (running) failRunning(reason, true);
      else failStart(reason);
    };
    timer = setTimeout(
      () => failStart(`the formula worker did not start within ${seconds(limits.start)}`),
      limits.start,
    );
  }

  function pump(): void {
    if (running) return;
    if (!worker) {
      if (queue.length) start();
      return;
    }
    if (!ready) return;
    let job = queue.shift();
    while (job?.wanted && !job.wanted()) {
      job.resolve(null);
      job = queue.shift();
    }
    if (!job) return;
    running = job;
    worker.postMessage({ id: job.id, tex: job.tex, display: job.display });
    timer = setTimeout(
      () => failRunning(`it took longer than ${seconds(limits.formula)} to lay out`, false),
      limits.formula,
    );
  }

  return {
    typeset(tex, display, wanted) {
      return new Promise((resolve) => {
        queue.push({ id: next++, tex, display, wanted, resolve });
        pump();
      });
    },
  };
}

// --- what the page shows ---------------------------------------------------------

export type Formula =
  | { state: "pending" }
  | { state: "done"; node: ReactNode; size: number }
  | { state: "failed"; reason: string; again?: boolean };

const PENDING: Formula = { state: "pending" };
/**
 * Laid-out formulas kept, the least recently shown dropped first: at most
 * this many, and at most this much markup (one formula may be 600 KB).
 */
export const KEPT = 500;
export const KEPT_BYTES = 16_000_000;

const kept = new Map<string, Formula>();
let keptBytes = 0;
const listeners = new Map<string, Set<() => void>>();
const asked = new Set<string>();
let engine: Engine | null | undefined;
let view: Promise<typeof import("./mathView")> | undefined;

function defaultEngine(): Engine | null {
  if (typeof Worker === "undefined") return null;
  return workerEngine(
    () => new Worker(new URL("./mathWorker.ts", import.meta.url), { type: "module" }),
  );
}

/** Tests lay formulas out on the page; null leaves formulas as their source. */
export function setMathEngine(next: Engine | null): void {
  engine = next;
  kept.clear();
  keptBytes = 0;
  asked.clear();
}

/** How many formulas are kept, and their markup's size (for tests). */
export function keptSize(): { count: number; bytes: number } {
  return { count: kept.size, bytes: keptBytes };
}

function drop(key: string): void {
  const formula = kept.get(key);
  if (formula?.state === "done") keptBytes -= formula.size;
  kept.delete(key);
}

function loadView(): Promise<typeof import("./mathView")> {
  view ??= import("./mathView").catch((error: unknown) => {
    view = undefined;
    throw error;
  });
  return view;
}

function settle(key: string, formula: Formula): void {
  asked.delete(key);
  drop(key);
  kept.set(key, formula);
  if (formula.state === "done") keptBytes += formula.size;
  for (const old of kept.keys()) {
    if (kept.size <= KEPT && keptBytes <= KEPT_BYTES) break;
    if (!listeners.get(old)?.size) drop(old);
  }
  for (const listener of listeners.get(key) ?? []) listener();
}

function ask(key: string): void {
  if (kept.has(key) || asked.has(key)) return;
  asked.add(key);
  if (engine === undefined) engine = defaultEngine();
  if (!engine) {
    settle(key, { state: "failed", reason: "this browser cannot lay out formulas" });
    return;
  }
  const display = key[0] === "D";
  const tex = key.slice(1);
  const wanted = () => (listeners.get(key)?.size ?? 0) > 0;
  Promise.all([engine.typeset(tex, display, wanted), loadView()]).then(
    ([result, { toReact }]) => {
      if (!result) {
        asked.delete(key);
        // Shown again while it was being skipped: ask once more.
        if (listeners.get(key)?.size) ask(key);
        return;
      }
      if ("error" in result) {
        settle(key, { state: "failed", reason: result.error, again: "again" in result });
        return;
      }
      try {
        settle(key, {
          state: "done",
          node: toReact(result.markup),
          size: result.markup.length,
        });
      } catch (error) {
        settle(key, {
          state: "failed",
          reason: error instanceof Error ? error.message : String(error),
        });
      }
    },
    (error: unknown) => {
      settle(key, {
        state: "failed",
        reason: `the formula display could not be loaded: ${error instanceof Error ? error.message : String(error)}`,
        again: true,
      });
    },
  );
}

function subscribe(key: string, listener: () => void): () => void {
  let set = listeners.get(key);
  if (!set) listeners.set(key, (set = new Set()));
  set.add(listener);
  // Shown again: the most recently shown are kept longest, and one that
  // failed for the worker's sake rather than its own is tried again.
  const formula = kept.get(key);
  if (formula?.state === "failed" && formula.again) {
    drop(key);
  } else if (formula) {
    kept.delete(key);
    kept.set(key, formula);
  }
  ask(key);
  return () => {
    set.delete(listener);
    if (!set.size) listeners.delete(key);
  };
}

/** A formula's layout: pending until the worker answers, then done or failed. */
export function useFormula(tex: string, display: boolean): Formula {
  const key = (display ? "D" : "I") + tex;
  const listen = useCallback((listener: () => void) => subscribe(key, listener), [key]);
  return useSyncExternalStore(listen, () => kept.get(key) ?? PENDING);
}
