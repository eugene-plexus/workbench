/**
 * What a chat says after it changes the notes: "Remembered: ... Undo · Manage"
 * after "Remember that ...", or after a message's Remember this; why a note was
 * not kept, in plain words; and, after "forget ...", the notes it might mean,
 * for the person to pick one. Nothing is ever deleted on a guess.
 *
 * A toast that can be undone stays ten seconds, and stays longer while the
 * pointer or the keyboard is on it. One that asks a question, or says why
 * something was not done, stays until it is dismissed.
 */

import { CircleAlert, CircleCheck, Info, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { del, post } from "../lib/api";
import { excerpt, failureWords, refusalWords, UNDO_MS } from "../lib/memory";
import type { MemoryCommand, MemoryNote, Refusal } from "../lib/types";

/** What the toast is about: a note kept, words refused, or notes to choose from. */
export type ToastOutcome =
  | { kind: "remembered"; note: MemoryNote; created: boolean }
  | { kind: "refused"; refusal: Refusal; messageSent: boolean }
  | { kind: "forget"; words: string; candidates: MemoryNote[] };

/** What a sent message's `memory` key comes to, or null for no toast. */
export function outcomeOf(command: MemoryCommand | undefined): ToastOutcome | null {
  if (!command) return null;
  if (command.command === "forget") {
    return { kind: "forget", words: command.words, candidates: command.candidates };
  }
  if ("remembered" in command) {
    return { kind: "remembered", note: command.remembered, created: command.created };
  }
  return { kind: "refused", refusal: command.refused, messageSent: true };
}

type Shown =
  | { kind: "remembered"; note: MemoryNote; created: boolean }
  | { kind: "deleted"; note: MemoryNote }
  | { kind: "undone"; note: MemoryNote }
  | { kind: "refused"; refusal: Refusal; messageSent: boolean }
  | { kind: "forget"; words: string; candidates: MemoryNote[] };

const quoted = (note: Pick<MemoryNote, "text">) => (
  <span dir="auto" className="wrap-anywhere">
    {"“"}
    {note.text}
    {"”"}
  </span>
);

export function MemoryToast({
  outcome,
  onManage,
  onChanged,
  onClose,
}: {
  outcome: ToastOutcome;
  /** Open the Memory page. */
  onManage: () => void;
  /** A note was kept, taken back or deleted from here: the chat's chip is stale. */
  onChanged: () => void;
  onClose: () => void;
}) {
  const [shown, setShown] = useState<Shown>(outcome);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [held, setHeld] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  // A button is held while its work is done, which takes the keyboard's place
  // from it; and what it was pressed on has changed by then (Undo is gone). If
  // the keyboard was in the toast, it goes to the toast's first button again.
  const keyboardWasIn = useRef(false);
  useEffect(() => {
    if (busy || !keyboardWasIn.current) return;
    keyboardWasIn.current = false;
    const now = document.activeElement;
    if (!now || now === document.body) root.current?.querySelector("button")?.focus();
  }, [busy, shown]);
  // The latest way to close, so a parent that draws again (an answer streaming
  // in) does not start the clock over.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  // How long it stays: an undo for ten seconds, a note taken back for four, and
  // a question or a refusal until it is dismissed.
  const stay =
    shown.kind === "remembered" || shown.kind === "deleted"
      ? UNDO_MS
      : shown.kind === "undone"
        ? 4000
        : null;
  useEffect(() => {
    if (stay === null || held || busy) return;
    const timer = window.setTimeout(() => close.current(), stay);
    return () => window.clearTimeout(timer);
  }, [stay, held, busy, shown]);

  /** The person closes it (its button, or Escape). If the keyboard was in it, the
   * keyboard goes back to the message box, where it was writing. */
  function dismiss() {
    const inside = root.current?.contains(document.activeElement) ?? false;
    onClose();
    if (inside) queueMicrotask(() => document.getElementById("message-composer")?.focus());
  }

  async function run(work: () => Promise<void>) {
    keyboardWasIn.current = root.current?.contains(document.activeElement) ?? false;
    setBusy(true);
    setProblem(null);
    try {
      await work();
      onChanged();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  /** Undo a note kept a moment ago: it is deleted, and nothing of it remains. */
  const takeBack = (note: MemoryNote) =>
    run(async () => {
      await del(`/api/memory/notes/${encodeURIComponent(note.id)}`);
      setShown({ kind: "undone", note });
    });

  /** Delete the note the person picked of the ones "forget" found. */
  const forget = (note: MemoryNote) =>
    run(async () => {
      await del(`/api/memory/notes/${encodeURIComponent(note.id)}`);
      setShown({ kind: "deleted", note });
    });

  /** Undo a deletion: the words are kept again, as a new note. */
  const keepAgain = (note: MemoryNote) =>
    run(async () => {
      const kept = await post<{ note: MemoryNote; created: boolean }>("/api/memory/notes", {
        text: note.text,
        kind: note.kind,
        pinned: note.pinned,
      });
      setShown({ kind: "remembered", note: kept.note, created: kept.created });
    });

  const alert = shown.kind === "refused";
  const Glyph = alert ? CircleAlert : shown.kind === "forget" ? Info : CircleCheck;
  const tone = alert ? "notice--warn" : shown.kind === "forget" ? "" : "notice--ok";

  const actions = (undo: (() => void) | null) => (
    <div className="mt-1 flex flex-wrap items-center gap-1 -ms-2">
      {undo && (
        <button
          type="button"
          data-testid="memory-toast-undo"
          disabled={busy}
          onClick={undo}
          className="btn btn-quiet"
        >
          Undo
        </button>
      )}
      <button
        type="button"
        data-testid="memory-toast-manage"
        onClick={onManage}
        className="btn btn-quiet"
      >
        Manage
      </button>
    </div>
  );

  return (
    <div
      ref={root}
      role={alert ? "alert" : "status"}
      data-testid="memory-toast"
      className={`notice ${tone} notice--float shadow-lg`}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          dismiss();
        }
      }}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
    >
      <Glyph size={16} aria-hidden />
      <div className="min-w-0 flex-1">
        {shown.kind === "remembered" && (
          <>
            <span>
              <strong className="font-semibold">
                {shown.created ? "Remembered:" : "Already remembered:"}
              </strong>{" "}
              {quoted(shown.note)}
            </span>
            {actions(shown.created ? () => void takeBack(shown.note) : null)}
          </>
        )}
        {shown.kind === "deleted" && (
          <>
            <span>
              <strong className="font-semibold">Deleted:</strong> {quoted(shown.note)}
            </span>
            {actions(() => void keepAgain(shown.note))}
          </>
        )}
        {shown.kind === "undone" && (
          <span>
            <strong className="font-semibold">Undone.</strong> {quoted(shown.note)} is not
            remembered.
          </span>
        )}
        {shown.kind === "refused" && (
          <>
            <span>
              <strong className="font-semibold">Not remembered.</strong>{" "}
              {refusalWords(shown.refusal)}
              {shown.messageSent ? " Your message was still sent." : ""}
            </span>
            {actions(null)}
          </>
        )}
        {shown.kind === "forget" && (
          <>
            {shown.candidates.length === 0 ? (
              <span>
                <strong className="font-semibold">No note matches</strong>{" "}
                <span dir="auto" className="wrap-anywhere">
                  {"“"}
                  {shown.words}
                  {"”"}
                </span>
                .
              </span>
            ) : (
              <>
                <span className="notice-title">Delete one of these notes?</span>
                <ul className="flex flex-col divide-y divide-line">
                  {shown.candidates.map((note) => (
                    <li key={note.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
                      <span dir="auto" className="min-w-0 flex-1 basis-48 wrap-anywhere">
                        {note.text}
                      </span>
                      <button
                        type="button"
                        data-testid="memory-toast-forget"
                        disabled={busy}
                        aria-label={`Delete note: ${excerpt(note)}`}
                        onClick={() => void forget(note)}
                        className="btn btn-quiet btn-danger"
                      >
                        Delete
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
            {actions(null)}
          </>
        )}
        {problem && (
          <p role="alert" className="mt-1 text-error">
            {problem}
          </p>
        )}
      </div>
      <button
        type="button"
        data-testid="memory-toast-dismiss"
        aria-label="Dismiss"
        onClick={dismiss}
        className="icon-btn -my-1 -me-2 shrink-0"
      >
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}
