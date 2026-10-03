import { useState } from "react";

import { post } from "../lib/api";
import type { Message, ToolCall } from "../lib/types";

const labels: Record<ToolCall["status"], string> = {
  pending: "Waiting for approval",
  running: "Running",
  done: "Finished",
  declined: "Declined",
  cancelled: "Did not run",
  failed: "Server reported a failure",
  uncertain: "Result unknown — check the server before trying again",
};

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
    <section aria-label="Tool calls" className="flex flex-col gap-2">
      {calls.map((call, index) => (
        <article
          key={`${index}:${call.id}`}
          className="rounded-plexus border border-line p-3 text-sm"
        >
          <h3 className="font-semibold">
            {call.serverName} · {call.tool}
          </h3>
          <p role="status" className="text-muted">
            {labels[call.status]}
          </p>
          <details open={call.status === "pending"}>
            <summary className="cursor-pointer">Arguments for this call</summary>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all py-2">
              {JSON.stringify(call.arguments, null, 2)}
            </pre>
          </details>
          {call.result && (
            <details>
              <summary className="cursor-pointer">Result</summary>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all py-2">
                {call.result}
              </pre>
            </details>
          )}
          {call.status === "pending" && message.status === "running" && !readOnly && (
            <div className="mt-2 flex gap-3">
              <button
                disabled={sent.has(call.id)}
                onClick={() => void decide(call, true)}
                className="rounded-plexus bg-accent px-3 py-1 text-on-accent"
              >
                Approve call
              </button>
              <button
                disabled={sent.has(call.id)}
                onClick={() => void decide(call, false)}
                className="px-3 py-1"
              >
                Decline
              </button>
            </div>
          )}
        </article>
      ))}
      {problem && (
        <p role="alert" className="text-error">
          {problem}
        </p>
      )}
      <p className="text-xs text-muted">
        Stopping, editing or retrying a chat does not undo actions a tool has already taken.
      </p>
    </section>
  );
}
