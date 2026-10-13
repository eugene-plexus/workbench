import { ArrowLeft, CircleAlert, CircleCheck, Info, TriangleAlert, Wrench } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api, del, post } from "../lib/api";
import type { ToolServer, ToolServers } from "../lib/types";
import { CopyButton } from "./CopyButton";
import { LocalToolForm } from "./LocalToolForm";
import { SiteLinkNote } from "./SiteLinkNote";
import { Folders } from "./Folders";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** What a check came back with. A failure keeps the words the check returned,
 * which name what it observed (a refused credential, a redirect, a reply that
 * was too large, a connection that failed): this page never guesses a cause. */
type Check = { kind: "ok" | "empty" | "failed"; text: string };

/** After a question is answered the button that asked it is back: keyboard
 * focus goes there instead of to the page. */
const refocus = (id: string) => queueMicrotask(() => document.getElementById(id)?.focus());

export function Tools({ owner, onClose }: { owner: boolean; onClose: () => void }) {
  const uid = useId();
  const [servers, setServers] = useState<ToolServer[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [remove, setRemove] = useState<string | null>(null);
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [local, setLocal] = useState<ToolServers["localProcesses"] | null>(null);
  const [reload, setReload] = useState(0);
  const [checking, setChecking] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api<ToolServers>("/api/tools/servers")
      .then((result) => {
        if (!cancelled) {
          setServers(result.servers);
          setLocal(result.localProcesses);
          setLoaded(true);
          setLoadProblem(null);
        }
      })
      .catch((error) => {
        if (!cancelled) setLoadProblem(problemOf(error));
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  async function check(server: ToolServer) {
    setBusy(true);
    setChecking(server.id);
    setChecks((old) => {
      const next = { ...old };
      delete next[server.id];
      return next;
    });
    try {
      const result = await post<{ tools: { name: string }[] }>(
        `/api/tools/servers/${encodeURIComponent(server.id)}/check`,
      );
      setChecks((old) => ({
        ...old,
        [server.id]: result.tools.length
          ? { kind: "ok", text: `Available tools: ${result.tools.map((t) => t.name).join(", ")}` }
          : { kind: "empty", text: "This server offers no tools." },
      }));
    } catch (error) {
      setChecks((old) => ({ ...old, [server.id]: { kind: "failed", text: problemOf(error) } }));
    } finally {
      setChecking(null);
      setBusy(false);
    }
  }

  async function removeServer(server: ToolServer) {
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
  }

  return (
    <section aria-label="Tools" className="page">
      <div className="page-col">
        <header className="page-head">
          <h1 id="view-heading" tabIndex={-1} className="title-page">
            <Wrench size={20} aria-hidden />
            Toolbox · Tools
          </h1>
          <button className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <p className="help">
          Connect an MCP server, then choose it in a chat&apos;s settings. Each call shows what it
          will send, and you approve it. Network servers are shared with everyone signed into this
          Workbench; local servers are for the owner only.
        </p>
        <Folders owner={owner} />
        <section aria-labelledby={`${uid}-servers`} className="section">
          <h2 id={`${uid}-servers`} className="title-section">
            Tool servers (MCP)
          </h2>
          {loadProblem && (
            <div role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              <div>
                <span className="notice-title">Could not read the tool servers</span>
                {loadProblem}
                <div className="mt-2">
                  <button
                    type="button"
                    className="btn"
                    onClick={() => {
                      setLoadProblem(null);
                      setReload((n) => n + 1);
                    }}
                  >
                    Try again
                  </button>
                </div>
              </div>
            </div>
          )}
          {problem && (
            <p role="alert" className="notice notice--error">
              <CircleAlert size={16} aria-hidden />
              {problem}
            </p>
          )}
          {!loaded && !loadProblem && (
            <p role="status" className="text-sm text-muted">
              Loading tool servers…
            </p>
          )}
          {loaded && servers.length === 0 && <p className="empty">No tool servers yet.</p>}
          {servers.map((server) => {
            const result = checks[server.id];
            const removeId = `${uid}-remove-${server.id}`;
            return (
              <article key={server.id} className="card">
                <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
                  <h3 className="title-card">{server.name}</h3>
                  <div className="flex flex-wrap gap-1.5">
                    {server.transport === "site" ? (
                      <>
                        <span className="badge">Job site</span>
                        {!server.available && (
                          <span className="badge badge--warn">Unavailable now</span>
                        )}
                      </>
                    ) : server.transport === "stdio" ? (
                      <>
                        <span className="badge">Local server</span>
                        <span className="badge">Owner only</span>
                      </>
                    ) : (
                      <span className="badge">Shared network server</span>
                    )}
                  </div>
                </div>
                {server.transport === "site" ? (
                  <>
                    <p className="text-sm">
                      On your job site {server.label}. Added at the machine by its administrator;
                      its owner says who may use which of its tools.
                    </p>
                    {!server.available && server.reason && (
                      <p className="notice notice--warn">
                        <TriangleAlert size={16} aria-hidden />
                        {server.reason}
                      </p>
                    )}
                    <SiteLinkNote
                      site={server.site}
                      label={server.label}
                      linking={server}
                      onChanged={() => setReload((n) => n + 1)}
                    />
                  </>
                ) : server.transport === "stdio" ? (
                  <>
                    <dl className="facts">
                      <dt>Command</dt>
                      <dd className="flex flex-wrap items-center gap-x-2">
                        <span>{server.command}</span>
                        <CopyButton text={server.command} label={`the command of ${server.name}`} />
                      </dd>
                      <dt>Arguments</dt>
                      <dd>
                        <code className="break-all font-mono text-[0.8125rem]">
                          {JSON.stringify(server.args)}
                        </code>
                      </dd>
                    </dl>
                    <p className="text-sm">
                      Stored environment names: {server.environmentKeys.join(", ") || "none"}
                    </p>
                    <p className="help">
                      Checking starts the program, then closes it. A chat starts its own process for
                      each answer.
                    </p>
                  </>
                ) : (
                  <dl className="facts">
                    <dt>Address</dt>
                    <dd className="flex flex-wrap items-center gap-x-2">
                      <span>{server.url}</span>
                      <CopyButton text={server.url} label={`the address of ${server.name}`} />
                    </dd>
                    <dt>Credential</dt>
                    <dd>
                      {server.hasToken ? "A credential is stored." : "No credential is stored."}
                    </dd>
                  </dl>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className="btn"
                    disabled={busy || (server.transport === "stdio" && !local?.available)}
                    aria-label={`${server.transport === "stdio" ? "Start and check" : "Check connection to"} ${server.name}`}
                    onClick={() => void check(server)}
                  >
                    {server.transport === "stdio" ? "Start and check" : "Check connection"}
                  </button>
                  {owner && server.transport !== "site" && remove !== server.id && (
                    <button
                      id={removeId}
                      className="btn btn-quiet btn-danger -me-2 ml-auto"
                      aria-label={`Remove ${server.name}`}
                      onClick={() => setRemove(server.id)}
                    >
                      Remove
                    </button>
                  )}
                </div>
                {owner && server.transport !== "site" && remove === server.id && (
                  <div
                    role="group"
                    aria-label={`Remove ${server.name}?`}
                    className="notice notice--error flex-col"
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        setRemove(null);
                        refocus(removeId);
                      }
                    }}
                  >
                    <p>
                      Remove {server.name}? Pending calls will not run. Saved server files will
                      remain.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        autoFocus
                        className="btn"
                        aria-label={`Keep ${server.name}: do not remove it`}
                        onClick={() => {
                          setRemove(null);
                          refocus(removeId);
                        }}
                      >
                        Keep it
                      </button>
                      <button
                        disabled={busy}
                        className="btn btn-danger"
                        aria-label={`Remove connection ${server.name}`}
                        onClick={() => void removeServer(server)}
                      >
                        Remove connection
                      </button>
                    </div>
                  </div>
                )}
                {checking === server.id && (
                  <p role="status" className="text-sm text-muted">
                    Checking…
                  </p>
                )}
                {result?.kind === "ok" && (
                  <p role="status" className="notice notice--ok whitespace-pre-wrap">
                    <CircleCheck size={16} aria-hidden />
                    {result.text}
                  </p>
                )}
                {result?.kind === "empty" && (
                  <p role="status" className="notice">
                    <Info size={16} aria-hidden />
                    {result.text}
                  </p>
                )}
                {result?.kind === "failed" && (
                  <div role="alert" className="notice notice--error">
                    <CircleAlert size={16} aria-hidden />
                    <div className="whitespace-pre-wrap">
                      <span className="notice-title">The check failed</span>
                      {result.text}
                    </div>
                  </div>
                )}
              </article>
            );
          })}
          {owner &&
            local &&
            (local.available ? (
              <LocalToolForm onAdded={(server) => setServers((old) => [...old, server])} />
            ) : (
              local.reason && (
                <p className="notice">
                  <Info size={16} aria-hidden />
                  {local.reason}
                </p>
              )
            ))}
          {owner ? (
            <form
              aria-labelledby={`${uid}-add`}
              className="card card--add"
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
              <h3 id={`${uid}-add`} className="title-card">
                Add a shared network server
              </h3>
              <div className="field">
                <label htmlFor={`${uid}-name`} className="field-label">
                  Name
                </label>
                <input
                  id={`${uid}-name`}
                  required
                  maxLength={80}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="control"
                />
              </div>
              <div className="field">
                <label htmlFor={`${uid}-url`} className="field-label">
                  MCP address
                </label>
                <input
                  id={`${uid}-url`}
                  required
                  type="url"
                  maxLength={2048}
                  placeholder="https://example.org/mcp"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  aria-describedby={`${uid}-url-help`}
                  className="control"
                />
                <p id={`${uid}-url-help`} className="help">
                  Use the Streamable HTTP address. HTTPS is required except on this machine&apos;s
                  loopback address.
                </p>
              </div>
              <div className="field">
                <label htmlFor={`${uid}-token`} className="field-label">
                  Bearer credential (optional)
                </label>
                <input
                  id={`${uid}-token`}
                  type="password"
                  autoComplete="new-password"
                  maxLength={4096}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  aria-describedby={`${uid}-token-help`}
                  className="control"
                />
                <p id={`${uid}-token-help`} className="help">
                  Use a credential intended for everyone here. To change a connection, remove it and
                  add a new one.
                </p>
              </div>
              <button disabled={busy} type="submit" className="btn btn-primary self-start">
                Add server
              </button>
            </form>
          ) : (
            <p className="help">The owner can add or remove shared servers.</p>
          )}
        </section>
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
  const [reload, setReload] = useState(0);
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
  }, [reload]);
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="eyebrow mb-2 text-muted">Toolbox · Tools for this chat</legend>
      <p className="help">Applies to the next answer. Each call needs your approval.</p>
      {problem && (
        <div role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read the tool servers</span>
            {problem}
            <div className="mt-2">
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setProblem(null);
                  setReload((n) => n + 1);
                }}
              >
                Try again
              </button>
            </div>
          </div>
        </div>
      )}
      {servers === null && !problem && (
        <p role="status" className="text-sm text-muted">
          Loading servers…
        </p>
      )}
      {servers?.length === 0 && (
        <p className="empty">No servers available. The owner can add one in Tools.</p>
      )}
      {servers?.map((server) => {
        // The separator is a text node of the label, outside the muted span:
        // an accessible name computed from content drops a span's leading space.
        const note =
          server.transport === "stdio"
            ? "Local, owner only"
            : server.transport === "site" && !server.available
              ? "Unavailable now"
              : "";
        return (
          <label key={server.id} className="check">
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
            <span>
              {server.name}
              {note && (
                <>
                  {" "}
                  <span className="text-muted">· {note}</span>
                </>
              )}
            </span>
          </label>
        );
      })}
      {servers &&
        selected
          .filter((id) => !servers.some((s) => s.id === id))
          .map((id) => (
            <label key={id} className="check">
              <input
                type="checkbox"
                checked
                onChange={() => onChange(selected.filter((i) => i !== id))}
              />
              <span className="text-warn">Removed or unavailable server — uncheck to clear it</span>
            </label>
          ))}
    </fieldset>
  );
}
