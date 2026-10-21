import { CircleAlert, Wrench } from "lucide-react";
import { useState } from "react";

import { post } from "../lib/api";
import { CopyButton } from "./CopyButton";
import { ToolContent } from "./ToolContent";
import { RememberReceipt } from "./MemoryLearned";
import {
  passkeyProblem,
  passkeysHere,
  rememberedPasskey,
  rememberPasskey,
  signEnvelope,
} from "../lib/passkeys";
import type { HeldList, Message, ToolCall } from "../lib/types";

const labels: Record<ToolCall["status"], string> = {
  pending: "Waiting for approval",
  signing: "Waiting for your signature",
  running: "Running",
  done: "Finished",
  declined: "Declined",
  cancelled: "Did not run",
  failed: "Server reported a failure",
  uncertain: "Result unknown — check the server before trying again",
};

/** The colour of a call's state: a dot and words carry it, never the colour
 * alone. Amber is a call that waits on you or whose result is unknown. */
const tone: Record<ToolCall["status"], string> = {
  pending: "text-warn",
  signing: "text-warn",
  uncertain: "text-warn",
  running: "text-info",
  done: "text-ok",
  failed: "text-error",
  declined: "text-muted",
  cancelled: "text-muted",
};

/** A card's edge, for the calls that ask something of you or went wrong. */
const edge: Partial<Record<ToolCall["status"], string>> = {
  pending: "border-warn-line",
  signing: "border-warn-line",
  uncertain: "border-warn-line",
  failed: "border-error-line",
};

