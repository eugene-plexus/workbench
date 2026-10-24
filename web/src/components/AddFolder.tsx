/**
 * Add a folder to the person's documents: choose one of the folders the owner of this Workbench
 * gave them. The choices are names, never paths (a person never sees where a folder is). Its first
 * check starts at once, so for a folder that is big or has things in it that should not be read,
 * what to leave out can be written first, under a disclosure: the easy way is one choice and one
 * button.
 *
 * Where there is nothing to choose the card says why and what to do: no folder was given (the
 * owner gives folders to people in Toolbox · Tools, and says so; the owner is offered a button
 * that opens it), or every folder given is added. A refusal is said where it belongs: a pattern
 * that cannot be used, in the server's words, under the patterns; any other in a notice by the
 * button.
 */

import { CircleAlert, ChevronRight, FolderPlus, Wrench } from "lucide-react";
import { useId, useState } from "react";

import { post } from "../lib/api";
import { ADD_WORDS, nothingToAddWords } from "../lib/documentFolders";
import { failureWords } from "../lib/memory";
import type { DocumentFolder, FolderChoice, FolderLimits } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { type RulesDraft, RulesFields, rulesOf } from "./FolderRules";

const FRESH: RulesDraft = { text: "", includeHidden: false, useGitignore: true };

/** The HTTP status of a refusal, read off the failure: a test that replaces the module has no class
 * to ask. */
const statusOf = (failure: unknown): number | null => {
  const status = (failure as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : null;
};

export function AddFolder({
  choices,
  limits,
  owner,
  disabled,
  onAdded,
  onTools,
  projectId,
}: {
  choices: FolderChoice[];
  limits: FolderLimits;
  owner: boolean;
  /** The screen is busy with something else, or the owner has turned documents off. */
  disabled: boolean;
  /** The folder that was added, so the screen can bring it into view. */
  onAdded: (folder: DocumentFolder) => void;
  /** Opens Toolbox · Tools, where the owner gives folders to people. */
  onTools?: () => void;
  projectId?: string;
}) {
  const uid = useId();
  const open = choices.filter((choice) => !choice.added);
  const [chosen, setChosen] = useState("");
  const [draft, setDraft] = useState<RulesDraft>(FRESH);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [patternError, setPatternError] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const keepFocus = useKeepFocus(pending);
  // The first folder not added yet is chosen until the person chooses another.
  const grantId = open.some((choice) => choice.id === chosen) ? chosen : (open[0]?.id ?? "");

  async function add() {
    if (!grantId) return;
    keepFocus();
    setPending(true);
    setPatternError(null);
    setProblem(null);
    try {
      const made = await post<DocumentFolder>("/api/documents/folders", {
        grantId,
        ...(projectId ? { projectId } : {}),
        ...rulesOf(draft),
      });
      setDraft(FRESH);
      setRulesOpen(false);
      setChosen("");
      onAdded(made);
    } catch (failure) {
      // A pattern that cannot be used is said under the patterns, which are opened to show it.
      if (statusOf(failure) === 422) {
        setPatternError(failureWords(failure));
        setRulesOpen(true);
      } else {
        setProblem(failureWords(failure));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div data-testid="folders-add" className="card card--add">
      <h3 id={`${uid}-add`} className="title-card">
        Add a folder
      </h3>
      {open.length === 0 ? (
        <>
          <p data-testid="folders-add-none" className="help">
            {nothingToAddWords(owner, choices.length > 0)}
          </p>
          {owner && onTools && (
            <div>
              <button
                type="button"
                data-testid="folders-open-tools"
                onClick={onTools}
                className="btn"
              >
                <Wrench size={14} aria-hidden />
                Open Toolbox · Tools
              </button>
            </div>
          )}
        </>
      ) : (
        <form
          aria-labelledby={`${uid}-add`}
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!pending && !disabled) void add();
          }}
        >
          <p id={`${uid}-help`} className="help">
            {ADD_WORDS}
          </p>
          <div className="field">
            <label htmlFor={`${uid}-folder`} className="field-label">
              Folder to add
            </label>
            <select
              id={`${uid}-folder`}
              data-testid="folders-add-select"
              value={grantId}
              disabled={pending || disabled}
              onChange={(event) => setChosen(event.target.value)}
              className="control"
            >
              {open.map((choice) => (
                <option key={choice.id} value={choice.id}>
                  {choice.name}
                </option>
              ))}
            </select>
          </div>
          <details
            data-testid="folders-add-rules"
            open={rulesOpen}
            onToggle={(event) => setRulesOpen(event.currentTarget.open)}
            className="group/rules text-sm"
          >
            <summary
              data-testid="folders-add-rules-toggle"
              className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
            >
              <ChevronRight
                size={14}
                aria-hidden
                className="shrink-0 transition-transform group-open/rules:rotate-90"
              />
              Choose what to leave out first
            </summary>
            <div className="mt-2">
              <RulesFields
                prefix="folders-add"
                draft={draft}
                limits={limits}
                disabled={pending || disabled}
                error={patternError}
                onChange={(next) => {
                  setDraft(next);
                  setPatternError(null);
                }}
              />
            </div>
          </details>
          {problem && (
            <p role="alert" data-testid="folders-add-problem" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
          <div>
            <button
              type="submit"
              data-testid="folders-add-go"
              aria-describedby={`${uid}-help`}
              disabled={pending || disabled}
              className="btn btn-primary"
            >
              <FolderPlus size={14} aria-hidden />
              Add folder
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
