import { Copy, FileText, Music, Pencil, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { fileUrl, post } from "../lib/api";
import type { AttachedFile, Message, Progress } from "../lib/types";
import {
  answerParts,
  DRAFT_HINT,
  DRAFT_LABEL,
  progressWords,
  reasoningParts,
  SEARCHED_MARK,
  statusWords,
} from "../lib/words";
import { Markdown } from "./Markdown";
import { ToolCalls } from "./ToolCalls";

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
              try {
                await post(`/api/chats/${chatId}/messages/${message.id}/edit`, {
                  content: editing,
                });
                setEditing(null);
                onChanged();
              } catch (error) {
                setProblem(error instanceof Error ? error.message : String(error));
              }
            }}
          >
            <textarea
              aria-label="Your message"
              value={editing}
              onChange={(e) => setEditing(e.target.value)}
              rows={4}
              className="rounded-plexus border border-line bg-soft p-2"
            />
            <p className="text-xs text-muted">
              Asking again replaces everything after this message.
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setEditing(null)} className="px-3 py-1">
                Cancel
              </button>
              <button type="submit" className="rounded-plexus bg-accent px-3 py-1 text-on-accent">
                Save and ask again
              </button>
            </div>
          </form>
        ) : (
          message.content && (
            <div className="max-w-2xl whitespace-pre-wrap rounded-plexus bg-soft px-3 py-2">
              {message.content}
            </div>
          )
        )}
        {problem && <p className="text-sm text-error">{problem}</p>}
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
        <p aria-live="polite" className="text-muted" data-testid="progress">
          {progressWords(progress)}
        </p>
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
        <div className="flex gap-3 text-xs text-muted">
          {answer && (
            <button
              type="button"
              onClick={() => void navigator.clipboard?.writeText(answer)}
              className="flex items-center gap-1 hover:text-fg"
            >
              <Copy size={12} aria-hidden /> Copy
            </button>
          )}
          {last && !readOnly && !busy && (
            <button
              type="button"
              data-testid="try-again"
              onClick={async () => {
                try {
                  await post(`/api/chats/${chatId}/retry`);
                  onChanged();
                } catch (error) {
                  setProblem(error instanceof Error ? error.message : String(error));
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
      {problem && <p className="text-sm text-error">{problem}</p>}
    </article>
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
