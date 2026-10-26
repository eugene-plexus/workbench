import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { digest } from "../lib/artifacts";
import { runner } from "../lib/previewRunner";
import { ArtifactPreview } from "./ArtifactPreview";

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

  it("an unmarked artifact starts by itself and records its start", () => {
    localStorage.setItem(STORE, JSON.stringify(["another artifact"]));
    render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    expect(stored()).toEqual(["another artifact", key()]);
  });

  it("Run starts a held artifact and re-marks it; only its matching finished reply clears it", () => {
    localStorage.setItem(STORE, JSON.stringify([key(), "another artifact"]));
    render(<ArtifactPreview {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const frame = screen.getByTestId("artifact-frame") as HTMLIFrameElement;
    expect(stored()).toEqual(["another artifact", key()]);
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
    expect(stored()).toContain(key());
  });

  it("an unfinished start survives unmount and its next showing waits for Run", () => {
    const view = render(<ArtifactPreview {...props} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
    view.unmount();
    render(<ArtifactPreview {...props} />);
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
    expect(screen.getByRole("button", { name: "Run" })).toBeVisible();
  });

  it.each([
    { chatId: "another chat" },
    { blockKey: "another message:html:original" },
    { source: "<p>Fixed</p>" },
  ])("only holds the same chat, message and content: %s", (other) => {
    localStorage.setItem(STORE, JSON.stringify([key()]));
    render(<ArtifactPreview {...props} {...other} />);
    expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
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
  render(<ArtifactPreview {...props} />);
  expect(screen.getByTestId("artifact-frame")).toBeInTheDocument();
  expect(read.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
  expect(write.mock.calls.filter(([name]) => name === STORE)).toEqual([]);
});
