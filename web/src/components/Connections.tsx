/**
 * Connections (MR9b), on the Memory page: let another app, such as Claude Code, Codex or
 * Open WebUI, read this person's own notes and search their own documents through
 * Workbench's MCP address.
 *
 * A connection is the person's say for what it may do: each right is chosen here, and
 * the page says before the token is shown that the app may send what it reads to a model
 * outside their machines. The token is shown once, in the answer that made it; Workbench
 * keeps only its hash, so a lost token means a new connection. Revoking stops the next
 * call at once, and the suggestions it made that were not kept go with it.
 */

import { Copy, Plug, TriangleAlert } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";

import { api, del, post } from "../lib/api";
import { failureWords, noteDate, plural } from "../lib/memory";

export interface ConnectionRights {
  notes: boolean;
  documents: boolean;
  suggest: boolean;
}

export interface Connection {
  id: string;
  name: string;
  rights: ConnectionRights;
  createdAt: number;
  expiresAt: number;
  expired: boolean;
  lastUsedAt: number | null;
  callsToday: number;
}

export interface ConnectionsPage {
  enabled: boolean;
  notesAllowed: boolean;
  documentsAllowed: boolean;
  suggestionsAllowed: boolean;
  address: string;
  limit: number;
  days: number[];
  connections: Connection[];
}

interface Made {
  connection: Connection;
  token: string;
  address: string;
  setups: { claudeCode: string; codex: string; json: string };
}

const RIGHT_WORDS: [keyof ConnectionRights, string][] = [
  ["notes", "Reads your notes"],
  ["documents", "Searches your documents"],
  ["suggest", "Suggests notes for you to keep or dismiss"],
];

function rightsWords(rights: ConnectionRights): string {
  return RIGHT_WORDS.filter(([key]) => rights[key])
    .map(([, words]) => words)
    .join(" · ");
}

const day = (seconds: number) => noteDate(seconds);

