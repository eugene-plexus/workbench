/**
 * Permission groups on the Settings page (row 70c). Eugene says who is in which group; a group
 * may set policies for its members and, in this Workbench, a memory server.
 *
 * - **Your groups**, for everyone in a group: each value a group sets, and each of their
 *   groups' memory servers, as "Set by your group", with no control to change it.
 * - **Group memory servers**, for the owner: attach a memory server to a group, or remove it.
 *   Everyone in the group gets it, and members cannot turn it off.
 */

import { CircleAlert, Info, Server, Users } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";

import { api, put } from "../lib/api";
import { failureWords } from "../lib/memory";
import type { GroupRef, GroupServersPage, GroupsPage, MemoryServerView } from "../lib/types";

const POLICY_WORDS: Record<string, string> = {
  queueBand: "Queue priority",
  summaryContextPercent: "Summarise long chats at",
};

/** Nothing sends a member's chats to a group's server until row 27; the page says so. */
const NOT_YET = "Chats go to it once group delivery is built.";

const BANDS: Record<string, string> = {
  interactive: "Interactive",
  background: "Background",
  sheddable: "Sheddable",
};

function valueWords(policy: string, value: string | number): string {
  if (policy === "summaryContextPercent") return `${value}%`;
  return BANDS[String(value)] ?? String(value);
}

function setBy(groups: GroupRef[]): string {
  const names = groups.map((g) => g.name).join(", ");
  return groups.length > 1 ? `Set by your groups: ${names}` : `Set by your group: ${names}`;
}

function ServerLine({ server }: { server: MemoryServerView }) {
  return (
    <p>
      <span className="font-medium">{server.name || "Memory server"}</span>{" "}
      <span className="text-muted wrap-anywhere">{server.url}</span>
      {server.lastError && (
        <span className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          Not reached: {server.lastError}
        </span>
      )}
    </p>
  );
}

