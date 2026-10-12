/**
 * A folder's files, as Workbench holds them: each one's path, what it is, where it stands in the
 * words of every document (ready, being read, skipped or failed, and for a file that was
 * skipped or failed the server's own reason), in path order and a page at a time, with a filter
 * for where a file stands, one for why it was skipped or failed, and a box for a word in its path
 * ("did it read my file?").
 *
 * A file can be opened as Workbench holds it, its text can be shown, and it can be left out: the
 * server removes it from the person's documents and writes its path on the folder's ignore list,
 * or it would come back at the next check, and the question says so. The file in the folder itself
 * is never touched. A file that was skipped has no copy, so it has no original to open.
 */

import {
  CircleAlert,
  CircleCheck,
  Clock,
  Download,
  FileText,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { del, fileOf } from "../lib/api";
import {
  type FileFilter,
  filterOptions,
  leaveOutWords,
  reasonOptions,
  rangeWords,
} from "../lib/documentFolders";
import { failureWords, saveFile } from "../lib/memory";
import { addedWords, docMeta, stateWords } from "../lib/savedDocuments";
import type { DocumentFolder, FolderFile } from "../lib/types";
import { useDebounced } from "../lib/useDebounced";
import { useFolderFiles } from "../lib/useFolderFiles";
import { useDocumentText } from "./DocumentText";

/** How many files a page holds. */
export const PAGE_SIZE = 50;

function FileRow({
  file,
  locked,
  busy,
  problem,
  onOpen,
  onLeaveOut,
}: {
  file: FolderFile;
  locked: boolean;
  busy: boolean;
  problem: string | null;
  onOpen: () => void;
  onLeaveOut: () => void;
}) {
  const uid = useId();
  const [confirming, setConfirming] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const back = useRef(false);
  const words = stateWords(file);
  const added = addedWords(file.addedAt);
  const text = useDocumentText({
    id: file.id,
    name: file.path,
    describedBy: `${uid}-path`,
    saved: true,
    prefix: "folder-file",
  });
  const ended = words.state === "skipped" || words.state === "failed";
  // A file that was read has a copy to open; one that was skipped never had.
  const hasCopy = !ended;

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

  return (
    <li
      data-testid="folder-file"
      data-state={file.state}
      data-code={file.code ?? undefined}
      data-path={file.path}
      aria-busy={words.tone === "busy" || undefined}
      className="flex flex-col gap-1.5 px-4 py-3"
    >
      <div className="flex items-start gap-2">
        <FileText size={16} aria-hidden className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <p
            id={`${uid}-path`}
            dir="auto"
            data-testid="folder-file-path"
            className="text-sm font-medium wrap-anywhere"
          >
            {file.path}
          </p>
          <p data-testid="folder-file-meta" className="text-xs text-muted">
            {docMeta(file)}
            {added && <> · {added}</>}
          </p>
        </div>
      </div>
      {ended ? (
        <p
          data-testid="folder-file-status"
          className="flex items-start gap-1.5 text-sm text-muted wrap-anywhere"
        >
          {words.tone === "error" ? (
            <CircleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-error" />
          ) : (
            <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
          )}
          <span>{words.label}</span>
        </p>
      ) : words.tone === "busy" ? (
        <p data-testid="folder-file-status" className="flex items-center gap-2 text-sm text-muted">
          <span aria-hidden className="working-dots">
            <span />
            <span />
            <span />
          </span>
          <span>{words.label}</span>
        </p>
      ) : words.tone === "quiet" ? (
        <p
          data-testid="folder-file-status"
          className="flex items-center gap-1.5 text-sm text-muted"
        >
          <Clock size={16} aria-hidden className="shrink-0" />
          {words.label}
        </p>
      ) : (
        <p data-testid="folder-file-status" className="flex items-center gap-1.5 text-sm">
          <CircleCheck size={16} aria-hidden className="shrink-0 text-ok" />
          {words.label}
        </p>
      )}
      {problem && (
        <p role="alert" data-testid="folder-file-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {confirming ? (
        <div
          role="group"
          aria-label={`Leave out ${file.path}?`}
          data-testid="folder-file-leave-confirm"
          className="notice notice--error flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") leave();
          }}
        >
          <p className="wrap-anywhere">{leaveOutWords(file.path)}</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              autoFocus
              data-testid="folder-file-leave-keep"
              onClick={leave}
              className="btn"
            >
              Keep it
            </button>
            <button
              type="button"
              data-testid="folder-file-leave-go"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onLeaveOut();
              }}
              className="btn btn-danger"
            >
              <Trash2 size={14} aria-hidden />
              Leave it out
            </button>
          </div>
        </div>
      ) : (
        <div className="-mx-1 flex flex-wrap items-center gap-x-1">
          {hasCopy && (
            <button
              type="button"
              data-testid="folder-file-open"
              aria-label={`Open original: ${file.path}`}
              disabled={busy}
              onClick={onOpen}
              className="act"
            >
              <Download size={14} aria-hidden /> Open original
            </button>
          )}
          {file.searchable && text.action}
          {!locked && (
            <button
              ref={opener}
              type="button"
              data-testid="folder-file-leave"
              aria-label={`Leave out: ${file.path}`}
              disabled={busy}
              onClick={() => setConfirming(true)}
              className="act text-error"
            >
              <Trash2 size={14} aria-hidden /> Leave out
            </button>
          )}
        </div>
      )}
      {text.panel}
    </li>
  );
}

