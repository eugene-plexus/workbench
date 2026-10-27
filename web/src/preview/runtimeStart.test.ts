import { act } from "@testing-library/react";
import { createElement } from "react";
import * as ReactDOMClient from "react-dom/client";
import { start } from "./runtime";

vi.mock("react-dom/client", async (actual) => {
  const client = await actual<typeof import("react-dom/client")>();
  return { ...client, createRoot: vi.fn(client.createRoot) };
});

const FIREFOX = "Mozilla/5.0 Gecko/20100101 Firefox/155.0";
const WEBKIT = "Mozilla/5.0 AppleWebKit/605.1.15 Version/26.6 Safari/605.1.15";
const CHROME = "Mozilla/5.0 AppleWebKit/537.36 Chrome/150.0 Safari/537.36";

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  delete (window as unknown as { __previewAlive?: number }).__previewAlive;
});

function running(kind: "html" | "react", source: string) {
  const listeners = vi.spyOn(window, "addEventListener");
  start();
  const receive = listeners.mock.calls.find(([name]) => name === "message")![1] as EventListener;
  return {
    run: () =>
      receive(
        new MessageEvent("message", {
          source: window,
          data: { type: "run", kind, source, nonce: "first-run" },
        }),
      ),
    dispose: () => {
      for (const [name, listener] of listeners.mock.calls)
        window.removeEventListener(name, listener);
    },
  };
}

it.each([FIREFOX, WEBKIT, CHROME])(
  "HTML startup is acknowledged at the right time in %s",
  (agent) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agent);
    const order: string[] = [];
    vi.spyOn(document, "open").mockImplementation(() => {
      order.push("open");
      return null;
    });
    vi.spyOn(document, "write").mockImplementation(() => {
      order.push("execute");
    });
    vi.spyOn(document, "close").mockImplementation(() => {
      order.push("close");
    });
    vi.spyOn(window, "postMessage").mockImplementation((message) => {
      if (message.type === "started") order.push("started");
    });
    const preview = running("html", "<p>Hello</p>");
    try {
      preview.run();
      if (agent !== CHROME) {
        // Inline module scripts can still be pending when document.close returns.
        expect(order).toEqual(["open", "execute", "close"]);
        window.dispatchEvent(new Event("load"));
        expect(order).toEqual(["open", "execute", "close"]);
        vi.advanceTimersByTime(0);
      }
      expect(order).toEqual(
        agent === CHROME
          ? ["started", "open", "execute", "close"]
          : ["open", "execute", "close", "started"],
      );
    } finally {
      preview.dispose();
    }
  },
);

it.each([FIREFOX, WEBKIT])(
  "React startup is acknowledged after its first render in %s",
  (agent) => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agent);
    const order: string[] = [];
    const roots = vi.mocked(ReactDOMClient.createRoot).mockClear();
    vi.spyOn(document.head, "appendChild").mockImplementation((node) => {
      // Supply the compiled module's export without executing a script in jsdom;
      // the real React renderer still calls the component and acknowledges it.
      const box = (
        window as unknown as {
          __artifact: { module: { exports: { default: () => ReturnType<typeof createElement> } } };
        }
      ).__artifact;
      box.module.exports.default = () => {
        order.push("render");
        return createElement("p", null, "Hello");
      };
      return node;
    });
    vi.spyOn(window, "postMessage").mockImplementation((message) => {
      if (message.type === "started") order.push("started");
    });
    const preview = running("react", 'export default function C() { return "Hello"; }');
    try {
      act(() => preview.run());
      expect(order).toEqual(["render", "started"]);
    } finally {
      act(() => {
        for (const result of roots.mock.results)
          if (result.type === "return") result.value.unmount();
      });
      preview.dispose();
    }
  },
);
