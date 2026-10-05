import { useCallback, useEffect, useState } from "react";

import { api, post } from "../lib/api";
import type { JobSite, JobSiteInvite, JobSiteList } from "../lib/types";
import { CopyButton } from "./CopyButton";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Job sites (your machines): the machines whose files you use from here.
 * Yours alone: only you say who may use their folders, yourself included. */
export function JobSites({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<JobSiteList | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [invite, setInvite] = useState<JobSiteInvite | null>(null);
  const [machine, setMachine] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<JobSiteList>("/api/job-sites"));
    } catch (error) {
      setProblem(problemOf(error));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await work();
      await load();
    } catch (error) {
      setProblem(problemOf(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Job sites"
      className="mx-auto flex w-full max-w-3xl flex-col gap-4 overflow-y-auto p-4"
    >
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Job sites (your machines)</h1>
        <button className="text-sm text-accent" onClick={onClose}>
          Back to chats
        </button>
      </div>
      <p className="text-sm text-muted">
        A job site is a machine of yours that Workbench can work on from anywhere: its folders,
        chosen by you, and each listing, read or write with your approval. The machine connects out
        to Eugene and nothing connects to it. Only you say who may use its folders, you included.
      </p>
      {problem && (
        <p role="alert" className="text-sm text-error">
          {problem}
        </p>
      )}
      {data?.sites.length === 0 && <p className="text-sm">You have no job sites yet.</p>}
      {data?.sites.map((site) => (
        <Site key={site.node} site={site} busy={busy} act={act} />
      ))}
      <section
        aria-label="Add a job site"
        className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
      >
        <h2 className="font-semibold">Add a job site</h2>
        {data && !data.canInvite ? (
          <p>
            Eugene&apos;s owner has not opened a route for machines outside its network yet, so a
            machine can be added only from Eugene&apos;s console.
          </p>
        ) : (
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              void act(async () =>
                setInvite(
                  await post<JobSiteInvite>("/api/job-sites/invite", {
                    nodeName: machine.trim() || null,
                  }),
                ),
              );
            }}
          >
            <label className="flex flex-col gap-1">
              Machine name (optional)
              <input
                value={machine}
                onChange={(event) => setMachine(event.target.value)}
                placeholder="its own name"
                className="rounded-plexus border border-line bg-transparent px-2 py-1"
              />
            </label>
            <button disabled={busy} className="rounded-plexus border border-line px-3 py-1">
              Make the command
            </button>
          </form>
        )}
        {invite && (
          <div className="flex flex-col gap-2" data-testid="job-site-invite">
            <p>
              Run one of these on the machine, within 15 minutes. It asks for your Eugene password
              there: that is how Eugene knows the machine is yours. The service install needs an
              administrator (Windows) or root (Linux).
            </p>
            <h3 className="font-semibold">Windows (PowerShell)</h3>
            <pre className="whitespace-pre-wrap break-all rounded-plexus bg-soft p-2 text-xs">
              {invite.commands.windows}
            </pre>
            <CopyButton text={invite.commands.windows} />
            <h3 className="font-semibold">Linux</h3>
            <pre className="whitespace-pre-wrap break-all rounded-plexus bg-soft p-2 text-xs">
              {invite.commands.posix}
            </pre>
            <CopyButton text={invite.commands.posix} />
          </div>
        )}
      </section>
    </section>
  );
}