export function Connections() {
  const uid = useId();
  const [data, setData] = useState<ConnectionsPage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [making, setMaking] = useState(false);
  const [made, setMade] = useState<Made | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setData(await api<ConnectionsPage>("/api/connections"));
    } catch (failure) {
      setProblem(`Could not load your connections: ${failureWords(failure)}`);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function revoke(id: string) {
    setBusy(true);
    setProblem(null);
    try {
      await del(`/api/connections/${encodeURIComponent(id)}`);
      setRevoking(null);
      await load();
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  if (data === null) {
    return problem ? (
      <section aria-labelledby={`${uid}-title`} className="section">
        <h2 id={`${uid}-title`} className="title-section">
          <Plug size={18} aria-hidden />
          Connections to other apps
        </h2>
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      </section>
    ) : null;
  }
  const full = data.connections.length >= data.limit;

  return (
    <section aria-labelledby={`${uid}-title`} data-testid="memory-connections" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Plug size={18} aria-hidden />
        Connections to other apps
      </h2>
      <p className="help">
        Let an app such as Claude Code, Codex or Open WebUI read your notes and search your
        documents through Workbench. A connection reads only yours, does only what you choose, and
        works while you are signed in to Workbench on any device. To stop one, revoke it.
      </p>
      {!data.enabled && (
        <p data-testid="memory-connections-off" className="notice">
          The owner of this Workbench has turned connections to other apps off. None of yours works
          until they turn them on again.
        </p>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      {data.connections.length > 0 && (
        <ul className="divide-y divide-line rounded-plexus border border-line bg-panel">
          {data.connections.map((c) => (
            <li key={c.id} data-testid="memory-connection" className="flex flex-col gap-1 p-3">
              <p className="wrap-anywhere">
                <strong>{c.name}</strong>
              </p>
              <p className="help">{rightsWords(c.rights)}</p>
              <p className="help">
                {c.expired ? (
                  <span data-testid="memory-connection-expired">
                    Expired on {day(c.expiresAt)}. Make a new one to use it again.
                  </span>
                ) : (
                  <>Works until {day(c.expiresAt)}. </>
                )}{" "}
                {c.lastUsedAt
                  ? `Last used ${day(c.lastUsedAt)}; ${plural(c.callsToday, "call")} today.`
                  : "Not used yet."}
              </p>
              {revoking === c.id ? (
                <div role="group" aria-label={`Revoke ${c.name}?`} className="flex flex-wrap gap-2">
                  <span className="help">
                    The app stops at once. Its unkept suggestions go too.
                  </span>
                  <button
                    type="button"
                    className="btn"
                    disabled={busy}
                    onClick={() => setRevoking(null)}
                  >
                    Keep it
                  </button>
                  <button
                    type="button"
                    data-testid="memory-connection-revoke-go"
                    className="btn btn-danger"
                    disabled={busy}
                    onClick={() => void revoke(c.id)}
                  >
                    Revoke
                  </button>
                </div>
              ) : (
                <div>
                  <button
                    type="button"
                    data-testid="memory-connection-revoke"
                    className="btn"
                    disabled={busy}
                    onClick={() => setRevoking(c.id)}
                  >
                    Revoke
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {made ? (
        <Token made={made} onDone={() => setMade(null)} />
      ) : making ? (
        <MakeForm
          data={data}
          onCancel={() => setMaking(false)}
          onMade={(result) => {
            setMaking(false);
            setMade(result);
            void load();
          }}
        />
      ) : (
        data.enabled && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="memory-connection-new"
              className="btn"
              disabled={full}
              onClick={() => setMaking(true)}
            >
              <Plug size={14} aria-hidden />
              Make a connection
            </button>
            {full && (
              <span className="help">
                You have {data.limit} connections. Revoke one to make another.
              </span>
            )}
          </div>
        )
      )}
    </section>
  );
}

function MakeForm({
  data,
  onCancel,
  onMade,
}: {
  data: ConnectionsPage;
  onCancel: () => void;
  onMade: (made: Made) => void;
}) {
  const uid = useId();
  const [name, setName] = useState("");
  const [rights, setRights] = useState<ConnectionRights>({
    notes: data.notesAllowed,
    documents: data.documentsAllowed,
    suggest: false,
  });
  const [days, setDays] = useState(data.days[0] ?? 90);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const allowed: Record<keyof ConnectionRights, boolean> = {
    notes: data.notesAllowed,
    documents: data.documentsAllowed,
    suggest: data.suggestionsAllowed,
  };
  const why: Record<keyof ConnectionRights, string> = {
    notes: "Notes are turned off on this Workbench.",
    documents: "Documents are turned off on this Workbench.",
    suggest: "The owner of this Workbench has turned suggested notes off.",
  };
  const chosen = rights.notes || rights.documents || rights.suggest;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      onMade(await post<Made>("/api/connections", { name: name.trim(), rights, days }));
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      aria-labelledby={`${uid}-make`}
      data-testid="memory-connection-form"
      className="flex flex-col gap-3 rounded-plexus border border-line bg-panel p-3"
      onSubmit={(event) => void submit(event)}
    >
      <h3 id={`${uid}-make`} className="title-card">
        Make a connection
      </h3>
      <div className="field max-w-md">
        <label htmlFor={`${uid}-name`} className="field-label">
          Name it after the app and the machine
        </label>
        <input
          id={`${uid}-name`}
          data-testid="memory-connection-name"
          required
          maxLength={80}
          autoComplete="off"
          placeholder="Claude Code on my laptop"
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="control"
        />
      </div>
      <fieldset className="flex flex-col gap-1">
        <legend className="field-label mb-1">What it may do</legend>
        {RIGHT_WORDS.map(([key, words]) => (
          <label key={key} className="flex items-start gap-2">
            <input
              type="checkbox"
              data-testid={`memory-connection-right-${key}`}
              checked={rights[key] && allowed[key]}
              disabled={!allowed[key]}
              onChange={(event) => setRights({ ...rights, [key]: event.target.checked })}
            />
            <span>
              {words}
              {key === "suggest" && allowed.suggest && (
                <span className="help block">
                  Suggestions wait under Suggested notes on this page until you keep or dismiss
                  them.
                </span>
              )}
              {!allowed[key] && <span className="help block">{why[key]}</span>}
            </span>
          </label>
        ))}
      </fieldset>
      <div className="field w-48">
        <label htmlFor={`${uid}-days`} className="field-label">
          Works for
        </label>
        <select
          id={`${uid}-days`}
          data-testid="memory-connection-days"
          value={days}
          onChange={(event) => setDays(Number(event.target.value))}
          className="control"
        >
          {[...data.days]
            .sort((a, b) => a - b)
            .map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
        </select>
      </div>
      <p className="notice flex items-start gap-1.5">
        <TriangleAlert size={14} aria-hidden className="mt-0.5 shrink-0" />
        <span>
          The app may send what it reads to the model it uses, which can run outside your machines
          (Claude Code sends it to Anthropic). Make a connection only for an app you trust with your
          notes and documents.
        </span>
      </p>
      {problem && (
        <p role="alert" className="notice notice--error">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="submit"
          data-testid="memory-connection-make"
          className="btn btn-primary"
          disabled={busy || !chosen || !name.trim()}
        >
          Make connection
        </button>
      </div>
    </form>
  );
}

function Token({ made, onDone }: { made: Made; onDone: () => void }) {
  const uid = useId();
  const [said, setSaid] = useState<string | null>(null);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      setSaid(`Copied the ${what}.`);
    } catch {
      setSaid("This browser would not copy it here. Select the text and copy it yourself.");
    }
  }

  return (
    <div
      role="region"
      aria-labelledby={`${uid}-token`}
      data-testid="memory-connection-token"
      className="flex flex-col gap-3 rounded-plexus border border-line bg-panel p-3"
    >
      <h3 id={`${uid}-token`} className="title-card">
        {made.connection.name}: copy its token now
      </h3>
      <p className="help">
        Workbench shows this token once and keeps only a fingerprint of it. If you lose it, revoke
        the connection and make a new one. Anyone who has it can read what this connection may read.
      </p>
      <div className="field">
        <label htmlFor={`${uid}-value`} className="field-label">
          Token
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id={`${uid}-value`}
            data-testid="memory-connection-token-value"
            readOnly
            value={made.token}
            onFocus={(event) => event.target.select()}
            className="control font-mono min-w-0 flex-1"
          />
          <button type="button" className="btn" onClick={() => void copy(made.token, "token")}>
            <Copy size={14} aria-hidden />
            Copy
          </button>
        </div>
      </div>
      <p className="help wrap-anywhere">
        Address: <code>{made.address}</code>, sent with the header{" "}
        <code>Authorization: Bearer</code> and the token.
      </p>
      <div className="field">
        <span className="field-label">Claude Code</span>
        <pre className="wrap-anywhere whitespace-pre-wrap">{made.setups.claudeCode}</pre>
        <div>
          <button
            type="button"
            className="btn"
            onClick={() => void copy(made.setups.claudeCode, "command")}
          >
            <Copy size={14} aria-hidden />
            Copy the command
          </button>
        </div>
      </div>
      <div className="field">
        <span className="field-label">Codex</span>
        <p className="help">
          Add this to <code>.codex/config.toml</code> in your home folder. The Codex app in your
          editor reads the same file.
        </p>
        <pre className="wrap-anywhere whitespace-pre-wrap">{made.setups.codex}</pre>
        <div>
          <button
            type="button"
            className="btn"
            onClick={() => void copy(made.setups.codex, "Codex settings")}
          >
            <Copy size={14} aria-hidden />
            Copy the Codex settings
          </button>
        </div>
      </div>
      <div className="field">
        <span className="field-label">Apps that read an MCP settings file</span>
        <pre className="wrap-anywhere whitespace-pre-wrap">{made.setups.json}</pre>
      </div>
      {said && (
        <p role="status" className="help">
          {said}
        </p>
      )}
      <div>
        <button
          type="button"
          data-testid="memory-connection-done"
          className="btn btn-primary"
          onClick={onDone}
        >
          I have copied it
        </button>
      </div>
    </div>
  );
}
