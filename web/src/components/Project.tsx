/**
 * A project's page (MV7): its name, its chats, the notes and documents kept to it, the two
 * switches that say whether its chats are also given the person's general notes and also search
 * My documents, and the one question that deletes it. A change that fails says why, in the server's words, and the page shows what the
 * server holds, never what was asked for.
 */

import {
  ArrowLeft,
  CircleAlert,
  CircleCheck,
  FolderOpen,
  Pin,
  Plus,
  TriangleAlert,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";

import { api, del, fileOf, patch, post } from "../lib/api";
import { characters, failureWords, kindLabel, NOTE_LIMIT, plural, saveFile } from "../lib/memory";
import { problemWords } from "../lib/projects";
import { OWNER_OFF, OWNER_OFF_EFFECT } from "../lib/savedDocuments";
import type { MemoryNote, NotesFound, ProjectDetail } from "../lib/types";
import { useSavedDocuments } from "../lib/useSavedDocuments";
import { AddDocuments } from "./AddDocuments";
import { DocumentRow } from "./DocumentRow";
import { ProjectFolders } from "./ProjectFolders";

export function ProjectPage({
  projectId,
  onOpenChat,
  onNewChat,
  creating = false,
  onChanged,
  onClose,
  onOpenMemory,
}: {
  projectId: string;
  onOpenChat: (id: string) => void;
  /** Makes a chat in this project and opens it. */
  onNewChat: () => void;
  creating?: boolean;
  /** The project, its chats or its notes changed: the sidebar reads again. */
  onChanged: () => void;
  /** Back to the chat list. */
  onClose: () => void;
  /** Opens the Memory page, where a note is edited, pinned or moved. */
  onOpenMemory?: () => void;
}) {
  const uid = useId();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [notes, setNotes] = useState<MemoryNote[]>([]);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [switchProblem, setSwitchProblem] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const [docsSwitchProblem, setDocsSwitchProblem] = useState<string | null>(null);
  const [docsSwitching, setDocsSwitching] = useState(false);
  const [docBusy, setDocBusy] = useState(false);
  const [rowProblems, setRowProblems] = useState<Record<string, string>>({});
  const saved = useSavedDocuments(projectId);
  const latest = useRef(0);
  // The name box is filled from the first answer, then by what is typed and by a rename.
  const named = useRef(false);

  const load = useCallback(async () => {
    const mine = ++latest.current;
    try {
      const [found, listed] = await Promise.all([
        api<ProjectDetail>(`/api/projects/${encodeURIComponent(projectId)}`),
        api<NotesFound>(`/api/memory/notes?where=${encodeURIComponent(projectId)}&limit=200`),
      ]);
      if (mine !== latest.current) return;
      setProject(found);
      setNotes(listed.notes ?? []);
      if (!named.current) {
        named.current = true;
        setName(found.name);
      }
      setLoadProblem(null);
    } catch (failure) {
      if (mine === latest.current) setLoadProblem(problemWords(failure));
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const header = (
    <header className="page-head">
      <h1 id="view-heading" tabIndex={-1} className="title-page" data-testid="project-title">
        <FolderOpen size={20} aria-hidden />
        {project?.name ?? "Project"}
      </h1>
      <div className="flex flex-wrap gap-2">
        {project && (
          <button
            type="button"
            data-testid="project-new-chat-page"
            className="btn btn-primary"
            disabled={creating}
            onClick={onNewChat}
          >
            <Plus size={14} aria-hidden />
            {creating ? "Creating…" : "New chat"}
          </button>
        )}
        <button type="button" data-testid="project-back" className="btn" onClick={onClose}>
          <ArrowLeft size={14} aria-hidden />
          Back to chat
        </button>
      </div>
    </header>
  );

  if (!project) {
    return (
      <section aria-label="Project" className="page" data-testid="project-page">
        <div className="page-col">
          {header}
          {loadProblem ? (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read this project</span>
                {loadProblem}
                <div className="mt-2">
                  <button type="button" className="btn" onClick={() => void load()}>
                    Try again
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <p role="status" className="text-sm text-muted">
              Loading the project…
            </p>
          )}
        </div>
      </section>
    );
  }

  async function rename(event: FormEvent) {
    event.preventDefault();
    const next = name.trim();
    if (!project || !next || next === project.name || saving) return;
    setSaving(true);
    setProblem(null);
    setSaid(null);
    try {
      const kept = await patch<ProjectDetail>(`/api/projects/${encodeURIComponent(project.id)}`, {
        name: next,
      });
      setProject({ ...project, ...kept, chatList: project.chatList });
      setName(kept.name);
      setSaid("Renamed.");
      onChanged();
    } catch (failure) {
      setProblem(problemWords(failure));
    } finally {
      setSaving(false);
    }
  }

  async function setGeneralMemory(on: boolean) {
    if (!project) return;
    setSwitching(true);
    setSwitchProblem(null);
    try {
      const kept = await patch<ProjectDetail>(`/api/projects/${encodeURIComponent(project.id)}`, {
        generalMemory: on,
      });
      setProject({ ...project, ...kept, chatList: project.chatList });
      onChanged();
    } catch (failure) {
      setSwitchProblem(`Not changed: ${problemWords(failure)}`);
    } finally {
      setSwitching(false);
    }
  }

  async function setGeneralDocuments(on: boolean) {
    if (!project) return;
    setDocsSwitching(true);
    setDocsSwitchProblem(null);
    try {
      const kept = await patch<ProjectDetail>(`/api/projects/${encodeURIComponent(project.id)}`, {
        generalDocuments: on,
      });
      setProject({ ...project, ...kept, chatList: project.chatList });
      onChanged();
    } catch (failure) {
      setDocsSwitchProblem(`Not changed: ${problemWords(failure)}`);
    } finally {
      setDocsSwitching(false);
    }
  }

  /** Work on one document: a refusal is said on its row, then the list and the count are read again. */
  async function actOn(id: string, work: () => Promise<unknown>) {
    setRowProblems((now) => {
      const rest = { ...now };
      delete rest[id];
      return rest;
    });
    setDocBusy(true);
    try {
      await work();
    } catch (failure) {
      setRowProblems((now) => ({ ...now, [id]: failureWords(failure) }));
    } finally {
      await saved.reload();
      setDocBusy(false);
      void load();
      onChanged();
    }
  }

  async function remove(keep: boolean) {
    if (!project || deleting) return;
    setDeleting(true);
    setProblem(null);
    try {
      await del(`/api/projects/${encodeURIComponent(project.id)}?keep=${keep ? "true" : "false"}`);
      onChanged();
      onClose();
    } catch (failure) {
      setProblem(problemWords(failure));
      setDeleting(false);
      // What was deleted before it failed is gone: read what is left.
      void load();
    }
  }

  const same = name.trim() === project.name;
  const counts = `${plural(project.chats, "chat")}, ${plural(project.notes, "note")} and ${plural(project.documents, "document")}`;
  const docs = saved.data;
  const docsOff = docs ? !docs.installEnabled : false;

  return (
    <section aria-label="Project" className="page" data-testid="project-page">
      <div className="page-col">
        {header}
        {problem && (
          <p role="alert" data-testid="project-problem" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem}
          </p>
        )}
        {loadProblem && (
          <p role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {loadProblem}{" "}
            <button
              type="button"
              className="btn btn-quiet -my-1 ml-auto"
              onClick={() => void load()}
            >
              Try again
            </button>
          </p>
        )}
        <form
          aria-labelledby={`${uid}-name`}
          className="section"
          onSubmit={(event) => void rename(event)}
        >
          <h2 id={`${uid}-name`} className="title-section">
            Name
          </h2>
          <div className="flex flex-wrap items-end gap-2">
            <div className="field">
              <label htmlFor={`${uid}-name-box`} className="field-label">
                Project name
              </label>
              <input
                id={`${uid}-name-box`}
                data-testid="project-name"
                value={name}
                maxLength={400}
                disabled={saving}
                onChange={(event) => {
                  setName(event.target.value);
                  setSaid(null);
                }}
                className="control w-72 max-w-full"
              />
            </div>
            <button
              type="submit"
              data-testid="project-rename"
              disabled={saving || !name.trim() || same}
              className="btn"
            >
              Rename
            </button>
          </div>
          {said && (
            <p role="status" className="notice notice--ok">
              <CircleCheck size={16} aria-hidden />
              {said}
            </p>
          )}
        </form>

        <section aria-labelledby={`${uid}-chats`} className="section" data-testid="project-chats">
          <div className="field">
            <label className="field-label" htmlFor={`${uid}-model`}>
              Model for new chats
            </label>
            <select
              id={`${uid}-model`}
              data-testid="project-model"
              className="control"
              value={project.model ?? ""}
              disabled={saving}
              onChange={async (event) => {
                setSaving(true);
                setProblem(null);
                try {
                  const next = await patch<ProjectDetail>(
                    `/api/projects/${encodeURIComponent(projectId)}`,
                    { model: event.target.value || null },
                  );
                  setProject((old) => (old ? { ...old, ...next } : next));
                } catch (error) {
                  setProblem(problemWords(error));
                } finally {
                  setSaving(false);
                }
              }}
            >
              <option value="">
                Workbench&apos;s default (
                {project.models?.[0] ?? project.modelInEffect ?? "no model served"})
              </option>
              {project.model && !project.models?.includes(project.model) && (
                <option value={project.model}>{project.model}</option>
              )}
              {project.models?.map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
            {project.model && project.model !== project.modelInEffect && (
              <p className="help">
                {project.model} is not served now. New chats use{" "}
                {project.modelInEffect ?? "no model until one is served"}.
              </p>
            )}
            {project.modelProblem && <p role="alert">{project.modelProblem}</p>}
          </div>
          <h2 id={`${uid}-chats`} className="title-section">
            Chats
          </h2>
          {project.chatList.length === 0 ? (
            <p className="empty">No chats in this project yet. Choose New chat to start one.</p>
          ) : (
            <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
              {project.chatList.map((chat) => (
                <li key={chat.id}>
                  <button
                    type="button"
                    data-testid="project-chat"
                    onClick={() => onOpenChat(chat.id)}
                    className="w-full truncate px-4 py-2 text-left hover:bg-hover"
                  >
                    {chat.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby={`${uid}-notes`} className="section" data-testid="project-notes">
          <h2 id={`${uid}-notes`} className="title-section">
            Notes
          </h2>
          <p className="help">
            Notes kept to this project. Only its chats are given them.
            {onOpenMemory && (
              <>
                {" "}
                To edit, pin or move one, use{" "}
                <button type="button" className="link" onClick={onOpenMemory}>
                  Memory
                </button>
                .
              </>
            )}
          </p>
          {notes.length === 0 ? (
            <p className="empty">No notes in this project yet.</p>
          ) : (
            <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
              {notes.map((note) => (
                <li
                  key={note.id}
                  data-testid="project-note"
                  className="flex flex-col gap-1 px-4 py-3"
                >
                  <p dir="auto" className="wrap-anywhere">
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
                  </div>
                </li>
              ))}
            </ul>
          )}
          <AddProjectNote
            projectId={project.id}
            onKept={() => {
              void load();
              onChanged();
            }}
          />
        </section>

        <section
          aria-labelledby={`${uid}-documents`}
          className="section"
          data-testid="project-documents"
        >
          <h2 id={`${uid}-documents`} className="title-section">
            Documents
          </h2>
          <p className="help">
            Chats in this project search these documents when Documents is on in the chat.
          </p>
          {(saved.blocked || saved.problem) && (
            <p
              role="alert"
              data-testid="project-documents-problem"
              className="notice notice--error"
            >
              <CircleAlert size={16} aria-hidden />
              {saved.blocked?.message ?? saved.problem}{" "}
              <button
                type="button"
                className="btn btn-quiet -my-1 ml-auto"
                onClick={() => void saved.reload()}
              >
                Try again
              </button>
            </p>
          )}
          {docsOff && (
            <p data-testid="project-documents-off" className="notice">
              <span>
                <span className="notice-title">{OWNER_OFF}</span>
                {OWNER_OFF_EFFECT}
              </span>
            </p>
          )}
          {docs &&
            (docs.documents.length === 0 ? (
              <p className="empty">No documents in this project yet.</p>
            ) : (
              <ul
                data-testid="project-documents-list"
                className="divide-y divide-line rounded-plexus border border-line bg-panel"
              >
                {docs.documents.map((doc) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    locked={docsOff}
                    busy={docBusy}
                    problem={rowProblems[doc.id] ?? null}
                    onOpen={() =>
                      void actOn(doc.id, async () => {
                        const file = await fileOf(
                          `/api/documents/${encodeURIComponent(doc.id)}/original`,
                        );
                        saveFile(file.blob, file.name ?? doc.name);
                      })
                    }
                    onReread={() =>
                      void actOn(doc.id, () =>
                        post(`/api/documents/${encodeURIComponent(doc.id)}/reread`),
                      )
                    }
                    onRemove={() =>
                      void actOn(doc.id, () => del(`/api/documents/${encodeURIComponent(doc.id)}`))
                    }
                  />
                ))}
              </ul>
            ))}
          {docs && !docsOff && (
            <AddDocuments
              projectId={project.id}
              onChanged={() => {
                void saved.reload();
                void load();
                onChanged();
              }}
            />
          )}
          <ProjectFolders projectId={projectId} />
          <label className="flex min-h-7 items-center gap-2">
            <input
              type="checkbox"
              data-testid="project-general-documents"
              checked={project.generalDocuments}
              disabled={docsSwitching}
              aria-describedby={`${uid}-documents-help`}
              onChange={(event) => void setGeneralDocuments(event.target.checked)}
            />
            Also search My documents
          </label>
          <p id={`${uid}-documents-help`} className="help">
            Chats in this project also search My documents.
          </p>
          {docsSwitchProblem && (
            <p role="alert" data-testid="project-documents-switch-problem" className="text-error">
              {docsSwitchProblem}
            </p>
          )}
        </section>

        <section aria-labelledby={`${uid}-memory`} className="section">
          <h2 id={`${uid}-memory`} className="title-section">
            Memory
          </h2>
          <label className="flex min-h-7 items-center gap-2">
            <input
              type="checkbox"
              data-testid="project-general-memory"
              checked={project.generalMemory}
              disabled={switching}
              aria-describedby={`${uid}-memory-help`}
              onChange={(event) => void setGeneralMemory(event.target.checked)}
            />
            Also use my general memory
          </label>
          <p id={`${uid}-memory-help`} className="help">
            Chats in this project are also given your general notes. Turned off, they are given only
            this project&apos;s notes.
          </p>
          {switchProblem && (
            <p role="alert" data-testid="project-switch-problem" className="text-error">
              {switchProblem}
            </p>
          )}
        </section>

        <section aria-labelledby={`${uid}-delete`} className="section">
          <h2 id={`${uid}-delete`} className="title-section">
            Delete project
          </h2>
          {!asking ? (
            <div>
              <button
                type="button"
                data-testid="project-delete"
                className="btn btn-danger"
                onClick={() => setAsking(true)}
              >
                Delete project
              </button>
            </div>
          ) : (
            <div
              role="group"
              aria-label="Delete this project?"
              data-testid="project-delete-ask"
              className="notice notice--error flex-col items-start"
              onKeyDown={(event) => {
                if (event.key === "Escape" && !deleting) setAsking(false);
              }}
            >
              <p className="flex items-center gap-2">
                <TriangleAlert size={16} aria-hidden />
                {`This project has ${counts}${project.folders ? ` and ${plural(project.folders, "folder")}` : ""}. What should happen to them?`}
              </p>
              {Boolean(project.folders) && (
                <p>
                  Delete removes the folders from Documents. Keep moves them to My documents. Files
                  on disk stay as they are.
                </p>
              )}
              <div className="flex flex-col items-start gap-2">
                <div>
                  <button
                    type="button"
                    data-testid="project-delete-all"
                    disabled={deleting}
                    onClick={() => void remove(false)}
                    className="btn btn-danger"
                  >
                    Delete the project and its chats, notes and documents
                  </button>
                  <p className="help">{`Deletes ${counts} for good.`}</p>
                </div>
                <div>
                  <button
                    type="button"
                    data-testid="project-delete-keep"
                    disabled={deleting}
                    onClick={() => void remove(true)}
                    className="btn"
                  >
                    Delete the project, keep its chats, notes and documents
                  </button>
                  <p className="help">
                    {`${plural(project.chats, "chat")} and ${plural(project.notes, "note")} stay and become general. ${plural(project.documents, "document")} ${project.documents === 1 ? "moves" : "move"} to My documents; a file of the same name there is replaced.`}
                  </p>
                </div>
                <button
                  type="button"
                  autoFocus
                  data-testid="project-delete-cancel"
                  disabled={deleting}
                  onClick={() => setAsking(false)}
                  className="btn btn-quiet"
                >
                  Keep the project
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

/** A note typed here is kept to the project (MV7). */
function AddProjectNote({ projectId, onKept }: { projectId: string; onKept: () => void }) {
  const uid = useId();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const over = characters(text.trim()) > NOTE_LIMIT;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving || over || !text.trim()) return;
    setSaving(true);
    setProblem(null);
    setSaid(null);
    try {
      const kept = await post<{ created: boolean }>("/api/memory/notes", {
        text: text.trim(),
        kind: "fact",
        projectId,
      });
      setSaid(kept.created ? "Kept to this project." : "This project has that note already.");
      setText("");
      onKept();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      aria-labelledby={`${uid}-add`}
      data-testid="project-add-form"
      className="card card--add"
      onSubmit={(event) => void submit(event)}
    >
      <h3 id={`${uid}-add`} className="title-card">
        Add a note
      </h3>
      <div className="field">
        <label htmlFor={`${uid}-text`} className="field-label">
          What should this project&apos;s chats remember?
        </label>
        <input
          id={`${uid}-text`}
          data-testid="project-add-text"
          value={text}
          disabled={saving}
          aria-invalid={over || undefined}
          onChange={(event) => {
            setText(event.target.value);
            setSaid(null);
          }}
          className="control"
        />
        <p className="help">
          {characters(text.trim())} of {NOTE_LIMIT} characters.
        </p>
      </div>
      {problem && (
        <p role="alert" data-testid="project-add-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {said && (
        <p role="status" className="notice notice--ok">
          <CircleCheck size={16} aria-hidden />
          {said}
        </p>
      )}
      <div>
        <button
          type="submit"
          data-testid="project-add"
          disabled={saving || over || !text.trim()}
          className="btn btn-primary"
        >
          Add note
        </button>
      </div>
    </form>
  );
}
