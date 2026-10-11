import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../lib/api";
import type { Chat, Me } from "../lib/types";
import { Sidebar } from "./Sidebar";

vi.mock("../lib/api", () => ({ api: vi.fn() }));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const owner: Me = { ...me, owner: true, ownerReadsChats: true };

const chat = (over: Partial<Chat>): Chat => ({
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: Date.now() / 1000,
  running: false,
  readOnly: false,
  ...over,
});

function sidebar(
  who: Me,
  chatsLoaded?: boolean,
  over: {
    chats?: Chat[];
    current?: string | null;
    error?: string | null;
    onRetry?: () => void;
  } = {},
) {
  render(
    <Sidebar
      me={who}
      chats={over.chats ?? []}
      chatsLoaded={chatsLoaded}
      current={over.current ?? null}
      onOpen={vi.fn()}
      onOpenMedia={vi.fn()}
      onClose={vi.fn()}
      onNew={vi.fn()}
      onSignOut={vi.fn()}
      creating={false}
      error={over.error ?? null}
      onRetry={over.onRetry ?? vi.fn()}
    />,
  );
}

beforeEach(() => vi.mocked(api).mockReset());

it("says Loading chats before the first list, and never No chats yet", () => {
  sidebar(me, false);
  expect(screen.getByRole("status")).toHaveTextContent("Loading chats…");
  expect(screen.queryByText(/No chats yet/)).toBeNull();
});

it("says what to do once the list has loaded empty", () => {
  sidebar(me, true);
  expect(screen.getByText("No chats yet. Choose New chat to start one.")).toBeInTheDocument();
  expect(screen.queryByText("Loading chats…")).toBeNull();
});

