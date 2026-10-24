/**
 * A run of earlier messages the model was sent as a summary (row 26, call CTX-C3: folded).
 * Workbench never changes a message: the run folds into one row, Show them unfolds the originals
 * under a band that says they went as a summary, and Read the summary shows what was sent.
 */

import { ChevronDown, ChevronRight, FileText, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";

import { api } from "../lib/api";
import { sentWords, writtenWords, type Fold } from "../lib/longChats";
import type { Message, SummaryDetail } from "../lib/types";

export function SummaryFold({
  chatId,
  fold,
  open,
  onToggle,
  known,
}: {
  chatId: string;
  fold: Fold;
  /** The originals are shown below. */
  open: boolean;
  onToggle: () => void;
  /** The summary as the chat was loaded with it, when it was. */
  known?: SummaryDetail;
}) {
  const id = useId();
  const [reading, setReading] = useState(false);
  const [detail, setDetail] = useState<SummaryDetail | null>(known ?? null);
  const [problem, setProblem] = useState<string | null>(null);

  async function read() {
    if (reading) {
      setReading(false);
      return;
    }
    setReading(true);
    if (detail && detail.id === fold.id) return;
    setProblem(null);
    try {
      setDetail(await api<SummaryDetail>(`/api/chats/${chatId}/summaries/${fold.id}`));
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div
      className="rounded-plexus border border-line bg-surface px-3 py-2 text-sm"
      data-testid="summary-fold"
      data-from={fold.from}
      data-to={fold.to}
    >
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
        <FileText size={14} aria-hidden className="shrink-0" />
        <span className="font-medium text-strong">Earlier messages summarised</span>
        <span aria-hidden>·</span>
        <span>
          {fold.messages} message{fold.messages === 1 ? "" : "s"}
        </span>
        <span aria-hidden>·</span>
        <button
          type="button"
          className="btn btn-quiet -my-1"
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
          {open ? "Hide them" : "Show them"}
        </button>
        <span aria-hidden>·</span>
        <button
          type="button"
          className="btn btn-quiet -my-1"
          aria-expanded={reading}
          aria-controls={`${id}-summary`}
          onClick={() => void read()}
        >
          Read the summary
        </button>
      </p>
      {reading && (
        <div id={`${id}-summary`} className="mt-2 border-t border-line pt-2">
          {problem && (
            <p role="alert" className="notice notice--error">
              <TriangleAlert size={14} aria-hidden />
              {problem}
            </p>
          )}
          {!problem && !detail && <p className="text-muted">Loading the summary…</p>}
          {detail && (
            <>
              {detail.text != null ? (
                <p className="whitespace-pre-wrap" data-testid="summary-text">
                  {detail.text}
                </p>
              ) : (
                <p className="text-muted">Not shown. {detail.hidden}</p>
              )}
              <p className="help mt-1">{writtenWords(detail)}</p>
            </>
          )}
        </div>
      )}
      {open && (
        <p className="mt-2 border-t border-line pt-2 text-muted" data-testid="summary-band">
          Sent to the model as a summary.
        </p>
      )}
    </div>
  );
}

/** Under an answer: what it was sent of earlier messages, and a backend that dropped part of
 * the chat to fit its window. Nothing for an answer sent whole. */
export function SentWithSummaries({ message }: { message: Message }) {
  if (message.role !== "assistant") return null;
  const sent = sentWords(message.summaries ?? []);
  if (!sent.length && !message.promptTruncated) return null;
  return (
    <div className="max-w-measure text-sm text-muted" data-testid="sent-summaries">
      {sent.length > 0 && <p>Sent with {sent.join(", ")}.</p>}
      {message.promptTruncated && (
        <p className="notice notice--warn mt-1" data-testid="prompt-truncated">
          <TriangleAlert size={14} aria-hidden />
          {message.model ?? "The model"} dropped part of this chat to fit its window, so this answer
          may miss earlier messages. The next message is sent with a summary.
        </p>
      )}
    </div>
  );
}
