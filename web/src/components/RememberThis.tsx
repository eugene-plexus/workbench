/**
 * Remember this: keep what a message says as a note, from the message's own
 * actions. It works on the person's words and, deliberately, on an answer's,
 * and is recorded as kept with a button, with the chat and the message it
 * came from. A note is one line of at most 240 characters, so a message that
 * is longer is not cut quietly: the person is told, and shortens it to what
 * they want kept.
 *
 * The button goes where the message's other actions are, and what the person
 * has to read or fill in goes below them, so the actions that appear on hover
 * never hide an open question.
 */

import { BookmarkPlus, CircleAlert, Info } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { post } from "../lib/api";
import { characters, cutNote, failureWords, NOTE_LIMIT, noteFrom, YOU_OFF } from "../lib/memory";
import type { MemoryNote, Message } from "../lib/types";

export interface RememberProps {
  /** `you`: the person has turned memory off, so a note would not be used. */
  off: "you" | null;
  /** A note was kept, or the same words already were: the chat says so. */
  onSaved: (note: MemoryNote, created: boolean) => void;
  onManage: () => void;
}

/** The button for a message's actions row, and what it opens below them. */
export function useRemember({
  chatId,
  message,
  text,
  memory,
}: {
  chatId: string;
  message: Message;
  /** The words that would be kept: the person's message, or the answer. */
  text: string;
  memory?: RememberProps;
}): { action: ReactNode; panel: ReactNode } {
  const uid = useId();
  const button = useRef<HTMLButtonElement>(null);
  const [editing, setEditing] = useState<{ draft: string; length: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const returnFocus = useRef(false);

  // Closing the box puts focus back on the button that opened it, once the
  // button can take it (it is disabled while a note is being kept, which takes
  // the keyboard's place from it). Not when the person has gone somewhere else.
  useEffect(() => {
    if (editing || saving || !returnFocus.current) return;
    returnFocus.current = false;
    const now = document.activeElement;
    if (!now || now === document.body) button.current?.focus();
  }, [editing, saving]);

  if (!memory || !text.trim()) return { action: null, panel: null };

  const noun = message.role === "user" ? "message" : "answer";

  function back() {
    returnFocus.current = true;
    setEditing(null);
  }

  async function keep(words: string, fromEditor: boolean) {
    returnFocus.current = true;
    setSaving(true);
    setProblem(null);
    try {
      const kept = await post<{ note: MemoryNote; created: boolean }>("/api/memory/notes", {
        text: words,
        source: "button",
        chatId,
        messageId: message.id,
      });
      if (fromEditor) back();
      memory?.onSaved(kept.note, kept.created);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setSaving(false);
    }
  }

  function start() {
    setProblem(null);
    if (memory?.off === "you") {
      setOff(true);
      return;
    }
    setOff(false);
    const note = noteFrom(text);
    if (note.over) {
      setEditing({ draft: cutNote(text), length: note.length });
      return;
    }
    void keep(note.text, false);
  }

  const draftLength = editing ? characters(editing.draft) : 0;
  const tooLong = draftLength > NOTE_LIMIT;

  const action = (
    <button
      ref={button}
      type="button"
      data-testid="remember-this"
      aria-label={`Remember this ${noun}`}
      disabled={saving}
      onClick={start}
      className="act"
    >
      <BookmarkPlus size={14} aria-hidden /> Remember this
    </button>
  );

  const panel = (
    <>
      {off && (
        <div data-testid="remember-off" role="status" className="notice max-w-measure">
          <Info size={16} aria-hidden />
          <div>
            {YOU_OFF}
            <div className="mt-2">
              <button
                type="button"
                data-testid="remember-open-memory"
                className="btn"
                onClick={() => memory.onManage()}
              >
                Open Memory
              </button>
            </div>
          </div>
        </div>
      )}
      {editing && (
        <form
          data-testid="remember-editor"
          aria-label={`Keep a note from this ${noun}`}
          className="flex w-full max-w-measure flex-col gap-2 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            if (!saving && !tooLong && editing.draft.trim()) void keep(editing.draft.trim(), true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !saving) {
              event.stopPropagation();
              back();
            }
          }}
        >
          <div className="field">
            <label htmlFor={`${uid}-note`} className="field-label">
              Note to keep
            </label>
            <textarea
              id={`${uid}-note`}
              data-testid="remember-text"
              autoFocus
              rows={4}
              dir="auto"
              value={editing.draft}
              disabled={saving}
              aria-describedby={`${uid}-help`}
              aria-invalid={tooLong || undefined}
              onChange={(event) => setEditing({ ...editing, draft: event.target.value })}
              className="control"
            />
            <p id={`${uid}-help`} data-testid="remember-help" className="help">
              A note is one line of at most {NOTE_LIMIT} characters, and this {noun} is{" "}
              {editing.length.toLocaleString()}. The box starts as the first part of it; edit it to
              what you want kept.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span
              data-testid="remember-count"
              className={`tabular-nums ${tooLong ? "text-error" : "text-muted"}`}
            >
              {draftLength.toLocaleString()} of {NOTE_LIMIT}
            </span>
            <span className="ml-auto flex gap-2">
              <button
                type="button"
                data-testid="remember-cancel"
                disabled={saving}
                onClick={back}
                className="btn btn-quiet"
              >
                Cancel
              </button>
              <button
                type="submit"
                data-testid="remember-save"
                disabled={saving || tooLong || !editing.draft.trim()}
                className="btn btn-primary"
              >
                Keep note
              </button>
            </span>
          </div>
        </form>
      )}
      {problem && (
        <p
          role="alert"
          data-testid="remember-problem"
          className="notice notice--error max-w-measure"
        >
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
    </>
  );

  return { action, panel };
}
