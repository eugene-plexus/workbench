/**
 * Memory's settings, on the Settings page (MV4: app before config). The notes, Suggested notes,
 * Recently learned and the tags are the Memory page's; what a model is given, who may write and
 * how much room notes take are set here. Each control shows the value in effect and whose word it
 * is (the person's, the owner's, or nobody's yet), as it did on the Memory page; a setting does
 * exactly what it did there. Where the owner has turned Memory off, the section says so and holds
 * no control.
 */

import { Brain, ChevronRight, CircleAlert, Info } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";

import { api, put } from "../lib/api";
import {
  BUDGET_MAX,
  EXTERNAL_CHOICES,
  externalLabel,
  failureWords,
  OWNER_OFF,
  setByWords,
} from "../lib/memory";
import type { ExternalChoice, MemoryPage, WritePolicyName } from "../lib/types";
import { useKeepFocus } from "../lib/useKeepFocus";
import { MemoryModels } from "./MemoryModels";
import { MemoryServer } from "./MemoryServer";
import { WritePolicy } from "./MemoryLearned";
import { LearnSettings } from "./MemorySuggestions";

/** What one change to the person's memory settings sends (`PUT /api/memory/prefs`). */
type PrefsBody = {
  enabled?: boolean;
  external?: ExternalChoice | null;
  budgetTokens?: number | null;
  relevantBudgetTokens?: number | null;
  searchTool?: boolean;
  chatsTool?: boolean;
  learn?: "off" | "suggest" | null;
  useMode?: "relevant" | "all";
  writePolicy?: WritePolicyName | null;
};

