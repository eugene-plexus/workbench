import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, fileUrl, post } from "../lib/api";
import type { Chat, ChatDetail, Me, Message } from "../lib/types";
import { ChatView } from "./ChatView";
import { MessageView, shortTime, siteOf } from "./MessageView";
import { Sidebar } from "./Sidebar";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

let detail: ChatDetail;
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update: vi.fn(),
    progress: null,
    error: null,
    reload: vi.fn(),
    look: vi.fn(),
  }),
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
const message = (over: Partial<Message>): Message => ({
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
  model: "m",
  finish: null,
  createdAt: Date.now() / 1000,
  finishedAt: null,
  ...over,
});

function sidebar(chats: Chat[], onOpen = vi.fn(), who: Me = me) {
  render(
    <Sidebar
      me={who}
      chats={chats}
      current={null}
      onOpen={onOpen}
      onOpenMedia={vi.fn()}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onSignOut={vi.fn()}
      creating={false}
      error={null}
      onRetry={vi.fn()}
    />,
  );
  return onOpen;
}

function show(m: Message) {
  return render(
    <MessageView
      chatId="c"
      message={m}
      progress={null}
      busy={false}
      readOnly={false}
      onChanged={() => undefined}
    />,
  );
}

beforeEach(() => {
  // jsdom has no ResizeObserver; ChatView uses it only to follow the bottom.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(fileUrl).mockReset();
  detail = { chat, messages: [], ownerName: null };
});

it("opens the first matching chat when Enter is pressed in chat search", () => {
  const newer = { ...chat, id: "two", title: "Birdhouse roof", updatedAt: 5 };
  const onOpen = sidebar([chat, newer, { ...chat, id: "three", title: "Recipes" }]);
  const search = screen.getByRole("searchbox");
  fireEvent.keyDown(search, { key: "Enter" });
  expect(onOpen).not.toHaveBeenCalled(); // nothing typed: Enter does nothing
  fireEvent.change(search, { target: { value: "birdhouse" } });
  fireEvent.keyDown(search, { key: "Enter" });
  expect(onOpen).toHaveBeenCalledWith("two");
});

it("names the running dot for a screen reader", () => {
  sidebar([{ ...chat, running: true }]);
  expect(screen.getByRole("img", { name: "An answer is being written" })).toBeInTheDocument();
});

it("says why a person's chats did not load, instead of failing silently", async () => {
  vi.mocked(api)
    .mockResolvedValueOnce({ people: [{ sub: "bo", name: "Bo", chats: 2, media: 0 }] })
    .mockRejectedValueOnce(new Error("The server is busy"));
  sidebar([chat], vi.fn(), { ...me, owner: true, ownerReadsChats: true });
  const details = screen.getByText(/People's chats/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.click(await screen.findByRole("button", { name: "Bo (2)" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Bo's chats did not load: The server is busy",
  );
});

it("names the open chat in the tab's title, and gives it back on leaving", () => {
  const view = render(
    <ChatView
      chatId="one"
      me={me}
      models={null}
      modelsError={null}
      onChanged={vi.fn()}
      onDeleted={vi.fn()}
    />,
  );
  expect(document.title).toBe("Build a birdhouse · Workbench");
  view.unmount();
  expect(document.title).toBe("Workbench");
});

it("starts the delete question on Keep it, and Escape backs out", () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Delete this chat" }));
  expect(screen.getByTestId("keep-chat")).toHaveFocus();
  fireEvent.keyDown(screen.getByTestId("keep-chat"), { key: "Escape" });
  expect(screen.queryByTestId("confirm-delete")).toBeNull();
});

it("saves an edited message with Ctrl+Enter", async () => {
  vi.mocked(post).mockResolvedValue({});
  show(message({}));
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  const box = screen.getByLabelText("Edit your message");
  fireEvent.change(box, { target: { value: "Hello again" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(post).not.toHaveBeenCalled(); // a plain Enter is a new line
  expect(screen.getByTestId("edit-note")).toHaveTextContent("Ctrl+Enter saves");
  fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/chats/c/messages/u/edit", { content: "Hello again" }),
  );
});

it("opens an attached image full size, and keeps a long file name on one line", async () => {
  vi.mocked(fileUrl).mockResolvedValue("blob:picture");
  const long = "a-very-long-report-name-that-would-otherwise-stretch-the-row.pdf";
  show(
    message({
      files: [
        { id: "i", name: "photo.png", mediaType: "image/png" },
        { id: "p", name: long, mediaType: "application/pdf" },
      ],
    }),
  );
  const link = await screen.findByTestId("attachment-image");
  expect(link).toHaveAttribute("href", "blob:picture");
  expect(link).toHaveAttribute("target", "_blank");
  const chip = screen.getByTitle(long);
  expect(chip.className).toMatch(/max-w-/);
  expect(screen.getByText(long).className).toMatch(/truncate/);
});

it("shows the site each source is on", () => {
  show(
    message({
      role: "assistant",
      content: "An answer.",
      searches: 1,
      sources: [{ url: "https://www.example.org/a", title: "A page" }],
    }),
  );
  expect(screen.getByRole("link", { name: "A page" })).toBeInTheDocument();
  expect(screen.getByTestId("source-site")).toHaveTextContent("example.org");
  expect(siteOf("javascript:alert(1)")).toBeNull();
  expect(siteOf("not a url")).toBeNull();
});

it("gives an earlier day's time its date, and today's time alone", () => {
  const now = new Date(2026, 9, 9, 15, 0);
  const today = shortTime(new Date(2026, 9, 9, 9, 5), now);
  const earlier = shortTime(new Date(2026, 9, 7, 9, 5), now);
  const lastYear = shortTime(new Date(2025, 9, 7, 9, 5), now);
  expect(today).not.toMatch(/Oct/);
  expect(earlier).toMatch(/Oct/);
  expect(earlier).not.toMatch(/2025|2026/);
  expect(lastYear).toMatch(/2025/);
});
