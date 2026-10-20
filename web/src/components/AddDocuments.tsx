/**
 * Add files to the person's documents: a button, or files dropped anywhere on the
 * screen, many at once. Each file goes in a request of its own, one after another,
 * so each has its own line: waiting, how much of it has gone, and then added or
 * refused in the server's own words (a file Workbench does not read, one too large,
 * one past the person's limit, a full disk). A refusal never stops the files after
 * it. An added file is queued, and the list above reads it in the background.
 *
 * Leaving the screen stops what is left: a half-sent file is never kept.
 */

import { CircleAlert, CircleCheck, Clock, FileUp, Folder, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { upload } from "../lib/api";
import { DOCUMENT_ACCEPT } from "../lib/documents";
import {
  refusedBy,
  sizeWords,
  type Upload,
  uploadSummary,
  uploadWords,
} from "../lib/savedDocuments";
import type { AddedFiles } from "../lib/types";

/** The row's glyph: a file waiting, being sent, kept, left out, or lost on the way. */
function Mark({ state }: { state: Upload["state"] }) {
  switch (state) {
    case "waiting":
      return <Clock size={14} aria-hidden className="mt-0.5 shrink-0 text-muted" />;
    case "adding":
      return (
        <span aria-hidden className="working-dots mt-2 shrink-0 text-muted">
          <span />
          <span />
          <span />
        </span>
      );
    case "added":
      return <CircleCheck size={14} aria-hidden className="mt-0.5 shrink-0 text-ok" />;
    case "refused":
      return <TriangleAlert size={14} aria-hidden className="mt-0.5 shrink-0 text-warn" />;
    case "failed":
      return <CircleAlert size={14} aria-hidden className="mt-0.5 shrink-0 text-error" />;
  }
}

/** Folders dropped from a file manager, by name: a folder arrives as a file with no
 * type, and what is in it is not given. Browsers that cannot tell give none. */
function foldersIn(items: DataTransferItemList | undefined): Set<string> {
  const names = new Set<string>();
  for (const item of Array.from(items ?? [])) {
    const entry = item.kind === "file" ? item.webkitGetAsEntry?.() : null;
    if (entry?.isDirectory) names.add(entry.name);
  }
  return names;
}

export function AddDocuments({
  onChanged,
  projectId,
}: {
  onChanged: () => void;
  /** Keeps the files to this project (MV7b) instead of My documents. */
  projectId?: string;
}) {
  const uid = useId();
  const input = useRef<HTMLInputElement>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  // The files still to go, in order, and what is sending now.
  const waiting = useRef<{ key: string; file: File }[]>([]);
  const running = useRef(false);
  const stop = useRef<AbortController | null>(null);
  const counter = useRef(0);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  // Leaving the screen stops the file being sent and the ones that wait.
  useEffect(() => {
    const controller = new AbortController();
    stop.current = controller;
    return () => controller.abort();
  }, []);

  function show(key: string, change: Partial<Upload>) {
    setUploads((now) => now.map((row) => (row.key === key ? { ...row, ...change } : row)));
  }

  async function run() {
    if (running.current) return;
    running.current = true;
    try {
      for (let next = waiting.current.shift(); next; next = waiting.current.shift()) {
        const { key, file } = next;
        show(key, { state: "adding", progress: 0 });
        try {
          const form = new FormData();
          form.append("files", file, file.name);
          if (projectId) form.append("projectId", projectId);
          const made = await upload<AddedFiles>("/api/documents", form, {
            signal: stop.current?.signal,
            onProgress: (progress) => show(key, { progress }),
          });
          const refused = made.refused[0];
          const kept = made.documents[0];
          if (refused) show(key, { state: "refused", message: refused.message });
          else {
            show(key, {
              state: "added",
              progress: 1,
              message: kept?.unchanged
                ? `${file.name} is already in ${projectId ? "this project" : "your documents"}, exactly as it is.`
                : kept && kept.replaced.length > 0
                  ? "Added. It takes the place of the older file with the same name."
                  : null,
            });
          }
        } catch (failure) {
          if (stop.current?.signal.aborted) return;
          const why = refusedBy(failure, file.name);
          // A refusal says why in the server's words; a lost connection says that.
          show(key, {
            state: why.status >= 400 && why.status < 500 ? "refused" : "failed",
            message: why.message,
          });
        }
        changed.current();
      }
    } finally {
      running.current = false;
    }
  }

  function add(files: File[], folders: string[] = []) {
    const rows: Upload[] = [];
    for (const name of folders) {
      rows.push({
        key: `u${++counter.current}`,
        name,
        size: 0,
        state: "refused",
        progress: 0,
        message: `${name} is a folder. Choose or drop the files in it.`,
      });
    }
    for (const file of files) {
      const key = `u${++counter.current}`;
      waiting.current.push({ key, file });
      rows.push({
        key,
        name: file.name,
        size: file.size,
        state: "waiting",
        progress: 0,
        message: null,
      });
    }
    if (rows.length === 0) return;
    setUploads((now) => [...now, ...rows]);
    void run();
  }

  const going = uploads.some((row) => row.state === "waiting" || row.state === "adding");
  // Once every file has gone, a file that was added with nothing more to say is in the
  // list below, and its line is not said again here. A refusal stays until it is read,
  // and so does a note ("already there", "takes the place of the older file").
  const shown = going
    ? uploads
    : uploads.filter((row) => !(row.state === "added" && row.message === null));

  return (
    <div
      data-testid="documents-drop"
      className={`card card--add ${dragging ? "border-accent ring-2 ring-inset ring-accent" : ""}`}
      onDragEnter={(event) => {
        if (!event.dataTransfer?.types.includes("Files")) return;
        event.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer?.types.includes("Files")) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        const folders = foldersIn(event.dataTransfer?.items);
        const files = Array.from(event.dataTransfer?.files ?? []);
        add(
          files.filter((file) => !(file.type === "" && folders.has(file.name))),
          [...folders],
        );
      }}
    >
      <h3 id={`${uid}-add`} className="title-card">
        Add files
      </h3>
      <p id={`${uid}-help`} className="help">
        {dragging
          ? "Drop the files to add them."
          : "Choose files, or drop them here. Workbench reads each one so a chat can search it."}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <input
          ref={input}
          type="file"
          multiple
          accept={DOCUMENT_ACCEPT}
          className="hidden"
          aria-label={`Choose files to add to ${projectId ? "this project" : "your documents"}`}
          data-testid="documents-input"
          onChange={(event) => {
            add(Array.from(event.target.files ?? []));
            // The same file can be chosen again.
            event.target.value = "";
          }}
        />
        <button
          type="button"
          data-testid="documents-add"
          aria-describedby={`${uid}-help`}
          onClick={() => input.current?.click()}
          className="btn btn-primary"
        >
          <FileUp size={14} aria-hidden /> Choose files
        </button>
      </div>
      {uploads.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="documents-uploads">
          <p role="status" data-testid="documents-upload-summary" className="text-sm">
            {uploadSummary(uploads)}
          </p>
          {going && (
            <p className="text-xs text-muted">Stay on this page until every file is added.</p>
          )}
          {shown.length > 0 && (
            <ul className="max-h-80 divide-y divide-line overflow-y-auto rounded-plexus border border-line bg-panel">
              {shown.map((row) => (
                <li
                  key={row.key}
                  data-testid="documents-upload"
                  data-state={row.state}
                  className="flex flex-col gap-1 px-3 py-2 text-sm"
                >
                  <div className="flex items-start gap-2">
                    {row.size === 0 && row.state === "refused" ? (
                      <Folder size={14} aria-hidden className="mt-0.5 shrink-0 text-warn" />
                    ) : (
                      <Mark state={row.state} />
                    )}
                    <span
                      data-testid="documents-upload-name"
                      className="min-w-0 flex-1 font-medium wrap-anywhere"
                    >
                      {row.name}
                    </span>
                    {row.size > 0 && (
                      <span className="shrink-0 text-xs tabular-nums text-muted">
                        {sizeWords(row.size)}
                      </span>
                    )}
                  </div>
                  {row.state === "adding" && (
                    <progress
                      aria-label={`Adding ${row.name}`}
                      max={100}
                      value={Math.round(row.progress * 100)}
                      className="h-1.5 w-full"
                    />
                  )}
                  <p
                    data-testid="documents-upload-words"
                    className={`ps-6 wrap-anywhere ${
                      row.state === "refused"
                        ? "text-warn"
                        : row.state === "failed"
                          ? "text-error"
                          : "text-muted"
                    }`}
                  >
                    {uploadWords(row)}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {!going && (
            <div>
              <button
                type="button"
                data-testid="documents-uploads-clear"
                onClick={() => setUploads([])}
                className="btn btn-quiet -ms-2"
              >
                Clear this list
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
