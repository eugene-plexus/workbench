/**
 * Team collections (row 47), a section of the Documents screen: the documents and notes a team
 * shares. Each collection shows what is in it and who added each thing first; then, for someone
 * who adds to it, the notes a model proposed, which wait for a person; then, for its manager,
 * who may use it. A collection the person cannot use is never listed.
 */

import { CircleAlert, Inbox, Plus, Trash2, Users } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useId, useState } from "react";

import { plural } from "../lib/memory";
import { problemWords } from "../lib/projects";
import {
  acceptNote,
  addedWords,
  addNote,
  type Collection,
  type CollectionDetail,
  deleteCollection,
  type Grant,
  listCollections,
  makeCollection,
  putGrants,
  readCollection,
  removeItem,
  roleWords,
  type TeamItem,
  teamPeople,
  type TeamPeople,
} from "../lib/team";
import { AddDocuments } from "./AddDocuments";

const READING = new Set(["queued", "reading", "embedding"]);

export function TeamCollections() {
  const uid = useId();
  const [list, setList] = useState<Collection[] | null>(null);
  const [canCreate, setCanCreate] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const got = await listCollections();
      setList(got.collections);
      setCanCreate(got.canCreate);
      setProblem(null);
    } catch (failure) {
      setProblem(problemWords(failure));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function make(event: FormEvent) {
    event.preventDefault();
    if (!naming?.trim()) return;
    try {
      const made = await makeCollection(naming);
      setNaming(null);
      await reload();
      setOpen(made.id);
    } catch (failure) {
      setProblem(problemWords(failure));
    }
  }

  if (list === null && !problem) return null;
  return (
    <section aria-labelledby={`${uid}-team`} className="section" data-testid="team-collections">
      <div className="flex items-center gap-2">
        <h2 id={`${uid}-team`} className="title-section">
          Team collections
        </h2>
        {canCreate && naming === null && (
          <button
            type="button"
            data-testid="team-new"
            className="btn btn-quiet ml-auto"
            onClick={() => setNaming("")}
          >
            <Plus size={14} aria-hidden className="text-accent" /> New collection
          </button>
        )}
      </div>
      {naming !== null && (
        <form className="flex flex-wrap gap-2" onSubmit={(event) => void make(event)}>
          <input
            autoFocus
            aria-label="Collection name"
            data-testid="team-new-name"
            value={naming}
            maxLength={400}
            placeholder="Collection name"
            onChange={(event) => setNaming(event.target.value)}
            className="control min-w-0 flex-1"
          />
          <button type="submit" className="btn btn-primary" disabled={!naming.trim()}>
            Create collection
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setNaming(null)}>
            Cancel
          </button>
        </form>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {list && list.length === 0 && (
        <p className="help" data-testid="team-empty">
          {canCreate
            ? "No team collections yet. Make one, add documents, then choose who may use it."
            : "No team collections are shared with you."}
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {(list ?? []).map((collection) => (
          <li key={collection.id} className="card" data-testid="team-collection">
            <button
              type="button"
              aria-expanded={open === collection.id}
              className="flex w-full flex-wrap items-center gap-2 text-left"
              onClick={() => setOpen(open === collection.id ? null : collection.id)}
            >
              <Users size={16} aria-hidden className="text-accent" />
              <span className="font-medium">{collection.name}</span>
              <span className="text-sm text-muted">
                {plural(collection.documents, "document")} · {plural(collection.notes, "note")} ·{" "}
                {roleWords(collection.role)}
              </span>
              {collection.proposed > 0 && (
                <span className="badge badge--warn ml-auto" data-testid="team-waiting">
                  <Inbox size={12} aria-hidden /> {collection.proposed} waiting
                </span>
              )}
            </button>
            {open === collection.id && (
              <CollectionPanel
                id={collection.id}
                onChanged={() => void reload()}
                onGone={() => {
                  setOpen(null);
                  void reload();
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One collection, opened: its documents, its notes, what waits, and who may use it. */
export function CollectionPanel({
  id,
  onChanged,
  onGone,
  documentsOnly = false,
}: {
  id: string;
  onChanged?: () => void;
  onGone?: () => void;
  /** On a shared project's page: its documents only. */
  documentsOnly?: boolean;
}) {
  const uid = useId();
  const [data, setData] = useState<CollectionDetail | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await readCollection(id));
      setProblem(null);
    } catch (failure) {
      setProblem(problemWords(failure));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // While a document is being read, look again.
  const reading = data?.documentList.some((d) => READING.has(d.document?.state ?? "")) ?? false;
  useEffect(() => {
    if (!reading) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [reading, load]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      await work();
      await load();
      onChanged?.();
    } catch (failure) {
      setProblem(problemWords(failure));
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return problem ? (
      <p role="alert" className="notice notice--error mt-2">
        <CircleAlert size={16} aria-hidden />
        {problem}
      </p>
    ) : (
      <p role="status" className="help mt-2">
        Loading the collection…
      </p>
    );
  }
  const adds = data.role === "manage" || data.role === "contribute";
  // Its manager takes anything out; someone who adds, what they added (the server says no to
  // anything else, in words).
  const removable = (item: TeamItem) => data.role === "manage" || (adds && !item.formerMember);

  return (
    <div className="mt-3 flex flex-col gap-4" data-testid="team-panel">
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <div className="flex flex-col gap-2">
        <h3 id={`${uid}-docs`} className="title-card">
          Documents
        </h3>
        {adds && (
          <AddDocuments
            uploadTo={`/api/collections/${encodeURIComponent(id)}/documents`}
            place={data.name}
            onChanged={() => void load()}
          />
        )}
        {data.documentList.length === 0 ? (
          <p className="help">No documents yet.</p>
        ) : (
          <ul className="flex flex-col gap-1" aria-labelledby={`${uid}-docs`}>
            {data.documentList.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                canRemove={removable(item)}
                busy={busy}
                onRemove={() => void act(() => removeItem(id, item.id))}
              />
            ))}
          </ul>
        )}
      </div>
      {!documentsOnly && (
        <div className="flex flex-col gap-2">
          <h3 id={`${uid}-notes`} className="title-card">
            Notes
          </h3>
          {data.noteList.length === 0 ? (
            <p className="help">No notes yet.</p>
          ) : (
            <ul className="flex flex-col gap-1" aria-labelledby={`${uid}-notes`}>
              {data.noteList.map((item) => (
                <ItemRow
                  key={item.id}
                  item={item}
                  canRemove={removable(item)}
                  busy={busy}
                  onRemove={() => void act(() => removeItem(id, item.id))}
                />
              ))}
            </ul>
          )}
          {adds && (
            <form
              className="flex flex-wrap gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (!note.trim()) return;
                void act(async () => {
                  await addNote(id, note);
                  setNote("");
                });
              }}
            >
              <input
                aria-label={`A note for ${data.name}`}
                data-testid="team-note"
                value={note}
                maxLength={240}
                placeholder="A note for the team"
                onChange={(event) => setNote(event.target.value)}
                className="control min-w-0 flex-1"
              />
              <button type="submit" className="btn" disabled={busy || !note.trim()}>
                Add note
              </button>
            </form>
          )}
        </div>
      )}
      {adds && data.inbox.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="team-inbox">
          <h3 className="title-card">Proposed by a model</h3>
          <p className="help">
            A proposed note is not searched until you accept it. Read it first: it came from a
            model, not a person.
          </p>
          <ul className="flex flex-col gap-1">
            {data.inbox.map((item) => (
              <li key={item.id} className="flex flex-wrap items-start gap-2">
                <span className="min-w-0 flex-1">
                  {item.title}
                  <span className="block text-sm text-muted">
                    Proposed in {item.formerMember ? "a former member" : item.addedBy}&apos;s chat
                    {item.model ? ` by ${item.model}` : ""}
                  </span>
                </span>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void act(() => acceptNote(id, item.id))}
                >
                  Accept
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  disabled={busy}
                  onClick={() => void act(() => removeItem(id, item.id))}
                >
                  Dismiss
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.grants && (
        <div className="flex flex-col gap-2">
          <h3 className="title-card">Who may use it</h3>
          <GrantEditor
            grants={data.grants}
            empty="Nobody else yet. Add people or groups."
            onSave={(next) => act(() => putGrants(id, next))}
          />
        </div>
      )}
      {data.role === "manage" && data.kind === "install" && !documentsOnly && (
        <div>
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy}
            onClick={() => {
              if (
                window.confirm(
                  `Delete ${data.name}? Its ${plural(data.documents, "document")} and ${plural(data.notes, "note")} go for everyone.`,
                )
              ) {
                void act(async () => {
                  await deleteCollection(id);
                  onGone?.();
                });
              }
            }}
          >
            <Trash2 size={14} aria-hidden /> Delete collection
          </button>
        </div>
      )}
    </div>
  );
}

function ItemRow({
  item,
  canRemove,
  busy,
  onRemove,
}: {
  item: TeamItem;
  canRemove: boolean;
  busy: boolean;
  onRemove: () => void;
}) {
  const state = item.document?.state;
  return (
    <li className="flex flex-wrap items-start gap-2" data-testid="team-item">
      <span className="min-w-0 flex-1">
        {item.title}
        <span className="block text-sm text-muted">
          {addedWords(item)}
          {state && READING.has(state) ? " · Being read" : ""}
          {item.document?.message ? ` · ${item.document.message}` : ""}
        </span>
        {item.body && <span className="block text-sm">{item.body}</span>}
      </span>
      {canRemove && (
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          aria-label={`Take ${item.title} out`}
          onClick={onRemove}
        >
          <Trash2 size={14} aria-hidden /> Take out
        </button>
      )}
    </li>
  );
}

/** People and groups, each with read or contribute; every change is kept at once, and the list
 * shows what the server kept. */
export function GrantEditor({
  grants,
  empty,
  onSave,
}: {
  grants: Grant[];
  empty: string;
  onSave: (next: Omit<Grant, "name">[]) => Promise<void>;
}) {
  const [people, setPeople] = useState<TeamPeople | null>(null);
  const [pick, setPick] = useState("");
  const [role, setRole] = useState<"read" | "contribute">("read");

  useEffect(() => {
    teamPeople()
      .then(setPeople)
      .catch(() => setPeople({ people: [], groups: [], problem: "Could not list people." }));
  }, []);

  const names: Record<string, string> = {};
  for (const p of people?.people ?? []) names[`person:${p.sub}`] = p.name;
  for (const g of people?.groups ?? []) names[`group:${g.id}`] = g.name;
  const plain = (list: Grant[]) => list.map(({ kind, id, role: r }) => ({ kind, id, role: r }));

  return (
    <div className="flex flex-col gap-2" data-testid="grant-editor">
      {grants.length === 0 ? (
        <p className="help">{empty}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {grants.map((grant) => {
            const key = `${grant.kind}:${grant.id}`;
            return (
              <li key={key} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1">
                  {grant.kind === "group" ? "Group: " : ""}
                  {grant.name ?? names[key] ?? grant.id}
                </span>
                <select
                  aria-label={`What ${grant.name ?? names[key] ?? grant.id} may do`}
                  value={grant.role}
                  className="control"
                  onChange={(event) =>
                    void onSave(
                      plain(grants).map((g) =>
                        g.kind === grant.kind && g.id === grant.id
                          ? { ...g, role: event.target.value as "read" | "contribute" }
                          : g,
                      ),
                    )
                  }
                >
                  <option value="read">Read</option>
                  <option value="contribute">Read and add</option>
                </select>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() =>
                    void onSave(
                      plain(grants).filter((g) => !(g.kind === grant.kind && g.id === grant.id)),
                    )
                  }
                >
                  Remove
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {people?.problem && <p className="help">{people.problem}</p>}
      <div className="flex flex-wrap gap-2">
        <select
          aria-label="Person or group"
          data-testid="grant-pick"
          value={pick}
          className="control min-w-0 flex-1"
          onChange={(event) => setPick(event.target.value)}
        >
          <option value="">Choose a person or group</option>
          {(people?.groups ?? []).map((g) => (
            <option key={g.id} value={`group:${g.id}`}>
              Group: {g.name}
            </option>
          ))}
          {(people?.people ?? []).map((p) => (
            <option key={p.sub} value={`person:${p.sub}`}>
              {p.name}
            </option>
          ))}
        </select>
        <select
          aria-label="What they may do"
          value={role}
          className="control"
          onChange={(event) => setRole(event.target.value as "read" | "contribute")}
        >
          <option value="read">Read</option>
          <option value="contribute">Read and add</option>
        </select>
        <button
          type="button"
          className="btn"
          disabled={!pick}
          onClick={() => {
            const [kind, ...rest] = pick.split(":");
            const id = rest.join(":");
            const next = plain(grants).filter((g) => !(g.kind === kind && g.id === id));
            void onSave([...next, { kind: kind as "person" | "group", id, role }]);
            setPick("");
          }}
        >
          Add
        </button>
      </div>
    </div>
  );
}
