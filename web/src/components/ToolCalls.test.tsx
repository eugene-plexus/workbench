import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { post } from "../lib/api";
import type { Message, ToolCall } from "../lib/types";
import { ToolCalls } from "./ToolCalls";

vi.mock("../lib/api", () => ({ post: vi.fn() }));

const message: Message = {
  id: "answer",
  status: "running",
  seq: 2,
  role: "assistant",
  content: "",
  reasoning: "",
  attachments: [],
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "model",
  finish: null,
  createdAt: 0,
  finishedAt: null,
  toolRounds: [
    {
      calls: [
        {
          id: "call",
          serverId: "server",
          serverName: "Shared files",
          tool: "rename",
          arguments: { from: "a", to: "b" },
          status: "pending",
          result: null,
        },
      ],
    },
  ],
};

afterEach(() => vi.clearAllMocks());

it("shows exact arguments and submits one approval for the displayed answer", async () => {
  vi.mocked(post).mockResolvedValue(undefined);
  const changed = vi.fn();
  render(<ToolCalls chatId="chat" message={message} readOnly={false} onChanged={changed} />);
  expect(screen.getByText(/"from": "a"/)).toBeInTheDocument();
  const approve = screen.getByRole("button", { name: "Approve rename call" });
  fireEvent.click(approve);
  expect(approve).toBeDisabled();
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(post).toHaveBeenCalledWith("/api/chats/chat/messages/answer/tools/decision", {
    callId: "call",
    approve: true,
  });
});

it("declines without approving", async () => {
  vi.mocked(post).mockResolvedValue(undefined);
  render(
    <ToolCalls chatId="chat" message={message} readOnly={false} onChanged={() => undefined} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Decline rename call" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(expect.any(String), { callId: "call", approve: false }),
  );
});

it("cannot approve while reading another person's chat", () => {
  render(<ToolCalls chatId="chat" message={message} readOnly onChanged={() => undefined} />);
  expect(screen.queryByRole("button", { name: /Approve|Decline/ })).toBeNull();
  expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
});

it("shows failures and lets the person retry a decision", async () => {
  vi.mocked(post).mockRejectedValue(new Error("This call is no longer waiting for approval."));
  render(
    <ToolCalls chatId="chat" message={message} readOnly={false} onChanged={() => undefined} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve rename call" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer waiting");
  expect(screen.getByRole("button", { name: "Approve rename call" })).not.toBeDisabled();
});

it("renders tool output as text without fetching images or running HTML", () => {
  const hostile = {
    ...message,
    status: "done",
    toolRounds: [
      {
        calls: [
          {
            ...message.toolRounds![0]!.calls[0]!,
            status: "uncertain",
            result: '<img src="https://evil.test/beacon" onerror="alert(1)">',
          },
        ],
      },
    ],
  } as Message;
  const { container } = render(
    <ToolCalls chatId="chat" message={hostile} readOnly={false} onChanged={() => undefined} />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText(/<img src=/)).toBeInTheDocument();
  expect(screen.getByText(/Result unknown/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Approve|Decline/ })).toBeNull();
});

