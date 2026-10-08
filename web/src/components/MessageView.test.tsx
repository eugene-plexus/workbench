import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { post } from "../lib/api";
import type { Message } from "../lib/types";
import { MessageView } from "./MessageView";

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
    show(answer({ searches: 1, sources: [{ url: "https://example.org/a", title: "A page" }] }));
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
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Next version" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/chats/c/messages/a3/choose");
  });

  it("names a message's versions, and stops at the first", () => {
    view(answer({ role: "user", content: "Hi", versions: { ...versions, index: 1 } }));
    expect(screen.getByRole("group", { name: "Message 1 of 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous version" })).toBeDisabled();
  });

  it("waits while an answer runs, and says so", () => {
    view(answer({ versions }), { busy: true });
    for (const name of ["Previous version", "Next version"]) {
      const button = screen.getByRole("button", { name });
      expect(button).toBeDisabled();
      expect(button).toHaveAttribute("title", "Wait for the answer, or stop it.");
    }
  });

  it("only looks for the owner, even while an answer runs", () => {
    const { onLook, onChanged } = view(answer({ versions }), { busy: true, readOnly: true });
    fireEvent.click(screen.getByRole("button", { name: "Previous version" }));
    expect(onLook).toHaveBeenCalledWith("a1");
    expect(post).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.queryByTestId("try-again")).toBeNull();
  });

  it("keeps a hidden message's arrows for the owner (V5)", () => {
    const { onLook } = view(answer({ versions, redacted: { site: "desk" } }), { readOnly: true });
    expect(screen.getByTestId("redacted-message")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next version" }));
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

  it("says an edit keeps the earlier version", () => {
    view(answer({ role: "user", content: "Hi" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByTestId("edit-note")).toHaveTextContent(
      "Your earlier version and what followed it are kept. Use the arrows to go back.",
    );
  });
});
