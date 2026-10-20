/**
 * MV3b: what models keep by themselves, and the write policy that decides what they may.
 *
 * - `WritePolicy`: the policy in effect, whose word it is, what it allows beyond Standard or
 *   holds back (settings never lie), and the stricter presets a person may choose.
 * - `RecentlyLearned`: notes models kept in the last 30 days, each with its model and Undo,
 *   and "remove everything a model kept since a date". Showing a note marks it seen.
 * - `RememberReceipt`, `LearnedReceipts`: "Remembered … · Undo" in a chat.
 */

import { useEffect, useId, useRef, useState } from "react";
import { api, post } from "../lib/api";
import { failureWords, noteDate, plural } from "../lib/memory";
import type { LearnedNote, MemoryNote, MemoryPrefs, ToolCall, WritePolicyName } from "../lib/types";

const POLICY_LABELS: Record<WritePolicyName | "custom", string> = {
  strict: "Strict",
  standard: "Standard",
  open: "Open",
  custom: "Custom",
};

const POLICY_HELP: Record<WritePolicyName, string> = {
  strict:
    "Notes only from your own words. No model keeps notes by itself, models never suggest instructions, and a note drawn while web pages, tool results, documents or pasted text are in the chat is dropped.",
  standard:
    "Notes only from your own words. A model you set to Keep automatically may keep notes; with web pages, tool results, documents or pasted text in the chat, they go to Suggested notes instead. Notes a model kept stay on your machines until you have seen them.",
  open: "Models may keep notes drawn from anything in a chat, answers and pasted text included. For testing models.",
};

