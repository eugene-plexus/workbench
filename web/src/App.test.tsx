import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App, { documentsFromPath, memoryFromPath } from "./App";
import { api, patch, post } from "./lib/api";
import type { Chat, Me, Models } from "./lib/types";

vi.mock("./lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./lib/api")>()),
  api: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

// The pages are other files' business; what App does is the frame around them:
// the top bar, the title, where focus goes.
vi.mock("./components/ChatView", () => ({
  ChatView: ({
    chatId,
    onOpenMemory,
    onOpenDocuments,
  }: {
    chatId: string;
    onOpenMemory: () => void;
    onOpenDocuments: () => void;
  }) => (
    <div>
      <h1>Chat {chatId}</h1>
      <div id="conversation" role="region" aria-label="Conversation" tabIndex={0} />
      <button onClick={onOpenMemory}>Manage notes from the chat</button>
      <button onClick={onOpenDocuments}>Manage documents from the chat</button>
    </div>
  ),
}));
vi.mock("./components/Documents", () => ({
  Documents: ({
    onClose,
    onAsk,
    me,
  }: {
    onClose: () => void;
    onAsk: () => void;
    me?: { owner: boolean; consoleUrl?: string | null } | null;
  }) => (
    <section>
      <h1 id="view-heading">The documents page</h1>
      <p data-testid="documents-reader">
        {me ? `${me.owner ? "owner" : "person"} ${me.consoleUrl ?? "no console"}` : "nobody"}
      </p>
      <button onClick={onAsk}>Ask about the documents</button>
      <button onClick={onClose}>Back to chat</button>
    </section>
  ),
}));
vi.mock("./components/Memory", () => ({
  Memory: ({
    chats,
    chatsLoaded,
    onOpenChat,
    onClose,
  }: {
    chats: { id: string; title: string }[];
    chatsLoaded: boolean;
    onOpenChat: (id: string) => void;
    onClose: () => void;
  }) => (
    <section>
      <h1 id="view-heading">The memory page</h1>
      <p data-testid="memory-chats">
        {chatsLoaded ? "loaded" : "loading"}: {chats.map((c) => c.title).join(", ")}
      </p>
      <button onClick={() => onOpenChat("one")}>Open the note's chat</button>
      <button onClick={onClose}>Back to chat</button>
    </section>
  ),
}));
vi.mock("./components/Tools", () => ({
  Tools: ({ onClose }: { onClose: () => void }) => (
    <section>
      <h1 id="view-heading">The tools page</h1>
      <button onClick={onClose}>Back to chat</button>
    </section>
  ),
}));
vi.mock("./components/JobSites", () => ({
  JobSites: () => (
    <section>
      <h1>The job sites page</h1>
    </section>
  ),
}));
vi.mock("./components/Media", () => ({
  Media: ({ door, onDoor }: { door: string; onDoor: (door: "videos") => void }) => (
    <section>
      <h1>The media page</h1>
      <p data-testid="door">{door}</p>
      <button onClick={() => onDoor("videos")}>To the videos</button>
    </section>
  ),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: Date.now() / 1000,
  running: false,
  readOnly: false,
};
const served: Models = {
  models: [
    {
      id: "local",
      ready: true,
      onDemand: false,
      imageInput: false,
      fileInput: false,
      audioInput: false,
      webSearch: false,
      contextLength: 4096,
    },
  ],
  webSearch: { available: false, reason: null },
};

function serve(over: { me?: Partial<Me>; chats?: Chat[]; models?: Models | Error } = {}) {
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/me") return { ...me, ...over.me };
    if (path === "/api/chats") return { chats: over.chats ?? [] };
    if (path === "/api/models") {
      if (over.models instanceof Error) throw over.models;
      return over.models ?? served;
    }
    throw new Error(`not served: ${path}`);
  }) as never);
}

/** The app, signed in, at an address. */
async function open(path = "/", over: Parameters<typeof serve>[0] = {}) {
  window.history.replaceState(null, "", path);
  serve(over);
  const view = render(<App />);
  await screen.findByRole("navigation", { name: "Workbench" });
  return view;
}