export function MemorySettings({ onOpenMemory }: { onOpenMemory: () => void }) {
  const uid = useId();
  const [data, setData] = useState<MemoryPage | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // A held control gives the keyboard back to the one that was pressed once the change is done.
  const keepFocus = useKeepFocus(busy);
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

  /** A change: the section is busy while it runs and reads again after it, whether it worked or
   * not, and a refusal is said in the server's words. */
  async function change(body: PrefsBody) {
    setProblem(null);
    keepFocus();
    setBusy(true);
    try {
      await put("/api/memory/prefs", body);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      await load();
      setBusy(false);
    }
  }

  const off = data !== null && !data.installEnabled;
  return (
    <>
      <section
        id="memory"
        aria-labelledby={`${uid}-memory`}
        data-testid="settings-memory"
        className="section scroll-mt-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <h2 id={`${uid}-memory`} className="title-section flex-1">
            <Brain size={18} aria-hidden />
            Memory
          </h2>
          <button
            type="button"
            data-testid="settings-open-memory"
            className="btn btn-quiet"
            onClick={onOpenMemory}
          >
            Open Memory
          </button>
        </div>
        {loadProblem && (
          <div role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <div>
              <span className="notice-title">Could not read your memory settings</span>
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
        {data === null && !loadProblem && (
          <p role="status" className="text-sm text-muted">
            Loading your memory settings…
          </p>
        )}
        {off && (
          <p data-testid="memory-owner-off" className="notice">
            <Info size={16} aria-hidden />
            <span>
              <span className="notice-title">{OWNER_OFF}</span>
              No notes are sent to any model, so there is nothing to set here.
            </span>
          </p>
        )}
        {problem && (
          <p role="alert" data-testid="memory-problem" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem}
          </p>
        )}
        {data && !off && (
          <MemoryControls data={data} busy={busy} onPrefs={(body) => void change(body)} />
        )}
      </section>
      {data && !off && <MemoryModels />}
      {data && !off && <MemoryServer />}
    </>
  );
}

function MemoryControls({
  data,
  busy,
  onPrefs,
}: {
  data: MemoryPage;
  busy: boolean;
  onPrefs: (body: PrefsBody) => void;
}) {
  const uid = useId();
  const { prefs, limits } = data;
  const { effective } = prefs;
  const ownerSet = effective.externalSetBy === "owner";
  // What was just chosen shows while the server catches up, and a refusal puts
  // the old value back: these are only read while the page is busy.
  const [wanted, setWanted] = useState<{ enabled?: boolean; external?: ExternalChoice }>({});
  const [wantedSearch, setWantedSearch] = useState<boolean | null>(null);
  const [wantedChats, setWantedChats] = useState<boolean | null>(null);
  const enabled = busy && wanted.enabled !== undefined ? wanted.enabled : effective.enabled;
  const external = busy && wanted.external !== undefined ? wanted.external : effective.external;
  return (
    <>
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-use`}>
          <input
            id={`${uid}-use`}
            type="checkbox"
            role="switch"
            data-testid="memory-use-switch"
            aria-describedby={`${uid}-use-help`}
            checked={enabled}
            disabled={busy}
            onChange={(event) => {
              setWanted({ enabled: event.target.checked });
              onPrefs({ enabled: event.target.checked });
            }}
          />
          Use memory
        </label>
        <p id={`${uid}-use-help`} data-testid="memory-use-effective" className="help">
          {effective.enabled
            ? "Memory is on: models in your chats are given your notes. Turn it off for one chat from its Memory button."
            : "You turned memory off. No model is given your notes, and Remember that … keeps nothing. Your notes stay saved here."}
        </p>
      </div>
      <fieldset className="flex min-w-0 flex-col gap-2" aria-describedby={`${uid}-outside-help`}>
        <legend className="field-label mb-1">Models that run outside your machines</legend>
        <p id={`${uid}-outside-help`} className="help">
          Some models run on another company&apos;s computers. Your notes would leave your machines
          to reach them.
        </p>
        {EXTERNAL_CHOICES.map((choice) => (
          <div key={choice.value}>
            <label className="check">
              <input
                type="radio"
                name={`${uid}-external`}
                value={choice.value}
                data-testid={`memory-external-${choice.value}`}
                aria-describedby={`${uid}-external-${choice.value}`}
                checked={external === choice.value}
                disabled={busy || ownerSet}
                onChange={() => {
                  setWanted({ external: choice.value });
                  onPrefs({ external: choice.value });
                }}
              />
              <span>{choice.label}</span>
            </label>
            <p id={`${uid}-external-${choice.value}`} className="help ps-[1.625rem]">
              {choice.help}
            </p>
          </div>
        ))}
        <p data-testid="memory-external-effective" className="help">
          In effect: <strong>{externalLabel(effective.external)}</strong>.{" "}
          {setByWords(effective.externalSetBy)}
        </p>
        {prefs.external !== null && !ownerSet && (
          <button
            type="button"
            data-testid="memory-external-default"
            disabled={busy}
            onClick={() => onPrefs({ external: null })}
            className="btn btn-quiet -ms-2 self-start"
          >
            Use the default
          </button>
        )}
      </fieldset>
      <LearnSettings data={data} busy={busy} onPrefs={onPrefs} />
      <WritePolicy
        prefs={data.prefs}
        busy={busy}
        onChoose={(writePolicy) => onPrefs({ writePolicy })}
      />
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-search`}>
          <input
            id={`${uid}-search`}
            type="checkbox"
            role="switch"
            data-testid="memory-search-switch"
            aria-describedby={`${uid}-search-help`}
            checked={busy && wantedSearch !== null ? wantedSearch : prefs.searchTool}
            disabled={busy}
            onChange={(event) => {
              setWantedSearch(event.target.checked);
              onPrefs({ searchTool: event.target.checked });
            }}
          />
          Let models search my notes
        </label>
        <p id={`${uid}-search-help`} data-testid="memory-search-effective" className="help">
          {prefs.searchTool
            ? "A model that can use tools may look up notes it was not given. It only reads your notes and can change nothing."
            : "Models are only given the notes Workbench chooses for them."}
        </p>
      </div>
      <div className="field">
        <label className="check items-center" htmlFor={`${uid}-chats`}>
          <input
            id={`${uid}-chats`}
            type="checkbox"
            role="switch"
            data-testid="memory-chats-switch"
            aria-describedby={`${uid}-chats-help`}
            checked={busy && wantedChats !== null ? wantedChats : prefs.chatsTool !== false}
            disabled={busy}
            onChange={(event) => {
              setWantedChats(event.target.checked);
              onPrefs({ chatsTool: event.target.checked });
            }}
          />
          Let models search my past chats
        </label>
        <p id={`${uid}-chats-help`} data-testid="memory-chats-effective" className="help">
          {prefs.chatsTool !== false
            ? "A model that can use tools may look up what your other chats said. It only reads them and can change nothing."
            : "Models are not given your other chats, unless you turn on Use past chats in a chat."}
        </p>
      </div>
      <details className="group/b text-sm" data-testid="memory-budget-details">
        <summary
          data-testid="memory-budget-summary"
          className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
        >
          <ChevronRight
            size={14}
            aria-hidden
            className="transition-transform group-open/b:rotate-90"
          />
          How much room notes may take
        </summary>
        <BudgetField
          key={`pinned-${String(prefs.budgetTokens)}`}
          kind="pinned"
          label="Pinned notes: most tokens in every chat"
          help={`A chat's pinned notes and instructions take at most this many tokens, and never more than ${limits.budgetSharePercent}% of its model's window.`}
          value={prefs.budgetTokens}
          effective={prefs.effective.budgetTokens}
          ceiling={limits.budgetCeiling}
          busy={busy}
          floor={limits.budgetFloor}
          onSave={(budgetTokens) => onPrefs({ budgetTokens })}
        />
        <BudgetField
          key={`relevant-${String(prefs.relevantBudgetTokens)}`}
          kind="relevant"
          label="Relevant notes: most tokens a turn"
          help={`The notes chosen for each message take at most this many tokens, and never more than ${limits.relevantSharePercent}% of its model's window.`}
          value={prefs.relevantBudgetTokens}
          effective={prefs.effective.relevantBudgetTokens}
          ceiling={limits.relevantCeiling}
          busy={busy}
          floor={limits.budgetFloor}
          onSave={(relevantBudgetTokens) => onPrefs({ relevantBudgetTokens })}
        />
      </details>
    </>
  );
}

