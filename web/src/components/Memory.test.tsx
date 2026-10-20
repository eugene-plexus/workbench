import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, del, fileOf, patch, post, put } from "../lib/api";
import { characters, saveFile } from "../lib/memory";
import type { Chat, MemoryNote, MemoryPrefs } from "../lib/types";
import { NOW, note, page, prefs } from "../test-memory";
import { Memory } from "./Memory";
import { MemorySettings } from "./MemorySettings";

// Connections (MR9b) has its own tests; this page's do not serve it.
vi.mock("./Connections", () => ({ Connections: () => null }));
// So has MemoryModels, which the settings hold.
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));
vi.mock("./MemoryServer", () => ({ MemoryServer: () => null }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  fileOf: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));
vi.mock("../lib/memory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/memory")>()),
  saveFile: vi.fn(),
}));

// --- a Workbench that keeps notes, as far as the page can tell ---

const refuse = (message: string, reason: string, status = 422, extra: object = {}) =>
  new ApiError(message, status, { message, reason, ...extra });

interface Server {
  notes: MemoryNote[];
  prefs: MemoryPrefs;
  installEnabled: boolean;
  ownerReadsChats: boolean;
  maxNotes: number;
  notSent: number;
}

let server: Server;
let counter: number;

const fresh = (over: Partial<Server> = {}): Server => ({
  notes: [],
  prefs: prefs(),
  installEnabled: true,
  ownerReadsChats: false,
  maxNotes: 300,
  notSent: 0,
  ...over,
});

const seen = () =>
  page(server.notes, {
    installEnabled: server.installEnabled,
    ownerReadsChats: server.ownerReadsChats,
    prefs: server.prefs,
    usage: { maxNotes: server.maxNotes, notSent: server.notSent },
  });

function vetted(text: string): string {
  const said = text.trim();
  if (!said) throw refuse("Write what to remember.", "empty");
  if (said.includes("4242")) {
    throw refuse(
      "That looks like a password or key, so Workbench will not keep it",
      "secret",
      422,
      {
        looksLike: "a card number",
      },
    );
  }
  if (characters(said) > 240) {
    throw refuse(`A note is at most 240 characters; this one is ${characters(said)}.`, "too_long");
  }
  return said;
}

function listen() {
  vi.mocked(api).mockImplementation((async (path: string, init?: RequestInit) => {
    if (path === "/api/memory") return seen();
    const whole = /^\/api\/memory\/notes\/([^/?]+)$/.exec(path);
    if (whole && !init?.method) {
      const found = server.notes.find((n) => n.id === whole[1]);
      if (!found) throw new ApiError("There is no such note.", 404);
      return { note: found, earlier: [] };
    }
    if (path === "/api/memory/notes?confirm=all" && init?.method === "DELETE") {
      const deleted = server.notes.length;
      server.notes = [];
      return { deleted };
    }
    throw new Error(`not served: ${path}`);
  }) as never);
  vi.mocked(post).mockImplementation((async (path: string, body: Record<string, unknown>) => {
    if (path !== "/api/memory/notes") throw new Error(`not served: ${path}`);
    if (!server.installEnabled) {
      throw refuse("The owner of this Workbench has turned Memory off.", "off", 403);
    }
    const text = vetted(String(body.text ?? ""));
    const same = server.notes.find((n) => n.text.toLowerCase() === text.toLowerCase());
    if (same) return { note: same, created: false };
    if (server.notes.length >= server.maxNotes) {
      throw refuse("You have 300 notes, the limit. Delete some.", "full", 409, { limit: 300 });
    }
    const kind = (body.kind ?? "fact") as MemoryNote["kind"];
    const made = note({
      id: `n${++counter}`,
      text,
      kind,
      pinned: body.pinned === true,
      createdAt: NOW,
      updatedAt: NOW,
    });
    server.notes = [made, ...server.notes];
    return { note: made, created: true };
  }) as never);
  vi.mocked(patch).mockImplementation((async (path: string, body: Record<string, unknown>) => {
    const id = path.split("/").pop();
    const at = server.notes.findIndex((n) => n.id === id);
    if (at < 0) throw new ApiError("There is no such note.", 404);
    const text = typeof body.text === "string" ? vetted(body.text) : server.notes[at]!.text;
    const kind = (body.kind ?? server.notes[at]!.kind) as MemoryNote["kind"];
    const pinned = typeof body.pinned === "boolean" ? body.pinned : server.notes[at]!.pinned;
    const next = note({
      ...server.notes[at]!,
      text,
      kind,
      pinned,
      always: pinned || kind === "instruction",
    });
    server.notes = server.notes.map((n, i) => (i === at ? next : n));
    return { note: next };
  }) as never);
  vi.mocked(del).mockImplementation((async (path: string) => {
    const id = path.split("/").pop();
    if (!server.notes.some((n) => n.id === id)) throw new ApiError("There is no such note.", 404);
    server.notes = server.notes.filter((n) => n.id !== id);
  }) as never);
  vi.mocked(put).mockImplementation((async (_path: string, body: Record<string, unknown>) => {
    const next: MemoryPrefs = { ...server.prefs };
    if ("enabled" in body) next.enabled = Boolean(body.enabled);
    if ("external" in body) next.external = (body.external ?? null) as MemoryPrefs["external"];
    if ("budgetTokens" in body) next.budgetTokens = (body.budgetTokens ?? null) as number | null;
    server.prefs = prefs({
      ...next,
      effective: {
        enabled: next.enabled,
        disabledBy: next.enabled ? null : "you",
        external: next.external ?? "ask",
        externalSetBy: next.external ? "you" : "default",
        budgetTokens: next.budgetTokens ?? 2000,
      },
    });
    return server.prefs;
  }) as never);
}

const chats: Chat[] = [
  {
    id: "c1",
    title: "Build a birdhouse",
    model: "local",
    search: false,
    settings: {},
    createdAt: 1,
    updatedAt: 1,
    running: false,
    readOnly: false,
  },
];

function open(over: { loaded?: boolean } = {}) {
  const calls = { onOpenChat: vi.fn(), onClose: vi.fn() };
  const view = render(<Memory chats={chats} chatsLoaded={over.loaded ?? true} {...calls} />);
  return { ...view, ...calls };
}

/** The page, once it has read the notes. */
async function opened(over: { loaded?: boolean } = {}) {
  const view = open(over);
  // The page is there once it has stopped saying it is loading.
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
  return view;
}

