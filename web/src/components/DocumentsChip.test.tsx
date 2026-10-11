import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { patch } from "../lib/api";
import type { Chat, ChatDocumentsView } from "../lib/types";
import { chatDocuments, held, off, owners, saved, scanned, queued } from "../test-saved";
import { DocumentsChip, DocumentsDrawer, DocumentsHint } from "./DocumentsChip";

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

function chip(view: ChatDocumentsView | null, over: { failed?: boolean; open?: boolean } = {}) {
  const onToggle = vi.fn();
  render(
    <DocumentsChip
      view={view}
      failed={over.failed ?? false}
      open={over.open ?? false}
      onToggle={onToggle}
    />,
  );
  return { onToggle, button: screen.getByTestId("documents-chip") };
}

function drawer(
  view: ChatDocumentsView | null,
  over: Partial<Parameters<typeof DocumentsDrawer>[0]> = {},
) {
  const calls = {
    onChat: vi.fn(),
    onReload: vi.fn().mockResolvedValue(undefined),
    onManage: vi.fn(),
    onClose: vi.fn(),
  };
  const shown = render(
    <DocumentsDrawer chatId="c1" view={view} error={null} {...calls} {...over} />,
  );
  return { ...shown, ...calls };
}

const status = () => screen.getByTestId("documents-status");

beforeEach(() => {
  vi.mocked(patch).mockReset();
  window.matchMedia = undefined as never;
});

