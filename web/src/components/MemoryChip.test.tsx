import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, patch, post } from "../lib/api";
import type { Chat, ChatMemoryView } from "../lib/types";
import { BLOCK, held, notSent, NOW, owners, sending, sentNote, turn } from "../test-memory";
import { MemoryChip, MemoryDrawer } from "./MemoryChip";

vi.mock("../lib/api", () => ({ api: vi.fn(), del: vi.fn(), patch: vi.fn(), post: vi.fn() }));
// The settings sheet's own lists fetch; this file is about the drawer beside it.
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));

const chat: Chat = {
  id: "c1",
  title: "Build a birdhouse",
  model: "qwen3-8b",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
};

function chip(view: ChatMemoryView | null, over: { failed?: boolean; open?: boolean } = {}) {
  const onToggle = vi.fn();
  render(
    <MemoryChip
      view={view}
      failed={over.failed ?? false}
      open={over.open ?? false}
      onToggle={onToggle}
    />,
  );
  return { onToggle, button: screen.getByTestId("memory-chip") };
}

function drawer(
  view: ChatMemoryView | null,
  over: Partial<Parameters<typeof MemoryDrawer>[0]> = {},
) {
  const calls = {
    onChat: vi.fn(),
    onReload: vi.fn().mockResolvedValue(undefined),
    onAdopt: vi.fn(),
    onManage: vi.fn(),
    onClose: vi.fn(),
  };
  const shown = render(<MemoryDrawer chatId="c1" view={view} error={null} {...calls} {...over} />);
  return { ...shown, ...calls };
}

const status = () => screen.getByTestId("memory-status");

beforeEach(() => {
  vi.mocked(patch).mockReset();
  vi.mocked(post).mockReset();
  window.matchMedia = undefined as never;
});

