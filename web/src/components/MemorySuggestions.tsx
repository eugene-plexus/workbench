/**
 * Learn from my chats (MR8) on the Memory page: the setting, the suggestions waiting for the
 * person, and the one "Still true?" question about a project.
 *
 * A suggestion is a note a chat's model proposed from the person's own words. It shows those
 * words, the chat it came from, and, when it would replace a note, the note it replaces.
 * Nothing in it is used until the person keeps it; turning it away means it is never
 * suggested again.
 */

import { CircleAlert, Lightbulb, MessageSquareQuote } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

import { post } from "../lib/api";
import {
  characters,
  failureWords,
  KINDS,
  kindLabel,
  LEARN_CHOICES,
  learnSetByWords,
  NOTE_LIMIT,
  USE_CHOICES,
} from "../lib/memory";
import type { MemoryNote, MemoryPage, MemorySuggestion, NoteKind } from "../lib/types";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

export function LearnSettings({
  data,
  busy,
  onPrefs,
}: {
  data: MemoryPage;
  busy: boolean;
  onPrefs: (body: { learn?: "off" | "suggest" | null; useMode?: "relevant" | "all" }) => void;
}) {
  const uid = useId();
  const { prefs } = data;
  const { effective } = prefs;
  const allowed = effective.suggestionsAllowed;
  return (
    <>
      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="memory-learn">
        <legend className="field-label mb-1">Learn from my chats</legend>
        {LEARN_CHOICES.map((choice) => (
          <div key={choice.value}>
            <label className="check">
              <input
                type="radio"
                name={`${uid}-learn`}
                value={choice.value}
                data-testid={`memory-learn-${choice.value}`}
                aria-describedby={`${uid}-learn-${choice.value}`}
                checked={effective.learn === choice.value}
                disabled={busy || !allowed}
                onChange={() => onPrefs({ learn: choice.value })}
              />
              <span>{choice.label}</span>
            </label>
            <p id={`${uid}-learn-${choice.value}`} className="help ps-[1.625rem]">
              {choice.help}
            </p>
          </div>
        ))}
        <p data-testid="memory-learn-effective" className="help">
          {learnSetByWords(effective.learnSetBy)}
        </p>
        {prefs.learn !== null && allowed && (
          <button
            type="button"
            data-testid="memory-learn-default"
            disabled={busy}
            onClick={() => onPrefs({ learn: null })}
            className="btn btn-quiet -ms-2 self-start"
          >
            Use the default
          </button>
        )}
      </fieldset>
      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="memory-use-mode">
        <legend className="field-label mb-1">Which notes a chat is given</legend>
        {USE_CHOICES.map((choice) => (
          <div key={choice.value}>
            <label className="check">
              <input
                type="radio"
                name={`${uid}-use-mode`}
                value={choice.value}
                data-testid={`memory-use-${choice.value}`}
                aria-describedby={`${uid}-use-${choice.value}`}
                checked={prefs.useMode === choice.value}
                disabled={busy}
                onChange={() => onPrefs({ useMode: choice.value })}
              />
              <span>{choice.label}</span>
            </label>
            <p id={`${uid}-use-${choice.value}`} className="help ps-[1.625rem]">
              {choice.help}
            </p>
          </div>
        ))}
      </fieldset>
    </>
  );
}

export function Suggestions({
  suggestions,
  busy,
  locked,
  from,
  working,
  onProblem,
}: {
  suggestions: MemorySuggestion[];
  busy: boolean;
  /** The owner has turned Memory off: suggestions can be turned away, not kept. */
  locked: boolean;
  from: (note: MemoryNote) => ReactNode;
  working: Working;
  onProblem: (words: string | null) => void;
}) {
  const uid = useId();
  if (suggestions.length === 0) return null;
  return (
    <section
      aria-labelledby={`${uid}-suggestions`}
      data-testid="memory-suggestions"
      className="section"
    >
      <h2 id={`${uid}-suggestions`} className="title-section">
        <Lightbulb size={18} aria-hidden />
        Suggested notes
      </h2>
      <p className="help">
        Your chats&apos; models suggested these from what you wrote. Keep the ones that are right;
        nothing here is given to a model until you do.
      </p>
      <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
        {suggestions.map((s) => (
          <SuggestionItem
            key={s.id}
            suggestion={s}
            busy={busy}
            locked={locked}
            from={from(s)}
            working={working}
            onProblem={onProblem}
          />
        ))}
      </ul>
    </section>
  );
}