const bar = () => screen.getByRole("navigation", { name: "Workbench" });
// The sidebar has a Back to chat of its own, for a phone.
const back = () => within(screen.getByRole("main")).getByRole("button", { name: "Back to chat" });
const tab = (name: string | RegExp) => within(bar()).getByRole("button", { name });

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(patch).mockReset();
  vi.mocked(post).mockReset().mockResolvedValue(undefined);
  document.title = "Workbench";
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => vi.restoreAllMocks());

it("says it is loading until it knows who is here", async () => {
  vi.mocked(api).mockImplementation(() => new Promise(() => undefined));
  window.history.replaceState(null, "", "/");
  render(<App />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading Workbench…");
});

describe("the top bar", () => {
  it("shows every page, always, as plain labels with a glyph that is only a glyph", async () => {
    await open();
    for (const name of ["Toolbox · Tools", "Bins · Media", "Job sites (your machines)"]) {
      const page = tab(name);
      expect(page).toHaveClass("nav-tab");
      expect(page.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      expect(page).not.toHaveAttribute("aria-current");
    }
    expect(screen.getByTestId("open-media")).toBe(tab("Bins · Media"));
  });

  it("names Job sites with its space, though a phone shows only the two words", async () => {
    await open();
    const sites = tab("Job sites (your machines)");
    expect(sites).toHaveTextContent("Job sites (your machines)");
    expect(within(sites).getByText("(your machines)")).toHaveClass("max-sm:sr-only");
  });

  it("gives the owner no Job sites, as before", async () => {
    await open("/", { me: { owner: true } });
    expect(within(bar()).queryByRole("button", { name: /Job sites/ })).toBeNull();
    expect(tab("Toolbox · Tools")).toBeInTheDocument();
  });

  it("marks the page a person is on, and only that one", async () => {
    await open();
    fireEvent.click(tab("Toolbox · Tools"));
    expect(tab("Toolbox · Tools")).toHaveAttribute("aria-current", "page");
    expect(tab("Bins · Media")).not.toHaveAttribute("aria-current");
    expect(tab("Job sites (your machines)")).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: "The tools page" })).toBeInTheDocument();

    fireEvent.click(tab("Job sites (your machines)"));
    expect(tab("Job sites (your machines)")).toHaveAttribute("aria-current", "page");
    expect(tab("Toolbox · Tools")).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: "The job sites page" })).toBeInTheDocument();

    fireEvent.click(tab("Bins · Media"));
    expect(tab("Bins · Media")).toHaveAttribute("aria-current", "page");
    expect(tab("Job sites (your machines)")).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: "The media page" })).toBeInTheDocument();
  });

  it("leaves the page alone when its own tab is pressed again", async () => {
    await open();
    fireEvent.click(tab("Bins · Media"));
    expect(screen.getByTestId("door")).toHaveTextContent("images");
    fireEvent.click(screen.getByRole("button", { name: "To the videos" }));
    expect(screen.getByTestId("door")).toHaveTextContent("videos");
    // Pressing the tab of the page you are on does not send you back to its first screen.
    fireEvent.click(tab("Bins · Media"));
    expect(screen.getByTestId("door")).toHaveTextContent("videos");
  });

  it("marks nothing while a chat is open, and the chats button is for a phone", async () => {
    await open("/chats/one", { chats: [chat] });
    expect(
      within(bar())
        .queryAllByRole("button")
        .filter((b) => b.getAttribute("aria-current")),
    ).toEqual([]);
    expect(tab("Chats")).toHaveClass("nav-tab", "md:hidden");
  });

  it("shows the page again after Back to chat, with nothing marked", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Toolbox · Tools"));
    fireEvent.click(back());
    expect(tab("Toolbox · Tools")).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
  });
});