it("shows people loading, then a person's chats with aria-expanded, and No chats for none", async () => {
  let release: (v: unknown) => void = () => undefined;
  vi.mocked(api).mockImplementation((path: string) => {
    if (path === "/api/people") {
      return new Promise((resolve) => {
        release = resolve;
      }) as never;
    }
    return Promise.resolve({ chats: [] }) as never;
  });
  sidebar(owner, true);
  const details = screen.getByText(/People's chats and media/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  expect(screen.getByText("Loading people…")).toHaveAttribute("role", "status");
  release({ people: [{ sub: "bo", name: "Bo", chats: 0, media: 0 }] });
  const person = await screen.findByRole("button", { name: "Bo (0)" });
  expect(screen.queryByText("Loading people…")).toBeNull();
  expect(person).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(person);
  await waitFor(() => expect(person).toHaveAttribute("aria-expanded", "true"));
  expect(screen.getByText("No chats.")).toBeInTheDocument();
});

describe("the open chat (the blue rail)", () => {
  const chats = [
    chat({ id: "one", title: "Build a birdhouse" }),
    chat({ id: "two", title: "Recipes" }),
  ];

  it("has a 3 px accent rail, a tint and a heavier weight, and the others have none", () => {
    sidebar(me, true, { chats, current: "one" });
    const open = screen.getByRole("button", { name: "Build a birdhouse" });
    expect(open).toHaveAttribute("aria-current", "page");
    expect(open).toHaveClass("border-l-[3px]", "border-accent", "bg-tint", "font-medium");
    const other = screen.getByRole("button", { name: "Recipes" });
    expect(other).not.toHaveAttribute("aria-current");
    expect(other).not.toHaveClass("border-accent", "bg-tint", "font-medium");
  });

  it("keeps its text where the others' text is: the rail's width comes out of the padding", () => {
    sidebar(me, true, { chats, current: "one" });
    // 3 px of border and 5 px of padding, against 8 px of padding and no border.
    expect(screen.getByRole("button", { name: "Build a birdhouse" })).toHaveClass("pl-[5px]");
    const other = screen.getByRole("button", { name: "Recipes" });
    expect(other).toHaveClass("pl-2");
    expect(other).not.toHaveClass("border-l-[3px]");
  });

  it("truncates a long title inside the row, and names it in a tooltip", () => {
    const long = "A very long chat name that would otherwise run out of the sidebar and widen it";
    sidebar(me, true, { chats: [chat({ title: long })] });
    const row = screen.getByRole("button", { name: long });
    expect(row).toHaveAttribute("title", long);
    expect(screen.getByText(long)).toHaveClass("truncate");
  });

  it("pulses a dot while an answer is being written, and keeps it in forced colours", () => {
    sidebar(me, true, { chats: [chat({ running: true })] });
    const dot = screen.getByTestId("chat-running");
    expect(dot).toHaveClass("animate-pulse", "run-dot");
    expect(dot).toHaveAttribute("role", "img");
  });
});

describe("who can see what, said on the screen (not only in a tooltip)", () => {
  const dev = "Eugene's owner can see all tool information, including what your job sites return.";
  const production = "Eugene's owner cannot see what your job sites' tools return.";

  it.each([
    ["dev", dev, "Developer mode"],
    ["production", production, "Production mode"],
  ] as const)("says it in %s mode, as readable as in the other", (mode, said, label) => {
    sidebar({ ...me, installMode: mode }, true);
    const sentence = screen.getByText(said);
    expect(sentence).toHaveAttribute("id", "install-mode-explained");
    // Not for a screen reader alone: a keyboard and a touch screen read it too.
    expect(sentence).not.toHaveClass("sr-only");
    expect(sentence).toHaveClass("help", "block");
    const badge = screen.getByTestId("install-mode");
    expect(badge).toHaveTextContent(label);
    expect(badge).toHaveAccessibleDescription(said);
    expect(badge).toHaveClass("badge");
  });

  it("marks developer mode in the error colours and production mode in none", () => {
    sidebar({ ...me, installMode: "dev" }, true);
    expect(screen.getByTestId("install-mode")).toHaveClass("text-error", "bg-error-bg");
  });

  it("leaves production mode as a neutral badge", () => {
    sidebar({ ...me, installMode: "production" }, true);
    expect(screen.getByTestId("install-mode")).not.toHaveClass("text-error");
  });

  it("warns a person that the owner reads their chats, in a notice with a glyph", () => {
    sidebar({ ...me, ownerReadsChats: true }, true);
    const warning = screen.getByTestId("owner-reads");
    expect(warning).toHaveClass("notice", "notice--warn");
    expect(warning).toHaveTextContent("The owner of this Workbench can read your chats and media.");
    expect(warning.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("does not say it to the owner", () => {
    sidebar(owner, true);
    expect(screen.queryByTestId("owner-reads")).toBeNull();
  });
});

describe("the owner's footer", () => {
  it("opens the console in another tab, as a link that is a real target", () => {
    sidebar({ ...owner, consoleUrl: "http://nas:8279/" }, true);
    const link = screen.getByRole("link", { name: /Open Eugene console/ });
    expect(link).toHaveAttribute("href", "http://nas:8279/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveClass("link", "min-h-7");
  });

  it("has no console link for a person who is not the owner", () => {
    sidebar({ ...me, consoleUrl: "http://nas:8279/" }, true);
    expect(screen.queryByRole("link", { name: /Open Eugene console/ })).toBeNull();
  });

  it("signs out with a quiet button that is muted until it is pointed at", () => {
    sidebar(me, true);
    const out = screen.getByRole("button", { name: "Sign out" });
    expect(out).toHaveClass("btn", "btn-quiet", "text-muted");
  });
});

describe("the search box and the list's failure", () => {
  it("clears with a button that is a real target, and that keeps its name", () => {
    sidebar(me, true, { chats: [chat({})] });
    expect(screen.queryByRole("button", { name: "Clear chat search" })).toBeNull();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "bird" } });
    expect(screen.getByRole("button", { name: "Clear chat search" })).toHaveClass("icon-btn");
  });

  it("draws its own focus on the box around the input, which keeps a hidden outline", () => {
    sidebar(me, true);
    const input = screen.getByRole("searchbox");
    expect(input).toHaveClass("focus:outline-hidden");
    // Only while focused: a transparent outline that is always there is drawn, as a
    // box that looks like focus, in Windows high contrast.
    expect(input).not.toHaveClass("outline-hidden");
    // The edge and the ring follow focus anywhere inside the box.
    expect(input.parentElement).toHaveClass(
      "focus-within:border-accent",
      "focus-within:ring-1",
      "focus-within:ring-accent",
    );
  });

  it("shows why the list did not load as an error notice with a way to try again", () => {
    const retry = vi.fn();
    sidebar(me, false, { error: "Workbench answered 502.", onRetry: retry });
    const failure = screen.getByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Workbench answered 502.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
