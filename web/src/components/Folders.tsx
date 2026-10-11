import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api, del, post } from "../lib/api";
import type { FolderGrants } from "../lib/types";
import { CopyButton } from "./CopyButton";
import { SiteLinkNote } from "./SiteLinkNote";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));
type Recipient = { sub: string; name: string; username: string | null };

/** After a question is answered the button that asked it is back: keyboard
 * focus goes there instead of to the page. */
const refocus = (id: string) => queueMicrotask(() => document.getElementById(id)?.focus());

export function Folders({ owner }: { owner: boolean }) {
  const uid = useId();
  const [data, setData] = useState<FolderGrants | null>(null);
  const [people, setPeople] = useState<Recipient[]>([]);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [subject, setSubject] = useState("");
  const [writable, setWritable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

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
          setLoadProblem(null);
        }
      })
      .catch((error) => {
        if (!stopped) setLoadProblem(problemOf(error));
      });
    return () => {
      stopped = true;
    };
  }, [owner, reload]);

  const canGrant = data?.localAvailable ?? data?.available;
  // Why the owner cannot grant, when Eugene says: the grant button is disabled, so the page says why.
  const reason =
    data && (!data.available || (owner && data.localAvailable === false)) ? data.reason : null;

  return (
    <section aria-label="Folder access" className="section">
      <h2 className="title-section">Folders · File tools</h2>
      <p className="help">
        Choose a granted folder in a chat&apos;s settings. Each listing, read or write needs your
        approval. File contents and results go to the selected model and stay in the chat.
      </p>
      <p className="help">
        These grants apply to the built-in file tools. Owner-installed local programs still share
        Workbench&apos;s OS account and its file access.
      </p>
      {loadProblem && (
        <div role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read the folder grants</span>
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
      {data === null && !loadProblem && !problem && (
        <p role="status" className="text-sm text-muted">
          Loading folders…
        </p>
      )}
      {reason && (
        <p id={`${uid}-reason`} className="notice">
          <Info size={16} aria-hidden />
          {reason}
        </p>
      )}
      {data?.nodeReason && (
        <p className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          Node folders: {data.nodeReason}
        </p>
      )}
      {owner && data?.manageUrl && (
        <a
          className="link self-start text-sm"
          href={data.manageUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Manage node folders and people in Eugene ↗
        </a>
      )}
      {data?.grants.length === 0 && <p className="empty">No folders granted yet.</p>}
      {data?.grants.map((grant) => {
        const person = people.find((p) => p.sub === grant.subject)?.name ?? grant.subject;
        const removeId = `${uid}-remove-${grant.id}`;
        return (
          <article key={grant.id} className="card">
            <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
              <h3 className="title-card">
                {grant.label ? `${grant.label} · ` : ""}
                {grant.name}
              </h3>
              {grant.writable ? (
                <span className="badge badge--warn">Read and write text</span>
              ) : (
                <span className="badge">Read only</span>
              )}
            </div>
            {(owner || grant.path) && (
              <dl className="facts">
                {owner && (
                  <>
                    <dt>Person</dt>
                    <dd>{person}</dd>
                  </>
                )}
                {grant.path && (
                  <>
                    <dt>Path</dt>
                    <dd className="flex flex-wrap items-center gap-x-2">
                      <span>{grant.path}</span>
                      <CopyButton text={grant.path} label={`the path of ${grant.name}`} />
                    </dd>
                  </>
                )}
              </dl>
            )}
            {grant.available === false && (
              <div className="notice notice--warn">
                <TriangleAlert size={16} aria-hidden />
                {grant.reason ? (
                  <div>
                    <span className="notice-title">This folder is unavailable now</span>
                    {grant.reason}
                  </div>
                ) : (
                  <div>This folder is currently unavailable.</div>
                )}
              </div>
            )}
            {grant.source === "node" &&
              grant.site &&
              data.grants.find((g) => g.source === "node" && g.site === grant.site) === grant && (
                <SiteLinkNote
                  site={grant.site}
                  label={grant.label ?? "that machine"}
                  linking={grant}
                  onChanged={() => setReload((n) => n + 1)}
                />
              )}
            {owner &&
              grant.source !== "node" &&
              (removing === grant.id ? (
                <div
                  role="group"
                  aria-label={`Remove access to ${grant.name}?`}
                  className="notice notice--error flex-col"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      setRemoving(null);
                      refocus(removeId);
                    }
                  }}
                >
                  <p>
                    Remove access to {grant.name}? Pending calls will fail. Files and saved chat
                    results remain.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      autoFocus
                      disabled={busy}
                      aria-label={`Keep access to ${grant.name}`}
                      className="btn"
                      onClick={() => {
                        setRemoving(null);
                        refocus(removeId);
                      }}
                    >
                      Keep access
                    </button>
                    <button
                      disabled={busy}
                      aria-label={`Remove access to ${grant.name}`}
                      className="btn btn-danger"
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
                  </div>
                </div>
              ) : (
                <button
                  id={removeId}
                  disabled={busy}
                  aria-label={`Remove grant for ${grant.name}`}
                  className="btn btn-quiet btn-danger -ms-2 self-start"
                  onClick={() => setRemoving(grant.id)}
                >
                  Remove grant
                </button>
              ))}
          </article>
        );
      })}
      {owner && (
        <form
          aria-label="Grant folder access"
          className="card card--add"
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
          <h3 className="title-card">Grant an existing folder</h3>
          <p className="help">
            Use a folder on the machine running Workbench. Its service account must already have OS
            permission to use it. Adding a grant does not change OS permissions.
          </p>
          <div className="field">
            <label htmlFor={`${uid}-name`} className="field-label">
              Folder name
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
            <label htmlFor={`${uid}-path`} className="field-label">
              Full folder path on Workbench&apos;s host
            </label>
            <input
              id={`${uid}-path`}
              required
              maxLength={4096}
              value={path}
              onChange={(e) => setPath(e.target.value)}
              spellCheck={false}
              className="control"
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-subject`} className="field-label">
              Person who can use it
            </label>
            <select
              id={`${uid}-subject`}
              required
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              aria-describedby={`${uid}-subject-help`}
              className="control"
            >
              <option value="">Choose a person</option>
              {people.map((person) => (
                <option key={person.sub} value={person.sub}>
                  {person.name}
                  {person.username ? ` (${person.username})` : ""}
                </option>
              ))}
            </select>
            <p id={`${uid}-subject-help`} className="help">
              People appear after signing into Workbench once. Grant the same folder separately to
              each person who should share it.
            </p>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={writable}
              onChange={(e) => setWritable(e.target.checked)}
            />
            <span>Allow creating and editing text files</span>
          </label>
          <p className="help">
            Lists and UTF-8 text files only (32 KiB / 16384 characters maximum; 8192 characters per
            write). No links, deletion or program execution. Edits need the hash from a prior read.
          </p>
          <p className="notice notice--warn">
            <TriangleAlert size={16} aria-hidden />
            Edits are not automatically undone.
          </p>
          <button
            disabled={busy || !canGrant}
            aria-describedby={!canGrant && reason ? `${uid}-reason` : undefined}
            className="btn btn-primary self-start"
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
  const [reload, setReload] = useState(0);
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
  }, [reload]);
  const grants = data?.grants.filter((g) => g.usable);
  // A job site's workspace runs what its rules allow without asking (J70);
  // Workbench's own folders still ask each time (J75).
  const onSites = grants?.some((g) => g.source === "node") ?? false;
  const local = grants?.some((g) => g.source !== "node") ?? false;
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="eyebrow mb-2 text-muted">Folders for this chat</legend>
      <p className="help">
        {onSites
          ? `${local ? "Workbench's own folders ask before each file operation. " : ""}On a job ` +
            "site, your rules there say which operations run without asking."
          : "Each file operation waits for approval."}{" "}
        Read contents go to this chat&apos;s model.
      </p>
      {problem && (
        <div role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          <div>
            <span className="notice-title">Could not read the folders</span>
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
      {data === null && !problem && (
        <p role="status" className="text-sm text-muted">
          Loading folders…
        </p>
      )}
      {data && !data.available && data.reason && (
        <p className="notice">
          <Info size={16} aria-hidden />
          {data.reason}
        </p>
      )}
      {data?.nodeReason && (
        <p className="notice notice--warn">
          <TriangleAlert size={16} aria-hidden />
          Node folders: {data.nodeReason}
        </p>
      )}
      {grants?.length === 0 && (
        <p className="empty">
          No folders assigned to you. The owner can assign node folders in Eugene&apos;s People
          page, or local folders in Tools.
        </p>
      )}
      {grants?.map((grant) => (
        <label key={grant.id} className="check">
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
          <span>
            {grant.label ? `${grant.label} · ` : ""}
            {grant.name}{" "}
            <span className="text-muted">
              · {grant.writable ? "Read and write text" : "Read only"}
            </span>
            {grant.available === false && (
              <>
                {" "}
                <span className="text-muted">· {grant.reason || "Unavailable"}</span>
              </>
            )}
          </span>
        </label>
      ))}
      {grants &&
        selected
          .filter((id) => !grants.some((g) => g.id === id))
          .map((id) => (
            <label key={id} className="check">
              <input
                type="checkbox"
                checked
                onChange={() => onChange(selected.filter((value) => value !== id))}
              />
              <span className="text-warn">Removed or unavailable folder — uncheck to clear it</span>
            </label>
          ))}
    </fieldset>
  );
}
