/**
 * A chat with the person's documents (MR4): the Documents chip beside the Memory chip,
 * the drawer it opens, the quiet hint in the composer, and what a search that went
 * wrong says under an answer, against a fake Workbench that answers each address as the
 * real one does.
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, del, patch, post } from "../lib/api";
import type { Chat, ChatDetail, ChatDocumentsView, Me, Message, Models } from "../lib/types";
import { sending as notesSending } from "../test-memory";
import { chatDocuments, held, off, owners, record } from "../test-saved";
import { ChatView } from "./ChatView";
vi.mock("./AGUIConversation", () => ({ AGUIConversation: () => null }));

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

const reload = vi.fn();
const update = vi.fn();
let detail: ChatDetail;
// The settings panel's own lists fetch; this file is about the chat around it.
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update,
    progress: null,
    error: null,
    reload,
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
const models: Models = {
  models: [
    {
      id: "local",
      ready: true,
      onDemand: false,
      imageInput: false,
      fileInput: false,
      audioInput: false,
      webSearch: false,
      contextLength: 32768,
    },
  ],
  webSearch: { available: false, reason: null },
};

const message = (over: Partial<Message>): Message => ({
  id: "m",
  seq: 1,
  role: "assistant",
  content: "",
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
  finishedAt: 2,
  ...over,
});

/** What the fake server answers with for this chat's documents; a function so a test can change it. */
let documents: () => ChatDocumentsView | Error;

function listen() {
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/chats/one/documents") {
      const view = documents();
      if (view instanceof Error) throw view;
      return view;
    }
    if (path === "/api/chats/one/memory") return notesSending();
    if (path.startsWith("/api/chats/one/files"))
      return {
        model: { id: "local", known: true, window: 32768, fileInput: false },
        files: [],
        request: null,
      };
    throw new Error(`not served: ${path}`);
  }) as never);
}

const documentCalls = () =>
  vi.mocked(api).mock.calls.filter(([path]) => path === "/api/chats/one/documents");

function view(over: { readOnly?: boolean; onOpenDocuments?: () => void } = {}) {
  const onOpenDocuments = over.onOpenDocuments ?? vi.fn();
  const again = () => (
    <ChatView
      chatId="one"
      me={me}
      models={models}
      modelsError={null}
      onChanged={vi.fn()}
      onDeleted={vi.fn()}
      onOpenMemory={vi.fn()}
      onOpenDocuments={onOpenDocuments}
    />
  );
  const shown = render(again());
  return { ...shown, again, onOpenDocuments };
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  window.localStorage.clear();
  vi.mocked(api).mockReset();
  vi.mocked(del).mockReset();
  vi.mocked(patch)
    .mockReset()
    .mockResolvedValue({ ...chat, settings: { documents: "on" } });
  vi.mocked(post).mockReset();
  reload.mockReset();
  update.mockReset();
  detail = { chat, messages: [], ownerName: null };
  documents = () => chatDocuments();
  listen();
});

