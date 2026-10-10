import { ChevronLeft, ChevronRight, FileText, Music, Pencil, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { fileUrl, post } from "../lib/api";
import type { AttachedFile, Message, Progress } from "../lib/types";
import {
  answerParts,
  DRAFT_HINT,
  DRAFT_LABEL,
  EDIT_KEYS,
  EDIT_NOTE,
  progressWords,
  redactedNote,
  reasoningParts,
  SEARCHED_MARK,
  statusWords,
  VERSIONS_WAIT,
} from "../lib/words";
import { Markdown } from "./Markdown";
import { CopyButton } from "./CopyButton";
import { ToolCalls } from "./ToolCalls";
import { WorkingScene } from "./WorkingScene";

export function MessageView({
  chatId,
  message,
  progress,
  busy,
  readOnly,
  onChanged,
  onLook,
}: {
  chatId: string;
  message: Message;
  progress: Progress | null;
  busy: boolean;
  readOnly: boolean;
  onChanged: () => void;
  /** The owner's read-only view: show another version without choosing it. */
  onLook?: (id: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const arrows = (
    <VersionArrows
      chatId={chatId}
      message={message}
      busy={busy}
      readOnly={readOnly}
      onChanged={onChanged}
      onLook={onLook}
      onProblem={setProblem}
    />
  );

  if (message.redacted) {
    return (
      <div className="flex flex-col gap-1">
        <p
          data-testid="redacted-message"
          className="rounded-plexus border border-line px-3 py-2 text-sm text-muted"
        >
          {redactedNote(message.redacted.site)}
        </p>
        {/* Every branch stays reachable, hidden or not (V5). */}
        <div className="flex text-xs text-muted">{arrows}</div>
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1" data-testid="user-message">
        {message.files && message.files.length > 0 && (
          <div className="flex flex-wrap justify-end gap-2">
            {message.files.map((file) => (
              <Attachment key={file.id} file={file} />
            ))}
          </div>
        )}
        {editing !== null ? (
          <form
            className="flex w-full max-w-2xl flex-col gap-2"
            onSubmit={async (e) => {
              e.preventDefault();
              if (acting) return;
              setActing(true);
              setProblem(null);
              try {
                await post(`/api/chats/${chatId}/messages/${message.id}/edit`, {
                  content: editing,
                });
                setEditing(null);
                onChanged();
              } catch (error) {
                setProblem(error instanceof Error ? error.message : String(error));
              } finally {
                setActing(false);
              }
            }}
          >
            <textarea
              aria-label="Edit your message"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              rows={4}
              disabled={acting}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Escape" && !acting) setEditing(null);
                if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
              className="rounded-plexus border border-line bg-soft p-2"
            />
            <p className="text-xs text-muted" data-testid="edit-note">
              {EDIT_NOTE} {EDIT_KEYS}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={acting}
                onClick={() => setEditing(null)}
                className="px-3 py-1"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={acting || (!editing.trim() && message.attachments.length === 0)}
                className="rounded-plexus bg-accent px-3 py-1 text-on-accent"
              >
                Save and ask again
              </button>
            </div>
          </form>
        ) : (
          message.content && (
            <div className="max-w-full whitespace-pre-wrap break-words rounded-plexus bg-soft px-3 py-2 sm:max-w-2xl">
              {message.content}
            </div>
          )
        )}
        {problem && (
          <p role="alert" className="text-sm text-error">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-muted">
          <MessageTime message={message} />
          {editing === null && message.content && (
            <CopyButton text={message.content} label="your message" />
          )}
          {arrows}
          {!readOnly && editing === null && !busy && (
            <button
              type="button"
              aria-label="Edit your message"
              onClick={() => setEditing(message.content)}
              className="flex items-center gap-1 text-xs text-muted hover:text-fg"
            >
              <Pencil size={12} aria-hidden /> Edit
            </button>
          )}
        </div>
      </div>
    );
  }

  const { draft, answer } = answerParts(message);
  const thoughts = reasoningParts(message);
  const running = message.status === "running";
  const waiting = running && !answer;
  const status = statusWords(message);
  const hasTools = Boolean(message.toolRounds?.length);
  const draftLabel = hasTools ? "Written before using tools" : DRAFT_LABEL;
  const draftHint = hasTools
    ? "The model wrote this before it received the tool results."
    : DRAFT_HINT;
  return (
    <article className="flex flex-col gap-2" data-testid="answer" data-status={message.status}>
      {message.reasoning && (
        <details className="rounded-plexus border border-line px-3 py-1.5 text-sm text-muted">
          <summary className="cursor-pointer">Reasoning</summary>
          {thoughts.map((thought, i) => (
            <div key={i}>
              {i > 0 && (
                <p className="mt-1 border-t border-line pt-1 text-xs" data-testid="searched-mark">
                  {hasTools ? "Used tools" : SEARCHED_MARK}
                </p>
              )}
              <div className="whitespace-pre-wrap pt-1">{thought}</div>
            </div>
          ))}
        </details>
      )}
      {draft && (
        <details
          className="rounded-plexus border border-line px-3 py-1.5 text-sm text-muted"
          data-testid="draft"
        >
          <summary className="cursor-pointer" title={draftHint}>
            {draftLabel}
          </summary>
          <p className="pt-1 text-xs">{draftHint}</p>
          <div className="pt-1">
            <Markdown text={draft} />
          </div>
        </details>
      )}
      <ToolCalls chatId={chatId} message={message} readOnly={readOnly} onChanged={onChanged} />
      {waiting && (
        // Eugene works while the model does; waiting on the person's
        // approval is not work, so he steps away then.
        <WorkingScene active={!(progress?.stage === "tool" && progress.phase === "approval")}>
          <p aria-live="polite" className="text-muted" data-testid="progress">
            {progressWords(progress)}
          </p>
        </WorkingScene>
      )}
      {answer && <Markdown text={answer} />}
      {(message.sources.length > 0 || message.searches > 0) && (
        <div className="text-sm" data-testid="sources">
          <h3 className="font-semibold">
            Sources
            {message.searches
              ? ` (${message.searches} search${message.searches === 1 ? "" : "es"})`
              : ""}
          </h3>
          {/* A search ran and the answer linked none of what it found: said,
              so a searched answer never looks like an unsearched one. */}
          {message.sources.length === 0 && (
            <p className="text-muted">The answer does not link to any page the search found.</p>
          )}
          <ol className="list-decimal pl-5">
            {message.sources.map((source) => (
              <li key={source.url}>
                <a
                  href={safe(source.url)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-accent underline"
                >
                  {source.title}
                </a>
                {siteOf(source.url) && (
                  <span className="text-muted" data-testid="source-site">
                    {" "}
                    · {siteOf(source.url)}
                  </span>
                )}
              </li>
            ))}
          </ol>
          {(message.searchSuggestions ?? []).map((html, i) => (
            <SearchSuggestions key={i} html={html} />
          ))}
        </div>
      )}
      {status && (
        <p
          role={message.status === "failed" ? "alert" : undefined}
          data-testid="answer-status"
          className={
            message.status === "failed"
              ? "rounded-plexus border border-error-line bg-error-bg px-3 py-2 text-sm text-error"
              : "text-sm text-muted"
          }
        >
          {status}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3 break-all text-xs text-muted">
        {!running && <MessageTime message={message} />}
        {!running && answer && <CopyButton text={answer} label="this answer" />}
        {/* On any answer (V6); while one runs, it is stopped and kept (V2). */}
        {!readOnly && (
          <button
            type="button"
            data-testid="try-again"
            aria-label="Try again: write a new version of this answer"
            disabled={acting}
            title={
              running
                ? "Stops this answer, keeps it as a version, and writes another."
                : busy
                  ? "Stops the answer being written, keeps it, and tries this one again."
                  : undefined
            }
            onClick={async () => {
              if (acting) return;
              setActing(true);
              setProblem(null);
              try {
                await post(`/api/chats/${chatId}/messages/${message.id}/retry`);
                onChanged();
              } catch (error) {
                setProblem(error instanceof Error ? error.message : String(error));
              } finally {
                setActing(false);
              }
            }}
            className="flex items-center gap-1 hover:text-fg"
          >
            <RotateCcw size={12} aria-hidden /> Try again
          </button>
        )}
        {arrows}
        {message.model && (!running || (message.versions?.count ?? 1) > 1) && (
          <span>{message.model}</span>
        )}
      </div>
      {problem && (
        <p role="alert" className="text-sm text-error">
          {problem}
        </p>
      )}
    </article>
  );
}

/** `‹ 2 of 3 ›`: the versions of a message, or the tries of an answer.
 * Moving chooses that version for every tab; the owner's arrows only look. */
function VersionArrows({
  chatId,
  message,
  busy,
  readOnly,
  onChanged,
  onLook,
  onProblem,
}: {
  chatId: string;
  message: Message;
  busy: boolean;
  readOnly: boolean;
  onChanged: () => void;
  onLook?: (id: string) => void;
  onProblem: (problem: string | null) => void;
}) {
  const [moving, setMoving] = useState(false);
  const versions = message.versions;
  if (!versions || versions.count < 2) return null;
  const what = message.role === "user" ? "Message" : "Answer";
  // Looking changes nothing, so a running answer holds only the person's.
  const held = busy && !readOnly;

  async function go(step: number) {
    const id = versions?.ids[versions.index - 1 + step];
    if (!id || moving) return;
    if (readOnly) {
      onLook?.(id);
      return;
    }
    setMoving(true);
    onProblem(null);
    try {
      await post(`/api/chats/${chatId}/messages/${id}/choose`);
      onChanged();
    } catch (error) {
      onProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setMoving(false);
    }
  }

  const arrow = (step: number, label: string, Icon: typeof ChevronLeft, end: boolean) => (
    <button
      type="button"
      aria-label={label}
      title={held ? VERSIONS_WAIT : label}
      disabled={held || moving || end}
      onClick={() => void go(step)}
      className="rounded-plexus p-0.5 hover:text-fg disabled:opacity-40"
    >
      <Icon size={14} aria-hidden />
    </button>
  );
  return (
    <span
      role="group"
      aria-label={`${what} ${versions.index} of ${versions.count}`}
      title={held ? VERSIONS_WAIT : undefined}
      data-testid="versions"
      className="flex items-center gap-0.5 whitespace-nowrap"
    >
      {arrow(
        -1,
        `Previous version of this ${what.toLowerCase()}`,
        ChevronLeft,
        versions.index <= 1,
      )}
      <span aria-hidden>
        {versions.index} of {versions.count}
      </span>
      {arrow(
        1,
        `Next version of this ${what.toLowerCase()}`,
        ChevronRight,
        versions.index >= versions.count,
      )}
    </span>
  );
}

/** The time alone today; with its date on any earlier day, so an old chat
 * does not read as if it were written this morning. */
export function shortTime(date: Date, now = new Date()): string {
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  const day = date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${day}, ${time}`;
}

function MessageTime({ message }: { message: Message }) {
  const date = new Date(message.createdAt * 1000);
  return (
    <time dateTime={date.toISOString()} title={date.toLocaleString()}>
      {shortTime(date)}
    </time>
  );
}

/** The site a source is on, without `www.`, so a reader sees where it leads. */
export function siteOf(url: string): string | null {
  if (!/^https?:/i.test(url)) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

/** Google's Search Suggestions, exactly as Google sent them (its terms forbid
 * editing, framing around, or interspersing them). They sit in a frame that
 * can run nothing and read nothing of this page: `allow-popups` lets a chip
 * open Google in a new tab, and no `allow-scripts` or `allow-same-origin`
 * leaves the HTML inert. The frame's own page only says where links open, to
 * send no referrer, and that it follows light or dark as Google's CSS does. */
export function searchSuggestionsDocument(html: string): string {
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<meta name="color-scheme" content="light dark">' +
    '<meta name="referrer" content="no-referrer">' +
    '<base target="_blank"></head><body style="margin:0">' +
    html +
    "</body></html>"
  );
}

function SearchSuggestions({ html }: { html: string }) {
  return (
    <iframe
      title="Google Search Suggestions"
      sandbox="allow-popups allow-popups-to-escape-sandbox"
      srcDoc={searchSuggestionsDocument(html)}
      referrerPolicy="no-referrer"
      data-testid="search-suggestions"
      className="mt-2 block h-14 w-full border-0"
    />
  );
}

function safe(url: string): string | undefined {
  return /^https?:/i.test(url) ? url : undefined;
}

function Attachment({ file }: { file: AttachedFile }) {
  const [url, setUrl] = useState<string | null>(null);
  const image = file.mediaType.startsWith("image/");
  useEffect(() => {
    if (!image) return;
    let made: string | null = null;
    let cancelled = false;
    void fileUrl(file.id)
      .then((u) => {
        made = u;
        if (!cancelled) setUrl(u);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [file.id, image]);
  if (image && url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        title={`Open ${file.name} full size`}
        data-testid="attachment-image"
      >
        <img
          src={url}
          alt={file.name}
          className="h-24 rounded-plexus border border-line object-cover"
        />
      </a>
    );
  }
  const Icon = file.mediaType.startsWith("audio/") ? Music : FileText;
  return (
    <span
      title={file.name}
      className="flex max-w-xs items-center gap-1 rounded-plexus border border-line bg-soft px-2 py-1 text-sm"
    >
      <Icon size={14} aria-hidden className="shrink-0" />
      <span className="truncate">{file.name}</span>
    </span>
  );
}