describe("the chip", () => {
  it("is a button named by its words, with a glyph that is only a glyph", () => {
    const { button } = chip(chatDocuments());
    expect(button).toHaveAccessibleName("Documents (3)");
    expect(button).toHaveClass("icon-btn");
    expect(button.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(button).toHaveAttribute("aria-controls", "documents-drawer");
  });

  it("says whether its drawer is open, and opens it on a press", () => {
    const { button, onToggle } = chip(chatDocuments(), { open: true });
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("says plain Documents before it has read anything, and says so when it could not", () => {
    const first = chip(null);
    expect(first.button).toHaveAccessibleName("Documents");
    expect(first.button).not.toHaveClass("text-warn");
  });

  it("warns, with a different glyph and its words, when it could not read", () => {
    const { button } = chip(null, { failed: true });
    expect(button).toHaveAccessibleName("Documents: not available");
    expect(button).toHaveClass("text-warn");
    expect(button.querySelector("svg")).toHaveClass("lucide-triangle-alert");
  });

  it("marks a chat that is searching, quietly: the glyph is blue and the words are plain", () => {
    const { button } = chip(chatDocuments());
    expect(button).toHaveClass("text-fg");
    expect(button.querySelector("svg")).toHaveClass("text-accent");
  });

  it("warns for documents that were held back, in the warning colour and with the warning glyph", () => {
    const { button } = chip(held("outside_model", "Your documents were not sent."));
    expect(button).toHaveAccessibleName("Documents not sent: outside model");
    expect(button).toHaveClass("text-warn");
    expect(button.querySelector("svg")).toHaveClass("lucide-triangle-alert");
  });

  it("keeps the glyph and the count for a window too narrow for the words, and the words for the rest", () => {
    const { button } = chip(chatDocuments());
    const [words, count] = Array.from(button.querySelectorAll("span"));
    expect(words).toHaveClass("max-xl:hidden");
    expect(words).toHaveTextContent("Documents (3)");
    expect(count).toHaveClass("xl:hidden");
    expect(count).toHaveTextContent("3");
    // Neither is read: the button's own name is.
    expect(words).toHaveAttribute("aria-hidden", "true");
    expect(count).toHaveAttribute("aria-hidden", "true");
  });

  it("says Off beside the glyph for a chat with Documents off", () => {
    const { button } = chip(off());
    expect(button).toHaveAccessibleName("Documents off");
    const [words, short] = Array.from(button.querySelectorAll("span"));
    expect(words).toHaveTextContent("Documents off");
    expect(short).toHaveTextContent("Off");
  });

  it("has no count for a chat that sends nothing and has nothing to say in a few letters", () => {
    const { button } = chip(held("no_model", "Choose a model first."));
    expect(button).toHaveAccessibleName("Documents: choose a model");
    expect(button.querySelectorAll("span")).toHaveLength(1);
  });

  it("says what the owner reading someone's chat may know: whether Documents was on, never what is in them", () => {
    expect(chip(owners()).button).toHaveAccessibleName("Documents on");
  });
});

describe("the drawer opening", () => {
  it("is a named section that is a panel above the chat, reads again when it opens, and says nothing it has not read", () => {
    const { onReload } = drawer(null);
    const panel = screen.getByRole("region", { name: "Documents in this chat" });
    expect(panel).toHaveClass("sheet");
    expect(panel).toHaveAttribute("id", "documents-drawer");
    expect(panel).not.toHaveAttribute("role", "dialog");
    expect(
      screen.getByRole("heading", { level: 2, name: "Documents in this chat" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Reading this chat's documents…");
    expect(onReload).toHaveBeenCalledOnce();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("says why it could not read, in the server's words, and tries again", () => {
    const { onReload } = drawer(null, { error: "Workbench answered 502." });
    const failure = screen.getByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Could not read this chat's documents");
    expect(failure).toHaveTextContent("Workbench answered 502.");
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(within(failure).getByRole("button", { name: "Try again" }));
    expect(onReload).toHaveBeenCalledTimes(2);
  });

  it("closes with its button and with Escape", () => {
    const { onClose } = drawer(chatDocuments());
    fireEvent.click(screen.getByRole("button", { name: "Close documents" }));
    fireEvent.keyDown(screen.getByRole("region", { name: "Documents in this chat" }), {
      key: "Escape",
    });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("opens the Documents screen from Manage documents", () => {
    const { onManage } = drawer(chatDocuments());
    fireEvent.click(screen.getByRole("button", { name: "Manage documents" }));
    expect(onManage).toHaveBeenCalledOnce();
  });
});

describe("what the next message does", () => {
  it("says it searches the person's documents and sends the passages that match, and where to see which", () => {
    drawer(chatDocuments());
    expect(status()).toHaveTextContent(
      "Each question you send is searched for in your documents, and the passages that match go to qwen3-8b. Sources under each answer says which.",
    );
    expect(status()).not.toHaveClass("notice");
    // This chat has no files of its own that it searches, and does not say it has.
    expect(status()).not.toHaveTextContent("searched too");
  });

  it("says this chat's own files are searched too, when it has some", () => {
    drawer(
      chatDocuments({
        counts: {
          library: { documents: 3, passages: 9, waiting: 0 },
          chat: { documents: 1, passages: 4, waiting: 0 },
        },
      }),
    );
    expect(status()).toHaveTextContent(
      "The files of this chat that you chose to search are searched too.",
    );
  });

  it("says there is nothing to search yet, and leads to the screen that adds documents", () => {
    const none = {
      library: { documents: 0, passages: 0, waiting: 0 },
      chat: { documents: 0, passages: 0, waiting: 0 },
    };
    const { onManage } = drawer(chatDocuments({ counts: none }));
    expect(status()).toHaveTextContent("You have no documents to search yet.");
    fireEvent.click(screen.getByRole("button", { name: "Open Documents to add some" }));
    expect(onManage).toHaveBeenCalledOnce();
  });

  it("says the documents are still being read, and offers nothing to add", () => {
    drawer(
      chatDocuments({
        counts: {
          library: { documents: 0, passages: 0, waiting: 2 },
          chat: { documents: 0, passages: 0, waiting: 0 },
        },
      }),
    );
    expect(status()).toHaveTextContent(
      "Your documents are still being read, so none can be searched yet. They are searched as soon as they are ready.",
    );
    expect(screen.queryByRole("button", { name: /Open Documents/ })).toBeNull();
  });

  it("says only this chat's own files are searched when Documents are off for the rest", () => {
    drawer(
      off({
        sending: true,
        searching: { library: false, chat: true },
        reason: null,
        reasonCode: null,
        counts: {
          library: { documents: 3, passages: 9, waiting: 0 },
          chat: { documents: 1, passages: 4, waiting: 0 },
        },
      }),
    );
    expect(status()).toHaveTextContent(
      "The files of this chat that you chose to search are searched for each question, and the passages that match go to qwen3-8b.",
    );
  });
});

describe("every reason nothing was sent, in the server's words", () => {
  it.each([
    ["install_off", "The owner of this Workbench has turned off documents.", false],
    ["chat_off", "Documents are off for this chat.", false],
    ["no_model", "Choose a model first, and documents can be sent to it.", false],
    [
      "model_unknown",
      "Workbench could not ask Eugene about this model (Eugene did not answer), so no saved documents were sent.",
      true,
    ],
    [
      "window_too_small",
      "Documents are off for this model: its window (2,048 tokens) is too small.",
      true,
    ],
  ] as const)("%s", (code, reason, warns) => {
    drawer(held(code, reason, { switch: code === "chat_off" ? "off" : "on" }));
    expect(status()).toHaveTextContent(reason);
    expect(status()).toHaveClass("notice");
    if (warns) expect(status()).toHaveClass("notice--warn");
    else expect(status()).not.toHaveClass("notice--warn");
    expect(status().querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("gives the server's hint after its reason", () => {
    drawer(
      held("window_too_small", "Documents are off for this model.", {
        hint: "Pick a model with a larger window.",
      }),
    );
    expect(status()).toHaveTextContent(
      "Documents are off for this model.Pick a model with a larger window.",
    );
  });

  it("offers the owner's off no switch and no counts: it is the owner's", () => {
    drawer(held("install_off", "The owner of this Workbench has turned off documents."));
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByTestId("documents-counts")).toBeNull();
    expect(screen.getByRole("button", { name: "Close documents" })).toBeInTheDocument();
  });
});

describe("a model that runs outside the person's machines", () => {
  const outside = held(
    "outside_model",
    "Your documents were not sent: this chat uses OpenRouter, which runs outside your machines.",
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
    expect(status()).toHaveTextContent("Your documents were not sent: this chat uses OpenRouter");
    expect(status()).toHaveTextContent("Choose Send for this chat to send them anyway.");
    expect(status().textContent!.match(/Your documents were not sent/g)).toHaveLength(1);
  });

  it("sends for this chat on a press, and takes the chat's new settings and the view again", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: { documentsExternal: true } });
    const { onChat, onReload } = drawer(outside);
    onReload.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "Send for this chat" }));
    await waitFor(() => expect(onChat).toHaveBeenCalledOnce());
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documentsExternal: true },
    });
    expect(onChat).toHaveBeenCalledWith({ ...chat, settings: { documentsExternal: true } });
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("offers no way round the owner's or the person's own never", () => {
    drawer({
      ...outside,
      hint: "The owner of this Workbench has set documents never to go to such models.",
      outside: {
        choice: "never",
        setBy: "owner",
        canSendForThisChat: false,
        sendForThisChat: false,
      },
    });
    expect(screen.queryByRole("button", { name: "Send for this chat" })).toBeNull();
    expect(status()).toHaveTextContent("has set documents never to go to such models");
  });

  it("does not say the model is outside when Eugene cannot place it", () => {
    drawer(
      held(
        "outside_model",
        "Your documents were not sent: Eugene cannot tell whether this chat's model runs on your machines.",
        {
          model: { id: "m", window: 4096, locality: "unknown", runsOn: null },
          outside: outside.outside,
        },
      ),
    );
    expect(status()).toHaveTextContent(
      "Eugene cannot tell whether this chat's model runs on your machines",
    );
    expect(status()).not.toHaveTextContent("runs outside your machines");
  });

  it("says when it is sending because the person chose to, and can stop", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: {} });
    const { onChat } = drawer(
      chatDocuments({
        model: { id: "gpt-5", window: 128000, locality: "external", runsOn: "OpenRouter" },
        outside: {
          choice: "ask",
          setBy: "default",
          canSendForThisChat: true,
          sendForThisChat: true,
        },
      }),
    );
    expect(status()).toHaveClass("notice--warn");
    expect(status()).toHaveTextContent(
      "The passages of your documents that match a question go to OpenRouter, which runs outside your machines, because you chose Send for this chat.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop sending" }));
    await waitFor(() => expect(onChat).toHaveBeenCalledOnce());
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documentsExternal: null },
    });
  });

  it.each([
    ["owner", "because the owner of this Workbench has set documents to always go to such models"],
    ["you", "because you have set documents to always go to such models"],
    ["default", "because this Workbench is set documents to always go to such models"],
  ] as const)("says whose word 'always' is: %s", (setBy, words) => {
    drawer(
      chatDocuments({
        model: { id: "gpt-5", window: 128000, locality: "external", runsOn: null },
        outside: { choice: "always", setBy, canSendForThisChat: false, sendForThisChat: false },
      }),
    );
    expect(status()).toHaveTextContent(
      "The passages of your documents that match a question go to another service, which runs outside your machines",
    );
    expect(status()).toHaveTextContent(words);
    expect(screen.queryByRole("button", { name: "Stop sending" })).toBeNull();
  });

  it("offers Send for this chat beside what is said of files that are searched whatever the switch says", () => {
    drawer({
      ...outside,
      sending: true,
      searching: { library: false, chat: true },
      counts: {
        library: { documents: 3, passages: 9, waiting: 0 },
        chat: { documents: 1, passages: 4, waiting: 0 },
      },
    });
    expect(status()).toHaveTextContent(
      "The files of this chat that you chose to search are searched",
    );
    expect(status()).toHaveTextContent("Your documents were not sent: this chat uses OpenRouter");
    expect(screen.getByRole("button", { name: "Send for this chat" })).toBeInTheDocument();
  });
});

