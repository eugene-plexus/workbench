/**
 * Tags on the Memory page: the person's own list of tags, each with a short description
 * (a model reads it, so it knows what the tag is for), and the box a note's tags are typed
 * in. A note's tags are always on the list; the list is edited here: add, rename, describe,
 * delete, export and import. Renaming a tag onto one already on the list merges the two, and
 * deleting a tag takes it off its notes; both ask first, naming the notes.
 */

import { CircleAlert, CircleCheck, Download, Upload } from "lucide-react";
import { type FormEvent, useId, useRef, useState } from "react";

import { api, fileOf, patch, post } from "../lib/api";
import { failureWords, plural, readText, saveFile, tagKey } from "../lib/memory";
import type { MemoryPage, MemoryTag, TagImportAnswer, TagImportProblem } from "../lib/types";

type Act = (work: () => Promise<void>) => Promise<void>;

/** The tags of a note, typed in one box, with the person's list offered as they type. */
export function TagsField({
  value,
  onChange,
  tags,
  maxTags,
  disabled,
  testId,
}: {
  value: string;
  onChange: (text: string) => void;
  tags: MemoryTag[];
  maxTags: number;
  disabled: boolean;
  testId: string;
}) {
  const uid = useId();
  return (
    <div className="field">
      <label htmlFor={`${uid}-tags`} className="field-label">
        Tags
      </label>
      <input
        id={`${uid}-tags`}
        data-testid={testId}
        value={value}
        disabled={disabled}
        list={`${uid}-list`}
        autoComplete="off"
        placeholder="work, cooking"
        aria-describedby={`${uid}-tags-help`}
        onChange={(event) => onChange(event.target.value)}
        className="control"
      />
      <datalist id={`${uid}-list`}>
        {tags.map((t) => (
          <option key={t.key} value={t.tag} label={t.description || undefined} />
        ))}
      </datalist>
      <p id={`${uid}-tags-help`} className="help">
        Separate tags with commas. At most {maxTags} a note. A new tag joins your list.
      </p>
    </div>
  );
}

/** Tags as chips, each named for what it is. */
export function TagChips({ tags }: { tags: string[] }) {
  if (tags.length === 0) return null;
  return (
    <>
      {tags.map((tag) => (
        <span key={tag} data-testid="memory-note-tag" className="badge">
          {tag}
        </span>
      ))}
    </>
  );
}

type Confirm =
  | { kind: "delete"; tag: MemoryTag }
  | { kind: "merge"; from: MemoryTag; into: MemoryTag; name: string; description: string | null };

interface Pending {
  text: string;
  format: "csv" | "txt";
  wouldRemove: { tag: string; notes: number }[];
  problems: TagImportProblem[];
}

