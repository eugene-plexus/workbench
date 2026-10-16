import { useEffect, useId, useState } from "react";
import { api, patch, post } from "../lib/api";
import { aguiPath, type Agent, type useAGUI } from "../lib/agui";
import { words, type ObjectValue } from "../lib/interactions";
import type { Me, Models } from "../lib/types";
import { AgentConnections } from "./AgentConnections";

export function AGUIConversation({
  chat,
  me,
  empty,
  selected,
  onSelect,
  controller,
  onChanged,
  model,
  models,
}: {
  chat: string;
  me: Me;
  empty: boolean;
  selected: string;
  onSelect: (source: string) => void;
  controller: ReturnType<typeof useAGUI>;
  onChanged: () => void;
  model: string | null;
  models: Models | null;
}) {
  const uid = useId();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [generation, refresh] = useState(0);
  const [text, setText] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [modelChoice, setModelChoice] = useState("");
  const { run, loaded, sending, unclear } = controller;
  const source = run ? (run.agentId ?? "native") : selected;
  const nativeModel =
    modelChoice || model || models?.models.find((m) => m.ready || m.onDemand)?.id || "";
  const active = run?.status === "running";
  const blocked = !loaded || sending || stopping || unclear;
  useEffect(() => {
    let live = true;
    void Promise.resolve(api<Agent[]>("/api/v1/ag-ui/agents"))
      .then((agents) => {
        if (live) setAgents(Array.isArray(agents) ? agents : []);
      })
      .catch((error) => {
        if (live) setError(words(error));
      });
    return () => {
      live = false;
    };
  }, [generation]);
  async function send(resume?: ObjectValue[]) {
    setError(null);
    try {
      if (source === "native" && nativeModel !== model)
        await patch(`/api/chats/${encodeURIComponent(chat)}`, { model: nativeModel });
      await controller.start(source, text, resume);
      setText("");
      setAnswers({});
      onChanged();
    } catch (error) {
      setError(words(error));
    }
  }
  return (
    <section
      aria-label="Agent conversation"
      className="flex max-h-[50vh] shrink-0 flex-col gap-3 overflow-y-auto border-t border-line px-4 py-3"
    >
      {empty && !run && (
        <label className="flex items-center gap-2">
          Answer with
          <select
            className="input"
            value={selected}
            disabled={!loaded || sending || unclear}
            onChange={(e) => onSelect(e.target.value)}
          >
            <option value="ordinary">Normal chat</option>
            <option value="native">Workbench agent (AG-UI)</option>
            {agents
              .filter((a) => a.subjects.includes(me.sub))
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
        </label>
      )}
      {me.owner && empty && !run && <AgentConnections onChanged={() => refresh((n) => n + 1)} />}
      {source !== "ordinary" && (
        <>
          {source === "native" && (
            <label className="flex items-center gap-2">
              Agent model
              <select
                className="input"
                value={nativeModel}
                disabled={blocked || active}
                onChange={(e) => setModelChoice(e.target.value)}
              >
                <option value="">Choose a model</option>
                {models?.models.map((m) => (
                  <option key={m.id} value={m.id} disabled={!m.ready && !m.onDemand}>
                    {m.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p role="status">
            {source === "native"
              ? "Workbench agent"
              : (agents.find((a) => a.id === source)?.name ?? "Connected agent")}{" "}
            · {run?.status ?? "Ready"}
          </p>
          {run && (
            <details>
              <summary>Run details</summary>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-xs">
                {JSON.stringify(
                  {
                    runId: run.runId,
                    state: run.state,
                    messages: run.messages,
                    outcome: run.outcome,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
          {active && run.direction === "connect" && (
            <div aria-label="Agent response" className="max-h-48 overflow-auto whitespace-pre-wrap">
              {run.messages
                .filter((m) => m.role === "assistant")
                .map((m) => (
                  <p key={String(m.id)}>{String(m.content ?? "")}</p>
                ))}
            </div>
          )}
          {run?.status === "uncertain" && (
            <p role="alert">
              The agent's last result is unknown. Check any external effects before continuing.
              Workbench will not retry that run.
            </p>
          )}
          {active && (
            <button
              className="btn self-start"
              disabled={blocked}
              onClick={async () => {
                setStopping(true);
                setError(null);
                try {
                  await post(`${aguiPath(chat)}/runs/${encodeURIComponent(run.runId)}/stop`);
                  await controller.reload();
                  onChanged();
                } catch (error) {
                  setError(words(error));
                } finally {
                  setStopping(false);
                }
              }}
            >
              Stop agent
            </button>
          )}
          {run?.outcome?.type === "interrupt" ? (
            <div className="flex flex-col gap-2">
              <p>The agent needs a response before it can continue.</p>
              {run.outcome.interrupts?.map((interrupt) => (
                <div key={String(interrupt.id)} className="flex flex-col gap-2">
                  <label htmlFor={`${uid}-${String(interrupt.id)}`}>
                    Response for {String(interrupt.reason ?? interrupt.id)} (JSON)
                  </label>
                  <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-sm">
                    {JSON.stringify(interrupt, null, 2)}
                  </pre>
                  <textarea
                    className="input"
                    id={`${uid}-${String(interrupt.id)}`}
                    value={answers[String(interrupt.id)] ?? ""}
                    maxLength={16384}
                    disabled={blocked}
                    onChange={(e) =>
                      setAnswers((old) => ({ ...old, [String(interrupt.id)]: e.target.value }))
                    }
                  />
                </div>
              ))}
              <div className="flex gap-2">
                <button
                  className="btn"
                  disabled={blocked}
                  onClick={() =>
                    void send(
                      run.outcome?.interrupts?.map((i) => ({
                        interruptId: String(i.id),
                        status: "cancelled",
                      })),
                    )
                  }
                >
                  Cancel requests
                </button>
                <button
                  className="btn btn-primary"
                  disabled={blocked}
                  onClick={() => {
                    try {
                      void send(
                        run.outcome?.interrupts?.map((i) => ({
                          interruptId: String(i.id),
                          status: "resolved",
                          payload: JSON.parse(answers[String(i.id)] ?? ""),
                        })),
                      );
                    } catch {
                      setError("Enter a valid JSON response for each request.");
                    }
                  }}
                >
                  Send responses
                </button>
              </div>
            </div>
          ) : (
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <label htmlFor={`${uid}-message`}>Message to agent</label>
              <textarea
                id={`${uid}-message`}
                className="input"
                value={text}
                maxLength={32768}
                disabled={blocked || active}
                onChange={(e) => setText(e.target.value)}
              />
              <button
                className="btn btn-primary self-start"
                disabled={blocked || active || !text.trim()}
              >
                Send to agent
              </button>
            </form>
          )}
        </>
      )}
      {unclear && (
        <p role="alert">
          The start request's result is unknown. Workbench is checking its saved run and will not
          send it again. If it cannot be recovered, start a new chat after checking the agent.
        </p>
      )}
      {(error || controller.error) && <p role="alert">{error ?? controller.error}</p>}
    </section>
  );
}
