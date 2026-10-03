/**
 * A chat's settings. An unset value is the model's own default and says
 * so -- never a number that looks like a choice (Troy's rule: settings
 * never lie). Clearing a box returns it to the model's default.
 */

import { useState } from "react";

import type { ChatSettings as Settings } from "../lib/types";
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
    hint: "Samples only from the likeliest words that add up to this share.",
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

export function ChatSettings({
  settings,
  onSave,
  onClose,
}: {
  settings: Settings;
  onSave: (next: Settings) => Promise<void>;
  onClose: () => void;
}) {
  const [instructions, setInstructions] = useState(settings.instructions ?? "");
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      FIELDS.map((f) => [f.key, settings[f.key] != null ? String(settings[f.key]) : ""]),
    ),
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [toolServers, setToolServers] = useState(settings.toolServers ?? []);
  const [folderGrants, setFolderGrants] = useState(settings.folderGrants ?? []);

  async function save() {
    const next: Settings = {
      instructions: instructions.trim() ? instructions : null,
      toolServers,
      folderGrants,
    };
    for (const field of FIELDS) {
      const raw = values[field.key]?.trim() ?? "";
      if (!raw) {
        next[field.key] = null;
        continue;
      }
      const number = Number(raw);
      if (!Number.isFinite(number) || number < field.min || number > field.max) {
        setProblem(
          `${field.label} must be between ${field.min} and ${field.max}, or empty for the model's default.`,
        );
        return;
      }
      next[field.key] = field.key === "maxTokens" ? Math.round(number) : number;
    }
    try {
      await onSave(next);
      setProblem(null);
      setSaved(true);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <section
      aria-label="This chat's settings"
      className="max-h-[60vh] shrink-0 overflow-y-auto border-b border-line bg-panel px-4 py-3"
      data-testid="chat-settings"
    >
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium">Instructions</span>
          <span className="text-muted">Told to the model before every message in this chat.</span>
          <textarea
            value={instructions}
            onChange={(e) => {
              setInstructions(e.target.value);
              setSaved(false);
            }}
            rows={3}
            placeholder="None"
            className="rounded-plexus border border-line bg-soft p-2"
          />
        </label>
        <div className="grid gap-3 sm:grid-cols-3">
          {FIELDS.map((field) => (
            <label key={field.key} className="flex flex-col gap-1 text-sm" title={field.hint}>
              <span className="font-medium">{field.label}</span>
              <input
                inputMode="decimal"
                value={values[field.key]}
                onChange={(e) => {
                  setValues((v) => ({ ...v, [field.key]: e.target.value }));
                  setSaved(false);
                }}
                placeholder="The model's default"
                className="rounded-plexus border border-line bg-soft px-2 py-1"
              />
            </label>
          ))}
        </div>
        <ToolSelection
          selected={toolServers}
          onChange={(ids) => {
            setToolServers(ids);
            setSaved(false);
          }}
        />
        <FolderSelection
          selected={folderGrants}
          onChange={(ids) => {
            setFolderGrants(ids);
            setSaved(false);
          }}
        />
        {problem && (
          <p role="alert" className="text-sm text-error">
            {problem}
          </p>
        )}
        <div className="flex items-center justify-end gap-3 text-sm">
          {saved && <span className="text-muted">Saved. The next answer uses these.</span>}
          <button type="button" onClick={onClose} className="px-3 py-1">
            Close
          </button>
          <button
            type="button"
            onClick={() => void save()}
            className="rounded-plexus bg-accent px-3 py-1 text-on-accent"
          >
            Save
          </button>
        </div>
      </div>
    </section>
  );
}