describe("the tab's title and where focus goes", () => {
  it("names the page in the tab, and gives the plain name back on leaving it", async () => {
    await open();
    fireEvent.click(tab("Toolbox · Tools"));
    expect(document.title).toBe("Toolbox · Tools · Workbench");
    fireEvent.click(back());
    expect(document.title).toBe("Workbench");
    fireEvent.click(tab("Bins · Media"));
    expect(document.title).toBe("Bins · Media · Workbench");
    fireEvent.click(tab("Job sites (your machines)"));
    expect(document.title).toBe("Job sites · Workbench");
  });

  it("moves focus to the page's heading when a page opens, and not before", async () => {
    await open();
    expect(document.body).toHaveFocus();
    fireEvent.click(tab("Toolbox · Tools"));
    const heading = screen.getByRole("heading", { name: "The tools page" });
    expect(heading).toHaveFocus();
    expect(heading).toHaveAttribute("tabindex", "-1");
  });

  it("finds a page's heading even when the page did not mark it", async () => {
    await open();
    fireEvent.click(tab("Bins · Media"));
    const heading = screen.getByRole("heading", { name: "The media page" });
    expect(heading).toHaveFocus();
    expect(heading).toHaveAttribute("tabindex", "-1");
  });

  it("does not take focus when a page is where the address opened, only when a person changes page", async () => {
    await open("/media/images");
    const heading = screen.getByRole("heading", { name: "The media page" });
    expect(heading).not.toHaveFocus();
    expect(heading).not.toHaveAttribute("tabindex");
    // Changing page is what moves it.
    fireEvent.click(tab("Toolbox · Tools"));
    expect(screen.getByRole("heading", { name: "The tools page" })).toHaveFocus();
  });

  it("does not take focus from a person who has not changed page", async () => {
    await open("/chats/one", { chats: [chat] });
    screen.getByRole("region", { name: "Conversation" }).focus();
    await waitFor(() => expect(screen.getByRole("region", { name: "Conversation" })).toHaveFocus());
    expect(document.title).toBe("Workbench");
  });
});

describe("the skip link", () => {
  it("is the first stop on the page, and goes to the conversation", async () => {
    await open("/chats/one", { chats: [chat] });
    const skip = screen.getByRole("button", { name: "Skip to the conversation" });
    expect(skip).toHaveClass("skip-link");
    const first = document.body.querySelector(
      "button, a[href], input, select, textarea, [tabindex='0']",
    );
    expect(first).toBe(skip);
    fireEvent.click(skip);
    expect(screen.getByRole("region", { name: "Conversation" })).toHaveFocus();
  });

  it("goes to the page's heading when a page is open, and says so", async () => {
    await open();
    fireEvent.click(tab("Toolbox · Tools"));
    const skip = screen.getByRole("button", { name: "Skip to the page" });
    const heading = screen.getByRole("heading", { name: "The tools page" });
    // Focus is somewhere else (the sidebar, say), and the skip link brings it back.
    tab("Keyboard shortcuts").focus();
    expect(heading).not.toHaveFocus();
    fireEvent.click(skip);
    expect(heading).toHaveFocus();
  });
});

