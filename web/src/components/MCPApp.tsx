import { useCallback, useEffect, useRef, useState } from "react";
import { post } from "../lib/api";
import {
  object,
  surfacePath,
  words,
  type Action,
  type ObjectValue,
  type Surface,
} from "../lib/interactions";

type RpcId = string | number;
interface Rpc {
  jsonrpc: "2.0";
  id?: RpcId;
  method?: string;
  params?: ObjectValue;
  result?: unknown;
  error?: { code: number; message: string };
}
export function offlineDocument(resource: ObjectValue): string {
  const item = object((resource.contents as ObjectValue[])[0]);
  const policy = object(object(item._meta).ui);
  if (
    policy.domain ||
    Object.keys(object(policy.permissions)).length ||
    Object.values(object(policy.csp)).some((v) => !Array.isArray(v) || v.length)
  ) {
    throw new Error(
      "This app needs network or device access that Workbench's isolated app view does not support. Use its text fallback.",
    );
  }
  if (typeof item.text === "string") return item.text;
  if (typeof item.blob === "string")
    return new TextDecoder("utf-8", { fatal: true }).decode(
      Uint8Array.from(atob(item.blob), (c) => c.charCodeAt(0)),
    );
  throw new Error("The app did not return an HTML document.");
}
function context(): ObjectValue {
  return {
    theme: matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark",
    locale: navigator.language,
    displayMode: "inline",
    availableDisplayModes: ["inline"],
    platform: "web",
  };
}
function AppFrame({
  chat,
  surface,
  actions,
  onAction,
  onStop,
  attempt,
  onRetry,
}: {
  chat: string;
  surface: Surface;
  actions: Action[];
  onAction: (method: string, args: ObjectValue, id?: string) => Promise<Action>;
  onStop: () => void;
  attempt: number;
  onRetry: () => void;
}) {
  const [channel] = useState(() => crypto.randomUUID());
  const frame = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState("Opening isolated app…");
  const [failed, setFailed] = useState(false);
  const latest = useRef(onAction);
  useEffect(() => {
    latest.current = onAction;
  }, [onAction]);
  const completions = useRef<(values: Action[]) => void>(() => {});
  useEffect(() => completions.current(actions), [actions]);
  const path = surfacePath(chat, surface);
  const revision = surface.revision;
  const viewJson = JSON.stringify(surface.view);

  useEffect(() => {
    let initializing = false;
    let live = true,
      loading = false,
      initialized = false,
      ready = false;
    let count = 0,
      since = performance.now();
    let lastPong = performance.now();
    const heartbeatId = `host-ping:${channel}`;
    const seen = new Map<RpcId, Rpc | null>();
    const pending = new Map<string, RpcId>();
    const send = (message: Rpc) => {
      if (live) frame.current?.contentWindow?.postMessage({ channel, message }, "*");
    };
    const finish = (id: RpcId, result?: unknown, error?: string, code = -32000) => {
      const response: Rpc = error
        ? { jsonrpc: "2.0", id, error: { code, message: error } }
        : { jsonrpc: "2.0", id, result: result ?? {} };
      seen.set(id, response);
      send(response);
    };
    const notify = (method: string, params: ObjectValue) =>
      send({ jsonrpc: "2.0", method, params });
    const read = (method: string, params: ObjectValue) =>
      post<ObjectValue>(`${path}/mcp-apps/read`, { revision, method, params });
    const fail = (error: unknown) => {
      if (live) {
        setStatus(words(error));
        setFailed(true);
      }
    };
    const timer = setTimeout(() => {
      if (!ready)
        fail(new Error("The app did not finish opening. Stop it and use the text fallback."));
    }, 60000);
    // Chrome can keep a loop's old sandbox process alive briefly after removal.
    // Retry only an empty proxy that never became ready, never a loaded app.
    const proxyTimer = setTimeout(() => {
      if (loading) return;
      if (attempt < 6) onRetry();
      else
        fail(
          new Error(
            "The isolated app process is not responding. Stop it and use the text fallback.",
          ),
        );
    }, 3000);
    const view = JSON.parse(viewJson) as ObjectValue;
    completions.current = (values) => {
      for (const action of values) {
        const id = pending.get(action.id);
        if (id === undefined || ["proposed", "queued", "dispatching"].includes(action.status))
          continue;
        pending.delete(action.id);
        finish(
          id,
          action.result,
          action.status === "completed" ? undefined : (action.error ?? `Action ${action.status}.`),
        );
      }
    };
    const receive = async (event: MessageEvent) => {
      if (
        event.source !== frame.current?.contentWindow ||
        event.origin !== "null" ||
        event.data?.channel !== channel
      )
        return;
      const msg = event.data.message as Rpc;
      if (
        msg?.jsonrpc === "2.0" &&
        msg.id === heartbeatId &&
        !msg.method &&
        ("result" in msg || "error" in msg)
      ) {
        lastPong = performance.now();
        return;
      }
      if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") return;
      if (performance.now() - since > 1000) {
        count = 0;
        since = performance.now();
      }
      if (++count > 40) return;
      if (msg.method === "ui/notifications/sandbox-proxy-ready") {
        if (loading) return;
        loading = true;
        try {
          const html = offlineDocument(
            await read("resources/read", { uri: String(view.resourceUri) }),
          );
          notify("ui/notifications/sandbox-resource-ready", { html });
        } catch (error) {
          fail(error);
        }
        return;
      }
      if (msg.id === undefined) {
        if (msg.method === "ui/notifications/initialized" && initialized && !ready) {
          ready = true;
          lastPong = performance.now();
          clearTimeout(timer);
          setStatus("App open. Requested actions need your review below.");
          notify("ui/notifications/tool-input", object(view.toolInput));
          notify("ui/notifications/tool-result", object(view.toolResult));
        }
        if (
          ready &&
          msg.method === "ui/notifications/size-changed" &&
          typeof msg.params?.height === "number" &&
          Number.isFinite(msg.params.height)
        ) {
          frame.current?.setAttribute(
            "height",
            String(Math.max(120, Math.min(640, msg.params.height))),
          );
        }
        return;
      }
      const id = msg.id;
      if (!(
        (typeof id === "string" && id.length <= 80) ||
        (typeof id === "number" && Number.isSafeInteger(id))
      ))
        return;
      if (seen.has(id)) {
        const old = seen.get(id);
        if (old) send(old);
        return;
      }
      if (seen.size >= 128 || [...seen.values()].filter((v) => v === null).length >= 16) {
        send({
          jsonrpc: "2.0",
          id,
          error: { code: -32000, message: "The app has reached its request limit." },
        });
        return;
      }
      seen.set(id, null);
      try {
        if (JSON.stringify(msg).length > 20480) {
          finish(id, undefined, "Request too large.", -32600);
          return;
        }
        const params = msg.params ?? {};
        if (msg.method === "ui/initialize") {
          if (initializing || initialized) {
            finish(id, undefined, "The app is already initialized.");
            return;
          }
          // Reserve the handshake before awaiting network IO.
          initializing = true;
          const result = await read(msg.method, params);
          initialized = true;
          finish(id, {
            ...result,
            hostInfo: { name: "Eugene Workbench", version: "1" },
            hostCapabilities: {
              serverTools: {},
              serverResources: {},
              sandbox: { csp: {}, permissions: {} },
            },
            hostContext: context(),
          });
        } else if (!ready) finish(id, undefined, "Initialize the app first.");
        else if (["ping", "tools/list", "resources/read"].includes(msg.method))
          finish(id, await read(msg.method, params));
        else if (["tools/call", "ui/message", "ui/update-model-context"].includes(msg.method)) {
          const action = await latest.current(msg.method, params);
          pending.set(action.id, id);
          completions.current([action]);
        } else finish(id, undefined, "This app method is not supported.", -32601);
      } catch (error) {
        if (msg.method === "ui/initialize") initializing = false;
        finish(id, undefined, words(error));
      }
    };
    const handler = (event: MessageEvent) => {
      void receive(event);
    };
    const heartbeat = setInterval(() => {
      if (!ready) return;
      if (performance.now() - lastPong > 6000) {
        clearInterval(heartbeat);
        fail(new Error("The app stopped responding. Its isolated view has been closed."));
      } else send({ jsonrpc: "2.0", id: heartbeatId, method: "ping", params: {} });
    }, 2000);
    window.addEventListener("message", handler);
    const theme = new MutationObserver(() => {
      if (ready) notify("ui/notifications/host-context-changed", context());
    });
    theme.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme", "class"],
    });
    const scheme = matchMedia("(prefers-color-scheme: light)");
    const changed = () => {
      if (ready) notify("ui/notifications/host-context-changed", context());
    };
    scheme.addEventListener("change", changed);
    return () => {
      live = false;
      clearTimeout(timer);
      clearTimeout(proxyTimer);
      clearInterval(heartbeat);
      pending.clear();
      window.removeEventListener("message", handler);
      theme.disconnect();
      scheme.removeEventListener("change", changed);
      completions.current = () => {};
    };
  }, [channel, path, revision, viewJson, attempt, onRetry]);

  return (
    <div className="flex flex-col gap-2">
      <p role={failed ? "alert" : "status"}>{status}</p>
      <button className="btn self-start" onClick={onStop}>
        Stop app
      </button>
      {!failed && (
        <iframe
          ref={frame}
          title="Isolated MCP app"
          src={`/mcp-apps/frame#${channel}`}
          sandbox="allow-scripts allow-forms"
          allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-write 'none'"
          referrerPolicy="no-referrer"
          height={320}
          className="w-full rounded border border-line"
        />
      )}
    </div>
  );
}

export function MCPApp(
  props: Omit<Parameters<typeof AppFrame>[0], "onStop" | "onRetry" | "attempt">,
) {
  const [open, setOpen] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return open ? (
    <AppFrame
      key={`${props.surface.revision}:${attempt}`}
      {...props}
      attempt={attempt}
      onRetry={retry}
      onStop={() => setOpen(false)}
    />
  ) : (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-muted">
        Runs in an isolated view without network or device access.
      </p>
      <button
        className="btn self-start"
        onClick={() => {
          setAttempt(0);
          setOpen(true);
        }}
      >
        Open app
      </button>
    </div>
  );
}
