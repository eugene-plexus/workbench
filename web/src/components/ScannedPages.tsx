import { useEffect, useId, useState } from "react";

import { api, post, put } from "../lib/api";
import { failureWords } from "../lib/memory";

interface PageSettings {
  enabled: boolean;
  outside: boolean;
  model: string | null;
  maxPages: number;
  wholeByParts: string;
  message: string;
  pages?: number;
}

export function ScannedPages({
  scope = "library",
  onChanged,
  onEnabled,
}: {
  scope?: string;
  onChanged?: () => void;
  onEnabled?: (enabled: boolean) => void;
}) {
  const uid = useId();
  const [settings, setSettings] = useState<PageSettings | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outside, setOutside] = useState(false);
  const path = `/api/documents/page-reading/settings?scope=${encodeURIComponent(scope)}`;
  useEffect(() => {
    let current = true;
    void api<PageSettings>(path)
      .then((next) => {
        if (current) {
          setSettings(next);
          setOutside(next.outside);
          onEnabled?.(next.enabled);
        }
      })
      .catch((failure) => {
        if (current) setProblem(failureWords(failure));
      });
    return () => {
      current = false;
    };
  }, [path, onEnabled]);

  async function change(enabled: boolean) {
    setBusy(true);
    setProblem(null);
    try {
      const next = await put<PageSettings>("/api/documents/page-reading/settings", {
        scope,
        enabled,
        outside,
      });
      setSettings(next);
      onEnabled?.(next.enabled);
      onChanged?.();
    } catch (failure) {
      setProblem(failureWords(failure));
      try {
        setSettings(await api<PageSettings>(path));
      } catch {
        setSettings(null);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-plexus border border-line p-3">
      {settings && (
        <>
          <p>{settings.message}</p>
          {settings.model && (
            <p className="help">
              {settings.pages ?? 0} pages to read with {settings.model}. At most {settings.maxPages}{" "}
              per document. PNG and JPEG files can also be added when this is on.
            </p>
          )}
          <label htmlFor={`${uid}-on`} className="flex items-center gap-2">
            <input
              id={`${uid}-on`}
              data-testid="scanned-pages-switch"
              type="checkbox"
              checked={settings.enabled}
              disabled={busy || !settings.model}
              onChange={(e) => void change(e.target.checked)}
            />
            Read scanned pages
          </label>
          {!settings.enabled && settings.model && (
            <label className="flex items-center gap-2 text-sm">
              <input
                data-testid="scanned-pages-outside"
                type="checkbox"
                checked={outside}
                disabled={busy}
                onChange={(e) => setOutside(e.target.checked)}
              />
              Allow page images outside my machines for this reading, if the document rules allow it
            </label>
          )}
          <p className="help">
            Whole-document questions:{" "}
            {settings.wholeByParts === "always"
              ? "Read all parts automatically"
              : settings.wholeByParts === "ask"
                ? "Ask before reading all parts"
                : "Use passages only"}
            . The owner can change this in the console.
          </p>
        </>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}

export function ReadScannedDocument({ id, onStarted }: { id: string; onStarted: () => void }) {
  const [model, setModel] = useState<string | null>(null);
  const [plan, setPlan] = useState<{ message: string; canRead: boolean; maxPages: number } | null>(
    null,
  );
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [outside, setOutside] = useState(false);
  const base = `/api/documents/${encodeURIComponent(id)}/read-pages`;
  useEffect(() => {
    let current = true;
    void api<PageSettings>("/api/documents/page-reading/settings")
      .then((s) => {
        if (current) setModel(s.model);
      })
      .catch((f) => {
        if (current) setProblem(failureWords(f));
      });
    return () => {
      current = false;
    };
  }, []);

  async function act() {
    setBusy(true);
    setProblem(null);
    try {
      if (!plan) setPlan(await post(base + "/plan", {}));
      else {
        await post(base, { outside });
        setPlan(null);
        onStarted();
      }
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-start gap-2">
      {plan && (
        <p role="status">
          {plan.message}
          {!plan.canRead ? `; Workbench reads at most ${plan.maxPages}.` : ""}
        </p>
      )}
      {plan?.canRead && (
        <label>
          <input
            data-testid="scanned-document-outside"
            type="checkbox"
            checked={outside}
            onChange={(e) => setOutside(e.target.checked)}
          />{" "}
          Allow page images outside my machines for this reading, if the document rules allow it
        </label>
      )}
      {model ? (
        <button
          type="button"
          data-testid="scanned-document-read"
          className="btn"
          disabled={busy || plan?.canRead === false}
          onClick={() => void act()}
        >
          {plan ? "Start reading pages" : `Read the pages with ${model}`}
        </button>
      ) : (
        <p className="help">Off: scanned pages are skipped</p>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
