/**
 * Models that learn (MV3a): what each chat model does with what a person writes (Off or
 * Suggest, and Keep automatically with the write policy), whose word that is, and Eugene's
 * recommendation with its reason, from a measurement Eugene ships or one run here with
 * Measure this model. The person decides; the recommendation is advice.
 */

import { Brain } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api, del, post, put } from "../lib/api";
import { failureWords } from "../lib/memory";

type Mode = "off" | "suggest" | "auto";

export interface LearningModel {
  id: string;
  locality: "local" | "external" | "unknown";
  provider: string | null;
  tools: boolean;
  listed: boolean;
  mode: Mode;
  by: "you" | "owner" | "measured" | "default" | "memory_off";
  chosen: Mode | null;
  defaultMode: Mode;
  recommendation: {
    level: "keep" | "suggest" | "no" | "unmeasured";
    text: string;
    measuredOn: string | null;
    by: "eugene" | "install" | null;
    /** A run here by someone other than the owner: advice, it sets no default. */
    advice?: boolean;
  };
  tagsInRequest: boolean;
  lastRun: { outcome: string; text: string } | null;
}

interface Run {
  model: string;
  done: number;
  total: number;
  startedAt: number;
  yours: boolean;
  outside: boolean;
}

export interface LearningModels {
  models: LearningModel[];
  listingError: string | null;
  allowed: boolean;
  measuring: Run | null;
  runsLeftToday: number;
  requests: number;
  /** MV3b: whether the write policy in effect lets a model keep notes by itself. */
  keepAllowed?: boolean;
  policy?: string;
}

const MODE_LABELS: Record<Mode, string> = {
  off: "Off",
  suggest: "Suggest",
  auto: "Keep automatically",
};

/** Whose word a model's mode is, after the mode. */
export function modeByWords(model: LearningModel): string {
  if (model.by === "owner") return "The owner of this Workbench has turned learning off.";
  if (model.by === "measured") return "Eugene's recommendation; you have not chosen.";
  if (model.by === "default") return "Nobody has chosen, so it is off.";
  if (model.by === "memory_off") return "You turned memory off.";
  return model.chosen ? "You chose this." : "Your choice for models you have not set.";
}

