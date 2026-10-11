import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { del, patch } from "../lib/api";
import type { Chat, ChatDetail, Me, Message } from "../lib/types";
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
// The settings panel's own lists fetch; this file is about the chat around it.
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update: vi.fn(),
    progress: null,
    error: loadError,
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
  />
);

function view() {
  return render(element());
}

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
  loadError = null;
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

describe("the conversation", () => {
  it("is a named region a keyboard can scroll, with room kept for its scrollbar", () => {
    view();
    const region = screen.getByRole("region", { name: "Conversation" });
    expect(region).toBe(screen.getByTestId("messages"));
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveAttribute("id", "conversation");
    expect(region.className).toContain("[scrollbar-gutter:stable_both-edges]");
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
