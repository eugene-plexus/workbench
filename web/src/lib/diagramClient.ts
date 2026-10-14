/**
 * Diagrams drawn in the background (workbench-rich-content-roadmap.md RC2).
 * Each diagram on the page asks for its picture once; the picture is kept,
 * so an answer being written does not draw its finished diagrams again on
 * every chunk, and the same diagram in another answer or branch is free.
 *
 * Mermaid runs in a worker (diagramWorker.ts), one diagram at a time; one
 * that takes longer than its limit is stopped by ending the worker, says so,
 * and the next goes on in a new one (lib/workerQueue.ts). What the worker
 * sends is read through the page's allowlist (diagramMarkup.ts, loaded with
 * the first diagram).
 *
 * A picture is in the colours of the theme it was drawn for: the theme is
 * part of what is kept, so a change of theme draws each diagram once more.
 */

import { useCallback, useSyncExternalStore } from "react";
import type { Picture } from "./diagramMarkup";
import { workerQueue, type Outcome, type Queue } from "./workerQueue";

export type { Picture };

/** How long one diagram may take once the worker is running (a 200-step flowchart takes 0.2 s). */
export const DIAGRAM_MS = 4_000;
/** How long the worker may take to start (Mermaid is read once per worker). */
export const START_MS = 10_000;

export type Scheme = "light" | "dark";

/** Mermaid's theme variables, from the page's colours. */
export type Palette = Record<string, string>;

/** What the worker sends back for one diagram. */
export type Drawing = { tree: unknown; kind: string } | { error: string };

/** Draws one diagram; null when it was skipped because nobody wants it. */
export interface DiagramEngine {
  draw(
    source: string,
    palette: Palette,
    dark: boolean,
    wanted?: () => boolean,
  ): Promise<Outcome<Drawing> | null>;
}

/** The engine on a worker made by `make`, with its limits. */
export function diagramWorkerEngine(
  make: () => Worker,
  limits: { diagram: number; start: number } = { diagram: DIAGRAM_MS, start: START_MS },
): DiagramEngine {
  const queue: Queue<{ source: string; palette: Palette; dark: boolean }, Drawing> = workerQueue(
    make,
    { job: limits.diagram, start: limits.start },
    {
      worker: "the diagram worker",
      unavailable: "diagrams cannot be drawn in this browser",
      doing: "to draw",
    },
    (reply) =>
      "tree" in reply && typeof reply.kind === "string"
        ? { tree: reply.tree, kind: reply.kind }
        : { error: String(reply.error ?? "no reason was given") },
  );
  return { draw: (source, palette, dark, wanted) => queue.run({ source, palette, dark }, wanted) };
}

// --- the page's colours -----------------------------------------------------------------

const SECTIONS = [
  "--status-info-border",
  "--status-ok-border",
  "--status-warn-border",
  "--status-error-border",
  "--tint-edge",
];

/**
 * Mermaid's theme variables and the page's colour each takes (styles.css):
 * shapes in the tint, their edges in the cool accent, notes edged in the warm
 * one, titles in the heading colour, lines muted.
 */
export const PALETTE_VARIABLES: Record<string, string> = {
  background: "--panel",
  mainBkg: "--tint",
  primaryColor: "--tint",
  primaryTextColor: "--foreground-strong",
  primaryBorderColor: "--accent-left",
  nodeBorder: "--accent-left",
  secondaryColor: "--panel-soft",
  secondaryTextColor: "--foreground-strong",
  secondaryBorderColor: "--border-hover",
  tertiaryColor: "--panel-hover",
  tertiaryTextColor: "--foreground-strong",
  tertiaryBorderColor: "--border-hover",
  lineColor: "--muted",
  textColor: "--foreground",
  titleColor: "--heading",
  edgeLabelBackground: "--panel",
  clusterBkg: "--panel-soft",
  clusterBorder: "--border-hover",
  noteBkgColor: "--panel-soft",
  noteBorderColor: "--accent-right",
  noteTextColor: "--foreground-strong",
  actorBkg: "--tint",
  actorBorder: "--accent-left",
  actorTextColor: "--foreground-strong",
  actorLineColor: "--muted",
  signalColor: "--foreground",
  signalTextColor: "--foreground",
  labelBoxBkgColor: "--tint",
  labelBoxBorderColor: "--accent-left",
  labelTextColor: "--foreground-strong",
  loopTextColor: "--foreground",
  activationBkgColor: "--panel-hover",
  activationBorderColor: "--accent-left",
  sequenceNumberColor: "--on-accent-left",
  pie1: "--accent-left",
  pie2: "--accent-right",
  pie3: "--status-ok-fg",
  pie4: "--status-warn-fg",
  pie5: "--status-error-fg",
  pie6: "--heading",
  pie7: "--accent-active",
  pie8: "--muted",
  pieSectionTextColor: "--on-accent-left",
  pieTitleTextColor: "--heading",
  pieLegendTextColor: "--foreground",
  pieStrokeColor: "--panel",
  pieOuterStrokeColor: "--border-hover",
  // A timeline's sections, a mind map's branches and a Git graph's branches:
  // the status colours' edges, each under the strongest text (5:1 or more in
  // both themes).
  ...Object.fromEntries(
    Array.from({ length: 12 }, (_, i) => [
      [`cScale${i}`, SECTIONS[i % SECTIONS.length] ?? "--tint-edge"],
      [`cScaleLabel${i}`, "--foreground-strong"],
    ]).flat(),
  ),
  ...Object.fromEntries(
    Array.from({ length: 8 }, (_, i) => [
      [`git${i}`, SECTIONS[i % SECTIONS.length] ?? "--tint-edge"],
      [`gitBranchLabel${i}`, "--foreground-strong"],
    ]).flat(),
  ),
};

