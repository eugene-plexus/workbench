import { useEffect, useState } from "react";

import { api, del, post } from "../lib/api";
import type { ToolServer } from "../lib/types";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

export function Tools({ owner, onClose }: { owner: boolean; onClose: () => void }) {
  const [servers, setServers] = useState<ToolServer[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    void api<{ servers: ToolServer[] }>("/api/tools/servers")
      .then((result) => {
        if (!cancelled) setServers(result.servers);
      })
      .catch((error) => {
        if (!cancelled) setProblem(problemOf(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-label="Tools" className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold">Toolbox · Tools</h1>
          <button onClick={onClose}>Back to chat</button>
        </div>
        <p className="text-sm text-muted">
          Connect an MCP server, then choose it in a chat&apos;s settings. Each call shows what it
          will send and waits for your approval.
        </p>
        <p className="text-sm text-muted">
          These connections are shared with everyone signed into this Workbench. Local server
          commands and folder access are not available yet.
        </p>
        {problem && (
          <p role="alert" className="text-error">
            {problem}
          </p>
        )}
        {servers.map((server) => (
          <article
            key={server.id}
            className="flex flex-col gap-2 rounded-plexus border border-line p-3"
          >
            <h2 className="font-semibold">{server.name}</h2>
            <p className="break-all text-sm text-muted">{server.url}</p>
            <p className="text-sm">
              {server.hasToken ? "A credential is stored." : "No credential is stored."}
            </p>
            <div className="flex flex-wrap gap-3 text-sm">
              <button
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    const result = await post<{ tools: { name: string }[] }>(
                      `/api/tools/servers/${server.id}/check`,
                    );
                    setChecks((old) => ({
                      ...old,
                      [server.id]: result.tools.length
                        ? `Available tools: ${result.tools.map((t) => t.name).join(", ")}`
                        : "This server offers no tools.",
                    }));
                  } catch (error) {
                    setChecks((old) => ({ ...old, [server.id]: problemOf(error) }));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Check connection
              </button>
              {owner &&
                (remove === server.id ? (
                  <>
                    <span>Remove this shared connection? Pending calls will not run.</span>
                    <button
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        try {
                          await del(`/api/tools/servers/${server.id}`);
                          setServers((old) => old.filter((s) => s.id !== server.id));
                          setRemove(null);
                        } catch (error) {
                          setProblem(problemOf(error));
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      Remove connection
                    </button>
                    <button onClick={() => setRemove(null)}>Keep it</button>
                  </>
                ) : (
                  <button onClick={() => setRemove(server.id)}>Remove</button>
                ))}
            </div>
            {checks[server.id] && (
              <p role="status" className="whitespace-pre-wrap break-words text-sm">
                {checks[server.id]}
              </p>
            )}
          </article>
        ))}
        {owner ? (
          <form
            className="flex flex-col gap-3 rounded-plexus border border-line p-3"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setProblem(null);
              try {
                const server = await post<ToolServer>("/api/tools/servers", { name, url, token });
                setServers((old) => [...old, server]);
                setName("");
                setUrl("");
                setToken("");
              } catch (error) {
                setProblem(problemOf(error));
              } finally {
                setBusy(false);
              }
            }}
          >
            <h2 className="font-semibold">Add a shared server</h2>
            <label className="flex flex-col gap-1 text-sm">
              Name
              <input
                required
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="rounded-plexus border border-line bg-soft p-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              MCP address
              <input
                required
                type="url"
                maxLength={2048}
                placeholder="https://example.org/mcp"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="rounded-plexus border border-line bg-soft p-2"
              />
            </label>
            <p className="text-sm text-muted">
              Use the Streamable HTTP address. HTTPS is required except on this machine&apos;s
              loopback address.
            </p>
            <label className="flex flex-col gap-1 text-sm">
              Bearer credential (optional)
              <input
                type="password"
                autoComplete="new-password"
                maxLength={4096}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                className="rounded-plexus border border-line bg-soft p-2"
              />
            </label>
            <p className="text-sm text-muted">
              Use a credential intended for everyone here. To change a connection, remove it and add
              a new one.
            </p>
            <button
              disabled={busy}
              type="submit"
              className="self-end rounded-plexus bg-accent px-3 py-2 text-on-accent"
            >
              Add server
            </button>
          </form>
        ) : (
          <p className="text-sm text-muted">The owner can add or remove shared servers.</p>
        )}
      </div>
    </section>
  );
}

export function ToolSelection({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [servers, setServers] = useState<ToolServer[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void api<{ servers: ToolServer[] }>("/api/tools/servers")
      .then((result) => {
        if (!cancelled) setServers(result.servers);
      })
      .catch((error) => {
        if (!cancelled) setProblem(problemOf(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <fieldset className="flex flex-col gap-2 text-sm">
      <legend className="font-medium">Toolbox · Tools for this chat</legend>
      <p className="text-muted">Applies to the next answer. Each call needs your approval.</p>
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      {servers === null && !problem && <p>Loading servers…</p>}
      {servers?.length === 0 && <p>No shared servers. The owner can add one in Tools.</p>}
      {servers?.map((server) => (
        <label key={server.id} className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={selected.includes(server.id)}
            onChange={(event) =>
              onChange(
                event.target.checked
                  ? [...selected, server.id]
                  : selected.filter((id) => id !== server.id),
              )
            }
          />
          {server.name}
        </label>
      ))}
      {servers &&
        selected
          .filter((id) => !servers.some((s) => s.id === id))
          .map((id) => (
            <label key={id} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked
                onChange={() => onChange(selected.filter((i) => i !== id))}
              />
              Removed server — uncheck to clear it
            </label>
          ))}
    </fieldset>
  );
}
