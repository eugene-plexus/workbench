/**
 * The files a message is about to carry, in the composer (MR2). An image or a
 * sound is a small chip with its name. A document is a card that says what
 * Workbench found in it and what will be done with it for the model that is
 * picked: read whole, only a part, sent as the file itself, or held until the
 * person chooses how much to read (never cut without being chosen), with the
 * choice in effect, the file's text to check, and Remove.
 *
 * What it says is the decision the server made for the picked model (see
 * useChatFiles): the numbers are its numbers, and for a file it could not read
 * the sentence is its own.
 */

import {
  CircleAlert,
  CircleCheck,
  FileText,
  Info,
  RotateCcw,
  TriangleAlert,
  X,
} from "lucide-react";
import { useId, useRef } from "react";

import {
  type Facts,
  chipWords,
  isDocument,
  metaWords,
  offerChoice,
  offerWords,
  requestWords,
  WINDOW_UNKNOWN,
} from "../lib/documents";
import { elapsedWords } from "../lib/media";
import type { ChatFile, FilesModel, ReadingChoice } from "../lib/types";
import type { Problem } from "../lib/useChatFiles";
import { useClock } from "../lib/useClock";
import { useDocumentText } from "./DocumentText";

export function Attachments({
  files,
  facts,
  problems,
  choosing,
  locked,
  removing,
  onRemove,
  onChoose,
}: {
  files: ChatFile[];
  facts: Facts | null;
  problems: Record<string, Problem>;
  /** The file whose choice is being kept. */
  choosing: string | null;
  /** A message is being sent, or a file is being taken back: nothing else changes. */
  locked: boolean;
  removing: string | null;
  onRemove: (file: ChatFile) => void;
  onChoose: (file: ChatFile, choice: ReadingChoice | null) => Promise<void>;
}) {
  if (files.length === 0) return null;
  const documents = files.some(isDocument);
  const model = facts?.model ?? null;
  const request = documents ? requestWords(facts?.request ?? null) : null;
  return (
    // The files and what is said under them take what the window has left after
    // the header, a line of the conversation and the rest of the composer (32 rem,
    // measured on a phone with two files in it), and scroll past it: many files
    // must not push the message box and Send off the screen. Its buttons are what a
    // keyboard stops on. The padding is room for a focus ring the scroller would cut.
    <div
      data-testid="attachments"
      className="-m-1 max-h-[max(8rem,calc(100dvh-32rem))] overflow-y-auto overscroll-contain p-1"
    >
      <div className="flex flex-col gap-3">
        <ul aria-label="Attached" data-testid="attached" className="flex flex-wrap gap-2">
          {files.map((file) =>
            isDocument(file) ? (
              <DocumentChip
                key={file.id}
                file={file}
                model={model}
                problem={problems[file.id] ?? null}
                busy={choosing === file.id}
                locked={locked || choosing !== null}
                removing={removing !== null}
                onRemove={onRemove}
                onChoose={onChoose}
              />
            ) : (
              <li
                key={file.id}
                className="flex min-w-0 max-w-full items-center gap-1 rounded-plexus border border-line bg-soft py-0.5 pl-2 pr-0.5 text-sm wrap-anywhere"
              >
                {file.name}
                <button
                  type="button"
                  data-testid="attachment-remove"
                  aria-label={`Remove ${file.name}`}
                  disabled={locked || removing !== null}
                  onClick={() => onRemove(file)}
                  className="icon-btn shrink-0"
                >
                  <X size={14} />
                </button>
              </li>
            ),
          )}
        </ul>
        {documents && model && !model.known && (
          <p
            data-testid="files-window-unknown"
            className="flex items-start gap-1.5 text-sm text-muted"
          >
            <Info size={14} aria-hidden className="mt-1 shrink-0" />
            {WINDOW_UNKNOWN}
          </p>
        )}
        {documents && facts?.request?.full && (
          <p data-testid="files-full" className="notice notice--warn">
            <TriangleAlert size={16} aria-hidden />
            {facts.request.full}
          </p>
        )}
        {request && (
          <p data-testid="files-request" className="text-xs text-muted">
            {request}
          </p>
        )}
      </div>
    </div>
  );
}

