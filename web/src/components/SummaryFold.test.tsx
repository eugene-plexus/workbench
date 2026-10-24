import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../lib/api";
import { foldsOf, fullnessWords } from "../lib/longChats";
import type { Chat, ChatDetail, LongChatView, Me, Message } from "../lib/types";
import { progressWords } from "../lib/words";
import { ChatSettings } from "./ChatSettings";
import { ChatView } from "./ChatView";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

let detail: ChatDetail;
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));
vi.mock("./AGUIConversation", () => ({ AGUIConversation: () => null }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update: vi.fn(),
    progress: null,
    error: null,
    notice: null,
    reload: vi.fn(),
    look: vi.fn(),
  }),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "The garden",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
};

const RUN = { id: "s1", firstId: "m3", lastId: "m6", from: 3, to: 6, messages: 4, tokens: 120 };

function messages(): Message[] {
  return Array.from({ length: 8 }, (_, i) => ({
    id: `m${i + 1}`,
    seq: i + 1,
    role: i % 2 === 0 ? "user" : "assistant",
    content: `Message ${i + 1}`,
    reasoning: "",
    attachments: [],
    status: "done",
    error: null,
    sources: [],
    searches: 0,
    search: false,
    model: "local",
    finish: "stop",
    createdAt: 1,
    finishedAt: 2,
    // The last answer went with a summary of messages 3 to 6; the one before it whole.
    summaries: i === 7 ? [RUN] : i === 5 ? [] : null,
  }));
}

const element = (at: string | null = null) => (
  <ChatView
    chatId="one"
    me={me}
    models={null}
    modelsError={null}
    onChanged={vi.fn()}
    onDeleted={vi.fn()}
    onOpenMemory={vi.fn()}
    onOpenDocuments={vi.fn()}
    at={at}
  />
);

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.mocked(api).mockReset();
  detail = { chat, messages: messages(), ownerName: null };
});

const shown = (id: string) => document.getElementById(`message-${id}`);

it("folds the run the model was sent as a summary", () => {
  render(element());
  const fold = screen.getByTestId("summary-fold");
  expect(within(fold).getByText("Earlier messages summarised")).toBeInTheDocument();
  expect(within(fold).getByText("4 messages")).toBeInTheDocument();
  for (const id of ["m3", "m4", "m5", "m6"]) expect(shown(id)).toBeNull();
  for (const id of ["m1", "m2", "m7", "m8"]) expect(shown(id)).not.toBeNull();
  // The fold stands where its run was.
  const order = [...document.querySelectorAll('[id^="message-m"], [data-testid="summary-fold"]')];
  expect(order.map((e) => e.id || "fold")).toEqual([
    "message-m1",
    "message-m2",
    "fold",
    "message-m7",
    "message-m8",
  ]);
  expect(screen.getByTestId("sent-summaries")).toHaveTextContent(
    "Sent with A summary of messages 3-6 (120 tokens).",
  );
});

it("unfolds the originals under a band, and folds them again", () => {
  render(element());
  fireEvent.click(screen.getByRole("button", { name: "Show them" }));
  for (const id of ["m3", "m4", "m5", "m6"]) expect(shown(id)).not.toBeNull();
  expect(screen.getByTestId("summary-band")).toHaveTextContent("Sent to the model as a summary.");
  fireEvent.click(screen.getByRole("button", { name: "Hide them" }));
  expect(shown("m4")).toBeNull();
});

it("a link into the run unfolds it", () => {
  render(element("m4"));
  expect(shown("m4")).not.toBeNull();
  expect(screen.getByRole("button", { name: "Hide them" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

it("reads the summary, with who wrote it from how much", async () => {
  vi.mocked(api).mockResolvedValue({
    ...RUN,
    sourceTokens: 1800,
    model: "qwen3-8b",
    madeAt: 1_792_540_800,
    cut: false,
    how: "auto",
    text: "The person plans beds, paths and a pond.",
    hidden: null,
  });
  render(element());
  fireEvent.click(screen.getByRole("button", { name: "Read the summary" }));
  expect(await screen.findByTestId("summary-text")).toHaveTextContent(
    "The person plans beds, paths and a pond.",
  );
  expect(api).toHaveBeenCalledWith("/api/chats/one/summaries/s1");
  expect(
    screen.getByText(/^Written by qwen3-8b on .+ from about 1,800 tokens; 120 tokens\.$/),
  ).toBeInTheDocument();
});

it("an answer sent whole folds nothing", () => {
  detail = {
    chat,
    messages: messages().map((m) => ({ ...m, summaries: m.id === "m8" ? [] : null })),
    ownerName: null,
  };
  render(element());
  expect(screen.queryByTestId("summary-fold")).toBeNull();
  expect(foldsOf(detail.messages)).toEqual([]);
});

it("says a backend dropped part of the chat", () => {
  detail = {
    chat,
    messages: messages().map((m) => (m.id === "m8" ? { ...m, promptTruncated: true } : m)),
    ownerName: null,
  };
  render(element());
  expect(screen.getByTestId("prompt-truncated")).toHaveTextContent(
    "local dropped part of this chat to fit its window, so this answer may miss earlier messages. The next message is sent with a summary.",
  );
});

it("says what is summarised while it runs", () => {
  expect(progressWords({ stage: "summarising", messages: 16, model: "qwen3-8b" })).toBe(
    "Summarising 16 earlier messages to fit qwen3-8b's window…",
  );
});

const LONG: LongChatView = {
  model: "qwen3-8b",
  on: true,
  installOn: true,
  chatOn: true,
  percent: 80,
  percentSetBy: "install",
  window: 32768,
  trigger: 26214,
  ceiling: null,
  about: 21400,
  measured: true,
  summaries: 1,
};

it("the drawer says how full the chat is, and the switch saves at once", async () => {
  expect(fullnessWords(LONG)).toBe(
    `About ${(21400).toLocaleString()} of ${(32768).toLocaleString()} tokens · summarises at ${(26214).toLocaleString()} · 1 summary`,
  );
  expect(fullnessWords({ ...LONG, window: null, trigger: null, about: null })).toBe(
    "This model's window is not known. Workbench summarises only when the model says the chat is too long.",
  );
  vi.mocked(api).mockResolvedValue(LONG);
  const save = vi.fn().mockResolvedValue(undefined);
  render(<ChatSettings chatId="one" settings={{}} onSave={save} onClose={vi.fn()} />);
  expect(await screen.findByTestId("long-chat-line")).toHaveTextContent(fullnessWords(LONG));
  const switch_ = screen.getByRole("checkbox", { name: "Summarise when this chat is long: On" });
  expect(switch_).toBeChecked();
  fireEvent.click(switch_);
  await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith({ summarise: false }));
});

it("the switch shows off when the owner turned summarising off", async () => {
  vi.mocked(api).mockResolvedValue({ ...LONG, on: false, installOn: false });
  render(<ChatSettings chatId="one" settings={{}} onSave={vi.fn()} onClose={vi.fn()} />);
  const switch_ = await screen.findByRole("checkbox", {
    name: "Summarise when this chat is long: Off",
  });
  expect(switch_).not.toBeChecked();
  expect(switch_).toBeDisabled();
  expect(
    screen.getByText("Off for this Workbench: its owner turned summarising off."),
  ).toBeVisible();
});
