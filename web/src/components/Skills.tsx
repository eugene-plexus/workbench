/**
 * Toolbox · Skills (row 32): the skills a person added or wrote, and those Eugene gives everyone.
 *
 * A skill is instructions a stranger may have written, so an uploaded skill arrives off and its
 * whole SKILL.md is shown before it is turned on. Every switch shows what the server kept.
 */

import { ArrowLeft, CircleAlert, CircleCheck, ScrollText, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

import { api, ApiError, del, patch, post, upload } from "../lib/api";
import type { Skill, SkillDetail } from "../lib/types";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

const sizeOf = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
    : bytes >= 1024
      ? `${Math.max(1, Math.round(bytes / 1024))} KiB`
      : `${bytes} bytes`;

/** An upload that asked about a name already taken: the file, and the skill with that name. */
type Asked = { file: File; name: string };

export function Skills({ owner, onClose }: { owner: boolean; onClose: () => void }) {
  const uid = useId();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [asked, setAsked] = useState<Asked | null>(null);
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");

  useEffect(() => {
    let live = true;
    void api<{ skills: Skill[] }>("/api/skills")
      .then((result) => {
        if (live) {
          setSkills(result.skills);
          setLoadProblem(null);
        }
      })
      .catch((error) => {
        if (live) setLoadProblem(problemOf(error));
      });
    return () => {
      live = false;
    };
  }, [reload]);

  const refresh = useCallback(() => setReload((n) => n + 1), []);

  async function add(file: File, onConflict?: "replace" | "both") {
    setBusy(true);
    setProblem(null);
    setDone(null);
    setAsked(null);
    const form = new FormData();
    form.append("file", file);
    if (onConflict) form.append("onConflict", onConflict);
    try {
      const skill = await upload<Skill>("/api/skills", form);
      setDone(`Added ${skill.name}, off. Read its SKILL.md below, then turn it on.`);
      refresh();
    } catch (error) {
      const conflict = error instanceof ApiError ? error.detail?.conflict : undefined;
      if (conflict && typeof conflict === "object" && "name" in conflict) {
        setAsked({ file, name: String((conflict as { name: unknown }).name) });
      } else {
        setProblem(problemOf(error));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Skills" className="page">
      <div className="page-col">
        <header className="page-head">
          <h1 id="view-heading" tabIndex={-1} className="title-page">
            <ScrollText size={20} aria-hidden />
            Toolbox · Skills
          </h1>
          <button className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <p className="help">
          A skill is instructions for one kind of task. The model sees each skill&apos;s name and
          description, and reads the rest only when a request needs it. Scripts in a skill run only
          on a Job Site that allows commands.
        </p>

        <section aria-labelledby={`${uid}-add`} className="section">
          <h2 id={`${uid}-add`} className="title-section">
            Add a skill
          </h2>
          <div className="field">
            <label htmlFor={`${uid}-file`} className="field-label">
              Skill file
            </label>
            <input
              id={`${uid}-file`}
              type="file"
              accept=".zip,.md"
              disabled={busy}
              aria-describedby={`${uid}-file-help`}
              className="control"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void add(file);
              }}
            />
            <p id={`${uid}-file-help`} className="help">
              A .zip of a skill&apos;s folder, or one SKILL.md. A skill from Claude Code or Codex
              works as it is. It is added off.
            </p>
          </div>
          {asked && (
            <div role="group" aria-label="A skill with this name" className="notice flex-col">
              <p>You already have a skill named {asked.name}.</p>
              <div className="flex flex-wrap gap-2">
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void add(asked.file, "replace")}
                >
                  Replace it
                </button>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => void add(asked.file, "both")}
                >
                  Keep both
                </button>
                <button className="btn btn-quiet" onClick={() => setAsked(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {problem && (
            <p role="alert" className="notice notice--error whitespace-pre-wrap">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
          {done && (
            <p role="status" className="notice notice--ok">
              <CircleCheck size={16} aria-hidden />
              {done}
            </p>
          )}
          <form
            aria-labelledby={`${uid}-write`}
            className="card card--add"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setProblem(null);
              setDone(null);
              try {
                const skill = await post<Skill>("/api/skills/written", {
                  name,
                  description,
                  instructions,
                });
                setDone(`Added ${skill.name}, on.`);
                setName("");
                setDescription("");
                setInstructions("");
                refresh();
              } catch (error) {
                setProblem(problemOf(error));
              } finally {
                setBusy(false);
              }
            }}
          >
            <h3 id={`${uid}-write`} className="title-card">
              Write a skill
            </h3>
            <div className="field">
              <label htmlFor={`${uid}-name`} className="field-label">
                Name
              </label>
              <input
                id={`${uid}-name`}
                required
                maxLength={64}
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-describedby={`${uid}-name-help`}
                className="control"
              />
              <p id={`${uid}-name-help`} className="help">
                Lowercase letters, digits and hyphens, for example meeting-notes.
              </p>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-description`} className="field-label">
                When to use it
              </label>
              <input
                id={`${uid}-description`}
                required
                maxLength={1024}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                aria-describedby={`${uid}-description-help`}
                className="control"
              />
              <p id={`${uid}-description-help`} className="help">
                One line. The model reads it to choose the skill.
              </p>
            </div>
            <div className="field">
              <label htmlFor={`${uid}-instructions`} className="field-label">
                Instructions
              </label>
              <textarea
                id={`${uid}-instructions`}
                rows={6}
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                className="control resize-y"
              />
            </div>
            <button disabled={busy} type="submit" className="btn btn-primary self-start">
              Add skill
            </button>
          </form>
        </section>

        <section aria-labelledby={`${uid}-list`} className="section">
          <h2 id={`${uid}-list`} className="title-section">
            Your skills
          </h2>
          {loadProblem && (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read your skills</span>
                {loadProblem}
                <div className="mt-2">
                  <button type="button" className="btn" onClick={refresh}>
                    Try again
                  </button>
                </div>
              </div>
            </div>
          )}
          {skills === null && !loadProblem && (
            <p role="status" className="text-sm text-muted">
              Loading skills…
            </p>
          )}
          {skills?.length === 0 && <p className="empty">No skills yet.</p>}
          {skills?.map((skill) => (
            <SkillCard key={skill.id} skill={skill} owner={owner} onChanged={refresh} />
          ))}
        </section>
      </div>
    </section>
  );
}

function SkillCard({
  skill,
  owner,
  onChanged,
}: {
  skill: Skill;
  owner: boolean;
  onChanged: () => void;
}) {
  const uid = useId();
  // An off skill shows its whole SKILL.md: it is read before it is turned on.
  const [open, setOpen] = useState(!skill.on);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [filePath, setFilePath] = useState("");

  useEffect(() => {
    if (!open) return;
    let live = true;
    void api<SkillDetail>(`/api/skills/${encodeURIComponent(skill.id)}`)
      .then((found) => {
        if (live) setDetail(found);
      })
      .catch((error) => {
        if (live) setProblem(problemOf(error));
      });
    return () => {
      live = false;
    };
  }, [open, skill.id, skill.updatedAt]);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setProblem(null);
    try {
      await work();
      onChanged();
    } catch (error) {
      setProblem(problemOf(error));
    } finally {
      setBusy(false);
    }
  }

  const path = `/api/skills/${encodeURIComponent(skill.id)}`;
  return (
    <article className="card" aria-labelledby={`${uid}-name`}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <h3 id={`${uid}-name`} className="title-card">
          {skill.name}
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {skill.fromEugene && <span className="badge">From Eugene</span>}
          {skill.everyone && !skill.fromEugene && <span className="badge">Given to everyone</span>}
          {skill.hasScripts && <span className="badge">Has scripts</span>}
          {!skill.on && <span className="badge badge--warn">Off</span>}
        </div>
      </div>
      <p className="text-sm">{skill.description}</p>
      {skill.on && !skill.listed && skill.notListed && (
        <p className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          {skill.notListed}
        </p>
      )}
      {skill.fromEugene && (
        <p className="help">
          Eugene gives this skill to everyone here. You can turn it off for yourself.
        </p>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={skill.on}
          disabled={busy || (!skill.on && !detail)}
          onChange={() => void act(() => patch(path, { on: !skill.on }))}
          className="accent-accent"
        />
        Use in chats: {skill.on ? "On" : "Off"}
      </label>
      {!skill.on && !detail && (
        <p className="help">Read its SKILL.md below before you turn it on.</p>
      )}
      {owner && skill.canEdit && (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={skill.everyone}
            disabled={busy}
            onChange={() => void act(() => patch(path, { everyone: !skill.everyone }))}
            className="accent-accent"
          />
          Give to everyone on this install: {skill.everyone ? "On" : "Off"}
        </label>
      )}
      {skill.files.length > 0 && (
        <ul className="text-sm" aria-label={`Files of ${skill.name}`}>
          {skill.files.map((file) => (
            <li key={file.path} className="flex flex-wrap items-center gap-x-2">
              <code className="font-mono text-[0.8125rem]">{file.path}</code>
              <span className="text-muted">{sizeOf(file.size)}</span>
              {skill.canEdit && (
                <button
                  className="btn btn-quiet btn-danger"
                  disabled={busy}
                  aria-label={`Remove ${file.path} from ${skill.name}`}
                  onClick={() =>
                    void act(() =>
                      del(
                        `${path}/files/${file.path.split("/").map(encodeURIComponent).join("/")}`,
                      ),
                    )
                  }
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn" aria-expanded={open} onClick={() => setOpen((shown) => !shown)}>
          {open ? "Hide SKILL.md" : "Read SKILL.md"}
        </button>
        {skill.canEdit && (
          <button
            className="btn"
            disabled={busy}
            onClick={() => {
              setEditing((shown) => !shown);
              setOpen(true);
            }}
          >
            {editing ? "Stop editing" : "Edit"}
          </button>
        )}
        {skill.canEdit && !removing && (
          <button
            className="btn btn-quiet btn-danger -me-2 ml-auto"
            aria-label={`Delete ${skill.name}`}
            onClick={() => setRemoving(true)}
          >
            Delete
          </button>
        )}
      </div>
      {removing && (
        <div
          role="group"
          aria-label={`Delete ${skill.name}?`}
          className="notice notice--error flex-col"
        >
          <p>
            Delete {skill.name}? Its files go too.
            {skill.everyone ? " Nobody on this install has it after this." : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            <button autoFocus className="btn" onClick={() => setRemoving(false)}>
              Keep it
            </button>
            <button
              disabled={busy}
              className="btn btn-danger"
              aria-label={`Delete skill ${skill.name}`}
              onClick={() => void act(() => del(path))}
            >
              Delete skill
            </button>
          </div>
        </div>
      )}
      {problem && (
        <p role="alert" className="notice notice--error whitespace-pre-wrap">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {open && detail && !editing && (
        <div className="flex flex-col gap-2">
          {detail.problem && (
            <p className="notice notice--warn">
              <TriangleAlert size={16} aria-hidden />
              {detail.problem}
            </p>
          )}
          <pre
            aria-label={`SKILL.md of ${skill.name}`}
            className="max-h-96 overflow-auto whitespace-pre-wrap rounded-plexus border border-line p-3 font-mono text-[0.8125rem]"
          >
            {detail.skillMd}
          </pre>
          {detail.others.length > 0 && (
            <p className="help">
              Also in its frontmatter, kept and not used by Workbench:{" "}
              {detail.others.map((o) => o.key).join(", ")}.
            </p>
          )}
        </div>
      )}
      {editing && detail && (
        <SkillEdit
          detail={detail}
          busy={busy}
          onSave={(words) =>
            void act(async () => {
              await patch(path, words);
              setEditing(false);
            })
          }
        />
      )}
      {skill.canEdit && (
        <form
          aria-label={`Add a file to ${skill.name}`}
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const input = event.currentTarget.elements.namedItem("file") as HTMLInputElement | null;
            const file = input?.files?.[0];
            if (!file) return;
            const form = new FormData();
            form.append("file", file);
            form.append("path", filePath || file.name);
            void act(async () => {
              await upload(`${path}/files`, form);
              setFilePath("");
              if (input) input.value = "";
            });
          }}
        >
          <div className="field">
            <label htmlFor={`${uid}-path`} className="field-label">
              Place in the skill
            </label>
            <input
              id={`${uid}-path`}
              value={filePath}
              placeholder="references/notes.md"
              onChange={(e) => setFilePath(e.target.value)}
              className="control"
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-add-file`} className="field-label">
              File
            </label>
            <input id={`${uid}-add-file`} name="file" type="file" className="control" />
          </div>
          <button type="submit" disabled={busy} className="btn">
            Add file
          </button>
        </form>
      )}
    </article>
  );
}

