/**
 * One of the person's folders on the Documents screen: its name and where it stands (waiting,
 * syncing with how far it has got, synced with when it was checked and when it will be next,
 * a problem, not available, or withdrawn by the owner), what it holds (files, ready, being read,
 * skipped), what the last check did, what was left out and why, and what can be done with it:
 * Sync now (and a deep check, as an expert option), its files, what was left out, its rules, and
 * Remove folder.
 *
 * Every sentence is composed from the fields the server sent (`lib/documentFolders.ts`), against
 * Workbench's own clock, and a problem, a missing folder and a withdrawal are the server's own
 * words. Removing a folder deletes what Workbench read from it and nothing in the folder itself,
 * and the question says so; a withdrawn folder is already out of the search, so Dismiss only
 * clears its entry.
 */

import {
  Ban,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock,
  EyeOff,
  FolderOpen,
  FolderX,
  List,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { del, post } from "../lib/api";
import {
  type BadgeTone,
  capsWords,
  countItems,
  DEEP_WORDS,
  doneCount,
  holdsWords,
  lastWords,
  leftSummary,
  readingWords,
  removeFolderWords,
  stateBadge,
  stateLines,
} from "../lib/documentFolders";
import { failureWords } from "../lib/memory";
import { addedWords } from "../lib/savedDocuments";
import type { DocumentFolder, FolderLimits, FolderState } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { FolderFiles } from "./FolderFiles";
import { FolderLeftOut } from "./FolderLeftOut";
import { FolderRules } from "./FolderRules";
import { FolderPlace } from "./FolderPlace";

const BADGE_CLASS: Record<BadgeTone, string> = {
  quiet: "badge",
  ok: "badge badge--ok",
  warn: "badge badge--warn",
};

/** The glyph on a folder's badge: words and a glyph say it, never colour alone. */
function BadgeGlyph({ state }: { state: FolderState }) {
  switch (state) {
    case "waiting":
      return <Clock size={14} aria-hidden />;
    case "syncing":
      return <RefreshCw size={14} aria-hidden />;
    case "synced":
      return <CircleCheck size={14} aria-hidden />;
    case "withdrawn":
      return <Ban size={14} aria-hidden />;
    case "unavailable":
      return <FolderX size={14} aria-hidden />;
    default:
      return <TriangleAlert size={14} aria-hidden />;
  }
}

export function FolderCard({
  folder,
  limits,
  now,
  locked,
  disabled,
  onChanged,
  onRemoved,
}: {
  folder: DocumentFolder;
  limits: FolderLimits;
  /** The time now by Workbench's clock, in seconds. */
  now: number;
  /** The owner has turned documents off: nothing is checked, and a folder can only be looked at
   * and removed. */
  locked: boolean;
  /** Something else on the screen is being changed. */
  disabled: boolean;
  /** Something about the folder changed: the screen reads its folders again. */
  onChanged: () => void;
  /** The folder is gone from the list, by Remove folder or Dismiss. */
  onRemoved: () => void;
}) {
  const uid = useId();
  const [files, setFiles] = useState(false);
  const [left, setLeft] = useState(false);
  const [rules, setRules] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const keepFocus = useKeepFocus(pending);
  const opener = useRef<HTMLButtonElement>(null);
  const back = useRef(false);

  const { state, counts } = folder;
  const badge = stateBadge(state);
  const withdrawn = state === "withdrawn";
  const missing = withdrawn || state === "unavailable";
  // A check is under way or about to be: asking for another would only repeat it.
  const checking = state === "syncing" || state === "waiting";
  const held = pending || disabled;
  const lines = stateLines(folder, now);
  const reading = readingWords(counts);
  const caps = capsWords(folder.caps);
  const last = lastWords(folder.last);
  const summary = leftSummary(folder.last);
  const added = addedWords(folder.addedAt);
  // What the open lists are read again for: the folder says it was checked, or what it holds moved.
  const again = `${folder.checkedAt ?? ""}|${JSON.stringify(counts.byState)}|${counts.files}`;
  const checked = `${folder.checkedAt ?? ""}|${state}`;
  const run = folder.syncing;

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

  async function act(work: () => Promise<void>, done?: () => void) {
    keepFocus();
    setPending(true);
    setProblem(null);
    try {
      await work();
      done?.();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setPending(false);
      onChanged();
    }
  }

  const sync = (deep: boolean) => {
    if (checking) return;
    void act(() =>
      post(`/api/documents/folders/${encodeURIComponent(folder.id)}/sync`, { deep }).then(
        () => undefined,
      ),
    );
  };

  const remove = () =>
    void act(
      () => del(`/api/documents/folders/${encodeURIComponent(folder.id)}`),
      () => onRemoved(),
    );

  return (
    <li
      data-testid="folder"
      data-state={state}
      data-folder-id={folder.id}
      aria-busy={state === "syncing" || undefined}
      className="card"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <h3
          id={`${uid}-name`}
          tabIndex={-1}
          dir="auto"
          className="title-card flex items-start gap-2"
        >
          <FolderOpen size={16} aria-hidden className="mt-1 shrink-0 text-muted" />
          <span data-testid="folder-name">{folder.name}</span>
        </h3>
        <span
          role="status"
          data-testid="folder-badge"
          data-state={state}
          className={BADGE_CLASS[badge.tone]}
        >
          <BadgeGlyph state={state} />
          {badge.label}
        </span>
      </div>

      {state === "problem" || missing ? (
        <div
          data-testid="folder-status"
          className={`notice ${state === "problem" ? "notice--error" : "notice--warn"}`}
        >
          {state === "problem" ? (
            <CircleAlert size={16} aria-hidden />
          ) : (
            <TriangleAlert size={16} aria-hidden />
          )}
          <div className="flex flex-col gap-1">
            {lines.map((line, i) => (
              <span key={line} className={i === 0 ? "wrap-anywhere" : "text-muted"}>
                {line}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div data-testid="folder-status" className="flex flex-col gap-1 text-sm">
          {lines.map((line, i) => (
            <p
              key={line}
              className={`flex items-center gap-1.5 ${state === "synced" && i > 0 ? "text-muted" : ""}`}
            >
              {i === 0 && state === "syncing" && (
                <span aria-hidden className="working-dots shrink-0 text-muted">
                  <span />
                  <span />
                  <span />
                </span>
              )}
              {line}
            </p>
          ))}
          {state === "syncing" && run && run.phase === "reading" && run.total > 0 && (
            <progress
              aria-label={`Reading ${folder.name}`}
              max={run.total}
              value={Math.min(run.done, run.total)}
              className="h-1.5 w-full"
            />
          )}
        </div>
      )}

      {!missing && (
        <>
          <ul data-testid="folder-counts" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {countItems(counts).map((item) => (
              <li key={item.kind} data-testid="folder-count" data-kind={item.kind} data-n={item.n}>
                <span className="font-medium tabular-nums">{item.n.toLocaleString()}</span>{" "}
                {item.label}
              </li>
            ))}
          </ul>
          <p data-testid="folder-holds" className="text-xs text-muted">
            {holdsWords(counts)}
            {added && <> · {added}</>}
          </p>
          {reading && (
            <div data-testid="folder-reading" className="flex flex-col gap-1 text-sm">
              <p>{reading}</p>
              <progress
                aria-label={`Files of ${folder.name} that are done`}
                max={counts.files}
                value={doneCount(counts)}
                className="h-1.5 w-full"
              />
            </div>
          )}
          {caps.length > 0 && (
            <div data-testid="folder-caps" className="notice notice--warn">
              <TriangleAlert size={16} aria-hidden />
              <ul className="flex flex-col gap-1">
                {caps.map((line) => (
                  <li key={line} className="wrap-anywhere">
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {last && (
            <p data-testid="folder-last" className="text-xs text-muted">
              {last}
            </p>
          )}
          {summary && (
            <p data-testid="folder-left-summary" className="text-xs text-muted wrap-anywhere">
              {summary}
            </p>
          )}
        </>
      )}
      {locked && !missing && (
        <p data-testid="folder-locked" className="text-xs text-muted">
          The owner of this Workbench has turned off documents, so this folder is not checked now.
        </p>
      )}

      {problem && (
        <p role="alert" data-testid="folder-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}

      {confirming ? (
        <div
          role="group"
          aria-label={`Remove ${folder.name}?`}
          data-testid="folder-remove-confirm"
          className="notice notice--error flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") leave();
          }}
        >
          <p className="wrap-anywhere">{removeFolderWords(folder)}</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              autoFocus
              data-testid="folder-remove-keep"
              onClick={leave}
              className="btn"
            >
              Keep it
            </button>
            <button
              type="button"
              data-testid="folder-remove-go"
              disabled={held}
              onClick={() => {
                setConfirming(false);
                remove();
              }}
              className="btn btn-danger"
            >
              <Trash2 size={14} aria-hidden />
              Remove folder
            </button>
          </div>
        </div>
      ) : (
        <div className="-mx-1 flex flex-wrap items-center gap-x-1 gap-y-1">
          {!locked && !withdrawn && (
            <button
              type="button"
              data-testid="folder-sync"
              aria-label={`Sync now: ${folder.name}`}
              aria-disabled={checking || undefined}
              disabled={held}
              onClick={() => sync(false)}
              className="btn mr-1"
            >
              <RefreshCw size={14} aria-hidden /> Sync now
            </button>
          )}
          {!missing && (
            <button
              type="button"
              data-testid="folder-files-toggle"
              aria-label={`${files ? "Hide" : "Show"} files: ${folder.name}`}
              aria-expanded={files}
              aria-controls={`${uid}-files`}
              onClick={() => setFiles(!files)}
              className="act"
            >
              <List size={14} aria-hidden /> {files ? "Hide files" : "Show files"}
            </button>
          )}
          {!missing && (
            <button
              type="button"
              data-testid="folder-left-toggle"
              aria-label={`${left ? "Hide" : "Show"} what was left out: ${folder.name}`}
              aria-expanded={left}
              aria-controls={`${uid}-left`}
              onClick={() => setLeft(!left)}
              className="act"
            >
              <EyeOff size={14} aria-hidden />{" "}
              {left ? "Hide what was left out" : "Show what was left out"}
            </button>
          )}
          {!locked && !missing && (
            <button
              type="button"
              data-testid="folder-rules-toggle"
              aria-label={`${rules ? "Hide" : "Edit"} rules: ${folder.name}`}
              aria-expanded={rules}
              aria-controls={`${uid}-rules`}
              onClick={() => setRules(!rules)}
              className="act"
            >
              <SlidersHorizontal size={14} aria-hidden /> {rules ? "Hide rules" : "Edit rules"}
            </button>
          )}
          {withdrawn ? (
            <button
              ref={opener}
              type="button"
              data-testid="folder-dismiss"
              aria-label={`Dismiss: ${folder.name}`}
              disabled={held}
              onClick={remove}
              className="btn"
            >
              Dismiss
            </button>
          ) : (
            <button
              ref={opener}
              type="button"
              data-testid="folder-remove"
              aria-label={`Remove folder: ${folder.name}`}
              disabled={held}
              onClick={() => setConfirming(true)}
              className="act text-error"
            >
              <Trash2 size={14} aria-hidden /> Remove folder
            </button>
          )}
        </div>
      )}

      {!locked && !withdrawn && (
        <details data-testid="folder-deep-details" className="group/deep text-sm">
          <summary
            data-testid="folder-deep-toggle"
            aria-label={`A deep check: ${folder.name}`}
            className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
          >
            <ChevronRight
              size={14}
              aria-hidden
              className="shrink-0 transition-transform group-open/deep:rotate-90"
            />
            A deep check
          </summary>
          <div className="mt-2 flex flex-col items-start gap-2">
            <p id={`${uid}-deep`} className="help">
              {DEEP_WORDS}
            </p>
            <button
              type="button"
              data-testid="folder-deep"
              aria-label={`Run a deep check: ${folder.name}`}
              aria-describedby={`${uid}-deep`}
              aria-disabled={checking || undefined}
              disabled={held}
              onClick={() => sync(true)}
              className="btn"
            >
              Run a deep check
            </button>
          </div>
        </details>
      )}

      {files && !missing && (
        <div id={`${uid}-files`}>
          <FolderFiles folder={folder} locked={locked} again={again} onChanged={onChanged} />
        </div>
      )}
      {left && !missing && (
        <div id={`${uid}-left`}>
          <FolderLeftOut folder={folder} again={checked} />
        </div>
      )}
      {!locked && !missing && <FolderPlace folder={folder} onChanged={onChanged} />}
      {rules && !locked && !missing && (
        <div id={`${uid}-rules`}>
          <FolderRules folder={folder} limits={limits} onSaved={onChanged} />
        </div>
      )}
    </li>
  );
}