describe("the chip", () => {
  it("is a button named by its words, with a glyph that is only a glyph", () => {
    const { button } = chip(sending());
    expect(button).toHaveAccessibleName("Memory: 1 note sent");
    expect(button).toHaveClass("icon-btn");
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAttribute("aria-controls", "memory-drawer");
  });

  it("says whether its drawer is open, and opens it on a press", () => {
    const { button, onToggle } = chip(sending(), { open: true });
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("says plain Memory before it has read anything, and says so when it could not", () => {
    const first = chip(null);
    expect(first.button).toHaveAccessibleName("Memory");
    expect(first.button).not.toHaveClass("text-warn");
  });

  it("warns, with a different glyph and its words, when it could not read", () => {
    const { button } = chip(null, { failed: true });
    expect(button).toHaveAccessibleName("Memory: not available");
    expect(button).toHaveClass("text-warn");
    expect(button.querySelector("svg")).toHaveClass("lucide-triangle-alert");
  });

  it("marks a chat that is sending its notes, quietly: the glyph is blue and the words are plain", () => {
    const { button } = chip(sending());
    expect(button).toHaveClass("text-fg");
    expect(button.querySelector("svg")).toHaveClass("text-accent");
  });

  it("warns for a model that was held back, in the warning colour and with the warning glyph", () => {
    const { button } = chip(held("outside_model", "Your notes were not sent."));
    expect(button).toHaveAccessibleName("Memory not sent: outside model");
    expect(button).toHaveClass("text-warn");
    expect(button.querySelector("svg")).toHaveClass("lucide-triangle-alert");
  });

  it("keeps the glyph and the count for a phone, and the words for everyone else", () => {
    const { button } = chip(sending({ counts: { notes: 3, sent: 3, notSent: 0 } }));
    const [words, count] = Array.from(button.querySelectorAll("span"));
    expect(words).toHaveClass("max-sm:hidden");
    expect(words).toHaveTextContent("Memory: 3 notes sent");
    expect(count).toHaveClass("sm:hidden");
    expect(count).toHaveTextContent("3");
    // Neither is read: the button's own name is.
    expect(words).toHaveAttribute("aria-hidden", "true");
    expect(count).toHaveAttribute("aria-hidden", "true");
  });

  it("has no count for a chat that sends nothing", () => {
    const { button } = chip(held("chat_off", "Memory is off for this chat."));
    expect(button).toHaveAccessibleName("Memory off in this chat");
    expect(button.querySelectorAll("span")).toHaveLength(1);
  });

  it("says what the owner reading someone's chat may know: how many, never which", () => {
    const { button } = chip(owners());
    expect(button).toHaveAccessibleName("Memory: 2 notes used");
  });
});

describe("the drawer opening", () => {
  it("is a named section that is a panel above the chat, reads again when it opens, and says nothing it has not read", () => {
    const { onReload } = drawer(null);
    const panel = screen.getByRole("region", { name: "Memory in this chat" });
    expect(panel).toHaveClass("sheet");
    expect(panel).toHaveAttribute("id", "memory-drawer");
    expect(panel).not.toHaveAttribute("role", "dialog");
    expect(
      screen.getByRole("heading", { level: 2, name: "Memory in this chat" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Reading this chat's notes…");
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("says why it could not read, in the server's words, and tries again", () => {
    const { onReload } = drawer(null, { error: "Workbench answered 502." });
    const failure = screen.getByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Could not read this chat's notes");
    expect(failure).toHaveTextContent("Workbench answered 502.");
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(within(failure).getByRole("button", { name: "Try again" }));
    expect(onReload).toHaveBeenCalledTimes(2);
  });

  it("closes with its button and with Escape", () => {
    const { onClose } = drawer(sending());
    fireEvent.click(screen.getByRole("button", { name: "Close memory" }));
    fireEvent.keyDown(screen.getByRole("region", { name: "Memory in this chat" }), {
      key: "Escape",
    });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("opens the Memory page from Manage notes", () => {
    const { onManage } = drawer(sending());
    fireEvent.click(screen.getByRole("button", { name: "Manage notes" }));
    expect(onManage).toHaveBeenCalledOnce();
  });
});

describe("what was sent", () => {
  it("lists each note with its kind, and marks the ones that are always used", () => {
    drawer(
      sending({
        counts: { notes: 2, sent: 2, notSent: 0 },
        sent: [
          sentNote({ id: "a", text: "I live in Lisbon", pinned: true }),
          sentNote({ id: "b", text: "Keep answers short", kind: "instruction" }),
          sentNote({ id: "c", text: "I am building a birdhouse", kind: "project" }),
        ],
      }),
    );
    const list = screen.getByTestId("memory-sent");
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("I live in Lisbon");
    expect(rows[0]).toHaveTextContent("Fact");
    expect(rows[0]).toHaveTextContent("Always used");
    expect(rows[1]).toHaveTextContent("Instruction");
    expect(rows[1]).toHaveTextContent("Always used");
    expect(rows[2]).toHaveTextContent("Project");
    expect(rows[2]).not.toHaveTextContent("Always used");
    expect(within(rows[0]!).getByText("I live in Lisbon")).toHaveAttribute("dir", "auto");
    expect(
      screen.getByRole("heading", { level: 3, name: "Sent to the model in every turn" }),
    ).toBeInTheDocument();
  });

  it("says how much of the model's room they take", () => {
    drawer(sending({ tokens: 162, budget: 655 }));
    expect(
      screen.getByText(/Using 162 of 655 tokens of this model's memory room/),
    ).toBeInTheDocument();
  });

  it("says when the chat took its notes, and that they stay the same", () => {
    drawer(sending({ frozenAt: NOW }));
    expect(status()).toHaveTextContent(
      "Sent to qwen3-8b with every message in this chat. This chat took these notes",
    );
    expect(status()).toHaveTextContent("so they stay the same until you choose Refresh memory");
    expect(status()).not.toHaveClass("notice");
  });

  it("says they will go with the next message when the chat has not sent any yet", () => {
    drawer(sending({ frozen: false, frozenAt: null }));
    expect(status()).toHaveTextContent("These notes will go to qwen3-8b with your next message.");
  });

  it("lists what did not fit, with the reason the server gave", () => {
    drawer(
      sending({
        counts: { notes: 2, sent: 1, notSent: 1 },
        notSent: [
          notSent({
            id: "x",
            text: "An older note",
            reasonText: "Did not fit this model's memory budget (655 tokens).",
          }),
        ],
      }),
    );
    const list = screen.getByTestId("memory-not-sent");
    expect(within(list).getByText("An older note")).toBeInTheDocument();
    expect(list).toHaveTextContent("Did not fit this model's memory budget (655 tokens).");
    expect(screen.getByRole("heading", { level: 3, name: "Not sent" })).toBeInTheDocument();
  });

  it("lists a note added since, and says to refresh", () => {
    drawer(
      sending({
        counts: { notes: 2, sent: 1, notSent: 1 },
        notSent: [
          notSent({
            id: "y",
            reason: "added_since",
            text: "A new note",
            reasonText: "Added after this chat started. Refresh memory to include it.",
          }),
        ],
      }),
    );
    expect(screen.getByTestId("memory-not-sent")).toHaveTextContent(
      "Added after this chat started. Refresh memory to include it.",
    );
  });

  it("shows neither list when there is nothing to show", () => {
    drawer(held("no_notes", "You have no notes yet."));
    expect(screen.queryByTestId("memory-sent")).toBeNull();
    expect(screen.queryByTestId("memory-not-sent")).toBeNull();
    expect(screen.queryByTestId("memory-exact-details")).toBeNull();
  });
});

describe("the exact text", () => {
  it("is what the model was given, byte for byte, behind a row that opens", () => {
    drawer(sending());
    const details = screen.getByTestId("memory-exact-details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("The exact text the model was given")).toHaveClass(
      "disclosure",
    );
    const text = screen.getByTestId("memory-exact");
    expect(text.textContent).toBe(BLOCK);
    expect(text).toHaveAttribute("dir", "auto");
    expect(text).toHaveClass("reasoning-body", "whitespace-pre-wrap");
    expect(text).toHaveAccessibleName("Text sent to the model");
  });

  it("is a keyboard stop only while it scrolls", () => {
    const { unmount } = drawer(sending());
    expect(screen.getByTestId("memory-exact")).not.toHaveAttribute("tabindex");
    unmount();
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(900);
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(320);
    drawer(sending());
    expect(screen.getByTestId("memory-exact")).toHaveAttribute("tabindex", "0");
    vi.restoreAllMocks();
  });

  it("is brought into view when it is opened low in a panel that scrolls", () => {
    drawer(sending());
    const details = screen.getByTestId("memory-exact-details") as HTMLDetailsElement;
    const into = vi.fn();
    details.scrollIntoView = into;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(into).toHaveBeenCalledWith({ block: "nearest" });
    into.mockClear();
    details.open = false;
    fireEvent(details, new Event("toggle"));
    expect(into).not.toHaveBeenCalled();
  });
});

describe("every reason nothing was sent, in the server's words", () => {
  it.each([
    ["install_off", "The owner of this Workbench has turned Memory off.", false],
    [
      "person_off",
      "Memory is off for you. Turn it on in Memory to share notes with models.",
      false,
    ],
    ["chat_off", "Memory is off for this chat.", false],
    ["no_model", "Choose a model first, and notes can be sent to it.", false],
    [
      "model_unknown",
      "Workbench could not ask Eugene about this model (Eugene did not answer), so no notes were sent.",
      true,
    ],
    [
      "window_too_small",
      "Memory is off for this model: its window (2,048 tokens) is too small to share notes.",
      true,
    ],
    ["no_notes", "You have no notes yet.", false],
  ] as const)("%s", (code, reason, warns) => {
    drawer(held(code, reason));
    expect(status()).toHaveTextContent(reason);
    expect(status()).toHaveClass("notice");
    if (warns) expect(status()).toHaveClass("notice--warn");
    else expect(status()).not.toHaveClass("notice--warn");
    expect(status().querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // Nothing was sent, so there is nothing to list and no copy to refresh.
    expect(screen.queryByTestId("memory-sent")).toBeNull();
    expect(screen.getByTestId("memory-refresh")).toBeDisabled();
  });

  it("gives the server's hint after its reason", () => {
    drawer(held("person_off", "Memory is off for you.", { hint: "Turn it on in Memory." }));
    expect(status()).toHaveTextContent("Memory is off for you.Turn it on in Memory.");
  });

  it("leads a person who turned memory off to the page that turns it on, and no switch is offered", () => {
    const { onManage } = drawer(held("person_off", "Memory is off for you."));
    fireEvent.click(screen.getByRole("button", { name: "Open Memory to turn it on" }));
    expect(onManage).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("memory-switch")).toBeNull();
  });

  it("leads a person with no notes to write some", () => {
    const { onManage } = drawer(held("no_notes", "You have no notes yet."));
    fireEvent.click(screen.getByRole("button", { name: "Open Memory to add notes" }));
    expect(onManage).toHaveBeenCalledOnce();
  });

  it("offers the owner's off no button and no switch: it is the owner's", () => {
    drawer(held("install_off", "The owner of this Workbench has turned Memory off."));
    expect(screen.queryByRole("button", { name: /Open Memory/ })).toBeNull();
    expect(screen.queryByTestId("memory-switch")).toBeNull();
  });
});

describe("a model that runs outside the person's machines", () => {
  const outside = held(
    "outside_model",
    "Your notes were not sent: this chat uses OpenRouter, which runs outside your machines.",
    {
      hint: "Choose Send for this chat to send them anyway.",
      model: { id: "gpt-5", window: 128000, locality: "external", runsOn: "OpenRouter" },
      outside: {
        choice: "ask",
        setBy: "default",
        canSendForThisChat: true,
        sendForThisChat: false,
      },
    },
  );

  it("says so once, in a warning, with the reason and the hint", () => {
    drawer(outside);
    expect(status()).toHaveClass("notice--warn");
    expect(status()).toHaveTextContent("Your notes were not sent: this chat uses OpenRouter");
    expect(status()).toHaveTextContent("Choose Send for this chat to send them anyway.");
    // The sentence is the server's, and is not said twice.
    expect(status().textContent!.match(/Your notes were not sent/g)).toHaveLength(1);
  });

  it("sends for this chat on a press, and takes the chat's new settings and the view again", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: { memoryExternal: true } });
    const { onChat, onReload } = drawer(outside);
    onReload.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Send for this chat" }));
    await waitFor(() => expect(onChat).toHaveBeenCalledOnce());
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { memoryExternal: true },
    });
    expect(onChat).toHaveBeenCalledWith({ ...chat, settings: { memoryExternal: true } });
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("offers no way round the owner's or the person's own never", () => {
    drawer({
      ...outside,
      hint: "The owner of this Workbench has set notes never to go to such models.",
      outside: {
        choice: "never",
        setBy: "owner",
        canSendForThisChat: false,
        sendForThisChat: false,
      },
    });
    expect(screen.queryByRole("button", { name: "Send for this chat" })).toBeNull();
    expect(status()).toHaveTextContent("has set notes never to go to such models");
  });

  it("says when it is sending because the person chose to, and can stop", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: {} });
    const { onChat } = drawer(
      sending({
        model: { id: "gpt-5", window: 128000, locality: "external", runsOn: "OpenRouter" },
        outside: {
          choice: "ask",
          setBy: "default",
          canSendForThisChat: true,
          sendForThisChat: true,
        },
        frozen: false,
        frozenAt: null,
      }),
    );
    expect(status()).toHaveClass("notice--warn");
    expect(status()).toHaveTextContent(
      "Your notes go to OpenRouter, which runs outside your machines, because you chose Send for this chat.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop sending" }));
    await waitFor(() => expect(onChat).toHaveBeenCalledOnce());
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { memoryExternal: null },
    });
  });

  it.each([
    ["owner", "because the owner of this Workbench has set notes to always go to such models"],
    ["you", "because you have set notes to always go to such models"],
    ["default", "because this Workbench is set notes to always go to such models"],
  ] as const)("says whose word 'always' is: %s", (setBy, words) => {
    drawer(
      sending({
        model: { id: "gpt-5", window: 128000, locality: "external", runsOn: null },
        outside: { choice: "always", setBy, canSendForThisChat: false, sendForThisChat: false },
      }),
    );
    expect(status()).toHaveTextContent(
      "Your notes go to another service, which runs outside your machines",
    );
    expect(status()).toHaveTextContent(words);
    expect(screen.queryByRole("button", { name: "Stop sending" })).toBeNull();
  });
});

