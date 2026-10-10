import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { post } from "../lib/api";
import type { Message } from "../lib/types";
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
