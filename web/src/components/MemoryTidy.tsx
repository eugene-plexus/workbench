/**
 * MV6: what Memory offers to tidy. Notes that look the same ("These look like the same note":
 * keep one, or both) and an event whose day has passed ("Remove it?"). Nothing changes until the
 * person answers; a note that changed since is left alone and the page says so.
 */

import { CircleAlert, Sparkles } from "lucide-react";

import { post } from "../lib/api";
import { failureWords, kindLabel, noteDate } from "../lib/memory";
import type { MemoryNote, TidyProposal } from "../lib/types";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

type Action =
  | { action: "keep"; keepId: string }
  | { action: "keep_both" }
  | { action: "remove" }
  | { action: "keep_event" };

export function TidyProposals({
  proposals,
  busy,
  working,
  onProblem,
}: {
  proposals: TidyProposal[];
  busy: boolean;
  working: Working;
  onProblem: (words: string | null) => void;
}) {
  if (proposals.length === 0) return null;
  async function answer(proposal: TidyProposal, body: Action) {
    onProblem(null);
    try {
      await working(() =>
        post(`/api/memory/tidy/${encodeURIComponent(proposal.id)}`, body).then(() => undefined),
      );
    } catch (failure) {
      onProblem(failureWords(failure));
    }
  }
  return (
    <div data-testid="memory-tidy" className="flex flex-col gap-2">
      {proposals.map((proposal) =>
        proposal.kind === "duplicate" ? (
          <Duplicate key={proposal.id} proposal={proposal} busy={busy} answer={answer} />
        ) : (
          <Past key={proposal.id} proposal={proposal} busy={busy} answer={answer} />
        ),
      )}
    </div>
  );
}

function Shown({ note }: { note: MemoryNote }) {
  const created = new Date(note.createdAt * 1000);
  return (
    <>
      <p dir="auto" className="wrap-anywhere">
        {note.text}
      </p>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span className="badge">{kindLabel(note.kind)}</span>
        <time dateTime={created.toISOString()} title={created.toLocaleString()}>
          {noteDate(note.createdAt)}
        </time>
      </div>
    </>
  );
}

function Duplicate({
  proposal,
  busy,
  answer,
}: {
  proposal: TidyProposal;
  busy: boolean;
  answer: (proposal: TidyProposal, body: Action) => Promise<void>;
}) {
  return (
    <div data-testid="memory-tidy-duplicate" className="notice">
      <Sparkles size={16} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <span className="notice-title">These look like the same note</span>
        <ul className="flex flex-col gap-2">
          {proposal.notes.map((note) => (
            <li key={note.id} data-testid="memory-tidy-note" className="flex flex-col gap-1">
              <Shown note={note} />
              <div>
                <button
                  type="button"
                  data-testid="memory-tidy-keep"
                  disabled={busy}
                  className="btn"
                  onClick={() => void answer(proposal, { action: "keep", keepId: note.id })}
                >
                  Keep this one
                </button>
              </div>
            </li>
          ))}
        </ul>
        <div>
          <button
            type="button"
            data-testid="memory-tidy-keep-both"
            disabled={busy}
            className="btn btn-quiet"
            onClick={() => void answer(proposal, { action: "keep_both" })}
          >
            Keep both
          </button>
        </div>
      </div>
    </div>
  );
}

function Past({
  proposal,
  busy,
  answer,
}: {
  proposal: TidyProposal;
  busy: boolean;
  answer: (proposal: TidyProposal, body: Action) => Promise<void>;
}) {
  const note = proposal.notes[0];
  if (!note) return null;
  return (
    <div data-testid="memory-tidy-past" className="notice">
      <CircleAlert size={16} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <span>
          <span className="notice-title" data-testid="memory-tidy-past-title">
            {note.expiresAt
              ? `This was on ${noteDate(Date.parse(`${note.expiresAt}T12:00:00`) / 1000)}. Remove it?`
              : "This event has passed. Remove it?"}
          </span>
        </span>
        <Shown note={note} />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="memory-tidy-remove"
            disabled={busy}
            className="btn"
            onClick={() => void answer(proposal, { action: "remove" })}
          >
            Remove
          </button>
          <button
            type="button"
            data-testid="memory-tidy-keep-event"
            disabled={busy}
            className="btn btn-quiet"
            onClick={() => void answer(proposal, { action: "keep_event" })}
          >
            Keep
          </button>
        </div>
      </div>
    </div>
  );
}
