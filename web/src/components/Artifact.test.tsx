/**
 * Artifacts in an answer (rich content RC5): a card for a block the answer asked
 * to be one, Open as artifact on a plain one, a dialog that runs nothing until
 * Run, a frame held apart, and every edit kept as a new revision.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, ApiError, post } from "../lib/api";
import { type ArtifactSummary, digest, forgetDrafts } from "../lib/artifacts";
import { chatMarkdown } from "../lib/conveniences";
import type { ChatDetail } from "../lib/types";
import { ArtifactScope, RunPreviews, type Scope } from "./Artifact";
import { PREVIEW_FRAME, readSaid } from "./ArtifactPreview";
import { Markdown } from "./Markdown";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

const PAGE = "<h1>Prices</h1>\n<script>parent.document.title = 'got you'</script>";
const ASKED = `Here:\n\n\`\`\`html artifact title="Prices"\n${PAGE}\n\`\`\`\n`;

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

/** An answer, with previews waiting for Run (RC5's way; RC6's running by themselves is
 * ArtifactInline.test.tsx's). */
function shown(text: string, value: Scope | null = scope()) {
  return render(
    <RunPreviews.Provider value={false}>
      {value ? (
        <ArtifactScope.Provider value={value}>
          <Markdown text={text} />
        </ArtifactScope.Provider>
      ) : (
        <Markdown text={text} />
      )}
    </RunPreviews.Provider>,
  );
}

async function openCard(name = "Open Prices") {
  fireEvent.click(screen.getByRole("button", { name }));
  return screen.findByTestId("artifact-dialog");
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  forgetDrafts();
});

