/**
 * Your memory server (MV5), on the Settings page under Memory: a memory system the person runs
 * themselves, reached over MCP, beside their notes. It receives their own words from each chat
 * from when it is connected (as their write policy allows) and is asked for memory every turn.
 * Eugene cannot check what it keeps, so the section says so, and says what the server cannot do.
 */

import { CircleAlert, Info, Server } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";

import { api, put } from "../lib/api";
import { failureWords, plural } from "../lib/memory";
import type { MemoryServerPage } from "../lib/types";
import { MemoryBackfill } from "./MemoryBackfill";

function when(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function MemoryServer() {
  const uid = useId();
  const [data, setData] = useState<MemoryServerPage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [asking, setAsking] = useState(false);

  async function load() {
    try {
      setData(await api<MemoryServerPage>("/api/memory/server"));
      setProblem(null);
    } catch (failure) {
      setProblem(failureWords(failure));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      const same = data?.server?.url === url.trim();
      const next = await put<MemoryServerPage>("/api/memory/server", {
        url: url.trim(),
        token: token === "" && same ? null : token,
      });
      setData(next);
      setEditing(false);
      setToken("");
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setProblem(null);
    try {
      setData(await api<MemoryServerPage>("/api/memory/server", { method: "DELETE" }));
      setAsking(false);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const server = data?.server ?? null;
  const showForm = data?.allowed && (server === null || editing);
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="memory-server" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Server size={18} aria-hidden />
        Your memory server
      </h2>
      <p className="help">
        A memory system you run yourself, such as Hindsight, reached over MCP. It receives your own
        words from each chat, as your write policy allows, from when you connect it, and is asked
        for memory every turn, beside your notes. Eugene cannot check what it keeps, who else can
        read it, or whether it deletes.
      </p>
      {problem && (
        <p role="alert" data-testid="memory-server-problem" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {data && !data.allowed && (
        <p data-testid="memory-server-off" className="notice">
          <Info size={16} aria-hidden />
          {data.why}
        </p>
      )}
      {server && (
        <div data-testid="memory-server-connected" className="flex flex-col gap-1 text-sm">
          <p>
            <span className="font-medium">{server.name || "Memory server"}</span>{" "}
            <span className="text-muted wrap-anywhere">{server.url}</span>
          </p>
          {server.lastError ? (
            <p data-testid="memory-server-error" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              Not reached
              {server.lastErrorAt ? ` since ${when(server.lastErrorAt)}` : ""}: {server.lastError}
            </p>
          ) : (
            <p className="help">
              {server.lastOkAt ? `Reached ${when(server.lastOkAt)}.` : "Not asked yet."}
            </p>
          )}
          <p data-testid="memory-server-counts" className="help">
            {plural(server.sent, "exchange")} sent.
            {server.waiting ? ` ${plural(server.waiting, "exchange")} waiting to be sent.` : ""}
            {server.forgetting
              ? ` ${plural(server.forgetting, "exchange")} of deleted chats waiting to be forgotten.`
              : ""}
            {server.skipped
              ? ` Not sent for looking like a password or key: ${plural(server.skipped, "turn")}.`
              : ""}
          </p>
          {server.missing.length > 0 && (
            <ul data-testid="memory-server-missing" className="help list-disc pl-5">
              {server.missing.map((words) => (
                <li key={words}>{words}</li>
              ))}
            </ul>
          )}
          {server.verbs.includes("retain") && (
            <MemoryBackfill key={server.url} allowed={data?.allowed ?? false} />
          )}
          {data?.allowed && !editing && !asking && (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => {
                  setUrl(server.url);
                  setToken("");
                  setEditing(true);
                }}
              >
                Change
              </button>
              <button
                type="button"
                data-testid="memory-server-disconnect"
                className="btn btn-quiet"
                disabled={busy}
                onClick={() => setAsking(true)}
              >
                Disconnect
              </button>
            </div>
          )}
          {asking && (
            <div role="alertdialog" aria-labelledby={`${uid}-ask`} className="notice">
              <div>
                <span id={`${uid}-ask`} className="notice-title">
                  Disconnect this memory server?
                </span>
                Nothing on the server is deleted.
                {server.forgetting
                  ? ` It will not be asked to forget the ${plural(server.forgetting, "exchange")} of deleted chats still waiting.`
                  : ""}
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn"
                    autoFocus
                    disabled={busy}
                    onClick={() => setAsking(false)}
                  >
                    Keep it
                  </button>
                  <button
                    type="button"
                    data-testid="memory-server-disconnect-yes"
                    className="btn btn-quiet"
                    disabled={busy}
                    onClick={() => void disconnect()}
                  >
                    Disconnect
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      {showForm && (
        <form
          data-testid="memory-server-form"
          className="flex flex-col gap-2"
          onSubmit={(e) => void connect(e)}
        >
          <div className="field">
            <label htmlFor={`${uid}-url`} className="field-label">
              Address
            </label>
            <input
              id={`${uid}-url`}
              type="url"
              required
              maxLength={2000}
              placeholder="https://memory.example.com/mcp/"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="control max-w-full"
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-token`} className="field-label">
              Token (optional)
            </label>
            <input
              id={`${uid}-token`}
              type="password"
              autoComplete="off"
              maxLength={4096}
              placeholder={server?.hasToken ? "A token is saved" : ""}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="control max-w-full"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn btn-primary" disabled={busy || !url.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
            {editing && (
              <button
                type="button"
                className="btn btn-quiet"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
            )}
          </div>
          <p className="help">Your chats before connecting are not sent.</p>
        </form>
      )}
    </section>
  );
}
