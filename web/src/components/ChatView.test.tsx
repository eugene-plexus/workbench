import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, del, patch, post } from "../lib/api";
import type { Chat, ChatDetail, Me, Message, Models } from "../lib/types";
import { held, note, owners, sending } from "../test-memory";
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
let loadError: string | null = null;
let purgeNotice: string | null = null;
// The settings panel's own lists fetch; this file is about the chat around it.
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));
vi.mock("./Skills", () => ({ SkillSelection: () => null }));
// Agent controls have their own tests and the G5 browser wiring acceptance.
vi.mock("./AGUIConversation", () => ({ AGUIConversation: () => null }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update: vi.fn(),
    progress: null,
    error: loadError,
    notice: purgeNotice,
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

const element = () => (
  <ChatView
    chatId="one"
    me={me}
    models={null}
    modelsError={null}
    onChanged={vi.fn()}
    onDeleted={vi.fn()}
    onOpenMemory={vi.fn()}
    onOpenDocuments={vi.fn()}
  />
);

function view() {
  return render(element());
}

it("run_artifacts_off_shows_run_without_starting_the_artifact", async () => {
  detail = {
    chat: { ...chat, settings: { runArtifacts: false } },
    messages: [message({ content: '```html artifact title="Page"\n<h1>Page</h1>\n```' })],
    ownerName: null,
  };
  view();
  const run = await screen.findByRole("button", { name: "Run" });
  await new Promise((done) => setTimeout(done, 50));
  expect(screen.queryByTestId("artifact-frame")).toBeNull();
  fireEvent.click(run);
  expect(await screen.findByTestId("artifact-frame")).toHaveAttribute(
    "sandbox",
    "allow-scripts allow-forms",
  );
});

it("turning_run_artifacts_off_stops_an_automatic_preview_and_keeps_manual_run", async () => {
  detail = {
    chat,
    messages: [message({ content: '```html artifact title="Page"\n<h1>Page</h1>\n```' })],
    ownerName: null,
  };
  const shown = view();
  await screen.findByTestId("artifact-frame");
  detail = { ...detail, chat: { ...chat, settings: { runArtifacts: false } } };
  shown.rerender(element());
  await waitFor(() => expect(screen.queryByTestId("artifact-frame")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(await screen.findByTestId("artifact-frame")).toBeInTheDocument();
});

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

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.mocked(api).mockReset();
  vi.mocked(del).mockReset();
  vi.mocked(patch).mockReset();
  vi.mocked(post).mockReset();
  reload.mockReset();
  loadError = null;
  purgeNotice = null;
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

it("shows the older recall removal notice after a purge", () => {
  purgeNotice = "Also removed 3 older memory recalls that could quote this answer.";
  view();
  expect(screen.getByText(purgeNotice)).toHaveAttribute("role", "status");
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

describe("the conversation", () => {
  it("is a named region a keyboard can scroll, with room kept for its scrollbar", () => {
    view();
    const region = screen.getByRole("region", { name: "Conversation" });
    expect(region).toBe(screen.getByTestId("messages"));
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveAttribute("id", "conversation");
    expect(region.className).toContain("[scrollbar-gutter:stable_both-edges]");
  });

  it("holds what is hidden in it for a screen reader, so nothing deep in it scrolls the page", () => {
    view();
    const region = screen.getByRole("region", { name: "Conversation" });
    expect(region).toHaveClass("relative", "overflow-y-auto");
  });

  it("sits on the same frame as the header and the composer", () => {
    detail = { chat, messages: [message({ role: "user", content: "Hi" })], ownerName: null };
    view();
    const frame = screen.getByTestId("messages").firstElementChild;
    expect(frame).toHaveClass("max-w-3xl", "mx-auto");
    expect(screen.getByTestId("chat-title").parentElement).toHaveClass("max-w-3xl", "mx-auto");
    expect(screen.getByTestId("composer-area").firstElementChild).toHaveClass("max-w-3xl");
  });
});

describe("what a screen reader is told as the newest answer changes", () => {
  const asked = message({ id: "u", role: "user", content: "Why?", status: "done" });
  const show = (answer: Partial<Message>) => {
    detail = { chat, messages: [asked, message({ id: "a", ...answer })], ownerName: null };
  };

  it("says nothing of what is already on the page when a chat opens", () => {
    show({ status: "done", content: "Because." });
    view();
    const announcer = screen.getByTestId("announcer");
    expect(announcer).toHaveAttribute("aria-live", "polite");
    expect(announcer).toHaveAttribute("aria-atomic", "true");
    expect(announcer).toHaveClass("sr-only");
    expect(announcer).toBeEmptyDOMElement();
  });

  it("says each phase as it changes, and not the same phase again", () => {
    show({ status: "running" });
    const { rerender } = view();
    const announcer = screen.getByTestId("announcer");
    expect(announcer).toBeEmptyDOMElement();
    const next = (answer: Partial<Message>) => {
      show(answer);
      rerender(element());
    };
    next({ status: "running", reasoning: "Hmm" });
    expect(announcer).toHaveTextContent("Thinking.");
    // The reasoning growing is the same phase: the text is unchanged.
    next({ status: "running", reasoning: "Hmm, let me see" });
    expect(announcer).toHaveTextContent("Thinking.");
    next({ status: "running", reasoning: "Hmm", content: "Because" });
    expect(announcer).toHaveTextContent("Writing the answer.");
    next({ status: "done", reasoning: "Hmm", content: "Because." });
    expect(announcer).toHaveTextContent("Answer finished.");
  });

  it("is a plain live region and not a status: the page's own status stays one", () => {
    show({ status: "done", content: "Because." });
    view();
    expect(screen.getByTestId("announcer")).not.toHaveAttribute("role");
  });
});

describe("the header and its delete question", () => {
  it("has four icon buttons, and the delete one is the danger one", () => {
    view();
    for (const name of [
      "Export chat as Markdown",
      "Rename this chat",
      "This chat's settings",
      "Delete this chat",
    ]) {
      expect(screen.getByRole("button", { name })).toHaveClass("icon-btn");
    }
    expect(screen.getByRole("button", { name: "Delete this chat" })).toHaveClass(
      "icon-btn--danger",
    );
    expect(screen.getByRole("button", { name: "Rename this chat" })).not.toHaveClass(
      "icon-btn--danger",
    );
  });

  it("asks in an error notice: the safe answer first and focused, the danger answer second", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Delete this chat" }));
    const question = screen.getByRole("group", { name: "Delete this chat?" });
    expect(question).toHaveClass("notice", "notice--error");
    const [first, second] = Array.from(question.querySelectorAll("button"));
    expect(first).toBe(screen.getByTestId("keep-chat"));
    expect(first).toHaveFocus();
    expect(first).toHaveClass("btn");
    expect(second).toBe(screen.getByTestId("confirm-delete"));
    expect(second).toHaveClass("btn", "btn-danger");
    // The icon buttons give way to the question while it is asked.
    expect(screen.queryByRole("button", { name: "Rename this chat" })).toBeNull();
  });

  it("names the chat's title in the sentence, however long and unbroken it is", () => {
    const title = "x".repeat(150);
    detail = { chat: { ...chat, title }, messages: [], ownerName: null };
    view();
    fireEvent.click(screen.getByRole("button", { name: "Delete this chat" }));
    expect(screen.getByText(`Delete "${title}" and the files attached to it?`)).toBeInTheDocument();
  });

  it("renames in a field and two icon buttons, with the names the tests read", () => {
    view();
    fireEvent.click(screen.getByRole("button", { name: "Rename this chat" }));
    expect(screen.getByRole("textbox", { name: "Chat name" })).toHaveClass("control");
    expect(screen.getByRole("button", { name: "Save the name" })).toHaveClass("icon-btn");
    expect(screen.getByRole("button", { name: "Keep the old name" })).toHaveClass("icon-btn");
  });

  it("shows the chat's settings open on its button", () => {
    view();
    const settings = screen.getByRole("button", { name: "This chat's settings" });
    expect(settings).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(settings);
    expect(settings).toHaveAttribute("aria-expanded", "true");
  });
});

describe("what is said above the thread", () => {
  it("says someone else's chat in a warning bar, and has no composer", () => {
    detail = {
      chat: { ...chat, readOnly: true },
      messages: [message({ role: "user", content: "Hi" })],
      ownerName: "Bo",
    };
    view();
    const note = screen.getByTestId("read-only");
    expect(note).toHaveClass("notice", "notice--warn", "notice--bar");
    expect(note).toHaveTextContent("Bo's chat. You can read it");
    expect(screen.queryByTestId("composer")).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete this chat" })).toBeNull();
  });

  it("says why a chat did not load, as an error bar with a way to load it again", () => {
    loadError = "Workbench answered 502.";
    view();
    // The composer has an alert of its own (no model is served here).
    const bar = screen.getByText(/Workbench answered 502[.]/);
    expect(bar).toHaveAttribute("role", "alert");
    expect(bar).toHaveClass("notice", "notice--error", "notice--bar");
    expect(bar).toHaveTextContent("Workbench answered 502.");
    fireEvent.click(screen.getByRole("button", { name: "Reload chat" }));
    expect(reload).toHaveBeenCalledOnce();
  });

  it("names the cause when there is no chat to show at all, and offers to try again", () => {
    loadError = "Workbench answered 404.";
    detail = null as unknown as ChatDetail;
    view();
    const failure = screen.getByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Workbench answered 404.");
    fireEvent.click(screen.getByRole("button", { name: "Try loading this chat again" }));
    expect(reload).toHaveBeenCalledOnce();
  });
});

describe("Jump to latest", () => {
  /** The thread, scrolled by a reader: first down at the bottom, then up. */
  function scrollUp() {
    const box = screen.getByTestId("messages");
    let top = 0;
    Object.defineProperty(box, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(box, "clientHeight", { value: 400, configurable: true });
    Object.defineProperty(box, "scrollTop", {
      get: () => top,
      set: (value: number) => {
        top = value;
      },
      configurable: true,
    });
    top = 600;
    fireEvent.scroll(box);
    top = 300;
    fireEvent.scroll(box);
    return box;
  }

  it("is not there while the reader is at the bottom", () => {
    view();
    expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull();
  });

  it("floats over the thread, so showing it does not resize the pane, and takes the reader down", () => {
    view();
    const box = scrollUp();
    const jump = screen.getByRole("button", { name: "Jump to latest" });
    expect(jump).toHaveClass("btn", "absolute", "rounded-full");
    // It is inside the thread's own relative box, not a band between two rules.
    expect(jump.parentElement).toHaveClass("relative");
    expect(jump.parentElement).toContainElement(box);
    fireEvent.click(jump);
    expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull();
    expect(box.scrollTop).toBe(1000);
  });
});

describe("Memory in a chat", () => {
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

  function shown() {
    const onOpenMemory = vi.fn();
    const view = render(
      <ChatView
        chatId="one"
        me={me}
        models={models}
        modelsError={null}
        onChanged={vi.fn()}
        onDeleted={vi.fn()}
        onOpenMemory={onOpenMemory}
        onOpenDocuments={vi.fn()}
      />,
    );
    return { ...view, onOpenMemory };
  }

  const memoryCalls = () =>
    vi.mocked(api).mock.calls.filter(([path]) => path === "/api/chats/one/memory");

  it("has a chip in the header that says what the next message carries, between the title and the buttons", async () => {
    vi.mocked(api).mockResolvedValue(sending());
    shown();
    const chip = await screen.findByRole("button", { name: "Memory: 1 note sent" });
    expect(chip).toHaveAttribute("data-testid", "memory-chip");
    const header = chip.parentElement!;
    const order = Array.from(header.children);
    expect(order.indexOf(screen.getByTestId("chat-title"))).toBeLessThan(order.indexOf(chip));
    expect(order.indexOf(chip)).toBeLessThan(
      order.indexOf(screen.getByRole("button", { name: "Export chat as Markdown" })),
    );
    expect(memoryCalls()).toHaveLength(1);
  });

  it("says plain Memory until it has read, and that it could not when it could not", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    shown();
    expect(await screen.findByRole("button", { name: "Memory: not available" })).toHaveClass(
      "text-warn",
    );
    fireEvent.click(screen.getByTestId("memory-chip"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
  });

  it("is a chip even in someone else's chat, where it says how many notes were used and nothing more", async () => {
    vi.mocked(api).mockResolvedValue(owners());
    detail = {
      chat: { ...chat, readOnly: true },
      messages: [message({ role: "user", content: "Hi" })],
      ownerName: "Bo",
    };
    shown();
    expect(await screen.findByRole("button", { name: "Memory: 2 notes used" })).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("memory-chip"));
    expect(screen.getByTestId("memory-status")).toHaveTextContent("not what they said");
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("opens its drawer on a press, reads the notes again, and closes it on a second press", async () => {
    vi.mocked(api).mockResolvedValue(sending());
    shown();
    const chip = await screen.findByTestId("memory-chip");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("memory-drawer")).toBeNull();
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("memory-drawer")).toBeInTheDocument();
    await waitFor(() => expect(memoryCalls()).toHaveLength(2));
    fireEvent.click(chip);
    expect(screen.queryByTestId("memory-drawer")).toBeNull();
  });

  it("is one panel at a time with the chat's settings, whichever is opened last", async () => {
    vi.mocked(api).mockResolvedValue(sending());
    shown();
    const chip = await screen.findByTestId("memory-chip");
    const settings = screen.getByRole("button", { name: "This chat's settings" });
    fireEvent.click(chip);
    expect(screen.getByTestId("memory-drawer")).toBeInTheDocument();
    fireEvent.click(settings);
    expect(screen.queryByTestId("memory-drawer")).toBeNull();
    expect(screen.getByTestId("chat-settings")).toBeInTheDocument();
    expect(settings).toHaveAttribute("aria-expanded", "true");
    expect(chip).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(chip);
    expect(screen.queryByTestId("chat-settings")).toBeNull();
    expect(screen.getByTestId("memory-drawer")).toBeInTheDocument();
    expect(settings).toHaveAttribute("aria-expanded", "false");
  });

  it("opens the Memory page from the drawer's Manage notes", async () => {
    vi.mocked(api).mockResolvedValue(sending());
    const { onOpenMemory } = shown();
    fireEvent.click(await screen.findByTestId("memory-chip"));
    fireEvent.click(screen.getByRole("button", { name: "Manage notes" }));
    expect(onOpenMemory).toHaveBeenCalledOnce();
  });

  it("reads the notes again when the chat's model changes, and when its memory settings do", async () => {
    vi.mocked(api).mockResolvedValue(sending());
    const again = () => (
      <ChatView
        chatId="one"
        me={me}
        models={models}
        modelsError={null}
        onChanged={vi.fn()}
        onDeleted={vi.fn()}
        onOpenMemory={vi.fn()}
        onOpenDocuments={vi.fn()}
      />
    );
    const { rerender } = render(again());
    await screen.findByTestId("memory-chip");
    expect(memoryCalls()).toHaveLength(1);
    // Nothing changed: nothing is read.
    rerender(again());
    expect(memoryCalls()).toHaveLength(1);
    detail = { chat: { ...chat, model: "gpt-5" }, messages: [], ownerName: null };
    rerender(again());
    await waitFor(() => expect(memoryCalls()).toHaveLength(2));
    detail = {
      chat: { ...chat, model: "gpt-5", settings: { memory: "off" } },
      messages: [],
      ownerName: null,
    };
    rerender(again());
    await waitFor(() => expect(memoryCalls()).toHaveLength(3));
    detail = {
      chat: { ...chat, model: "gpt-5", settings: { memory: "off", memoryExternal: true } },
      messages: [],
      ownerName: null,
    };
    rerender(again());
    await waitFor(() => expect(memoryCalls()).toHaveLength(4));
  });

  describe("after a message that begins with a command", () => {
    const sent = (memory: unknown) => {
      vi.mocked(post).mockResolvedValue({
        user: message({ id: "u1", role: "user", content: "x" }),
        answer: message({ id: "a1", status: "running" }),
        memory,
      });
    };

    async function say(words: string) {
      fireEvent.change(await screen.findByTestId("composer"), { target: { value: words } });
      fireEvent.click(screen.getByTestId("send"));
    }

    it("says what was kept, in a toast over the thread, and the chip is read again", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      sent({
        command: "remember",
        remembered: note({ text: "I use metric units" }),
        created: true,
      });
      shown();
      await screen.findByTestId("memory-chip");
      const before = memoryCalls().length;
      await say("Remember that I use metric units");
      const toast = await screen.findByTestId("memory-toast");
      expect(toast).toHaveTextContent("Remembered: “I use metric units”");
      // Over the thread, above the composer, not in the flow of the page.
      expect(toast.parentElement).toHaveClass("absolute", "z-10", "bottom-4");
      expect(toast.parentElement!.parentElement).toContainElement(screen.getByTestId("messages"));
      await waitFor(() => expect(memoryCalls().length).toBeGreaterThan(before));
    });

    it("sits above the jump pill when that is showing", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      detail = {
        chat,
        messages: [message({ id: "u9", role: "user", content: "I use metric units" })],
        ownerName: null,
      };
      vi.mocked(post).mockResolvedValue({ note: note(), created: true });
      shown();
      const box = await screen.findByTestId("messages");
      let top = 0;
      Object.defineProperty(box, "scrollHeight", { value: 1000, configurable: true });
      Object.defineProperty(box, "clientHeight", { value: 400, configurable: true });
      Object.defineProperty(box, "scrollTop", {
        get: () => top,
        set: (value: number) => {
          top = value;
        },
        configurable: true,
      });
      top = 600;
      fireEvent.scroll(box);
      top = 300;
      fireEvent.scroll(box);
      expect(screen.getByRole("button", { name: "Jump to latest" })).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
      const toast = await screen.findByTestId("memory-toast");
      expect(toast.parentElement).toHaveClass("bottom-16");
      expect(toast.parentElement).not.toHaveClass("bottom-4");
    });

    it("says why words were not kept, and that the message was still sent", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      sent({
        command: "remember",
        refused: {
          message: "That looks like a password or key, so Workbench will not keep it",
          reason: "secret",
          looksLike: "a card number",
        },
      });
      shown();
      await say("Remember that my card is 4242 4242 4242 4242");
      const toast = await screen.findByTestId("memory-toast");
      expect(toast).toHaveAttribute("role", "alert");
      expect(toast).toHaveTextContent("It looks like a card number. Your message was still sent.");
    });

    it("lists the notes forget might mean, and deletes only the one picked", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      vi.mocked(del).mockResolvedValue(undefined);
      sent({
        command: "forget",
        words: "metric",
        candidates: [
          note({ id: "a", text: "I use metric units" }),
          note({ id: "b", text: "Metric only" }),
        ],
      });
      shown();
      await say("Forget my metric units");
      const toast = await screen.findByTestId("memory-toast");
      expect(toast).toHaveTextContent("Delete one of these notes?");
      expect(del).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Delete note: Metric only" }));
      await waitFor(() => expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/b"));
    });

    it("says nothing for a message that was no command, and a new toast replaces an old one", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      sent(undefined);
      shown();
      await say("Hello");
      await waitFor(() => expect(post).toHaveBeenCalledOnce());
      expect(screen.queryByTestId("memory-toast")).toBeNull();
      sent({ command: "remember", remembered: note({ text: "First" }), created: true });
      await say("Remember that First");
      await waitFor(() => expect(screen.getByTestId("memory-toast")).toHaveTextContent("First"));
      sent({ command: "remember", remembered: note({ id: "n2", text: "Second" }), created: true });
      await say("Remember that Second");
      await waitFor(() => expect(screen.getByTestId("memory-toast")).toHaveTextContent("Second"));
      expect(screen.getAllByTestId("memory-toast")).toHaveLength(1);
    });

    it("opens the Memory page from the toast's Manage", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      sent({ command: "remember", remembered: note(), created: true });
      const { onOpenMemory } = shown();
      await say("Remember that I use metric units");
      fireEvent.click(await screen.findByTestId("memory-toast-manage"));
      expect(onOpenMemory).toHaveBeenCalledOnce();
    });
  });

  describe("Remember this on a message", () => {
    beforeEach(() => {
      detail = {
        chat,
        messages: [
          message({ id: "u1", role: "user", content: "I use metric units" }),
          message({ id: "a1", content: "Metric it is." }),
        ],
        ownerName: null,
      };
    });

    it("keeps the message's words as a note from a button, and says so in the toast", async () => {
      vi.mocked(api).mockResolvedValue(sending());
      vi.mocked(post).mockResolvedValue({
        note: note({ id: "k", text: "I use metric units", source: "button" }),
        created: true,
      });
      shown();
      fireEvent.click(await screen.findByRole("button", { name: "Remember this message" }));
      expect(await screen.findByTestId("memory-toast")).toHaveTextContent(
        "Remembered: “I use metric units”",
      );
      expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
        text: "I use metric units",
        source: "button",
        chatId: "one",
        messageId: "u1",
      });
      // The answer's words can be kept as well.
      expect(screen.getByRole("button", { name: "Remember this answer" })).toBeInTheDocument();
    });

    it("is not offered where the owner has turned Memory off", async () => {
      vi.mocked(api).mockResolvedValue(
        held("install_off", "The owner of this Workbench has turned Memory off."),
      );
      shown();
      await screen.findByRole("button", { name: "Memory off" });
      expect(screen.queryByRole("button", { name: /^Remember this/ })).toBeNull();
    });

    it("says it keeps nothing, and how to turn memory on, where the person has turned it off", async () => {
      vi.mocked(api).mockResolvedValue(held("person_off", "Memory is off for you."));
      const { onOpenMemory } = shown();
      await screen.findByRole("button", { name: "Memory off" });
      fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
      expect(post).not.toHaveBeenCalled();
      expect(screen.getByTestId("remember-off")).toHaveTextContent(
        "Memory is off for you, so nothing was kept.",
      );
      fireEvent.click(screen.getByRole("button", { name: "Open Memory" }));
      expect(onOpenMemory).toHaveBeenCalledOnce();
    });

    it("is not offered in someone else's chat", async () => {
      vi.mocked(api).mockResolvedValue(owners());
      detail = { ...detail, chat: { ...chat, readOnly: true }, ownerName: "Bo" };
      shown();
      await screen.findByTestId("memory-chip");
      expect(screen.queryByRole("button", { name: /^Remember this/ })).toBeNull();
    });

    it("is offered before the notes have been read: the server says no, if it is no", async () => {
      vi.mocked(api).mockReturnValue(new Promise(() => undefined) as never);
      shown();
      expect(
        await screen.findByRole("button", { name: "Remember this message" }),
      ).toBeInTheDocument();
    });
  });
});
