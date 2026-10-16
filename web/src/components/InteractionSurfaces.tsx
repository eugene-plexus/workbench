import { useState } from "react";
import { post } from "../lib/api";
import {
  interactionsPath,
  propose,
  words,
  type Action,
  type ObjectValue,
  type Snapshot,
  type Surface,
} from "../lib/interactions";
import { A2UIForm } from "./A2UIForm";
import { MCPApp } from "./MCPApp";

function SurfaceView({
  chat,
  surface,
  actions,
  readOnly,
  refresh,
}: {
  chat: string;
  surface: Surface;
  actions: Action[];
  readOnly: boolean;
  refresh: () => void;
}) {
  const [problem, setProblem] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const disabled = readOnly || !surface.actionable;
  const onAction = async (name: string, args: ObjectValue, id?: string) => {
    const result = await propose(chat, surface, name, args, id);
    refresh();
    return result;
  };
  async function decide(action: Action, approve: boolean) {
    setActing(true);
    setProblem(null);
    try {
      await post(`${interactionsPath(chat)}/actions/${encodeURIComponent(action.id)}/decision`, {
        approve,
      });
      refresh();
    } catch (error) {
      setProblem(words(error));
    } finally {
      setActing(false);
    }
  }
  return (
    <section
      className="interactive-surface flex flex-col gap-3 rounded-lg border border-line p-4"
      aria-label={surface.protocol === "a2ui" ? "Interactive form" : "Interactive app"}
    >
      <p className="text-sm text-muted">
        {surface.protocol === "a2ui" ? "Form" : "App"}
        {surface.sourceName ? ` from ${surface.sourceName}` : ""}
        {disabled ? " · Read only" : ""}
      </p>
      {surface.protocol === "a2ui" &&
        surface.version === "0.9.1" &&
        surface.view.ready === true && (
          <A2UIForm
            chat={chat}
            surface={surface}
            disabled={disabled}
            onAction={onAction}
            onChanged={refresh}
          />
        )}
      {surface.protocol === "mcp-apps" && surface.version === "2026-01-26" && !disabled && (
        <MCPApp chat={chat} surface={surface} actions={actions} onAction={onAction} />
      )}
      <details open={disabled || surface.view.ready === false}>
        <summary>Text fallback</summary>
        <p className="whitespace-pre-wrap">{surface.fallback}</p>
      </details>
      {actions.map((action) => (
        <div
          key={action.id}
          className="flex flex-col gap-2 border-t border-line pt-3"
          aria-label="App action review"
        >
          <p>
            {action.name} ·{" "}
            {action.status === "proposed" ? "Waiting for your decision" : action.status}
          </p>
          <details open={action.status === "proposed"}>
            <summary>Requested action</summary>
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-sm">
              {JSON.stringify(action.arguments, null, 2)}
            </pre>
          </details>
          {action.status === "proposed" && !disabled && (
            <div className="flex gap-2">
              <button className="btn" disabled={acting} onClick={() => void decide(action, false)}>
                Decline
              </button>
              <button
                className="btn btn-primary"
                disabled={acting}
                onClick={() => void decide(action, true)}
              >
                Allow once
              </button>
            </div>
          )}
          {action.status === "uncertain" && (
            <p role="alert">
              The connection ended before the result was known. This action will not be retried.
            </p>
          )}
          {action.error && <p role="alert">{action.error}</p>}
        </div>
      ))}
      {problem && <p role="alert">{problem}</p>}
    </section>
  );
}

export function InteractionSurfaces({
  chat,
  message,
  snapshot,
  refresh,
}: {
  chat: string;
  message: string;
  snapshot: Snapshot | null;
  refresh: () => void;
}) {
  return snapshot?.surfaces
    .filter((s) => s.messageId === message)
    .map((surface) => (
      <SurfaceView
        key={surface.id}
        chat={chat}
        surface={surface}
        actions={snapshot.actions.filter((a) => a.surfaceId === surface.id)}
        readOnly={snapshot.readOnly}
        refresh={refresh}
      />
    ));
}