describe("the chat's own switch", () => {
  it("shows what the chat does, and a name a screen reader says with its help", () => {
    drawer(chatDocuments());
    const toggle = screen.getByRole("switch", { name: "Documents in this chat" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAccessibleDescription(
      /Search your documents for each question you send here/,
    );
  });

  it("turns Documents on for the chat at once, and the chat's setting follows", async () => {
    let finish: (chat: Chat) => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { onChat, onReload } = drawer(off());
    onReload.mockClear();
    const toggle = screen.getByRole("switch", { name: "Documents in this chat" });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    // It shows the choice while the server catches up, and cannot be pressed twice.
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documents: "on" },
    });
    finish({ ...chat, settings: { documents: "on" } });
    await waitFor(() =>
      expect(onChat).toHaveBeenCalledWith({ ...chat, settings: { documents: "on" } }),
    );
    await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
  });

  it("turns it off by saying so, not by leaving it unsaid", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: { documents: "off" } });
    drawer(chatDocuments());
    const toggle = screen.getByRole("switch", { name: "Documents in this chat" });
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documents: "off" },
    });
    await waitFor(() => expect(toggle).toBeEnabled());
  });

  it("keeps the keyboard where it was: held while the server answers, and focused again after", async () => {
    let finish: (chat: Chat) => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    drawer(chatDocuments());
    const toggle = screen.getByRole("switch", { name: "Documents in this chat" });
    toggle.focus();
    fireEvent.click(toggle);
    expect(toggle).toBeDisabled();
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(toggle, "focus");
    finish({ ...chat, settings: { documents: "off" } });
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("puts the switch back and says why when the server refused", async () => {
    vi.mocked(patch).mockRejectedValue(new Error("This chat is gone."));
    const { onChat } = drawer(chatDocuments());
    const toggle = screen.getByRole("switch", { name: "Documents in this chat" });
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent("This chat is gone.");
    expect(toggle).toBeChecked();
    expect(toggle).toBeEnabled();
    expect(onChat).not.toHaveBeenCalled();
  });
});

