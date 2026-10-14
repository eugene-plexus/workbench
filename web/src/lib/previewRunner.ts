/**
 * When previews run (workbench-rich-content-roadmap.md RC6). Every preview frame
 * of the page is one Chrome process (measured 2026-10-14: one loops, every
 * other goes silent; the chat itself still answers), so they are run as a
 * group:
 *
 * - **One starts at a time.** A preview that runs by itself (in view, the
 *   person's switch on) waits in line until the one before it has had its
 *   source for `PROBATION_MS`, so a preview that loops as it starts is caught
 *   alone.
 * - **At most `MAX_RUNNING` run,** and the one that left the screen first
 *   makes room.
 * - **When every running preview that has its source falls silent for
 *   `STUCK_MS`,** one of them loops: all are stopped (so Chrome can end the
 *   process), the one the person was in, else the one given its source last,
 *   is named and does not run by itself again (kept in this browser), and the
 *   others start again. A frame that never got its source (it was put in the
 *   stuck process) is never the one named.
 *
 * The previews themselves are components/ArtifactPreview.tsx; this module only
 * decides, and is told what happened.
 */

export const MAX_RUNNING = 4;
export const PROBATION_MS = 2_000;
export const STUCK_MS = 3_000;
export const WATCH_MS = 500;
/** How many stopped-for-not-answering previews this browser remembers. */
const KEPT_STUCK = 100;
const STORE = "workbench.previews.stuck";

/** Why the runner stopped a preview. */
export type StopReason = "room" | "stuck" | "culprit";

export type Slot = {
  /** This preview, while it is on the page. */
  id: string;
  /** What it shows now (its artifact and words): what is remembered when it loops. */
  key: () => string;
  /** Start it (make its frame). */
  start: () => void;
  /** Stop it (take its frame away), saying why. */
  stop: (why: StopReason) => void;
  /** Whether the person is in its frame now. */
  focused: () => boolean;
};

type Entry = {
  slot: Slot;
  order: number;
  wanted: boolean;
  visible: boolean;
  seenAt: number;
  running: boolean;
  startedAt: number;
  /** When its frame said it was ready and was given its source; null before. */
  readyAt: number | null;
  /** When it last said it still answers. */
  answeredAt: number | null;
};

function readStuck(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORE);
    const keys: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(keys) ? keys.filter((k) => typeof k === "string") : []);
  } catch {
    return new Set();
  }
}

function writeStuck(keys: Set<string>): void {
  try {
    window.localStorage.setItem(STORE, JSON.stringify([...keys].slice(-KEPT_STUCK)));
  } catch {
    // Not kept past this page, then.
  }
}

