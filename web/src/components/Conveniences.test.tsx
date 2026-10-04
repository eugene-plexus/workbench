import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, patch, post } from "../lib/api";
import { chatGroups, chatMarkdown, readDraft, saveDraft } from "../lib/conveniences";
import { forget } from "../lib/session";
import type { Chat, Me, Message, Models } from "../lib/types";
import { Composer } from "./Composer";
import { CopyButton } from "./CopyButton";
import { Sidebar } from "./Sidebar";

vi.mock("../lib/api", () => ({ api: vi.fn(), patch: vi.fn(), post: vi.fn() }));
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
const message: Message = {
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
function composer(person = me, room = chat) {
  return render(
    <Composer
      chat={room}
      me={person}
      models={models}
      modelsError={null}
      running={false}
      onSent={vi.fn()}
      onChat={vi.fn()}
    />,
  );
}
beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.clearAllMocks();
});

it("keeps drafts across remounts and refresh reads, separates people and chats, and clears on sign-out", () => {
  const first = composer();
  fireEvent.change(screen.getByTestId("composer"), { target: { value: "My unfinished question" } });
  first.unmount();
  const second = composer();
  expect(screen.getByTestId("composer")).toHaveValue("My unfinished question");
  second.unmount();
  expect(readDraft("ada", "two")).toBe("");
  composer({ ...me, sub: "grace" });
  expect(screen.getByTestId("composer")).toHaveValue("");
  forget();
  expect(readDraft("ada", "one")).toBe("");
});

it("keeps a failed send's draft and clears it only after a successful send", async () => {
  saveDraft(me.sub, chat.id, "Please keep this");
  vi.mocked(post)
    .mockRejectedValueOnce(new Error("Connection lost"))
    .mockResolvedValueOnce({ user: message, answer: { ...message, role: "assistant" } });
  composer();
  fireEvent.click(screen.getByTestId("send"));
  await screen.findByText("Connection lost");
  expect(readDraft(me.sub, chat.id)).toBe("Please keep this");
  fireEvent.click(screen.getByTestId("send"));
  await waitFor(() => expect(screen.getByTestId("composer")).toHaveValue(""));
  expect(readDraft(me.sub, chat.id)).toBe("");
});

it("uploads dropped files before allowing Enter or Send and reports upload failures", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(api).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  composer();
  fireEvent.change(screen.getByTestId("composer"), { target: { value: "Read this" } });
  fireEvent.drop(screen.getByTestId("composer-area"), {
    dataTransfer: { files: [new File(["pdf"], "notes.pdf", { type: "application/pdf" })] },
  });
  expect(screen.getByRole("status")).toHaveTextContent("Uploading 1 of 1: notes.pdf");
  expect(screen.getByTestId("send")).toBeDisabled();
  fireEvent.keyDown(screen.getByTestId("composer"), { key: "Enter" });
  expect(post).not.toHaveBeenCalled();
  await waitFor(() => expect(api).toHaveBeenCalled());
  finish({ id: "f", name: "notes.pdf", mediaType: "application/pdf" });
  await screen.findByRole("button", { name: "Remove notes.pdf" });
  expect(screen.getByTestId("send")).toBeEnabled();
  vi.mocked(api).mockRejectedValueOnce(new Error("Too large"));
  fireEvent.paste(screen.getByTestId("composer"), {
    clipboardData: { files: [new File(["png"], "shot.png", { type: "image/png" })] },
  });
  await screen.findByText("shot.png: Too large");
  expect(screen.getByRole("button", { name: "Remove notes.pdf" })).toBeInTheDocument();
});

it("pastes images through the same attachment endpoint while ordinary text keeps native paste", async () => {
  vi.mocked(api).mockResolvedValue({ id: "p", name: "paste.png", mediaType: "image/png" });
  composer();
  fireEvent.paste(screen.getByTestId("composer"), {
    clipboardData: { files: [new File(["png"], "paste.png", { type: "image/png" })] },
  });
  await screen.findByRole("button", { name: "Remove paste.png" });
  expect(api).toHaveBeenCalledWith(
    "/api/chats/one/files",
    expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
  );
  expect(fireEvent.paste(screen.getByTestId("composer"), { clipboardData: { files: [] } })).toBe(
    true,
  );
  expect(api).toHaveBeenCalledTimes(1);
});

it("searches chat names without changing order and offers a clear search action", () => {
  const other = { ...chat, id: "two", title: "Weekend recipes", updatedAt: 2 };
  render(
    <Sidebar
      me={me}
      chats={[chat, other]}
      current={chat.id}
      onOpen={vi.fn()}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onSignOut={vi.fn()}
      creating={false}
      error={null}
      onRetry={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "BIRDHOUSE" } });
  expect(screen.getByRole("button", { name: "Build a birdhouse" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.queryByRole("button", { name: "Weekend recipes" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "missing" } });
  expect(screen.getByRole("status")).toHaveTextContent("No chats match");
  fireEvent.click(screen.getByRole("button", { name: "Clear chat search" }));
  expect(screen.getByRole("button", { name: "Weekend recipes" })).toBeInTheDocument();
});

it("groups by local calendar days and sorts newest first without mutating the list", () => {
  const now = new Date(2026, 10, 2, 12);
  const at = (day: number) => +new Date(2026, 10, day, 10) / 1000;
  const rows = [
    { ...chat, id: "old", updatedAt: at(-9) },
    { ...chat, id: "yesterday", updatedAt: at(1) },
    { ...chat, id: "today", updatedAt: at(2) },
    { ...chat, id: "week", updatedAt: at(-2) },
  ];
  expect(chatGroups(rows, "", now).map((g) => [g.label, g.chats[0]!.id])).toEqual([
    ["Today", "today"],
    ["Yesterday", "yesterday"],
    ["Previous 7 days", "week"],
    ["Older", "old"],
  ]);
  expect(rows[0]!.id).toBe("old");
});

it("exports a snapshot with source, file names and unfinished status without fetching files", () => {
  const text = chatMarkdown({
    chat,
    ownerName: null,
    messages: [
      { ...message, files: [{ id: "f", name: "notes.pdf", mediaType: "application/pdf" }] },
      {
        ...message,
        role: "assistant",
        content: "Partial answer",
        reasoning: "Thinking",
        status: "running",
        sources: [{ title: "Reference", url: "https://example.org" }],
      },
    ],
  });
  expect(text).toContain("# Build a birdhouse");
  expect(text).toContain("Attachments: notes.pdf");
  expect(text).toContain("Thinking");
  expect(text).toContain("Partial answer");
  expect(text).toContain("Reference: https://example.org");
  expect(text).toContain("Answer still in progress");
  expect(api).not.toHaveBeenCalled();
});

it("acknowledges a successful copy and explains a refused clipboard", async () => {
  const writeText = vi
    .fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new Error("denied"));
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  render(<CopyButton text="The answer" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(writeText).toHaveBeenCalledWith("The answer");
  fireEvent.click(screen.getByRole("button", { name: "Copied" }));
  await screen.findByText("Copy was blocked. Select the text and copy it manually.");
});

it("does not send to a model that disappeared", () => {
  composer(me, { ...chat, model: "gone" });
  fireEvent.change(screen.getByTestId("composer"), { target: { value: "Hello" } });
  fireEvent.keyDown(screen.getByTestId("composer"), { key: "Enter" });
  expect(post).not.toHaveBeenCalled();
  expect(patch).not.toHaveBeenCalled();
  expect(screen.getByTestId("send")).toBeDisabled();
});