function since(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (seconds < 120) return `${seconds} s ago`;
  if (seconds < 7200) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

function Site({
  site,
  busy,
  act,
}: {
  site: JobSite;
  busy: boolean;
  act: (work: () => Promise<unknown>) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [writable, setWritable] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const base = `/api/job-sites/${encodeURIComponent(site.node)}`;
  return (
    <article
      className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
      data-testid={`job-site-${site.node}`}
    >
      <h2 className="font-semibold">{site.node}</h2>
      <p>
        {site.online ? "Online" : "Offline"} · last contact {since(site.lastContactAt)}
        {site.enabled && !site.ready && site.reason ? ` · ${site.reason}` : ""}
      </p>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={site.enabled}
          disabled={busy}
          onChange={(event) =>
            void act(() => post(`${base}/enabled`, { enabled: event.target.checked }))
          }
        />
        File support on this machine
      </label>
      {site.enabled && site.account && (
        <p className="text-muted">
          Give the OS account <strong>{site.account}</strong> permission to a folder on the machine,
          then add it here.
        </p>
      )}
      {site.folders.map((folder) => (
        <Folder key={folder.id} base={base} folder={folder} busy={busy} act={act} />
      ))}
      {site.enabled && (
        <form
          aria-label={`Add a folder on ${site.node}`}
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void act(async () => {
              await post(`${base}/folders`, { name, path, writable });
              setName("");
              setPath("");
              setWritable(false);
            });
          }}
        >
          <label className="flex flex-col gap-1">
            Folder name
            <input
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="rounded-plexus border border-line bg-transparent px-2 py-1"
            />
          </label>
          <label className="flex flex-col gap-1">
            Path on the machine
            <input
              required
              value={path}
              onChange={(event) => setPath(event.target.value)}
              className="rounded-plexus border border-line bg-transparent px-2 py-1"
            />
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={writable}
              onChange={(event) => setWritable(event.target.checked)}
            />
            Allow text writes
          </label>
          <button disabled={busy} className="rounded-plexus border border-line px-3 py-1">
            Add folder
          </button>
        </form>
      )}
      {leaving ? (
        <div className="flex flex-wrap gap-3">
          <p>
            Take {site.node} out of Eugene? Its files stay on it; Workbench stops reaching them.
          </p>
          <button
            disabled={busy}
            className="text-error"
            onClick={() => void act(() => post(`${base}/leave`))}
          >
            Take it out
          </button>
          <button disabled={busy} onClick={() => setLeaving(false)}>
            Keep it
          </button>
        </div>
      ) : (
        <button className="self-start text-error" onClick={() => setLeaving(true)}>
          Take this machine out
        </button>
      )}
    </article>
  );
}

function Folder({
  base,
  folder,
  busy,
  act,
}: {
  base: string;
  folder: JobSite["folders"][number];
  busy: boolean;
  act: (work: () => Promise<unknown>) => Promise<void>;
}) {
  const [people, setPeople] = useState(() =>
    folder.people.map((p) => `${p.name}${p.writable ? " (write)" : ""}`).join(", "),
  );
  const parse = () =>
    people
      .split(",")
      .map((entry) => entry.trim())
      .filter(Boolean)
      .map((entry) => {
        const write = /\(write\)$/i.test(entry);
        return { name: entry.replace(/\s*\(write\)$/i, "").trim(), writable: write };
      });
  return (
    <div className="flex flex-col gap-2 rounded-plexus border border-line p-2">
      <h3 className="font-semibold">
        {folder.name} · {folder.writable ? "read and write text" : "read only"}
      </h3>
      <p className="break-all text-muted">{folder.path}</p>
      <label className="flex flex-col gap-1">
        Who may use it (sign-in names, comma-separated; add &quot;(write)&quot; to let someone
        write). Include yourself to use it.
        <input
          value={people}
          onChange={(event) => setPeople(event.target.value)}
          className="rounded-plexus border border-line bg-transparent px-2 py-1"
        />
      </label>
      <div className="flex flex-wrap gap-3">
        <button
          disabled={busy}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() =>
            void act(() =>
              post(`${base}/folders/${encodeURIComponent(folder.id)}/people`, {
                people: parse(),
              }),
            )
          }
        >
          Save who may use it
        </button>
        <button
          disabled={busy}
          className="text-error"
          onClick={() =>
            void act(() => post(`${base}/folders/${encodeURIComponent(folder.id)}/remove`))
          }
        >
          Remove folder
        </button>
      </div>
    </div>
  );
}