/** Memory's settings, once they have read the page. */
async function openedSettings() {
  const view = render(<MemorySettings onOpenMemory={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your memory settings…")).toBeNull());
  return view;
}

const rows = () => screen.getAllByTestId("memory-note");
const textOf = (row: HTMLElement) => within(row).getByTestId("memory-note-text").textContent;

beforeEach(() => {
  server = fresh();
  counter = 0;
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(patch).mockReset();
  vi.mocked(del).mockReset();
  vi.mocked(put).mockReset();
  vi.mocked(fileOf).mockReset();
  vi.mocked(saveFile).mockReset();
  listen();
});

afterEach(() => vi.useRealTimers());

describe("opening the page", () => {
  it("says it is loading, then shows the page under a heading that takes focus from the top bar", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(api).mockReturnValue(new Promise((resolve) => (finish = resolve)) as never);
    open();
    expect(screen.getByRole("status")).toHaveTextContent("Loading your notes…");
    const heading = screen.getByRole("heading", { level: 1, name: "Memory" });
    expect(heading).toHaveAttribute("id", "view-heading");
    expect(heading).toHaveAttribute("tabindex", "-1");
    expect(heading).toHaveClass("title-page");
    expect(heading.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    finish(seen());
    await screen.findByTestId("memory-usage-notes");
    expect(screen.queryByText("Loading your notes…")).toBeNull();
  });

  it("is a frame like the other pages: one column, with Back to chat in its head", async () => {
    const { container, onClose } = await opened();
    expect(screen.getByTestId("memory-page")).toHaveClass("page");
    expect(container.querySelector(".page-col")).toBeInTheDocument();
    expect(container.querySelector("header")).toHaveClass("page-head");
    const back = screen.getByRole("button", { name: "Back to chat" });
    expect(back).toHaveClass("btn");
    expect(back).toHaveAttribute("data-testid", "memory-back");
    fireEvent.click(back);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("says why the notes could not be read, in the server's words, and tries again", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Workbench answered 502."));
    open();
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Could not read your notes");
    expect(failure).toHaveTextContent("Workbench answered 502.");
    fireEvent.click(within(failure).getByRole("button", { name: "Try again" }));
    await screen.findByTestId("memory-usage-notes");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says in a line what it is, and that only the person can see it", async () => {
    await opened();
    expect(screen.getByTestId("memory-intro")).toHaveTextContent(
      "What Workbench remembers about you. Only you can see this page.",
    );
    expect(
      screen.getByText(/Notes are short lines the model is given in your chats/),
    ).toBeInTheDocument();
    // What a chat does with a changed note is said once, where the person is looking at notes.
    expect(screen.getByText(/A chat keeps the notes it started with/)).toBeInTheDocument();
    expect(screen.getByText(/a deleted note leaves every chat at once\./)).toBeInTheDocument();
  });

  it("says that the owner can read chats, and an answer can quote a note, only when the owner can", async () => {
    server.ownerReadsChats = true;
    const { unmount } = await opened();
    const line = screen.getByTestId("memory-owner-reads");
    expect(line).toHaveClass("notice", "notice--warn");
    expect(line).toHaveTextContent(
      "The owner of this Workbench can read your chats, including any notes an answer quotes. The owner cannot read this page.",
    );
    unmount();
    server.ownerReadsChats = false;
    await opened();
    expect(screen.queryByTestId("memory-owner-reads")).toBeNull();
  });

  it("never uses the words of how it works underneath on the way a person reads it", async () => {
    server.notes = [note()];
    const { container } = await opened();
    expect(container.textContent).not.toMatch(/\b(RAG|vector|embedding|retrieval)s?\b/i);
  });
});

describe("when the owner has turned Memory off", () => {
  beforeEach(() => {
    server = fresh({ installEnabled: false });
  });

  it("says so, and offers nothing that would keep a note", async () => {
    await opened();
    const said = screen.getByTestId("memory-owner-off");
    expect(said).toHaveClass("notice");
    expect(said).toHaveTextContent("The owner of this Workbench has turned Memory off.");
    expect(said).toHaveTextContent("No notes are sent to any model.");
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.queryByTestId("memory-add-form")).toBeNull();
    expect(screen.queryByTestId("memory-empty")).toBeNull();
    expect(screen.queryByTestId("memory-usage-tokens")).toBeNull();
    expect(screen.queryByTestId("memory-intro")).toBeNull();
    // With no notes there is nothing else to say: no list, no export, no delete.
    expect(screen.queryByRole("heading", { level: 2, name: "Your notes" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 2, name: "Export or delete" })).toBeNull();
    expect(screen.queryByTestId("memory-export-md")).toBeNull();
    expect(screen.queryByTestId("memory-delete-all")).toBeNull();
    expect(screen.getAllByRole("heading")).toHaveLength(1);
  });

  it("leaves notes the person already has theirs to read, export and delete, and not to change", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    expect(screen.getByTestId("memory-owner-off")).toHaveTextContent(
      "The notes you already have stay yours: you can still export or delete them.",
    );
    expect(textOf(rows()[0]!)).toBe("I live in Lisbon");
    expect(screen.queryByRole("button", { name: /^Edit note/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Pin note/ })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Delete note: I live in Lisbon" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("memory-export-md")).toBeInTheDocument();
    expect(screen.getByTestId("memory-delete-all")).toBeInTheDocument();
    expect(screen.queryByTestId("memory-add-form")).toBeNull();
  });

  it("still deletes a note, since that is allowed whatever the owner has set", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: I live in Lisbon" }));
    await waitFor(() => expect(screen.queryByTestId("memory-note")).toBeNull());
    expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/a");
  });
});

describe("how much is used", () => {
  it("says tokens and notes the way the design does", async () => {
    server.notes = [note({ id: "a" }), note({ id: "b", text: "I live in Lisbon" })];
    await opened();
    expect(screen.getByTestId("memory-usage-tokens")).toHaveTextContent(
      "Pinned: 174 of 2,000 tokens, in every chat. Relevant: up to 6,000 tokens a turn, chosen from 2 notes by words.",
    );
    expect(screen.getByTestId("memory-usage-notes")).toHaveTextContent("2 of 300 notes");
  });

  it("says when notes do not fit, and what is used first", async () => {
    server.notes = [note({ id: "a" })];
    server.notSent = 1;
    await opened();
    const full = screen.getByTestId("memory-usage-full");
    expect(full).toHaveClass("notice--warn");
    expect(full).toHaveTextContent("1 note does not fit in this room, so it is left out.");
    expect(full).toHaveTextContent("Notes you pin, and instructions, are used first.");
    server.notSent = 4;
    cleanupPage();
    await opened();
    expect(screen.getByTestId("memory-usage-full")).toHaveTextContent(
      "4 notes do not fit in this room, so they are left out.",
    );
  });

  it("says when the limit is reached, and who can raise it", async () => {
    server.notes = [note({ id: "a" }), note({ id: "b", text: "Another" })];
    server.maxNotes = 2;
    await opened();
    expect(screen.getByTestId("memory-usage-limit")).toHaveTextContent(
      "You have 2 notes, the limit. Delete some, or ask the owner to raise it.",
    );
  });

  it("says nothing of a limit that is not reached", async () => {
    server.notes = [note({ id: "a" })];
    await opened();
    expect(screen.queryByTestId("memory-usage-limit")).toBeNull();
    expect(screen.queryByTestId("memory-usage-full")).toBeNull();
  });
});

/** Throw the page away, to open it on what the server now says. */
function cleanupPage() {
  document.body.innerHTML = "";
}

describe("the notes", () => {
  const mixed = () => [
    note({ id: "a", text: "I live in Lisbon", pinned: true, createdAt: NOW - 10 }),
    note({ id: "b", text: "Keep answers short", kind: "instruction", createdAt: NOW - 20 }),
    note({ id: "c", text: "I am building a birdhouse", kind: "project", createdAt: NOW - 30 }),
    note({ id: "d", text: "My flight leaves on March 3", kind: "event", createdAt: NOW - 40 }),
  ];

  it("are in two lists: always used, and used when there is room, each said in a line", async () => {
    server.notes = mixed();
    await opened();
    const always = screen.getByTestId("memory-section-always");
    const other = screen.getByTestId("memory-section-other");
    expect(
      within(always).getByRole("heading", { level: 3, name: "Always used" }),
    ).toBeInTheDocument();
    expect(always).toHaveTextContent("Instructions and pinned notes. Every chat gets these first.");
    expect(
      within(other).getByRole("heading", { level: 3, name: "Used when there is room" }),
    ).toBeInTheDocument();
    expect(other).toHaveTextContent(
      "The newest first, as many as fit the room a model has for notes.",
    );
    expect(within(always).getAllByTestId("memory-note").map(textOf)).toEqual([
      "I live in Lisbon",
      "Keep answers short",
    ]);
    expect(within(other).getAllByTestId("memory-note").map(textOf)).toEqual([
      "I am building a birdhouse",
      "My flight leaves on March 3",
    ]);
  });

  it("does not say they are used when relevant: a note is used when it fits, newest first", async () => {
    server.notes = mixed();
    const { container } = await opened();
    expect(container.textContent).not.toMatch(/when relevant/i);
  });

  it("says what a list is for when it has nothing in it yet", async () => {
    server.notes = [note({ id: "d", text: "My flight leaves on March 3", kind: "event" })];
    await opened();
    expect(screen.getByTestId("memory-section-always")).toHaveTextContent(
      "Nothing here yet. Pin a note, or write an instruction, to use it every time.",
    );
    server.notes = [note({ id: "a", text: "I live in Lisbon", pinned: true })];
    cleanupPage();
    await opened();
    expect(screen.getByTestId("memory-section-other")).toHaveTextContent("Nothing here yet.");
  });

  it("show each note's words, kind, pin, day and where it came from", async () => {
    server.notes = [
      note({ id: "a", text: "I live in Lisbon", pinned: true }),
      note({
        id: "b",
        text: "I am building a birdhouse",
        kind: "project",
        source: "button",
        sourceChatId: "c1",
      }),
    ];
    await opened();
    const [pinned, built] = rows();
    expect(within(pinned!).getByText("Fact")).toHaveClass("badge");
    expect(within(pinned!).getByText("Pinned")).toHaveClass("badge");
    expect(within(pinned!).getByTestId("memory-note-from")).toHaveTextContent("Written here");
    expect(within(built!).getByText("Project")).toHaveClass("badge");
    expect(within(built!).queryByText("Pinned")).toBeNull();
    const day = within(built!).getByText(/Oct 11/);
    expect(day.tagName).toBe("TIME");
    expect(day).toHaveAttribute("datetime", new Date((NOW - 3600) * 1000).toISOString());
  });

  it("take their direction from their own letters, and wrap a word with no break in it", async () => {
    server.notes = [note({ id: "a", text: "x".repeat(200) })];
    await opened();
    const words = screen.getByTestId("memory-note-text");
    expect(words).toHaveAttribute("dir", "auto");
    expect(words).toHaveClass("wrap-anywhere");
  });

  it("link the chat a note came from, which opens it", async () => {
    server.notes = [note({ id: "b", source: "said", sourceChatId: "c1" })];
    const { onOpenChat } = await opened();
    const from = screen.getByTestId("memory-note-from");
    expect(from).toHaveTextContent("From Build a birdhouse");
    const link = within(from).getByRole("link", { name: "Build a birdhouse" });
    expect(link).toHaveAttribute("href", "/chats/c1");
    expect(link).toHaveClass("link");
    fireEvent.click(link);
    expect(onOpenChat).toHaveBeenCalledExactlyOnceWith("c1");
  });

  it("leave a click that asks for a new tab to the browser", async () => {
    server.notes = [note({ id: "b", source: "said", sourceChatId: "c1" })];
    const { onOpenChat } = await opened();
    // The browser would open a tab; jsdom cannot, and says so unless it is stopped.
    window.addEventListener("click", (event) => event.preventDefault(), { once: true });
    fireEvent.click(screen.getByRole("link", { name: "Build a birdhouse" }), { ctrlKey: true });
    expect(onOpenChat).not.toHaveBeenCalled();
  });

  it("say when the chat a note came from was deleted, and are no link", async () => {
    server.notes = [note({ id: "b", source: "button", sourceChatId: "gone" })];
    await opened();
    expect(screen.getByTestId("memory-note-from")).toHaveTextContent(
      "From a chat that was deleted",
    );
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("do not say a chat was deleted before the list of chats has been read", async () => {
    server.notes = [note({ id: "b", source: "button", sourceChatId: "later" })];
    await opened({ loaded: false });
    expect(screen.getByTestId("memory-note-from")).toHaveTextContent("From a chat");
    expect(screen.getByTestId("memory-note-from")).not.toHaveTextContent("deleted");
  });

  it("each have Edit, Pin and Delete, named for the note they act on", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    for (const name of [
      "Edit note: I live in Lisbon",
      "Pin note: I live in Lisbon",
      "Delete note: I live in Lisbon",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByTestId("memory-note-delete")).toHaveClass("btn-danger");
    // A long note is named by its first words.
    server.notes = [note({ id: "z", text: "x".repeat(100) })];
    cleanupPage();
    await opened();
    expect(
      screen.getByRole("button", { name: `Edit note: ${"x".repeat(40)}…` }),
    ).toBeInTheDocument();
  });
});

describe("editing a note", () => {
  beforeEach(() => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
  });

  async function edit() {
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Edit note: I live in Lisbon" }));
    return screen.getByRole("textbox", { name: "Note" });
  }

  it("opens a box with the words, its kind and how long they are, focused", async () => {
    const box = await edit();
    expect(box).toHaveValue("I live in Lisbon");
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("dir", "auto");
    const form = screen.getByRole("form", { name: "Edit this note" });
    expect(within(form).getByRole("combobox", { name: "Kind" })).toHaveValue("fact");
    expect(within(form).getByText("16 of 240")).toBeInTheDocument();
  });

  it("saves the new words and kind, then reads the notes again and puts focus back on Edit", async () => {
    const box = await edit();
    fireEvent.change(box, { target: { value: "I live in Porto" } });
    fireEvent.change(
      within(screen.getByRole("form", { name: "Edit this note" })).getByRole("combobox", {
        name: "Kind",
      }),
      { target: { value: "project" } },
    );
    fireEvent.click(
      within(screen.getByRole("form", { name: "Edit this note" })).getByRole("button", {
        name: "Save",
      }),
    );
    await waitFor(() => expect(textOf(rows()[0]!)).toBe("I live in Porto"));
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/a", {
      text: "I live in Porto",
      kind: "project",
    });
    expect(within(rows()[0]!).getByText("Project")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Edit note: I live in Porto" })).toHaveFocus(),
    );
  });

  it("saves on Enter, and a new line is not a note: a pasted paragraph becomes one line", async () => {
    const box = await edit();
    fireEvent.change(box, { target: { value: "I live\nin Porto" } });
    expect(box).toHaveValue("I live in Porto");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(patch).toHaveBeenCalledOnce());
    expect(vi.mocked(patch).mock.calls[0]![1]).toMatchObject({ text: "I live in Porto" });
  });

  it("does not save on Shift+Enter or while a word is being composed", async () => {
    const box = await edit();
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    expect(patch).not.toHaveBeenCalled();
  });

  it("leaves the note alone on Cancel and on Escape", async () => {
    const box = await edit();
    fireEvent.change(box, { target: { value: "I live in Porto" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(textOf(rows()[0]!)).toBe("I live in Lisbon");
    fireEvent.click(screen.getByRole("button", { name: "Edit note: I live in Lisbon" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Note" }), { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "Note" })).toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("will not save nothing, or more than 240 characters, and says how long it is", async () => {
    const box = await edit();
    fireEvent.change(box, { target: { value: "x".repeat(250) } });
    expect(screen.getByText("250 of 240")).toHaveClass("text-error");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(
      within(screen.getByRole("form", { name: "Edit this note" })).getByRole("button", {
        name: "Save",
      }),
    ).toBeDisabled();
    fireEvent.change(box, { target: { value: "  " } });
    expect(
      within(screen.getByRole("form", { name: "Edit this note" })).getByRole("button", {
        name: "Save",
      }),
    ).toBeDisabled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(patch).not.toHaveBeenCalled();
  });

  it("says in the server's words why a note was refused, and keeps the box open", async () => {
    const box = await edit();
    fireEvent.change(box, { target: { value: "My card is 4242 4242 4242 4242" } });
    fireEvent.click(
      within(screen.getByRole("form", { name: "Edit this note" })).getByRole("button", {
        name: "Save",
      }),
    );
    const refused = await screen.findByRole("alert");
    expect(refused).toHaveTextContent(
      "That looks like a password or key, so Workbench will not keep it. It looks like a card number.",
    );
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAccessibleDescription(/It looks like a card number/);
    expect(screen.getByRole("textbox", { name: "Note" })).toHaveValue(
      "My card is 4242 4242 4242 4242",
    );
    // Nothing was changed on the server.
    expect(server.notes.map((n) => n.text)).toEqual(["I live in Lisbon"]);
  });
});

describe("pinning and deleting", () => {
  it("pins a note, which moves it to the list that is always used, and Unpin takes it back", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Pin note: I live in Lisbon" }));
    await waitFor(() =>
      expect(
        within(screen.getByTestId("memory-section-always")).getByText("I live in Lisbon"),
      ).toBeInTheDocument(),
    );
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/a", { pinned: true });
    expect(screen.getByText("Pinned")).toBeInTheDocument();
    // Focus follows the button to where it moved.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Unpin note: I live in Lisbon" })).toHaveFocus(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Unpin note: I live in Lisbon" }));
    await waitFor(() =>
      expect(
        within(screen.getByTestId("memory-section-other")).getByText("I live in Lisbon"),
      ).toBeInTheDocument(),
    );
    expect(patch).toHaveBeenLastCalledWith("/api/memory/notes/a", { pinned: false });
  });

  it("deletes a note for real at once, says so, and holds its words for ten seconds to put back", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon", pinned: true, kind: "project" })];
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: I live in Lisbon" }));
    const said = await screen.findByTestId("memory-deleted");
    expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/a");
    expect(said).toHaveAttribute("role", "status");
    expect(said).toHaveTextContent("Deleted: “I live in Lisbon”");
    expect(screen.queryByText("Fact")).toBeNull();
    // The page is the empty page now, and says so.
    expect(screen.getByTestId("memory-empty")).toBeInTheDocument();
    // Focus is not lost to the top of the page: it goes to the notes.
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: "Your notes" })).toHaveFocus(),
    );

    fireEvent.click(screen.getByTestId("memory-undo"));
    await waitFor(() => expect(textOf(rows()[0]!)).toBe("I live in Lisbon"));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
      text: "I live in Lisbon",
      kind: "project",
      pinned: true,
    });
    expect(screen.queryByTestId("memory-deleted")).toBeNull();
    expect(within(rows()[0]!).getByText("Pinned")).toBeInTheDocument();
  });

  it("lets the undo go after ten seconds", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    fireEvent.click(screen.getByRole("button", { name: "Delete note: I live in Lisbon" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(screen.getByTestId("memory-deleted")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9_900);
    });
    expect(screen.getByTestId("memory-deleted")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(screen.queryByTestId("memory-deleted")).toBeNull();
  });

  it("says why when a note could not be deleted, and keeps it", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    vi.mocked(del).mockRejectedValue(new ApiError("There is no such note.", 404));
    fireEvent.click(screen.getByRole("button", { name: "Delete note: I live in Lisbon" }));
    const failure = await screen.findByTestId("memory-problem");
    expect(failure).toHaveAttribute("role", "alert");
    expect(failure).toHaveTextContent("There is no such note.");
    expect(screen.queryByTestId("memory-deleted")).toBeNull();
    expect(rows()).toHaveLength(1);
  });

  it("holds every button while a change is being made, so one cannot be pressed twice", async () => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
    await opened();
    let finish: () => void = () => undefined;
    vi.mocked(patch).mockReturnValue(new Promise<void>((resolve) => (finish = resolve)) as never);
    fireEvent.click(screen.getByRole("button", { name: "Pin note: I live in Lisbon" }));
    expect(screen.getByRole("button", { name: "Delete note: I live in Lisbon" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Edit note: I live in Lisbon" })).toBeDisabled();
    finish();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Delete note: I live in Lisbon" })).toBeEnabled(),
    );
  });
});

