/**
 * One of the person's documents on the Documents screen: its name, what it is (type,
 * size, pages or lines, how many passages, the day it was added), where it stands
 * in the words of the design, and what can be done with it: open the file as it was
 * added, show the text Workbench searches, read it again, remove it.
 *
 * Removing is real: the server deletes its copy, what it read from it and every entry
 * for it in the search, and cannot bring them back, so the row asks first and says
 * so. Nothing here is undone after the fact.
 */

import {
  CircleAlert,
  CircleCheck,
  Clock,
  Download,
  FileText,
  RotateCw,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { addedWords, docMeta, stateWords } from "../lib/savedDocuments";
import type { SavedDocument } from "../lib/types";
import { useDocumentText } from "./DocumentText";

export function DocumentRow({
  doc,
  locked,
  busy,
  problem,
  onOpen,
  onReread,
  onRemove,
}: {
  doc: SavedDocument;
  /** The owner has turned documents off: a document can be read and removed, not
   * read again. */
  locked: boolean;
  busy: boolean;
  /** Why the last thing done to this document did not work, in the server's words. */
  problem: string | null;
  onOpen: () => void;
  onReread: () => void;
  onRemove: () => void;
}) {
  const uid = useId();
  const [confirming, setConfirming] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const back = useRef(false);
  const words = stateWords(doc);
  const meta = docMeta(doc);
  const added = addedWords(doc.addedAt);
  const text = useDocumentText({
    id: doc.id,
    name: doc.name,
    describedBy: `${uid}-name`,
    saved: true,
  });
  const date = doc.addedAt != null ? new Date(doc.addedAt * 1000) : null;
  // Read again only what has stopped: a document still being read has nothing to redo.
  const finished = doc.state === "ready" || doc.state === "skipped" || doc.state === "failed";

  // Closing the question puts focus back on the button that opened it.
  useEffect(() => {
    if (confirming || !back.current) return;
    back.current = false;
    opener.current?.focus();
  }, [confirming]);

  function leave() {
    back.current = true;
    setConfirming(false);
  }

  const ended = words.state === "skipped" || words.state === "failed";
  return (
    <li
      data-testid="saved-document"
      data-state={doc.state}
      aria-busy={words.tone === "busy" || undefined}
      className="flex flex-col gap-2 px-4 py-3"
    >
      <div className="flex items-start gap-2">
        <FileText size={16} aria-hidden className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <p
            id={`${uid}-name`}
            dir="auto"
            data-testid="saved-document-name"
            className="text-base font-medium wrap-anywhere"
          >
            {doc.name}
          </p>
          <p data-testid="saved-document-meta" className="text-xs text-muted">
            {meta}
            {added && date && (
              <>
                {" · "}
                <time dateTime={date.toISOString()} title={date.toLocaleString()}>
                  {added}
                </time>
              </>
            )}
          </p>
        </div>
      </div>
      {ended ? (
        <div
          data-testid="saved-document-status"
          className={`notice ${words.tone === "error" ? "notice--error" : "notice--warn"}`}
        >
          {words.tone === "error" ? (
            <CircleAlert size={16} aria-hidden />
          ) : (
            <TriangleAlert size={16} aria-hidden />
          )}
          <span className="wrap-anywhere">{words.label}</span>
        </div>
      ) : words.tone === "busy" ? (
        <p
          data-testid="saved-document-status"
          className="flex items-center gap-2 text-sm text-muted"
        >
          <span aria-hidden className="working-dots">
            <span />
            <span />
            <span />
          </span>
          <span>{words.label}</span>
        </p>
      ) : words.tone === "quiet" ? (
        <p
          data-testid="saved-document-status"
          className="flex items-center gap-1.5 text-sm text-muted"
        >
          <Clock size={16} aria-hidden className="shrink-0" />
          {words.label}
        </p>
      ) : (
        <p data-testid="saved-document-status" className="flex items-center gap-1.5 text-sm">
          <CircleCheck size={16} aria-hidden className="shrink-0 text-ok" />
          {words.label}
        </p>
      )}
      {doc.notes.length > 0 && !ended && (
        <ul
          data-testid="saved-document-notes"
          className="list-disc space-y-0.5 pl-5 text-xs text-muted"
        >
          {doc.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {problem && (
        <p role="alert" data-testid="saved-document-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {confirming ? (
        <div
          role="group"
          aria-label={`Remove ${doc.name}?`}
          data-testid="saved-document-remove-confirm"
          className="notice notice--error flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") leave();
          }}
        >
          <p className="wrap-anywhere">
            Remove {doc.name}? Workbench deletes its copy of the file and the text it read from it,
            and no chat can find it again. This cannot be undone.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              autoFocus
              data-testid="saved-document-remove-keep"
              onClick={leave}
              className="btn"
            >
              Keep it
            </button>
            <button
              type="button"
              data-testid="saved-document-remove-go"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onRemove();
              }}
              className="btn btn-danger"
            >
              <Trash2 size={14} aria-hidden />
              Remove
            </button>
          </div>
        </div>
      ) : (
        <div className="-mx-1 flex flex-wrap items-center gap-x-1">
          <button
            type="button"
            data-testid="saved-document-open"
            aria-label={`Open original: ${doc.name}`}
            disabled={busy}
            onClick={onOpen}
            className="act"
          >
            <Download size={14} aria-hidden /> Open original
          </button>
          {doc.searchable && text.action}
          {!locked && finished && (
            <button
              type="button"
              data-testid="saved-document-reread"
              aria-label={`Re-read: ${doc.name}`}
              disabled={busy}
              onClick={onReread}
              className="act"
            >
              <RotateCw size={14} aria-hidden /> Re-read
            </button>
          )}
          <button
            ref={opener}
            type="button"
            data-testid="saved-document-remove"
            aria-label={`Remove: ${doc.name}`}
            disabled={busy}
            onClick={() => setConfirming(true)}
            className="act text-error"
          >
            <Trash2 size={14} aria-hidden /> Remove
          </button>
        </div>
      )}
      {text.panel}
    </li>
  );
}