function SuggestionItem({
  suggestion,
  busy,
  locked,
  from,
  working,
  onProblem,
}: {
  suggestion: MemorySuggestion;
  busy: boolean;
  locked: boolean;
  from: ReactNode;
  working: Working;
  onProblem: (words: string | null) => void;
}) {
  const uid = useId();
  const [draft, setDraft] = useState<{ text: string; kind: NoteKind } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  async function keep(replace: boolean) {
    setProblem(null);
    onProblem(null);
    const text = draft?.text.trim();
    try {
      await working(() =>
        post(`/api/memory/suggestions/${suggestion.id}/keep`, {
          replace,
          ...(draft && text !== suggestion.text ? { text } : {}),
          ...(draft && draft.kind !== suggestion.kind ? { kind: draft.kind } : {}),
        }).then(() => undefined),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  async function dismiss() {
    setProblem(null);
    onProblem(null);
    try {
      await working(() =>
        post(`/api/memory/suggestions/${suggestion.id}/dismiss`, {}).then(() => undefined),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  const over = draft ? characters(draft.text) > NOTE_LIMIT : false;
  const empty = draft ? !draft.text.trim() : false;
  return (
    <li data-testid="memory-suggestion" className="flex flex-col gap-2 px-4 py-3">
      {draft ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={`${uid}-text`} className="field-label">
            The note
          </label>
          <textarea
            id={`${uid}-text`}
            data-testid="memory-suggestion-text"
            dir="auto"
            rows={2}
            value={draft.text}
            disabled={busy}
            aria-invalid={over || undefined}
            onChange={(event) => setDraft({ ...draft, text: event.target.value })}
            className="control"
          />
          <p className="help">
            {characters(draft.text)} of {NOTE_LIMIT} characters
          </p>
          <label htmlFor={`${uid}-kind`} className="field-label">
            Kind
          </label>
          <select
            id={`${uid}-kind`}
            data-testid="memory-suggestion-kind"
            value={draft.kind}
            disabled={busy}
            onChange={(event) => setDraft({ ...draft, kind: event.target.value as NoteKind })}
            className="control w-48"
          >
            {KINDS.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <p dir="auto" data-testid="memory-suggestion-words" className="wrap-anywhere">
          {suggestion.text} <span className="badge">{kindLabel(suggestion.kind)}</span>
        </p>
      )}
      {suggestion.quote && (
        <p data-testid="memory-suggestion-quote" className="help flex items-start gap-1.5">
          <MessageSquareQuote size={14} aria-hidden className="mt-0.5 shrink-0" />
          <span dir="auto" className="wrap-anywhere">
            You wrote: {"“"}
            {suggestion.quote}
            {"”"} {from}
          </span>
        </p>
      )}
      {suggestion.replaces && (
        <p data-testid="memory-suggestion-replaces" className="help wrap-anywhere">
          Replaces your note: <s dir="auto">{suggestion.replaces.text}</s>
        </p>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {suggestion.replaces ? (
          <>
            <button
              type="button"
              data-testid="memory-suggestion-keep-new"
              disabled={busy || locked || over || empty}
              className="btn btn-primary"
              onClick={() => void keep(true)}
            >
              Keep new
            </button>
            <button
              type="button"
              data-testid="memory-suggestion-keep-both"
              disabled={busy || locked || over || empty}
              className="btn"
              onClick={() => void keep(false)}
            >
              Keep both
            </button>
          </>
        ) : (
          <button
            type="button"
            data-testid="memory-suggestion-keep"
            disabled={busy || locked || over || empty}
            className="btn btn-primary"
            onClick={() => void keep(false)}
          >
            Keep
          </button>
        )}
        {!draft && !locked && (
          <button
            type="button"
            data-testid="memory-suggestion-edit"
            disabled={busy}
            className="btn"
            onClick={() => setDraft({ text: suggestion.text, kind: suggestion.kind })}
          >
            Edit
          </button>
        )}
        <button
          type="button"
          data-testid="memory-suggestion-dismiss"
          disabled={busy}
          className="btn btn-quiet"
          onClick={() => void dismiss()}
        >
          Dismiss
        </button>
      </div>
    </li>
  );
}

export function StillTrue({
  note,
  busy,
  working,
  onProblem,
}: {
  note: MemoryNote;
  busy: boolean;
  working: Working;
  onProblem: (words: string | null) => void;
}) {
  async function answer(value: "yes" | "no" | "later") {
    onProblem(null);
    try {
      await working(() =>
        post("/api/memory/revisit", { noteId: note.id, answer: value }).then(() => undefined),
      );
    } catch (failure) {
      onProblem(failureWords(failure));
    }
  }
  return (
    <div data-testid="memory-revisit" className="notice">
      <Lightbulb size={16} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <span>
          <span className="notice-title">Still true?</span>
          <span dir="auto" className="wrap-anywhere">
            {"“"}
            {note.text}
            {"”"}
          </span>
        </span>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-testid="memory-revisit-yes"
            disabled={busy}
            className="btn"
            onClick={() => void answer("yes")}
          >
            Still true
          </button>
          <button
            type="button"
            data-testid="memory-revisit-no"
            disabled={busy}
            className="btn"
            onClick={() => void answer("no")}
          >
            Not any more: delete it
          </button>
          <button
            type="button"
            data-testid="memory-revisit-later"
            disabled={busy}
            className="btn btn-quiet"
            onClick={() => void answer("later")}
          >
            Ask me next week
          </button>
        </div>
      </div>
    </div>
  );
}
