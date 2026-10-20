/**
 * The Memory page: the notes a person keeps, which Workbench gives to the
 * model in their chats. Short notes, written by the person (here, by saying
 * "Remember that ..." in a chat, or from a message's Remember this), shown
 * with where each came from, and always deletable: a note is gone for real.
 *
 * The page is the app, and its settings are on the Settings page (MV4: app before
 * config). What waits for the person comes first (Still true?, Suggested notes,
 * Recently learned), then the notes with their search and tag filter, then a way
 * into past chats, the tags, and the person's own copy. A setting that changes what
 * the page means (Use memory off) is said here and changed there. Where the owner of
 * this Workbench has turned Memory off, the page says so and offers nothing that
 * would keep a note; what a person already has stays theirs to read, export and
 * delete.
 */

import {
  ArrowLeft,
  Brain,
  CircleAlert,
  CircleCheck,
  Eye,
  FileJson,
  FileText,
  Info,
  MessagesSquare,
  Pin,
  PinOff,
  Search,
  Settings as SettingsIcon,
  TriangleAlert,
} from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";

import { api, del, fileOf, patch, post } from "../lib/api";
import {
  characters,
  excerpt,
  failureWords,
  KINDS,
  kindLabel,
  noteDate,
  NOTE_LIMIT,
  OWNER_OFF,
  plural,
  refusalOf,
  saveFile,
  showingWords,
  tagsFrom,
  UNDO_MS,
  usageWords,
} from "../lib/memory";
import type { Chat, MemoryNote, MemoryPage, MemoryTag, NoteKind, NoteWhole } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { Details, NoteSearch, readWhole } from "./MemoryDetails";
import { RecentlyLearned } from "./MemoryLearned";
import { StillTrue, Suggestions } from "./MemorySuggestions";
import { TagChips, Tags, TagsField } from "./MemoryTags";

