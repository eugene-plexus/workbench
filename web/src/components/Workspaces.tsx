import { useEffect, useState } from "react";

import { post } from "../lib/api";
import type {
  Decision,
  JobSite,
  JobSiteWorkspace,
  JobSiteWorkspaceDetail,
  SiteCommands,
} from "../lib/types";
import { CopyButton } from "./CopyButton";

type Act = (work: () => Promise<unknown>) => Promise<void>;

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** The words for each decision (J70), as a person reads them. */
export const DECISIONS: { value: Decision; label: string }[] = [
  { value: "allow", label: "Without asking" },
  { value: "ask", label: "Ask me each time" },
  { value: "deny", label: "Never" },
];

/** Commands are signed each time or never (J47, J88): never "without asking". */
export const COMMAND_DECISIONS: { value: Decision; label: string }[] = [
  { value: "ask", label: "Ask me each time (my signature)" },
  { value: "deny", label: "Never" },
];

/** One deny pattern a line, blanks dropped, each once (`SiteDenyPattern`). */
export function parsePatterns(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}

/** Why a pattern list will be refused, or null: patterns only hide. */
export function patternProblem(patterns: string[]): string | null {
  if (patterns.length > 64) return "Use at most 64 patterns.";
  const bad = patterns.find((p) => p.startsWith("!") || p.length > 256);
  return bad ? `${bad} cannot be used: a pattern only hides, and is at most 256 characters.` : null;
}

