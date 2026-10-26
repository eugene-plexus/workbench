/**
 * A page or a component an answer wrote, run in a frame of its own
 * (workbench-rich-content-roadmap.md RC5): `/preview/frame`, a page with no
 * origin and no network (web.py `PREVIEW_CSP`, preview/runtime.ts). The frame
 * is `sandbox="allow-scripts allow-forms"` (a form's own script runs; its
 * sending never does) and never `allow-same-origin`: with scripts, that could
 * lift the sandbox off its own frame.
 *
 * In an answer it runs by itself once it is on screen (RC6), unless the
 * person turned that off or stopped it; in the artifact's dialog, and in the
 * owner's view of someone else's chat, it waits for Run. When it runs is the
 * page's one runner's to say (lib/previewRunner.ts): every preview of the page
 * is one Chrome process, so they start one at a time, at most a few run, and
 * one that loops is found and stopped with the others.
 *
 * This page and the frame speak only in messages, and only to each other: a
 * message is heard when it comes from this frame's own window, and is read
 * as words. The source goes to the frame once, when it says it is ready. The
 * frame says it still answers every half second, and how tall its page is;
 * in an answer the frame takes that height, up to most of the screen.
 *
 * Stop removes the frame, and with it whatever runs there. Chrome keeps a
 * process stuck in a loop after its frames are gone (measured 2026-10-14: a
 * whole core for 5 s after Stop, none 15 s after) and puts the next preview
 * in it, where it never starts. So a frame that has not said it is ready in
 * `READY_WITHIN_MS` is replaced, up to `TRIES` frames, and the person is told
 * why it waits. A preview that is ready but not started in `START_WITHIN_MS`
 * (one that loops before it shows anything) is stopped.
 */

import { Play, RotateCw, Square } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { digest } from "../lib/artifacts";
import {
  markPreviewFinished,
  markPreviewStarted,
  previewWasUnfinished,
} from "../lib/previewMarkers";
import { runner, type StopReason } from "../lib/previewRunner";

/** Where a preview runs; the server gives it its own policy there. */
export const PREVIEW_FRAME = "/preview/frame";
/** A frame that has not said it is ready by then is replaced, so many times. */
export const READY_WITHIN_MS = 3_000;
export const TRIES = 7;
/** A preview that is ready and has not started by then is stopped. */
export const START_WITHIN_MS = 10_000;
/** A frame in an answer is at least this tall, and at most this share of the window. */
export const LEAST_HEIGHT = 120;
export const MOST_HEIGHT = 0.7;
/** What is said of a preview: its last few messages. */
const NOTES = 8;
/**
 * A frame is read so often a second and no more (a preview that floods the page
 * with messages is not read past it), and its height changes so often, by so
 * much at least.
 */
export const MESSAGES_PER_SECOND = 40;
export const SIZE_EVERY_MS = 200;
const SIZE_STEP = 4;
/** How a refusal begins: what the preview tried and was not let do. */
const REFUSED = "Refused: ";

type Said =
  | { type: "ready" }
  | { type: "started"; nonce: string }
  | { type: "failed"; nonce: string; message: string }
  | { type: "error"; message: string }
  | { type: "refused"; what: string }
  | { type: "leaving" }
  | { type: "alive" }
  | { type: "size"; height: number };

/** A message from the frame, as one of the eight it may send; anything else is nothing. */
export function readSaid(data: unknown): Said | null {
  if (typeof data !== "object" || data === null) return null;
  const said = data as Record<string, unknown>;
  const text = (value: unknown) => (typeof value === "string" ? value.slice(0, 500) : "");
  switch (said.type) {
    case "ready":
    case "leaving":
    case "alive":
      return { type: said.type };
    case "started":
      return typeof said.nonce === "string" ? { type: "started", nonce: said.nonce } : null;
    case "failed":
      return typeof said.nonce === "string"
        ? { type: "failed", nonce: said.nonce, message: text(said.message) || "it failed" }
        : null;
    case "error":
      return { type: "error", message: text(said.message) || "an error" };
    case "refused":
      return { type: "refused", what: text(said.what) || "something" };
    case "size":
      return typeof said.height === "number" && Number.isFinite(said.height)
        ? { type: "size", height: Math.max(0, Math.min(said.height, 100_000)) }
        : null;
    default:
      return null;
  }
}

/** The height a frame in an answer takes for a page this tall. */
export function frameHeight(page: number, windowHeight: number): number {
  return Math.round(Math.max(LEAST_HEIGHT, Math.min(page, windowHeight * MOST_HEIGHT)));
}

function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

type Running = { id: number; tries: number; state: "starting" | "running" };

