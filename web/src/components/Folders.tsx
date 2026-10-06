import { useEffect, useState } from "react";

import { api, del, post } from "../lib/api";
import type { FolderGrants } from "../lib/types";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));
type Recipient = { sub: string; name: string; username: string | null };

export function Folders({ owner }: { owner: boolean }) {
  const [data, setData] = useState<FolderGrants | null>(null);
  const [people, setPeople] = useState<Recipient[]>([]);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [subject, setSubject] = useState("");
  const [writable, setWritable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => {
    let stopped = false;
    void Promise.all([
      api<FolderGrants>("/api/folders"),
      owner ? api<{ people: Recipient[] }>("/api/folders/people") : Promise.resolve({ people: [] }),
    ])
      .then(([grants, recipients]) => {
        if (!stopped) {
          setData(grants);
          setPeople(recipients.people);
        }
      })
      .catch((error) => {
        if (!stopped) setProblem(problemOf(error));
      });
    return () => {
      stopped = true;
    };
  }, [owner]);

  return (
    <section aria-label="Folder access" className="flex flex-col gap-3 border-t border-line pt-4">
      <h2 className="font-semibold">Folders · File tools</h2>
      <p className="text-sm text-muted">
        Choose a granted folder in a chat&apos;s settings. Each listing, read or write needs your
        approval. File contents and results go to the selected model and stay in the chat.
      </p>
      <p className="text-sm text-muted">
        These grants apply to the built-in file tools. Owner-installed local programs still share
        Workbench&apos;s OS account and its file access.
      </p>
      {problem && (
        <p role="alert" className="text-sm text-error">
          {problem}
        </p>
      )}
      {data && !data.available && <p className="text-sm">{data.reason}</p>}
      {data?.nodeReason && <p className="text-sm text-muted">Node folders: {data.nodeReason}</p>}
      {owner && data?.manageUrl && (
        <a
          className="self-start text-sm underline"
          href={data.manageUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Manage node folders and people in Eugene ↗
        </a>
      )}
      {data?.grants.length === 0 && <p className="text-sm">No folders granted yet.</p>}
      {data?.grants.map((grant) => (
        <article
          key={grant.id}
          className="flex flex-col gap-2 rounded-plexus border border-line p-3 text-sm"
        >
          <h3 className="font-semibold">
            {grant.label ? `${grant.label} · ` : ""}
            {grant.name}
          </h3>
          <p>
            {grant.writable ? "Read and write text" : "Read only"}
            {owner && ` · ${people.find((p) => p.sub === grant.subject)?.name ?? grant.subject}`}
          </p>
          {grant.path && <p className="break-all text-muted">{grant.path}</p>}
          {grant.available === false && (
            <p>{grant.reason || "This folder is currently unavailable."}</p>
          )}
          {owner &&
            grant.source !== "node" &&
            (removing === grant.id ? (
              <div className="flex flex-wrap gap-3">
                <p>Remove access? Pending calls will fail. Files and saved chat results remain.</p>
                <button
                  disabled={busy}
                  className="text-error"
                  onClick={async () => {
                    setBusy(true);
                    setProblem(null);
                    try {
                      await del(`/api/folders/${grant.id}`);
                      setData(await api<FolderGrants>("/api/folders"));
                      setRemoving(null);
                    } catch (error) {
                      setProblem(problemOf(error));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Remove access
                </button>
                <button disabled={busy} onClick={() => setRemoving(null)}>
                  Keep access
                </button>
              </div>
            ) : (
              <button
                disabled={busy}
                className="self-start text-error"
                onClick={() => setRemoving(grant.id)}
              >
                Remove grant
              </button>
            ))}
        </article>
      ))}
      {owner && (
        <form
          aria-label="Grant folder access"
          className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setProblem(null);
            try {
              await post("/api/folders", { name, path, subject, writable });
              setData(await api<FolderGrants>("/api/folders"));
              setName("");
              setPath("");
              setWritable(false);
            } catch (error) {
              setProblem(problemOf(error));
            } finally {
              setBusy(false);
            }
          }}
        >
          <h3 className="font-medium">Grant an existing folder</h3>
          <p className="text-muted">
            Use a folder on the machine running Workbench. Its service account must already have OS
            permission to use it. Adding a grant does not change OS permissions.
          </p>
          <label className="flex flex-col gap-1">
            Folder name
            <input
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="rounded-plexus border border-line bg-soft px-2 py-1"
            />
          </label>
          <label className="flex flex-col gap-1">
            Full folder path on Workbench&apos;s host
            <input
              required
              maxLength={4096}
              value={path}
              onChange={(e) => setPath(e.target.value)}
              spellCheck={false}
              className="rounded-plexus border border-line bg-soft px-2 py-1"
            />
          </label>
          <label className="flex flex-col gap-1">
            Person who can use it
            <select
              required
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="rounded-plexus border border-line bg-soft px-2 py-1"
            >
              <option value="">Choose a person</option>
              {people.map((person) => (
                <option key={person.sub} value={person.sub}>
                  {person.name}
                  {person.username ? ` (${person.username})` : ""}
                </option>
              ))}
            </select>
          </label>
          <p className="text-muted">
            People appear after signing into Workbench once. Grant the same folder separately to
            each person who should share it.
          </p>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={writable}
              onChange={(e) => setWritable(e.target.checked)}
            />
            Allow creating and editing text files
          </label>
          <p className="text-muted">
            Lists and UTF-8 text files only (32 KiB / 16384 characters maximum; 8192 characters per
            write). No links, deletion or program execution. Edits need the hash from a prior read;
            they are not automatically undone.
          </p>
          <button
            disabled={busy || !(data?.localAvailable ?? data?.available)}
            className="self-start rounded-plexus bg-accent px-3 py-1 text-on-accent"
          >
            Grant folder access
          </button>
        </form>
      )}
    </section>
  );
}

export function FolderSelection({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
}) {
  const [data, setData] = useState<FolderGrants | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false;
    void api<FolderGrants>("/api/folders")
      .then((result) => {
        if (!stopped) setData(result);
      })
      .catch((error) => {
        if (!stopped) setProblem(problemOf(error));
      });
    return () => {
      stopped = true;
    };
  }, []);
  const grants = data?.grants.filter((g) => g.usable);
  return (
    <fieldset className="flex flex-col gap-2 text-sm">
      <legend className="font-medium">Folders for this chat</legend>
      <p className="text-muted">
        Each file operation waits for approval. Read contents go to this chat&apos;s model.
      </p>
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      {data && !data.available && <p>{data.reason}</p>}
      {data?.nodeReason && <p className="text-muted">Node folders: {data.nodeReason}</p>}
      {grants?.length === 0 && (
        <p>
          No folders assigned to you. The owner can assign node folders in Eugene&apos;s People
          page, or local folders in Tools.
        </p>
      )}
      {grants?.map((grant) => (
        <label key={grant.id} className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={selected.includes(grant.id)}
            disabled={!(grant.available ?? data?.available) && !selected.includes(grant.id)}
            onChange={(e) =>
              onChange(
                e.target.checked
                  ? [...selected, grant.id]
                  : selected.filter((id) => id !== grant.id),
              )
            }
          />
          {grant.label ? `${grant.label} · ` : ""}
          {grant.name} · {grant.writable ? "Read and write text" : "Read only"}
          {grant.available === false && (
            <span className="text-muted"> · {grant.reason || "Unavailable"}</span>
          )}
        </label>
      ))}
      {grants &&
        selected
          .filter((id) => !grants.some((g) => g.id === id))
          .map((id) => (
            <label key={id} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked
                onChange={() => onChange(selected.filter((value) => value !== id))}
              />
              Removed or unavailable folder — uncheck to clear it
            </label>
          ))}
    </fieldset>
  );
}
