/**
 * Your data (row 7), on the Settings page: everyone can download everything Workbench holds of
 * them, at any time, as one ZIP file. It is made on the server, kept for a day, and downloaded
 * only by the person who asked for it.
 */

import { Download } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api, fileOf, post } from "../lib/api";
import { failureWords, noteTime, saveFile } from "../lib/memory";
import { type DataExport, sizeWords } from "../lib/removal";

const POLL_MS = 1000;

export function YourData() {
  const uid = useId();
  const [exports, setExports] = useState<DataExport[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Each change reads the list again; a download being made asks for another in a second.
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    let wait: number | null = null;
    api<{ exports: DataExport[] }>("/api/exports")
      .then((answer) => {
        if (!live) return;
        const listed = answer.exports.filter((e) => e.scope === "self");
        setExports(listed);
        if (listed.some((e) => e.state === "making")) {
          wait = window.setTimeout(() => setTick((n) => n + 1), POLL_MS);
        }
      })
      .catch((failure: unknown) => {
        if (live) setProblem(`Could not load your downloads: ${failureWords(failure)}`);
      });
    return () => {
      live = false;
      if (wait !== null) window.clearTimeout(wait);
    };
  }, [tick]);

  async function make() {
    setBusy(true);
    setProblem(null);
    try {
      await post<{ export: DataExport }>("/api/me/export");
      setTick((n) => n + 1);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function download(made: DataExport) {
    setProblem(null);
    try {
      const file = await fileOf(`/api/exports/${encodeURIComponent(made.id)}`);
      saveFile(file.blob, file.name ?? "workbench-my-data.zip");
    } catch (failure) {
      setProblem(`Could not download it: ${failureWords(failure)}`);
    }
  }

  const latest = exports?.[0];
  return (
    <section
      id="your-data"
      aria-labelledby={`${uid}-title`}
      className="section scroll-mt-4"
      data-testid="your-data"
    >
      <h2 id={`${uid}-title`} className="title-section">
        <Download size={18} aria-hidden />
        Your data
      </h2>
      <p className="help">
        Download your chats, files, media, notes and documents as one ZIP file.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className="btn"
          onClick={() => void make()}
          disabled={busy || latest?.state === "making"}
          data-testid="export-mine"
        >
          Download your data
        </button>
        {latest?.state === "making" && <span role="status">Making your download…</span>}
        {latest?.state === "ready" && (
          <span role="status" className="flex items-center gap-2">
            Ready, {sizeWords(latest.size)}. Kept until {noteTime(latest.expiresAt)}.
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => void download(latest)}
              data-testid="export-download"
            >
              Save it
            </button>
          </span>
        )}
        {latest?.state === "failed" && (
          <span role="alert" className="notice notice--error">
            {latest.error}
          </span>
        )}
      </div>
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
    </section>
  );
}