describe("the Documents chip", () => {
  it("sits in the header between the title and the Memory chip, and says what the next message does", async () => {
    view();
    const chip = await screen.findByRole("button", { name: "Documents (3)" });
    expect(chip).toHaveAttribute("data-testid", "documents-chip");
    const order = Array.from(chip.parentElement!.children);
    expect(order.indexOf(screen.getByTestId("chat-title"))).toBeLessThan(order.indexOf(chip));
    expect(order.indexOf(chip)).toBeLessThan(order.indexOf(screen.getByTestId("memory-chip")));
    expect(documentCalls()).toHaveLength(1);
  });

  it("says plain Documents until it has read, and that it could not when it could not", async () => {
    documents = () => new Error("Workbench answered 502.");
    view();
    expect(await screen.findByRole("button", { name: "Documents: not available" })).toHaveClass(
      "text-warn",
    );
    fireEvent.click(screen.getByTestId("documents-chip"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
  });

  it("keeps what it said when a later read fails, and says why in the drawer", async () => {
    view();
    await screen.findByRole("button", { name: "Documents (3)" });
    documents = () => new Error("Workbench answered 502.");
    fireEvent.click(screen.getByTestId("documents-chip"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
    // It still has what it read before: it does not claim to have nothing.
    expect(screen.getByTestId("documents-chip")).toHaveAccessibleName("Documents (3)");
  });

  it("is a chip even in someone else's chat, where it says whether Documents was on and nothing more", async () => {
    documents = () => owners();
    detail = {
      chat: { ...chat, readOnly: true },
      messages: [message({ role: "user", content: "Hi" })],
      ownerName: "Bo",
    };
    view();
    expect(await screen.findByRole("button", { name: "Documents on" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("documents-chip"));
    expect(screen.getByTestId("documents-status")).toHaveTextContent("not what is in them");
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("opens its drawer on a press, reads the documents again, and closes it on a second press", async () => {
    view();
    const chip = await screen.findByTestId("documents-chip");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("documents-drawer")).toBeNull();
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("documents-drawer")).toBeInTheDocument();
    await waitFor(() => expect(documentCalls()).toHaveLength(2));
    fireEvent.click(chip);
    expect(screen.queryByTestId("documents-drawer")).toBeNull();
  });

  it("is one panel at a time with the chat's settings and Memory, whichever is opened last", async () => {
    view();
    const chip = await screen.findByTestId("documents-chip");
    const settings = screen.getByRole("button", { name: "This chat's settings" });
    const memory = screen.getByTestId("memory-chip");
    fireEvent.click(chip);
    expect(screen.getByTestId("documents-drawer")).toBeInTheDocument();
    fireEvent.click(memory);
    expect(screen.queryByTestId("documents-drawer")).toBeNull();
    expect(screen.getByTestId("memory-drawer")).toBeInTheDocument();
    expect(chip).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(chip);
    expect(screen.queryByTestId("memory-drawer")).toBeNull();
    expect(screen.getByTestId("documents-drawer")).toBeInTheDocument();
    fireEvent.click(settings);
    expect(screen.queryByTestId("documents-drawer")).toBeNull();
    expect(screen.getByTestId("chat-settings")).toBeInTheDocument();
    expect(chip).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the Documents screen from the drawer's Manage documents", async () => {
    const { onOpenDocuments } = view();
    fireEvent.click(await screen.findByTestId("documents-chip"));
    fireEvent.click(screen.getByRole("button", { name: "Manage documents" }));
    expect(onOpenDocuments).toHaveBeenCalledOnce();
  });

  it("reads the documents again when the chat's model changes, and when a documents setting does, and not when something else does", async () => {
    const { rerender, again } = view();
    await screen.findByTestId("documents-chip");
    expect(documentCalls()).toHaveLength(1);
    rerender(again());
    expect(documentCalls()).toHaveLength(1);
    // Not a setting that decides what is searched.
    detail = { chat: { ...chat, settings: { temperature: 0.5 } }, messages: [], ownerName: null };
    rerender(again());
    expect(documentCalls()).toHaveLength(1);
    detail = { chat: { ...chat, model: "gpt-5" }, messages: [], ownerName: null };
    rerender(again());
    await waitFor(() => expect(documentCalls()).toHaveLength(2));
    detail = {
      chat: { ...chat, model: "gpt-5", settings: { documents: "on" } },
      messages: [],
      ownerName: null,
    };
    rerender(again());
    await waitFor(() => expect(documentCalls()).toHaveLength(3));
    detail = {
      chat: { ...chat, model: "gpt-5", settings: { documents: "on", documentsOnly: true } },
      messages: [],
      ownerName: null,
    };
    rerender(again());
    await waitFor(() => expect(documentCalls()).toHaveLength(4));
    detail = {
      chat: {
        ...chat,
        model: "gpt-5",
        settings: { documents: "on", documentsOnly: true, documentsExternal: true },
      },
      messages: [],
      ownerName: null,
    };
    rerender(again());
    await waitFor(() => expect(documentCalls()).toHaveLength(5));
  });

  it("is told the chat changed, and reads again, when the drawer's switch is turned", async () => {
    documents = () => off();
    view();
    fireEvent.click(await screen.findByTestId("documents-chip"));
    const before = documentCalls().length;
    fireEvent.click(await screen.findByRole("switch", { name: "Documents in this chat" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/one", {
        settings: { documents: "on" },
      }),
    );
    await waitFor(() => expect(documentCalls().length).toBeGreaterThan(before));
    // The chat shown is the chat as the server now has it.
    await waitFor(() => expect(update).toHaveBeenCalled());
    const change = update.mock.calls.at(-1)![0] as (shown: ChatDetail) => ChatDetail;
    expect(change(detail).chat.settings).toEqual({ documents: "on" });
  });

  it("reads the documents again after a message is sent", async () => {
    vi.mocked(post).mockResolvedValue({
      user: message({ id: "u1", role: "user", content: "x" }),
      answer: message({ id: "a1", status: "running" }),
    });
    view();
    await screen.findByTestId("documents-chip");
    const before = documentCalls().length;
    fireEvent.change(await screen.findByTestId("composer"), {
      target: { value: "What is the code word?" },
    });
    fireEvent.click(screen.getByTestId("send"));
    await waitFor(() => expect(post).toHaveBeenCalled());
    await waitFor(() => expect(documentCalls().length).toBeGreaterThan(before));
  });
});

describe("the hint in the composer", () => {
  it("says the person's documents are not searched here, when they have some and the chat has Documents off", async () => {
    documents = () => off();
    view();
    const hint = await screen.findByTestId("documents-hint");
    expect(hint).toHaveTextContent("Your 3 documents are off here.");
    // In the composer, under the tools, beside nothing that is an alert.
    expect(screen.getByTestId("composer-area")).toContainElement(hint);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is not there while Documents are on, or when there is nothing to search, or when the owner turned them off", async () => {
    documents = () => chatDocuments();
    const first = view();
    await screen.findByTestId("documents-chip");
    expect(screen.queryByTestId("documents-hint")).toBeNull();
    first.unmount();
    documents = () =>
      off({
        counts: {
          library: { documents: 0, passages: 0, waiting: 1 },
          chat: { documents: 0, passages: 0, waiting: 0 },
        },
      });
    const second = view();
    await screen.findByTestId("documents-chip");
    expect(screen.queryByTestId("documents-hint")).toBeNull();
    second.unmount();
    documents = () =>
      held("install_off", "The owner of this Workbench has turned off documents.", {
        switch: "off",
      });
    view();
    await screen.findByTestId("documents-chip");
    expect(screen.queryByTestId("documents-hint")).toBeNull();
  });

  it("is not there in someone else's chat, which has no composer", async () => {
    documents = () => owners({ switch: "off" });
    detail = { chat: { ...chat, readOnly: true }, messages: [], ownerName: "Bo" };
    view();
    await screen.findByTestId("documents-chip");
    expect(screen.queryByTestId("documents-hint")).toBeNull();
  });

  it("turns Documents on in this chat, and is gone once it is", async () => {
    let on = false;
    documents = () => (on ? chatDocuments() : off());
    vi.mocked(patch).mockImplementation((async () => {
      on = true;
      return { ...chat, settings: { documents: "on" } };
    }) as never);
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Turn on Documents" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/one", {
        settings: { documents: "on" },
      }),
    );
    await waitFor(() => expect(screen.queryByTestId("documents-hint")).toBeNull());
    expect(await screen.findByRole("button", { name: "Documents (3)" })).toBeInTheDocument();
  });

  it("is held while it turns Documents on, so it is not turned on twice", async () => {
    documents = () => off();
    let done: (chat: Chat) => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise<Chat>((resolve) => (done = resolve)) as never);
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Turn on Documents" }));
    expect(screen.getByRole("button", { name: "Turn on Documents" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Hide this hint" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Turn on Documents" }));
    expect(patch).toHaveBeenCalledOnce();
    await act(async () => done({ ...chat, settings: { documents: "on" } }));
  });

  it("says why it could not turn Documents on, and stays", async () => {
    documents = () => off();
    vi.mocked(patch).mockRejectedValue(new Error("This chat is gone."));
    view();
    fireEvent.click(await screen.findByRole("button", { name: "Turn on Documents" }));
    expect(await screen.findByTestId("documents-hint-problem")).toHaveTextContent(
      "This chat is gone.",
    );
    expect(screen.getByTestId("documents-hint")).toBeInTheDocument();
  });

  it("can be hidden, for good, by the person who hid it", async () => {
    documents = () => off();
    const first = view();
    fireEvent.click(await screen.findByRole("button", { name: "Hide this hint" }));
    expect(screen.queryByTestId("documents-hint")).toBeNull();
    expect(window.localStorage.getItem("workbench-documents-hint:ada")).toBe("hidden");
    first.unmount();
    view();
    await screen.findByTestId("documents-chip");
    expect(screen.queryByTestId("documents-hint")).toBeNull();
  });

  it("is still there for someone else in the same browser", async () => {
    window.localStorage.setItem("workbench-documents-hint:bo", "hidden");
    documents = () => off();
    view();
    expect(await screen.findByTestId("documents-hint")).toBeInTheDocument();
  });
});

describe("under an answer", () => {
  const damaged = message({
    id: "a9",
    content: "I do not know.",
    retrieval: record({
      status: "failed",
      code: "index_damaged",
      message:
        "The search index for your documents is damaged (SQLite said: malformed). Your files are safe.",
      passages: 0,
      documents: 0,
      cited: [],
    }),
  });

  it("leads from a search that failed to the screen that mends it", async () => {
    detail = {
      chat,
      messages: [message({ id: "u9", role: "user", content: "Q" }), damaged],
      ownerName: null,
    };
    const { onOpenDocuments } = view();
    fireEvent.click(
      await screen.findByRole("button", { name: "Open Documents to rebuild the search" }),
    );
    expect(onOpenDocuments).toHaveBeenCalledOnce();
  });

  it("offers the owner reading someone's chat no way into the screen", async () => {
    detail = {
      chat: { ...chat, readOnly: true },
      messages: [message({ id: "u9", role: "user", content: "Q" }), damaged],
      ownerName: "Bo",
    };
    documents = () => owners();
    view();
    await screen.findByTestId("documents-chip");
    expect(screen.getByTestId("search-failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Open Documents/ })).toBeNull();
  });
});
