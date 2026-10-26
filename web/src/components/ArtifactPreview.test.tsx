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

  it("does not write a marker before the frame is ready", () => {
    localStorage.setItem(STORE, JSON.stringify(["another artifact"]));
    const write = vi.spyOn(Storage.prototype, "setItem");
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(READY_WITHIN_MS));
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    expect(stored()).toEqual(["another artifact"]);
    expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
  });

  it("writes its marker immediately before sending run, once per frame", () => {
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
    expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([
      [STORE, JSON.stringify([key()])],
    ]);
  });

  it("Run starts a held artifact and re-marks it; only its matching finished reply clears it", () => {
    localStorage.setItem(STORE, JSON.stringify([key(), "another artifact"]));
    render(<ArtifactPreview {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    expect(stored()).toEqual([key(), "another artifact"]);
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
    expect(stored()).toEqual(["another artifact"]);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    expect(stored()).toContain(key());
  });

  it("unmounting a sent-but-not-started preview clears its marker", () => {
    localStorage.setItem(STORE, JSON.stringify(["another artifact"]));
    const view = render(<ArtifactPreview {...props} />);
    say(screen.getByTestId("artifact-frame") as HTMLIFrameElement, { type: "ready" });
    expect(stored()).toEqual(["another artifact", key()]);
    view.unmount();
    expect(stored()).toEqual(["another artifact"]);
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
  });

  it("ending a sent-but-not-started run clears its marker", () => {
    render(<ArtifactPreview {...props} />);
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    say(frame, { type: "ready" });
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
  expect(read.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
  expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
});