function BudgetField({
  kind,
  label,
  help,
  value,
  effective,
  ceiling,
  busy,
  floor,
  onSave,
}: {
  /** `pinned` keeps the ids the page has always had; `relevant` is the second field. */
  kind: "pinned" | "relevant";
  label: string;
  help: string;
  value: number | null;
  effective: number;
  ceiling: number;
  busy: boolean;
  floor: number;
  onSave: (tokens: number | null) => void;
}) {
  const uid = useId();
  const [text, setText] = useState(value === null ? "" : String(value));
  const [problem, setProblem] = useState<string | null>(null);
  const id = kind === "pinned" ? "budget" : "relevant-budget";
  function submit(event: FormEvent) {
    event.preventDefault();
    const tokens = Number(text.trim());
    if (!Number.isInteger(tokens) || tokens < floor || tokens > BUDGET_MAX) {
      setProblem(
        `Enter a whole number from ${floor.toLocaleString()} to ${BUDGET_MAX.toLocaleString()}, or use the default.`,
      );
      return;
    }
    setProblem(null);
    onSave(tokens);
  }
  return (
    <div className="mt-2 flex flex-col gap-3">
      <p className="help">{help}</p>
      <p data-testid={`memory-${id}-effective`} className="help">
        In effect: <strong>{effective.toLocaleString()} tokens</strong>.{" "}
        {value === null ? setByWords("default") : setByWords("you")}
      </p>
      <form className="flex flex-wrap items-end gap-2" onSubmit={submit}>
        <div className="field">
          <label htmlFor={`${uid}-${id}`} className="field-label">
            {label}
          </label>
          <input
            id={`${uid}-${id}`}
            data-testid={`memory-${id}-input`}
            inputMode="numeric"
            value={text}
            disabled={busy}
            placeholder={`${ceiling.toLocaleString()} (the default)`}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${uid}-${id}-problem` : undefined}
            onChange={(event) => setText(event.target.value)}
            className="control w-44"
          />
        </div>
        <button type="submit" data-testid={`memory-${id}-save`} disabled={busy} className="btn">
          Save
        </button>
        {value !== null && (
          <button
            type="button"
            data-testid={`memory-${id}-default`}
            disabled={busy}
            onClick={() => onSave(null)}
            className="btn btn-quiet"
          >
            Use the default
          </button>
        )}
      </form>
      {problem && (
        <p id={`${uid}-${id}-problem`} role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
    </div>
  );
}