it("runs a call the site's rules allow without offering to approve it (J70)", () => {
  const allowed: Message = {
    ...message,
    toolRounds: [
      { calls: [{ ...message.toolRounds![0]!.calls[0]!, tool: "read_text", ask: false }] },
    ],
  };
  render(
    <ToolCalls chatId="chat" message={allowed} readOnly={false} onChanged={() => undefined} />,
  );
  expect(screen.getByText(/Allowed by the rules/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve read_text call" })).toBeNull();
});

const held: Message = {
  ...message,
  toolRounds: [
    {
      calls: [
        {
          id: "cmd",
          serverId: "site:s-desk:files",
          serverName: "Ada's desktop · Files",
          tool: "run_command",
          arguments: { folder: "Work", command: "npm test" },
          status: "signing",
          result: null,
          jobSite: true,
          site: "s-desk",
          label: "desk",
          ask: false,
          signed: true,
          held: {
            id: "h1",
            kind: "call",
            words: ["Run this command on desk as HOST/ada, starting in “Work”:", "npm test"],
            approvePage: "http://127.0.0.1:8079/link/approve",
          },
        },
      ],
    },
  ],
};

it("shows what the machine holds for a signature, and where to sign it", () => {
  render(<ToolCalls chatId="chat" message={held} readOnly={false} onChanged={vi.fn()} />);
  expect(screen.getByText("Waiting for your signature")).toBeInTheDocument();
  expect(screen.getByText("desk asks for your signature")).toBeInTheDocument();
  expect(screen.getByLabelText("What desk holds")).toHaveTextContent("npm test");
  expect(screen.getByRole("link", { name: "Sign on desk's page" })).toHaveAttribute(
    "href",
    "http://127.0.0.1:8079/link/approve",
  );
  // No approval of Workbench's own: the machine checks the signature itself.
  expect(
    screen.queryByRole("button", { name: "Approve run_command call" }),
  ).not.toBeInTheDocument();
});

it("says no to a held call without signing it", async () => {
  vi.mocked(post).mockResolvedValue(undefined);
  render(<ToolCalls chatId="chat" message={held} readOnly={false} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Do not sign run_command call" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/chats/chat/messages/answer/tools/decision", {
      callId: "cmd",
      approve: false,
    }),
  );
});

it("offers a window for an hour of allowed tools", () => {
  const call = held.toolRounds![0]!.calls[0]!;
  const windowed: Message = {
    ...held,
    toolRounds: [
      {
        calls: [
          {
            ...call,
            tool: "read_text",
            held: { ...call.held!, kind: "window", minutes: 60, words: ["Let Workbench use…"] },
          },
        ],
      },
    ],
  };
  render(<ToolCalls chatId="chat" message={windowed} readOnly={false} onChanged={vi.fn()} />);
  expect(screen.getByText("Open a 60-minute window on desk")).toBeInTheDocument();
});

it("names the tool on every decision button, and none submits a form", () => {
  render(<ToolCalls chatId="chat" message={message} readOnly={false} onChanged={vi.fn()} />);
  for (const name of ["Approve rename call", "Decline rename call"]) {
    expect(screen.getByRole("button", { name })).toHaveAttribute("type", "button");
  }
});

it("names the tool on the signing buttons", () => {
  render(<ToolCalls chatId="chat" message={held} readOnly={false} onChanged={vi.fn()} />);
  expect(screen.getByRole("button", { name: "Do not sign run_command call" })).toHaveAttribute(
    "type",
    "button",
  );
});

it("copies a call's arguments and its result", () => {
  const writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const done: Message = {
    ...message,
    status: "done",
    toolRounds: [
      { calls: [{ ...message.toolRounds![0]!.calls[0]!, status: "done", result: "renamed" }] },
    ],
  };
  render(<ToolCalls chatId="chat" message={done} readOnly={false} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Copy rename call arguments" }));
  expect(writeText).toHaveBeenLastCalledWith(JSON.stringify({ from: "a", to: "b" }, null, 2));
  fireEvent.click(screen.getByRole("button", { name: "Copy rename call result" }));
  expect(writeText).toHaveBeenLastCalledWith("renamed");
});

describe("a call's state is a tone, a dot and words, not a sentence in one grey", () => {
  const call = message.toolRounds![0]!.calls[0]!;
  const withStatus = (status: ToolCall["status"], over: Partial<ToolCall> = {}): Message => ({
    ...message,
    toolRounds: [
      { calls: [{ ...call, status, result: status === "done" ? "ok" : null, ...over }] },
    ],
  });
  const show = (m: Message) =>
    render(<ToolCalls chatId="chat" message={m} readOnly={false} onChanged={() => undefined} />);
  // The copy buttons inside the call have statuses of their own, for a screen reader.
  const state = () => screen.getAllByRole("status").find((el) => el.tagName === "P")!;
  const card = () => screen.getByRole("article");

  it.each([
    // status, the words it says, the tone of the words, the edge of the card
    ["pending", "Waiting for approval", "text-warn", "border-warn-line"],
    ["signing", "Waiting for your signature", "text-warn", "border-warn-line"],
    ["uncertain", "Result unknown", "text-warn", "border-warn-line"],
    ["running", "Running", "text-info", "border-line"],
    ["done", "Finished", "text-ok", "border-line"],
    ["failed", "Server reported a failure", "text-error", "border-error-line"],
    ["declined", "Declined", "text-muted", "border-line"],
    ["cancelled", "Did not run", "text-muted", "border-line"],
  ] as const)("%s: %s", (status, words, tone, edge) => {
    show(withStatus(status));
    expect(state()).toHaveTextContent(words);
    expect(state()).toHaveClass(tone);
    expect(card()).toHaveClass(edge);
    // A dot beside the words, which is only a dot to a screen reader.
    expect(state().firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(state().firstElementChild).toHaveClass("rounded-full", "bg-current");
  });

  it("pulses the dot while a call runs, and only then", () => {
    const { unmount } = show(withStatus("running"));
    expect(state().firstElementChild).toHaveClass("animate-pulse");
    unmount();
    show(withStatus("done"));
    expect(state().firstElementChild).not.toHaveClass("animate-pulse");
  });

  it("does not call a call the site's rules allow a call waiting on you", () => {
    show(withStatus("pending", { ask: false }));
    expect(state()).toHaveTextContent("Allowed by the rules: runs without asking");
    expect(state()).toHaveClass("text-muted");
    expect(state()).not.toHaveClass("text-warn");
    expect(card()).toHaveClass("border-line");
    expect(card()).not.toHaveClass("border-warn-line");
  });

  it("keeps its words with its time, as one sentence", () => {
    const until = new Date(2026, 9, 11, 19, 5).toISOString();
    show(withStatus("signing", { windowUntil: until }));
    expect(state().textContent).toMatch(
      /^Waiting for your signature · your window there is open until 7:05/,
    );
  });

  it("names the tool with a glyph that is only a glyph, and keeps to the measure", () => {
    show(withStatus("done"));
    const title = screen.getByRole("heading", { name: "Shared files · rename" });
    expect(title.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(title).toHaveClass("wrap-anywhere");
    expect(screen.getByRole("region", { name: "Tool calls" })).toHaveClass(
      "max-w-measure",
      "text-sm",
    );
  });

  it("folds the arguments and the result under rows that are a real target on a phone", () => {
    show(withStatus("done"));
    for (const name of ["Arguments for this call", "Result"]) {
      expect(screen.getByText(name)).toHaveClass("disclosure", "text-muted");
    }
  });

  it("wraps arguments and a result with no break in them, in a bordered box", () => {
    show(withStatus("done", { arguments: { path: "x".repeat(300) }, result: "y".repeat(300) }));
    for (const text of [/x{300}/, /y{300}/]) {
      const box = screen.getByText(text);
      expect(box.tagName).toBe("PRE");
      expect(box).toHaveClass("break-all", "whitespace-pre-wrap", "overflow-auto", "border");
    }
  });
});

describe("what a call asks of you is drawn as buttons", () => {
  it("makes Approve the one primary button and Decline a plain one", () => {
    render(<ToolCalls chatId="chat" message={message} readOnly={false} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Approve rename call" })).toHaveClass(
      "btn",
      "btn-primary",
    );
    expect(screen.getByRole("button", { name: "Decline rename call" })).toHaveClass("btn");
    expect(screen.getByRole("button", { name: "Decline rename call" })).not.toHaveClass(
      "btn-primary",
    );
  });

  it("makes a link to the machine's page the primary action of a held call, and the rest plain", () => {
    render(<ToolCalls chatId="chat" message={held} readOnly={false} onChanged={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Sign on desk's page" })).toHaveClass(
      "btn",
      "btn-primary",
    );
    expect(screen.getByRole("button", { name: "Do not sign run_command call" })).toHaveClass("btn");
  });

  it("keeps the blue edge on what asks for your signature, and says what the machine holds as a group", () => {
    render(<ToolCalls chatId="chat" message={held} readOnly={false} onChanged={vi.fn()} />);
    const words = screen.getByRole("group", { name: "What desk holds" });
    expect(words).toHaveTextContent("npm test");
    expect(words.parentElement).toHaveClass("border-accent");
    expect(words.querySelector("p")).toHaveClass("font-mono", "wrap-anywhere");
  });

  it("says why a decision failed in an error notice, with the cause it was given", async () => {
    vi.mocked(post).mockRejectedValue(new Error("This call is no longer waiting for approval."));
    render(
      <ToolCalls chatId="chat" message={message} readOnly={false} onChanged={() => undefined} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve rename call" }));
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps the safety sentence once, as quiet help, under every call", () => {
    render(<ToolCalls chatId="chat" message={message} readOnly={false} onChanged={vi.fn()} />);
    const note = screen.getByText(
      "Stopping, editing or retrying a chat does not undo actions a tool has already taken.",
    );
    expect(note).toHaveClass("text-xs", "text-muted");
  });
});