/** The lines of a box, one note each: trimmed, with the empty ones left out. */
function linesOf(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** A note is one line: a box of one line takes a pasted paragraph as spaces. */
const oneLineOf = (text: string) => text.replace(/\s*[\r\n]+\s*/g, " ");

type Working = <T>(work: () => Promise<T>) => Promise<T>;

export function Memory({
  chats,
  chatsLoaded,
  onOpenChat,
  onOpenSettings,
  onSearchChats,
  onClose,
}: {
  /** The person's chats, to name where a note came from. */
  chats: Chat[];
  chatsLoaded: boolean;
  onOpenChat: (id: string) => void;
  /** Opens the Settings page at Memory's section. */
  onOpenSettings?: () => void;
  /** Opens Search chats with these words searched. */
  onSearchChats?: (query: string) => void;
  onClose: () => void;
}) {
  const uid = useId();
  const [data, setData] = useState<MemoryPage | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Buttons are held while a change is made, which takes the keyboard's place
  // from the one that was pressed: it is given back when the change is done.
  const keepFocus = useKeepFocus(busy);
  const [undo, setUndo] = useState<MemoryNote | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  // The two boxes of a person with no notes stay on the page while some lines
  // have not been kept, though other lines were: the ones left are theirs to fix.
  const [starting, setStarting] = useState(false);
  const latest = useRef(0);

  async function load() {
    const mine = ++latest.current;
    try {
      const next = await api<MemoryPage>("/api/memory");
      if (mine !== latest.current) return;
      setData(next);
      setLoadProblem(null);
    } catch (failure) {
      if (mine !== latest.current) return;
      setLoadProblem(failure instanceof Error ? failure.message : String(failure));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // After a change the thing a person was on may have moved (a pinned note goes
  // to the other list): focus follows it, by id, once the page has redrawn.
  useEffect(() => {
    if (!focusId) return;
    document.getElementById(focusId)?.focus();
    setFocusId(null);
  }, [focusId, data]);

  // A deleted note can be had back for ten seconds, from the words held here.
  useEffect(() => {
    if (!undo) return;
    const timer = window.setTimeout(() => setUndo(null), UNDO_MS);
    return () => window.clearTimeout(timer);
  }, [undo]);

  /** Work that changes the notes or the settings: the page is busy while it
   * runs, and reads again after it, whether it worked or not. */
  const working: Working = async (work) => {
    keepFocus();
    setBusy(true);
    try {
      return await work();
    } finally {
      await load();
      setBusy(false);
    }
  };

  /** The same, for a button on the page: a failure is said at the top, in the
   * server's words. */
  async function act(work: () => Promise<void>, focus?: string) {
    setProblem(null);
    setSaid(null);
    try {
      await working(work);
      if (focus) setFocusId(focus);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  const titles = useMemo(() => new Map(chats.map((chat) => [chat.id, chat.title])), [chats]);

  const header = (
    <header className="page-head">
      <h1 id="view-heading" tabIndex={-1} className="title-page">
        <Brain size={20} aria-hidden />
        Memory
      </h1>
      {onOpenSettings && (
        <button
          type="button"
          data-testid="memory-open-settings"
          className="btn btn-quiet"
          onClick={onOpenSettings}
        >
          <SettingsIcon size={14} aria-hidden />
          Memory settings
        </button>
      )}
      <button type="button" data-testid="memory-back" className="btn" onClick={onClose}>
        <ArrowLeft size={14} aria-hidden />
        Back to chat
      </button>
    </header>
  );

  if (!data) {
    return (
      <section aria-label="Memory" className="page" data-testid="memory-page">
        <div className="page-col">
          {header}
          {loadProblem ? (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read your notes</span>
                {loadProblem}
                <div className="mt-2">
                  <button
                    type="button"
                    data-testid="memory-retry"
                    className="btn"
                    onClick={() => void load()}
                  >
                    Try again
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <p role="status" className="text-sm text-muted">
              Loading your notes…
            </p>
          )}
        </div>
      </section>
    );
  }

  const off = !data.installEnabled;
  const past = data.sections.past ?? [];
  const notes = [...data.sections.always, ...data.sections.other, ...past];
  // "Used when relevant" is said only when it is what happens: the person asked for it and a
  // search model runs to do it. Otherwise the notes are used when there is room, and why is said.
  const relevantOnly = data.prefs.useMode === "relevant" && data.relevance?.available === true;
  const relevanceWhy =
    data.prefs.useMode === "relevant" && !relevantOnly ? data.relevance?.why : null;
  // Where the owner has turned Memory off and the person has no notes, there is
  // nothing to show but that.
  const bare = off && notes.length === 0;

  /** Where a note came from: a link to its chat, or the reason there is none. */
  function whereFrom(note: MemoryNote): ReactNode {
    if (note.sourceConnection) return `Suggested by ${note.sourceConnection}`;
    if (!note.sourceChatId) return note.source === "typed" ? "Written here" : "Kept in a chat";
    const title = titles.get(note.sourceChatId);
    if (title === undefined) return chatsLoaded ? "From a chat that was deleted" : "From a chat";
    return (
      <>
        From{" "}
        <a
          href={`/chats/${encodeURIComponent(note.sourceChatId)}`}
          data-testid="memory-note-from-link"
          className="link"
          onClick={(event) => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
            event.preventDefault();
            onOpenChat(note.sourceChatId!);
          }}
        >
          {title}
        </a>
      </>
    );
  }

  const list = (members: MemoryNote[]) => (
    <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
      {members.map((note) => (
        <NoteItem
          key={note.id}
          note={note}
          locked={off}
          busy={busy}
          from={whereFrom(note)}
          working={working}
          limits={data.limits}
          tags={data.tags}
          onSaid={setSaid}
          onPin={() =>
            void act(
              () => patch(`/api/memory/notes/${note.id}`, { pinned: !note.pinned }),
              `pin-${note.id}`,
            )
          }
          onDelete={(whole) =>
            void act(async () => {
              await del(`/api/memory/notes/${note.id}`);
              if (whole.earlier.length === 0) {
                setUndo(whole.note);
              } else {
                setUndo(null);
                setSaid(`Deleted the note and ${plural(whole.earlier.length, "earlier version")}.`);
              }
            }, `${uid}-notes`)
          }
        />
      ))}
    </ul>
  );

  return (
    <section aria-label="Memory" className="page" data-testid="memory-page">
      <div className="page-col">
        {header}
        {off ? (
          <p data-testid="memory-owner-off" className="notice">
            <Info size={16} aria-hidden />
            <span>
              <span className="notice-title">{OWNER_OFF}</span>
              No notes are sent to any model.
              {notes.length > 0 &&
                " The notes you already have stay yours: you can still export or delete them."}
            </span>
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              <p data-testid="memory-intro" className="help">
                What Workbench remembers about you. Only you can see this page.
              </p>
              <p className="help">
                Notes are short lines the model is given in your chats. A chat keeps the notes it
                started with until you choose Refresh memory there; a deleted note leaves every chat
                at once.
              </p>
            </div>
            {!data.prefs.effective.enabled && (
              <p data-testid="memory-person-off" className="notice notice--warn">
                <Info size={16} aria-hidden />
                <span>
                  Memory is off: no model is given your notes.{" "}
                  {onOpenSettings ? (
                    <button
                      type="button"
                      data-testid="memory-person-off-settings"
                      className="link"
                      onClick={onOpenSettings}
                    >
                      Turn it on in Memory settings.
                    </button>
                  ) : (
                    "Turn it on in Memory settings."
                  )}
                </span>
              </p>
            )}
            {data.ownerReadsChats && (
              <p data-testid="memory-owner-reads" className="notice notice--warn">
                <Eye size={16} aria-hidden />
                The owner of this Workbench can read your chats, including any notes an answer
                quotes. The owner cannot read this page.
              </p>
            )}
          </>
        )}
        {loadProblem && (
          <div role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <div>
              <span className="notice-title">Could not read your notes again</span>
              {loadProblem}
              <div className="mt-2">
                <button
                  type="button"
                  data-testid="memory-retry"
                  className="btn"
                  onClick={() => void load()}
                >
                  Try again
                </button>
              </div>
            </div>
          </div>
        )}
        {problem && (
          <p role="alert" data-testid="memory-problem" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem}
          </p>
        )}
        {!off &&
          (data.revisit ?? []).map((note) => (
            <StillTrue
              key={note.id}
              note={note}
              busy={busy}
              working={working}
              onProblem={setProblem}
            />
          ))}
        <Suggestions
          suggestions={data.suggestions ?? []}
          busy={busy}
          locked={off}
          from={whereFrom}
          working={working}
          onProblem={setProblem}
        />
        {!off && (data.learned?.length ?? 0) > 0 && (
          <RecentlyLearned notes={data.learned ?? []} onChanged={() => void load()} />
        )}
        {!bare && (
          <section aria-labelledby={`${uid}-notes`} className="section">
            <h2 id={`${uid}-notes`} tabIndex={-1} className="title-section">
              Your notes
            </h2>
            {notes.length > 0 && <NoteSearch data={data} list={list} />}
            {!off && <Usage data={data} />}
            {said && (
              <p role="status" data-testid="memory-said" className="notice notice--ok">
                <CircleCheck size={16} aria-hidden />
                {said}
              </p>
            )}
            {undo && (
              <p role="status" data-testid="memory-deleted" className="notice notice--ok">
                <CircleCheck size={16} aria-hidden />
                <span className="min-w-0 flex-1">
                  <strong className="font-semibold">Deleted:</strong>{" "}
                  <span dir="auto" className="wrap-anywhere">
                    {"“"}
                    {undo.text}
                    {"”"}
                  </span>
                </span>
                <button
                  type="button"
                  data-testid="memory-undo"
                  disabled={busy}
                  className="btn btn-quiet -my-1 shrink-0"
                  onClick={() =>
                    void act(async () => {
                      await post("/api/memory/notes", {
                        text: undo.text,
                        kind: undo.kind,
                        pinned: undo.pinned,
                        ...(undo.body ? { body: undo.body } : {}),
                        ...(undo.tags.length > 0 ? { tags: undo.tags } : {}),
                      });
                      setUndo(null);
                    })
                  }
                >
                  Undo
                </button>
              </p>
            )}
            {!off && (notes.length === 0 || starting) && (
              <Starter busy={busy} working={working} onLeft={(left) => setStarting(left > 0)} />
            )}
            {notes.length > 0 && (
              <>
                <section
                  aria-labelledby={`${uid}-always`}
                  data-testid="memory-section-always"
                  className="flex flex-col gap-2"
                >
                  <h3 id={`${uid}-always`} className="title-card">
                    Always used
                  </h3>
                  <p className="help">
                    Instructions and pinned notes. Every chat gets these first.
                  </p>
                  {data.sections.always.length === 0 ? (
                    <p className="empty">
                      Nothing here yet. Pin a note, or write an instruction, to use it every time.
                    </p>
                  ) : (
                    list(data.sections.always)
                  )}
                </section>
                <section
                  aria-labelledby={`${uid}-other`}
                  data-testid="memory-section-other"
                  className="flex flex-col gap-2"
                >
                  <h3 id={`${uid}-other`} className="title-card">
                    {relevantOnly ? "Used when relevant" : "Used when there is room"}
                  </h3>
                  <p className="help">
                    {relevantOnly
                      ? "A chat is given the ones about what it starts with, as many as fit the room a model has for notes."
                      : "The newest first, as many as fit the room a model has for notes."}
                  </p>
                  {relevanceWhy && (
                    <p data-testid="memory-relevance-why" className="help">
                      {relevanceWhy}
                    </p>
                  )}
                  {data.sections.other.length === 0 ? (
                    <p className="empty">Nothing here yet.</p>
                  ) : (
                    list(data.sections.other)
                  )}
                  {data.sections.otherTotal > data.sections.other.length && (
                    <p data-testid="memory-showing" className="help">
                      {showingWords(data.sections.other.length, data.sections.otherTotal)}. Search
                      to find the rest.
                    </p>
                  )}
                </section>
                {past.length > 0 && (
                  <section
                    aria-labelledby={`${uid}-past`}
                    data-testid="memory-section-past"
                    className="flex flex-col gap-2"
                  >
                    <h3 id={`${uid}-past`} className="title-card">
                      Past
                    </h3>
                    <p className="help">
                      Events whose day has gone. No chat is given them; delete them when you like.
                    </p>
                    {list(past)}
                  </section>
                )}
              </>
            )}
            {!off && notes.length > 0 && (
              <AddNote busy={busy} working={working} limits={data.limits} tags={data.tags} />
            )}
          </section>
        )}
        {onSearchChats && <PastChatsDoor onSearch={onSearchChats} />}
        {!off && !bare && <Tags data={data} busy={busy} act={(work) => act(work)} />}
        {!bare && (
          <YourData
            count={notes.length}
            busy={busy}
            onProblem={setProblem}
            onDeleted={(count) => {
              setUndo(null);
              setSaid(`Deleted ${plural(count, "note")}. Nothing of them is kept.`);
              // The button that asked is gone: focus goes to the notes, which are now none.
              setFocusId(`${uid}-notes`);
            }}
            working={working}
          />
        )}
      </div>
    </section>
  );
}

// --- past chats ---

/** Past chats on the Memory page (MV4): one box, whose words open Search chats searched. */
function PastChatsDoor({ onSearch }: { onSearch: (query: string) => void }) {
  const uid = useId();
  const [query, setQuery] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) onSearch(query.trim());
  }
  return (
    <section aria-labelledby={`${uid}-chats`} data-testid="memory-past-chats" className="section">
      <h2 id={`${uid}-chats`} className="title-section">
        <MessagesSquare size={18} aria-hidden />
        Past chats
      </h2>
      <p className="help">
        Find what you asked and what the answers said, in all your chats. Only you can search them.
      </p>
      <form
        role="search"
        aria-label="Search your past chats"
        className="flex flex-wrap items-end gap-2"
        onSubmit={submit}
      >
        <div className="field">
          <label htmlFor={`${uid}-query`} className="field-label">
            Search your chats
          </label>
          <input
            id={`${uid}-query`}
            data-testid="memory-past-chats-input"
            type="search"
            value={query}
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
            className="control w-64 max-w-full"
          />
        </div>
        <button
          type="submit"
          data-testid="memory-past-chats-go"
          disabled={!query.trim()}
          className="btn"
        >
          <Search size={14} aria-hidden />
          Search chats
        </button>
      </form>
    </section>
  );
}

// --- how much is used ---

function Usage({ data }: { data: MemoryPage }) {
  const { usage } = data;
  const full = usage.notes >= usage.maxNotes;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <p data-testid="memory-usage-tokens">{usageWords(usage)}</p>
        <p data-testid="memory-usage-notes">
          {usage.notes.toLocaleString()} of {usage.maxNotes.toLocaleString()} notes
        </p>
      </div>
      {usage.notSent > 0 && (
        <p data-testid="memory-usage-full" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          {plural(usage.notSent, "note")} {usage.notSent === 1 ? "does" : "do"} not fit in this
          room, so {usage.notSent === 1 ? "it is" : "they are"} left out. Notes you pin, and
          instructions, are used first.
        </p>
      )}
      {full && (
        <p data-testid="memory-usage-limit" className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          You have {usage.maxNotes.toLocaleString()} notes, the limit. Delete some, or ask the owner
          to raise it.
        </p>
      )}
    </div>
  );
}

