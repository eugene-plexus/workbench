import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { digest } from "../lib/artifacts";
import { runner } from "../lib/previewRunner";
import { ArtifactPreview, READY_WITHIN_MS, START_WITHIN_MS } from "./ArtifactPreview";

const STORE = "workbench.previews.unfinished";
const FIREFOX = "Mozilla/5.0 Gecko/20100101 Firefox/155.0";
const WEBKIT = "Mozilla/5.0 AppleWebKit/605.1.15 Version/26.6 Safari/605.1.15";
const CHROME = "Mozilla/5.0 AppleWebKit/537.36 Chrome/150.0 Safari/537.36";
const props = {
  chatId: "chat-1",
  blockKey: "message-1:html:original",
  kind: "html" as const,
  source: "<p>Hello</p>",
  title: "Hello",
  auto: true,
};
const key = (over: Partial<typeof props> = {}) => {
  const p = { ...props, ...over };
  return JSON.stringify([p.chatId, `${p.blockKey}:${digest(p.source)}`]);
};
const stored = () => JSON.parse(localStorage.getItem(STORE) ?? "[]") as string[];
const say = (frame: HTMLIFrameElement, data: unknown) =>
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { source: frame.contentWindow, data }));
  });

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  runner.reset();
});

afterEach(() => {
  cleanup();
  runner.reset();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe.each([FIREFOX, WEBKIT])("unfinished previews in %s", (agent) => {
  beforeEach(() => vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agent));

  it("a marked artifact waits for Run with the reason and never starts by itself", () => {
    localStorage.setItem(STORE, JSON.stringify([key()]));
    render(<ArtifactPreview {...props} />);
    act(() => vi.advanceTimersByTime(5_000));
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(screen.getByRole("button", { name: "Run" })).toBeVisible();
    expect(
      screen.getByText(
        "This preview didn't finish last time and may have frozen the page. Run it?",
      ),
    ).toBeVisible();
  });

  it("writes its marker in begin before the frame is ready", () => {
    localStorage.setItem(STORE, JSON.stringify(["another artifact"]));
    const write = vi.spyOn(Storage.prototype, "setItem");
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    expect(stored()).toEqual(["another artifact", key()]);
    expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([
      [STORE, JSON.stringify(["another artifact", key()])],
    ]);
  });

  it("keeps the early marker while a silent frame is replaced", () => {
    render(<ArtifactPreview {...props} />);
    const first = screen.getByTestId("artifact-frame");
    act(() => vi.advanceTimersByTime(READY_WITHIN_MS));
    expect(screen.getByTestId("artifact-frame")).not.toBe(first);
    expect(stored()).toEqual([key()]);
  });

  it("sends run once per frame without writing storage in ready", () => {
    render(<ArtifactPreview {...props} />);
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    const write = vi.spyOn(Storage.prototype, "setItem");
    const sent = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {
      expect(stored()).toEqual([key()]);
    });
    say(frame, { type: "ready" });
    say(frame, { type: "ready" });
    expect(sent).toHaveBeenCalledExactlyOnceWith(
      { type: "run", nonce: expect.any(String), kind: props.kind, source: props.source },
      "*",
    );
    expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
  });

  it("Run starts a held artifact and re-marks it; only its matching finished reply clears it", () => {
    localStorage.setItem(STORE, JSON.stringify([key(), "another artifact"]));
    render(<ArtifactPreview {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    expect(stored()).toEqual(["another artifact", key()]);
    expect(
      screen.queryByText(
        "This preview didn't finish last time and may have frozen the page. Run it?",
      ),
    ).toBeNull();
    const sent = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
    say(frame, { type: "ready" });
    expect(stored()).toContain(key());
    const { nonce } = sent.mock.calls[0]![0] as { nonce: string };
    say(frame, { type: "started", nonce: "another run" });
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { source: window, data: { type: "started", nonce } }),
      );
    });
    expect(stored()).toContain(key());
    say(frame, { type: "started", nonce });
    expect(stored()).toEqual(["another artifact"]);
    fireEvent.click(screen.getByRole("button", { name: "Reload the page" }));
    expect(stored()).toEqual(["another artifact", key()]);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    expect(stored()).toContain(key());
  });

  it.each(["begun", "sent"])("a responsive unmount clears a %s marker", (stage) => {
    localStorage.setItem(STORE, JSON.stringify(["another artifact"]));
    const view = render(<ArtifactPreview {...props} />);
    if (stage === "sent") {
      say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    }
    expect(stored()).toEqual(["another artifact", key()]);
    view.unmount();
    expect(stored()).toEqual(["another artifact"]);
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
  });

  it.each(["begun", "sent"])("Reload while %s keeps the new early marker", (stage) => {
    render(<ArtifactPreview {...props} />);
    if (stage === "sent") {
      say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    }
    fireEvent.click(screen.getByRole("button", { name: "Reload the page" }));
    expect(stored()).toEqual([key()]);
  });

  it.each(["begun", "sent"])("Stop keeps a %s marker on responsive unmount", (stage) => {
    const view = render(<ArtifactPreview {...props} />);
    if (stage === "sent") {
      say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    }
    fireEvent.click(screen.getByRole("button", { name: "Stop the page" }));
    view.unmount();
    expect(stored()).toEqual([key()]);
  });

  it("pagehide clears begun markers and keeps sent and previous-page markers", () => {
    localStorage.setItem(STORE, JSON.stringify(["previous page"]));
    const begun = { ...props, blockKey: "begun" };
    const sent = { ...props, blockKey: "sent", auto: false };
    render(<ArtifactPreview {...begun} />);
    const second = render(<ArtifactPreview {...sent} />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(stored()).toEqual(["previous page", key(begun), key(sent)]);
    const frame = second.container.querySelector("iframe")!;
    const posted = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {
      // Stage must change before postMessage can give the frame a turn.
      window.dispatchEvent(new Event("pagehide"));
    });
    say(frame, { type: "ready" });
    expect(posted).toHaveBeenCalledOnce();
    expect(stored()).toEqual(["previous page", key(sent)]);
    window.dispatchEvent(new Event("pagehide"));
    expect(stored()).toEqual(["previous page", key(sent)]);
  });

  it.each(["begun", "sent"])("ending a %s run responsively clears its marker", (stage) => {
    render(<ArtifactPreview {...props} />);
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    if (stage === "sent") say(frame, { type: "ready" });
    expect(stored()).toContain(key());
    say(frame, { type: "leaving" });
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(stored()).not.toContain(key());
  });

  it("Run resets Stop's marker retention on the same preview", () => {
    const view = render(<ArtifactPreview {...props} />);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    fireEvent.click(screen.getByRole("button", { name: "Stop the page" }));
    expect(stored()).toContain(key());
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    view.unmount();
    expect(stored()).not.toContain(key());
  });

  it.each(["timeout", "Stop"])("%s keeps the marker, until a new responsive run exits", (exit) => {
    const view = render(<ArtifactPreview {...props} />);
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    say(frame, { type: "ready" });
    expect(stored()).toContain(key());
    if (exit === "timeout") {
      // Bootstrap heartbeats keep the runner from stopping it before its start deadline.
      for (let elapsed = 1_000; elapsed < START_WITHIN_MS; elapsed += 1_000) {
        act(() => vi.advanceTimersByTime(1_000));
        say(frame, { type: "alive" });
      }
      act(() => vi.advanceTimersByTime(1_000));
      expect(
        screen.getByText(
          `The page did not start within ${START_WITHIN_MS / 1000} seconds, and was stopped.`,
        ),
      ).toBeVisible();
    } else {
      fireEvent.click(screen.getByRole("button", { name: "Stop the page" }));
    }
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(stored()).toContain(key());
    view.unmount();
    expect(stored()).toContain(key());
    const next = render(<ArtifactPreview {...props} />);
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(screen.getByRole("button", { name: "Run" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    next.unmount();
    expect(stored()).not.toContain(key());
  });

  it("the runner's culprit stop keeps the marker through a responsive unmount", () => {
    const view = render(<ArtifactPreview {...props} />);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    expect(stored()).toContain(key());
    // No heartbeat: the runner names this preview as the one not answering.
    act(() => vi.advanceTimersByTime(START_WITHIN_MS - 1_000));
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(screen.getByText(/stopped answering \(a loop\?\)/)).toBeVisible();
    view.unmount();
    expect(stored()).toContain(key());
  });

  it.each([
    { chatId: "another chat" },
    { blockKey: "another message:html:original" },
    { source: "<p>Fixed</p>" },
  ])("only holds the same chat, message and content: %s", (other) => {
    localStorage.setItem(STORE, JSON.stringify([key()]));
    render(<ArtifactPreview {...props} {...other} />);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    expect(stored()).toContain(key(other));
  });

  it("storage blocked still allows automatic runs", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
  });
});

it("Chromium starts even a marked artifact and never touches the marker store", () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(CHROME);
  localStorage.setItem(STORE, JSON.stringify([key()]));
  const read = vi.spyOn(Storage.prototype, "getItem");
  const write = vi.spyOn(Storage.prototype, "setItem");
  const view = render(<ArtifactPreview {...props} />);
  const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
  const sent = vi.spyOn(frame.contentWindow!, "postMessage").mockImplementation(() => {});
  say(frame, { type: "ready" });
  const { nonce } = sent.mock.calls[0]![0] as { nonce: string };
  say(frame, { type: "started", nonce });
  fireEvent.click(screen.getByRole("button", { name: "Reload the page" }));
  say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
  view.unmount();
  window.dispatchEvent(new Event("pagehide"));
  expect(read.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
  expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
});
