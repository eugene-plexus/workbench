/**
 * A page or a component an answer wrote, run when the person presses Run and
 * not before (workbench-rich-content-roadmap.md RC5), in a frame of its own:
 * `/preview/frame`, a page with no origin and no network (web.py
 * `PREVIEW_CSP`, preview/runtime.ts). The frame is `sandbox="allow-scripts
 * allow-forms"` (a form's own script runs; its sending never does) and never
 * `allow-same-origin`: with scripts, that could lift the sandbox off its own
 * frame.
 *
 * This page and the frame speak only in messages, and only to each other: a
 * message is heard when it comes from this frame's own window, and is read
 * as words. The source goes to the frame once, when it says it is ready.
 *
 * Stop removes the frame, and with it whatever runs there. Chrome runs a
 * sandboxed frame in a process of its own, so a preview that never stops
 * leaves this page free to press Stop (measured 2026-10-14: a 3 s loop in the
 * frame, the page's longest frame 9 ms); a browser that does not may stall
 * with it, and the source and Download stay beside it.
 *
 * That process lives on after its frame is gone while it is stuck: Chrome ends
 * it by itself, and until then puts the next preview in it, where it never
 * starts (measured 2026-10-14 in Workbench: the stopped page still used a
 * whole core for 5 s after Stop and none 15 s after; a component run at once
 * started 6.2 s after Stop, on its third frame, four times in four). So a
 * frame that has not said it is ready in `READY_WITHIN_MS` is replaced, up to
 * `TRIES` frames, and the person is told why it waits. A preview that is
 * ready but not started in `START_WITHIN_MS` (one that loops before it shows
 * anything) is stopped.
 */

import { Play, RotateCw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** Where a preview runs; the server gives it its own policy there. */
export const PREVIEW_FRAME = "/preview/frame";
/** A frame that has not said it is ready by then is replaced, so many times. */
export const READY_WITHIN_MS = 3_000;
export const TRIES = 7;
/** A preview that is ready and has not started by then is stopped. */
export const START_WITHIN_MS = 10_000;
/** What is said of a preview: its last few messages. */
const NOTES = 8;
/** How a refusal begins: what the preview tried and was not let do. */
const REFUSED = "Refused: ";

type Said =
  | { type: "ready" }
  | { type: "started"; nonce: string }
  | { type: "failed"; nonce: string; message: string }
  | { type: "error"; message: string }
  | { type: "refused"; what: string }
  | { type: "leaving" };

/** A message from the frame, as one of the six it may send; anything else is nothing. */
export function readSaid(data: unknown): Said | null {
  if (typeof data !== "object" || data === null) return null;
  const said = data as Record<string, unknown>;
  const text = (value: unknown) => (typeof value === "string" ? value.slice(0, 500) : "");
  switch (said.type) {
    case "ready":
    case "leaving":
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
    default:
      return null;
  }
}

function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

type Running = { id: number; tries: number; state: "starting" | "running" };

export function ArtifactPreview({
  kind,
  source,
  title,
}: {
  kind: "html" | "react";
  source: string;
  title: string;
}) {
  const [run, setRun] = useState<Running | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const frame = useRef<HTMLIFrameElement>(null);
  const runs = useRef(0);
  const noun = kind === "html" ? "page" : "component";

  const note = (words: string) =>
    setNotes((was) => (was.includes(words) ? was : [...was, words].slice(-NOTES)));
  const start = () => {
    setNotes([]);
    runs.current += 1;
    setRun({ id: runs.current, tries: 1, state: "starting" });
  };
  const stop = (why?: string) => {
    setRun(null);
    if (why) note(why);
  };

  const id = run?.id;
  const tries = run?.tries ?? 0;
  useEffect(() => {
    if (id === undefined) return;
    const nonce = newNonce();
    let sent = false;
    let started = false;
    let late: number | undefined;
    const heard = (event: MessageEvent) => {
      const own = frame.current?.contentWindow;
      if (!own || event.source !== own) return;
      const said = readSaid(event.data);
      if (!said) return;
      switch (said.type) {
        case "ready":
          if (sent) return;
          sent = true;
          own.postMessage({ type: "run", nonce, kind, source }, "*");
          late = window.setTimeout(() => {
            if (started) return;
            setRun((was) => (was?.id === id ? null : was));
            note(
              `The ${noun} did not start within ${START_WITHIN_MS / 1000} seconds, and was stopped.`,
            );
          }, START_WITHIN_MS);
          return;
        case "started":
          if (said.nonce === nonce) {
            started = true;
            setRun((was) => (was?.id === id ? { ...was, state: "running" } : was));
          }
          return;
        case "failed":
          if (said.nonce !== nonce) return;
          started = true;
          setRun((was) => (was?.id === id ? null : was));
          note(`The ${noun} could not run: ${said.message}.`);
          return;
        case "error":
          note(`The ${noun} said: ${said.message}`);
          return;
        case "refused":
          note(`${REFUSED}${said.what}`);
          return;
        case "leaving":
          setRun((was) => (was?.id === id ? null : was));
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
      setRun((was) => (was?.id === id ? null : was));
      note(
        `The ${noun} did not start: the browser gave its frame no place to run in ` +
          `${(READY_WITHIN_MS * TRIES) / 1000} seconds. Run it again in a moment.`,
      );
    }, READY_WITHIN_MS);
    return () => {
      window.removeEventListener("message", heard);
      window.clearTimeout(silent);
      window.clearTimeout(late);
    };
    // The source and kind are this preview's own: a new one is a new preview (its key).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, tries]);

  return (
    <div className="artifact-preview" data-testid="artifact-preview">
      <div className="artifact-run">
        {run ? (
          <>
            <button
              type="button"
              className="act"
              onClick={() => stop()}
              aria-label={`Stop the ${noun}`}
            >
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
              Runs here, in a frame of its own: it cannot reach this page or your session, and loads
              nothing from the network. If it goes to another address, it is stopped.
            </span>
          </>
        )}
      </div>
      {run && (
        <iframe
          key={`${run.id}:${run.tries}`}
          ref={frame}
          className="artifact-frame"
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
