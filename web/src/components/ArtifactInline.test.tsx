/**
 * Artifacts shown and run in place (rich content RC6): drawn where the answer
 * puts them, a page or a component started by itself once on screen when the
 * person lets previews run, never in the owner's view of someone's chat, and
 * its frame as tall as its page.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../lib/api";
import { type ArtifactSummary, digest, forgetDrafts } from "../lib/artifacts";
import { runner } from "../lib/previewRunner";
import type { Me } from "../lib/types";
import { ArtifactScope, RunPreviews, type Scope } from "./Artifact";
import { frameHeight, LEAST_HEIGHT, MESSAGES_PER_SECOND, SIZE_EVERY_MS } from "./ArtifactPreview";
import { Markdown } from "./Markdown";
import { Sidebar } from "./Sidebar";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

const PAGE = "<h1>Prices</h1>";
const fence = (language: string, title: string, body: string) =>
  `\`\`\`${language} artifact title="${title}"\n${body}\n\`\`\`\n`;

function scope(over: Partial<Scope> = {}): Scope {
  return {
    chatId: "c1",
    messageId: "m1",
    artifacts: [],
    readOnly: false,
    writing: false,
    onKept: vi.fn(),
    ...over,
  };
}

function shown(
  text: string,
  { run = true, value = scope() }: { run?: boolean; value?: Scope } = {},
) {
  return render(
    <RunPreviews.Provider value={run}>
      <ArtifactScope.Provider value={value}>
        <Markdown text={text} />
      </ArtifactScope.Provider>
    </RunPreviews.Provider>,
  );
}

const card = (title: string) =>
  screen.getAllByTestId("artifact-card").find((c) => within(c).queryByText(title))!;

beforeEach(() => {
  // jsdom identifies as WebKit; unfinished frames must not leak between tests.
  localStorage.clear();
  vi.mocked(api).mockReset();
  forgetDrafts();
  runner.reset();
});

describe("an artifact in its answer", () => {
  it("is drawn where it stands: Markdown as it reads, SVG as a picture", async () => {
    shown(
      fence("md", "Notes", "# Shopping\n\n- bread") +
        fence(
          "svg",
          "Dot",
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><circle r="2"/></svg>',
        ),
    );
    const notes = card("Notes");
    expect(await within(notes).findByRole("heading", { name: "Shopping" })).toBeInTheDocument();
    const dot = card("Dot");
    expect((await within(dot).findByRole("img", { name: "Dot" })).getAttribute("src")).toMatch(
      /^data:image\/svg\+xml/,
    );
  });

  it("runs a page by itself, in place, when the person lets previews run", async () => {
    shown(fence("html", "Prices", PAGE));
    const frame = await within(card("Prices")).findByTestId("artifact-frame");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-forms");
    expect(frame.className).toContain("artifact-frame-inline");
    // Stopped by the person, it stays stopped.
    fireEvent.click(within(card("Prices")).getByRole("button", { name: "Stop the page" }));
    expect(within(card("Prices")).queryByTestId("artifact-frame")).toBeNull();
    await new Promise((done) => setTimeout(done, 50));
    expect(within(card("Prices")).queryByTestId("artifact-frame")).toBeNull();
  });

  it("waits for Run when the person turned that off, and in the owner's view of a chat", async () => {
    const { unmount } = shown(fence("html", "Prices", PAGE), { run: false });
    expect(await within(card("Prices")).findByRole("button", { name: "Run" })).toBeVisible();
    // Long enough for one that runs by itself to have started (as the next test's does).
    await new Promise((done) => setTimeout(done, 50));
    expect(within(card("Prices")).queryByTestId("artifact-frame")).toBeNull();
    unmount();
    shown(fence("html", "Prices", PAGE), { value: scope({ readOnly: true }) });
    expect(await within(card("Prices")).findByRole("button", { name: "Run" })).toBeVisible();
    await new Promise((done) => setTimeout(done, 50));
    expect(within(card("Prices")).queryByTestId("artifact-frame")).toBeNull();
  });

  it("starts one preview at a time", async () => {
    shown(fence("html", "One", "<p>1</p>") + fence("html", "Two", "<p>2</p>"));
    await within(card("One")).findByTestId("artifact-frame");
    expect(within(card("Two")).queryByTestId("artifact-frame")).toBeNull();
  });

  it("takes its page's height, between its least and most of the window", async () => {
    shown(fence("html", "Prices", PAGE));
    const frame = (await within(card("Prices")).findByTestId(
      "artifact-frame",
    )) as HTMLIFrameElement;
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "size", height: 300 },
          source: frame.contentWindow,
        }),
      );
    });
    await waitFor(() =>
      expect(frame.style.height).toBe(`${frameHeight(300, window.innerHeight)}px`),
    );
    expect(frameHeight(10, 1000)).toBe(LEAST_HEIGHT);
    expect(frameHeight(5000, 1000)).toBe(700);
  });

  it("reads a frame that floods the page only so far, and moves its height only so often", async () => {
    shown(fence("html", "Prices", PAGE));
    const frame = (await within(card("Prices")).findByTestId(
      "artifact-frame",
    )) as HTMLIFrameElement;
    const heights: string[] = [];
    const watched = new MutationObserver(() => heights.push(frame.style.height));
    watched.observe(frame, { attributes: true, attributeFilter: ["style"] });
    act(() => {
      for (let i = 0; i < MESSAGES_PER_SECOND * 5; i++) {
        window.dispatchEvent(
          new MessageEvent("message", {
            data: { type: "size", height: 200 + i },
            source: frame.contentWindow,
          }),
        );
      }
    });
    await new Promise((done) => setTimeout(done, SIZE_EVERY_MS * 2));
    watched.disconnect();
    // The first at once, then one more for the burst: never one a message.
    expect(heights.length).toBeLessThanOrEqual(3);
    // Past the frame's share of messages, nothing it said was read.
    const last = Number.parseInt(frame.style.height, 10);
    expect(last).toBeLessThan(frameHeight(200 + MESSAGES_PER_SECOND, window.innerHeight) + 1);
  });

  it("moves its height at most every so often while its page keeps changing", async () => {
    shown(fence("html", "Prices", PAGE));
    const frame = (await within(card("Prices")).findByTestId(
      "artifact-frame",
    )) as HTMLIFrameElement;
    const heights: string[] = [];
    const watched = new MutationObserver(() => heights.push(frame.style.height));
    watched.observe(frame, { attributes: true, attributeFilter: ["style"] });
    // A page that grows by 10 px every 20 ms, for 400 ms.
    for (let i = 0; i < 20; i++) {
      act(() => {
        window.dispatchEvent(
          new MessageEvent("message", {
            data: { type: "size", height: 200 + i * 10 },
            source: frame.contentWindow,
          }),
        );
      });
      await new Promise((done) => setTimeout(done, 20));
    }
    await new Promise((done) => setTimeout(done, SIZE_EVERY_MS + 50));
    watched.disconnect();
    // At once, then about every SIZE_EVERY_MS: never one a message.
    expect(heights.length).toBeGreaterThanOrEqual(2);
    expect(heights.length).toBeLessThanOrEqual(5);
  });

  it("holds no artifacts of its own: a Markdown artifact's fences are plain blocks", async () => {
    // Four backticks around it, so the fence inside is the Markdown's own.
    shown(
      `\`\`\`\`md artifact title="Notes"\n# Inside\n\n${fence("html", "Nested", PAGE)}\`\`\`\`\n`,
    );
    await within(card("Notes")).findByRole("heading", { name: "Inside" });
    expect(screen.getAllByTestId("artifact-card")).toHaveLength(1);
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
  });

  it("runs nothing by itself in an answer still being written", async () => {
    shown(fence("html", "Prices", PAGE), { value: scope({ writing: true }) });
    expect(await within(card("Prices")).findByRole("button", { name: "Run" })).toBeVisible();
    await new Promise((done) => setTimeout(done, 50));
    expect(within(card("Prices")).queryByTestId("artifact-frame")).toBeNull();
  });

  it("shows the last revision kept of it", async () => {
    const kept: ArtifactSummary = {
      id: "a1",
      kind: "markdown",
      title: "Notes",
      digest: digest("# One"),
      revisions: 2,
      createdAt: 1,
      updatedAt: 2,
    };
    vi.mocked(api).mockImplementation(async (path: string) =>
      path.endsWith("/revisions/2")
        ? { number: 2, author: "person", size: 5, createdAt: 2, source: "# Two" }
        : {
            ...kept,
            messageId: "m1",
            revisionList: [
              { number: 1, author: "answer", size: 5, createdAt: 1 },
              { number: 2, author: "person", size: 5, createdAt: 2 },
            ],
          },
    );
    shown(fence("md", "Notes", "# One"), { value: scope({ artifacts: [kept] }) });
    expect(await within(card("Notes")).findByRole("heading", { name: "Two" })).toBeInTheDocument();
    expect(within(card("Notes")).queryByRole("heading", { name: "One" })).toBeNull();
  });
});

describe("the person's switch", () => {
  it("shows what Workbench keeps, and says when keeping it failed", async () => {
    const me: Me = { sub: "p", name: "Ada", username: "ada", owner: false, ownerReadsChats: false };
    const onRunPreviews = vi
      .fn<(on: boolean) => Promise<void>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(undefined);
    const props = {
      chats: [],
      current: null,
      onOpen: vi.fn(),
      onOpenMedia: vi.fn(),
      onClose: vi.fn(),
      onNew: vi.fn(),
      onSignOut: vi.fn(),
      creating: false,
      error: null,
      onRetry: vi.fn(),
      onRunPreviews,
    };
    const { rerender } = render(<Sidebar me={me} {...props} />);
    const box = screen.getByRole("checkbox", { name: "Run previews when shown" });
    expect(box).toBeChecked();
    fireEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("Not changed: offline");
    expect(box).toBeChecked();
    fireEvent.click(box);
    await waitFor(() => expect(onRunPreviews).toHaveBeenLastCalledWith(false));
    rerender(<Sidebar me={{ ...me, runPreviews: false }} {...props} />);
    expect(screen.getByRole("checkbox", { name: "Run previews when shown" })).not.toBeChecked();
  });
});