describe("the keyboard shortcuts", () => {
  const panel = () => document.getElementById("keyboard-shortcuts")!;

  it("open as an overlay under the bar, not a push that moves the chat down", async () => {
    await open("/chats/one", { chats: [chat] });
    const button = tab("Keyboard shortcuts");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "keyboard-shortcuts");
    expect(document.getElementById("keyboard-shortcuts")).toBeNull();
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(panel()).toHaveClass("absolute", "top-full", "z-20");
    // The panel is beside the bar, in the same box that holds it, and not a sibling in the column.
    expect(panel().parentElement).toBe(bar().parentElement);
    expect(panel().parentElement).toHaveClass("relative");
  });

  it("show each key as a key, grouped by where it works", async () => {
    await open();
    fireEvent.click(tab("Keyboard shortcuts"));
    const keys = Array.from(panel().querySelectorAll("kbd")).map((k) => k.textContent);
    expect(keys.slice(0, 3)).toEqual(["Ctrl", "Alt", "N"]);
    for (const group of ["Anywhere", "Message box", "Chat search", "Editing a message"]) {
      expect(within(panel()).getByRole("heading", { name: group })).toBeInTheDocument();
    }
    for (const what of ["New chat", "Search chats", "Write a message", "Send", "New line"]) {
      expect(within(panel()).getByText(what)).toBeInTheDocument();
    }
    expect(within(panel()).getByText("Save").previousElementSibling).toHaveTextContent(
      "Ctrl+Enter",
    );
  });

  it("name the keys of a Mac as a Mac names them", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
    await open();
    fireEvent.click(tab("Keyboard shortcuts"));
    const keys = Array.from(panel().querySelectorAll("kbd")).map((k) => k.textContent);
    expect(keys.slice(0, 3)).toEqual(["Control", "Option", "N"]);
    expect(keys).toContain("Cmd");
    expect(keys).not.toContain("Ctrl");
  });

  it("close with their button or Escape, and hand focus back to the button that opened them", async () => {
    await open();
    const button = tab("Keyboard shortcuts");
    fireEvent.click(button);
    fireEvent.click(within(panel()).getByRole("button", { name: "Close shortcuts" }));
    expect(document.getElementById("keyboard-shortcuts")).toBeNull();
    expect(button).toHaveFocus();

    fireEvent.click(button);
    within(panel()).getByRole("button", { name: "Close shortcuts" }).focus();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(document.getElementById("keyboard-shortcuts")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("are a button that a phone, which has no keyboard, hides", async () => {
    await open();
    expect(tab("Keyboard shortcuts")).toHaveClass("nav-keys");
    expect(tab("Keyboard shortcuts")).toHaveClass("ml-auto");
  });
});

describe("what App says above the page", () => {
  it("says a change of mode in a warning bar that is dismissed with OK", async () => {
    await open("/", {
      me: {
        installMode: "dev",
        installModeNotice: true,
        installModeChangedAt: "2026-10-10T12:00:00Z",
      },
    });
    const note = screen.getByTestId("install-mode-notice");
    expect(note).toHaveAttribute("role", "status");
    expect(note).toHaveClass("notice", "notice--warn", "notice--bar");
    expect(note).toHaveTextContent("Eugene switched to developer mode");
    expect(note.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    const ok = within(note).getByRole("button", { name: "OK" });
    expect(ok).toHaveClass("btn", "btn-quiet");
    fireEvent.click(ok);
    await waitFor(() => expect(screen.queryByTestId("install-mode-notice")).toBeNull());
    expect(post).toHaveBeenCalledWith("/api/me/mode-seen");
  });

  it("says why a new chat could not be made, floating over the page, until it is dismissed", async () => {
    await open();
    vi.mocked(post).mockRejectedValue(new Error("The disk is full."));
    fireEvent.click(screen.getByTestId("new-chat"));
    const failure = await screen.findByText(/The disk is full\./);
    expect(failure).toHaveAttribute("role", "alert");
    expect(failure).toHaveClass("notice", "notice--error", "notice--float", "absolute", "z-10");
    expect(failure.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    fireEvent.click(within(failure).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/The disk is full\./)).toBeNull();
  });
});

describe("the start page", () => {
  it("is a page with a heading, which asks the first question in plain words", async () => {
    await open();
    const title = screen.getByRole("heading", { level: 1, name: "Start your first chat" });
    expect(title).toHaveClass("text-2xl");
    expect(screen.getByText("Ask anything.")).toHaveClass("text-fg");
    expect(screen.getByText(/Answers keep going even if you close this tab\./)).toBeInTheDocument();
    expect(screen.getByTestId("new-chat-empty")).toHaveClass("btn", "btn-primary");
  });

  it("says to pick a chat once there are some", async () => {
    await open("/", { chats: [chat] });
    // The list arrives after the page does.
    expect(
      await screen.findByRole("heading", { level: 1, name: "Pick a chat, or start one" }),
    ).toBeInTheDocument();
  });

  it("says why there is no model to chat with, in a notice with a glyph", async () => {
    await open("/", { models: { models: [], webSearch: { available: false, reason: null } } });
    const notice = await screen.findByText(/not serving any model you can chat with yet/);
    expect(notice).toHaveClass("notice");
    expect(notice.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("says the cause when the models could not be read, as an error notice", async () => {
    await open("/", { models: new Error("Workbench answered 502.") });
    const failure = await screen.findByText(/Workbench answered 502\./);
    expect(failure).toHaveAttribute("role", "alert");
    expect(failure).toHaveClass("notice", "notice--error");
  });
});

describe("the Memory page", () => {
  it("is a page in the top bar, after Documents and before Job sites, with a glyph that is only a glyph", async () => {
    await open();
    const memory = tab("Memory");
    expect(memory).toHaveClass("nav-tab");
    expect(memory).toHaveAttribute("data-testid", "open-memory");
    expect(memory.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(memory).not.toHaveAttribute("aria-current");
    const names = within(bar())
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(names.indexOf("Memory")).toBe(names.indexOf("Documents") + 1);
    expect(names.indexOf("Job sites (your machines)")).toBe(names.indexOf("Memory") + 1);
  });

  it("is there for the owner as well, who has no Job sites", async () => {
    await open("/", { me: { owner: true } });
    expect(tab("Memory")).toBeInTheDocument();
    expect(within(bar()).queryByRole("button", { name: /Job sites/ })).toBeNull();
  });

  it("opens at /memory, marks the page a person is on, and names the tab", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Memory"));
    expect(window.location.pathname).toBe("/memory");
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
    expect(tab("Memory")).toHaveAttribute("aria-current", "page");
    for (const other of ["Toolbox · Tools", "Bins · Media", "Job sites (your machines)"]) {
      expect(tab(other)).not.toHaveAttribute("aria-current");
    }
    expect(document.title).toBe("Memory · Workbench");
    expect(screen.queryByRole("heading", { name: "Chat one" })).toBeNull();
  });

  it("moves focus to its heading when a person opens it, and not when the address did", async () => {
    await open();
    fireEvent.click(tab("Memory"));
    const heading = screen.getByRole("heading", { name: "The memory page" });
    expect(heading).toHaveFocus();
    expect(heading).toHaveAttribute("tabindex", "-1");
    cleanup();
    await open("/memory");
    const direct = screen.getByRole("heading", { name: "The memory page" });
    expect(direct).not.toHaveFocus();
    expect(direct).not.toHaveAttribute("tabindex");
    expect(tab("Memory")).toHaveAttribute("aria-current", "page");
  });

  it("is given the chats, so a note can say which one it came from", async () => {
    await open("/memory", { chats: [chat] });
    await waitFor(() =>
      expect(screen.getByTestId("memory-chats")).toHaveTextContent("loaded: Build a birdhouse"),
    );
  });

  it("is left alone when its own tab is pressed again", async () => {
    await open();
    fireEvent.click(tab("Memory"));
    const before = window.history.length;
    fireEvent.click(tab("Memory"));
    expect(window.history.length).toBe(before);
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
  });

  it("goes back to the chat it came from, with Back to chat", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Memory"));
    fireEvent.click(back());
    expect(window.location.pathname).toBe("/chats/one");
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
    expect(tab("Memory")).not.toHaveAttribute("aria-current");
    expect(document.title).toBe("Workbench");
  });

  it("goes back to the start page when there was no chat", async () => {
    await open();
    fireEvent.click(tab("Memory"));
    fireEvent.click(back());
    expect(window.location.pathname).toBe("/");
    expect(
      screen.getByRole("heading", { level: 1, name: "Start your first chat" }),
    ).toBeInTheDocument();
  });

  it("opens a note's chat from the page, and the address follows", async () => {
    await open("/memory", { chats: [chat] });
    fireEvent.click(screen.getByRole("button", { name: "Open the note's chat" }));
    expect(window.location.pathname).toBe("/chats/one");
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
    expect(tab("Memory")).not.toHaveAttribute("aria-current");
  });

  it("opens from a chat's own Manage notes", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(screen.getByRole("button", { name: "Manage notes from the chat" }));
    expect(window.location.pathname).toBe("/memory");
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
  });

  it("gives way to another page's tab, and is the page again when that page is left", async () => {
    await open();
    fireEvent.click(tab("Memory"));
    fireEvent.click(tab("Toolbox · Tools"));
    expect(screen.getByRole("heading", { name: "The tools page" })).toBeInTheDocument();
    expect(tab("Memory")).not.toHaveAttribute("aria-current");
    fireEvent.click(tab("Bins · Media"));
    expect(window.location.pathname).toBe("/media/images");
    expect(screen.getByRole("heading", { name: "The media page" })).toBeInTheDocument();
    expect(tab("Memory")).not.toHaveAttribute("aria-current");
    fireEvent.click(tab("Memory"));
    expect(window.location.pathname).toBe("/memory");
    expect(screen.queryByRole("heading", { name: "The media page" })).toBeNull();
  });

  it("follows the browser's back button", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Memory"));
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
    window.history.pushState(null, "", "/chats/one");
    act(() => void window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
    window.history.pushState(null, "", "/memory");
    act(() => void window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
  });

  it("is a page with a chat's address that is not it: a chat opens, never the page", async () => {
    await open("/chats/one", { chats: [chat] });
    expect(screen.queryByRole("heading", { name: "The memory page" })).toBeNull();
    expect(tab("Memory")).not.toHaveAttribute("aria-current");
  });
});

describe("what an address says is the Memory page", () => {
  it.each([
    ["/memory", true],
    ["/memory/", true],
    ["/memory/extra", false],
    ["/memories", false],
    ["/chats/memory", false],
    ["/", false],
  ])("%s is %s", (path, is) => {
    expect(memoryFromPath(path)).toBe(is);
  });
});

describe("the Documents page", () => {
  it("is a page in the top bar, between Media and Memory, with a glyph that is only a glyph", async () => {
    await open();
    const documents = tab("Documents");
    expect(documents).toHaveClass("nav-tab");
    expect(documents).toHaveAttribute("data-testid", "open-documents");
    expect(documents.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(documents).not.toHaveAttribute("aria-current");
    const names = within(bar())
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(names.indexOf("Documents")).toBe(names.indexOf("Bins · Media") + 1);
    expect(names.indexOf("Memory")).toBe(names.indexOf("Documents") + 1);
  });

  it("is there for the owner as well, who has no Job sites", async () => {
    await open("/", { me: { owner: true } });
    expect(tab("Documents")).toBeInTheDocument();
  });

  it("is told who is reading, and where Eugene's console is: a search model is the owner's to get there", async () => {
    await open("/documents", { me: { owner: true, consoleUrl: "http://nas:8279/" } });
    expect(screen.getByTestId("documents-reader")).toHaveTextContent("owner http://nas:8279/");
    cleanup();
    // Someone who is not the owner has no console: they are told who is, and are given no address.
    await open("/documents");
    expect(screen.getByTestId("documents-reader")).toHaveTextContent("person no console");
  });

  it("opens at /documents, marks the page a person is on, and names the tab", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Documents"));
    expect(window.location.pathname).toBe("/documents");
    expect(screen.getByRole("heading", { name: "The documents page" })).toBeInTheDocument();
    expect(tab("Documents")).toHaveAttribute("aria-current", "page");
    for (const other of [
      "Toolbox · Tools",
      "Bins · Media",
      "Memory",
      "Job sites (your machines)",
    ]) {
      expect(tab(other)).not.toHaveAttribute("aria-current");
    }
    expect(document.title).toBe("Documents · Workbench");
    expect(screen.queryByRole("heading", { name: "Chat one" })).toBeNull();
  });

  it("moves focus to its heading when a person opens it, and not when the address did", async () => {
    await open();
    fireEvent.click(tab("Documents"));
    const heading = screen.getByRole("heading", { name: "The documents page" });
    expect(heading).toHaveFocus();
    expect(heading).toHaveAttribute("tabindex", "-1");
    cleanup();
    await open("/documents");
    const direct = screen.getByRole("heading", { name: "The documents page" });
    expect(direct).not.toHaveFocus();
    expect(tab("Documents")).toHaveAttribute("aria-current", "page");
  });

  it("is left alone when its own tab is pressed again", async () => {
    await open();
    fireEvent.click(tab("Documents"));
    const before = window.history.length;
    fireEvent.click(tab("Documents"));
    expect(window.history.length).toBe(before);
    expect(screen.getByRole("heading", { name: "The documents page" })).toBeInTheDocument();
  });

  it("goes back to the chat it came from, with Back to chat, and to the start page when there was none", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Documents"));
    fireEvent.click(back());
    expect(window.location.pathname).toBe("/chats/one");
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
    expect(tab("Documents")).not.toHaveAttribute("aria-current");
    expect(document.title).toBe("Workbench");
    cleanup();
    await open();
    fireEvent.click(tab("Documents"));
    fireEvent.click(back());
    expect(window.location.pathname).toBe("/");
  });

  it("opens from a chat's own Manage documents", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(screen.getByRole("button", { name: "Manage documents from the chat" }));
    expect(window.location.pathname).toBe("/documents");
    expect(screen.getByRole("heading", { name: "The documents page" })).toBeInTheDocument();
  });

  it("gives way to another page's tab, and is the page again when that page is left", async () => {
    await open();
    fireEvent.click(tab("Documents"));
    fireEvent.click(tab("Memory"));
    expect(screen.getByRole("heading", { name: "The memory page" })).toBeInTheDocument();
    expect(tab("Documents")).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("heading", { name: "The documents page" })).toBeNull();
    fireEvent.click(tab("Documents"));
    expect(window.location.pathname).toBe("/documents");
    expect(screen.queryByRole("heading", { name: "The memory page" })).toBeNull();
    fireEvent.click(tab("Bins · Media"));
    expect(window.location.pathname).toBe("/media/images");
    expect(screen.queryByRole("heading", { name: "The documents page" })).toBeNull();
  });

  it("follows the browser's back button", async () => {
    await open("/chats/one", { chats: [chat] });
    fireEvent.click(tab("Documents"));
    window.history.pushState(null, "", "/chats/one");
    act(() => void window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "Chat one" })).toBeInTheDocument();
    window.history.pushState(null, "", "/documents");
    act(() => void window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "The documents page" })).toBeInTheDocument();
  });

  it("starts a chat with Documents on from Ask about your documents, and opens it", async () => {
    vi.mocked(post).mockResolvedValue({ ...chat, id: "fresh" });
    vi.mocked(patch).mockResolvedValue({ ...chat, id: "fresh", settings: { documents: "on" } });
    await open("/documents");
    fireEvent.click(screen.getByRole("button", { name: "Ask about the documents" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chats/fresh"));
    expect(post).toHaveBeenCalledWith("/api/chats", {});
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/fresh", {
      settings: { documents: "on" },
    });
    expect(screen.getByRole("heading", { name: "Chat fresh" })).toBeInTheDocument();
    expect(tab("Documents")).not.toHaveAttribute("aria-current");
  });

  it("opens the chat all the same, and says so, when Documents could not be turned on in it", async () => {
    vi.mocked(post).mockResolvedValue({ ...chat, id: "fresh" });
    vi.mocked(patch).mockRejectedValue(new Error("Workbench answered 500."));
    await open("/documents");
    fireEvent.click(screen.getByRole("button", { name: "Ask about the documents" }));
    await waitFor(() => expect(window.location.pathname).toBe("/chats/fresh"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The chat was made, but Documents could not be turned on in it: Workbench answered 500.",
    );
  });

  it("stays on the screen, and says why, when the chat could not be made", async () => {
    vi.mocked(post).mockRejectedValue(new Error("Workbench answered 502."));
    await open("/documents");
    fireEvent.click(screen.getByRole("button", { name: "Ask about the documents" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
    expect(window.location.pathname).toBe("/documents");
    expect(patch).not.toHaveBeenCalled();
  });
});

describe("six pages in the top bar of a phone", () => {
  it("keeps to one row that scrolls sideways below 640 px, and wraps above it", async () => {
    await open();
    const classes = bar().className.split(/\s+/);
    for (const wanted of ["flex-wrap", "max-sm:flex-nowrap", "max-sm:overflow-x-auto"]) {
      expect(classes, wanted).toContain(wanted);
    }
    // No tab shrinks to make room: each keeps its words whole.
    expect(classes).toContain("max-sm:[&>*]:shrink-0");
    // The row holds the words a tab hides from the eye (they are positioned out of the
    // way), so they scroll with it and do not make the whole page wider than the window.
    expect(classes).toContain("relative");
  });

  it("brings the page a person is on into view, whether they opened it or the address did", async () => {
    const into = vi.fn();
    HTMLElement.prototype.scrollIntoView = into;
    try {
      await open("/documents");
      expect(into).toHaveBeenCalled();
      expect(into.mock.instances.at(-1)).toBe(tab("Documents"));
      expect(into).toHaveBeenLastCalledWith({ block: "nearest", inline: "nearest" });
      into.mockClear();
      fireEvent.click(tab("Memory"));
      expect(into.mock.instances.at(-1)).toBe(tab("Memory"));
    } finally {
      delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
    }
  });

  it("is not stopped by a browser that cannot scroll an element into view", async () => {
    await open("/documents");
    expect(screen.getByRole("heading", { name: "The documents page" })).toBeInTheDocument();
  });
});

describe("what an address says is the Documents page", () => {
  it.each([
    ["/documents", true],
    ["/documents/", true],
    ["/documents/extra", false],
    ["/document", false],
    ["/chats/documents", false],
    ["/memory", false],
    ["/", false],
  ])("%s is %s", (path, is) => {
    expect(documentsFromPath(path)).toBe(is);
  });
});
