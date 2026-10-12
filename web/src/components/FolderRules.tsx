/**
 * What a folder leaves out, as the person writes it: an ignore list (a pattern on each line,
 * written like a .gitignore file), whether names that start with a dot are read, and whether the
 * folder's own .gitignore files are respected. What is always left out whatever is set (the
 * folders programs keep their bookkeeping in, a few files an operating system makes, temporary
 * files) is listed, from the server's own list.
 *
 * A change is saved all at once, and the folder is checked again at once, so the words say what
 * that does before it is done. A pattern the server cannot use is refused with its reason, and
 * the reason is said where the pattern is typed, in the server's words. Nothing is judged here:
 * the server is the authority on what a pattern may be.
 */

import { ChevronRight, CircleAlert, CircleCheck } from "lucide-react";
import { useId, useState } from "react";

import { patch } from "../lib/api";
import {
  ignoreText,
  parseIgnore,
  patternsWords,
  RULES_EFFECT,
  rulesChanged,
} from "../lib/documentFolders";
import { failureWords } from "../lib/memory";
import type { DocumentFolder, FolderLimits } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";

/** The rules as they are typed: the list is the text of the box, which is cut into patterns when
 * it is sent. */
export interface RulesDraft {
  text: string;
  includeHidden: boolean;
  useGitignore: boolean;
}

export const draftOf = (
  folder: Pick<DocumentFolder, "ignore" | "includeHidden" | "useGitignore">,
): RulesDraft => ({
  text: ignoreText(folder.ignore),
  includeHidden: folder.includeHidden,
  useGitignore: folder.useGitignore,
});

/** The rules to send for what was typed. */
export const rulesOf = (draft: RulesDraft) => ({
  ignore: parseIgnore(draft.text),
  includeHidden: draft.includeHidden,
  useGitignore: draft.useGitignore,
});

/** The fields: the list, the two switches, and what is always left out. `prefix` names each
 * control's test id, so the same fields serve a folder that is added and one that is changed. */
export function RulesFields({
  prefix,
  draft,
  limits,
  disabled,
  error,
  onChange,
}: {
  prefix: string;
  draft: RulesDraft;
  limits: FolderLimits;
  disabled: boolean;
  /** The server's reason for refusing a pattern, said where the patterns are typed. */
  error: string | null;
  onChange: (next: RulesDraft) => void;
}) {
  const uid = useId();
  const typed = parseIgnore(draft.text).length;
  const { rules } = limits;
  return (
    <div className="flex flex-col gap-3">
      <div className="field">
        <label htmlFor={`${uid}-ignore`} className="field-label">
          Leave out <small>(one pattern on each line)</small>
        </label>
        <textarea
          id={`${uid}-ignore`}
          data-testid={`${prefix}-ignore`}
          value={draft.text}
          rows={4}
          spellCheck={false}
          autoComplete="off"
          autoCapitalize="off"
          dir="auto"
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={`${uid}-help ${uid}-count${error ? ` ${uid}-error` : ""}`}
          onChange={(event) => onChange({ ...draft, text: event.target.value })}
          className="control font-mono"
        />
        <p id={`${uid}-help`} className="help">
          Written like a .gitignore file. <span className="font-mono">*.log</span> leaves out every
          .log file, <span className="font-mono">build/</span> a folder, and{" "}
          <span className="font-mono">/notes/old.md</span> one file. A pattern cannot start with !
          or #.
        </p>
        <p
          id={`${uid}-count`}
          data-testid={`${prefix}-count`}
          className="text-xs tabular-nums text-muted"
        >
          {patternsWords(typed, limits.maxIgnore)}
        </p>
        {error && (
          <p
            id={`${uid}-error`}
            role="alert"
            data-testid={`${prefix}-error`}
            className="notice notice--error"
          >
            <CircleAlert size={16} aria-hidden />
            {error}
          </p>
        )}
      </div>
      <div>
        <label className="check" htmlFor={`${uid}-hidden`}>
          <input
            id={`${uid}-hidden`}
            type="checkbox"
            data-testid={`${prefix}-hidden`}
            aria-describedby={`${uid}-hidden-help`}
            checked={draft.includeHidden}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, includeHidden: event.target.checked })}
          />
          Read hidden files and folders
        </label>
        <p id={`${uid}-hidden-help`} className="help ps-[1.625rem]">
          {rules.hidden} They are left out unless you turn this on.
        </p>
      </div>
      <div>
        <label className="check" htmlFor={`${uid}-gitignore`}>
          <input
            id={`${uid}-gitignore`}
            type="checkbox"
            data-testid={`${prefix}-gitignore`}
            aria-describedby={`${uid}-gitignore-help`}
            checked={draft.useGitignore}
            disabled={disabled}
            onChange={(event) => onChange({ ...draft, useGitignore: event.target.checked })}
          />
          Leave out what the folder&apos;s .gitignore files list
        </label>
        <p id={`${uid}-gitignore-help`} className="help ps-[1.625rem]">
          {rules.gitignore} A project&apos;s build output is not what you mean to search, so this is
          on unless you turn it off.
        </p>
      </div>
      <details data-testid={`${prefix}-always`} className="group/always text-sm">
        <summary
          data-testid={`${prefix}-always-toggle`}
          className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
        >
          <ChevronRight
            size={14}
            aria-hidden
            className="shrink-0 transition-transform group-open/always:rotate-90"
          />
          What is always left out
        </summary>
        <div className="mt-2 flex flex-col gap-2 text-xs text-muted">
          <p className="wrap-anywhere">
            Folders: <span className="font-mono">{rules.folders.join(", ")}</span>
          </p>
          <p className="wrap-anywhere">
            Files: <span className="font-mono">{rules.files.join(", ")}</span>
          </p>
          <p className="wrap-anywhere">
            Temporary files: <span className="font-mono">{rules.temporary.join(", ")}</span>
          </p>
          <p>These stay left out even when hidden files are read.</p>
        </div>
      </details>
    </div>
  );
}

