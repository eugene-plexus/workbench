import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { del, patch } from "../lib/api";
import type { Chat, ChatDetail, Me } from "../lib/types";
import { ChatView } from "./ChatView";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

const reload = vi.fn();
let detail: ChatDetail;
vi.mock("../lib/useChat", () => ({
  useChat: () => ({ detail, update: vi.fn(), progress: null, error: null, reload, look: vi.fn() }),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
};

function view() {
  render(
    <ChatView
      chatId="one"
      me={me}
      models={null}
      modelsError={null}
      onChanged={vi.fn()}
      onDeleted={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.mocked(del).mockReset();
  vi.mocked(patch).mockReset();
  reload.mockReset();
  detail = { chat, messages: [], ownerName: null };
});

it("names the chat in the delete question and on the button", () => {
  view();
  fireEvent.click(screen.getByRole("button", { name: "Delete this chat" }));
  expect(
    screen.getByText('Delete "Build a birdhouse" and the files attached to it?'),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete chat" })).toBeInTheDocument();
});

it("offers Dismiss, not Reload chat, after a failed delete", async () => {
  vi.mocked(del).mockRejectedValue(new Error("Disk is read only."));
  view();
  fireEvent.click(screen.getByRole("button", { name: "Delete this chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete chat" }));
  const failure = await screen.findByText(/Disk is read only\./);
  expect(failure).toHaveAttribute("role", "alert");
  expect(screen.queryByRole("button", { name: "Reload chat" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(screen.queryByText(/Disk is read only\./)).toBeNull());
  expect(reload).not.toHaveBeenCalled();
});