function Problems({ problems }: { problems: TagImportProblem[] }) {
  if (problems.length === 0) return null;
  return (
    <div role="alert" data-testid="memory-tags-problems" className="notice notice--error">
      <CircleAlert size={16} aria-hidden />
      <div>
        <span className="notice-title">{plural(problems.length, "line")} not taken</span>
        <ul className="flex flex-col gap-1">
          {problems.map((p) => (
            <li key={`${p.line}-${p.text}`} dir="auto" className="wrap-anywhere">
              Line {p.line}: {p.message}
              {p.text ? (
                <>
                  {" "}
                  {"“"}
                  {p.text}
                  {"”"}
                </>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function Tags({
  data,
  busy,
  act,
}: {
  data: MemoryPage;
  busy: boolean;
  /** Work that changes the page: it reads again after it, and a failure is said at the top. */
  act: Act;
}) {
  const uid = useId();
  const { tags, limits } = data;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ tag: "", description: "" });
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [problems, setProblems] = useState<TagImportProblem[]>([]);
  const chosen = useRef<HTMLInputElement>(null);

  /** One change to the list: its own refusal is said here, beside the list. */
  async function change(work: () => Promise<void>, done?: string) {
    setProblem(null);
    setSaid(null);
    let failed = false;
    await act(async () => {
      try {
        await work();
      } catch (failure) {
        failed = true;
        setProblem(failureWords(failure));
      }
    });
    if (!failed && done) setSaid(done);
    return !failed;
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    if (busy || !name.trim()) return;
    const body = {
      tag: name.trim(),
      ...(description.trim() ? { description: description.trim() } : {}),
    };
    if (
      await change(() => post("/api/memory/tags", body).then(() => undefined), `Added ${body.tag}.`)
    ) {
      setName("");
      setDescription("");
    }
  }

  function startEdit(tag: MemoryTag) {
    setEditing(tag.key);
    setDraft({ tag: tag.tag, description: tag.description });
    setConfirm(null);
    setProblem(null);
  }

  async function saveEdit(tag: MemoryTag, confirmed?: Confirm) {
    const renamed = draft.tag.trim() !== tag.tag;
    const newDescription = draft.description.trim();
    const describing = newDescription !== tag.description;
    if (!draft.tag.trim() || (!renamed && !describing)) {
      setEditing(null);
      return;
    }
    const into = renamed
      ? tags.find((t) => t.key !== tag.key && t.key === tagKey(draft.tag.trim()))
      : undefined;
    if (into && !confirmed) {
      setConfirm({
        kind: "merge",
        from: tag,
        into,
        name: draft.tag.trim(),
        description: describing ? newDescription : null,
      });
      return;
    }
    const body = {
      ...(renamed ? { tag: draft.tag.trim() } : {}),
      ...(describing ? { description: newDescription } : {}),
    };
    const ok = await change(async () => {
      const done = await patch<{ merged: boolean }>(
        `/api/memory/tags/${encodeURIComponent(tag.tag)}`,
        body,
      );
      setSaid(
        done.merged ? `Merged ${tag.tag} into ${draft.tag.trim()}.` : `Saved ${draft.tag.trim()}.`,
      );
    });
    if (ok) {
      setEditing(null);
      setConfirm(null);
    }
  }

  async function remove(tag: MemoryTag) {
    const ok = await change(async () => {
      await api(`/api/memory/tags/${encodeURIComponent(tag.tag)}`, { method: "DELETE" });
    }, `Deleted ${tag.tag}. Its notes stay.`);
    if (ok) setConfirm(null);
  }

  async function download() {
    setProblem(null);
    try {
      const out = await fileOf("/api/memory/tags/export");
      saveFile(out.blob, out.name ?? "workbench-tags.csv");
    } catch (failure) {
      setProblem(`Could not export your tags: ${failureWords(failure)}`);
    }
  }

  async function importFile(text: string, format: "csv" | "txt", confirmed?: string[]) {
    setProblem(null);
    setSaid(null);
    let answer: TagImportAnswer | null = null;
    const ok = await change(async () => {
      answer = await post<TagImportAnswer>("/api/memory/tags/import", {
        text,
        format,
        replace,
        ...(confirmed ? { confirm: confirmed } : {}),
      });
    });
    const got = answer as TagImportAnswer | null;
    if (!ok || !got) return;
    setProblems(got.problems);
    if (got.confirmNeeded) {
      if (got.wouldRemove.length === 0) {
        await importFile(text, format, []);
        return;
      }
      setPending({ text, format, wouldRemove: got.wouldRemove, problems: got.problems });
      return;
    }
    setPending(null);
    setFile(null);
    if (chosen.current) chosen.current.value = "";
    const gone = got.removed.length > 0 ? `, removed ${got.removed.length}` : "";
    setSaid(`Added ${got.added}, updated ${got.updated}${gone}.`);
  }

  async function startImport(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    let text: string;
    try {
      text = await readText(file);
    } catch (failure) {
      setProblem(`Could not read ${file.name}: ${failureWords(failure)}`);
      return;
    }
    await importFile(text, file.name.toLowerCase().endsWith(".txt") ? "txt" : "csv");
  }

  return (
    <section aria-labelledby={`${uid}-tags`} data-testid="memory-tags" className="section">
      <h2 id={`${uid}-tags`} className="title-section">
        Tags
      </h2>
      <p className="help">
        Tag notes to find them again. In a search, #tag finds only the notes that have it. A tag is
        at most {limits.maxTagChars} characters; its description is at most{" "}
        {limits.maxDescriptionChars}.
      </p>
      {problem && (
        <p role="alert" data-testid="memory-tags-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {said && !problem && (
        <p role="status" data-testid="memory-tags-said" className="notice notice--ok">
          <CircleCheck size={16} aria-hidden />
          {said}
        </p>
      )}
      {tags.length === 0 ? (
        <p className="empty">No tags yet.</p>
      ) : (
        <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
          {tags.map((tag) => (
            <li key={tag.key} data-testid="memory-tag" className="flex flex-col gap-2 px-4 py-3">
              {editing === tag.key ? (
                <form
                  aria-label={`Edit tag: ${tag.tag}`}
                  className="flex flex-col gap-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveEdit(tag);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditing(null);
                  }}
                >
                  <div className="field">
                    <label htmlFor={`${uid}-${tag.key}-name`} className="field-label">
                      Tag name
                    </label>
                    <input
                      id={`${uid}-${tag.key}-name`}
                      data-testid="memory-tag-edit-name"
                      autoFocus
                      value={draft.tag}
                      disabled={busy}
                      onChange={(event) => setDraft({ ...draft, tag: event.target.value })}
                      className="control"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`${uid}-${tag.key}-description`} className="field-label">
                      Description
                    </label>
                    <input
                      id={`${uid}-${tag.key}-description`}
                      data-testid="memory-tag-edit-description"
                      value={draft.description}
                      disabled={busy}
                      onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                      className="control"
                    />
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      data-testid="memory-tag-edit-cancel"
                      disabled={busy}
                      onClick={() => {
                        setEditing(null);
                        setConfirm(null);
                      }}
                      className="btn btn-quiet"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      data-testid="memory-tag-edit-save"
                      disabled={busy || !draft.tag.trim()}
                      className="btn btn-primary"
                    >
                      Save tag
                    </button>
                  </div>
                </form>
              ) : (
                <>
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span data-testid="memory-tag-name" className="badge">
                      {tag.tag}
                    </span>
                    <span data-testid="memory-tag-count" className="text-xs text-muted">
                      {plural(tag.notes, "note")}
                    </span>
                  </div>
                  {tag.description && (
                    <p
                      dir="auto"
                      data-testid="memory-tag-description"
                      className="help wrap-anywhere"
                    >
                      {tag.description}
                    </p>
                  )}
                  <div className="-ms-2 flex flex-wrap items-center gap-1">
                    <button
                      type="button"
                      data-testid="memory-tag-edit"
                      aria-label={`Edit tag: ${tag.tag}`}
                      disabled={busy}
                      onClick={() => startEdit(tag)}
                      className="btn btn-quiet"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      data-testid="memory-tag-delete"
                      aria-label={`Delete tag: ${tag.tag}`}
                      disabled={busy}
                      onClick={() => setConfirm({ kind: "delete", tag })}
                      className="btn btn-quiet btn-danger"
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
              {confirm?.kind === "delete" && confirm.tag.key === tag.key && (
                <div
                  role="group"
                  aria-label={`Delete tag ${tag.tag}?`}
                  data-testid="memory-tag-delete-confirm"
                  className="notice notice--error flex-col"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setConfirm(null);
                  }}
                >
                  <p>
                    Delete the tag {"“"}
                    {tag.tag}
                    {"”"}? {plural(tag.notes, "note")} lose{tag.notes === 1 ? "s" : ""} it. The
                    notes stay.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      data-testid="memory-tag-delete-cancel"
                      onClick={() => setConfirm(null)}
                      className="btn"
                    >
                      Keep the tag
                    </button>
                    <button
                      type="button"
                      data-testid="memory-tag-delete-go"
                      disabled={busy}
                      onClick={() => void remove(tag)}
                      className="btn btn-danger"
                    >
                      Delete tag
                    </button>
                  </div>
                </div>
              )}
              {confirm?.kind === "merge" && confirm.from.key === tag.key && (
                <div
                  role="group"
                  aria-label={`Merge tag ${confirm.from.tag} into ${confirm.into.tag}?`}
                  data-testid="memory-tag-merge-confirm"
                  className="notice notice--error flex-col"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setConfirm(null);
                  }}
                >
                  <p>
                    {"“"}
                    {confirm.into.tag}
                    {"”"} is already on your list. Merge {"“"}
                    {confirm.from.tag}
                    {"”"} into it? {plural(confirm.from.notes, "note")} tagged {"“"}
                    {confirm.from.tag}
                    {"”"} get {"“"}
                    {confirm.into.tag}
                    {"”"} instead, once. {"“"}
                    {confirm.from.tag}
                    {"”"} leaves your list.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      data-testid="memory-tag-merge-cancel"
                      onClick={() => setConfirm(null)}
                      className="btn"
                    >
                      Keep them apart
                    </button>
                    <button
                      type="button"
                      data-testid="memory-tag-merge-go"
                      disabled={busy}
                      onClick={() => void saveEdit(confirm.from, confirm)}
                      className="btn btn-danger"
                    >
                      Merge tags
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      <form
        aria-labelledby={`${uid}-add`}
        data-testid="memory-tag-add-form"
        className="card card--add"
        onSubmit={(event) => void add(event)}
      >
        <h3 id={`${uid}-add`} className="title-card">
          Add a tag
        </h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="field">
            <label htmlFor={`${uid}-new`} className="field-label">
              Tag
            </label>
            <input
              id={`${uid}-new`}
              data-testid="memory-tag-add-name"
              value={name}
              disabled={busy}
              autoComplete="off"
              onChange={(event) => setName(event.target.value)}
              className="control"
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-new-description`} className="field-label">
              Description (optional)
            </label>
            <input
              id={`${uid}-new-description`}
              data-testid="memory-tag-add-description"
              value={description}
              disabled={busy}
              autoComplete="off"
              onChange={(event) => setDescription(event.target.value)}
              className="control"
            />
          </div>
        </div>
        <button
          type="submit"
          data-testid="memory-tag-add"
          disabled={busy || !name.trim()}
          className="btn btn-primary self-start"
        >
          Add tag
        </button>
      </form>
      <div className="flex flex-col gap-3">
        <h3 className="title-card">Export or import tags</h3>
        <div>
          <button
            type="button"
            data-testid="memory-tags-export"
            onClick={() => void download()}
            className="btn"
          >
            <Download size={14} aria-hidden />
            Export tags as CSV
          </button>
        </div>
        <form className="flex flex-col gap-2" onSubmit={(event) => void startImport(event)}>
          <div className="field">
            <label htmlFor={`${uid}-file`} className="field-label">
              Tag file (.csv or .txt)
            </label>
            <input
              id={`${uid}-file`}
              ref={chosen}
              type="file"
              accept=".csv,.txt"
              data-testid="memory-tags-file"
              aria-label="Tag file (.csv or .txt)"
              disabled={busy}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setPending(null);
                setProblems([]);
              }}
              className="control"
            />
            <p className="help">
              A CSV has the tag, then its description, on each line. A .txt has one tag a line.
            </p>
          </div>
          <div>
            <label className="check">
              <input
                type="checkbox"
                data-testid="memory-tags-replace"
                checked={replace}
                disabled={busy}
                aria-describedby={`${uid}-replace-help`}
                onChange={(event) => {
                  setReplace(event.target.checked);
                  setPending(null);
                }}
              />
              <span>Replace my list</span>
            </label>
            <p id={`${uid}-replace-help`} className="help ps-[1.625rem]">
              Off, new tags are added and descriptions updated; nothing is removed. On, tags that
              are not in the file leave your list and your notes, after you confirm.
            </p>
          </div>
          <button
            type="submit"
            data-testid="memory-tags-import"
            disabled={busy || !file}
            className="btn self-start"
          >
            <Upload size={14} aria-hidden />
            Import tags
          </button>
        </form>
        {pending && (
          <div
            role="group"
            aria-label="Replace my list?"
            data-testid="memory-tags-replace-confirm"
            className="notice notice--error flex-col"
            onKeyDown={(event) => {
              if (event.key === "Escape") setPending(null);
            }}
          >
            <p>
              Replace my list removes {plural(pending.wouldRemove.length, "tag")} that{" "}
              {pending.wouldRemove.length === 1 ? "is" : "are"} not in the file, and takes{" "}
              {pending.wouldRemove.length === 1 ? "it" : "them"} off the notes. The notes stay.
            </p>
            <ul data-testid="memory-tags-would-remove" className="flex flex-col gap-1">
              {pending.wouldRemove.map((w) => (
                <li key={w.tag} dir="auto" className="wrap-anywhere">
                  {w.tag}: {plural(w.notes, "note")}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                data-testid="memory-tags-replace-cancel"
                onClick={() => setPending(null)}
                className="btn"
              >
                Cancel
              </button>
              <button
                type="button"
                data-testid="memory-tags-replace-go"
                disabled={busy}
                onClick={() =>
                  void importFile(
                    pending.text,
                    pending.format,
                    pending.wouldRemove.map((w) => w.tag),
                  )
                }
                className="btn btn-danger"
              >
                Yes, replace my list
              </button>
            </div>
          </div>
        )}
        <Problems problems={problems} />
      </div>
    </section>
  );
}