/** The editor on a folder's card: what it leaves out now, to change and save. */
export function FolderRules({
  folder,
  limits,
  onSaved,
}: {
  folder: DocumentFolder;
  limits: FolderLimits;
  onSaved: () => void;
}) {
  const uid = useId();
  const [draft, setDraft] = useState<RulesDraft>(() => draftOf(folder));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const keepFocus = useKeepFocus(pending);
  // The rules in effect are always what is shown: when the folder's own change under the editor (a
  // file left out from the list, or another window), the editor takes them up.
  const inEffect = JSON.stringify([folder.ignore, folder.includeHidden, folder.useGitignore]);
  const [seen, setSeen] = useState(inEffect);
  if (seen !== inEffect) {
    setSeen(inEffect);
    setDraft(draftOf(folder));
  }
  const rules = rulesOf(draft);
  const changed = rulesChanged(folder, rules);

  async function save() {
    keepFocus();
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const made = await patch<DocumentFolder>(
        `/api/documents/folders/${encodeURIComponent(folder.id)}`,
        rules,
      );
      setDraft(draftOf(made));
      setSaved(true);
      onSaved();
    } catch (failure) {
      setError(failureWords(failure));
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      id={`${uid}-rules`}
      role="group"
      aria-label={`What ${folder.name} leaves out`}
      data-testid="folder-rules"
      className="flex flex-col gap-3 rounded-plexus border border-line p-3"
    >
      <RulesFields
        prefix="folder-rules"
        draft={draft}
        limits={limits}
        disabled={pending}
        error={error}
        onChange={(next) => {
          setDraft(next);
          setSaved(false);
          setError(null);
        }}
      />
      <div className="flex flex-col items-start gap-2">
        <p className="help">{RULES_EFFECT}</p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="folder-rules-save"
            aria-label={`Save what ${folder.name} leaves out`}
            disabled={pending || !changed}
            onClick={() => void save()}
            className="btn btn-primary"
          >
            Save
          </button>
          <button
            type="button"
            data-testid="folder-rules-revert"
            aria-label={`Put back what ${folder.name} leaves out`}
            disabled={pending || !changed}
            onClick={() => {
              setDraft(draftOf(folder));
              setError(null);
            }}
            className="btn btn-quiet"
          >
            Put back
          </button>
        </div>
        {saved && (
          <p role="status" data-testid="folder-rules-saved" className="notice notice--ok">
            <CircleCheck size={16} aria-hidden />
            Saved. Workbench is checking the folder again.
          </p>
        )}
      </div>
    </div>
  );
}
