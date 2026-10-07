import { useCallback, useEffect, useState } from "react";

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

/** Job sites (your machines): the machines whose files you use from here.
 * Yours alone: only you say who may use their folders, yourself included. */
export function JobSites({ onClose, sub }: { onClose: () => void; sub?: string }) {
  const [data, setData] = useState<JobSiteList | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
        A job site is a machine of yours that Workbench can work on from anywhere: the folders you
        choose, under your rules for what may run there without asking you. The machine connects out
        to Eugene and nothing connects to it. Only you say who may use its folders and its tools,
        you included, and the machine itself keeps that list.
      </p>
      {problem && (
        <p role="alert" className="text-sm text-error">
          {problem}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm" data-testid="job-site-held">
          {notice}
        </p>
      )}
      {data?.sites.length === 0 && <p className="text-sm">You have no job sites yet.</p>}
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
      <section
        aria-label="Add a job site"
        className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
      >
        <h2 className="font-semibold">Add a job site</h2>
        {data && !data.canInvite ? (
          <p>
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
              The machine must already have Eugene installed and joined as a node; the command adds
              the job site there (a standalone install comes later). Run one of these on the
              machine, within 15 minutes. It asks for your Eugene password there: that is how Eugene
              knows the machine is yours. The service install needs an administrator (Windows) or
              root (Linux).
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

type Act = (work: () => Promise<unknown>) => Promise<void>;

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
  for (const folder of site.folders) for (const p of folder.people) names.set(p.person, p.name);
  for (const entry of site.servers ?? []) for (const p of entry.people) names.set(p.person, p.name);
  const own = links?.find((l) => l.subject === sub);
  const others = (links ?? []).filter((l) => l.subject !== sub);
  const notThere = (l: { reason?: string | null }) => l.reason || "not signed in there now";
  return (
    <div className="flex flex-col gap-2" data-testid={`linking-${site.id}`}>
      {solo && <p>This machine serves only you (macOS has no folder boundary yet).</p>}
      {!solo && links && own && (
        <div className="flex flex-wrap items-center gap-3">
          <p>
            Your calls here run as <strong>{own.accountName}</strong>
            {own.available ? "" : ` · ${notThere(own)}`}
          </p>
          <button
            type="button"
            disabled={busy}
            className="text-error"
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
              <li key={link.subject} className="flex flex-wrap items-center gap-3">
                <span>
                  {who} runs as {link.accountName}
                  {link.available ? "" : ` · ${notThere(link)}`}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Remove link for ${who} (${link.accountName})`}
                  className="text-error"
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
        <p className="text-muted">
          People link their own account at the machine: on {site.label}, open {site.linkPage} and
          sign in.
        </p>
      )}
      {!solo && links && site.linkPage === null && (
        <div className="flex flex-col gap-1 text-muted">
          <p>On a Linux machine, people link with:</p>
          <pre className="whitespace-pre-wrap break-all rounded-plexus bg-soft p-2 text-xs">
            {LINUX_LINK}
          </pre>
          <p>Run it on the machine as an administrator, naming the person as they sign in.</p>
        </div>
      )}
    </div>
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
    <article
      className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
      data-testid={`job-site-${site.id}`}
    >
      <h2 className="font-semibold">{site.label} · linked</h2>
      <p>
        {site.online ? "Online" : "Offline"} · last contact {since(site.lastContactAt)}
        {!site.ready && site.reason ? ` · ${site.reason}` : ""}
      </p>
      <Linking site={site} sub={sub} base={base} busy={busy} act={act} />
      <Signing site={site} own />
      {site.signing?.passkeys && site.signing.people && passkeys && (
        <Passkeys site={site} base={base} context={passkeys} busy={busy} act={act} />
      )}
      {site.signing?.people ? (
        <Workspaces site={site} base={base} busy={busy} act={act} owner={false} />
      ) : (
        <p className="text-muted">
          Eugene on {site.label} keeps only its owner&apos;s folders. Once it is updated, you can
          keep workspaces of your own there.
        </p>
      )}
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
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [writable, setWritable] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const base = `/api/job-sites/${encodeURIComponent(site.id)}`;
  return (
    <article
      className="flex flex-col gap-3 rounded-plexus border border-line p-3 text-sm"
      data-testid={`job-site-${site.id}`}
    >
      <h2 className="font-semibold">{site.label}</h2>
      <p>
        {site.online ? "Online" : "Offline"} · last contact {since(site.lastContactAt)}
        {!site.ready && site.reason ? ` · ${site.reason}` : ""}
      </p>
      <Linking site={site} sub={sub} base={base} busy={busy} act={act} />
      <Signing site={site} />
      {site.signing?.passkeys && passkeys && (
        <Passkeys site={site} base={base} context={passkeys} busy={busy} act={act} />
      )}
      {site.signing?.people && <Workspaces site={site} base={base} busy={busy} act={act} owner />}
      {!site.signing?.people &&
        site.folders.map((folder) => (
          <Folder
            key={folder.id}
            base={base}
            folder={folder}
            busy={busy}
            act={act}
            solo={site.sharing === false}
          />
        ))}
      <form
        hidden={Boolean(site.signing?.people)}
        aria-label={`Add a folder on ${site.label}`}
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void act(async () => {
            const value = await post(`${base}/folders`, { name, path, writable });
            setName("");
            setPath("");
            setWritable(false);
            return value;
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
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={site.ownerInDevMode}
            disabled={busy}
            onChange={(event) =>
              void act(() => post(`${base}/settings`, { ownerInDevMode: event.target.checked }))
            }
          />
          Let Eugene&apos;s owner use folders they give themselves here, while Eugene is in dev mode
        </label>
      )}
      <Audit base={base} label={site.label} />
      {leaving ? (
        <div className="flex flex-wrap gap-3">
          <p>
            Take {site.label} out of Eugene? Its files stay on it; Workbench stops reaching them.
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

/** Whether the machine checks your changes with your own key (J14a), in
 * words: nothing runs there under your rules until it does. `own`: a site you
 * are linked to, not its owner (2b.3b), where only your own rules are yours. */
function Signing({ site, own = false }: { site: JobSite; own?: boolean }) {
  const signing = site.signing;
  if (!signing) return null;
  const page = signing.approvePage;
  const there = page ? `On ${site.label}, open ${page}` : null;
  if (own) {
    const state = signing.state;
    return (
      <p data-testid={`signing-${site.id}`}>
        {state === "unsigned"
          ? `Your workspaces on ${site.label} wait until you add your own key there.${there ? ` ${there} and make a key.` : ""}`
          : state === "unconfirmed"
            ? `Nothing of yours runs on ${site.label} until you approve your rules with your key.`
            : `Your changes on ${site.label} wait for your approval with your own key.`}
        {signing.held > 0
          ? ` ${signing.held} ${signing.held === 1 ? "change is" : "changes are"} waiting.`
          : ""}
      </p>
    );
  }
  if (signing.state === "unsigned") {
    return (
      <p data-testid={`signing-${site.id}`}>
        No tool runs on {site.label} until you add your own key there.{" "}
        {there
          ? `${there} and make a key. Changes that give access then wait there for you to approve them.`
          : signing.passkeys
            ? `Pair a passkey with it below, with the code its owner's command shows there.`
            : `Eugene on ${site.label} cannot take a key yet: this kind of install gets it in a later update.`}
      </p>
    );
  }
  if (signing.state === "unconfirmed") {
    return (
      <p data-testid={`signing-${site.id}`}>
        No tool runs on {site.label} until you approve its rules with your key.{" "}
        {there
          ? `${there} to approve them${signing.passkeys ? ", or approve them here with your passkey" : ""}.`
          : signing.passkeys
            ? "Approve them here with your passkey."
            : "Approve them at the machine."}
      </p>
    );
  }
  return (
    <p className="text-muted" data-testid={`signing-${site.id}`}>
      Changes that give access wait for your approval on {site.label}, with your own key.
      {signing.held > 0
        ? ` ${signing.held} ${signing.held === 1 ? "change is" : "changes are"} waiting${page ? `: open ${page} there` : ""}.`
        : ""}
    </p>
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
      <p className="text-muted" data-testid={`passkeys-${site.id}`}>
        {rpId
          ? "This browser cannot use a passkey here."
          : "To approve changes from here with a passkey, open Workbench at its https address."}
      </p>
    );
  }
  return (
    <section
      aria-label={`Passkeys for ${site.label}`}
      className="flex flex-col gap-2 rounded-plexus border border-line p-2"
      data-testid={`passkeys-${site.id}`}
    >
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          disabled={busy || working}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() => void review()}
        >
          Review changes waiting
        </button>
        <button
          type="button"
          disabled={busy || working}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() => setPairing(!pairing)}
        >
          Add a passkey
        </button>
      </div>
      {pairing && (
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            void pair();
          }}
        >
          <label className="flex flex-col gap-1">
            The code {site.label} shows
            <input
              required
              autoComplete="off"
              spellCheck={false}
              value={code}
              placeholder="XXXXX-XXXXX"
              onChange={(event) => setCode(event.target.value)}
              className="rounded-plexus border border-line bg-transparent px-2 py-1 font-mono"
              data-testid="passkey-code"
            />
          </label>
          <button
            disabled={busy || working || !looksLikeCode(code)}
            className="rounded-plexus border border-line px-3 py-1"
          >
            Make and pair a passkey
          </button>
          <p className="w-full text-muted">
            {site.linkPage
              ? `To get the code, open ${site.linkPage} on ${site.label} and choose Show a code for a passkey.`
              : site.linkPage === null
                ? `To get the code, run Eugene's installer on ${site.label} again with --site-pair.`
                : `${site.label} shows the code on its key page.`}{" "}
            The code stays in this browser: Eugene never sees it.
          </p>
        </form>
      )}
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      {passkeys.length > 1 && (
        <label className="flex items-center gap-2">
          Approve with
          <select
            value={key ?? ""}
            onChange={(event) => {
              setChosen(event.target.value);
              rememberPasskey(site.id, event.target.value);
            }}
            className="rounded-plexus border border-line bg-transparent px-2 py-1"
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
        </label>
      )}
      {held && passkeys.length === 0 && (
        <p>No passkey is paired with {site.label} yet. Add one with the code it shows.</p>
      )}
      {passkeys.length > 0 && (
        <ul className="flex flex-col gap-1" aria-label={`Your passkeys for ${site.label}`}>
          {passkeys.map((p) => (
            <li
              key={p.id}
              className="flex flex-wrap items-center gap-3"
              data-testid={`passkey-${p.id}`}
            >
              <span>{p.label}</span>
              {removing === p.id ? (
                <>
                  <span className="text-muted">
                    {lastKey
                      ? `It is your last key for ${site.label}: no tool runs there until you add one and approve its rules.`
                      : "What it approved stays."}
                  </span>
                  <button
                    type="button"
                    disabled={busy || working}
                    className="text-error"
                    onClick={() => void remove(p.id)}
                  >
                    Remove it
                  </button>
                  <button type="button" disabled={working} onClick={() => setRemoving(null)}>
                    Keep it
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  disabled={busy || working}
                  className="text-error"
                  onClick={() => setRemoving(p.id)}
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {held && held.items.length === 0 && passkeys.length > 0 && <p>Nothing is waiting.</p>}
      {held?.items.map((item) => (
        <div key={item.id} className="flex flex-col gap-1" data-testid={`held-${item.id}`}>
          <p className="font-semibold">
            {item.id === "rules" ? `${site.label}'s rules, as a whole` : "A change waiting"}
          </p>
          <ul className="list-disc pl-5">
            {item.words.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
          <p className="text-muted">
            These words come from {site.label}. Your passkey signs exactly what {site.label} wrote,
            and it checks the signature itself.
          </p>
          <div className="flex gap-3">
            <button
              type="button"
              disabled={busy || working || !key}
              className="rounded-plexus border border-line px-3 py-1"
              onClick={() => void approve(item.id)}
            >
              Approve with your passkey
            </button>
            {item.id !== "rules" && (
              <button
                type="button"
                disabled={busy || working}
                className="text-error"
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

const READ = "read";
const CHANGE = "change";

function Folder({
  base,
  folder,
  busy,
  act,
  solo,
}: {
  base: string;
  folder: JobSite["folders"][number];
  busy: boolean;
  act: Act;
  solo: boolean;
}) {
  const [people, setPeople] = useState(() =>
    folder.people.map((p) => ({ name: p.name, access: p.writable ? CHANGE : READ })),
  );
  const [adding, setAdding] = useState("");
  return (
    <div
      className="flex flex-col gap-2 rounded-plexus border border-line p-2"
      data-testid={`folder-${folder.id}`}
    >
      <h3 className="font-semibold">
        {folder.name} · {folder.writable ? "read and write text" : "read only"}
      </h3>
      <p className="break-all text-muted">{folder.path}</p>
      <p>Who may use it. Include yourself to use it.</p>
      {people.length === 0 && <p className="text-muted">Nobody yet.</p>}
      {people.map((person, index) => (
        <div key={person.name} className="flex flex-wrap items-center gap-2">
          <span className="min-w-24">{person.name}</span>
          <select
            aria-label={`What ${person.name} may do in ${folder.name}`}
            value={person.access}
            onChange={(event) =>
              setPeople((old) =>
                old.map((p, i) => (i === index ? { ...p, access: event.target.value } : p)),
              )
            }
            className="rounded-plexus border border-line bg-soft px-2 py-1"
          >
            <option value={READ}>Read</option>
            {folder.writable && <option value={CHANGE}>May change files without asking you</option>}
          </select>
          <button
            type="button"
            className="text-error"
            onClick={() => setPeople((old) => old.filter((_, i) => i !== index))}
          >
            Remove {person.name}
          </button>
        </div>
      ))}
      {!solo && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1">
            Add a person (how they sign in)
            <input
              value={adding}
              onChange={(event) => setAdding(event.target.value)}
              className="rounded-plexus border border-line bg-transparent px-2 py-1"
            />
          </label>
          <button
            type="button"
            className="rounded-plexus border border-line px-3 py-1"
            onClick={() => {
              const name = adding.trim();
              if (name && !people.some((p) => p.name.toLowerCase() === name.toLowerCase())) {
                setPeople((old) => [...old, { name, access: READ }]);
              }
              setAdding("");
            }}
          >
            Add
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <button
          disabled={busy}
          className="rounded-plexus border border-line px-3 py-1"
          onClick={() =>
            void act(() =>
              post(`${base}/folders/${encodeURIComponent(folder.id)}/people`, {
                people: people.map((p) => ({ name: p.name, writable: p.access === CHANGE })),
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
    <div
      className="flex flex-col gap-2 rounded-plexus border border-line p-2"
      data-testid={`server-${server.id}`}
    >
      <h3 className="font-semibold">
        {server.name}
        {server.system ? " · can change this machine's settings" : ""}
      </h3>
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={server.enabled}
          disabled={busy}
          onChange={(event) =>
            void act(() => post(`${path}/enabled`, { enabled: event.target.checked }))
          }
        />
        On
      </label>
      {server.reason && <p className="text-muted">{server.reason}</p>}
      {server.enabled && server.tools.length > 0 && (
        <>
          <p>Who may use which tools, and whether each is asked about each time.</p>
          {people.map((person, index) => (
            <fieldset key={person.name} className="flex flex-wrap items-center gap-3">
              <legend>{person.name}</legend>
              {server.tools.map((tool) => (
                <label key={tool.name} className="flex items-center gap-1">
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
                    className="rounded-plexus border border-line bg-soft px-1"
                  >
                    <option value={NO}>No</option>
                    <option value={ASK}>Yes, asking each time</option>
                    <option value={ALLOW}>Yes, without asking</option>
                  </select>
                  {tool.title || tool.name}
                </label>
              ))}
              <button
                type="button"
                className="text-error"
                onClick={() => setPeople((old) => old.filter((_, i) => i !== index))}
              >
                Remove {person.name}
              </button>
            </fieldset>
          ))}
          {!solo && (
            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1">
                Add a person (how they sign in)
                <input
                  value={adding}
                  onChange={(event) => setAdding(event.target.value)}
                  className="rounded-plexus border border-line bg-transparent px-2 py-1"
                />
              </label>
              <button
                type="button"
                className="rounded-plexus border border-line px-3 py-1"
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
            className="self-start rounded-plexus border border-line px-3 py-1"
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
    </div>
  );
}

/** The machine's own audit log: who asked for what, and what it decided. */
function Audit({ base, label }: { base: string; label: string }) {
  const [entries, setEntries] = useState<SiteAuditEntry[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        className="self-start text-accent"
        onClick={async () => {
          setProblem(null);
          try {
            const page = await post<{ entries: SiteAuditEntry[] }>(`${base}/audit`, {
              limit: 50,
            });
            setEntries(page.entries);
          } catch (error) {
            setProblem(problemOf(error));
          }
        }}
      >
        Show what {label} was asked
      </button>
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      {entries?.length === 0 && <p className="text-muted">Nothing has been asked of it yet.</p>}
      {entries && entries.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs" aria-label={`What ${label} was asked`}>
          {entries.map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              {new Date(entry.at).toLocaleString()} · {entry.subject} ·{" "}
              {entry.tool ?? entry.action ?? entry.method ?? ""} ·{" "}
              {entry.decision === "allowed" ? "allowed" : "refused"}
              {entry.reason ? ` · ${entry.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
