/**
 * A chat's settings. An unset value is the model's own default and says
 * so -- never a number that looks like a choice (Troy's rule: settings
 * never lie). Clearing a box returns it to the model's default.
 */

import { Check, CircleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import type { ChatSettings as Settings, Project } from "../lib/types";
import { ToolSelection } from "./Tools";
import { FolderSelection } from "./Folders";

const FIELDS: {
  key: "temperature" | "topP" | "maxTokens";
  label: string;
  hint: string;
  step: string;
  min: number;
  max: number;
}[] = [
  {
    key: "temperature",
    label: "Temperature",
    hint: "Higher is more varied, lower more predictable.",
    step: "0.1",
    min: 0,
    max: 2,
  },
  {
    key: "topP",
    label: "Top-p",
    hint: "Samples only from the likeliest tokens that add up to this share.",
    step: "0.05",
    min: 0.01,
    max: 1,
  },
  {
    key: "maxTokens",
    label: "Longest answer (tokens)",
    hint: "The answer stops at this length.",
    step: "1",
    min: 1,
    max: 1000000,
  },
];

/** Where the sheet covers the whole window instead of sitting above the chat
 * (the same width as the `.sheet` rule in styles.css). */
export const SHEET_OVERLAY_QUERY = "(max-width: 47.99rem)";

/** Whether the sheet is an overlay now. jsdom and old browsers have no
 * matchMedia: there the sheet is the panel above the chat. */
export function useOverlay(): boolean {
  const ask = () =>
    typeof window.matchMedia === "function" && window.matchMedia(SHEET_OVERLAY_QUERY).matches;
  const [overlay, setOverlay] = useState(ask);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(SHEET_OVERLAY_QUERY);
    const update = () => setOverlay(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, []);
  return overlay;
}

export const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

export function ChatSettings({
  settings,
  onSave,
  onClose,
  projects,
  projectId = null,
  onMove,
}: {
  settings: Settings;
  onSave: (next: Settings) => Promise<void>;
  onClose: () => void;
  /** MV7: the person's projects, once read; null while they are being read. */
  projects?: Project[] | null;
  /** The project this chat is in now. */
  projectId?: string | null;
  /** Puts the chat in a project, or in none; a refusal is thrown with the server's words. */
  onMove?: (projectId: string | null) => Promise<void>;
}) {
  const uid = useId();
  const overlay = useOverlay();
  const sheet = useRef<HTMLElement>(null);
  const firstField = useRef<HTMLTextAreaElement>(null);
  const openedAsOverlay = useRef(overlay);
  const [instructions, setInstructions] = useState(settings.instructions ?? "");
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      FIELDS.map((f) => [f.key, settings[f.key] != null ? String(settings[f.key]) : ""]),
    ),
  );
  const [problem, setProblem] = useState<{ message: string; field: string | null } | null>(null);
  const [saved, setSaved] = useState(false);
  const [toolServers, setToolServers] = useState(settings.toolServers ?? []);
  const [folderGrants, setFolderGrants] = useState(settings.folderGrants ?? []);
  const [repetitionMode, setRepetitionMode] = useState<
    NonNullable<Settings["repetitionMode"]> | ""
  >(settings.repetitionMode ?? "");
  const alertId = `${uid}-problem`;
  const [target, setTarget] = useState(projectId ?? "");
  const [moveProblem, setMoveProblem] = useState<string | null>(null);
  const [moved, setMoved] = useState(false);
  const [moving, setMoving] = useState(false);
  // What the chat is in is what the box shows, once it has been moved or re-read.
  useEffect(() => setTarget(projectId ?? ""), [projectId]);

  async function move() {
    if (!onMove || moving) return;
    setMoving(true);
    setMoveProblem(null);
    setMoved(false);
    try {
      await onMove(target || null);
      setMoved(true);
    } catch (error) {
      setMoveProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setMoving(false);
    }
  }

  // Over the whole window the sheet is a dialog: focus goes into it, and back
  // to the button that opened it when it closes (also when it is a panel).
  useEffect(() => {
    const opener = document.activeElement;
    if (openedAsOverlay.current) firstField.current?.focus({ preventScroll: true });
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onClose();
      return;
    }
    // A dialog keeps Tab inside it: from its last control to its first and back.
    if (event.key !== "Tab" || !overlay || !sheet.current) return;
    // In page order, whatever order the selector list is answered in.
    const items = [...sheet.current.querySelectorAll<HTMLElement>(FOCUSABLE)].sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1,
    );
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function save() {
    const next: Settings = {
      instructions: instructions.trim() ? instructions : null,
      toolServers,
      folderGrants,
      repetitionMode: repetitionMode || null,
    };
    for (const field of FIELDS) {
      const raw = values[field.key]?.trim() ?? "";
      if (!raw) {
        next[field.key] = null;
        continue;
      }
      const number = Number(raw);
      if (!Number.isFinite(number) || number < field.min || number > field.max) {
        setProblem({
          message: `${field.label} must be between ${field.min} and ${field.max}, or empty for the model's default.`,
          field: field.key,
        });
        return;
      }
      next[field.key] = field.key === "maxTokens" ? Math.round(number) : number;
    }
    try {
      await onSave(next);
      setProblem(null);
      setSaved(true);
    } catch (error) {
      setProblem({ message: error instanceof Error ? error.message : String(error), field: null });
    }
  }

  return (
    <section
      ref={sheet}
      aria-label="This chat's settings"
      role={overlay ? "dialog" : undefined}
      aria-modal={overlay ? true : undefined}
      className="sheet"
      data-testid="chat-settings"
      onKeyDown={onKeyDown}
    >
      <div className="sheet-body">
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {/* Over the whole window the chat's own title is out of sight: the
              sheet says what it is. Above the chat the settings button says so. */}
          {overlay && <h2 className="title-card">This chat&apos;s settings</h2>}
          <fieldset className="flex min-w-0 flex-col gap-4">
            <legend className="eyebrow mb-3 text-muted">Answers</legend>
            <div className="field">
              <label htmlFor={`${uid}-instructions`} className="field-label">
                Instructions
              </label>
              <textarea
                id={`${uid}-instructions`}
                ref={firstField}
                value={instructions}
                onChange={(e) => {
                  setInstructions(e.target.value);
                  setSaved(false);
                }}
                rows={3}
                placeholder="None"
                aria-describedby={`${uid}-instructions-help`}
                className="control resize-y"
              />
              <p id={`${uid}-instructions-help`} className="help">
                Told to the model before every message in this chat.
              </p>
            </div>
            <div className="grid gap-4 sm:grid-cols-3">
              {FIELDS.map((field) => {
                const hint = `${uid}-hint-${field.key}`;
                const invalid = problem?.field === field.key;
                return (
                  <div key={field.key} className="field">
                    <label htmlFor={`${uid}-${field.key}`} className="field-label">
                      {field.label}
                    </label>
                    <input
                      id={`${uid}-${field.key}`}
                      inputMode="decimal"
                      value={values[field.key]}
                      onChange={(e) => {
                        setValues((v) => ({ ...v, [field.key]: e.target.value }));
                        setSaved(false);
                      }}
                      placeholder="The model's default"
                      aria-describedby={invalid ? `${hint} ${alertId}` : hint}
                      aria-invalid={invalid || undefined}
                      className={`control ${invalid ? "border-error-line border-2" : ""}`}
                    />
                    <p id={hint} className="help">
                      {field.hint} Between {field.min} and {field.max}.
                    </p>
                  </div>
                );
              })}
            </div>
            <div className="field">
              <label htmlFor={`${uid}-repetition`} className="field-label">
                Repeated response protection
              </label>
              <select
                id={`${uid}-repetition`}
                value={repetitionMode}
                onChange={(e) => {
                  setRepetitionMode(e.target.value as typeof repetitionMode);
                  setSaved(false);
                }}
                aria-describedby={`${uid}-repetition-help`}
                className="control"
              >
                <option value="">Use Eugene's setting</option>
                <option value="stop">Stop repeated responses</option>
                <option value="observe">Observe without stopping</option>
                <option value="off">Off — allow intentional repetition</option>
              </select>
              <p id={`${uid}-repetition-help`} className="help">
                Stops substantial repeated passages, or allows intentional repetition. Reasoning and
                tool arguments are never stopped. Time and length limits still apply.
              </p>
            </div>
          </fieldset>
          {onMove && (
            <fieldset className="flex min-w-0 flex-col gap-2" data-testid="chat-project-field">
              <legend className="eyebrow mb-3 text-muted">Project</legend>
              <div className="flex flex-wrap items-end gap-2">
                <div className="field">
                  <label htmlFor={`${uid}-project`} className="field-label">
                    Move to project
                  </label>
                  <select
                    id={`${uid}-project`}
                    data-testid="chat-project-select"
                    value={target}
                    disabled={moving}
                    onChange={(e) => {
                      setTarget(e.target.value);
                      setMoved(false);
                    }}
                    aria-describedby={`${uid}-project-help`}
                    className="control"
                  >
                    <option value="">No project</option>
                    {(projects ?? []).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </div>
                <button
                  type="button"
                  data-testid="chat-project-move"
                  disabled={moving || target === (projectId ?? "")}
                  onClick={() => void move()}
                  className="btn"
                >
                  Move
                </button>
              </div>
              <p id={`${uid}-project-help`} className="help">
                The next answer is given the notes of where the chat is: a project&apos;s, and your
                general notes unless the project leaves them out.
              </p>
              {moveProblem && (
                <p role="alert" data-testid="chat-project-problem" className="notice notice--error">
                  <CircleAlert size={16} aria-hidden />
                  {moveProblem}
                </p>
              )}
              {moved && (
                <p role="status" className="flex items-center gap-1.5 text-sm text-ok">
                  <Check size={14} aria-hidden />
                  Moved.
                </p>
              )}
            </fieldset>
          )}
          <div className="subsection">
            <ToolSelection
              selected={toolServers}
              onChange={(ids) => {
                setToolServers(ids);
                setSaved(false);
              }}
            />
          </div>
          <div className="subsection">
            <FolderSelection
              selected={folderGrants}
              onChange={(ids) => {
                setFolderGrants(ids);
                setSaved(false);
              }}
            />
          </div>
        </div>
      </div>
      <div className="sheet-foot">
        <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-end gap-3">
          {problem && (
            <p id={alertId} role="alert" className="notice notice--error mr-auto">
              <CircleAlert size={16} aria-hidden />
              {problem.message}
            </p>
          )}
          {saved && (
            <p role="status" className="mr-auto flex items-center gap-1.5 text-sm text-ok">
              <Check size={14} aria-hidden />
              Saved. The next answer uses these.
            </p>
          )}
          <button type="button" onClick={onClose} className="btn btn-quiet">
            Close settings
          </button>
          <button type="button" onClick={() => void save()} className="btn btn-primary">
            Save settings
          </button>
        </div>
      </div>
    </section>
  );
}