export function MemoryModels() {
  const uid = useId();
  const [data, setData] = useState<LearningModels | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<{ model: string; detail: string } | null>(null);
  const timer = useRef<number | null>(null);

  const [unread, setUnread] = useState<string | null>(null);

  async function load() {
    try {
      const page = await api<LearningModels>("/api/memory/models");
      // An answer without the list is not one this section can show.
      if (Array.isArray(page?.models)) {
        setData(page);
        setUnread(null);
      } else {
        setUnread("Could not load the models.");
      }
    } catch (failure) {
      setUnread(`Could not load the models: ${failureWords(failure)}`);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // While a model is measured, its progress is asked for every few seconds.
  const measuring = data?.measuring ?? null;
  useEffect(() => {
    if (!measuring) return;
    timer.current = window.setTimeout(() => void load(), 3000);
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [measuring]);

  async function choose(model: string, mode: Mode | null) {
    setBusy(true);
    setProblem(null);
    try {
      setData(
        await put<LearningModels>(`/api/memory/models/${encodeURIComponent(model)}`, { mode }),
      );
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function measure(model: string, confirmOutside = false) {
    setBusy(true);
    setProblem(null);
    try {
      await post("/api/memory/measurements", { model, confirmOutside });
      setConfirming(null);
      await load();
    } catch (failure) {
      if (
        failure instanceof ApiError &&
        failure.status === 409 &&
        failure.detail?.code === "confirm_outside"
      ) {
        setConfirming({ model, detail: failure.message });
      } else {
        setProblem(failureWords(failure));
      }
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    try {
      await del("/api/memory/measurements/current");
      await load();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    // Not an alert: nothing was asked of this section yet, and the page around it works.
    return unread ? (
      <p data-testid="memory-models-unread" className="notice">
        {unread}
      </p>
    ) : null;
  }
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="memory-models" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Brain size={18} aria-hidden />
        Models that learn
      </h2>
      <p className="help">
        Choose what each model may do with what you write: nothing, suggest notes for you to keep,
        or keep notes by itself, each with a receipt and Undo. Eugene says which models it
        recommends and why; the choice is yours.
      </p>
      {!data.allowed && (
        <p data-testid="memory-models-off" className="notice">
          The owner of this Workbench has turned learning off, so no model suggests or keeps notes.
        </p>
      )}
      {data.listingError && (
        <p className="notice">Eugene's list of models could not be read: {data.listingError}</p>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      {data.measuring && (
        <div data-testid="memory-measuring" className="notice flex flex-wrap items-center gap-2">
          <span>
            Measuring <strong>{data.measuring.model}</strong>: {data.measuring.done} of{" "}
            {data.measuring.total} requests.
          </span>
          {data.measuring.yours && (
            <button
              type="button"
              className="btn btn-quiet"
              disabled={busy}
              onClick={() => void cancel()}
            >
              Cancel
            </button>
          )}
        </div>
      )}
      <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
        {data.models.map((m) => (
          <li
            key={m.id}
            data-testid="memory-model"
            data-model={m.id}
            className="flex flex-col gap-1 p-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="wrap-anywhere">{m.id}</strong>
              <label className="flex items-center gap-2">
                <span className="sr-only">What {m.id} may do</span>
                <select
                  data-testid="memory-model-mode"
                  value={m.chosen ?? ""}
                  disabled={busy || !data.allowed}
                  onChange={(event) => {
                    const value = event.target.value;
                    void choose(m.id, value === "" ? null : (value as Mode));
                  }}
                >
                  <option value="">Default ({MODE_LABELS[m.defaultMode]})</option>
                  <option value="off">{MODE_LABELS.off}</option>
                  <option value="suggest">{MODE_LABELS.suggest}</option>
                  <option value="auto" disabled={!data.keepAllowed && m.chosen !== "auto"}>
                    {MODE_LABELS.auto}
                    {data.keepAllowed ? "" : " (not under your write policy)"}
                  </option>
                </select>
              </label>
            </div>
            <p className="help" data-testid="memory-model-effective">
              In effect: <strong>{MODE_LABELS[m.mode]}</strong>. {modeByWords(m)}
            </p>
            <p
              className="help"
              data-testid="memory-model-recommendation"
              data-level={m.recommendation.level}
            >
              {m.recommendation.text}
              {m.recommendation.by === "install" &&
                (m.recommendation.advice
                  ? " Measured on this Workbench by someone other than its owner, so it is advice only."
                  : " Measured on this Workbench.")}
              {m.tagsInRequest && " Your tag list is sent with its suggestions."}
            </p>
            {m.lastRun && <p className="help">{m.lastRun.text}</p>}
            {m.listed &&
              (confirming?.model === m.id ? (
                <div role="group" aria-label={`Measure ${m.id}?`} className="flex flex-wrap gap-2">
                  <span className="help">{confirming.detail}</span>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => setConfirming(null)}
                  >
                    Don't measure
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    data-testid="memory-model-measure-confirm"
                    disabled={busy}
                    onClick={() => void measure(m.id, true)}
                  >
                    Measure it
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  data-testid="memory-model-measure"
                  className="btn btn-quiet -ms-2 self-start"
                  disabled={
                    busy || !data.allowed || data.measuring !== null || data.runsLeftToday <= 0
                  }
                  onClick={() => void measure(m.id)}
                >
                  Measure this model
                </button>
              ))}
          </li>
        ))}
      </ul>
      <p className="help">
        Measuring sends {data.requests} made-up messages to the model, one at a time, and takes a
        few minutes on a model on your machines. You can measure {data.runsLeftToday} more today.
      </p>
    </section>
  );
}
