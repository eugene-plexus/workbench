import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { post } from "../lib/api";
import type { Message, Progress } from "../lib/types";
import { MessageView, shortTime } from "./MessageView";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  post: vi.fn(),
}));

const answer = (over: Partial<Message>): Message => ({
  id: "a",
  seq: 2,
  role: "assistant",
  content: "An answer.",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

function show(message: Message) {
  return render(
    <MessageView
      chatId="c"
      message={message}
      progress={null}
      busy={false}
      readOnly={false}
      onChanged={() => undefined}
    />,
  );
}

it("gives each turn a level two heading and optional speaker labels", async () => {
  const { setDisplay } = await import("../lib/display");
  const shown = show(answer({ model: "qwen3-8b", content: "# Heading" }));
  expect(screen.getByRole("heading", { level: 2, name: "Answer from qwen3-8b" })).toHaveClass(
    "sr-only",
  );
  expect(screen.getByRole("heading", { level: 3, name: "Heading" })).toBeInTheDocument();
  expect(shown.container.querySelector(".speaker-label")).toBeNull();
  shown.unmount();
  setDisplay({ speakers: true });
  const user = show(answer({ role: "user", content: "Hello" }));
  expect(screen.getByRole("heading", { level: 2, name: "You" })).toHaveClass("sr-only");
  expect(user.container.querySelector(".speaker-label")).toHaveTextContent("You");
  user.unmount();
  setDisplay({ speakers: false });
});

it("keeps a repetition-stopped answer and explains the intentional repetition override", () => {
  show(
    answer({ content: "The partial answer.", status: "stopped", finish: "repetition_detected" }),
  );
  expect(screen.getByText("The partial answer.")).toBeInTheDocument();
  expect(screen.getByTestId("answer-status")).toHaveTextContent("appears to be repeating");
  expect(screen.getByTestId("answer-status")).toHaveTextContent("Chat settings");
  expect(screen.getByTestId("try-again")).toBeInTheDocument();
});

describe("a searched answer (W6)", () => {
  it("lists what it cites as links", () => {
    show(
      answer({
        searches: 1,
        sources: [{ kind: "web", url: "https://example.org/a", title: "A page" }],
      }),
    );
    expect(screen.getByRole("link", { name: "A page" })).toHaveAttribute(
      "href",
      "https://example.org/a",
    );
    expect(screen.getByTestId("sources")).toHaveTextContent("Sources (1 search)");
  });

  it("says it searched even when it cites nothing", () => {
    show(answer({ searches: 2 }));
    expect(screen.getByTestId("sources")).toHaveTextContent("Sources (2 searches)");
    expect(screen.getByTestId("sources")).toHaveTextContent("does not link to any page");
  });

  it("says nothing of a search that did not run", () => {
    show(answer({}));
    expect(screen.queryByTestId("sources")).toBeNull();
  });

  it("folds away what it wrote before searching and shows the answer after (workbench#1)", () => {
    const draft = "A first answer 🎉.";
    show(
      answer({
        content: `${draft}\n\nThe answer with results.`,
        reasoning: "No tools needed.Now with results.",
        answerFrom: draft.length,
        reasoningFrom: "No tools needed.".length,
        searches: 1,
      }),
    );
    const folded = screen.getByTestId("draft");
    expect(folded).not.toHaveAttribute("open");
    expect(folded).toHaveTextContent("Written before searching");
    expect(folded).toHaveTextContent("A first answer 🎉.");
    expect(screen.getByText("The answer with results.").closest("details")).toBeNull();
    expect(screen.getByTestId("searched-mark")).toHaveTextContent("Searched the web");
  });

  it("copies the answer, not the draft", () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    show(answer({ content: "Draft.\n\nThe answer.", answerFrom: 6, searches: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "Copy this answer" }));
    expect(writeText).toHaveBeenCalledWith("The answer.");
  });

  it("keeps the draft folded while the answer after the search has not started", () => {
    render(
      <MessageView
        chatId="c"
        message={answer({ content: "Draft.", answerFrom: 6, status: "running" })}
        progress={{ stage: "tool", tool: "web_search", phase: "finished" }}
        busy
        readOnly={false}
        onChanged={() => undefined}
      />,
    );
    expect(screen.getByTestId("draft")).toHaveTextContent("Draft.");
    expect(screen.getByTestId("progress")).toHaveTextContent("Reading what the search found");
  });

  it("shows a reply that stopped right after its search as its answer", () => {
    show(answer({ content: "Draft.", answerFrom: 6, status: "stopped" }));
    expect(screen.queryByTestId("draft")).toBeNull();
    expect(screen.getByText("Draft.")).toBeInTheDocument();
  });

  it("does not fold a reply that wrote nothing before its search", () => {
    show(answer({ content: "\n\nOnly an answer.", answerFrom: 0, searches: 1 }));
    expect(screen.queryByTestId("draft")).toBeNull();
    expect(screen.queryByTestId("searched-mark")).toBeNull();
  });

  it("shows the reasoning folded away, and a failure's own words", () => {
    show(answer({ reasoning: "Thinking…", status: "failed", error: "the key was refused" }));
    expect(screen.getByText("Reasoning").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("alert")).toHaveTextContent("the key was refused");
  });

  it("shows reasoning tokens and duration without opening the reasoning", () => {
    show(answer({ reasoning: "Thinking...", reasoningTokens: 128, reasoningSeconds: 3.25 }));
    const fold = screen.getByText("Reasoning").closest("details")!;
    expect(fold).not.toHaveAttribute("open");
    expect(fold.querySelector("summary")).toHaveTextContent("128 tokens3.3s");
  });

  it("labels missing token counts as estimates and does not invent a duration", () => {
    show(answer({ reasoning: "Thinking..." }));
    const summary = screen.getByText("Reasoning").closest("summary")!;
    expect(summary).toHaveTextContent("~3 tokens");
    expect(summary).toHaveTextContent("(estimated)");
    expect(screen.getByTitle(/Estimated from reasoning text/)).toBeInTheDocument();
    expect(screen.queryByTitle(/Observed time streaming reasoning/)).toBeNull();
  });

  it("shows reported reasoning tokens even when the reasoning text is withheld", () => {
    show(answer({ reasoningTokens: 64 }));
    expect(screen.getByText("Reasoning").closest("summary")).toHaveTextContent("64 tokens");
    expect(screen.getByText("The model did not share its reasoning text.")).toBeInTheDocument();
  });
});

describe("versions of a message (workbench-answer-versions.md)", () => {
  const versions = { index: 2, count: 3, ids: ["a1", "a", "a3"] };

  function view(message: Message, over: { busy?: boolean; readOnly?: boolean } = {}) {
    const onChanged = vi.fn();
    const onLook = vi.fn();
    render(
      <MessageView
        chatId="c"
        message={message}
        progress={null}
        busy={over.busy ?? false}
        readOnly={over.readOnly ?? false}
        onChanged={onChanged}
        onLook={over.readOnly ? onLook : undefined}
      />,
    );
    return { onChanged, onLook };
  }

  beforeEach(() => vi.mocked(post).mockReset().mockResolvedValue(undefined));

  it("counts, names itself, and the model follows", () => {
    view(answer({ versions, model: "qwen" }));
    const arrows = screen.getByRole("group", { name: "Answer 2 of 3" });
    expect(arrows).toHaveTextContent("2 of 3");
    expect(arrows.nextElementSibling).toHaveTextContent("qwen");
    expect(screen.queryByRole("group", { name: /of 1/ })).toBeNull();
  });

  it("chooses the next version and reloads", async () => {
    const { onChanged } = view(answer({ versions }));
    fireEvent.click(screen.getByRole("button", { name: "Next version of this answer" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/chats/c/messages/a3/choose");
  });

  it("names a message's versions, and stops at the first", () => {
    view(answer({ role: "user", content: "Hi", versions: { ...versions, index: 1 } }));
    expect(screen.getByRole("group", { name: "Message 1 of 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous version of this message" })).toBeDisabled();
  });

  it("waits while an answer runs, and says so", () => {
    view(answer({ versions }), { busy: true });
    for (const name of ["Previous version of this answer", "Next version of this answer"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "Wait for the answer, or stop it.");
    }
  });

  it("only looks for the owner, even while an answer runs", () => {
    const { onLook, onChanged } = view(answer({ versions }), { busy: true, readOnly: true });
    fireEvent.click(screen.getByRole("button", { name: "Previous version of this answer" }));
    expect(onLook).toHaveBeenCalledWith("a1");
    expect(post).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.queryByTestId("try-again")).toBeNull();
  });

  it("keeps a hidden message's arrows for the owner (V5)", () => {
    const { onLook } = view(answer({ versions, redacted: { site: "desk" } }), { readOnly: true });
    expect(screen.getByTestId("redacted-message")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next version of this answer" }));
    expect(onLook).toHaveBeenCalledWith("a3");
  });

  it("tries again on any answer, and on a running one (V2, V6)", async () => {
    const { onChanged } = view(answer({ id: "early", status: "running", content: "Part" }), {
      busy: true,
    });
    const again = screen.getByTestId("try-again");
    expect(again).toHaveAttribute("title", expect.stringContaining("keeps it as a version"));
    fireEvent.click(again);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/chats/c/messages/early/retry");
  });

  it("names what Try again and Copy act on, on an answer", () => {
    view(answer({}));
    expect(
      screen.getByRole("button", { name: "Try again: write a new version of this answer" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy this answer" })).toBeInTheDocument();
  });

  it("names what Copy and Edit act on, on your message", () => {
    view(answer({ role: "user", content: "Hi" }));
    expect(screen.getByRole("button", { name: "Copy your message" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit your message" })).toBeInTheDocument();
  });

  it("names the Mac key in the edit hint", () => {
    view(answer({ role: "user", content: "Hi" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit your message" }));
    expect(screen.getByTestId("edit-note")).toHaveTextContent("Cmd+Enter on a Mac");
  });

  it("says an edit keeps the earlier version", () => {
    view(answer({ role: "user", content: "Hi" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit your message" }));
    expect(screen.getByTestId("edit-note")).toHaveTextContent(
      "Your earlier version and what followed it are kept. Use the arrows to go back.",
    );
  });
});

describe("a search provider's Search Suggestions (GS4)", () => {
  const GOOGLE =
    "<style>.container{padding:8px 12px}</style>" +
    '<div class="container"><a class="chip" href="https://www.google.com/search?q=barns">barns &amp; silos</a></div>';

  it("shows each one in a frame that can run nothing, holding Google's HTML as it came", () => {
    show(answer({ searches: 2, searchSuggestions: [GOOGLE, GOOGLE + "<!-- two -->"] }));
    const frames = screen.getAllByTitle("Google Search Suggestions");
    expect(frames).toHaveLength(2);
    const [first, second] = frames as HTMLIFrameElement[];
    expect(first!.getAttribute("sandbox")).toBe("allow-popups allow-popups-to-escape-sandbox");
    expect(first!.srcdoc).toContain(GOOGLE);
    expect(second!.srcdoc).toContain(GOOGLE + "<!-- two -->");
    expect(first!.srcdoc).toContain('<base target="_blank">');
    expect(first!.srcdoc).toContain('<meta name="referrer" content="no-referrer">');
    expect(screen.getByTestId("sources")).toContainElement(first!);
  });

  it("shows nothing when the search came with none", () => {
    show(answer({ searches: 1 }));
    expect(screen.queryByTitle("Google Search Suggestions")).toBeNull();
    show(answer({ searches: 1, searchSuggestions: [] }));
    expect(screen.queryByTitle("Google Search Suggestions")).toBeNull();
  });
});

describe("the person's words", () => {
  const mine = (over: Partial<Message> = {}) =>
    answer({ role: "user", content: "Why are there two tides a day?", ...over });
  const versions = { index: 2, count: 3, ids: ["a1", "a", "a3"] };

  it("is a quiet steel-blue bubble with a hairline, right-aligned, at the reading size and measure", () => {
    show(mine());
    const bubble = screen.getByText("Why are there two tides a day?");
    expect(bubble).toHaveClass("bg-tint", "border", "border-tint-line");
    expect(bubble).toHaveClass("text-read", "sm:max-w-measure", "max-w-[85%]");
    expect(bubble.parentElement).toHaveAttribute("data-testid", "user-message");
    expect(bubble.parentElement).toHaveClass("msg", "items-end");
  });

  it("wraps a word with no break in it, and takes its direction from its own letters", () => {
    show(mine({ content: "x".repeat(300) }));
    const bubble = screen.getByText("x".repeat(300));
    expect(bubble).toHaveClass("wrap-anywhere", "whitespace-pre-wrap");
    expect(bubble).toHaveAttribute("dir", "auto");
  });

  it("keeps Copy and Edit where they come and go, and the versions and the time where they stay", () => {
    const { container } = show(mine({ versions }));
    const actions = container.querySelector(".msg-actions")!;
    expect(actions).toContainElement(screen.getByRole("button", { name: "Copy your message" }));
    expect(actions).toContainElement(screen.getByRole("button", { name: "Edit your message" }));
    expect(actions).not.toContainElement(screen.getByRole("group", { name: "Message 2 of 3" }));
    expect(actions).not.toContainElement(container.querySelector("time"));
    expect(screen.getByRole("button", { name: "Copy your message" })).toHaveClass("act");
    expect(screen.getByRole("button", { name: "Edit your message" })).toHaveClass("act");
  });

  it("ends its row with the time, so it sits under the bubble's edge whatever shows", () => {
    const { container } = show(mine({ versions }));
    const time = container.querySelector("time")!;
    const row = time.parentElement!;
    expect(row.lastElementChild).toBe(time);
    expect(row).toHaveClass("justify-end");
    // The versions come just before it, and the actions that come and go before them.
    expect(time.previousElementSibling).toBe(screen.getByRole("group", { name: "Message 2 of 3" }));
    expect(time.previousElementSibling!.previousElementSibling).toHaveClass("msg-actions");
  });

  it("is edited at the reading size, with a quiet Cancel and a primary Save", () => {
    show(mine());
    fireEvent.click(screen.getByRole("button", { name: "Edit your message" }));
    const box = screen.getByLabelText("Edit your message");
    expect(box).toHaveClass("control", "text-read");
    expect(box).toHaveAttribute("dir", "auto");
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveClass("btn", "btn-quiet");
    expect(screen.getByRole("button", { name: "Save and ask again" })).toHaveClass(
      "btn",
      "btn-primary",
    );
    // The form keeps to the same measure as the bubble it replaces.
    expect(box.closest("form")).toHaveClass("max-w-measure", "text-read");
  });

  it("says why a retry or a version was refused, in an error notice with the server's words", async () => {
    vi.mocked(post).mockReset().mockRejectedValue(new Error("This chat is busy."));
    show(mine({ versions }));
    fireEvent.click(screen.getByRole("button", { name: "Next version of this message" }));
    const refused = await screen.findByRole("alert");
    expect(refused).toHaveClass("notice", "notice--error");
    expect(refused).toHaveTextContent("This chat is busy.");
  });
});

describe("an answer's surroundings", () => {
  const versions = { index: 2, count: 3, ids: ["a1", "a", "a3"] };

  it("is an article of parts 12 px apart, that is a message", () => {
    show(answer({}));
    const article = screen.getByTestId("answer");
    expect(article.tagName).toBe("ARTICLE");
    expect(article).toHaveClass("msg", "gap-3");
  });

  it("keeps Copy and Try again where they come and go, and the time, versions and model where they stay", () => {
    const { container } = show(answer({ versions, model: "qwen" }));
    const actions = container.querySelector(".msg-actions")!;
    expect(actions).toContainElement(screen.getByRole("button", { name: "Copy this answer" }));
    expect(actions).toContainElement(screen.getByTestId("try-again"));
    expect(actions).not.toContainElement(container.querySelector("time"));
    expect(actions).not.toContainElement(screen.getByRole("group", { name: "Answer 2 of 3" }));
    expect(actions).not.toContainElement(screen.getByText("qwen"));
    for (const name of ["Copy this answer", "Try again: write a new version of this answer"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("act");
    }
  });

  it("draws the version arrows as quiet actions without the opacity that never applied", () => {
    show(answer({ versions }));
    for (const name of ["Previous version of this answer", "Next version of this answer"]) {
      const arrow = screen.getByRole("button", { name });
      expect(arrow).toHaveClass("act", "justify-center");
      expect(arrow.className).not.toContain("disabled:opacity");
    }
  });

  it("gives a model name that has no break in it somewhere to wrap", () => {
    show(answer({ model: "unsloth/Qwen3-30B-A3B-GGUF:UD-Q4_K_XL_with_a_very_long_name" }));
    expect(screen.getByText(/unsloth\/Qwen3/, { selector: "span" })).toHaveClass("wrap-anywhere");
  });

  it("says its time as 9:05 AM, not 09:05 AM", () => {
    const now = new Date(2026, 9, 9, 15, 0);
    expect(shortTime(new Date(2026, 9, 9, 9, 5), now)).toMatch(/^9:05/);
    expect(shortTime(new Date(2026, 9, 7, 9, 5), now)).toMatch(/Oct 7, 9:05/);
  });
});

describe("reasoning is plain text in a quiet box that scrolls", () => {
  const reasoning = "Let me *think* about **this**.";

  afterEach(() => vi.restoreAllMocks());

  it("is a folded row with a chevron that opens to a named box of text, asterisks and all", () => {
    show(answer({ reasoning }));
    const row = screen.getByText("Reasoning");
    expect(row.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // 28 px under a mouse and 44 px under a finger (the stylesheet's .disclosure).
    expect(row).toHaveClass("disclosure");
    expect(row.closest("details")).not.toHaveAttribute("open");
    const box = screen.getByRole("group", { name: "Reasoning text" });
    expect(box).toHaveClass("reasoning-body");
    expect(box).toHaveTextContent(reasoning);
    // Not Markdown: it is what the model thought, not an answer.
    expect(box.querySelector("em, strong, p")).toBeNull();
  });

  it("keeps to the answer's measure, wraps what has no break in it and reads in its own direction", () => {
    show(answer({ reasoning: "x".repeat(400) }));
    const details = screen.getByText("Reasoning").closest("details")!;
    expect(details).toHaveClass("max-w-measure", "text-sm");
    const thought = screen.getByText("x".repeat(400));
    expect(thought).toHaveClass("wrap-anywhere", "whitespace-pre-wrap");
    expect(thought).toHaveAttribute("dir", "auto");
  });

  it("is a keyboard stop only while it overflows", () => {
    const { unmount } = show(answer({ reasoning }));
    expect(screen.getByRole("group", { name: "Reasoning text" })).not.toHaveAttribute("tabindex");
    unmount();
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(320);
    show(answer({ reasoning }));
    expect(screen.getByRole("group", { name: "Reasoning text" })).toHaveAttribute("tabindex", "0");
  });

  describe("while the model is thinking", () => {
    const thinking = (text: string) => (
      <MessageView
        chatId="c"
        message={answer({ status: "running", content: "", reasoning: text })}
        progress={null}
        busy
        readOnly={false}
        onChanged={() => undefined}
      />
    );

    it("follows the bottom as text arrives, and lets go once the reader scrolls up", () => {
      vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(320);
      let top = 0;
      const set = vi.spyOn(Element.prototype, "scrollTop", "set").mockImplementation((v) => {
        top = v;
      });
      vi.spyOn(Element.prototype, "scrollTop", "get").mockImplementation(() => top);
      const { rerender } = render(thinking("First."));
      expect(set).toHaveBeenLastCalledWith(900);
      // The reader scrolls up to read: nothing pulls them down again.
      top = 100;
      fireEvent.scroll(screen.getByRole("group", { name: "Reasoning text" }));
      set.mockClear();
      rerender(thinking("First. Then more."));
      expect(set).not.toHaveBeenCalled();
      // And back at the bottom, it follows again.
      top = 900 - 320 - 5;
      fireEvent.scroll(screen.getByRole("group", { name: "Reasoning text" }));
      rerender(thinking("First. Then more. And more."));
      expect(set).toHaveBeenCalledWith(900);
    });

    it("does not move a finished answer's reasoning", () => {
      vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
      vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(320);
      const set = vi.spyOn(Element.prototype, "scrollTop", "set");
      show(answer({ reasoning }));
      expect(set).not.toHaveBeenCalled();
    });
  });

  it("gives what was written before a search the same box, with its hint, its text quiet", () => {
    show(answer({ content: "Draft.\n\nThe answer.", answerFrom: 6, searches: 1 }));
    const draft = screen.getByTestId("draft");
    expect(draft).toHaveClass("max-w-measure");
    const box = screen.getByRole("group", { name: "Written before searching" });
    expect(box).toHaveClass("reasoning-body");
    expect(box).toHaveTextContent("The model wrote this before it read what the search found.");
    // Markdown, at the chrome's size and not the answer's.
    expect(box.querySelector(".answer.answer-quiet")).toHaveTextContent("Draft.");
    expect(screen.getByText("The answer.").closest(".answer")).not.toHaveClass("answer-quiet");
  });
});

describe("the working line", () => {
  const at = () => Date.now() / 1000;
  const run = (message: Message, progress: Progress | null = null) =>
    render(
      <MessageView
        chatId="c"
        message={message}
        progress={progress}
        busy
        readOnly={false}
        onChanged={() => undefined}
      />,
    );

  it("says the phase to a screen reader and shows what ticks beside it, unspoken", () => {
    run(answer({ status: "running", content: "", reasoning: "Hmm", createdAt: at() - 72 }));
    const line = screen.getByTestId("progress");
    expect(line).toHaveAttribute("aria-live", "polite");
    const [said, ticking] = Array.from(line.children);
    expect(said).toHaveTextContent(/^Thinking$/);
    expect(said).toHaveClass("text-fg");
    expect(ticking).toHaveAttribute("aria-hidden", "true");
    expect(ticking?.textContent).toMatch(/^ · 1 min/);
    // The words as they were: one line, to the tests and to the eye.
    expect(line.textContent).toMatch(/^Thinking · 1 min/);
  });

  it("is only its sentence when nothing on it ticks", () => {
    run(answer({ status: "running", content: "", createdAt: at() }));
    const line = screen.getByTestId("progress");
    expect(line).toHaveTextContent("Waiting for the model…");
    expect(line.children).toHaveLength(1);
    expect(line.querySelector("[aria-hidden]")).toBeNull();
  });

  it("moves below the words once they start, with the count and the speed, and is not announced", () => {
    const { container } = run(
      answer({ status: "running", content: "Tides are the ocean", reasoning: "Hmm" }),
      { stage: "generating", generated_tokens: 1234, tokens_per_second: 83.14 },
    );
    expect(screen.queryByTestId("progress")).toBeNull();
    const writing = screen.getByTestId("writing");
    expect(writing).toHaveTextContent("Writing the answer · 1,234 tokens · 83.1 tok/s");
    expect(writing).not.toHaveAttribute("aria-live");
    expect(writing.querySelector(".working-dots")).toHaveAttribute("aria-hidden", "true");
    // Below the text, not above it.
    const text = container.querySelector(".answer")!;
    expect(text.compareDocumentPosition(writing) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(text).toHaveTextContent("Tides are the ocean");
  });

  it("is gone once the answer ends", () => {
    run(answer({ status: "done", content: "Tides." }));
    expect(screen.queryByTestId("writing")).toBeNull();
    expect(screen.queryByTestId("progress")).toBeNull();
  });
});

describe("what is said under an answer that is not a finished one", () => {
  it.each([
    ["failed", { status: "failed", error: "The key was refused" }, "notice--error", "alert"],
    ["stopped at a length limit", { status: "done", finish: "length" }, "notice--warn", null],
    [
      "stopped for repeating",
      { status: "stopped", finish: "repetition_detected" },
      "notice--warn",
      null,
    ],
    ["interrupted by a restart", { status: "interrupted" }, "notice--warn", null],
  ] as const)("%s is a box with a glyph and its words", (_name, over, tone, role) => {
    show(answer(over as Partial<Message>));
    const note = screen.getByTestId("answer-status");
    expect(note).toHaveClass("notice", tone, "max-w-measure");
    if (role) expect(note).toHaveAttribute("role", role);
    else expect(note).not.toHaveAttribute("role");
    expect(note.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(note.textContent).not.toBe("");
  });

  it("gives each cause its own glyph", () => {
    const glyph = (over: Partial<Message>) => {
      const { unmount } = show(answer(over));
      const name = screen.getByTestId("answer-status").querySelector("svg")!.getAttribute("class");
      unmount();
      return name;
    };
    expect(glyph({ status: "failed", error: "x" })).toContain("circle-alert");
    expect(glyph({ status: "done", finish: "length" })).toContain("scissors");
    expect(glyph({ status: "stopped", finish: "repetition_detected" })).toContain("triangle-alert");
    expect(glyph({ status: "interrupted" })).toContain("triangle-alert");
  });

  it("leaves a stop the person asked for as a plain line, without a box or a glyph", () => {
    show(answer({ status: "stopped", content: "Part" }));
    const note = screen.getByTestId("answer-status");
    expect(note).toHaveTextContent("Stopped.");
    expect(note).not.toHaveClass("notice");
    expect(note.querySelector("svg")).toBeNull();
  });

  it("says nothing under a finished answer", () => {
    show(answer({ status: "done" }));
    expect(screen.queryByTestId("answer-status")).toBeNull();
  });
});

describe("sources", () => {
  it("are headed by a glyph and the count, and the count keeps its space", () => {
    show(
      answer({
        searches: 2,
        sources: [{ kind: "web", url: "https://example.org/a", title: "A page" }],
      }),
    );
    const sources = screen.getByTestId("sources");
    const heading = sources.querySelector("h3")!;
    expect(heading).toHaveClass("eyebrow");
    expect(heading.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(heading.textContent).toBe("Sources (2 searches)");
    expect(sources).toHaveClass("max-w-measure", "border-t");
  });

  it("link as links, and a title that has no break in it wraps", () => {
    show(
      answer({
        searches: 1,
        sources: [{ kind: "web", url: "https://example.org/a", title: "x".repeat(200) }],
      }),
    );
    expect(screen.getByRole("link", { name: "x".repeat(200) })).toHaveClass("link");
    expect(screen.getByRole("list")).toHaveClass("wrap-anywhere");
  });
});

describe("a message hidden from the owner", () => {
  it("keeps its note and its arrows, and wraps the name of a machine that has no break in it", () => {
    show(answer({ redacted: { site: "d".repeat(100) } }));
    const note = screen.getByTestId("redacted-message");
    expect(note).toHaveClass("max-w-measure", "wrap-anywhere");
  });
});