export function WritePolicy({
  prefs,
  busy,
  onChoose,
}: {
  prefs: MemoryPrefs;
  busy: boolean;
  onChoose: (policy: WritePolicyName | null) => void;
}) {
  const uid = useId();
  const policy = prefs.effective.policy;
  if (!policy) return null;
  const choices = prefs.policyChoices ?? [];
  return (
    <fieldset className="flex min-w-0 flex-col gap-2" data-testid="memory-write-policy">
      <legend className="field-label mb-1">What models may write to your memory</legend>
      <p className="help" data-testid="memory-policy-effective">
        In effect: <strong>{POLICY_LABELS[policy.name]}</strong>.{" "}
        {policy.setBy === "owner"
          ? "The owner of this Workbench set this."
          : `You chose this; the owner's is ${POLICY_LABELS[policy.owners]}.`}
      </p>
      {[...policy.allows, ...policy.holdsBack].length > 0 && (
        <ul className="help list-disc ps-5" data-testid="memory-policy-differs">
          {[...policy.allows, ...policy.holdsBack].map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      <label className="flex flex-col gap-1" htmlFor={`${uid}-policy`}>
        <span className="help">
          {prefs.policyPeople === "any"
            ? "You may choose any policy for your own memory."
            : "You may choose a stricter policy for your own memory."}
        </span>
        <select
          id={`${uid}-policy`}
          data-testid="memory-policy-choice"
          value={prefs.writePolicy ?? ""}
          disabled={busy}
          onChange={(event) =>
            onChoose(event.target.value === "" ? null : (event.target.value as WritePolicyName))
          }
        >
          <option value="">The owner's ({POLICY_LABELS[policy.owners]})</option>
          {choices.map((name) => (
            <option key={name} value={name}>
              {POLICY_LABELS[name]}
            </option>
          ))}
        </select>
      </label>
      {prefs.writePolicy && <p className="help">{POLICY_HELP[prefs.writePolicy]}</p>}
    </fieldset>
  );
}

function sinceDefault(): string {
  const day = new Date(Date.now() - 7 * 86_400_000);
  return day.toISOString().slice(0, 10);
}

export function RecentlyLearned({
  notes,
  onChanged,
}: {
  notes: MemoryNote[];
  onChanged: () => void;
}) {
  const uid = useId();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [since, setSince] = useState(sinceDefault);
  const [asking, setAsking] = useState<number | null>(null);
  const marked = useRef(new Set<string>());

  // Shown here is seen (call 12): a note a model kept may then go where your notes go.
  useEffect(() => {
    const unseen = notes.filter((n) => !n.seen && !marked.current.has(n.id)).map((n) => n.id);
    if (!unseen.length) return;
    for (const id of unseen) marked.current.add(id);
    void post("/api/memory/learned/seen", { ids: unseen }).catch(() => {
      for (const id of unseen) marked.current.delete(id);
    });
  }, [notes]);

  async function undo(note: MemoryNote) {
    setBusy(true);
    setProblem(null);
    try {
      const done = await post<{ restored: string[] }>(
        `/api/memory/learned/${encodeURIComponent(note.id)}/undo`,
      );
      setSaid(
        done.restored.length
          ? `Undone: "${note.text}". The note it replaced is back.`
          : `Undone: "${note.text}".`,
      );
      onChanged();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    setBusy(true);
    setProblem(null);
    try {
      const query = new URLSearchParams({ since, ...(model ? { model } : {}) });
      const found = await api<{ count: number }>(`/api/memory/learned/removal?${query}`);
      setAsking(found.count);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setProblem(null);
    try {
      const done = await post<{ count: number }>("/api/memory/learned/removal", {
        since,
        model: model || null,
      });
      setAsking(null);
      setSaid(`Removed ${plural(done.count, "note")}.`);
      onChanged();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const models = [...new Set(notes.map((n) => n.model).filter((m): m is string => !!m))];
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="memory-learned" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        Recently learned
      </h2>
      <p className="help">
        Notes a model kept by itself in the last 30 days. Undo takes one back; the note it replaced,
        if any, is current again.
      </p>
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      {said && (
        <p role="status" className="notice">
          {said}
        </p>
      )}
      {notes.length === 0 ? (
        <p className="help" data-testid="memory-learned-none">
          No model has kept a note by itself lately.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
          {notes.map((n) => (
            <li key={n.id} data-testid="memory-learned-note" className="flex flex-col gap-1 p-3">
              <p className="wrap-anywhere">{n.text}</p>
              <p className="help">
                Kept by {n.model ?? "a model"} on {noteDate(n.createdAt)}
                {n.policy
                  ? `, under the ${POLICY_LABELS[n.policy as WritePolicyName] ?? n.policy} policy`
                  : ""}
                .{!n.seen && !marked.current.has(n.id) && " New."}
              </p>
              <button
                type="button"
                data-testid="memory-learned-undo"
                className="btn btn-quiet -ms-2 self-start"
                disabled={busy}
                onClick={() => void undo(n)}
              >
                Undo
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2" data-testid="memory-learned-removal">
        <label className="flex flex-col gap-1">
          <span className="help">Remove everything kept by</span>
          <select value={model} disabled={busy} onChange={(e) => setModel(e.target.value)}>
            <option value="">any model</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="help">since</span>
          <input
            type="date"
            value={since}
            disabled={busy}
            onChange={(e) => setSince(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn"
          disabled={busy || !since}
          onClick={() => void preview()}
        >
          Remove…
        </button>
      </div>
      {asking !== null && (
        <div
          role="group"
          aria-label="Remove these notes?"
          className="flex flex-wrap items-center gap-2"
        >
          <span className="help" data-testid="memory-learned-removal-count">
            {asking === 0
              ? "Nothing to remove."
              : `This removes ${plural(asking, "note")} ${model || "any model"} kept since ${since}.`}
          </span>
          <button type="button" className="btn" disabled={busy} onClick={() => setAsking(null)}>
            Keep them
          </button>
          {asking > 0 && (
            <button
              type="button"
              className="btn btn-danger"
              data-testid="memory-learned-removal-confirm"
              disabled={busy}
              onClick={() => void remove()}
            >
              Remove {plural(asking, "note")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

/** A `remember` call in a chat: what it kept or offered, with Undo. */
export function RememberReceipt({ call, readOnly }: { call: ToolCall; readOnly: boolean }) {
  const [undone, setUndone] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (call.removed || undone) {
    return (
      <p className="text-sm text-muted" data-testid="memory-receipt">
        You removed this note.
      </p>
    );
  }
  if (!call.note) {
    return call.status === "done" ? (
      <p className="text-sm text-muted" data-testid="memory-receipt">
        The model asked to keep a note; Workbench did not keep it.
      </p>
    ) : null;
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-sm" data-testid="memory-receipt">
      {call.kept ? (
        <span>
          Remembered: <strong className="wrap-anywhere">{call.noteText}</strong>
        </span>
      ) : (
        <span>
          Suggested for your memory: <strong className="wrap-anywhere">{call.noteText}</strong>.
          Keep it or turn it away in Memory.
        </span>
      )}
      {call.kept && !readOnly && (
        <button
          type="button"
          className="btn btn-quiet"
          data-testid="memory-receipt-undo"
          onClick={() => {
            setProblem(null);
            void post(`/api/memory/learned/${encodeURIComponent(call.note ?? "")}/undo`)
              .then(() => setUndone(true))
              .catch((failure: unknown) => setProblem(failureWords(failure)));
          }}
        >
          Undo
        </button>
      )}
      {problem && (
        <span role="alert" className="text-error">
          {problem}
        </span>
      )}
    </p>
  );
}

/** Notes this chat's model kept by itself after the chat went quiet. */
export function LearnedReceipts({ notes = [] }: { notes?: LearnedNote[] }) {
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const shown = notes.filter((n) => !gone.has(n.id));
  if (!shown.length && !problem) return null;
  return (
    <div className="flex flex-col gap-1 text-sm" data-testid="memory-learned-receipts">
      {shown.map((n) => (
        <p key={n.id} className="flex flex-wrap items-center gap-2" data-testid="memory-receipt">
          <span>
            Remembered: <strong className="wrap-anywhere">{n.text}</strong>
          </span>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              setProblem(null);
              void post(`/api/memory/learned/${encodeURIComponent(n.id)}/undo`)
                .then(() => setGone((old) => new Set([...old, n.id])))
                .catch((failure: unknown) => setProblem(failureWords(failure)));
            }}
          >
            Undo
          </button>
        </p>
      ))}
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
    </div>
  );
}