/** What the signed-in person's groups set for them: read only. */
export function YourGroups() {
  const uid = useId();
  const [data, setData] = useState<GroupsPage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api<GroupsPage>("/api/groups")
      .then((page) => {
        setData(page);
        setProblem(null);
      })
      .catch((failure) => setProblem(failureWords(failure)));
  }, []);

  if (problem) {
    return (
      <p role="alert" className="notice notice--error">
        <CircleAlert size={16} aria-hidden />
        {problem}
      </p>
    );
  }
  if (!data || !data.known || data.groups.length === 0) return null;
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="your-groups" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Users size={18} aria-hidden />
        Your groups
      </h2>
      <p className="help">
        You are in {data.groups.map((g) => g.name).join(", ")}. Eugene&rsquo;s owner sets what a
        group gives its members. You cannot change it here.
      </p>
      {data.policies.length > 0 && (
        <ul className="flex flex-col gap-1 text-sm">
          {data.policies.map((p) => (
            <li key={p.policy} data-testid={`group-policy-${p.policy}`}>
              {POLICY_WORDS[p.policy] ?? p.policy}: {valueWords(p.policy, p.value)}.{" "}
              <span className="text-muted">{setBy(p.setBy)}</span>
            </li>
          ))}
        </ul>
      )}
      {data.servers.length > 0 && (
        <div className="flex flex-col gap-2 text-sm">
          <h3 className="font-medium">
            <Server size={16} aria-hidden /> Your groups&rsquo; memory servers
          </h3>
          {data.servers.map(({ group, server }) => (
            <div key={group.id} data-testid={`group-server-${group.id}`}>
              <ServerLine server={server} />
              <p className="help">
                {setBy([group])}. It is for everyone in the group, and you cannot turn it off.
              </p>
              <p className="help">{NOT_YET}</p>
            </div>
          ))}
          {!data.serversAllowed && data.why && (
            <p className="notice">
              <Info size={16} aria-hidden />
              {data.why}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function GroupServer({
  group,
  allowed,
  onChanged,
}: {
  group: GroupRef & { server: MemoryServerView | null };
  allowed: boolean;
  onChanged: (page: GroupServersPage) => void;
}) {
  const uid = useId();
  const [adding, setAdding] = useState(false);
  const [asking, setAsking] = useState(false);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const path = `/api/groups/${encodeURIComponent(group.id)}/memory-server`;

  async function connect(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      onChanged(await put<GroupServersPage>(path, { url: url.trim(), token }));
      setAdding(false);
      setToken("");
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setProblem(null);
    try {
      onChanged(await api<GroupServersPage>(path, { method: "DELETE" }));
      setAsking(false);
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid={`group-servers-${group.id}`} className="flex flex-col gap-1 text-sm">
      <p className="font-medium">{group.name}</p>
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {group.server ? (
        <>
          <ServerLine server={group.server} />
          <p data-testid="group-server-saved" className="help">
            Saved. {NOT_YET}
          </p>
          {!asking ? (
            <div>
              <button
                type="button"
                className="btn btn-quiet"
                disabled={busy}
                onClick={() => setAsking(true)}
              >
                Remove
              </button>
            </div>
          ) : (
            <div role="alertdialog" aria-labelledby={`${uid}-ask`} className="notice">
              <div>
                <span id={`${uid}-ask`} className="notice-title">
                  Remove {group.name}&rsquo;s memory server?
                </span>
                Nothing on the server is deleted.
                <div className="mt-2 flex flex-wrap gap-2">
                  <button type="button" className="btn" autoFocus onClick={() => setAsking(false)}>
                    Keep it
                  </button>
                  <button
                    type="button"
                    className="btn btn-quiet"
                    disabled={busy}
                    onClick={() => void remove()}
                  >
                    Remove it
                  </button>
                </div>
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="help">No memory server.</p>
      )}
      {allowed && !group.server && !adding && (
        <div>
          <button type="button" className="btn" onClick={() => setAdding(true)}>
            Add a memory server
          </button>
        </div>
      )}
      {adding && (
        <form className="flex flex-col gap-2" onSubmit={(e) => void connect(e)}>
          <p data-testid="group-server-warning" className="notice">
            <Info size={16} aria-hidden />
            Everyone in {group.name} gets this server, and members cannot turn it off. Each member
            sees it in their Settings. {NOT_YET}
          </p>
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
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="control max-w-full"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn btn-primary" disabled={busy || !url.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

/** The owner's: a memory server per permission group. */
export function GroupServers() {
  const uid = useId();
  const [data, setData] = useState<GroupServersPage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api<GroupServersPage>("/api/groups/manage")
      .then((page) => setData(page))
      .catch((failure) => setProblem(failureWords(failure)));
  }, []);

  const shown = problem ?? data?.problem ?? null;
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="group-servers" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Users size={18} aria-hidden />
        Group memory servers
      </h2>
      <p className="help">
        A memory server for everyone in a permission group. Eugene&rsquo;s People page sets who is
        in each group.
      </p>
      {shown && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {shown}
        </p>
      )}
      {data && !data.allowed && data.why && (
        <p className="notice">
          <Info size={16} aria-hidden />
          {data.why}
        </p>
      )}
      {data && data.groups.length === 0 && !data.problem && (
        <p className="help">Eugene has no group that gives this Workbench.</p>
      )}
      {data?.groups.map((group) => (
        <GroupServer key={group.id} group={group} allowed={data.allowed} onChanged={setData} />
      ))}
      {data && data.gone.length > 0 && (
        <div data-testid="group-servers-gone" className="flex flex-col gap-2">
          <p className="help">These groups are no longer in Eugene, or no longer give Workbench:</p>
          {data.gone.map((gone) => (
            <GroupServer
              key={gone.id}
              group={{ id: gone.id, name: gone.id, server: gone.server }}
              allowed={false}
              onChanged={setData}
            />
          ))}
        </div>
      )}
    </section>
  );
}
