import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { setDisplay } from "./display";
import { post } from "./api";
import { resolve, type Action, type ObjectValue, type Surface } from "./interactions";
import { runInput, type Run } from "./agui";
import { A2UIForm } from "../components/A2UIForm";
import { MCPApp, offlineDocument } from "../components/MCPApp";

vi.mock("./api", () => ({ post: vi.fn() }));
const surface: Surface = {
  id: "s",
  messageId: "a",
  protocol: "a2ui",
  version: "0.9.1",
  revision: 1,
  state: "active",
  actionable: true,
  expiresAt: 9999999999,
  fallback: "Summary",
  view: {
    surfaceId: "form",
    ready: true,
    dataModel: { name: "Ada", ok: false },
    components: [
      { id: "root", component: "Column", children: ["name", "ok", "submit"] },
      { id: "name", component: "TextField", label: "Name", value: { path: "/name" } },
      { id: "ok", component: "CheckBox", label: "Confirmed", value: { path: "/ok" } },
      {
        id: "submit",
        component: "Button",
        child: "label",
        action: { event: { name: "save", context: { name: { path: "/name" } } } },
      },
      { id: "label", component: "Text", text: "Submit" },
    ],
  },
};
beforeEach(() => {
  vi.mocked(post).mockReset();
});

it("theme override reaches a preview that is still opening", async () => {
  vi.mocked(post).mockResolvedValue({ protocolVersion: "2026-01-26" });
  setDisplay({ theme: "light" });
  render(
    <MCPApp
      chat="c"
      surface={{ ...surface, protocol: "mcp-apps", view: { resourceUri: "ui://app" } }}
      actions={[]}
      onAction={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open app" }));
  const frame = screen.getByTitle("Isolated MCP app") as HTMLIFrameElement;
  const channel = new URL(frame.src).hash.slice(1);
  const output = vi.spyOn(frame.contentWindow!, "postMessage");
  const emit = async (method: string, id?: number) => {
    await act(async () =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          origin: "null",
          data: { channel, message: { jsonrpc: "2.0", method, id } },
        }),
      ),
    );
  };
  await emit("ui/initialize", 1);
  output.mockClear();
  act(() => setDisplay({ theme: "dark" }));
  await emit("ui/notifications/initialized");
  expect(output).toHaveBeenCalledWith(
    {
      channel,
      message: {
        jsonrpc: "2.0",
        method: "ui/notifications/host-context-changed",
        params: expect.objectContaining({ theme: "dark" }),
      },
    },
    "*",
  );
  expect(screen.getByTitle("Isolated MCP app")).toBe(frame);
  fireEvent.click(screen.getByRole("button", { name: "Stop app" }));
  setDisplay({ theme: "system" });
});

it("resolves escaped pointers and pure checks without inherited properties", () => {
  expect(resolve({ path: "/a~1b/~0key" }, { "a/b": { "~key": "value" } })).toBe("value");
  expect(resolve({ path: "/constructor" }, {})).toBeNull();
  expect(resolve({ call: "required", args: { value: { path: "/empty" } } }, { empty: [] })).toBe(
    false,
  );
  expect(
    resolve({ call: "and", args: { values: [true, { call: "not", args: { value: false } }] } }, {}),
  ).toBe(true);
  expect(() => resolve({ call: "not", args: { value: "false" } }, {})).toThrow("boolean");
  expect(() => resolve({ call: "fetch", args: {} }, {})).toThrow("unsupported");
});

it("saves all drafts in revision order and blocks submission until they are saved", async () => {
  const action = vi.fn(),
    refresh = vi.fn();
  vi.mocked(post).mockResolvedValueOnce({ revision: 2 }).mockResolvedValueOnce({ revision: 3 });
  render(
    <A2UIForm chat="c" surface={surface} disabled={false} onAction={action} onChanged={refresh} />,
  );
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Grace" } });
  fireEvent.click(screen.getByLabelText("Confirmed"));
  expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(vi.mocked(post).mock.calls.map((c) => c[1])).toEqual([
    { revision: 1, componentId: "name", value: "Grace" },
    { revision: 2, componentId: "ok", value: true },
  ]);
  expect(action).not.toHaveBeenCalled();
});