export function createRunner(
  now: () => number = () => performance.now(),
  hidden: () => boolean = () => typeof document !== "undefined" && document.hidden,
) {
  const entries = new Map<string, Entry>();
  let stuck = readStuck();
  let order = 0;
  let watch: number | undefined;
  let lastTick: number | null = null;

  const running = () => [...entries.values()].filter((e) => e.running);

  function watching(): void {
    const any = running().length > 0;
    if (any && watch === undefined) {
      lastTick = now();
      watch = window.setInterval(tick, WATCH_MS);
    }
    if (!any && watch !== undefined) {
      window.clearInterval(watch);
      watch = undefined;
      lastTick = null;
    }
  }

  /** Every running preview counted as heard now: what was silent was the page, not them. */
  function forgive(t: number): void {
    for (const e of running()) if (e.readyAt !== null) e.answeredAt = t;
  }

  function begin(entry: Entry): void {
    entry.running = true;
    entry.wanted = false;
    entry.startedAt = now();
    entry.readyAt = null;
    entry.answeredAt = null;
  }

  /** When a preview with its source was last heard from. */
  const heard = (e: Entry) => Math.max(e.readyAt ?? 0, e.answeredAt ?? 0);

  /** The running preview longest out of view, to make room; none when all are in view. */
  function room(except: Entry): boolean {
    const others = running().filter((e) => e !== except);
    if (others.length < MAX_RUNNING) return true;
    const victim = others.filter((e) => !e.visible).sort((a, b) => a.seenAt - b.seenAt)[0];
    if (!victim) return false;
    victim.running = false;
    victim.slot.stop("room");
    return true;
  }

  function pump(): void {
    const t = now();
    // The one before has not had its source long enough (or not at all).
    if (running().some((e) => e.readyAt === null || t - e.readyAt < PROBATION_MS)) return;
    const next = [...entries.values()]
      .filter((e) => e.wanted && e.visible && !e.running && !stuck.has(e.slot.key()))
      .sort((a, b) => a.order - b.order)[0];
    if (!next || !room(next)) return;
    begin(next);
    next.slot.start();
    watching();
  }

  function tick(): void {
    const live = running();
    const given = live.filter((e) => e.readyAt !== null);
    const t = now();
    // A hidden tab, a long task of the page's own, or a machine asleep stops the
    // previews' beat and this watch alike: silence across such a gap says nothing.
    const gap = lastTick === null ? 0 : t - lastTick;
    lastTick = t;
    if (hidden() || gap > 2 * WATCH_MS) {
      forgive(t);
    } else if (given.length && given.every((e) => t - heard(e) > STUCK_MS)) {
      const culprit =
        given.find((e) => e.slot.focused()) ??
        [...given].sort((a, b) => (b.readyAt ?? 0) - (a.readyAt ?? 0))[0];
      for (const e of live) {
        e.running = false;
        if (e === culprit) {
          stuck.add(e.slot.key());
          writeStuck(stuck);
          e.slot.stop("culprit");
        } else {
          e.slot.stop("stuck");
          e.wanted = e.visible;
        }
      }
    }
    pump();
    watching();
  }

  return {
    register(slot: Slot): () => void {
      entries.set(slot.id, {
        slot,
        order: order++,
        wanted: false,
        visible: false,
        seenAt: 0,
        running: false,
        startedAt: 0,
        readyAt: null,
        answeredAt: null,
      });
      return () => {
        entries.delete(slot.id);
        pump();
        watching();
      };
    },
    /** It may run by itself when in view (the person's switch on, not stopped by them). */
    want(id: string, wanted: boolean): void {
      const e = entries.get(id);
      if (!e) return;
      e.wanted = wanted && !e.running;
      pump();
    },
    visible(id: string, visible: boolean): void {
      const e = entries.get(id);
      if (!e) return;
      // When it came into view, or left it: the one that left first makes room first.
      if (e.visible !== visible) e.seenAt = now();
      e.visible = visible;
      pump();
    },
    /**
     * The person pressed Run: it runs now, out of line and past the cap when every
     * running one is on screen (their choice), and is no longer remembered as stuck.
     */
    started(id: string): void {
      const e = entries.get(id);
      if (!e) return;
      if (stuck.delete(e.slot.key())) writeStuck(stuck);
      room(e);
      begin(e);
      watching();
    },
    /** It stopped by itself, or the person stopped it. */
    stopped(id: string): void {
      const e = entries.get(id);
      if (!e) return;
      e.running = false;
      e.wanted = false;
      pump();
      watching();
    },
    /** Its frame said it was ready, and was given its source. */
    ready(id: string): void {
      const e = entries.get(id);
      if (e?.running) e.readyAt = now();
    },
    alive(id: string): void {
      const e = entries.get(id);
      if (e?.running) e.answeredAt = now();
    },
    isStuck(key: string): boolean {
      return stuck.has(key);
    },
    /** The tab came back: its previews are heard afresh. */
    shown(): void {
      lastTick = now();
      forgive(lastTick);
    },
    /** Every remembered loop forgotten (signing out), and the running stopped watching. */
    reset(): void {
      stuck = new Set();
      writeStuck(stuck);
    },
    tick,
  };
}

export type Runner = ReturnType<typeof createRunner>;

/** The page's one runner. */
export const runner: Runner = createRunner();

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) runner.shown();
  });
}