function DocumentChip({
  file,
  model,
  problem,
  busy,
  locked,
  removing,
  onRemove,
  onChoose,
}: {
  file: ChatFile;
  model: FilesModel | null;
  problem: Problem | null;
  busy: boolean;
  locked: boolean;
  removing: boolean;
  onRemove: (file: ChatFile) => void;
  onChoose: (file: ChatFile, choice: ReadingChoice | null) => Promise<void>;
}) {
  const uid = useId();
  const root = useRef<HTMLLIElement>(null);
  const words = chipWords(file, model);
  const meta = metaWords(file);
  const reading = words.state === "reading";
  // How long this page has watched it being read: its own clock, since the
  // server's may not agree with the browser's.
  const since = useRef(Date.now() / 1000);
  const now = useClock(reading);
  const waited = reading ? Math.max(0, Math.floor(now - since.current)) : 0;
  const decision = file.decision ?? null;
  const offers =
    words.state === "choose" || words.state === "too_long" ? (decision?.offers ?? []) : [];
  const kept = (words.state === "whole" || words.state === "partial") && decision?.chosen != null;
  const text = useDocumentText({
    id: file.id,
    name: file.name,
    describedBy: `${uid}-name`,
  });
  const disabled = locked || busy;

  /** A choice made, and keyboard focus kept: what was pressed is gone by then. */
  async function choose(choice: ReadingChoice | null) {
    const had = root.current?.contains(document.activeElement) ?? false;
    await onChoose(file, choice);
    if (!had) return;
    const now = document.activeElement;
    if (!now || now === document.body) root.current?.focus({ preventScroll: true });
  }

  const attention = words.tone === "warn" || words.tone === "error";
  return (
    <li
      ref={root}
      tabIndex={-1}
      data-testid="document-chip"
      data-state={words.state}
      aria-busy={busy || undefined}
      className="flex w-full min-w-0 flex-col gap-2 rounded-plexus border border-line bg-soft p-2.5 text-sm"
    >
      <div className="flex items-start gap-2">
        <FileText size={16} aria-hidden className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <p id={`${uid}-name`} data-testid="document-name" className="font-medium wrap-anywhere">
            {file.name}
          </p>
          {meta && (
            <p data-testid="document-meta" className="text-xs text-muted">
              {meta}
            </p>
          )}
        </div>
        <button
          type="button"
          data-testid="attachment-remove"
          aria-label={`Remove ${file.name}`}
          disabled={locked || removing}
          onClick={() => onRemove(file)}
          className="icon-btn -mr-1 -mt-1 shrink-0"
        >
          <X size={14} />
        </button>
      </div>
      {problem && (
        <p role="alert" data-testid="document-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <span>
            <span className="notice-title">{problem.title}</span>
            {problem.text}
          </span>
        </p>
      )}
      {attention ? (
        <div
          data-testid="document-status"
          className={`notice ${words.tone === "error" ? "notice--error" : "notice--warn"}`}
        >
          {words.tone === "error" ? (
            <CircleAlert size={16} aria-hidden />
          ) : (
            <TriangleAlert size={16} aria-hidden />
          )}
          <div>
            {words.title && <span className="notice-title">{words.title}</span>}
            {words.sentence}
            {words.hint && <span className="mt-1 block text-muted">{words.hint}</span>}
            {offers.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {offers.map((offer) => (
                  <button
                    key={offer.action}
                    type="button"
                    data-testid={`document-offer-${offer.action === "readAll" ? "all" : "first"}`}
                    disabled={disabled}
                    onClick={() => void choose(offerChoice(offer))}
                    className="btn btn-wrap"
                  >
                    {offerWords(offer)}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : reading ? (
        <p data-testid="document-status" className="flex items-center gap-2 text-muted">
          <span aria-hidden className="working-dots">
            <span />
            <span />
            <span />
          </span>
          <span>{words.sentence}</span>
          {waited >= 3 && (
            <span aria-hidden className="tabular-nums">
              · {elapsedWords(waited)}
            </span>
          )}
        </p>
      ) : (
        <p data-testid="document-status" className="flex items-start gap-1.5">
          <CircleCheck size={16} aria-hidden className="mt-0.5 shrink-0 text-ok" />
          <span>{words.sentence}</span>
        </p>
      )}
      {words.choice && (
        <p data-testid="document-choice" className="text-muted">
          {words.choice}
        </p>
      )}
      {words.notes.length > 0 && (
        <ul data-testid="document-notes" className="list-disc space-y-0.5 pl-5 text-xs text-muted">
          {words.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
      {(kept || file.reading?.state === "ready") && (
        <div className="-mx-1 flex flex-wrap items-center gap-x-1">
          {kept && (
            <button
              type="button"
              data-testid="document-choose-again"
              aria-describedby={`${uid}-name`}
              disabled={disabled}
              onClick={() => void choose(null)}
              className="act"
            >
              <RotateCcw size={14} aria-hidden /> Choose again
            </button>
          )}
          {file.reading?.state === "ready" && text.action}
        </div>
      )}
      {text.panel}
    </li>
  );
}
