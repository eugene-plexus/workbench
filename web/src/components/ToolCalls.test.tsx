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
  const approve = screen.getByRole("button", { name: "Approve call" });
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
  fireEvent.click(screen.getByRole("button", { name: "Decline" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(expect.any(String), { callId: "call", approve: false }),
  );
});

it("cannot approve while reading another person's chat", () => {
  render(<ToolCalls chatId="chat" message={message} readOnly onChanged={() => undefined} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
});

it("shows failures and lets the person retry a decision", async () => {
  vi.mocked(post).mockRejectedValue(new Error("This call is no longer waiting for approval."));
  render(
    <ToolCalls chatId="chat" message={message} readOnly={false} onChanged={() => undefined} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve call" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no longer waiting");
  expect(screen.getByRole("button", { name: "Approve call" })).not.toBeDisabled();
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
  expect(screen.queryByRole("button")).toBeNull();
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
  expect(screen.getByRole("status")).toHaveTextContent("Allowed by the rules");
  expect(screen.queryByRole("button", { name: "Approve call" })).toBeNull();
});