describe("Documents only", () => {
  it("shows what the chat does, and says what it means", () => {
    drawer(chatDocuments({ documentsOnly: true }));
    const toggle = screen.getByRole("switch", { name: "Documents only" });
    expect(toggle).toBeChecked();
    expect(toggle).toHaveAccessibleDescription(
      /The model is told to answer only from the passages sent\. If nothing matches, Workbench says so itself and does not ask the model\./,
    );
  });

  it("turns on at once, and the chat's setting follows", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: { documentsOnly: true } });
    const { onChat } = drawer(chatDocuments());
    const toggle = screen.getByRole("switch", { name: "Documents only" });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documentsOnly: true },
    });
    await waitFor(() => expect(onChat).toHaveBeenCalledOnce());
  });

  it("turns off by clearing the setting, so the chat is as it was", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, settings: {} });
    drawer(chatDocuments({ documentsOnly: true }));
    const toggle = screen.getByRole("switch", { name: "Documents only" });
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/c1", {
      settings: { documentsOnly: null },
    });
    await waitFor(() => expect(toggle).toBeEnabled());
  });

  it("is there when Documents are off in this chat, for when they are turned on", () => {
    drawer(off());
    expect(screen.getByRole("switch", { name: "Documents only" })).toBeInTheDocument();
  });
});