describe("the chat's own switch", () => {
  it("shows what the chat does, and a name a screen reader says with its help", () => {
    drawer(sending());
    const toggle = screen.getByRole("switch", { name: "Memory in this chat" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAccessibleDescription(/Turn it off to send no notes in this chat/);
  });

  it("turns memory off for the chat at once, and the chat's setting follows", async () => {
    let finish: (chat: Chat) => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { onChat, onReload } = drawer(sending());
    onReload.mockClear();
    const toggle = screen.getByRole("switch", { name: "Memory in this chat" });
    fireEvent.click(toggle);
    // It shows the choice while the server catches up, and cannot be pressed twice.
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", { settings: { memory: "off" } });
    finish({ ...chat, settings: { memory: "off" } });
    await waitFor(() =>
      expect(onChat).toHaveBeenCalledWith({ ...chat, settings: { memory: "off" } }),
    );
    await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
  });

  it("turns it back on by clearing the setting, so the chat follows the person again", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: {} });
    drawer(held("chat_off", "Memory is off for this chat.", { chatSwitch: "off" }));
    const toggle = screen.getByRole("switch", { name: "Memory in this chat" });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", { settings: { memory: null } });
    await waitFor(() => expect(toggle).toBeEnabled());
  });

  it("keeps the keyboard where it was: held while the server answers, and focused again after", async () => {
    let finish: (chat: Chat) => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    drawer(sending());
    const toggle = screen.getByRole("switch", { name: "Memory in this chat" });
    toggle.focus();
    fireEvent.click(toggle);
    expect(toggle).toBeDisabled();
    // What a browser does with the control that has focus when it is disabled:
    // focus is on the page. (jsdom keeps it where it was, so the page says so.)
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(toggle, "focus");
    finish({ ...chat, settings: { memory: "off" } });
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("puts the switch back and says why when the server refused", async () => {
    vi.mocked(patch).mockRejectedValue(new Error("This chat is gone."));
    const { onChat } = drawer(sending());
    const toggle = screen.getByRole("switch", { name: "Memory in this chat" });
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent("This chat is gone.");
    expect(toggle).toBeChecked();
    expect(toggle).toBeEnabled();
    expect(onChat).not.toHaveBeenCalled();
  });
});

describe("Refresh memory and the notes that changed", () => {
  it("says the notes changed, and makes Refresh memory the main button", () => {
    drawer(sending({ changed: true }));
    expect(screen.getByTestId("memory-changed")).toHaveTextContent(
      "Notes changed since this chat started",
    );
    expect(screen.getByTestId("memory-changed")).toHaveClass("notice--warn");
    expect(screen.getByRole("button", { name: "Refresh memory" })).toHaveClass("btn-primary");
  });

  it("says nothing of a change that did not happen, and keeps the button quiet", () => {
    drawer(sending({ changed: false }));
    expect(screen.queryByTestId("memory-changed")).toBeNull();
    expect(screen.getByRole("button", { name: "Refresh memory" })).not.toHaveClass("btn-primary");
  });

  it("takes the notes again from the server, shows what it answers, and says so", async () => {
    const fresh = sending({ changed: false, counts: { notes: 2, sent: 2, notSent: 0 } });
    vi.mocked(post).mockResolvedValue(fresh);
    const { onAdopt } = drawer(sending({ changed: true }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh memory" }));
    await waitFor(() => expect(onAdopt).toHaveBeenCalledWith(fresh));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/chats/c1/memory/refresh");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Refreshed. Your next message sends your notes as they are now.",
    );
  });

  it("keeps the keyboard on Refresh memory while it works", async () => {
    let finish: (view: ChatMemoryView) => void = () => undefined;
    vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    drawer(sending({ changed: true }));
    const refresh = screen.getByRole("button", { name: "Refresh memory" });
    refresh.focus();
    fireEvent.click(refresh);
    expect(refresh).toBeDisabled();
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(refresh, "focus");
    finish(sending());
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("says why it could not, and does not say it refreshed", async () => {
    vi.mocked(post).mockRejectedValue(new Error("Workbench answered 500."));
    const { onAdopt } = drawer(sending({ changed: true }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh memory" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 500.");
    expect(onAdopt).not.toHaveBeenCalled();
    expect(screen.queryByText(/Refreshed/)).toBeNull();
  });

  it("is there to press once a copy exists, even when the chat sends nothing now", () => {
    drawer(held("chat_off", "Memory is off for this chat.", { frozen: true }));
    expect(screen.getByTestId("memory-refresh")).toBeEnabled();
  });
});

describe("the owner reading someone else's chat", () => {
  it("is told how many notes went, and that what they said is private; nothing to change", () => {
    drawer(owners({ counts: { sent: 2, notSent: 1 } }));
    expect(status()).toHaveTextContent(
      "2 notes went to the model in this chat, and 1 note did not fit.",
    );
    expect(status()).toHaveTextContent("You can see how many notes it used, not what they said");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByTestId("memory-refresh")).toBeNull();
    expect(screen.queryByTestId("memory-manage")).toBeNull();
    expect(screen.queryByTestId("memory-sent")).toBeNull();
    expect(screen.getByRole("button", { name: "Close memory" })).toBeInTheDocument();
  });
});

describe("over the whole window", () => {
  function overlay() {
    window.matchMedia = ((query: string) => ({
      matches: query.includes("max-width"),
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as never;
  }

  it("is a dialog, takes focus, and keeps Tab inside", () => {
    overlay();
    drawer(sending());
    const dialog = screen.getByRole("dialog", { name: "Memory in this chat" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("heading", { level: 2, name: "Memory in this chat" })).toHaveFocus();
    const buttons = within(dialog).getAllByRole("button");
    const last = buttons[buttons.length - 1]!;
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).not.toBe(last);
    expect(dialog.contains(document.activeElement)).toBe(true);
    const first = dialog.querySelector<HTMLElement>("input, button, summary, [tabindex='0']")!;
    first.focus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("gives focus back to the chip that opened it", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = drawer(sending());
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});

describe("this turn", () => {
  const withTurn = (over: Parameters<typeof sending>[0] = {}) =>
    sending({ relevantBudget: 3277, searchTool: true, turn: turn(), turns: ["a1"], ...over });

  it("lists the notes the turn was sent, how many tokens of the budget, and how they were found", () => {
    drawer(withTurn());
    const section = screen.getByRole("region", { name: "This turn" });
    expect(within(section).getByTestId("memory-turn-summary")).toHaveTextContent(
      "1 note chosen. Using 90 of 3,277 tokens.",
    );
    expect(within(section).getByTestId("memory-turn-how")).toHaveTextContent(
      "Chosen from 12 notes by words.",
    );
    const row = within(section).getByTestId("memory-turn-notes");
    expect(row).toHaveTextContent("Cedar is the wood for the birdhouse");
    expect(row).toHaveTextContent("Project");
    expect(row).toHaveTextContent("woodwork");
    expect(row).toHaveTextContent("Found by words.");
    expect(within(section).getByTestId("memory-turn-budget")).toHaveTextContent(
      "up to 3,277 tokens a turn. This model can search your notes.",
    );
  });

  it("says which part of a long note was sent, and what did not fit", () => {
    drawer(
      withTurn({
        searchTool: false,
        turn: turn({
          how: "meaning and words",
          didNotFit: 3,
          notes: [{ ...turn().notes[0]!, via: "both", passages: [1, 4] }],
        }),
      }),
    );
    const section = screen.getByRole("region", { name: "This turn" });
    expect(section).toHaveTextContent("3 more did not fit.");
    expect(section).toHaveTextContent("Chosen from 12 notes by meaning and words.");
    expect(section).toHaveTextContent("Found by meaning and words. 2 parts of its details sent.");
    expect(section).toHaveTextContent("This model cannot search your notes.");
  });

  it("gives the reason when every note that fits was used, or words only", () => {
    drawer(withTurn({ turn: turn({ how: "all", why: null, notes: [] }) }));
    expect(screen.getByTestId("memory-turn-summary")).toHaveTextContent(
      "No notes were chosen for this turn.",
    );
    expect(screen.getByTestId("memory-turn-how")).toHaveTextContent(
      "Every note that fits was used.",
    );
  });

  it("labels the pinned block as sent in every turn, and says when a pinned note's details went whole", () => {
    drawer(withTurn({ sent: [sentNote({ body: "Step one." })] }));
    expect(
      screen.getByRole("heading", { level: 3, name: "Sent to the model in every turn" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("memory-note-body-sent")).toHaveTextContent(
      "Its details went whole: 9 characters.",
    );
  });

  it("says why a pinned note's details go by relevance instead", () => {
    drawer(
      withTurn({
        counts: { notes: 2, sent: 1, notSent: 1 },
        notSent: [
          notSent({
            reason: "body_by_relevance",
            reasonText: "Its details are too long to send in every turn.",
          }),
        ],
      }),
    );
    expect(screen.getByTestId("memory-not-sent")).toHaveTextContent(
      "Its details are too long to send in every turn.",
    );
  });

  it("lets the person pick another turn when there are several, and reads it from the server", async () => {
    const earlier = turn({
      messageId: "a0",
      notes: [{ ...turn().notes[0]!, id: "n9", text: "Plans a trip to Oslo", tags: [] }],
    });
    vi.mocked(api).mockResolvedValue(sending({ turn: earlier }) as never);
    drawer(withTurn({ turns: ["a0", "a1"] }));
    const pick = screen.getByRole("combobox", { name: "Turn" });
    expect(pick).toHaveValue("a1");
    expect(
      within(pick)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Answer 1", "Answer 2 (latest)"]);
    fireEvent.change(pick, { target: { value: "a0" } });
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/chats/c1/memory?message=a0"));
    expect(await screen.findByText("Plans a trip to Oslo")).toBeInTheDocument();
    expect(screen.queryByText("Cedar is the wood for the birdhouse")).toBeNull();
    // Back to the latest needs no read.
    vi.mocked(api).mockClear();
    fireEvent.change(screen.getByRole("combobox", { name: "Turn" }), { target: { value: "a1" } });
    expect(await screen.findByText("Cedar is the wood for the birdhouse")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("offers no picker for one turn, and no section for a chat with no answers yet", () => {
    drawer(withTurn());
    expect(screen.queryByRole("combobox", { name: "Turn" })).toBeNull();
    document.body.innerHTML = "";
    drawer(sending());
    expect(screen.queryByTestId("memory-this-turn")).toBeNull();
  });

  it("says why a turn could not be read", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    drawer(withTurn({ turns: ["a0", "a1"] }));
    fireEvent.change(screen.getByRole("combobox", { name: "Turn" }), { target: { value: "a0" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
  });

  it("tells the owner reading someone else's chat only how many notes a turn was sent", () => {
    drawer(owners({ turn: { messageId: "a1", notes: 2, tokens: 90, budget: 3277 } }));
    expect(screen.getByTestId("memory-turn-counts")).toHaveTextContent(
      "The latest answer was sent 2 notes chosen for it. Using 90 of 3,277 tokens.",
    );
    expect(screen.queryByTestId("memory-turn-notes")).toBeNull();
  });
});