function DecisionSelect({
  label,
  value,
  onChange,
  only,
  choices = DECISIONS,
}: {
  label: string;
  value: Decision;
  onChange: (value: Decision) => void;
  only?: Decision;
  choices?: { value: Decision; label: string }[];
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="min-w-28">{label}</span>
      <select
        aria-label={label}
        value={only ?? value}
        disabled={only !== undefined}
        onChange={(event) => onChange(event.target.value as Decision)}
        className="rounded-plexus border border-line bg-soft px-2 py-1"
      >
        {choices.map((d) => (
          <option key={d.value} value={d.value}>
            {d.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Your workspaces on a machine (2b.3b, J67-J70): folders your own account
 * there opens, each with your rules, and paths no tool may touch. A change
 * that gives more waits for your own key on the machine (J68). For the
 * machine's owner, whom each is shared with, and their rules there (J69).
 */
export function Workspaces({
  site,
  base,
  busy,
  act,
  owner,
}: {
  site: JobSite;
  base: string;
  busy: boolean;
  act: Act;
  owner: boolean;
}) {
  const [details, setDetails] = useState<Map<string, JobSiteWorkspaceDetail> | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [reads, setReads] = useState(0);
  const workspaces = site.workspaces ?? [];
  const ids = workspaces.map((w) => w.id).join(",");

  // Paths and hidden patterns are read live from the machine, never kept by
  // Eugene (J76): each time the set of workspaces changes, and after a save,
  // so the page shows what is in effect there, not what was asked for.
  useEffect(() => {
    if (!ids) return;
    let current = true;
    setProblem(null);
    post<{ workspaces: JobSiteWorkspaceDetail[] }>(`${base}/workspaces/list`)
      .then((listed) => {
        if (current) setDetails(new Map(listed.workspaces.map((w) => [w.id, w])));
      })
      .catch((error: unknown) => {
        if (current) setProblem(problemOf(error));
      });
    return () => {
      current = false;
    };
  }, [base, ids, reads]);

  return (
    <section
      aria-label={`Your workspaces on ${site.label}`}
      className="flex flex-col gap-2"
      data-testid={`workspaces-${site.id}`}
    >
      <h3 className="font-semibold">Your workspaces on {site.label}</h3>
      <p className="text-muted">
        Folders your own account on {site.label} opens. Your rules say what Workbench may do in each
        without asking you; paths you hide are left out of every listing and search.
      </p>
      {workspaces.length === 0 && <p className="text-muted">None yet.</p>}
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      {workspaces.map((workspace) => (
        <Workspace
          key={workspace.id}
          base={base}
          workspace={workspace}
          detail={details?.get(workspace.id)}
          busy={busy}
          act={act}
          owner={owner}
          solo={site.sharing === false}
          commands={site.commands}
          onSaved={() => setReads((n) => n + 1)}
        />
      ))}
      <AddWorkspace base={base} label={site.label} busy={busy} act={act} commands={site.commands} />
    </section>
  );
}

function Workspace({
  base,
  workspace,
  detail,
  busy,
  act,
  owner,
  solo,
  commands,
  onSaved,
}: {
  base: string;
  workspace: JobSiteWorkspace;
  detail?: JobSiteWorkspaceDetail;
  busy: boolean;
  act: Act;
  owner: boolean;
  solo: boolean;
  commands?: SiteCommands;
  onSaved: () => void;
}) {
  // A person's edits until they save; otherwise the rules in effect, read
  // live from the machine. A change that gives more waits for their key
  // (J68), so it is shown only once the machine applies it.
  const [read, setRead] = useState<Decision | null>(null);
  const [change, setChange] = useState<Decision | null>(null);
  const [command, setCommand] = useState<Decision | null>(null);
  const [patterns, setPatterns] = useState<string | null>(null);
  const rules = detail?.rules ?? workspace.rules;
  const shownRead = read ?? rules.read;
  const shownChange = change ?? rules.change;
  const shownCommand = command ?? rules.command ?? "deny";
  const path = `${base}/workspaces/${encodeURIComponent(workspace.id)}`;
  const deny = patterns ?? (detail?.deny ?? []).join("\n");
  const saved = () => {
    setRead(null);
    setChange(null);
    setCommand(null);
    setPatterns(null);
    onSaved();
  };
  const parsed = parsePatterns(deny);
  const bad = patternProblem(parsed);
  return (
    <div
      className="flex flex-col gap-2 rounded-plexus border border-line p-2"
      data-testid={`workspace-${workspace.id}`}
    >
      <h4 className="font-semibold">
        {workspace.name}
        {workspace.writable ? "" : " · read only"}
      </h4>
      {detail && (
        <p className="flex flex-wrap items-center gap-2 text-muted">
          <span className="break-all">{detail.path}</span>
          <CopyButton text={detail.path} label={`the path of ${workspace.name}`} />
        </p>
      )}
      <DecisionSelect label="Read and search" value={shownRead} onChange={setRead} />
      <DecisionSelect
        label="Change files"
        value={shownChange}
        onChange={setChange}
        only={workspace.writable ? undefined : "deny"}
      />
      {commands && (
        <CommandRule
          value={shownCommand}
          onChange={setCommand}
          writable={workspace.writable}
          commands={commands}
        />
      )}
      <label className="flex flex-col gap-1">
        Paths to hide, one a line (like .gitignore: .env, secrets/, *.pem)
        <textarea
          rows={3}
          value={deny}
          disabled={detail === undefined}
          placeholder={detail ? "" : `Reading them from the machine…`}
          onChange={(event) => setPatterns(event.target.value)}
          className="rounded-plexus border border-line bg-transparent px-2 py-1 font-mono"
          aria-label={`Paths to hide in ${workspace.name}`}
        />
      </label>
      {bad && (
        <p role="alert" className="text-error">
          {bad}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <button
          aria-label={`Save rules for ${workspace.name}`}
          disabled={busy || Boolean(bad) || detail === undefined}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() =>
            void act(() =>
              post(`${path}/rules`, {
                rules: {
                  read: shownRead,
                  change: workspace.writable ? shownChange : "deny",
                  // Never a choice the person was not shown: a machine that
                  // runs no commands keeps them denied.
                  command: workspace.writable && commands ? shownCommand : "deny",
                },
                deny: parsed,
              }),
            ).then(saved)
          }
        >
          Save rules
        </button>
        <button
          aria-label={`Remove workspace ${workspace.name}`}
          disabled={busy}
          className="text-error"
          onClick={() => void act(() => post(`${path}/remove`))}
        >
          Remove workspace
        </button>
      </div>
      {owner && !solo && (
        <Sharing
          base={path}
          shared={detail?.people ?? workspace.people}
          writable={workspace.writable}
          workspaceId={workspace.id}
          workspaceName={workspace.name}
          busy={busy}
          act={act}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}

/** Whom the machine's owner shares a workspace with, and each one's rules. */
function Sharing({
  base,
  shared,
  writable,
  workspaceId,
  workspaceName,
  busy,
  act,
  onSaved,
}: {
  base: string;
  shared: JobSiteWorkspace["people"];
  writable: boolean;
  workspaceId: string;
  workspaceName: string;
  busy: boolean;
  act: Act;
  onSaved: () => void;
}) {
  type Entry = { name: string; read: Decision; change: Decision };
  // The owner's edits until they save; otherwise whom it is shared with now,
  // read live from the machine (a share that gives more waits for the key).
  const [edited, setEdited] = useState<Entry[] | null>(null);
  const inEffect = shared.map((p) => ({ name: p.name, read: p.read, change: p.change }));
  const people = edited ?? inEffect;
  const setPeople = (change: (old: Entry[]) => Entry[]) =>
    setEdited((old) => change(old ?? inEffect));
  const [adding, setAdding] = useState("");
  return (
    <div className="flex flex-col gap-2" data-testid={`sharing-${workspaceId}`}>
      <p>Shared with:</p>
      {people.length === 0 && <p className="text-muted">Nobody.</p>}
      {people.map((person, index) => (
        <fieldset key={person.name} className="flex flex-wrap items-center gap-3">
          <legend>{person.name}</legend>
          <DecisionSelect
            label={`${person.name} reads and searches`}
            value={person.read}
            onChange={(read) =>
              setPeople((old) => old.map((p, i) => (i === index ? { ...p, read } : p)))
            }
          />
          <DecisionSelect
            label={`${person.name} changes files`}
            value={person.change}
            only={writable ? undefined : "deny"}
            onChange={(change) =>
              setPeople((old) => old.map((p, i) => (i === index ? { ...p, change } : p)))
            }
          />
          <button
            type="button"
            className="text-error"
            onClick={() => setPeople((old) => old.filter((_, i) => i !== index))}
          >
            Stop sharing with {person.name}
          </button>
        </fieldset>
      ))}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1">
          Share with (how they sign in)
          <input
            value={adding}
            onChange={(event) => setAdding(event.target.value)}
            className="rounded-plexus border border-line bg-transparent px-2 py-1"
          />
        </label>
        <button
          type="button"
          aria-label={`Add to those sharing ${workspaceName}`}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() => {
            const name = adding.trim();
            if (name && !people.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
              setPeople((old) => [...old, { name, read: "allow", change: "deny" }]);
            }
            setAdding("");
          }}
        >
          Add
        </button>
      </div>
      {edited !== null && (
        <p role="status" className="text-muted">
          Not saved yet.
        </p>
      )}
      <button
        aria-label={`Save sharing for ${workspaceName}`}
        disabled={busy}
        className="self-start rounded-plexus border border-line px-3 py-1"
        onClick={() =>
          void act(() =>
            post(`${base}/people`, {
              people: people.map((p) => ({
                ...p,
                change: writable ? p.change : "deny",
              })),
            }),
          ).then(() => {
            setEdited(null);
            onSaved();
          })
        }
      >
        Save sharing
      </button>
    </div>
  );
}

/** Running commands in a workspace (2b.4, J88): signed each time, or never. */
function CommandRule({
  value,
  onChange,
  writable,
  commands,
}: {
  value: Decision;
  onChange: (value: Decision) => void;
  writable: boolean;
  commands: SiteCommands;
}) {
  return (
    <div className="flex flex-col gap-1">
      <DecisionSelect
        label="Run commands"
        value={value}
        onChange={onChange}
        only={writable ? undefined : "deny"}
        choices={COMMAND_DECISIONS}
      />
      <p className="text-xs text-muted">
        A command runs as your own account and can do whatever it can, not only in this folder.
        {commands.allowed ? "" : ` ${commands.reason ?? "This machine does not run commands now."}`}
      </p>
    </div>
  );
}

function AddWorkspace({
  base,
  label,
  busy,
  act,
  commands,
}: {
  base: string;
  label: string;
  busy: boolean;
  act: Act;
  commands?: SiteCommands;
}) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [writable, setWritable] = useState(true);
  const [read, setRead] = useState<Decision>("allow");
  const [change, setChange] = useState<Decision>("ask");
  const [command, setCommand] = useState<Decision>("ask");
  const [deny, setDeny] = useState("");
  const parsed = parsePatterns(deny);
  const bad = patternProblem(parsed);
  return (
    <form
      aria-label={`Add a workspace on ${label}`}
      className="flex flex-col gap-2 rounded-plexus border border-dashed border-line p-2"
      onSubmit={(event) => {
        event.preventDefault();
        void act(async () => {
          const value = await post(`${base}/workspaces`, {
            name,
            path,
            writable,
            rules: {
              read,
              change: writable ? change : "deny",
              command: writable && commands ? command : "deny",
            },
            deny: parsed,
          });
          setName("");
          setPath("");
          setDeny("");
          return value;
        });
      }}
    >
      <h4 className="font-semibold">Add a workspace</h4>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          Name
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded-plexus border border-line bg-transparent px-2 py-1"
          />
        </label>
        <label className="flex flex-col gap-1">
          Path on {label}
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
          Files in it may be changed
        </label>
      </div>
      <DecisionSelect label="Read and search" value={read} onChange={setRead} />
      <DecisionSelect
        label="Change files"
        value={change}
        onChange={setChange}
        only={writable ? undefined : "deny"}
      />
      {commands && (
        <CommandRule
          value={command}
          onChange={setCommand}
          writable={writable}
          commands={commands}
        />
      )}
      <label className="flex flex-col gap-1">
        Paths to hide, one a line (optional)
        <textarea
          rows={2}
          value={deny}
          onChange={(event) => setDeny(event.target.value)}
          className="rounded-plexus border border-line bg-transparent px-2 py-1 font-mono"
        />
      </label>
      {bad && (
        <p role="alert" className="text-error">
          {bad}
        </p>
      )}
      <p className="text-muted">
        It waits on {label} until you approve it with your own key there, or with your passkey here.
      </p>
      <button
        disabled={busy || Boolean(bad)}
        className="self-start rounded-plexus border border-line px-3 py-1"
      >
        Add workspace
      </button>
    </form>
  );
}