it("preserves the revision at which editing began and leaves a refused draft visible", async () => {
  const props = { chat: "c", surface, disabled: false, onAction: vi.fn(), onChanged: vi.fn() };
  const shown = render(<A2UIForm {...props} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Grace" } });
  shown.rerender(<A2UIForm {...props} surface={{ ...surface, revision: 2 }} />);
  vi.mocked(post).mockRejectedValue(new Error("The form changed."));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("The form changed.");
  expect(post).toHaveBeenCalledWith(expect.anything(), {
    revision: 1,
    componentId: "name",
    value: "Grace",
  });
  expect(screen.getByLabelText("Name")).toHaveValue("Grace");
  expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled();
});

it("refuses resource policy grants outside the offline sandbox", () => {
  const policies: ObjectValue[] = [
    { domain: "https://example.test" },
    { permissions: { camera: {} } },
    { csp: { connectDomains: ["https://example.test"] } },
  ];
  for (const ui of policies) {
    expect(() => offlineDocument({ contents: [{ text: "html", _meta: { ui } }] })).toThrow(
      "does not support",
    );
  }
  expect(
    offlineDocument({ contents: [{ text: "html", _meta: { ui: { csp: {}, permissions: {} } } }] }),
  ).toBe("html");
});

it("builds continuation from saved state and resumes without appending a user message", () => {
  const old: Run = {
    threadId: "c",
    runId: "r1",
    direction: "connect",
    agentId: "x",
    status: "interrupted",
    cursor: 8,
    messages: [{ id: "u", role: "user", content: "first" }],
    state: null,
    answerId: "a",
    outcome: { type: "interrupt" },
  };
  const resume = [{ interruptId: "i", status: "resolved", payload: true }];
  const value = runInput("c", old, "must not be sent", resume);
  expect(value).not.toHaveProperty("state");
  expect(value.messages).toEqual(old.messages);
  expect(value.parentRunId).toBe("r1");
  expect(value.resume).toEqual(resume);
  expect(value.runId).not.toBe(old.runId);
});

it("requires frame, opaque origin, channel and completed handshake; duplicates cannot propose twice", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.mocked(post).mockImplementation(async (_path, body) => {
    const method = (body as { method: string }).method;
    if (method === "resources/read")
      return { contents: [{ text: "<!doctype html><html></html>" }] };
    if (method === "ui/initialize") return { protocolVersion: "2026-01-26" };
    return {};
  });
  const action: Action = {
    id: "action",
    surfaceId: "s",
    revision: 1,
    name: "tools/call",
    arguments: {},
    status: "proposed",
    result: null,
    error: null,
  };
  const onAction = vi.fn().mockResolvedValue(action);
  const props = {
    chat: "c",
    surface: {
      ...surface,
      protocol: "mcp-apps",
      version: "2026-01-26",
      view: { resourceUri: "ui://app", toolInput: { arguments: {} }, toolResult: { content: [] } },
    },
    actions: [] as Action[],
    onAction,
  };
  setDisplay({ theme: "light" });
  const shown = render(<MCPApp {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "Open app" }));
  const frame = screen.getByTitle("Isolated MCP app") as HTMLIFrameElement;
  const channel = new URL(frame.src).hash.slice(1);
  const output = vi.spyOn(frame.contentWindow!, "postMessage");
  async function emit(method: string, id?: number, over: Partial<MessageEventInit> = {}) {
    await act(async () =>
      window.dispatchEvent(
        new MessageEvent("message", {
          source: frame.contentWindow,
          origin: "null",
          data: { channel, message: { jsonrpc: "2.0", method, id, params: {} } },
          ...over,
        }),
      ),
    );
  }
  await emit("ui/notifications/sandbox-proxy-ready");
  await emit("tools/call", 10);
  expect(onAction).not.toHaveBeenCalled();
  await emit("ui/initialize", 1);
  expect(output).toHaveBeenCalledWith(
    {
      channel,
      message: expect.objectContaining({
        id: 1,
        result: expect.objectContaining({
          hostContext: expect.objectContaining({ theme: "light" }),
        }),
      }),
    },
    "*",
  );
  await emit("ui/notifications/initialized");
  act(() => setDisplay({ theme: "dark" }));
  expect(output).toHaveBeenCalledWith(
    {
      channel,
      message: expect.objectContaining({
        method: "ui/notifications/host-context-changed",
        params: expect.objectContaining({ theme: "dark" }),
      }),
    },
    "*",
  );
  expect(screen.getByTitle("Isolated MCP app")).toBe(frame);
  await emit("tools/call", 11, { source: window });
  await emit("tools/call", 12, { origin: "https://wrong.test" });
  await emit("tools/call", 13, {
    data: {
      channel: "wrong",
      message: { jsonrpc: "2.0", method: "tools/call", id: 13, params: {} },
    },
  });
  expect(onAction).not.toHaveBeenCalled();
  await emit("tools/call", 2);
  await emit("tools/call", 2);
  expect(onAction).toHaveBeenCalledOnce();
  shown.rerender(
    <MCPApp {...props} actions={[{ ...action, status: "completed", result: { ok: true } }]} />,
  );
  await waitFor(() =>
    expect(output).toHaveBeenCalledWith(
      { channel, message: { jsonrpc: "2.0", id: 2, result: { ok: true } } },
      "*",
    ),
  );
  await emit("tools/call", 2);
  expect(onAction).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Stop app" }));
  await emit("tools/call", 14);
  expect(onAction).toHaveBeenCalledOnce();
  setDisplay({ theme: "system" });
  vi.unstubAllGlobals();
});
