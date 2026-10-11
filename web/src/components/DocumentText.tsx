/**
 * "Show what Workbench read": the text Workbench took from a document, in a
 * quiet scrolling box, so a person can check what a model was given when an
 * answer misses something that is in the file. A PDF's pages are marked
 * `[page N]`. The text is fetched with the secret when the box is opened (a
 * plain link cannot carry it) and dropped when it is closed.
 *
 * It gives the button for a row of actions and the box that opens below the row,
 * as Remember this does.
 */

import { CircleAlert, FileSearch } from "lucide-react";
import { type ReactNode, useId, useRef, useState } from "react";

import { fileText } from "../lib/api";
import { ScrollText } from "./ScrollText";

export function useDocumentText({
  id,
  name,
  describedBy,
}: {
  id: string;
  name: string;
  /** The id of what names the file, so a row of buttons that say the same
   * thing can be told apart by a screen reader. */
  describedBy?: string;
}): { action: ReactNode; panel: ReactNode } {
  const uid = useId();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // An answer that arrives after the box was closed (or opened again) is not shown.
  const latest = useRef(0);

  async function toggle() {
    const mine = ++latest.current;
    if (open) {
      setOpen(false);
      setText(null);
      setProblem(null);
      setLoading(false);
      return;
    }
    setOpen(true);
    setLoading(true);
    setProblem(null);
    try {
      const read = await fileText(id);
      if (mine === latest.current) setText(read);
    } catch (failure) {
      if (mine === latest.current) {
        setProblem(failure instanceof Error ? failure.message : String(failure));
      }
    } finally {
      if (mine === latest.current) setLoading(false);
    }
  }

  const action = (
    <button
      type="button"
      data-testid="document-text-toggle"
      aria-expanded={open}
      aria-controls={`${uid}-text`}
      aria-describedby={describedBy}
      onClick={() => void toggle()}
      className="act"
    >
      <FileSearch size={14} aria-hidden />{" "}
      {open ? "Hide what Workbench read" : "Show what Workbench read"}
    </button>
  );

  // Nothing is drawn while it is closed: a box with nothing in it would still
  // take its gap in the row of what it sits among.
  const panel = open ? (
    <div id={`${uid}-text`}>
      {loading && (
        <p role="status" className="text-xs text-muted">
          Opening the text of {name}…
        </p>
      )}
      {problem && (
        <p role="alert" data-testid="document-text-error" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {text !== null && (
        <>
          <p className="help mt-1">
            All the text Workbench read from {name}. A model is given the part you chose, or all of
            it.
          </p>
          <ScrollText
            label={`Text Workbench read from ${name}`}
            testId="document-text"
            text={text}
          />
        </>
      )}
    </div>
  ) : null;

  return { action, panel };
}
