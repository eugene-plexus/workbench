import { useEffect, useId, useState } from "react";
import { api, del, post } from "../lib/api";
import { words } from "../lib/interactions";
import type { Agent } from "../lib/agui";

export function AgentConnections({ onChanged }: { onChanged: () => void }) {
  const uid = useId();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [people, setPeople] = useState<{ sub: string; name: string }[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [subjects, setSubjects] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [generation, update] = useState(0);
  useEffect(() => {
    let live = true;
    void Promise.all([
      api<Agent[]>("/api/v1/ag-ui/agents"),
      api<{ sub: string; name: string }[]>("/api/v1/ag-ui/people"),
    ])
      .then(([connections, known]) => {
        if (live) {
          setAgents(Array.isArray(connections) ? connections : []);
          setPeople(Array.isArray(known) ? known : []);
        }
      })
      .catch((error) => {
        if (live) setError(words(error));
      });
    return () => {
      live = false;
    };
  }, [generation]);
  const changed = () => {
    update((n) => n + 1);
    onChanged();
  };
  return (
    <details className="rounded-lg border border-line p-3">
      <summary>Manage agent connections</summary>
      <p className="my-2 text-sm text-muted">
        Connect an AG-UI agent and choose who can use it. To change a connection, remove it and add
        a new one.
      </p>
      {agents.map((agent) => (
        <div key={agent.id} className="my-3 flex flex-wrap items-center gap-2">
          <span>
            {agent.name} · {agent.url}
          </span>
          {confirm !== agent.id ? (
            <button className="btn" onClick={() => setConfirm(agent.id)}>
              Remove {agent.name}
            </button>
          ) : (
            <>
              <p>Remove this connection and stop its active runs?</p>
              <button className="btn" onClick={() => setConfirm(null)}>
                Keep connection
              </button>
              <button
                className="btn btn-danger"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    await del(`/api/v1/ag-ui/agents/${encodeURIComponent(agent.id)}`);
                    setConfirm(null);
                    changed();
                  } catch (error) {
                    setError(words(error));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Remove connection
              </button>
            </>
          )}
        </div>
      ))}
      <form
        className="flex flex-col gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await post("/api/v1/ag-ui/agents", { name, url, token, subjects });
            setName("");
            setUrl("");
            setToken("");
            setSubjects([]);
            changed();
          } catch (error) {
            setError(words(error));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor={`${uid}-name`}>Agent name</label>
        <input
          className="input"
          id={`${uid}-name`}
          value={name}
          maxLength={120}
          required
          onChange={(e) => setName(e.target.value)}
        />
        <label htmlFor={`${uid}-url`}>Agent URL</label>
        <input
          className="input"
          id={`${uid}-url`}
          type="url"
          value={url}
          maxLength={2048}
          required
          onChange={(e) => setUrl(e.target.value)}
        />
        <label htmlFor={`${uid}-token`}>Bearer token (optional)</label>
        <input
          className="input"
          id={`${uid}-token`}
          type="password"
          autoComplete="new-password"
          value={token}
          maxLength={4096}
          onChange={(e) => setToken(e.target.value)}
        />
        <fieldset>
          <legend>People who may use this agent</legend>
          {people.map((person) => (
            <label key={person.sub} className="mr-4 inline-flex gap-2">
              <input
                type="checkbox"
                checked={subjects.includes(person.sub)}
                onChange={(e) =>
                  setSubjects((old) =>
                    e.target.checked ? [...old, person.sub] : old.filter((p) => p !== person.sub),
                  )
                }
              />
              {person.name}
            </label>
          ))}
        </fieldset>
        <p className="text-sm text-muted">People appear here after signing in to Workbench.</p>
        <button className="btn self-start" disabled={busy || !subjects.length}>
          Add agent connection
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