describe("in an answer", () => {
  it("shows a block the answer asked to be an artifact as its card, not its code", () => {
    shown(ASKED);
    const card = screen.getByTestId("artifact-card");
    expect(card).toHaveTextContent("Prices");
    expect(card).toHaveTextContent("HTML · From the answer");
    expect(screen.queryByText(/got you/)).toBeNull();
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("keeps it code while its fence is open, and outside an answer", () => {
    shown(ASKED.replace(/```\n$/, ""));
    expect(screen.queryByTestId("artifact-card")).toBeNull();
    expect(screen.getByText(/got you/)).toBeInTheDocument();
    shown(ASKED, null);
    expect(screen.queryAllByTestId("artifact-card")).toHaveLength(0);
  });

  it("offers Open as artifact on a plain block of a kind it can be, and nowhere else", () => {
    shown("```svg\n<svg/>\n```\n\n```python\nprint(1)\n```\n");
    expect(screen.getAllByRole("button", { name: /as an artifact/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Open this SVG as an artifact" })).toBeVisible();
  });

  it("says how many revisions a block has kept", () => {
    const kept: ArtifactSummary = {
      id: "a1",
      kind: "html",
      title: "Prices v2",
      digest: digest(PAGE),
      revisions: 3,
      createdAt: 1,
      updatedAt: 2,
    };
    shown(ASKED, scope({ artifacts: [kept] }));
    expect(screen.getByTestId("artifact-card")).toHaveTextContent("Prices v2");
    expect(screen.getByTestId("artifact-card")).toHaveTextContent("HTML · 3 revisions");
  });
});

describe("its preview", () => {
  it("runs nothing until Run, then only in a frame of its own", async () => {
    shown(ASKED);
    const dialog = await openCard();
    expect(dialog.querySelector("iframe")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    const frame = within(dialog).getByTestId("artifact-frame");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-forms");
    expect(frame.getAttribute("src")).toBe(PREVIEW_FRAME);
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
    fireEvent.click(within(dialog).getByRole("button", { name: "Stop the page" }));
    expect(within(dialog).queryByTestId("artifact-frame")).toBeNull();
  });

  it("sends the source only to its own frame once it is ready, and hears only it", async () => {
    shown(ASKED);
    const dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    const frame = within(dialog).getByTestId("artifact-frame") as HTMLIFrameElement;
    const own = frame.contentWindow!;
    const sent = vi.spyOn(own, "postMessage");
    // Another window saying it is ready, or that it failed, is not heard.
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { type: "ready" }, source: window }),
      );
      window.dispatchEvent(
        new MessageEvent("message", { data: { type: "error", message: "spoof" }, source: window }),
      );
    });
    expect(sent).not.toHaveBeenCalled();
    expect(within(dialog).queryByText(/spoof/)).toBeNull();
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "ready" }, source: own }));
      window.dispatchEvent(new MessageEvent("message", { data: { type: "ready" }, source: own }));
    });
    expect(sent).toHaveBeenCalledTimes(1);
    const [message, target] = sent.mock.calls[0] as [Record<string, string>, string];
    expect(message).toMatchObject({ type: "run", kind: "html", source: PAGE });
    expect(message.nonce).toMatch(/^[0-9a-f]{32}$/);
    expect(target).toBe("*");
    expect(within(dialog).getByRole("status")).toHaveTextContent("Starting…");
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", { data: { type: "started", nonce: "wrong" }, source: own }),
      );
    });
    expect(within(dialog).getByRole("status")).toHaveTextContent("Starting…");
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "started", nonce: message.nonce },
          source: own,
        }),
      );
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "error", message: "<b>boom</b>" },
          source: own,
        }),
      );
    });
    expect(within(dialog).getByRole("status")).toHaveTextContent("Running");
    expect(within(dialog).getByTestId("artifact-notes")).toHaveTextContent(
      "The page said: <b>boom</b>",
    );
    expect(dialog.querySelector("b")).toBeNull();
  });

  it("stops a preview that tries to go elsewhere, and one that never starts", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      shown(ASKED);
      const dialog = await openCard();
      fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
      const own = (within(dialog).getByTestId("artifact-frame") as HTMLIFrameElement)
        .contentWindow!;
      act(() => {
        window.dispatchEvent(
          new MessageEvent("message", { data: { type: "leaving" }, source: own }),
        );
      });
      expect(within(dialog).queryByTestId("artifact-frame")).toBeNull();
      expect(within(dialog).getByTestId("artifact-notes")).toHaveTextContent(
        "tried to go to another page, and was stopped",
      );
      // Ready, and then silent: stopped.
      fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
      const ready = (within(dialog).getByTestId("artifact-frame") as HTMLIFrameElement)
        .contentWindow!;
      act(() => {
        window.dispatchEvent(
          new MessageEvent("message", { data: { type: "ready" }, source: ready }),
        );
      });
      act(() => {
        vi.advanceTimersByTime(10_001);
      });
      expect(within(dialog).queryByTestId("artifact-frame")).toBeNull();
      expect(within(dialog).getByTestId("artifact-notes")).toHaveTextContent(
        "did not start within 10 seconds",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("puts a frame that never says it is ready in a new one, and gives up in words", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      shown(ASKED);
      const dialog = await openCard();
      fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
      const first = within(dialog).getByTestId("artifact-frame");
      act(() => {
        vi.advanceTimersByTime(3_001);
      });
      const second = within(dialog).getByTestId("artifact-frame");
      expect(second).not.toBe(first);
      // The new frame is heard; the one it replaced is not.
      const sent = vi.spyOn((second as HTMLIFrameElement).contentWindow!, "postMessage");
      act(() => {
        window.dispatchEvent(
          new MessageEvent("message", {
            data: { type: "ready" },
            source: (second as HTMLIFrameElement).contentWindow,
          }),
        );
      });
      expect(sent).toHaveBeenCalledTimes(1);
      fireEvent.click(within(dialog).getByRole("button", { name: "Stop the page" }));
      fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
      for (let i = 0; i < 7; i++) {
        act(() => {
          vi.advanceTimersByTime(3_001);
        });
      }
      expect(within(dialog).queryByTestId("artifact-frame")).toBeNull();
      expect(within(dialog).getByTestId("artifact-notes")).toHaveTextContent(
        "did not start: the browser gave its frame no place to run in 21 seconds",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("says at once why a component cannot run, and stops it", async () => {
    shown('```jsx artifact title="List"\nimport _ from "lodash";\n```\n');
    const dialog = await openCard("Open List");
    fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    const own = (within(dialog).getByTestId("artifact-frame") as HTMLIFrameElement).contentWindow!;
    const sent = vi.spyOn(own, "postMessage");
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: { type: "ready" }, source: own }));
    });
    const { nonce } = sent.mock.calls[0]![0] as { nonce: string };
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "failed", nonce: "another", message: "spoof" },
          source: own,
        }),
      );
    });
    expect(within(dialog).getByTestId("artifact-frame")).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { type: "failed", nonce, message: 'it cannot load "lodash"' },
          source: own,
        }),
      );
    });
    expect(within(dialog).queryByTestId("artifact-frame")).toBeNull();
    expect(within(dialog).getByTestId("artifact-notes")).toHaveTextContent(
      'The component could not run: it cannot load "lodash".',
    );
  });

  it("reads only the six things a frame may say", () => {
    expect(readSaid({ type: "ready" })).toEqual({ type: "ready" });
    expect(readSaid({ type: "failed", nonce: "n", message: "no" })).toEqual({
      type: "failed",
      nonce: "n",
      message: "no",
    });
    expect(readSaid({ type: "failed", message: "no nonce" })).toBeNull();
    expect(readSaid({ type: "error", message: "x".repeat(900) })).toEqual({
      type: "error",
      message: "x".repeat(500),
    });
    expect(readSaid({ type: "started" })).toBeNull();
    expect(readSaid({ type: "fetch", url: "/api/me" })).toBeNull();
    expect(readSaid("ready")).toBeNull();
    expect(readSaid(null)).toBeNull();
  });

  it("shows Markdown as an answer reads, and an SVG as a picture", async () => {
    const { unmount } = shown('```md artifact title="Notes"\n# Heading\n\nText\n```\n');
    let dialog = await openCard("Open Notes");
    expect(within(dialog).getByRole("heading", { name: "Heading" })).toBeInTheDocument();
    unmount();
    shown(
      '```svg artifact title="Dot"\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><circle r="2"/></svg>\n```\n',
    );
    dialog = await openCard("Open Dot");
    expect(within(dialog).getByRole("img", { name: "Dot" }).getAttribute("src")).toMatch(
      /^data:image\/svg\+xml/,
    );
  });
});

describe("its revisions", () => {
  it("keeps the block as revision 1 and an edit as revision 2, at the first save", async () => {
    const onKept = vi.fn();
    vi.mocked(post).mockResolvedValueOnce({
      id: "a1",
      kind: "html",
      title: "Prices",
      digest: digest(PAGE),
      revisions: 2,
      createdAt: 1,
      updatedAt: 2,
      messageId: "m1",
      revisionList: [
        { number: 1, author: "answer", size: 10, createdAt: 1 },
        { number: 2, author: "person", size: 12, createdAt: 2 },
      ],
    });
    shown(ASKED, scope({ onKept }));
    const dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    const editor = within(dialog).getByRole("textbox", { name: "Source of Prices" });
    fireEvent.change(editor, { target: { value: "<h1>Mine</h1>" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save as revision 2" }));
    await waitFor(() => expect(onKept).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/chats/c1/messages/m1/artifacts", {
      kind: "html",
      title: "Prices",
      original: PAGE,
      source: "<h1>Mine</h1>",
      base: 1,
    });
    expect(within(dialog).getByRole("combobox", { name: "Revision" })).toHaveDisplayValue(
      /^Revision 2 of 2, edited/,
    );
    expect(editor).toHaveValue("<h1>Mine</h1>");
  });

  it("opens a kept one at its last revision, and adds the next to it", async () => {
    const kept: ArtifactSummary = {
      id: "a1",
      kind: "html",
      title: "Prices",
      digest: digest(PAGE),
      revisions: 2,
      createdAt: 1,
      updatedAt: 2,
    };
    vi.mocked(api).mockImplementation(async (path: string) =>
      path.endsWith("/revisions/2")
        ? { number: 2, author: "person", size: 4, createdAt: 2, source: "<p>two</p>" }
        : {
            ...kept,
            messageId: "m1",
            revisionList: [
              { number: 1, author: "answer", size: 10, createdAt: 1 },
              { number: 2, author: "person", size: 4, createdAt: 2 },
            ],
          },
    );
    vi.mocked(post).mockResolvedValueOnce({
      ...kept,
      revisions: 3,
      messageId: "m1",
      revisionList: [1, 2, 3].map((number) => ({
        number,
        author: number === 1 ? "answer" : "person",
        size: 1,
        createdAt: number,
      })),
    });
    shown(ASKED, scope({ artifacts: [kept] }));
    const dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    const editor = within(dialog).getByRole("textbox", { name: "Source of Prices" });
    await waitFor(() => expect(editor).toHaveValue("<p>two</p>"));
    fireEvent.change(editor, { target: { value: "<p>three</p>" } });
    // Another revision waits for this one to be saved or discarded.
    expect(within(dialog).getByRole("combobox", { name: "Revision" })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save as revision 3" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/chats/c1/artifacts/a1/revisions", {
        source: "<p>three</p>",
        base: 2,
      }),
    );
  });

  it("keeps nothing from the owner's read-only view, nor while the answer is written", async () => {
    const { unmount } = shown(ASKED, scope({ readOnly: true }));
    let dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    expect(within(dialog).getByRole("textbox")).toHaveAttribute("readonly");
    expect(within(dialog).queryByRole("button", { name: /Save/ })).toBeNull();
    unmount();
    shown(ASKED, scope({ writing: true }));
    dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "x" } });
    expect(within(dialog).getByRole("button", { name: "Save as revision 2" })).toBeDisabled();
    expect(dialog).toHaveTextContent("still being written");
  });

  it("keeps an edit not yet saved when the dialog is closed and opened again", async () => {
    shown(ASKED);
    let dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "half done" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByTestId("artifact-dialog")).toBeNull());
    dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    expect(within(dialog).getByRole("textbox")).toHaveValue("half done");
    fireEvent.click(within(dialog).getByRole("button", { name: "Discard changes" }));
    expect(within(dialog).getByRole("textbox")).toHaveValue(PAGE);
  });

  it("keeps an edit a newer revision refused, says why, and saves it after that one next", async () => {
    const onKept = vi.fn();
    vi.mocked(post)
      .mockRejectedValueOnce(
        new ApiError("Revision 2 was saved after revision 1, where this edit began.", 409),
      )
      .mockResolvedValueOnce({
        id: "a1",
        kind: "html",
        title: "Prices",
        digest: digest(PAGE),
        revisions: 3,
        createdAt: 1,
        updatedAt: 3,
        messageId: "m1",
        revisionList: [1, 2, 3].map((number) => ({
          number,
          author: number === 1 ? "answer" : "person",
          size: 1,
          createdAt: number,
        })),
      });
    shown(ASKED, scope({ onKept }));
    const dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "mine" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save as revision 2" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "It was not saved: Revision 2 was saved after revision 1",
    );
    expect(onKept).toHaveBeenCalled();
    expect(within(dialog).getByRole("textbox")).toHaveValue("mine");
    fireEvent.click(within(dialog).getByRole("button", { name: /^Save as revision/ }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    // No base it knows of now: after the last the page has (its own revision 1 here).
    expect(vi.mocked(post).mock.calls[1]![1]).toMatchObject({ source: "mine", base: 1 });
  });

  it("keeps a running preview while its source is read", async () => {
    shown(ASKED);
    const dialog = await openCard();
    fireEvent.click(within(dialog).getByRole("button", { name: "Run" }));
    const frame = within(dialog).getByTestId("artifact-frame");
    fireEvent.click(within(dialog).getByRole("tab", { name: "Source" }));
    fireEvent.keyDown(within(dialog).getByRole("tab", { name: "Source" }), { key: "Home" });
    expect(within(dialog).getByRole("tab", { name: "Preview" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(dialog).getByTestId("artifact-frame")).toBe(frame);
  });
});

describe("an export", () => {
  it("names each kept artifact and its revisions, and no source", () => {
    const detail = {
      chat: { id: "c", title: "Made" },
      messages: [
        {
          id: "m",
          role: "assistant",
          content: "Done.",
          reasoning: "",
          sources: [],
          status: "done",
          createdAt: 1,
          artifacts: [
            { id: "a1", kind: "html", title: "Prices", digest: "d", revisions: 3 },
            { id: "a2", kind: "svg", title: "Logo", digest: "e", revisions: 1 },
          ],
        },
      ],
    } as unknown as ChatDetail;
    const text = chatMarkdown(detail);
    expect(text).toContain("### Artifacts");
    expect(text).toContain("- Prices (HTML): 3 revisions, the first the answer's block");
    expect(text).toContain("- Logo (SVG): 1 revision, the first");
    expect(text).not.toContain("a1");
  });
});