// --- one note ---

type Limits = MemoryPage["limits"];

interface Draft {
  text: string;
  kind: NoteKind;
  body: string;
  tags: string;
}

function NoteItem({
  note,
  locked,
  busy,
  from,
  working,
  limits,
  tags,
  onPin,
  onDelete,
  onSaid,
}: {
  note: MemoryNote;
  /** The owner has turned Memory off: a note can be read and deleted, not changed. */
  locked: boolean;
  busy: boolean;
  from: ReactNode;
  working: Working;
  limits: Limits;
  tags: MemoryTag[];
  onPin: () => void;
  /** The note whole, and its earlier versions, which go with it. */
  onDelete: (whole: NoteWhole) => void;
  onSaid: (words: string) => void;
}) {
  const uid = useId();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  // While the note is read whole (to edit it, or before it is deleted).
  const [reading, setReading] = useState(false);
  const [asking, setAsking] = useState<NoteWhole | null>(null);
  const original = useRef<{ body: string; tags: string[] }>({ body: "", tags: [] });
  const back = useRef(false);
  const name = excerpt(note);
  const created = new Date(note.createdAt * 1000);

  // Closing the editor puts focus back on the button that opened it.
  useEffect(() => {
    if (draft || !back.current) return;
    back.current = false;
    document.getElementById(`edit-${note.id}`)?.focus();
  }, [draft, note.id]);

  async function startEdit() {
    setProblem(null);
    let body = note.body ?? "";
    if (note.bodyChars > 0 && note.body === undefined) {
      setReading(true);
      try {
        body = (await readWhole(note.id)).note.body ?? "";
      } catch (failure) {
        setProblem(failureWords(failure));
        return;
      } finally {
        setReading(false);
      }
    }
    original.current = { body, tags: note.tags };
    setDraft({ text: note.text, kind: note.kind, body, tags: note.tags.join(", ") });
  }

  async function startDelete() {
    setProblem(null);
    setReading(true);
    try {
      const whole = await readWhole(note.id);
      if (whole.earlier.length === 0) onDelete(whole);
      else setAsking(whole);
    } catch (failure) {
      setProblem(`Could not check this note's earlier versions: ${failureWords(failure)}`);
    } finally {
      setReading(false);
    }
  }

  if (draft) {
    const length = characters(draft.text);
    const over = length > NOTE_LIMIT;
    const bodyLength = characters(draft.body);
    const bodyOver = bodyLength > Math.max(limits.maxBodyChars, original.current.body.length);
    const listed = tagsFrom(draft.tags);
    const save = async (asNewVersion: boolean) => {
      const text = draft.text.trim();
      if (!text || over || bodyOver || busy) return;
      setProblem(null);
      const body = {
        text,
        kind: draft.kind,
        ...(draft.body !== original.current.body ? { body: draft.body } : {}),
        ...(listed.join("\n") !== original.current.tags.join("\n") ? { tags: listed } : {}),
        ...(asNewVersion ? { asNewVersion: true } : {}),
      };
      try {
        await working(() => patch(`/api/memory/notes/${note.id}`, body).then(() => undefined));
        back.current = true;
        setDraft(null);
        if (asNewVersion) onSaid("Saved as a new version. The old one is under Earlier versions.");
      } catch (failure) {
        setProblem(failureWords(failure));
      }
    };
    return (
      <li data-testid="memory-note" className="px-4 py-3">
        <form
          aria-label="Edit this note"
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void save(false);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              back.current = true;
              setDraft(null);
            }
          }}
        >
          <div className="field">
            <label htmlFor={`${uid}-text`} className="field-label">
              Note
            </label>
            <textarea
              id={`${uid}-text`}
              data-testid="memory-edit-text"
              autoFocus
              rows={2}
              dir="auto"
              value={draft.text}
              disabled={busy}
              aria-invalid={over || problem ? true : undefined}
              aria-describedby={problem ? `${uid}-problem` : undefined}
              onChange={(event) => setDraft({ ...draft, text: oneLineOf(event.target.value) })}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void save(false);
                }
              }}
              className="control"
            />
          </div>
          {(limits.maxBodyChars > 0 || original.current.body) && (
            <div className="field">
              <label htmlFor={`${uid}-body`} className="field-label">
                Details
              </label>
              <textarea
                id={`${uid}-body`}
                data-testid="memory-edit-body"
                rows={5}
                dir="auto"
                value={draft.body}
                disabled={busy}
                aria-invalid={bodyOver || undefined}
                onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                className="control"
              />
              <p
                data-testid="memory-edit-body-count"
                className={`text-xs tabular-nums ${bodyOver ? "text-error" : "text-muted"}`}
              >
                {bodyLength.toLocaleString()} of {limits.maxBodyChars.toLocaleString()}
              </p>
            </div>
          )}
          <TagsField
            value={draft.tags}
            onChange={(text) => setDraft({ ...draft, tags: text })}
            tags={tags}
            maxTags={limits.maxTagsPerNote}
            disabled={busy}
            testId="memory-edit-tags"
          />
          <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
            <div className="field">
              <label htmlFor={`${uid}-kind`} className="field-label">
                Kind
              </label>
              <select
                id={`${uid}-kind`}
                data-testid="memory-edit-kind"
                value={draft.kind}
                disabled={busy}
                onChange={(event) => setDraft({ ...draft, kind: event.target.value as NoteKind })}
                className="control"
              >
                {KINDS.map((kind) => (
                  <option key={kind.kind} value={kind.kind}>
                    {kind.label}
                  </option>
                ))}
              </select>
            </div>
            <span className={`pb-2 text-xs tabular-nums ${over ? "text-error" : "text-muted"}`}>
              {length.toLocaleString()} of {NOTE_LIMIT}
            </span>
            <span className="ml-auto flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="memory-note-cancel"
                disabled={busy}
                onClick={() => {
                  back.current = true;
                  setDraft(null);
                }}
                className="btn btn-quiet"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="memory-note-save-version"
                disabled={busy || over || bodyOver || !draft.text.trim()}
                aria-describedby={`${uid}-versions`}
                onClick={() => void save(true)}
                className="btn"
              >
                Save as new version
              </button>
              <button
                type="submit"
                data-testid="memory-note-save"
                disabled={busy || over || bodyOver || !draft.text.trim()}
                aria-describedby={`${uid}-versions`}
                className="btn btn-primary"
              >
                Save
              </button>
            </span>
          </div>
          <p id={`${uid}-versions`} className="help">
            Save fixes this note where it is, for a typo. Save as new version is for a fact that
            changed: the old one is kept under Earlier versions and never sent to a model.
          </p>
          {problem && (
            <p id={`${uid}-problem`} role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
        </form>
      </li>
    );
  }

  return (
    <li data-testid="memory-note" className="flex flex-col gap-2 px-4 py-3">
      <p dir="auto" data-testid="memory-note-text" className="text-base wrap-anywhere">
        {note.text}
      </p>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span className="badge">{kindLabel(note.kind)}</span>
        {note.pinned && (
          <span className="badge">
            <Pin size={12} aria-hidden />
            Pinned
          </span>
        )}
        <TagChips tags={note.tags} />
        {note.bodyChars > 0 && (
          <span data-testid="memory-note-body-size" className="badge">
            Details: {plural(note.bodyChars, "character")}
          </span>
        )}
        <time dateTime={created.toISOString()} title={created.toLocaleString()}>
          {noteDate(note.createdAt)}
        </time>
        <span aria-hidden>·</span>
        <span data-testid="memory-note-from">{from}</span>
      </div>
      {asking && (
        <div
          role="group"
          aria-label="Delete this note and its earlier versions?"
          data-testid="memory-note-delete-confirm"
          className="notice notice--error flex-col"
          onKeyDown={(event) => {
            if (event.key === "Escape") setAsking(null);
          }}
        >
          <p>
            This note has {plural(asking.earlier.length, "earlier version")}. Deleting the note
            deletes {asking.earlier.length === 1 ? "it" : "them"} too. This cannot be undone.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-testid="memory-note-delete-cancel"
              onClick={() => setAsking(null)}
              className="btn"
            >
              Keep the note
            </button>
            <button
              type="button"
              data-testid="memory-note-delete-go"
              disabled={busy}
              onClick={() => onDelete(asking)}
              className="btn btn-danger"
            >
              Delete note and earlier versions
            </button>
          </div>
        </div>
      )}
      {problem && (
        <p role="alert" data-testid="memory-note-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <div className="-ms-2 flex flex-wrap items-center gap-1">
        <button
          type="button"
          data-testid="memory-note-details"
          aria-label={`${open ? "Hide" : "Show"} details of note: ${name}`}
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="btn btn-quiet"
        >
          {open ? "Hide details" : "Show details"}
        </button>
        {!locked && (
          <button
            type="button"
            id={`edit-${note.id}`}
            data-testid="memory-note-edit"
            aria-label={`Edit note: ${name}`}
            disabled={busy || reading}
            onClick={() => void startEdit()}
            className="btn btn-quiet"
          >
            Edit
          </button>
        )}
        {!locked && (
          <button
            type="button"
            id={`pin-${note.id}`}
            data-testid="memory-note-pin"
            aria-label={`${note.pinned ? "Unpin" : "Pin"} note: ${name}`}
            disabled={busy}
            onClick={onPin}
            className="btn btn-quiet"
          >
            {note.pinned ? <PinOff size={14} aria-hidden /> : <Pin size={14} aria-hidden />}
            {note.pinned ? "Unpin" : "Pin"}
          </button>
        )}
        <button
          type="button"
          id={`delete-${note.id}`}
          data-testid="memory-note-delete"
          aria-label={`Delete note: ${name}`}
          disabled={busy || reading}
          onClick={() => void startDelete()}
          className="btn btn-quiet btn-danger"
        >
          Delete
        </button>
      </div>
      {open && <Details note={note} busy={busy} working={working} />}
    </li>
  );
}

// --- writing notes ---

function AddNote({
  busy,
  working,
  limits,
  tags,
}: {
  busy: boolean;
  working: Working;
  limits: Limits;
  tags: MemoryTag[];
}) {
  const uid = useId();
  const [text, setText] = useState("");
  const [body, setBody] = useState("");
  const [tagText, setTagText] = useState("");
  const [kind, setKind] = useState<NoteKind>("fact");
  const [pinned, setPinned] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const length = characters(text.trim());
  const over = length > NOTE_LIMIT;
  const bodyLength = characters(body);
  const bodyOver = bodyLength > limits.maxBodyChars;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || over || bodyOver || !text.trim()) return;
    setProblem(null);
    setSaid(null);
    const listed = tagsFrom(tagText);
    try {
      const kept = await working(() =>
        post<{ created: boolean }>("/api/memory/notes", {
          text: text.trim(),
          kind,
          pinned,
          ...(body.trim() ? { body } : {}),
          ...(listed.length > 0 ? { tags: listed } : {}),
        }),
      );
      setSaid(kept.created ? "Kept." : "You already have that note.");
      setText("");
      setBody("");
      setTagText("");
      setPinned(false);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  return (
    <form
      aria-labelledby={`${uid}-add`}
      data-testid="memory-add-form"
      className="card card--add"
      onSubmit={(event) => void submit(event)}
    >
      <h3 id={`${uid}-add`} className="title-card">
        Add a note
      </h3>
      <div className="field">
        <label htmlFor={`${uid}-text`} className="field-label">
          What should Workbench remember?
        </label>
        <textarea
          id={`${uid}-text`}
          data-testid="memory-add-text"
          rows={2}
          dir="auto"
          value={text}
          disabled={busy}
          placeholder="I use metric units"
          aria-invalid={over || problem ? true : undefined}
          aria-describedby={`${uid}-help${problem ? ` ${uid}-problem` : ""}`}
          onChange={(event) => {
            setText(oneLineOf(event.target.value));
            setSaid(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          className="control"
        />
        <p id={`${uid}-help`} className="help">
          One line, at most {NOTE_LIMIT} characters. Passwords, keys and card numbers are not kept.
        </p>
      </div>
      {limits.maxBodyChars > 0 && (
        <div className="field">
          <label htmlFor={`${uid}-body`} className="field-label">
            Details (optional)
          </label>
          <textarea
            id={`${uid}-body`}
            data-testid="memory-add-body"
            rows={4}
            dir="auto"
            value={body}
            disabled={busy}
            aria-invalid={bodyOver || undefined}
            aria-describedby={`${uid}-body-help`}
            onChange={(event) => {
              setBody(event.target.value);
              setSaid(null);
            }}
            className="control"
          />
          <p id={`${uid}-body-help`} className="help">
            Markdown. Steps, a brief or a checklist go here; the line above stays the title.{" "}
            <span
              data-testid="memory-add-body-count"
              className={`tabular-nums ${bodyOver ? "text-error" : ""}`}
            >
              {bodyLength.toLocaleString()} of {limits.maxBodyChars.toLocaleString()}
            </span>
          </p>
        </div>
      )}
      <TagsField
        value={tagText}
        onChange={setTagText}
        tags={tags}
        maxTags={limits.maxTagsPerNote}
        disabled={busy}
        testId="memory-add-tags"
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="field">
          <label htmlFor={`${uid}-kind`} className="field-label">
            Kind
          </label>
          <select
            id={`${uid}-kind`}
            data-testid="memory-add-kind"
            value={kind}
            disabled={busy}
            onChange={(event) => setKind(event.target.value as NoteKind)}
            aria-describedby={`${uid}-kind-help`}
            className="control"
          >
            {KINDS.map((one) => (
              <option key={one.kind} value={one.kind}>
                {one.label}
              </option>
            ))}
          </select>
          <p id={`${uid}-kind-help`} className="help">
            {KINDS.find((one) => one.kind === kind)?.help}
          </p>
        </div>
        <label className="check self-start sm:mt-7">
          <input
            type="checkbox"
            data-testid="memory-add-pinned"
            checked={pinned}
            disabled={busy}
            onChange={(event) => setPinned(event.target.checked)}
          />
          <span>Always use this note</span>
        </label>
      </div>
      {problem && (
        <p
          id={`${uid}-problem`}
          role="alert"
          data-testid="memory-add-problem"
          className="notice notice--error"
        >
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {said && !problem && (
        <p role="status" className="text-sm text-ok">
          {said}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="memory-add"
          disabled={busy || over || bodyOver || !text.trim()}
          className="btn btn-primary"
        >
          Add note
        </button>
        <span
          data-testid="memory-add-count"
          className={`text-xs tabular-nums ${over ? "text-error" : "text-muted"}`}
        >
          {length.toLocaleString()} of {NOTE_LIMIT}
        </span>
      </div>
    </form>
  );
}

/** What a person with no notes sees: where to start, in two boxes (one pinned
 * note per line), and the other ways to keep one. */
function Starter({
  busy,
  working,
  onLeft,
}: {
  busy: boolean;
  working: Working;
  /** How many lines were left in the boxes: the page keeps them there while any are. */
  onLeft: (lines: number) => void;
}) {
  const uid = useId();
  const [about, setAbout] = useState("");
  const [answers, setAnswers] = useState("");
  const [problems, setProblems] = useState<string[]>([]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const jobs = [
      ...linesOf(about).map((text) => ({ box: "About me", kind: "fact", text })),
      ...linesOf(answers).map((text) => ({ box: "How I want answers", kind: "instruction", text })),
    ];
    if (jobs.length === 0) {
      setProblems(["Write at least one line, then save."]);
      return;
    }
    const long = jobs.filter((job) => characters(job.text) > NOTE_LIMIT);
    if (long.length > 0) {
      setProblems(
        long.map(
          (job) =>
            `${job.box}: “${excerpt(job, 30)}” is ${characters(job.text)} characters, and a note is at most ${NOTE_LIMIT}. Shorten it, or split it into two lines.`,
        ),
      );
      return;
    }
    const left: typeof jobs = [];
    const said: string[] = [];
    await working(async () => {
      for (const [index, job] of jobs.entries()) {
        try {
          await post("/api/memory/notes", { text: job.text, kind: job.kind, pinned: true });
        } catch (failure) {
          said.push(`${job.box}: “${excerpt(job, 30)}”: ${failureWords(failure)}`);
          left.push(job);
          // The limit, or the owner's switch, stops the rest as well: said once.
          const why = refusalOf(failure)?.reason;
          if (why === "full" || why === "off") {
            left.push(...jobs.slice(index + 1));
            break;
          }
        }
      }
      // Said before the page reads the notes again, which would otherwise take
      // the boxes away (there are notes now) with the lines that are left in them.
      onLeft(left.length);
    });
    setAbout(
      left
        .filter((job) => job.box === "About me")
        .map((job) => job.text)
        .join("\n"),
    );
    setAnswers(
      left
        .filter((job) => job.box !== "About me")
        .map((job) => job.text)
        .join("\n"),
    );
    setProblems(said);
  }

  return (
    <div data-testid="memory-empty" className="card">
      <h3 className="title-card">Nothing remembered yet</h3>
      <p className="help">Type “Remember that …” in any chat, or write one note a line below.</p>
      <form className="flex flex-col gap-4" onSubmit={(event) => void submit(event)}>
        <div className="field">
          <label htmlFor={`${uid}-about`} className="field-label">
            About me
          </label>
          <textarea
            id={`${uid}-about`}
            data-testid="memory-about"
            rows={3}
            dir="auto"
            value={about}
            disabled={busy}
            placeholder={"I use metric units\nI live in Lisbon"}
            aria-describedby={`${uid}-about-help`}
            onChange={(event) => setAbout(event.target.value)}
            className="control"
          />
          <p id={`${uid}-about-help`} className="help">
            What is true about you. One note per line, at most {NOTE_LIMIT} characters each.
          </p>
        </div>
        <div className="field">
          <label htmlFor={`${uid}-answers`} className="field-label">
            How I want answers
          </label>
          <textarea
            id={`${uid}-answers`}
            data-testid="memory-answers"
            rows={3}
            dir="auto"
            value={answers}
            disabled={busy}
            placeholder={"Keep answers short\nGive the number first"}
            aria-describedby={`${uid}-answers-help`}
            onChange={(event) => setAnswers(event.target.value)}
            className="control"
          />
          <p id={`${uid}-answers-help`} className="help">
            How you like answers written. One note per line, used in every chat.
          </p>
        </div>
        {problems.length > 0 && (
          <div role="alert" data-testid="memory-starter-problem" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <ul className="flex flex-col gap-1">
              {problems.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          </div>
        )}
        <button
          type="submit"
          data-testid="memory-starter-save"
          disabled={busy}
          className="btn btn-primary self-start"
        >
          Save these notes
        </button>
      </form>
    </div>
  );
}

// --- the person's own copy ---

function YourData({
  count,
  busy,
  working,
  onProblem,
  onDeleted,
}: {
  count: number;
  busy: boolean;
  working: Working;
  onProblem: (problem: string | null) => void;
  onDeleted: (count: number) => void;
}) {
  const uid = useId();
  const [exporting, setExporting] = useState<"md" | "json" | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const opener = useRef<HTMLButtonElement>(null);

  async function download(format: "md" | "json") {
    setExporting(format);
    onProblem(null);
    try {
      const file = await fileOf(`/api/memory/export?format=${format}`);
      saveFile(file.blob, file.name ?? `workbench-memory.${format}`);
    } catch (failure) {
      onProblem(`Could not export your notes: ${failureWords(failure)}`);
    } finally {
      setExporting(null);
    }
  }

  function close() {
    setConfirming(false);
    setTyped("");
    queueMicrotask(() => opener.current?.focus());
  }

  async function deleteAll() {
    onProblem(null);
    try {
      const done = await working(() =>
        api<{ deleted: number }>("/api/memory/notes?confirm=all", { method: "DELETE" }),
      );
      setConfirming(false);
      setTyped("");
      onDeleted(done.deleted);
    } catch (failure) {
      onProblem(failureWords(failure));
    }
  }

  return (
    <section aria-labelledby={`${uid}-data`} className="section">
      <h2 id={`${uid}-data`} className="title-section">
        Export or delete
      </h2>
      <p className="help">Export your notes as a file, or delete them all.</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="memory-export-md"
          disabled={exporting !== null}
          onClick={() => void download("md")}
          className="btn"
        >
          <FileText size={14} aria-hidden />
          Export as Markdown
        </button>
        <button
          type="button"
          data-testid="memory-export-json"
          disabled={exporting !== null}
          onClick={() => void download("json")}
          className="btn"
        >
          <FileJson size={14} aria-hidden />
          Export as JSON
        </button>
      </div>
      {count > 0 &&
        (confirming ? (
          <div
            role="group"
            aria-label="Delete all memory?"
            data-testid="memory-delete-all-confirm"
            className="notice notice--error flex-col"
            onKeyDown={(event) => {
              if (event.key === "Escape") close();
            }}
          >
            <p>
              Delete all {plural(count, "note")}? They leave every chat at once. This cannot be
              undone.
            </p>
            <div className="field w-full max-w-xs">
              <label htmlFor={`${uid}-typed`} className="field-label">
                Type DELETE to confirm
              </label>
              <input
                id={`${uid}-typed`}
                data-testid="memory-delete-all-typed"
                autoFocus
                autoComplete="off"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                className="control"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="memory-delete-all-cancel"
                onClick={close}
                className="btn"
              >
                Keep my notes
              </button>
              <button
                type="button"
                data-testid="memory-delete-all-go"
                disabled={busy || typed.trim().toLowerCase() !== "delete"}
                onClick={() => void deleteAll()}
                className="btn btn-danger"
              >
                Delete all notes
              </button>
            </div>
          </div>
        ) : (
          <button
            ref={opener}
            type="button"
            data-testid="memory-delete-all"
            onClick={() => setConfirming(true)}
            className="btn btn-quiet btn-danger -ms-2 self-start"
          >
            Delete all memory
          </button>
        ))}
    </section>
  );
}
