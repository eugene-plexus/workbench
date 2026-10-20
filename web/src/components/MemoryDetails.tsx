/**
 * A note's details on the Memory page: the long text a note may carry beside its one-line
 * title, the versions it replaced ("Earlier versions", never sent to a model), and the search
 * that finds the notes the page does not list (the page shows the newest 200 of the rest).
 */

import { CircleAlert } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";

import { api, del, post } from "../lib/api";
import { excerpt, failureWords, kindLabel, noteDate, plural } from "../lib/memory";
import type {
  DocumentPlace,
  DocumentsPage,
  MemoryNote,
  MemoryPage,
  NoteDocument,
  NotesFound,
  NoteWhole,
  Project,
} from "../lib/types";
import { ScrollText } from "./ScrollText";
import { TagChips } from "./MemoryTags";

type Working = <T>(work: () => Promise<T>) => Promise<T>;

/** One note whole, and the versions it replaced. */
export async function readWhole(id: string): Promise<NoteWhole> {
  const got = await api<NoteWhole>(`/api/memory/notes/${encodeURIComponent(id)}`);
  return { note: got.note, earlier: got.earlier ?? [], documents: got.documents ?? [] };
}

/** Where in a document an answer found it: "page 4", "pages 4-5", "lines 9-12", then the heading. */
export function placeWords(place: DocumentPlace): string {
  const parts: string[] = [];
  const span = (one: string, many: string, from?: number | null, to?: number | null) => {
    if (from == null) return;
    parts.push(to != null && to !== from ? `${many} ${from}-${to}` : `${one} ${from}`);
  };
  span("page", "pages", place.page_start, place.page_end);
  span("line", "lines", place.line_start, place.line_end);
  if (place.heading) parts.push(place.heading);
  return parts.join(", ");
}

/** The states in which a document's words can be read, so a note may be based on it. */
// A document can be linked once it has been read: not while it waits or is read, nor when it
// could not be.
const NOT_READ = new Set(["queued", "reading", "skipped", "failed"]);

/** The documents a note is based on, and the one place to link another. */
function BasedOn({
  note,
  documents,
  busy,
  working,
  onChanged,
}: {
  note: MemoryNote;
  documents: NoteDocument[];
  busy: boolean;
  working: Working;
  onChanged: () => void;
}) {
  const uid = useId();
  const [mine, setMine] = useState<DocumentsPage["documents"]>([]);
  const [choice, setChoice] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const linked = new Set(documents.map((d) => d.docId));
  const options = mine.filter((d) => !NOT_READ.has(d.state) && !linked.has(d.id));

  // The person's documents are read once, for the picker; a failure says why it is empty.
  useEffect(() => {
    let live = true;
    api<DocumentsPage>("/api/documents").then(
      (got) => {
        if (live) setMine(got.documents ?? []);
      },
      (failure: unknown) => {
        if (live) setProblem(`Your documents could not be listed: ${failureWords(failure)}`);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  async function link() {
    if (!choice) return;
    setProblem(null);
    try {
      await working(() =>
        post(`/api/memory/notes/${encodeURIComponent(note.id)}/documents`, { docId: choice }),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
      return;
    }
    setChoice("");
    onChanged();
  }

  async function unlink(doc: NoteDocument) {
    setProblem(null);
    try {
      await working(() =>
        del(
          `/api/memory/notes/${encodeURIComponent(note.id)}/documents/${encodeURIComponent(doc.docId)}`,
        ),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
      return;
    }
    onChanged();
  }

  return (
    <section
      aria-labelledby={`${uid}-documents`}
      data-testid="memory-documents"
      className="flex flex-col gap-1"
    >
      <h4 id={`${uid}-documents`} className="eyebrow text-muted">
        Based on a document
      </h4>
      {documents.length === 0 ? (
        <p data-testid="memory-documents-none" className="help">
          This note is not based on a document.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {documents.map((doc) => {
            const where = doc.how === "answer" ? doc.place.map(placeWords).filter(Boolean) : [];
            return (
              <li
                key={doc.docId}
                data-testid="memory-document"
                className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2"
              >
                <span dir="auto" className="min-w-0 wrap-anywhere">
                  {doc.name}
                </span>
                {where.length > 0 && (
                  <span data-testid="memory-document-place" className="text-xs text-muted">
                    {where.join("; ")}
                  </span>
                )}
                <button
                  type="button"
                  data-testid="memory-doc-unlink"
                  aria-label={`Unlink document: ${doc.name}`}
                  disabled={busy}
                  onClick={() => void unlink(doc)}
                  className="btn btn-quiet"
                >
                  Unlink
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {problem && (
        <p role="alert" data-testid="memory-documents-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {options.length > 0 && (
        <div className="flex flex-wrap items-end gap-2">
          <div className="field">
            <label htmlFor={`${uid}-pick`} className="field-label">
              Link a document
            </label>
            <select
              id={`${uid}-pick`}
              data-testid="memory-doc-pick"
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
              className="control max-w-full"
            >
              <option value="">Choose a document</option>
              {options.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            data-testid="memory-doc-link"
            disabled={busy || !choice}
            onClick={() => void link()}
            className="btn"
          >
            Link
          </button>
        </div>
      )}
    </section>
  );
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
          {whole.note.status === "active" && (
            <BasedOn
              note={whole.note}
              documents={whole.documents ?? []}
              busy={busy}
              working={working}
              onChanged={() => setAgain((n) => n + 1)}
            />
          )}
        </>
      )}
    </div>
  );
}

/** Search every note, by words and meaning, and by tag: for the notes the page does not list. */
export function NoteSearch({
  data,
  list,
  where = "all",
  onWhere,
  projects = [],
}: {
  data: MemoryPage;
  /** The same list the page draws its notes with. */
  list: (notes: MemoryNote[]) => ReactNode;
  /** MV7: where the notes are kept: `all`, `general`, or a project's id. */
  where?: string;
  onWhere?: (where: string) => void;
  projects?: Project[];
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
    if (where !== "all") params.set("where", where);
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
        {onWhere && (projects.length > 0 || where !== "all") && (
          <div className="field">
            <label htmlFor={`${uid}-where`} className="field-label">
              Where
            </label>
            <select
              id={`${uid}-where`}
              data-testid="memory-where"
              value={where}
              onChange={(event) => onWhere(event.target.value)}
              className="control"
            >
              <option value="all">All</option>
              <option value="general">General</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
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