export function FolderFiles({
  folder,
  locked,
  again,
  onChanged,
}: {
  folder: DocumentFolder;
  /** The owner has turned documents off: files can be read and opened, not left out. */
  locked: boolean;
  /** What the folder says changed (its counts, its last check): the list is read again. */
  again: string;
  /** A file was left out: the folder's rules and counts changed. */
  onChanged: () => void;
}) {
  const uid = useId();
  const [filter, setFilter] = useState<FileFilter>("all");
  const [code, setCode] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [problems, setProblems] = useState<Record<string, string>>({});
  const q = useDebounced(typed, 300);
  const { shown, problem, fetching, reload } = useFolderFiles(
    folder.id,
    folder.counts,
    { filter, code, q, offset, limit: PAGE_SIZE },
    again,
  );
  const filters = filterOptions(folder.counts, filter);
  const reasons = reasonOptions(folder.counts);
  const showReasons =
    reasons.length > 0 && (filter === "all" || filter === "skipped" || filter === "failed");
  const files = shown?.files ?? [];
  const total = shown?.total ?? 0;

  // A list that got shorter than the page it is on (a file was left out, or the folder changed)
  // goes back to its last page.
  useEffect(() => {
    if (shown && shown.files.length === 0 && shown.total > 0 && offset > 0) {
      setOffset(Math.max(0, Math.ceil(shown.total / PAGE_SIZE) - 1) * PAGE_SIZE);
    }
  }, [shown, offset]);

  async function act(file: FolderFile, work: () => Promise<void>) {
    setBusy(true);
    setProblems((now) => {
      const rest = { ...now };
      delete rest[file.id];
      return rest;
    });
    try {
      await work();
    } catch (failure) {
      setProblems((now) => ({ ...now, [file.id]: failureWords(failure) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id={`${uid}-files`}
      role="group"
      aria-label={`Files of ${folder.name}`}
      data-testid="folder-files"
      aria-busy={fetching || undefined}
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="field">
          <label htmlFor={`${uid}-state`} className="field-label">
            Show
          </label>
          <select
            id={`${uid}-state`}
            data-testid="folder-files-state"
            value={filter}
            onChange={(event) => {
              setFilter(event.target.value as FileFilter);
              setCode(null);
              setOffset(0);
            }}
            className="control control--sm"
          >
            {filters.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label} ({option.n.toLocaleString()})
              </option>
            ))}
          </select>
        </div>
        {showReasons && (
          <div className="field">
            <label htmlFor={`${uid}-reason`} className="field-label">
              Reason
            </label>
            <select
              id={`${uid}-reason`}
              data-testid="folder-files-reason"
              value={code ?? ""}
              onChange={(event) => {
                setCode(event.target.value || null);
                setOffset(0);
              }}
              className="control control--sm"
            >
              <option value="">Any reason</option>
              {reasons.map((reason) => (
                <option key={reason.code} value={reason.code}>
                  {reason.label} ({reason.n.toLocaleString()})
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="field min-w-48 flex-1">
          <label htmlFor={`${uid}-find`} className="field-label">
            Find a file
          </label>
          <input
            id={`${uid}-find`}
            data-testid="folder-files-find"
            type="search"
            dir="auto"
            autoComplete="off"
            spellCheck={false}
            maxLength={200}
            placeholder="A word in its path"
            value={typed}
            onChange={(event) => {
              setTyped(event.target.value);
              setOffset(0);
            }}
            className="control control--sm"
          />
        </div>
      </div>
      {problem && (
        <div role="alert" data-testid="folder-files-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read the files</span>
            {problem}
            <div className="mt-2">
              <button
                type="button"
                data-testid="folder-files-retry"
                aria-label={`Try again to read the files of ${folder.name}`}
                className="btn"
                onClick={reload}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {!shown && !problem && (
        <p role="status" className="text-sm text-muted">
          Reading the list of files…
        </p>
      )}
      {shown && (
        <p data-testid="folder-files-range" className="text-xs tabular-nums text-muted">
          {rangeWords(offset, files.length, total)}
        </p>
      )}
      {shown && files.length === 0 && (
        <p data-testid="folder-files-empty" className="empty">
          {folder.counts.files === 0
            ? "Workbench has read no files from this folder yet."
            : "No file fits what you chose."}
        </p>
      )}
      {files.length > 0 && (
        <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
          {files.map((file) => (
            <FileRow
              key={file.id}
              file={file}
              locked={locked}
              busy={busy}
              problem={problems[file.id] ?? null}
              onOpen={() =>
                void act(file, async () => {
                  const found = await fileOf(
                    `/api/documents/${encodeURIComponent(file.id)}/original`,
                  );
                  saveFile(found.blob, found.name ?? file.path.split("/").pop() ?? file.path);
                })
              }
              onLeaveOut={() =>
                void act(file, async () => {
                  await del(`/api/documents/${encodeURIComponent(file.id)}`);
                  reload();
                  onChanged();
                })
              }
            />
          ))}
        </ul>
      )}
      {shown && total > PAGE_SIZE && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="folder-files-previous"
            aria-label={`Earlier files of ${folder.name}`}
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            className="btn"
          >
            Earlier
          </button>
          <button
            type="button"
            data-testid="folder-files-next"
            aria-label={`Later files of ${folder.name}`}
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
            className="btn"
          >
            Later
          </button>
        </div>
      )}
    </div>
  );
}