describe("adding a note", () => {
  beforeEach(() => {
    server.notes = [note({ id: "a", text: "I live in Lisbon" })];
  });

  const add = () => screen.getByTestId("memory-add-form");
  const field = () =>
    within(add()).getByRole("textbox", { name: "What should Workbench remember?" });

  it("is a form of its own, a dashed card with a heading, a box, a kind and a count", async () => {
    await opened();
    expect(add()).toHaveClass("card", "card--add");
    expect(add()).toHaveAccessibleName("Add a note");
    expect(field()).toHaveClass("control");
    expect(field()).toHaveAttribute("dir", "auto");
    expect(field()).toHaveAccessibleDescription(/One line, at most 240 characters/);
    expect(within(add()).getByRole("combobox", { name: "Kind" })).toHaveValue("fact");
    expect(within(add()).getByText("Something true about you.")).toBeInTheDocument();
    expect(screen.getByTestId("memory-add-count")).toHaveTextContent("0 of 240");
    expect(screen.getByTestId("memory-add")).toBeDisabled();
  });

  it("says what each kind is for", async () => {
    await opened();
    const kind = within(add()).getByRole("combobox", { name: "Kind" });
    for (const [value, help] of [
      ["preference", "Something you like or prefer."],
      ["instruction", "How you want answers written. Always used."],
      ["project", "Something you are working on."],
      ["event", "Something with a date."],
      ["procedure", "Steps, a brief or a checklist. Can be long."],
    ] as const) {
      fireEvent.change(kind, { target: { value } });
      expect(within(add()).getByText(help)).toBeInTheDocument();
    }
    expect(
      within(kind)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Fact", "Preference", "Instruction", "Project", "Event", "Procedure"]);
  });

  it("keeps the note with its kind and whether it is always used, and then the page reads it", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "  Keep answers short  " } });
    fireEvent.change(within(add()).getByRole("combobox", { name: "Kind" }), {
      target: { value: "instruction" },
    });
    fireEvent.click(within(add()).getByRole("checkbox", { name: "Always use this note" }));
    expect(screen.getByTestId("memory-add-count")).toHaveTextContent("18 of 240");
    fireEvent.click(screen.getByTestId("memory-add"));
    await waitFor(() => expect(screen.getAllByTestId("memory-note")).toHaveLength(2));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
      text: "Keep answers short",
      kind: "instruction",
      pinned: true,
    });
    expect(
      within(screen.getByTestId("memory-section-always")).getByText("Keep answers short"),
    ).toBeInTheDocument();
    expect(await within(add()).findByText("Kept.")).toBeInTheDocument();
    expect(field()).toHaveValue("");
    expect(within(add()).getByRole("checkbox", { name: "Always use this note" })).not.toBeChecked();
  });

  it("keeps on Enter, and a new line is not part of a note", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "I use\nmetric units" } });
    expect(field()).toHaveValue("I use metric units");
    fireEvent.keyDown(field(), { key: "Enter" });
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    fireEvent.change(field(), { target: { value: "Another" } });
    fireEvent.keyDown(field(), { key: "Enter", shiftKey: true });
    expect(post).toHaveBeenCalledOnce();
  });

  it("keeps the keyboard in the box after a note is kept, to write the next", async () => {
    await opened();
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(post).mockReturnValueOnce(new Promise((resolve) => (finish = resolve)) as never);
    fireEvent.change(field(), { target: { value: "Another note" } });
    field().focus();
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(field()).toBeDisabled();
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(field(), "focus");
    finish({ note: note({ id: "z", text: "Another note" }), created: true });
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("says when the same words were already kept, and adds nothing", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "i live in lisbon" } });
    fireEvent.click(screen.getByTestId("memory-add"));
    expect(await within(add()).findByText("You already have that note.")).toBeInTheDocument();
    expect(screen.getAllByTestId("memory-note")).toHaveLength(1);
  });

  it("will not take more than 240 characters, and says how many it has", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "x".repeat(241) } });
    expect(screen.getByTestId("memory-add-count")).toHaveTextContent("241 of 240");
    expect(screen.getByTestId("memory-add-count")).toHaveClass("text-error");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("memory-add")).toBeDisabled();
    fireEvent.keyDown(field(), { key: "Enter" });
    expect(post).not.toHaveBeenCalled();
  });

  it("counts characters as the server does, so 240 emoji fit and 241 do not", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "🐦".repeat(240) } });
    expect(screen.getByTestId("memory-add-count")).toHaveTextContent("240 of 240");
    expect(screen.getByTestId("memory-add")).toBeEnabled();
    fireEvent.change(field(), { target: { value: "🐦".repeat(241) } });
    expect(screen.getByTestId("memory-add-count")).toHaveTextContent("241 of 240");
    expect(screen.getByTestId("memory-add")).toBeDisabled();
  });

  it("says in plain words why a note was refused: a card number is not kept, and what it looks like", async () => {
    await opened();
    fireEvent.change(field(), { target: { value: "My card is 4242 4242 4242 4242" } });
    fireEvent.click(screen.getByTestId("memory-add"));
    const refused = await screen.findByTestId("memory-add-problem");
    expect(refused).toHaveAttribute("role", "alert");
    expect(refused).toHaveClass("notice--error");
    expect(refused).toHaveTextContent(
      "That looks like a password or key, so Workbench will not keep it. It looks like a card number.",
    );
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(field()).toHaveAccessibleDescription(/It looks like a card number/);
    // What was typed stays, so it can be fixed.
    expect(field()).toHaveValue("My card is 4242 4242 4242 4242");
    expect(screen.getAllByTestId("memory-note")).toHaveLength(1);
  });

  it("says when the limit has been reached, in the server's words", async () => {
    server.maxNotes = 1;
    await opened();
    fireEvent.change(field(), { target: { value: "One more" } });
    fireEvent.click(screen.getByTestId("memory-add"));
    expect(await screen.findByTestId("memory-add-problem")).toHaveTextContent(
      "You have 300 notes, the limit. Delete some.",
    );
  });

  it("is not there for a person who has no notes: the two boxes are", async () => {
    server.notes = [];
    await opened();
    expect(screen.queryByTestId("memory-add-form")).toBeNull();
  });
});

