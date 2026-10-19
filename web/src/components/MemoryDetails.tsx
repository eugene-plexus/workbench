/**
 * A note's details on the Memory page: the long text a note may carry beside its one-line
 * title, the versions it replaced ("Earlier versions", never sent to a model), and the search
 * that finds the notes the page does not list (the page shows the newest 200 of the rest).
 */

import { CircleAlert } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { api, del } from "../lib/api";
import { excerpt, failureWords, kindLabel, noteDate, plural, showingWords } from "../lib/memory";
import type { MemoryNote, MemoryPage, NotesFound, NoteWhole } from "../lib/types";
import { ScrollText } from "./ScrollText";
import { TagChips } from "./MemoryTags";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

/** One note whole, and the versions it replaced. */
export async function readWhole(id: string): Promise<NoteWhole> {
  const got = await api<NoteWhole>(`/api/memory/notes/${encodeURIComponent(id)}`);
  return { note: got.note, earlier: got.earlier ?? [] };
}

export function Details({
  note,
  busy,
  working,
}: {
  note: MemoryNote;
  busy: boolean;
  working: Working;
}) {
  const uid = useId();
  const [whole, setWhole] = useState<NoteWhole | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // Bumped to read the note again after an earlier version is deleted.
  const [again, setAgain] = useState(0);

  // The details are read when they are opened, and again when the note is saved in place.
  useEffect(() => {
    let live = true;
    readWhole(note.id).then(
      (got) => {
        if (!live) return;
        setWhole(got);
        setProblem(null);
      },
      (failure: unknown) => {
        if (live) setProblem(failureWords(failure));
      },
    );
    return () => {
      live = false;
    };
  }, [note.id, note.updatedAt, again]);

  async function remove(earlier: MemoryNote) {
    setProblem(null);
    try {
      await working(() => del(`/api/memory/notes/${earlier.id}`));
    } catch (failure) {
      setProblem(failureWords(failure));
      return;
    }
    setAgain((n) => n + 1);
  }

  return (
    <div data-testid="memory-note-panel" className="flex flex-col gap-3 text-sm">
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {!whole && !problem && (
        <p role="status" className="text-muted">
          Reading this note…
        </p>
      )}
      {whole && (
        <>
          {whole.note.body ? (
            <section aria-labelledby={`${uid}-body`} className="flex flex-col gap-1">
              <h4 id={`${uid}-body`} className="eyebrow text-muted">
                Details
              </h4>
              <ScrollText
                label={`Details of note: ${excerpt(note)}`}
                testId="memory-note-body"
                text={whole.note.body}
              />
            </section>
          ) : (
            <p data-testid="memory-note-no-body" className="help">
              This note has no details.
            </p>
          )}
          <section aria-labelledby={`${uid}-earlier`} className="flex flex-col gap-1">
            <h4 id={`${uid}-earlier`} className="eyebrow text-muted">
              Earlier versions
            </h4>
            {whole.earlier.length === 0 ? (
              <p data-testid="memory-earlier-none" className="help">
                No earlier versions.
              </p>
            ) : (
              <ul data-testid="memory-earlier" className="divide-y divide-line">
                {whole.earlier.map((old) => (
                  <li key={old.id} data-testid="memory-earlier-version" className="py-2">
                    <p dir="auto" className="wrap-anywhere">
                      {old.text}
                    </p>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
                      <span className="badge">{kindLabel(old.kind)}</span>
                      <TagChips tags={old.tags} />
                      <span data-testid="memory-earlier-date">
                        Replaced on {noteDate(old.supersededAt ?? old.updatedAt)}
                      </span>
                      <button
                        type="button"
                        data-testid="memory-earlier-delete"
                        aria-label={`Delete earlier version: ${excerpt(old)}`}
                        disabled={busy}
                        onClick={() => void remove(old)}
                        className="btn btn-quiet btn-danger"
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}

/** Search every note, by words and meaning, and by tag: for the notes the page does not list. */
export function NoteSearch({
  data,
  list,
}: {
  data: MemoryPage;
  /** The same list the page draws its notes with. */
  list: (notes: MemoryNote[]) => ReactNode;
}) {
  const uid = useId();
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [found, setFound] = useState<NotesFound | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  // What was last searched for: the page reading again re-runs it, so a note that was pinned or
  // deleted does not stay in the results.
  const asked = useRef<{ query: string; tag: string } | null>(null);
  const latest = useRef(0);
  const PER = 50;

  async function run(what: { query: string; tag: string }, offset: number, limit = PER) {
    const mine = ++latest.current;
    const params = new URLSearchParams();
    if (what.query) params.set("query", what.query);
    if (what.tag) params.set("tag", what.tag);
    params.set("offset", String(offset));
    params.set("limit", String(limit));
    setReading(true);
    try {
      const got = await api<NotesFound>(`/api/memory/notes?${params.toString()}`);
      if (mine !== latest.current) return;
      setFound((was) =>
        offset > 0 && was ? { ...got, offset: 0, notes: [...was.notes, ...got.notes] } : got,
      );
      setProblem(null);
    } catch (failure) {
      if (mine !== latest.current) return;
      setProblem(failureWords(failure));
    } finally {
      if (mine === latest.current) setReading(false);
    }
  }

  const shown = found?.notes.length ?? 0;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (asked.current) void run(asked.current, 0, Math.max(shown, PER));
    // Only when the page has read the notes again, not each time the results change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const what = { query: query.trim(), tag };
    if (!what.query && !what.tag) {
      clear();
      return;
    }
    asked.current = what;
    void run(what, 0);
  }

  function clear() {
    latest.current += 1;
    asked.current = null;
    setFound(null);
    setProblem(null);
    setQuery("");
    setTag("");
  }

  return (
    <div data-testid="memory-search" className="flex flex-col gap-2">
      <p data-testid="memory-showing" className="help">
        {showingWords(data.sections.other.length, data.sections.otherTotal)}. Search to find the
        rest.
      </p>
      <form
        role="search"
        aria-label="Search your notes"
        className="flex flex-wrap items-end gap-2"
        onSubmit={submit}
      >
        <div className="field">
          <label htmlFor={`${uid}-query`} className="field-label">
            Search your notes
          </label>
          <input
            id={`${uid}-query`}
            data-testid="memory-search-input"
            type="search"
            value={query}
            autoComplete="off"
            placeholder="cedar #woodwork"
            onChange={(event) => setQuery(event.target.value)}
            className="control w-64 max-w-full"
          />
        </div>
        {data.tags.length > 0 && (
          <div className="field">
            <label htmlFor={`${uid}-tag`} className="field-label">
              Only notes tagged
            </label>
            <select
              id={`${uid}-tag`}
              data-testid="memory-search-tag"
              value={tag}
              onChange={(event) => setTag(event.target.value)}
              className="control"
            >
              <option value="">Any tag</option>
              {data.tags.map((t) => (
                <option key={t.key} value={t.tag}>
                  {t.tag}
                </option>
              ))}
            </select>
          </div>
        )}
        <button type="submit" data-testid="memory-search-go" disabled={reading} className="btn">
          Search
        </button>
        {(found || query || tag) && (
          <button
            type="button"
            data-testid="memory-search-clear"
            onClick={clear}
            className="btn btn-quiet"
          >
            Clear search
          </button>
        )}
      </form>
      {problem && (
        <p role="alert" data-testid="memory-search-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {found && (
        <section aria-label="Search results" data-testid="memory-search-results">
          <p role="status" data-testid="memory-search-count" className="help">
            {found.total === 0 ? "No notes matched." : `${plural(found.total, "note")} found.`}
            {found.search?.why ? ` ${found.search.why}` : ""}
          </p>
          {found.notes.length > 0 && list(found.notes)}
          {found.total > shown && (
            <button
              type="button"
              data-testid="memory-search-more"
              disabled={reading}
              onClick={() => asked.current && void run(asked.current, shown)}
              className="btn mt-2"
            >
              Show more
            </button>
          )}
        </section>
      )}
    </div>
  );
}