function SkillEdit({
  detail,
  busy,
  onSave,
}: {
  detail: SkillDetail;
  busy: boolean;
  onSave: (words: { name: string; description: string; instructions: string }) => void;
}) {
  const uid = useId();
  const [name, setName] = useState(detail.name);
  const [description, setDescription] = useState(detail.description);
  const [instructions, setInstructions] = useState(detail.instructions ?? "");
  return (
    <form
      aria-label={`Edit ${detail.name}`}
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({ name, description, instructions });
      }}
    >
      <div className="field">
        <label htmlFor={`${uid}-name`} className="field-label">
          Name
        </label>
        <input
          id={`${uid}-name`}
          required
          maxLength={64}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="control"
        />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-description`} className="field-label">
          When to use it
        </label>
        <input
          id={`${uid}-description`}
          required
          maxLength={1024}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="control"
        />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-instructions`} className="field-label">
          Instructions
        </label>
        <textarea
          id={`${uid}-instructions`}
          rows={10}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          className="control resize-y font-mono"
        />
      </div>
      <button type="submit" disabled={busy} className="btn btn-primary self-start">
        Save skill
      </button>
    </form>
  );
}

/** A chat's skills (row 32): all the person's skills that are on, or a pick of them. */
export function SkillSelection({
  selected,
  onChange,
}: {
  /** null is "All my skills"; an empty list is none. */
  selected: string[] | null;
  onChange: (ids: string[] | null) => void;
}) {
  const uid = useId();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void api<{ skills: Skill[] }>("/api/skills")
      .then((result) => {
        if (live) setSkills(result.skills);
      })
      .catch((error) => {
        if (live) setProblem(problemOf(error));
      });
    return () => {
      live = false;
    };
  }, []);
  const usable = (skills ?? []).filter((s) => s.on);
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="eyebrow mb-2 text-muted">Toolbox · Skills for this chat</legend>
      <p className="help">Applies to the next answer. Skills run without asking: they only read.</p>
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <label className="check">
        <input
          type="radio"
          name={`${uid}-skills`}
          checked={selected === null}
          onChange={() => onChange(null)}
        />
        <span>All my skills</span>
      </label>
      <label className="check">
        <input
          type="radio"
          name={`${uid}-skills`}
          checked={selected !== null}
          onChange={() => onChange(usable.map((s) => s.id))}
        />
        <span>Only these</span>
      </label>
      {selected !== null && skills?.length === 0 && (
        <p className="empty">No skills yet. Add one in Toolbox · Skills.</p>
      )}
      {selected !== null &&
        usable.map((skill) => (
          <label key={skill.id} className="check ml-6">
            <input
              type="checkbox"
              checked={selected.includes(skill.id)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, skill.id]
                    : selected.filter((id) => id !== skill.id),
                )
              }
            />
            <span>
              {skill.name}
              {skill.fromEugene && (
                <>
                  {" "}
                  <span className="text-muted">· From Eugene</span>
                </>
              )}
            </span>
          </label>
        ))}
      {selected !== null &&
        skills &&
        selected
          .filter((id) => !usable.some((s) => s.id === id))
          .map((id) => (
            <label key={id} className="check ml-6">
              <input
                type="checkbox"
                checked
                onChange={() => onChange(selected.filter((i) => i !== id))}
              />
              <span className="text-warn">Removed or turned off skill — uncheck to clear it</span>
            </label>
          ))}
    </fieldset>
  );
}