function clock(iso: string): string {
  const when = new Date(iso);
  return Number.isNaN(when.getTime())
    ? iso
    : when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/**
 * A call a job site holds for your own signature (J14b, J86). The site is
 * the authority: you sign on the machine's own page, which shows exactly what
 * you sign, or here with a passkey, whose prompt cannot show it (so the words
 * below are the machine's, carried by Eugene). Workbench asks the machine
 * again every few seconds and carries on once it runs.
 */
function SignCall({
  call,
  onDecide,
  disabled,
}: {
  call: ToolCall;
  onDecide: (signed: boolean) => Promise<void>;
  disabled: boolean;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const held = call.held;
  if (!held) return null;
  const machine = call.label ?? "the machine";
  const site = call.site ?? "";
  const base = `/api/job-sites/${encodeURIComponent(site)}`;

  const withPasskey = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const first = await post<HeldList>(`${base}/held`, { key: null });
      const passkeys = first.passkeys ?? [];
      const passkey =
        passkeys.find((p) => p.id === rememberedPasskey(site)) ??
        (passkeys.length === 1 ? passkeys[0] : undefined);
      if (!passkey) {
        throw new Error(
          passkeys.length
            ? `Choose your passkey for ${machine} once on the Job sites page.`
            : `You have no passkey for ${machine}. Pair one on the Job sites page, or sign on the machine.`,
        );
      }
      // A fresh envelope: one signed earlier may have spent its sequence.
      const listed = await post<HeldList>(`${base}/held`, { key: passkey.id });
      const item = listed.items.find((i) => i.id === held.id);
      if (!item?.envelope) throw new Error("That is no longer waiting for your signature.");
      const signed = await signEnvelope(item.envelope, passkey.credentialId, passkey.rpId);
      await post(`${base}/held/${encodeURIComponent(held.id)}/approve`, {
        envelope: item.envelope,
        key: passkey.id,
        ...signed,
      });
      rememberPasskey(site, passkey.id);
      await onDecide(true);
    } catch (error) {
      setProblem(passkeyProblem(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    // It asks something of you, so it keeps the blue edge.
    <div className="mt-2 flex flex-col gap-2 rounded-plexus border border-accent p-3">
      <p className="font-semibold">
        {held.kind === "window"
          ? `Open a ${held.minutes ?? 60}-minute window on ${machine}`
          : `${machine} asks for your signature`}
      </p>
      <div role="group" className="rounded-plexus bg-soft p-2" aria-label={`What ${machine} holds`}>
        {held.words.map((line, index) => (
          <p
            key={index}
            className="whitespace-pre-wrap font-mono text-[0.8125rem] leading-5 wrap-anywhere"
          >
            {line}
          </p>
        ))}
      </div>
      <p className="text-muted">
        Nothing has run. {machine} checks your own key itself: Eugene cannot sign for you. A passkey
        prompt does not show what it signs; {machine}'s own page does.
      </p>
      <div className="flex flex-wrap gap-2">
        {held.approvePage && (
          <a href={held.approvePage} target="_blank" rel="noreferrer" className="btn btn-primary">
            Sign on {machine}'s page
          </a>
        )}
        {passkeysHere() && (
          <button
            type="button"
            aria-label={`Sign ${call.tool} call with my passkey`}
            disabled={disabled || busy}
            onClick={() => void withPasskey()}
            className="btn"
          >
            Sign with my passkey
          </button>
        )}
        <button
          type="button"
          aria-label={`Do not sign ${call.tool} call`}
          disabled={disabled || busy}
          onClick={() => void onDecide(false)}
          className="btn"
        >
          Do not sign
        </button>
      </div>
      {held.approvePage && (
        <p className="text-xs text-muted">
          The page opens only on {machine} itself. This chat carries on by itself once you sign.
        </p>
      )}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
    </div>
  );
}

export function ToolCalls({
  chatId,
  message,
  readOnly,
  onChanged,
}: {
  chatId: string;
  message: Message;
  readOnly: boolean;
  onChanged: () => void;
}) {
  const [sent, setSent] = useState<Set<string>>(new Set());
  const [problem, setProblem] = useState<string | null>(null);

  async function decide(call: ToolCall, approve: boolean) {
    setSent((old) => new Set([...old, call.id]));
    setProblem(null);
    try {
      await post(`/api/chats/${chatId}/messages/${message.id}/tools/decision`, {
        callId: call.id,
        approve,
      });
      onChanged();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
      setSent((old) => new Set([...old].filter((id) => id !== call.id)));
    }
  }

  const calls = message.toolRounds?.flatMap((round) => round.calls) ?? [];
  if (!calls.length) return null;
  return (
    <section aria-label="Tool calls" className="flex max-w-measure flex-col gap-2 text-sm">
      {calls.map((call, index) => {
        // A pending call the site's rules allow is not waiting on you.
        const allowed = call.status === "pending" && call.ask === false;
        const said = allowed
          ? call.signed
            ? "Sent to the machine, which checks your signature"
            : "Allowed by the rules: runs without asking"
          : labels[call.status];
        const until = call.windowUntil
          ? ` · your window there is open until ${clock(call.windowUntil)}`
          : "";
        return (
          <article
            key={`${index}:${call.id}`}
            className={`rounded-plexus border p-3 ${(!allowed && edge[call.status]) || "border-line"}`}
          >
            <h3 className="flex min-w-0 items-center gap-1.5 font-semibold wrap-anywhere">
              <Wrench size={14} aria-hidden className="shrink-0 text-muted" />
              {call.serverName} · {call.tool}
            </h3>
            <p
              role="status"
              className={`mt-0.5 flex items-center gap-1.5 ${allowed ? "text-muted" : tone[call.status]}`}
            >
              <span
                aria-hidden="true"
                className={`h-2 w-2 shrink-0 rounded-full bg-current ${
                  call.status === "running" ? "animate-pulse" : ""
                }`}
              />
              {`${said}${until}`}
            </p>
            {call.serverId === "memory-write" && (
              <RememberReceipt call={call} readOnly={readOnly} />
            )}
            <ToolContent
              blocks={
                call.contentVersion === 1 && call.content
                  ? call.content
                  : call.result
                    ? [{ id: `${call.id}:text`, type: "text", text: call.result }]
                    : []
              }
              tool={`${call.serverId}/${call.tool}`}
            />
            <details open={call.status === "pending" && call.ask !== false}>
              <summary className="disclosure cursor-pointer text-muted hover:text-fg">
                Arguments for this call
              </summary>
              <div className="pt-1 text-xs text-muted">
                <CopyButton
                  text={JSON.stringify(call.arguments, null, 2)}
                  label={`${call.tool} call arguments`}
                />
              </div>
              <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-plexus border border-line bg-soft p-2 font-mono text-[0.8125rem] leading-5">
                {JSON.stringify(call.arguments, null, 2)}
              </pre>
            </details>
            {call.result && (
              <details>
                <summary className="disclosure cursor-pointer text-muted hover:text-fg">
                  Result
                </summary>
                <div className="pt-1 text-xs text-muted">
                  <CopyButton text={call.result} label={`${call.tool} call result`} />
                </div>
                <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-plexus border border-line bg-soft p-2 font-mono text-[0.8125rem] leading-5">
                  {call.result}
                </pre>
              </details>
            )}
            {call.status === "signing" && message.status === "running" && !readOnly && (
              <SignCall
                call={call}
                disabled={sent.has(call.id)}
                onDecide={(signed) => decide(call, signed)}
              />
            )}
            {call.status === "pending" &&
              call.ask !== false &&
              message.status === "running" &&
              !readOnly && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    aria-label={`Approve ${call.tool} call`}
                    disabled={sent.has(call.id)}
                    onClick={() => void decide(call, true)}
                    className="btn btn-primary"
                  >
                    Approve call
                  </button>
                  <button
                    type="button"
                    aria-label={`Decline ${call.tool} call`}
                    disabled={sent.has(call.id)}
                    onClick={() => void decide(call, false)}
                    className="btn"
                  >
                    Decline
                  </button>
                </div>
              )}
          </article>
        );
      })}
      {problem && (
        <p role="alert" className="notice notice--error">
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      <p className="text-xs text-muted">
        Stopping, editing or retrying a chat does not undo actions a tool has already taken.
      </p>
    </section>
  );
}
