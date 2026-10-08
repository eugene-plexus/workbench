import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError, del } from "../lib/api";
import type { Chat, Me, Message, Models } from "../lib/types";
import { Composer, type Pending } from "./Composer";
import { MessageView } from "./MessageView";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
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
const models: Models = {
  models: [
    {
      id: "local",
      ready: true,
      onDemand: false,
      imageInput: true,
      fileInput: true,
      audioInput: true,
      webSearch: false,
      contextLength: 4096,
    },
  ],
  webSearch: { available: false, reason: "No search account." },
};
const sent: Message = {
  id: "u",
  seq: 1,
  role: "user",
  content: "Hello",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "local",
  finish: null,
  createdAt: 1,
  finishedAt: null,
};
const dot: Pending = { id: "f1", name: "dot.png", mediaType: "image/png" };

function composer(initialAttachments?: Pending[]) {
  return (
    <Composer
      chat={chat}
      initialAttachments={initialAttachments}
      me={me}
      models={models}
      modelsError={null}
      running={false}
      onSent={vi.fn()}
      onChat={vi.fn()}
    />
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.clearAllMocks();
});

it("names the edit box apart from the composer (workbench#2)", () => {
  render(
    <>
      <MessageView
        chatId="one"
        message={sent}
        progress={null}
        busy={false}
        readOnly={false}
        onChanged={vi.fn()}
      />
      {composer()}
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const mine = screen.getAllByRole("textbox", { name: "Your message" });
  const edit = screen.getAllByRole("textbox", { name: "Edit your message" });
  expect(mine).toHaveLength(1);
  expect(edit).toHaveLength(1);
  expect(mine[0]).toHaveAttribute("data-testid", "composer");
  expect(edit[0]).toHaveValue("Hello");
});

describe("Remove on an attachment not yet sent (workbench#3)", () => {
  it("deletes the upload, then drops the chip", async () => {
    vi.mocked(del).mockResolvedValue(undefined);
    render(composer([dot]));
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove dot.png" })).not.toBeInTheDocument(),
    );
    expect(del).toHaveBeenCalledWith("/api/chats/one/files/f1");
  });

  it("keeps the chip and says why when the delete is refused", async () => {
    vi.mocked(del).mockRejectedValue(
      new ApiError("This file was sent with a message, so it stays with the chat.", 409),
    );
    render(composer([dot]));
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "dot.png was not removed: This file was sent with a message, so it stays with the chat.",
    );
    expect(screen.getByRole("button", { name: "Remove dot.png" })).toBeEnabled();
  });

  it("holds Send while the delete is under way", async () => {
    vi.mocked(del).mockReturnValue(new Promise(() => undefined));
    render(composer([dot]));
    expect(screen.getByTestId("send")).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    await waitFor(() => expect(screen.getByTestId("send")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Remove dot.png" })).toBeDisabled();
  });
});
