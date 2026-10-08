import { FileText, Music, Pencil, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { fileUrl, post } from "../lib/api";
import type { AttachedFile, Message, Progress } from "../lib/types";
import {
  answerParts,
  DRAFT_HINT,
  DRAFT_LABEL,
  progressWords,
  redactedNote,
  reasoningParts,
  SEARCHED_MARK,
  statusWords,
} from "../lib/words";
import { Markdown } from "./Markdown";
import { CopyButton } from "./CopyButton";
import { ToolCalls } from "./ToolCalls";
import { WorkingScene } from "./WorkingScene";

export function MessageView({
  chatId,
  message,
  progress,
  last,
  busy,
  readOnly,
  onChanged,
}: {
  chatId: string;
  message: Message;
  progress: Progress | null;
  last: boolean;
  busy: boolean;
  readOnly: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  if (message.redacted) {
    return (
      <p
        data-testid="redacted-message"
        className="rounded-plexus border border-line px-3 py-2 text-sm text-muted"
      >
        {redactedNote(message.redacted.site)}
      </p>
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
              aria-label="Your message"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              rows={4}
              disabled={acting}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === "Escape" && !acting) setEditing(null);
              }}
              className="rounded-plexus border border-line bg-soft p-2"
            />
            <p className="text-xs text-muted">
              Asking again replaces everything after this message.
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
          {editing === null && message.content && <CopyButton text={message.content} />}
          {!readOnly && editing === null && !busy && (
            <button
              type="button"
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
  const waiting = message.status === "running" && !answer;
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
              </li>
            ))}
          </ol>
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
      {message.status !== "running" && (
        <div className="flex flex-wrap items-center gap-3 break-all text-xs text-muted">
          <MessageTime message={message} />
          {answer && <CopyButton text={answer} />}
          {last && !readOnly && !busy && (
            <button
              type="button"
              data-testid="try-again"
              disabled={acting}
              onClick={async () => {
                if (acting) return;
                setActing(true);
                setProblem(null);
                try {
                  await post(`/api/chats/${chatId}/retry`);
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
          {message.model && <span>{message.model}</span>}
        </div>
      )}
      {problem && (
        <p role="alert" className="text-sm text-error">
          {problem}
        </p>
      )}
    </article>
  );
}

function MessageTime({ message }: { message: Message }) {
  const date = new Date(message.createdAt * 1000);
  return (
    <time dateTime={date.toISOString()} title={date.toLocaleString()}>
      {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </time>
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
      <img
        src={url}
        alt={file.name}
        className="h-24 rounded-plexus border border-line object-cover"
      />
    );
  }
  const Icon = file.mediaType.startsWith("audio/") ? Music : FileText;
  return (
    <span className="flex items-center gap-1 rounded-plexus border border-line bg-soft px-2 py-1 text-sm">
      <Icon size={14} aria-hidden /> {file.name}
    </span>
  );
}