export function ArtifactPreview({
  chatId,
  kind,
  source,
  title,
  blockKey,
  inline = false,
  auto = false,
}: {
  chatId: string;
  kind: "html" | "react";
  source: string;
  title: string;
  /** The artifact it shows (message, kind, words); with the words shown, what is
   * remembered if it loops. */
  blockKey: string;
  /** Drawn in an answer: its frame takes its page's height. */
  inline?: boolean;
  /** Runs by itself when on screen. */
  auto?: boolean;
}) {
  const slot = useId();
  // Remembered when it loops, by the words it runs: a revision that fixes it runs.
  const runKey = useMemo(() => `${blockKey}:${digest(source)}`, [blockKey, source]);
  const markerKey = JSON.stringify([chatId, runKey]);
  const [unfinished, setUnfinished] = useState(() => previewWasUnfinished(markerKey));
  const [run, setRun] = useState<Running | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  // Stopped by the person, or for not answering: it waits for Run.
  const [held, setHeld] = useState(() => unfinished || runner.isStuck(runKey));
  const [height, setHeight] = useState<number | null>(null);
  const [viewport, setViewport] = useState(() => window.innerHeight);
  const root = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const runs = useRef(0);
  const keepMarker = useRef(false);
  const noun = kind === "html" ? "page" : "component";

  const note = (words: string) =>
    setNotes((was) => (was.includes(words) ? was : [...was, words].slice(-NOTES)));
  const begin = () => {
    keepMarker.current = false;
    setUnfinished(false);
    setNotes([]);
    setHeight(null);
    runs.current += 1;
    setRun({ id: runs.current, tries: 1, state: "starting" });
  };
  // The person's Run or Reload: now, out of line.
  const start = () => {
    setHeld(false);
    runner.started(slot);
    begin();
  };
  const ended = () => {
    setRun(null);
    runner.stopped(slot);
  };
  const stop = () => {
    keepMarker.current = true;
    setHeld(true);
    ended();
  };

  // What the runner asks of this preview, with the latest of each.
  const told = useRef({ begin, note, noun, runKey });
  useEffect(() => {
    told.current = { begin, note, noun, runKey };
  });
  useEffect(
    () =>
      runner.register({
        id: slot,
        key: () => told.current.runKey,
        start: () => told.current.begin(),
        stop: (why: StopReason) => {
          setRun(null);
          const { note: say, noun: what } = told.current;
          if (why === "room") {
            say(`Stopped to make room for the previews on screen; it runs again when shown.`);
          } else if (why === "stuck") {
            say(`Stopped while another preview was not answering; it starts again by itself.`);
          } else {
            setHeld(true);
            say(
              `The ${what} stopped answering (a loop?), and was stopped with the previews ` +
                "beside it. It no longer runs by itself: press Run to try it again.",
            );
          }
        },
        focused: () => frame.current !== null && document.activeElement === frame.current,
      }),
    [slot],
  );

  // On screen or not, for the runner.
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      runner.visible(slot, true);
      return;
    }
    const observer = new IntersectionObserver((seen) =>
      runner.visible(
        slot,
        seen.some((s) => s.isIntersecting),
      ),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [slot]);

  const idle = run === null;
  useEffect(() => {
    runner.want(slot, auto && !held && idle);
  }, [slot, auto, held, idle]);

  // The window's height, for a frame's most (a phone turned, its keyboard shown).
  useEffect(() => {
    if (!inline) return;
    const resized = () => setViewport(window.innerHeight);
    window.addEventListener("resize", resized);
    return () => window.removeEventListener("resize", resized);
  }, [inline]);

  // The frame's height, by CSSOM (the page's policy refuses a style attribute).
  useEffect(() => {
    const element = frame.current;
    if (!inline || !element) return;
    element.style.height = height === null ? "" : `${frameHeight(height, viewport)}px`;
  }, [inline, height, viewport, run]);

  const id = run?.id;
  const tries = run?.tries ?? 0;
  useEffect(() => {
    if (id === undefined) return;
    const nonce = newNonce();
    let sent = false;
    let started = false;
    let late: number | undefined;
    let counted = 0;
    let countedFrom = performance.now();
    let shownHeight = -1;
    let nextHeight = 0;
    let sizing: number | undefined;
    const finish = () => {
      setRun((was) => (was?.id === id ? null : was));
      runner.stopped(slot);
    };
    const heard = (event: MessageEvent) => {
      const own = frame.current?.contentWindow;
      if (!own || event.source !== own) return;
      const now = performance.now();
      if (now - countedFrom > 1_000) {
        countedFrom = now;
        counted = 0;
      }
      if (++counted > MESSAGES_PER_SECOND) return;
      const said = readSaid(event.data);
      if (!said) return;
      switch (said.type) {
        case "ready":
          if (sent) return;
          sent = true;
          // Before the frame can run: a loop may give the chat no more turns.
          markPreviewStarted(markerKey);
          own.postMessage({ type: "run", nonce, kind, source }, "*");
          runner.ready(slot);
          late = window.setTimeout(() => {
            if (started) return;
            keepMarker.current = true;
            finish();
            setHeld(true);
            note(
              `The ${noun} did not start within ${START_WITHIN_MS / 1000} seconds, and was stopped.`,
            );
          }, START_WITHIN_MS);
          return;
        case "alive":
          runner.alive(slot);
          return;
        case "size":
          // The first at once; then the latest, every so often, when it moved enough.
          nextHeight = said.height;
          if (sizing !== undefined) return;
          sizing = window.setTimeout(
            () => {
              sizing = undefined;
              if (Math.abs(nextHeight - shownHeight) < SIZE_STEP) return;
              shownHeight = nextHeight;
              setHeight(nextHeight);
            },
            shownHeight < 0 ? 0 : SIZE_EVERY_MS,
          );
          return;
        case "started":
          if (sent && said.nonce === nonce) {
            markPreviewFinished(markerKey);
            started = true;
            runner.alive(slot);
            setRun((was) => (was?.id === id ? { ...was, state: "running" } : was));
          }
          return;
        case "failed":
          if (!sent || said.nonce !== nonce) return;
          // It returned a failure, so this first run did finish.
          markPreviewFinished(markerKey);
          started = true;
          finish();
          setHeld(true);
          note(`The ${noun} could not run: ${said.message}.`);
          return;
        case "error":
          note(`The ${noun} said: ${said.message}`);
          return;
        case "refused":
          note(`${REFUSED}${said.what}`);
          return;
        case "leaving":
          finish();
          setHeld(true);
          note(`The ${noun} tried to go to another page, and was stopped.`);
          return;
      }
    };
    window.addEventListener("message", heard);
    // A frame that says nothing is in a process still stuck on an earlier preview:
    // a new frame, and the stuck one goes.
    const silent = window.setTimeout(() => {
      if (sent) return;
      if (tries < TRIES) {
        setRun((was) => (was?.id === id ? { ...was, tries: tries + 1 } : was));
        return;
      }
      finish();
      setHeld(true);
      note(
        `The ${noun} did not start: the browser gave its frame no place to run in ` +
          `${(READY_WITHIN_MS * TRIES) / 1000} seconds. Run it again in a moment.`,
      );
    }, READY_WITHIN_MS);
    return () => {
      // A responsive exit is not evidence of a loop, unless timed out or stopped.
      if (sent && !started && !keepMarker.current) markPreviewFinished(markerKey);
      window.removeEventListener("message", heard);
      window.clearTimeout(silent);
      window.clearTimeout(late);
      window.clearTimeout(sizing);
    };
    // The source and kind are this preview's own: a new one is a new preview (its key).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tries]);

  // Gone from the page: so is its frame, and the runner is told.
  useEffect(() => () => runner.stopped(slot), [slot]);

  const waiting = auto && !held && !runner.isStuck(runKey);
  return (
    <div
      ref={root}
      className={inline ? "artifact-preview artifact-preview-inline" : "artifact-preview"}
      data-testid="artifact-preview"
    >
      <div className="artifact-run">
        {run ? (
          <>
            <button type="button" className="act" onClick={stop} aria-label={`Stop the ${noun}`}>
              <Square size={14} aria-hidden="true" />
              Stop
            </button>
            <button type="button" className="act" onClick={start} aria-label={`Reload the ${noun}`}>
              <RotateCw size={14} aria-hidden="true" />
              Reload
            </button>
            <span className="text-sm text-muted" role="status">
              {run.state === "running"
                ? "Running"
                : run.tries > 1
                  ? "Starting… waiting for the browser to end a preview stopped before"
                  : "Starting…"}
            </span>
          </>
        ) : (
          <>
            <button type="button" className="btn" onClick={start}>
              <Play size={14} aria-hidden="true" />
              Run
            </button>
            <span className="text-sm text-muted">
              {unfinished
                ? "This preview didn't finish last time and may have frozen the page. Run it?"
                : waiting
                  ? "Runs here when it is on screen, after the previews before it."
                  : "Runs here, in a frame of its own: it cannot reach this page or your session, " +
                    "and loads nothing from the network. If it goes to another address, it is stopped."}
            </span>
          </>
        )}
      </div>
      {run && (
        <iframe
          key={`${run.id}:${run.tries}`}
          ref={frame}
          className={inline ? "artifact-frame artifact-frame-inline" : "artifact-frame"}
          title={`Preview of ${title}`}
          src={PREVIEW_FRAME}
          sandbox="allow-scripts allow-forms"
          referrerPolicy="no-referrer"
          data-testid="artifact-frame"
        />
      )}
      {notes.length > 0 && (
        <div className="artifact-notes" aria-live="polite" data-testid="artifact-notes">
          <ul>
            {notes.map((words, i) => (
              <li key={i}>{words}</li>
            ))}
          </ul>
          {notes.some((words) => words.startsWith(REFUSED)) && (
            <p>
              A preview loads nothing from the network, sends no form, opens nothing and goes
              nowhere. Download the source to run it elsewhere.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
