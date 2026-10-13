import {
  ArrowLeft,
  Ban,
  Check,
  CircleAlert,
  Info,
  KeyRound,
  Laptop,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";

import { api, post } from "../lib/api";
import type {
  HeldChange,
  HeldList,
  JobSite,
  JobSiteInvite,
  JobSiteList,
  JobSiteServer,
  PasskeyContext,
  SiteAuditEntry,
} from "../lib/types";
import {
  looksLikeCode,
  makePasskey,
  pairingMac,
  passkeyProblem,
  passkeysHere,
  rememberPasskey,
  rememberedPasskey,
  signEnvelope,
} from "../lib/passkeys";
import { CopyButton } from "./CopyButton";
import { Workspaces } from "./Workspaces";

const problemOf = (error: unknown) => (error instanceof Error ? error.message : String(error));

const isHeld = (value: unknown): value is HeldChange =>
  typeof value === "object" && value !== null && (value as HeldChange).held === true;

/** After a question is answered the button that asked it is back: keyboard
 * focus goes there instead of to the page. */
const refocus = (id: string) => queueMicrotask(() => document.getElementById(id)?.focus());

/** Job sites (your machines): the machines whose files you use from here.
 * Yours alone: only you say who may use their folders, yourself included. */
export function JobSites({ onClose, sub }: { onClose: () => void; sub?: string }) {
  const uid = useId();
  const [data, setData] = useState<JobSiteList | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [loadProblem, setLoadProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [invite, setInvite] = useState<JobSiteInvite | null>(null);
  const [machine, setMachine] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api<JobSiteList>("/api/job-sites"));
      setLoadProblem(null);
    } catch (error) {
      setLoadProblem(problemOf(error));
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
    setNotice(null);
    try {
      const value = await work();
      // Held at the machine for its owner's key (J14a): not a refusal.
      if (isHeld(value)) setNotice(value.message);
      await load();
    } catch (error) {
      setProblem(problemOf(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label="Job sites" className="page">
      <div className="page-col">
        <header className="page-head">
          <h1 id="view-heading" tabIndex={-1} className="title-page">
            <Laptop size={20} aria-hidden />
            Job sites (your machines)
          </h1>
          <button className="btn" onClick={onClose}>
            <ArrowLeft size={14} aria-hidden />
            Back to chat
          </button>
        </header>
        <p className="help">
          A job site is a machine of yours that Workbench can work on from anywhere, in the folders
          you choose and under your rules for what runs without asking. It connects out to Eugene;
          nothing connects to it.
        </p>
        {loadProblem && (
          <div role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            <div>
              <span className="notice-title">Could not read job sites</span>
              {loadProblem}
              <span className="block text-muted">This page asks again every few seconds.</span>
            </div>
          </div>
        )}
        {problem && (
          <p role="alert" className="notice notice--error">
            <CircleAlert size={16} aria-hidden />
            {problem}
          </p>
        )}
        {notice && (
          <div role="status" className="notice" data-testid="job-site-held">
            <KeyRound size={16} aria-hidden />
            <div>
              <span className="notice-title">Needs your key</span>
              {notice}
            </div>
          </div>
        )}
        {data === null && !loadProblem && !problem && (
          <p role="status" className="text-sm text-muted">
            Loading job sites…
          </p>
        )}
        {data?.sites.length === 0 && <p className="empty">You have no job sites yet.</p>}
        {data?.sites.map((site) =>
          site.role === "linked" ? (
            <LinkedSite
              key={site.id}
              site={site}
              sub={sub}
              busy={busy}
              act={act}
              passkeys={data.passkeys}
            />
          ) : (
            <Site
              key={site.id}
              site={site}
              sub={sub}
              busy={busy}
              act={act}
              passkeys={data.passkeys}
            />
          ),
        )}
        <section aria-labelledby={`${uid}-add`} className="card card--add">
          <h2 id={`${uid}-add`} className="title-card">
            Add a job site
          </h2>
          {data && !data.canInvite ? (
            <p className="notice">
              <Info size={16} aria-hidden />
              Eugene does not know an address that machines join through yet, so a machine can be
              added only from Eugene&apos;s console.
            </p>
          ) : (
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void act(async () =>
                  setInvite(
                    await post<JobSiteInvite>("/api/job-sites/invite", {
                      label: machine.trim() || null,
                    }),
                  ),
                );
              }}
            >
              <div className="field min-w-48 flex-1">
                <label htmlFor={`${uid}-machine`} className="field-label">
                  Machine name (optional)
                </label>
                <input
                  id={`${uid}-machine`}
                  value={machine}
                  onChange={(event) => setMachine(event.target.value)}
                  placeholder="its own name"
                  className="control"
                />
              </div>
              <button disabled={busy} className="btn btn-primary">
                Make the command
              </button>
            </form>
          )}
          {invite && (
            <div className="flex flex-col gap-3" data-testid="job-site-invite">
              <ol className="help list-decimal space-y-1 pl-5 marker:text-muted">
                <li>
                  The machine needs Eugene installed and joined as a node first (a standalone
                  install comes later).
                </li>
                <li>
                  Run one of these on the machine, within 15 minutes. The service install needs an
                  administrator (Windows) or root (Linux).
                </li>
                <li>It asks for your Eugene password there, to prove the machine is yours.</li>
              </ol>
              <h3 className="title-sub">Windows (PowerShell)</h3>
              <Command text={invite.commands.windows} label="the Windows command" />
              <h3 className="title-sub">Linux</h3>
              <Command text={invite.commands.posix} label="the Linux command" />
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

/** A command to run at a machine: in a block that wraps rather than scrolls,
 * with Copy beside it (under it on a phone). */
function Command({ text, label }: { text: string; label: string }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-2">
      <pre className="min-w-0 flex-1 whitespace-pre-wrap break-all rounded-plexus border border-line bg-soft p-3 font-mono text-[0.8125rem] leading-5">
        {text}
      </pre>
      <CopyButton text={text} label={label} />
    </div>
  );
}

function since(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  if (seconds < 120) return `${seconds} s ago`;
  if (seconds < 7200) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}

type Act = (work: () => Promise<unknown>) => Promise<void>;

/** A machine's state in words, a dot and a tint: online and ready is the only green. */
function SiteState({ site }: { site: JobSite }) {
  const [words, tone] = !site.online
    ? ["Offline", " badge--warn"]
    : site.ready
      ? ["Online", " badge--ok"]
      : ["Online, not ready", " badge--warn"];
  return (
    <span className={`badge${tone}`}>
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
      {words}
    </span>
  );
}

/** A machine's title row and when it was last heard from. */
function SiteHead({ site, title }: { site: JobSite; title: string }) {
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <h2 className="title-card">{title}</h2>
        <SiteState site={site} />
      </div>
      <p className="text-xs text-muted">
        Last contact {since(site.lastContactAt)}
        {!site.ready && site.reason ? ` · ${site.reason}` : ""}
      </p>
    </>
  );
}

const LINUX_LINK =
  "curl -fsSL https://raw.githubusercontent.com/eugene-plexus/specs/main/scripts/install.sh | " +
  "sudo sh -s -- --site-link --person NAME";

/** Who runs as whom on a machine: the owner's own line, the others' links with
 * a way to take each away, and how a person links their own account. */
function Linking({
  site,
  sub,
  base,
  busy,
  act,
}: {
  site: JobSite;
  sub?: string;
  base: string;
  busy: boolean;
  act: Act;
}) {
  const solo = site.sharing === false;
  const links = site.links;
  const names = new Map<string, string>();
  for (const w of site.workspaces ?? []) for (const p of w.people) names.set(p.person, p.name);
  for (const entry of site.servers ?? []) for (const p of entry.people) names.set(p.person, p.name);
  const own = links?.find((l) => l.subject === sub);
  const others = (links ?? []).filter((l) => l.subject !== sub);
  const notThere = (l: { reason?: string | null }) => l.reason || "not signed in there now";
  return (
    <div className="flex flex-col gap-3 text-sm" data-testid={`linking-${site.id}`}>
      {solo && <p>This machine serves only you (macOS has no folder boundary yet).</p>}
      {!solo && links && own && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="min-w-0 wrap-anywhere">
            Your calls here run as <strong>{own.accountName}</strong>
            {own.available ? "" : ` · ${notThere(own)}`}
          </p>
          <button
            type="button"
            disabled={busy}
            aria-label={`Remove my link on ${site.label}`}
            className="btn btn-quiet btn-danger"
            onClick={() => void act(() => post(`${base}/links/remove`, {}))}
          >
            Remove my link
          </button>
        </div>
      )}
      {!solo && links && !own && sub && (
        <p>
          You have not linked your own account on {site.label} yet.
          {site.linkPage
            ? ` To link it, open ${site.linkPage} at the machine and sign in.`
            : site.linkPage === null
              ? " To link it, run the command below on the machine."
              : ""}
        </p>
      )}
      {!solo && others.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label={`Who else has linked on ${site.label}`}>
          {others.map((link) => {
            const who = names.get(link.subject) ?? "Someone";
            return (
              <li key={link.subject} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="min-w-0 wrap-anywhere">
                  {who} runs as {link.accountName}
                  {link.available ? "" : ` · ${notThere(link)}`}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Remove link for ${who} (${link.accountName})`}
                  className="btn btn-quiet btn-danger"
                  onClick={() =>
                    void act(() => post(`${base}/links/remove`, { person: link.subject }))
                  }
                >
                  Remove link
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {!solo && site.linkPage && (
        <p className="help">
          People link their own account at the machine: on {site.label}, open {site.linkPage} and
          sign in.
        </p>
      )}
      {!solo && links && site.linkPage === null && (
        <div className="flex flex-col gap-2">
          <p className="help">On a Linux machine, people link with:</p>
          <Command text={LINUX_LINK} label="the Linux link command" />
          <p className="help">
            Run it on the machine as an administrator, naming the person as they sign in.
          </p>
        </div>
      )}
    </div>
  );
}

function clockTime(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime())
    ? iso
    : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * Your window on a machine, and whether it runs commands (J14b, 2b.4). A
 * window you open with your key lets the tools your rules allow run without
 * your signature each, for an hour (J81, J82); closing it needs nothing.
 * Commands run only where an administrator allowed them at the machine
 * (J89); its owner may turn them off from here (J30).
 */
export function Calls({
  site,
  sub,
  base,
  busy,
  act,
  owner,
}: {
  site: JobSite;
  sub?: string;
  base: string;
  busy: boolean;
  act: Act;
  owner: boolean;
}) {
  const uid = useId();
  const [confirming, setConfirming] = useState(false);
  const commands = site.commands;
  if (!commands) return null;
  const mine = site.links?.find((link) => link.subject === sub);
  const until = mine?.windowUntil;
  const turnOff = `${uid}-turn-off`;
  return (
    <section aria-label={`Signed calls on ${site.label}`} className="subsection">
      <h3 className="title-sub">Your signature on {site.label}</h3>
      {until ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <p className="min-w-0">
            Your window is open until {clockTime(until)}: the tools your rules allow there run
            without your signature each time.
          </p>
          <button
            disabled={busy}
            aria-label={`Close it now: your window on ${site.label}`}
            className="btn"
            onClick={() => void act(() => post(`${base}/window/close`))}
          >
            Close it now
          </button>
        </div>
      ) : (
        <p className="help">
          No window is open. One signature opens an hour of the tools your rules allow there.
          Anything your rules ask about, and every command, needs your signature each time.
        </p>
      )}
      {commands.allowed ? (
        <div className="flex flex-col gap-3 text-sm">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <p className="min-w-0">
              Commands may run on {site.label}, each with your signature, in your workspaces whose
              rules ask about them.
            </p>
            {owner && !confirming && (
              <button
                id={turnOff}
                disabled={busy}
                aria-label={`Turn commands off on ${site.label}`}
                className="btn btn-quiet btn-danger -ms-2"
                onClick={() => setConfirming(true)}
              >
                Turn commands off
              </button>
            )}
          </div>
          {owner && confirming && (
            <div
              role="group"
              aria-label={`Turn commands off on ${site.label}?`}
              className="notice notice--error flex-col"
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setConfirming(false);
                  refocus(turnOff);
                }
              }}
            >
              <p>Only an administrator at {site.label} can turn them back on.</p>
              <div className="flex flex-wrap gap-2">
                <button
                  autoFocus
                  aria-label={`Keep them on ${site.label}`}
                  className="btn"
                  onClick={() => {
                    setConfirming(false);
                    refocus(turnOff);
                  }}
                >
                  Keep them
                </button>
                <button
                  disabled={busy}
                  aria-label={`Turn them off on ${site.label}`}
                  className="btn btn-danger"
                  onClick={() =>
                    void act(() => post(`${base}/commands/withdraw`)).then(() =>
                      setConfirming(false),
                    )
                  }
                >
                  Turn them off
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="help">{commands.reason ?? `Commands do not run on ${site.label}.`}</p>
      )}
    </section>
  );
}

/** A machine you linked your own account on and do not own (2b.3b): only
 * your own there -- your link, your keys, your workspaces and your lines of
 * its audit log. Its owner sees none of your workspaces. */
function LinkedSite({
  site,
  sub,
  busy,
  act,
  passkeys,
}: {
  site: JobSite;
  sub?: string;
  busy: boolean;
  act: Act;
  passkeys?: PasskeyContext;
}) {
  const base = `/api/job-sites/${encodeURIComponent(site.id)}`;
  return (
    <article className="card" data-testid={`job-site-${site.id}`}>
      <SiteHead site={site} title={`${site.label} · linked`} />
      <Linking site={site} sub={sub} base={base} busy={busy} act={act} />
      <Signing site={site} own />
      {site.signing?.passkeys && passkeys && (
        <Passkeys site={site} base={base} context={passkeys} busy={busy} act={act} />
      )}
      <Calls site={site} sub={sub} base={base} busy={busy} act={act} owner={false} />
      <Workspaces site={site} base={base} busy={busy} act={act} owner={false} />
      <Audit base={base} label={site.label} />
    </article>
  );
}

function Site({
  site,
  sub,
  busy,
  act,
  passkeys,
}: {
  site: JobSite;
  sub?: string;
  busy: boolean;
  act: Act;
  passkeys?: PasskeyContext;
}) {
  const uid = useId();
  const [leaving, setLeaving] = useState(false);
  const base = `/api/job-sites/${encodeURIComponent(site.id)}`;
  const leave = `${uid}-leave`;
  return (
    <article className="card" data-testid={`job-site-${site.id}`}>
      <SiteHead site={site} title={site.label} />
      <Linking site={site} sub={sub} base={base} busy={busy} act={act} />
      <Signing site={site} />
      {site.signing?.passkeys && passkeys && (
        <Passkeys site={site} base={base} context={passkeys} busy={busy} act={act} />
      )}
      <Calls site={site} sub={sub} base={base} busy={busy} act={act} owner />
      <Workspaces site={site} base={base} busy={busy} act={act} owner />
      {(site.servers ?? []).map((entry) => (
        <LocalServer
          key={entry.server.id}
          base={base}
          entry={entry}
          busy={busy}
          act={act}
          solo={site.sharing === false}
        />
      ))}
      {site.ownerInDevMode !== undefined && site.ownerInDevMode !== null && (
        <div className="subsection">
          <label className="check">
            <input
              type="checkbox"
              checked={site.ownerInDevMode}
              disabled={busy}
              onChange={(event) =>
                void act(() => post(`${base}/settings`, { ownerInDevMode: event.target.checked }))
              }
            />
            <span>
              Let Eugene&apos;s owner use folders they give themselves here, while Eugene is in
              developer mode
            </span>
          </label>
        </div>
      )}
      <Audit base={base} label={site.label} />
      <div className="subsection">
        {leaving ? (
          <div
            role="group"
            aria-label={`Take ${site.label} out of Eugene?`}
            className="notice notice--error flex-col"
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setLeaving(false);
                refocus(leave);
              }
            }}
          >
            <p>
              Take {site.label} out of Eugene? Its files stay on it; Workbench stops reaching them.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                autoFocus
                disabled={busy}
                aria-label={`Keep it: ${site.label}`}
                className="btn btn-wrap"
                onClick={() => {
                  setLeaving(false);
                  refocus(leave);
                }}
              >
                Keep it
              </button>
              <button
                disabled={busy}
                aria-label={`Take it out: ${site.label}`}
                className="btn btn-danger btn-wrap"
                onClick={() => void act(() => post(`${base}/leave`))}
              >
                Take it out
              </button>
            </div>
          </div>
        ) : (
          <button
            id={leave}
            aria-label={`Take this machine out: ${site.label}`}
            className="btn btn-quiet btn-danger -ms-2 self-start"
            onClick={() => setLeaving(true)}
          >
            Take this machine out
          </button>
        )}
      </div>
    </article>
  );
}

/** One sentence about the machine's key, in the tone it needs: a warning where
 * nothing runs until the person acts, a notice where changes wait, and plain
 * help where there is nothing to do. */
function Say({
  id,
  tone,
  children,
}: {
  id: string;
  tone: "warn" | "info" | "quiet";
  children: ReactNode;
}) {
  if (tone === "quiet") {
    return (
      <p className="help" data-testid={id}>
        {children}
      </p>
    );
  }
  return (
    <p className={`notice${tone === "warn" ? " notice--warn" : ""}`} data-testid={id}>
      {tone === "warn" ? <TriangleAlert size={16} aria-hidden /> : <Info size={16} aria-hidden />}
      {children}
    </p>
  );
}

/** Whether the machine checks your changes with your own key (J14a), in
 * words: nothing runs there under your rules until it does. `own`: a site you
 * are linked to, not its owner (2b.3b), where only your own rules are yours. */
function Signing({ site, own = false }: { site: JobSite; own?: boolean }) {
  const signing = site.signing;
  if (!signing) return null;
  const id = `signing-${site.id}`;
  const page = signing.approvePage;
  const there = page ? `On ${site.label}, open ${page}` : null;
  if (own) {
    const state = signing.state;
    return (
      <Say
        id={id}
        tone={
          state === "unsigned" || state === "unconfirmed"
            ? "warn"
            : signing.held > 0
              ? "info"
              : "quiet"
        }
      >
        {state === "unsigned"
          ? `Your workspaces on ${site.label} wait until you add your own key there.${there ? ` ${there} and make a key.` : ""}`
          : state === "unconfirmed"
            ? `Nothing of yours runs on ${site.label} until you approve your rules with your key.`
            : `Your changes on ${site.label} wait for your approval with your own key.`}
        {signing.held > 0
          ? ` ${signing.held} ${signing.held === 1 ? "change is" : "changes are"} waiting.`
          : ""}
      </Say>
    );
  }
  if (signing.state === "unsigned") {
    return (
      <Say id={id} tone="warn">
        No tool runs on {site.label} until you add your own key there.{" "}
        {there
          ? `${there} and make a key. Changes that give access then wait there for you to approve them.`
          : signing.passkeys
            ? `Pair a passkey with it below, with the code its owner's command shows there.`
            : `Eugene on ${site.label} cannot take a key yet: this kind of install gets it in a later update.`}
      </Say>
    );
  }
  if (signing.state === "unconfirmed") {
    return (
      <Say id={id} tone="warn">
        No tool runs on {site.label} until you approve its rules with your key.{" "}
        {there
          ? `${there} to approve them${signing.passkeys ? ", or approve them here with your passkey" : ""}.`
          : signing.passkeys
            ? "Approve them here with your passkey."
            : "Approve them at the machine."}
      </Say>
    );
  }
  return (
    <Say id={id} tone={signing.held > 0 ? "info" : "quiet"}>
      Changes that give access wait for your approval on {site.label}, with your own key.
      {signing.held > 0
        ? ` ${signing.held} ${signing.held === 1 ? "change is" : "changes are"} waiting${page ? `: open ${page} there` : ""}.`
        : ""}
    </Say>
  );
}

/** A passkey from here (J14a.3): pair one with the code the machine shows,
 * then review and approve what the machine holds, with it. The code stays in
 * this browser; the machine checks everything. */
function Passkeys({
  site,
  base,
  context,
  busy,
  act,
}: {
  site: JobSite;
  base: string;
  context: PasskeyContext;
  busy: boolean;
  act: Act;
}) {
  const uid = useId();
  const [code, setCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [held, setHeld] = useState<HeldList | null>(null);
  const [chosen, setChosen] = useState<string | null>(() => rememberedPasskey(site.id));
  const [problem, setProblem] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const rpId = context.rpId;
  const usable = rpId !== null && passkeysHere();

  const run = async (work: () => Promise<void>) => {
    setWorking(true);
    setProblem(null);
    try {
      await work();
    } catch (error) {
      setProblem(passkeyProblem(error));
    } finally {
      setWorking(false);
    }
  };

  const list = async (key: string | null) => {
    const value = await post<HeldList>(`${base}/held`, { key });
    setHeld(value);
    return value;
  };

  const pair = () =>
    run(async () => {
      if (!rpId) return;
      const before = await list(null);
      const made = await makePasskey({
        rpId,
        person: context.person,
        name: context.name || context.person,
        exclude: (before.passkeys ?? []).map((p) => p.credentialId),
      });
      const label = `Passkey from Workbench, added ${new Date().toISOString().slice(0, 10)}`;
      const mac = await pairingMac(code, {
        site: site.id,
        person: context.person,
        credentialId: made.credentialId,
        publicKey: made.publicKey,
        alg: made.alg,
        rpId,
      });
      await act(async () => {
        const pinned = await post<{ id: string }>(`${base}/passkeys`, {
          ...made,
          rpId,
          label,
          mac,
        });
        rememberPasskey(site.id, pinned.id);
        setChosen(pinned.id);
        setCode("");
        setPairing(false);
      });
      await list(null);
    });

  const passkeys = held?.passkeys ?? [];
  const key =
    passkeys.find((p) => p.id === chosen)?.id ??
    (passkeys.length === 1 ? (passkeys[0]?.id ?? null) : null);

  const review = () =>
    run(async () => {
      const first = await list(null);
      const only = first.passkeys?.length === 1 ? (first.passkeys[0]?.id ?? null) : null;
      const wanted = first.passkeys?.find((p) => p.id === chosen)?.id ?? only;
      if (wanted) await list(wanted);
    });

  const approve = (ident: string) =>
    run(async () => {
      if (!key || !rpId) return;
      // A fresh envelope each time: an approval spends its sequence number.
      const fresh = await list(key);
      const item = fresh.items.find((i) => i.id === ident);
      const passkey = passkeys.find((p) => p.id === key);
      if (!item?.envelope || !passkey) throw new Error("That is no longer waiting.");
      const signed = await signEnvelope(item.envelope, passkey.credentialId, passkey.rpId);
      await act(() =>
        post(`${base}/held/${encodeURIComponent(ident)}/approve`, {
          envelope: item.envelope,
          key,
          ...signed,
        }),
      );
      await list(key);
    });

  const reject = (ident: string) =>
    run(async () => {
      await act(() => post(`${base}/held/${encodeURIComponent(ident)}/reject`));
      await list(key);
    });

  // A lost phone (J60): removing a key only takes it away, so it needs no
  // passkey and no visit to the machine.
  const remove = (ident: string) =>
    run(async () => {
      await act(() => post(`${base}/passkeys/${encodeURIComponent(ident)}/remove`));
      setRemoving(null);
      if (chosen === ident) setChosen(null);
      await list(null);
    });
  const lastKey = (held?.keys.length ?? 0) <= 1;

  if (!usable) {
    return (
      <p className="help" data-testid={`passkeys-${site.id}`}>
        {rpId
          ? "This browser cannot use a passkey here."
          : "To approve changes from here with a passkey, open Workbench at its https address."}
      </p>
    );
  }
  return (
    <section
      aria-label={`Passkeys for ${site.label}`}
      className="subsection"
      data-testid={`passkeys-${site.id}`}
    >
      <h3 className="title-sub">Passkeys</h3>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || working}
          aria-label={`Review changes waiting on ${site.label}`}
          className="btn btn-wrap"
          onClick={() => void review()}
        >
          Review changes waiting
        </button>
        <button
          type="button"
          disabled={busy || working}
          aria-label={`Add a passkey for ${site.label}`}
          className="btn btn-wrap"
          onClick={() => setPairing(!pairing)}
        >
          Add a passkey
        </button>
      </div>
      {pairing && (
        <form
          className="flex flex-col gap-3 rounded-plexus border border-line p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void pair();
          }}
        >
          <div className="field">
            <label htmlFor={`${uid}-code`} className="field-label">
              The code {site.label} shows
            </label>
            <input
              id={`${uid}-code`}
              required
              autoComplete="off"
              spellCheck={false}
              value={code}
              placeholder="XXXXX-XXXXX"
              onChange={(event) => setCode(event.target.value)}
              aria-describedby={`${uid}-code-help`}
              className="control max-w-xs font-mono"
              data-testid="passkey-code"
            />
            <p id={`${uid}-code-help`} className="help">
              {site.linkPage
                ? `To get the code, open ${site.linkPage} on ${site.label} and choose Show a code for a passkey.`
                : site.linkPage === null
                  ? `To get the code, run Eugene's installer on ${site.label} again with --site-pair.`
                  : `${site.label} shows the code on its key page.`}{" "}
              The code stays in this browser: Eugene never sees it.
            </p>
          </div>
          <button
            aria-label={`Make and pair a passkey for ${site.label}`}
            disabled={busy || working || !looksLikeCode(code)}
            className="btn btn-primary btn-wrap self-start"
          >
            Make and pair a passkey
          </button>
        </form>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {passkeys.length > 1 && (
        <div className="field">
          <label htmlFor={`${uid}-approve-with`} className="field-label">
            Approve with
          </label>
          <select
            id={`${uid}-approve-with`}
            value={key ?? ""}
            onChange={(event) => {
              setChosen(event.target.value);
              rememberPasskey(site.id, event.target.value);
            }}
            className="control max-w-sm"
          >
            <option value="" disabled>
              choose a passkey
            </option>
            {passkeys.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      )}
      {held && passkeys.length === 0 && (
        <p className="empty">
          No passkey is paired with {site.label} yet. Add one with the code it shows.
        </p>
      )}
      {passkeys.length > 0 && (
        <ul className="flex flex-col gap-2" aria-label={`Your passkeys for ${site.label}`}>
          {passkeys.map((p) => {
            const removeId = `${uid}-remove-${p.id}`;
            return (
              <li
                key={p.id}
                className="flex flex-col gap-2 text-sm"
                data-testid={`passkey-${p.id}`}
              >
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 wrap-anywhere">{p.label}</span>
                  {removing !== p.id && (
                    <button
                      id={removeId}
                      type="button"
                      disabled={busy || working}
                      aria-label={`Remove passkey ${p.label}`}
                      className="btn btn-quiet btn-danger"
                      onClick={() => setRemoving(p.id)}
                    >
                      Remove
                    </button>
                  )}
                </div>
                {removing === p.id && (
                  <div
                    role="group"
                    aria-label={`Remove passkey ${p.label}?`}
                    className="notice notice--error flex-col"
                    onKeyDown={(event) => {
                      if (event.key === "Escape") {
                        setRemoving(null);
                        refocus(removeId);
                      }
                    }}
                  >
                    <p>
                      {lastKey
                        ? `It is your last key for ${site.label}: no tool runs there until you add one and approve its rules.`
                        : "What it approved stays."}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <button
                        autoFocus
                        type="button"
                        disabled={working}
                        aria-label={`Keep it: ${p.label}`}
                        className="btn btn-wrap"
                        onClick={() => {
                          setRemoving(null);
                          refocus(removeId);
                        }}
                      >
                        Keep it
                      </button>
                      <button
                        type="button"
                        disabled={busy || working}
                        aria-label={`Remove it: ${p.label}`}
                        className="btn btn-danger btn-wrap"
                        onClick={() => void remove(p.id)}
                      >
                        Remove it
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {held && held.items.length === 0 && passkeys.length > 0 && (
        <p className="empty">Nothing is waiting.</p>
      )}
      {held?.items.map((item) => (
        <div
          key={item.id}
          className="flex flex-col gap-2 rounded-plexus border border-line p-3"
          data-testid={`held-${item.id}`}
        >
          <p className="title-sub">
            {item.id === "rules" ? `${site.label}'s rules, as a whole` : "A change waiting"}
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-sm marker:text-muted">
            {item.words.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
          <p className="help">
            These words come from {site.label}. Your passkey signs exactly what {site.label} wrote,
            and it checks the signature itself.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy || working || !key}
              aria-label={`Approve with your passkey: ${item.id === "rules" ? `${site.label}'s rules` : `a change on ${site.label}`}`}
              className="btn btn-primary btn-wrap"
              onClick={() => void approve(item.id)}
            >
              Approve with your passkey
            </button>
            {item.id !== "rules" && (
              <button
                type="button"
                disabled={busy || working}
                aria-label={`Turn down: a change on ${site.label}`}
                className="btn btn-quiet btn-danger btn-wrap"
                onClick={() => void reject(item.id)}
              >
                Turn down
              </button>
            )}
          </div>
        </div>
      ))}
    </section>
  );
}

const NO = "no";
const ASK = "ask" as const;
const ALLOW = "allow" as const;

/** A local server its administrator added at the machine: on or off, and who
 * may use which of its tools, each without asking or asked about each time
 * (J78). A tool that can change things is asked about unless you say not. */
function LocalServer({
  base,
  entry,
  busy,
  act,
  solo,
}: {
  base: string;
  entry: JobSiteServer;
  busy: boolean;
  act: Act;
  solo: boolean;
}) {
  const uid = useId();
  const { server } = entry;
  const [people, setPeople] = useState(() =>
    entry.people.map((p) => ({
      name: p.name,
      tools: new Map(
        p.tools.map(
          (t) =>
            [
              t.name,
              t.decision ??
                (t.standing || !server.tools.find((x) => x.name === t.name)?.destructive
                  ? ALLOW
                  : ASK),
            ] as const,
        ),
      ),
    })),
  );
  const [adding, setAdding] = useState("");
  const path = `${base}/servers/${encodeURIComponent(server.id)}`;
  return (
    <section
      aria-labelledby={`${uid}-title`}
      className="subsection"
      data-testid={`server-${server.id}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h3 id={`${uid}-title`} className="title-sub">
          {server.name}
        </h3>
        {server.system && (
          <span className="badge badge--warn">Can change this machine&apos;s settings</span>
        )}
      </div>
      <label className="check">
        <input
          type="checkbox"
          checked={server.enabled}
          disabled={busy}
          onChange={(event) =>
            void act(() => post(`${path}/enabled`, { enabled: event.target.checked }))
          }
        />
        <span>On</span>
      </label>
      {server.reason &&
        (server.available ? (
          <p className="help">{server.reason}</p>
        ) : (
          <p className="notice notice--warn">
            <TriangleAlert size={16} aria-hidden />
            {server.reason}
          </p>
        ))}
      {server.enabled && server.tools.length > 0 && (
        <>
          <p className="help">
            Who may use which tools, and whether each is asked about each time.
          </p>
          {people.map((person, index) => (
            <fieldset
              key={person.name}
              className="flex min-w-0 flex-col gap-2 rounded-plexus border border-line p-3"
            >
              <legend className="px-1 text-sm font-semibold">{person.name}</legend>
              {server.tools.map((tool) => (
                <label
                  key={tool.name}
                  className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-[11rem_minmax(0,1fr)] sm:items-center sm:gap-3"
                >
                  <span>{tool.title || tool.name}</span>
                  <select
                    aria-label={`${person.name} may use ${tool.name}`}
                    value={person.tools.get(tool.name) ?? NO}
                    onChange={(event) =>
                      setPeople((old) =>
                        old.map((p, i) => {
                          if (i !== index) return p;
                          const tools = new Map(p.tools);
                          if (event.target.value === NO) tools.delete(tool.name);
                          else tools.set(tool.name, event.target.value === ALLOW ? ALLOW : ASK);
                          return { ...p, tools };
                        }),
                      )
                    }
                    className="control control--sm w-full max-w-xs"
                  >
                    <option value={NO}>No</option>
                    <option value={ASK}>Yes, asking each time</option>
                    <option value={ALLOW}>Yes, without asking</option>
                  </select>
                </label>
              ))}
              <button
                type="button"
                className="btn btn-quiet btn-danger btn-wrap -ms-2 self-start"
                onClick={() => setPeople((old) => old.filter((_, i) => i !== index))}
              >
                Remove {person.name}
              </button>
            </fieldset>
          ))}
          {!solo && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="field min-w-48 flex-1">
                <label htmlFor={`${uid}-add`} className="field-label">
                  Add a person (how they sign in)
                </label>
                <input
                  id={`${uid}-add`}
                  value={adding}
                  onChange={(event) => setAdding(event.target.value)}
                  className="control"
                />
              </div>
              <button
                type="button"
                aria-label={`Add to those who may use ${server.name}`}
                className="btn"
                onClick={() => {
                  const name = adding.trim();
                  if (name && !people.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
                    setPeople((old) => [
                      ...old,
                      { name, tools: new Map<string, "allow" | "ask">() },
                    ]);
                  }
                  setAdding("");
                }}
              >
                Add
              </button>
            </div>
          )}
          <button
            disabled={busy}
            className="btn btn-primary btn-wrap self-start"
            onClick={() =>
              void act(() =>
                post(`${path}/access`, {
                  people: people.map((p) => ({
                    name: p.name,
                    tools: server.tools
                      .filter((t) => p.tools.has(t.name))
                      .map((t) => ({ name: t.name, decision: p.tools.get(t.name) })),
                  })),
                }),
              )
            }
          >
            Save who may use {server.name}
          </button>
        </>
      )}
    </section>
  );
}

/** The machine's own audit log: who asked for what, and what it decided. */
/** The most entries this page asks a machine for each time. */
const AUDIT_LIMIT = 50;

function Audit({ base, label }: { base: string; label: string }) {
  const [entries, setEntries] = useState<SiteAuditEntry[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="subsection">
      <button
        type="button"
        className="btn btn-wrap self-start"
        aria-label={entries ? `Refresh what ${label} was asked` : undefined}
        onClick={async () => {
          setProblem(null);
          try {
            const page = await post<{ entries: SiteAuditEntry[] }>(`${base}/audit`, {
              limit: AUDIT_LIMIT,
            });
            setEntries(page.entries);
          } catch (error) {
            setProblem(problemOf(error));
          }
        }}
      >
        {entries ? "Refresh" : `Show what ${label} was asked`}
      </button>
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {entries?.length === 0 && <p className="empty">Nothing has been asked of it yet.</p>}
      {entries && entries.length > 0 && (
        <p role="status" className="text-xs text-muted">
          Showing the latest {entries.length} {entries.length === 1 ? "entry" : "entries"}, newest
          first. This list shows at most the latest {AUDIT_LIMIT}.
        </p>
      )}
      {entries && entries.length > 0 && (
        <ul className="flex flex-col gap-1.5 text-xs" aria-label={`What ${label} was asked`}>
          {entries.map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <time dateTime={entry.at} className="tabular-nums text-muted">
                {new Date(entry.at).toLocaleString()}
              </time>
              {" · "}
              {entry.subject}
              {" · "}
              {entry.tool ?? entry.action ?? entry.method ?? ""}
              {" · "}
              {entry.decision === "allowed" ? (
                <span className="inline-flex items-center gap-0.5 text-ok">
                  <Check size={12} aria-hidden />
                  allowed
                </span>
              ) : (
                <span className="inline-flex items-center gap-0.5 text-error">
                  <Ban size={12} aria-hidden />
                  refused
                </span>
              )}
              {entry.reason ? ` · ${entry.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