describe("a person with no notes", () => {
  const about = () => screen.getByRole("textbox", { name: "About me" });
  const answers = () => screen.getByRole("textbox", { name: "How I want answers" });

  it("is told where to start, in words, with the two boxes the design asks for", async () => {
    await opened();
    const empty = screen.getByTestId("memory-empty");
    expect(
      within(empty).getByRole("heading", { level: 3, name: "Nothing remembered yet" }),
    ).toBeInTheDocument();
    expect(empty).toHaveTextContent(
      "Type “Remember that …” in any chat, or write one note a line below.",
    );
    expect(about()).toHaveAccessibleDescription(/One note per line, at most 240 characters each/);
    expect(answers()).toHaveAccessibleDescription(/used in every chat/);
    expect(about()).toHaveAttribute("dir", "auto");
    expect(screen.queryByTestId("memory-section-always")).toBeNull();
    // The usage is still there, and the settings are not: this is a page, not a promotion.
    expect(screen.queryByTestId("memory-use-switch")).toBeNull();
    expect(screen.getByTestId("memory-usage-notes")).toHaveTextContent("0 of 300 notes");
  });

  it("keeps each line as a note that is always used: facts about the person, and instructions", async () => {
    await opened();
    fireEvent.change(about(), {
      target: { value: "I use metric units\n\n  I live in Lisbon  \n" },
    });
    fireEvent.change(answers(), { target: { value: "Keep answers short" } });
    fireEvent.click(screen.getByTestId("memory-starter-save"));
    await waitFor(() => expect(screen.getAllByTestId("memory-note")).toHaveLength(3));
    expect(vi.mocked(post).mock.calls.map((c) => c[1])).toEqual([
      { text: "I use metric units", kind: "fact", pinned: true },
      { text: "I live in Lisbon", kind: "fact", pinned: true },
      { text: "Keep answers short", kind: "instruction", pinned: true },
    ]);
    // The page is the page of someone with notes now.
    expect(screen.queryByTestId("memory-empty")).toBeNull();
    expect(
      within(screen.getByTestId("memory-section-always")).getAllByTestId("memory-note"),
    ).toHaveLength(3);
    expect(screen.getByTestId("memory-add-form")).toBeInTheDocument();
  });

  it("asks for a line before it saves nothing", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-starter-save"));
    expect(screen.getByTestId("memory-starter-problem")).toHaveTextContent(
      "Write at least one line, then save.",
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("names a line that is too long, before it keeps any of them", async () => {
    await opened();
    fireEvent.change(about(), { target: { value: `I live in Lisbon\n${"x".repeat(250)}` } });
    fireEvent.click(screen.getByTestId("memory-starter-save"));
    const problem = screen.getByTestId("memory-starter-problem");
    expect(problem).toHaveAttribute("role", "alert");
    expect(problem).toHaveTextContent(
      "About me: “xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…” is 250 characters, and a note is at most 240.",
    );
    expect(problem).toHaveTextContent("Shorten it, or split it into two lines.");
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps what it can, and leaves the lines it could not keep in their boxes with why", async () => {
    await opened();
    fireEvent.change(about(), {
      target: { value: "I live in Lisbon\nMy card is 4242 4242 4242 4242" },
    });
    fireEvent.change(answers(), { target: { value: "Keep answers short" } });
    fireEvent.click(screen.getByTestId("memory-starter-save"));
    const problem = await screen.findByTestId("memory-starter-problem");
    expect(problem).toHaveTextContent("About me: “My card is 4242 4242 4242 4242”");
    expect(problem).toHaveTextContent("It looks like a card number.");
    // The two that were fine are kept, and the one that was not is still there to fix.
    expect(about()).toHaveValue("My card is 4242 4242 4242 4242");
    expect(answers()).toHaveValue("");
    expect(server.notes.map((n) => n.text).sort()).toEqual([
      "I live in Lisbon",
      "Keep answers short",
    ]);
    expect(screen.getByTestId("memory-empty")).toBeInTheDocument();
  });

  it("stops at the limit and leaves every line it did not keep", async () => {
    server.maxNotes = 1;
    await opened();
    fireEvent.change(about(), { target: { value: "One\nTwo\nThree" } });
    fireEvent.click(screen.getByTestId("memory-starter-save"));
    await screen.findByTestId("memory-starter-problem");
    expect(about()).toHaveValue("Two\nThree");
    expect(server.notes.map((n) => n.text)).toEqual(["One"]);
    expect(post).toHaveBeenCalledTimes(2);
  });
});

describe("the settings (now on the Settings page)", () => {
  it("shows Use memory as it is in effect, and turning it off keeps notes and sends none", async () => {
    await openedSettings();
    const use = screen.getByRole("switch", { name: "Use memory" });
    expect(use).toBeChecked();
    expect(screen.getByTestId("memory-use-effective")).toHaveTextContent("Memory is on:");
    fireEvent.click(use);
    // It shows the choice at once, while the server catches up.
    expect(use).not.toBeChecked();
    await waitFor(() => expect(use).toBeEnabled());
    expect(put).toHaveBeenCalledExactlyOnceWith("/api/memory/prefs", { enabled: false });
    expect(use).not.toBeChecked();
    expect(screen.getByTestId("memory-use-effective")).toHaveTextContent(
      "You turned memory off. No model is given your notes, and Remember that … keeps nothing. Your notes stay saved here.",
    );
    fireEvent.click(use);
    await waitFor(() => expect(use).toBeChecked());
    expect(put).toHaveBeenLastCalledWith("/api/memory/prefs", { enabled: true });
  });

  it("keeps the keyboard on the switch while the server answers", async () => {
    await openedSettings();
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(put).mockReturnValue(new Promise((resolve) => (finish = resolve)) as never);
    const use = screen.getByRole("switch", { name: "Use memory" });
    use.focus();
    fireEvent.click(use);
    expect(use).toBeDisabled();
    // What a browser does with the control that has focus when it is disabled:
    // focus is on the page. (jsdom keeps it where it was, so the page says so.)
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(use, "focus");
    finish(prefs());
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("puts the switch back and says why when the server refused", async () => {
    await openedSettings();
    vi.mocked(put).mockRejectedValue(new Error("Workbench answered 500."));
    const use = screen.getByRole("switch", { name: "Use memory" });
    fireEvent.click(use);
    expect(await screen.findByTestId("memory-problem")).toHaveTextContent(
      "Workbench answered 500.",
    );
    expect(use).toBeChecked();
    expect(use).toBeEnabled();
  });

  it("shows which of the three choices about outside models is in effect, and whose word it is", async () => {
    await openedSettings();
    expect(screen.getByRole("radio", { name: "Ask me in each chat" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Never send them" })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: "Always send them" })).not.toBeChecked();
    expect(screen.getByTestId("memory-external-effective")).toHaveTextContent(
      "In effect: Ask me in each chat. Nobody has chosen, so the default applies.",
    );
    // Nothing has been chosen, so there is nothing to go back to.
    expect(screen.queryByTestId("memory-external-default")).toBeNull();
    expect(screen.getByRole("radio", { name: "Never send them" })).toHaveAccessibleDescription(
      "Notes only go to models that run on your own machines.",
    );
    expect(
      screen.getByRole("group", { name: "Models that run outside your machines" }),
    ).toBeInTheDocument();
  });

  it("changes the choice, says it is the person's, and offers the default back", async () => {
    await openedSettings();
    fireEvent.click(screen.getByRole("radio", { name: "Never send them" }));
    expect(screen.getByRole("radio", { name: "Never send them" })).toBeChecked();
    await waitFor(() =>
      expect(screen.getByTestId("memory-external-effective")).toHaveTextContent("You chose this."),
    );
    expect(put).toHaveBeenCalledExactlyOnceWith("/api/memory/prefs", { external: "never" });
    expect(screen.getByTestId("memory-external-effective")).toHaveTextContent(
      "In effect: Never send them.",
    );
    expect(screen.getByRole("radio", { name: "Ask me in each chat" })).not.toBeChecked();
    fireEvent.click(screen.getByTestId("memory-external-default"));
    await waitFor(() =>
      expect(screen.getByTestId("memory-external-effective")).toHaveTextContent(
        "Nobody has chosen",
      ),
    );
    expect(put).toHaveBeenLastCalledWith("/api/memory/prefs", { external: null });
    expect(screen.queryByTestId("memory-external-default")).toBeNull();
  });

  it("says the owner's never is final, and cannot be changed here", async () => {
    server.prefs = prefs({
      external: "always",
      effective: { external: "never", externalSetBy: "owner" },
    });
    await openedSettings();
    expect(screen.getByRole("radio", { name: "Never send them" })).toBeChecked();
    for (const radio of screen
      .getAllByRole("radio")
      .filter((r) => r.getAttribute("name")?.endsWith("-external")))
      expect(radio).toBeDisabled();
    expect(screen.getByTestId("memory-external-effective")).toHaveTextContent(
      "The owner of this Workbench set this, and it cannot be changed here.",
    );
    // What the person chose does not show as if it were in effect, nor can it be reset.
    expect(screen.getByRole("radio", { name: "Always send them" })).not.toBeChecked();
    expect(screen.queryByTestId("memory-external-default")).toBeNull();
  });
});

describe("how much room notes may take", () => {
  async function room() {
    await openedSettings();
    return screen.getByTestId("memory-budget-details");
  }

  it("is behind a row that opens, and says what is in effect and whose word it is", async () => {
    const details = await room();
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("How much room notes may take")).toHaveClass("disclosure");
    expect(screen.getByTestId("memory-budget-effective")).toHaveTextContent(
      "In effect: 2,000 tokens. Nobody has chosen, so the default applies.",
    );
    expect(details).toHaveTextContent("never more than 4% of its model's window");
    expect(screen.getByTestId("memory-budget-input")).toHaveAttribute(
      "placeholder",
      "2,000 (the default)",
    );
    expect(screen.queryByTestId("memory-budget-default")).toBeNull();
  });

  it("saves a whole number, then says it is the person's, with a way back to the default", async () => {
    await room();
    fireEvent.change(screen.getByTestId("memory-budget-input"), { target: { value: "600" } });
    fireEvent.click(screen.getByTestId("memory-budget-save"));
    await waitFor(() =>
      expect(screen.getByTestId("memory-budget-effective")).toHaveTextContent("600 tokens"),
    );
    expect(put).toHaveBeenCalledExactlyOnceWith("/api/memory/prefs", { budgetTokens: 600 });
    expect(screen.getByTestId("memory-budget-effective")).toHaveTextContent("You chose this.");
    expect(screen.getByTestId("memory-budget-input")).toHaveValue("600");
    fireEvent.click(screen.getByTestId("memory-budget-default"));
    await waitFor(() =>
      expect(screen.getByTestId("memory-budget-effective")).toHaveTextContent("2,000 tokens"),
    );
    expect(put).toHaveBeenLastCalledWith("/api/memory/prefs", { budgetTokens: null });
    expect(screen.getByTestId("memory-budget-input")).toHaveValue("");
  });

  it.each(["", "abc", "12.5", "199", "20001", "-5"])("will not save %j", async (value) => {
    await room();
    const input = screen.getByTestId("memory-budget-input");
    fireEvent.change(input, { target: { value } });
    fireEvent.click(screen.getByTestId("memory-budget-save"));
    const problem = screen.getByRole("alert");
    expect(problem).toHaveTextContent(
      "Enter a whole number from 200 to 20,000, or use the default.",
    );
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Enter a whole number from 200 to 20,000/);
    expect(put).not.toHaveBeenCalled();
  });

  it("takes both ends of what the server takes", async () => {
    await room();
    for (const value of ["200", "20000"]) {
      fireEvent.change(screen.getByTestId("memory-budget-input"), { target: { value } });
      fireEvent.click(screen.getByTestId("memory-budget-save"));
      await waitFor(() =>
        expect(put).toHaveBeenLastCalledWith("/api/memory/prefs", { budgetTokens: Number(value) }),
      );
      await waitFor(() => expect(screen.getByTestId("memory-budget-save")).toBeEnabled());
    }
  });
});

describe("export", () => {
  it("fetches the Markdown as a file with the secret, and saves it under the server's name", async () => {
    server.notes = [note({ id: "a" })];
    const blob = new Blob(["# Memory"]);
    vi.mocked(fileOf).mockResolvedValue({ blob, name: "workbench-memory.md" });
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Export as Markdown" }));
    await waitFor(() => expect(saveFile).toHaveBeenCalledOnce());
    expect(fileOf).toHaveBeenCalledExactlyOnceWith("/api/memory/export?format=md");
    expect(saveFile).toHaveBeenCalledWith(blob, "workbench-memory.md");
  });

  it("does the same for JSON, and names the file when the server did not", async () => {
    vi.mocked(fileOf).mockResolvedValue({ blob: new Blob(["{}"]), name: null });
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Export as JSON" }));
    await waitFor(() => expect(saveFile).toHaveBeenCalledOnce());
    expect(fileOf).toHaveBeenCalledExactlyOnceWith("/api/memory/export?format=json");
    expect(vi.mocked(saveFile).mock.calls[0]![1]).toBe("workbench-memory.json");
  });

  it("says why it could not, and saves nothing", async () => {
    vi.mocked(fileOf).mockRejectedValue(new Error("The disk is full."));
    await opened();
    fireEvent.click(screen.getByTestId("memory-export-md"));
    expect(await screen.findByTestId("memory-problem")).toHaveTextContent(
      "Could not export your notes: The disk is full.",
    );
    expect(saveFile).not.toHaveBeenCalled();
    expect(screen.getByTestId("memory-export-md")).toBeEnabled();
  });

  it("is named for the person's own copy, in a section of its own", async () => {
    await opened();
    expect(screen.getByRole("heading", { level: 2, name: "Export or delete" })).toBeInTheDocument();
    expect(
      screen.getByText(/Export your notes as a file, or delete them all\./),
    ).toBeInTheDocument();
    expect(screen.getByTestId("memory-export-md")).toHaveClass("btn");
  });
});

describe("delete all memory", () => {
  beforeEach(() => {
    server.notes = [note({ id: "a" }), note({ id: "b", text: "I live in Lisbon" })];
  });

  it("is not there when there is nothing to delete", async () => {
    server.notes = [];
    await opened();
    expect(screen.queryByTestId("memory-delete-all")).toBeNull();
  });

  it("asks first, in an error box, with the safe answer first and the typing focused", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    const ask = screen.getByRole("group", { name: "Delete all memory?" });
    expect(ask).toHaveClass("notice", "notice--error");
    expect(ask).toHaveTextContent(
      "Delete all 2 notes? They leave every chat at once. This cannot be undone.",
    );
    expect(within(ask).getByRole("textbox", { name: "Type DELETE to confirm" })).toHaveFocus();
    const [keep, go] = within(ask).getAllByRole("button");
    expect(keep).toHaveTextContent("Keep my notes");
    expect(go).toHaveTextContent("Delete all notes");
    expect(go).toHaveClass("btn-danger");
    expect(go).toBeDisabled();
    expect(api).not.toHaveBeenCalledWith("/api/memory/notes?confirm=all", expect.anything());
  });

  it("waits for the word, in any case, and not for nearly the word", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    const typed = screen.getByTestId("memory-delete-all-typed");
    const go = screen.getByTestId("memory-delete-all-go");
    for (const wrong of ["", "delet", "delete it", "deleted", "yes"]) {
      fireEvent.change(typed, { target: { value: wrong } });
      expect(go, wrong).toBeDisabled();
    }
    for (const right of ["DELETE", "delete", "  Delete "]) {
      fireEvent.change(typed, { target: { value: right } });
      expect(go, right).toBeEnabled();
    }
  });

  it("deletes every note, says how many, and shows the empty page", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    fireEvent.change(screen.getByTestId("memory-delete-all-typed"), {
      target: { value: "DELETE" },
    });
    fireEvent.click(screen.getByTestId("memory-delete-all-go"));
    const said = await screen.findByTestId("memory-said");
    expect(said).toHaveAttribute("role", "status");
    expect(said).toHaveTextContent("Deleted 2 notes. Nothing of them is kept.");
    expect(api).toHaveBeenCalledWith("/api/memory/notes?confirm=all", { method: "DELETE" });
    expect(server.notes).toEqual([]);
    expect(screen.getByTestId("memory-empty")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Delete all memory?" })).toBeNull();
    expect(screen.queryByTestId("memory-delete-all")).toBeNull();
    // The button that asked is gone: focus is not left on nothing.
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 2, name: "Your notes" })).toHaveFocus(),
    );
  });

  it("keeps everything on Keep my notes and on Escape, and puts focus back on the button", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    fireEvent.click(screen.getByTestId("memory-delete-all-cancel"));
    expect(screen.queryByRole("group", { name: "Delete all memory?" })).toBeNull();
    await waitFor(() => expect(screen.getByTestId("memory-delete-all")).toHaveFocus());
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    fireEvent.change(screen.getByTestId("memory-delete-all-typed"), {
      target: { value: "DELETE" },
    });
    fireEvent.keyDown(screen.getByTestId("memory-delete-all-typed"), { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Delete all memory?" })).toBeNull();
    // What was typed does not wait there for the next time.
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    expect(screen.getByTestId("memory-delete-all-typed")).toHaveValue("");
    expect(server.notes).toHaveLength(2);
  });

  it("says why when it could not, and keeps the notes and the question", async () => {
    await opened();
    vi.mocked(api).mockImplementation((async (path: string, init?: RequestInit) => {
      if (init?.method === "DELETE") throw new Error("The database is locked.");
      return path === "/api/memory" ? seen() : undefined;
    }) as never);
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    fireEvent.change(screen.getByTestId("memory-delete-all-typed"), {
      target: { value: "DELETE" },
    });
    fireEvent.click(screen.getByTestId("memory-delete-all-go"));
    expect(await screen.findByTestId("memory-problem")).toHaveTextContent(
      "The database is locked.",
    );
    expect(screen.getByRole("group", { name: "Delete all memory?" })).toBeInTheDocument();
    expect(server.notes).toHaveLength(2);
  });
});