const HEX = /^#[0-9a-f]{3,8}$/i;

/** The page's colours as Mermaid's theme variables (only hex colours pass). */
export function readPalette(read: (name: string) => string): Palette {
  const palette: Palette = {};
  for (const [variable, property] of Object.entries(PALETTE_VARIABLES)) {
    const value = read(property).trim();
    if (HEX.test(value)) palette[variable] = value;
  }
  return palette;
}

function pagePalette(): Palette {
  if (typeof document === "undefined") return {};
  const style = getComputedStyle(document.documentElement);
  return readPalette((name) => style.getPropertyValue(name));
}

const light =
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)")
    : null;

function listenScheme(listener: () => void): () => void {
  light?.addEventListener("change", listener);
  return () => light?.removeEventListener("change", listener);
}

/** The theme the page is in (it follows the system's). */
export function useScheme(): Scheme {
  return useSyncExternalStore(
    listenScheme,
    () => (light?.matches ? "light" : "dark"),
    () => "dark",
  );
}

// --- what the page shows ----------------------------------------------------------------

export type Shown =
  | { state: "pending" }
  | { state: "done"; picture: Picture }
  | { state: "failed"; reason: string; again?: boolean };

const PENDING: Shown = { state: "pending" };
/**
 * Pictures kept, the least recently shown dropped first: at most this many,
 * and at most this much SVG (one may be 6 MB).
 */
export const KEPT = 100;
export const KEPT_BYTES = 32_000_000;

const kept = new Map<string, Shown>();
let keptBytes = 0;
const listeners = new Map<string, Set<() => void>>();
const asked = new Set<string>();
let engine: DiagramEngine | null | undefined;
let markup: Promise<typeof import("./diagramMarkup")> | undefined;

function defaultEngine(): DiagramEngine | null {
  if (typeof Worker === "undefined") return null;
  return diagramWorkerEngine(
    () => new Worker(new URL("./diagramWorker.ts", import.meta.url), { type: "module" }),
  );
}

/** Tests draw with a stand-in; null leaves diagrams as their source. */
export function setDiagramEngine(next: DiagramEngine | null): void {
  engine = next;
  kept.clear();
  keptBytes = 0;
  asked.clear();
}

/** How many pictures are kept, and their size (for tests). */
export function keptSize(): { count: number; bytes: number } {
  return { count: kept.size, bytes: keptBytes };
}

// The SVG and its address, which holds it again, encoded.
const sizeOf = (shown: Shown | undefined): number =>
  shown?.state === "done" ? shown.picture.svg.length + shown.picture.src.length : 0;

function drop(key: string): void {
  keptBytes -= sizeOf(kept.get(key));
  kept.delete(key);
}

function loadMarkup(): Promise<typeof import("./diagramMarkup")> {
  markup ??= import("./diagramMarkup").catch((error: unknown) => {
    markup = undefined;
    throw error;
  });
  return markup;
}

function settle(key: string, shown: Shown): void {
  asked.delete(key);
  drop(key);
  kept.set(key, shown);
  keptBytes += sizeOf(shown);
  for (const old of kept.keys()) {
    if (kept.size <= KEPT && keptBytes <= KEPT_BYTES) break;
    if (!listeners.get(old)?.size) drop(old);
  }
  for (const listener of listeners.get(key) ?? []) listener();
}

function keyOf(scheme: Scheme, source: string): string {
  return `${scheme}\n${source}`;
}

function ask(key: string): void {
  if (kept.has(key) || asked.has(key)) return;
  asked.add(key);
  if (engine === undefined) engine = defaultEngine();
  if (!engine) {
    settle(key, { state: "failed", reason: "this browser cannot draw diagrams" });
    return;
  }
  const newline = key.indexOf("\n");
  const dark = key.slice(0, newline) === "dark";
  const source = key.slice(newline + 1);
  const palette = pagePalette();
  const wanted = () => (listeners.get(key)?.size ?? 0) > 0;
  Promise.all([engine.draw(source, palette, dark, wanted), loadMarkup()]).then(
    ([result, { toPicture }]) => {
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
          picture: toPicture(result.tree, result.kind, palette.background ?? ""),
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
        reason: `the diagram display could not be loaded: ${error instanceof Error ? error.message : String(error)}`,
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
  const shown = kept.get(key);
  if (shown?.state === "failed" && shown.again) {
    drop(key);
  } else if (shown) {
    kept.delete(key);
    kept.set(key, shown);
  }
  ask(key);
  return () => {
    set.delete(listener);
    if (!set.size) listeners.delete(key);
  };
}

/**
 * A diagram's picture in the page's theme: pending until drawn, then done or
 * failed. Until `wanted` it is not asked for (one already kept is shown).
 */
export function useDiagram(source: string, wanted = true): Shown {
  const scheme = useScheme();
  const key = keyOf(scheme, source);
  const listen = useCallback(
    (listener: () => void) => (wanted ? subscribe(key, listener) : () => undefined),
    [key, wanted],
  );
  return useSyncExternalStore(listen, () => kept.get(key) ?? PENDING);
}