describe("what there is to search", () => {
  it("counts the person's documents, in words, and what the room is", () => {
    drawer(chatDocuments());
    const counts = screen.getByTestId("documents-counts");
    expect(counts).toHaveTextContent("Your documents: 3 documents ready to search (9 passages).");
    expect(
      screen.getByText(
        "Passages may take up to 3,000 tokens of this model's 32,768-token window, and fewer when the chat is already long.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { level: 3, name: "What there is to search" }),
    ).toBeInTheDocument();
  });

  it("does not say what the room is for a chat that sends nothing", () => {
    drawer(off());
    expect(screen.queryByText(/Passages may take up to/)).toBeNull();
    expect(screen.getByTestId("documents-counts")).toBeInTheDocument();
  });

  it("lists the files of this chat that the person chose to search, and where each stands", () => {
    drawer(
      chatDocuments({
        chatDocuments: [
          saved({ id: "a", name: "long.pdf", scope: "chat" }),
          queued({ id: "b", name: "log.txt", scope: "chat" }),
          scanned({ id: "c", name: "scan.pdf", scope: "chat" }),
        ],
      }),
    );
    const list = screen.getByTestId("documents-chat-files");
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("long.pdf");
    expect(rows[0]).toHaveTextContent("Ready");
    expect(rows[1]).toHaveTextContent("Waiting to be read");
    expect(rows[2]).toHaveTextContent("Skipped: this PDF has no text");
    expect(
      screen.getByRole("heading", { level: 3, name: "Files of this chat you chose to search" }),
    ).toBeInTheDocument();
  });

  it("shows no list when the chat has no such files", () => {
    drawer(chatDocuments());
    expect(screen.queryByTestId("documents-chat-files")).toBeNull();
  });
});

describe("the owner reading someone else's chat", () => {
  it("is told whether Documents was on, and that what is in them is private; nothing to change", () => {
    drawer(owners({ documentsOnly: true }));
    expect(status()).toHaveTextContent(
      "Documents are on in this chat, and it answers only from them.",
    );
    expect(status()).toHaveTextContent("what a person keeps in their documents is private to them");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByTestId("documents-manage")).toBeNull();
    expect(screen.queryByTestId("documents-counts")).toBeNull();
    expect(screen.getByRole("button", { name: "Close documents" })).toBeInTheDocument();
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
    drawer(chatDocuments());
    const dialog = screen.getByRole("dialog", { name: "Documents in this chat" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("heading", { level: 2, name: "Documents in this chat" })).toHaveFocus();
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
    overlay();
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = drawer(chatDocuments());
    // The drawer took the keyboard into itself; closing it gives it back.
    expect(opener).not.toHaveFocus();
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("gives focus back also when it is a panel, once the person has moved into it", () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = drawer(chatDocuments());
    screen.getByRole("switch", { name: "Documents in this chat" }).focus();
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it("lets Tab leave a panel: only a dialog over the whole window keeps it inside", () => {
    drawer(chatDocuments());
    const panel = screen.getByRole("region", { name: "Documents in this chat" });
    const buttons = within(panel).getAllByRole("button");
    const last = buttons[buttons.length - 1]!;
    last.focus();
    // `fireEvent` answers false when the page cancelled the key.
    expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(true);
    expect(document.activeElement).toBe(last);
  });
});

describe("the hint in the composer", () => {
  function hint(over: Partial<Parameters<typeof DocumentsHint>[0]> = {}) {
    const calls = { onTurnOn: vi.fn(), onHide: vi.fn() };
    render(<DocumentsHint documents={3} busy={false} problem={null} {...calls} {...over} />);
    return calls;
  }

  it("says Documents are off here and that the person's documents are not searched, quietly", () => {
    hint();
    const line = screen.getByTestId("documents-hint");
    expect(line).toHaveTextContent("Your 3 documents are off here.");
    expect(line.querySelector("p")).toHaveClass("text-xs", "text-muted");
    expect(line.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(line.querySelector("[role=alert]")).toBeNull();
  });

  it("turns Documents on from the hint", () => {
    const { onTurnOn, onHide } = hint();
    fireEvent.click(screen.getByRole("button", { name: "Turn on Documents" }));
    expect(onTurnOn).toHaveBeenCalledOnce();
    expect(onHide).not.toHaveBeenCalled();
  });

  it("can be hidden, and its button says what it hides", () => {
    const { onHide } = hint();
    fireEvent.click(screen.getByRole("button", { name: "Hide this hint" }));
    expect(onHide).toHaveBeenCalledOnce();
  });

  it("is held while it turns Documents on", () => {
    hint({ busy: true });
    expect(screen.getByTestId("documents-hint-on")).toBeDisabled();
    expect(screen.getByTestId("documents-hint-hide")).toBeDisabled();
  });

  it("says why it could not, as an alert, beside the hint", () => {
    hint({ problem: "This chat is gone." });
    expect(screen.getByRole("alert")).toHaveTextContent("This chat is gone.");
  });
});
