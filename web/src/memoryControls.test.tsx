/**
 * Every control on the Memory screens has a name a screen reader says and a
 * `data-testid` a check can find it by (MR1 asks for both). Each screen is drawn
 * in each state it has, and every control on it is held to both: a control added
 * later with neither fails here, by name, so nobody has to remember to look.
 */

import { fireEvent, getRoles, render, screen, waitFor, within } from "@testing-library/react";

import { Memory } from "./components/Memory";
import { MemoryChip, MemoryDrawer } from "./components/MemoryChip";
import { MemoryToast, type ToastOutcome } from "./components/MemoryToast";
import { MessageView } from "./components/MessageView";
import { api, del, patch, post, put } from "./lib/api";
import type { Chat, ChatMemoryView, MemoryNote, MemoryPage, Message } from "./lib/types";
import { held, note, notSent, owners, page, prefs, sending } from "./test-memory";

vi.mock("./lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

// --- what counts as a control, and what each one needs ---

/** What a person can press, type into or choose. */
const CONTROLS = [
  "button",
  "a[href]",
  'input:not([type="hidden"])',
  "select",
  "textarea",
  "summary",
  '[role="switch"]',
  '[role="button"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/** The controls in `root`, and `root` itself when it is one. */
const controlsIn = (root: Element): HTMLElement[] => [
  ...(root.matches(CONTROLS) ? [root as HTMLElement] : []),
  ...root.querySelectorAll<HTMLElement>(CONTROLS),
];

function look(el: Element): string {
  const id = el.getAttribute("data-testid");
  const words = (el.getAttribute("aria-label") ?? el.textContent ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, 40);
  return `<${el.tagName.toLowerCase()}${id ? ` data-testid="${id}"` : ""}> "${words}"`;
}

/** The roles in `root`, hidden things included (the typings leave that option out). */
const rolesOf = getRoles as (
  root: HTMLElement,
  options: { hidden: boolean },
) => Record<string, HTMLElement[]>;

/** What each of `controls` lacks: a name (as the browser computes one) and a test id. */
function lacking(controls: HTMLElement[], scope: HTMLElement): string[] {
  const named = new Set<Element>();
  for (const role of Object.keys(rolesOf(scope, { hidden: true }))) {
    for (const el of within(scope).queryAllByRole(role, { name: /\S/, hidden: true })) {
      named.add(el);
    }
  }
  // The role table the queries use gives a `summary` no role, and a browser names it
  // from what is inside it.
  const nameless = (el: HTMLElement) =>
    el.tagName === "SUMMARY"
      ? (el.getAttribute("aria-label") ?? el.textContent ?? "").trim() === ""
      : !named.has(el);
  return controls.flatMap((el) => {
    const needs = [
      ...(nameless(el) ? ["accessible name"] : []),
      ...(el.hasAttribute("data-testid") ? [] : ["data-testid"]),
    ];
    return needs.length > 0 ? [`${look(el)} has no ${needs.join(" and no ")}`] : [];
  });
}

/** Everything wrong with every control on what is drawn: nothing, when it is right. */
const wrong = (view: { container: HTMLElement }) =>
  lacking(controlsIn(view.container), view.container);

// --- the gate can fail ---

describe("the check itself", () => {
  it("names each control that has no name, or no test id, or neither", () => {
    const view = render(
      <div>
        <button type="button">Save</button>
        <button type="button" data-testid="fine">
          Fine
        </button>
        <input />
        <a href="/elsewhere">
          <svg aria-hidden="true" />
        </a>
        <details>
          <summary>More</summary>
        </details>
        <details>
          <summary data-testid="empty" />
        </details>
      </div>,
    );
    expect(wrong(view)).toEqual([
      '<button> "Save" has no data-testid',
      '<input> "" has no accessible name and no data-testid',
      '<a> "" has no accessible name and no data-testid',
      '<summary> "More" has no data-testid',
      '<summary data-testid="empty"> "" has no accessible name',
    ]);
  });

  it("accepts a name from a label, an aria-label or the words inside", () => {
    const view = render(
      <div>
        <label htmlFor="a">Your name</label>
        <input id="a" data-testid="a" />
        <button type="button" aria-label="Close" data-testid="b" />
        <button type="button" data-testid="c">
          Open
        </button>
      </div>,
    );
    expect(wrong(view)).toEqual([]);
  });
});

// --- the page ---

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

/** One of each kind, pinned or not, from each source, and from a chat that is gone. */
const NOTES: MemoryNote[] = [
  note({ id: "n1", text: "Keep answers short", kind: "instruction" }),
  note({
    id: "n2",
    text: "I use metric units",
    kind: "fact",
    pinned: true,
    source: "said",
    sourceChatId: "c1",
    sourceMessageId: "m1",
  }),
  note({
    id: "n3",
    text: "I prefer Python",
    kind: "preference",
    source: "said",
    sourceChatId: "gone",
  }),
  note({
    id: "n4",
    text: "A birdhouse out of cedar",
    kind: "project",
    source: "button",
    sourceChatId: "c1",
    sourceMessageId: "m2",
  }),
  note({ id: "n5", text: "My flight leaves on March 3", kind: "event" }),
];

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(del)
    .mockReset()
    .mockResolvedValue(undefined as never);
  vi.mocked(patch)
    .mockReset()
    .mockResolvedValue({ note: note() } as never);
  vi.mocked(post)
    .mockReset()
    .mockResolvedValue({ note: note(), created: true } as never);
  vi.mocked(put)
    .mockReset()
    .mockResolvedValue(prefs() as never);
});

async function opened(data: MemoryPage) {
  vi.mocked(api).mockResolvedValue(data as never);
  const view = render(<Memory chats={chats} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
  return view;
}

const ALL = (over: Parameters<typeof page>[1] = {}) => page(NOTES, over);

describe("the Memory page", () => {
  it("with notes of every kind, from every source: every control", async () => {
    const view = await opened(
      ALL({
        ownerReadsChats: true,
        usage: { notSent: 2 },
        prefs: prefs({
          external: "never",
          budgetTokens: 800,
          effective: { external: "never", externalSetBy: "you", budgetTokens: 800 },
        }),
      }),
    );
    // A link back to a chat, and the buttons that offer the defaults, are among them.
    expect(screen.getAllByRole("link", { name: "Build a birdhouse" })).toHaveLength(2);
    expect(screen.getByTestId("memory-external-default")).toBeInTheDocument();
    expect(screen.getByTestId("memory-budget-default")).toBeInTheDocument();
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(30);
    expect(wrong(view)).toEqual([]);
  });

  it("with a note being edited", async () => {
    const view = await opened(ALL());
    fireEvent.click(screen.getAllByTestId("memory-note-edit")[0]!);
    expect(screen.getByTestId("memory-edit-text")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("with the question before every note is deleted", async () => {
    const view = await opened(ALL());
    fireEvent.click(screen.getByTestId("memory-delete-all"));
    expect(screen.getByTestId("memory-delete-all-confirm")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("with a deleted note, and Undo", async () => {
    const view = await opened(ALL());
    fireEvent.click(screen.getAllByTestId("memory-note-delete")[0]!);
    await screen.findByTestId("memory-deleted");
    expect(wrong(view)).toEqual([]);
  });

  it("with nothing remembered yet: the two boxes", async () => {
    const view = await opened(page([]));
    expect(screen.getByTestId("memory-empty")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("where the owner turned Memory off and there are no notes", async () => {
    const view = await opened(page([], { installEnabled: false }));
    expect(screen.getByTestId("memory-owner-off")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("where the owner turned Memory off and the notes stay", async () => {
    const view = await opened(ALL({ installEnabled: false }));
    expect(screen.queryByTestId("memory-note-edit")).toBeNull();
    expect(wrong(view)).toEqual([]);
  });

  it("where the person turned Memory off", async () => {
    const view = await opened(
      ALL({
        prefs: prefs({
          enabled: false,
          effective: { enabled: false, disabledBy: "you" },
        }),
      }),
    );
    expect(screen.getByTestId("memory-use-switch")).not.toBeChecked();
    expect(wrong(view)).toEqual([]);
  });

  it("where the owner's word on outside models is final", async () => {
    const view = await opened(
      ALL({ prefs: prefs({ effective: { external: "never", externalSetBy: "owner" } }) }),
    );
    expect(screen.getByTestId("memory-external-ask")).toBeDisabled();
    expect(wrong(view)).toEqual([]);
  });

  it("when the notes could not be read", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    const view = render(
      <Memory chats={chats} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />,
    );
    await screen.findByRole("alert");
    expect(wrong(view)).toEqual([]);
  });

  it("when the notes could not be read again after a change", async () => {
    const view = await opened(ALL());
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    fireEvent.click(screen.getAllByTestId("memory-note-pin")[0]!);
    await screen.findByText("Could not read your notes again");
    expect(wrong(view)).toEqual([]);
  });
});

// --- a chat's chip and drawer ---

const outside = (over: Partial<NonNullable<ReturnType<typeof sending>["outside"]>> = {}) => ({
  choice: "ask" as const,
  setBy: "default" as const,
  canSendForThisChat: false,
  sendForThisChat: false,
  ...over,
});

const VIEWS: [string, ChatMemoryView][] = [
  ["sending", sending()],
  [
    "sending, the notes changed and some are left out",
    sending({ changed: true, counts: { notes: 3, sent: 1, notSent: 2 }, notSent: [notSent()] }),
  ],
  [
    "sending outside, for this chat",
    sending({ outside: outside({ canSendForThisChat: true, sendForThisChat: true }) }),
  ],
  ["sending outside, always", sending({ outside: outside({ choice: "always", setBy: "you" }) })],
  [
    "held for an outside model that may be sent to",
    held("outside_model", "Your notes were not sent.", {
      outside: outside({ canSendForThisChat: true }),
    }),
  ],
  [
    "held for an outside model, by the owner",
    held("outside_model", "Your notes were not sent.", {
      outside: outside({ choice: "never", setBy: "owner" }),
    }),
  ],
  ["held by the person's switch", held("person_off", "Memory is off for you.")],
  ["held by the owner's switch", held("install_off", "The owner turned Memory off.")],
  ["held by the chat's switch", held("chat_off", "Memory is off here.", { chatSwitch: "off" })],
  ["held: no model", held("no_model", "This chat has no model yet.")],
  [
    "held: the model's place is not known",
    held("model_unknown", "Where this model runs is unknown."),
  ],
  ["held: a small window", held("window_too_small", "This model's window is small.")],
  ["held: no notes", held("no_notes", "You have no notes yet.")],
  ["someone else's chat", owners()],
];

function drawer(view: ChatMemoryView | null, error: string | null = null) {
  return render(
    <MemoryDrawer
      chatId="c1"
      view={view}
      error={error}
      onChat={vi.fn()}
      onReload={vi.fn().mockResolvedValue(undefined)}
      onAdopt={vi.fn()}
      onManage={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

describe("the Memory drawer", () => {
  it.each(VIEWS)("%s", (_name, view) => {
    expect(wrong(drawer(view))).toEqual([]);
  });

  it("while it reads, and when it could not", () => {
    expect(wrong(drawer(null))).toEqual([]);
    expect(wrong(drawer(null, "Workbench answered 502."))).toEqual([]);
  });

  it("with the exact text it sent", () => {
    const view = drawer(sending());
    expect(screen.getByTestId("memory-exact")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });
});

describe("the Memory chip", () => {
  it.each(VIEWS)("%s", (_name, view) => {
    const shown = render(<MemoryChip view={view} failed={false} open={false} onToggle={vi.fn()} />);
    expect(wrong(shown)).toEqual([]);
  });

  it("before it has read, and when it could not", () => {
    expect(
      wrong(render(<MemoryChip view={null} failed={false} open={false} onToggle={vi.fn()} />)),
    ).toEqual([]);
    expect(
      wrong(render(<MemoryChip view={null} failed open={false} onToggle={vi.fn()} />)),
    ).toEqual([]);
  });
});

// --- the toast ---

const kept = note({ id: "n1", text: "my code word is HELIOTROPE" });

const TOASTS: [string, ToastOutcome][] = [
  ["a note kept", { kind: "remembered", note: kept, created: true }],
  ["words that were already a note", { kind: "remembered", note: kept, created: false }],
  [
    "a refusal",
    {
      kind: "refused",
      refusal: { message: "That looks like a card number.", reason: "secret" },
      messageSent: true,
    },
  ],
  [
    "notes to choose from",
    {
      kind: "forget",
      words: "my cat",
      candidates: [kept, note({ id: "n2", text: "my cat is named Miso" })],
    },
  ],
  ["no note to choose from", { kind: "forget", words: "my cat", candidates: [] }],
];

function toast(outcome: ToastOutcome) {
  return render(
    <MemoryToast outcome={outcome} onManage={vi.fn()} onChanged={vi.fn()} onClose={vi.fn()} />,
  );
}

describe("the toast", () => {
  it.each(TOASTS)("%s", (_name, outcome) => {
    expect(wrong(toast(outcome))).toEqual([]);
  });

  it("after a note is taken back, and after one is deleted and can be had again", async () => {
    const first = toast(TOASTS[0]![1]);
    fireEvent.click(screen.getByTestId("memory-toast-undo"));
    await screen.findByText("Undone.");
    expect(wrong(first)).toEqual([]);
    first.unmount();

    const second = toast(TOASTS[3]![1]);
    fireEvent.click(screen.getAllByTestId("memory-toast-forget")[0]!);
    await screen.findByText("Deleted:");
    expect(screen.getByTestId("memory-toast-undo")).toBeInTheDocument();
    expect(wrong(second)).toEqual([]);
  });
});

// --- Remember this, on a message ---

const message = (over: Partial<Message> = {}): Message => ({
  id: "m1",
  seq: 1,
  role: "user",
  content: "I use metric units",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

/** What Remember this adds to a message: its button, and what the button opens. The
 * message's own controls are not this slice's. */
function remember(shown: Message, memory: { off: "you" | null }) {
  const view = render(
    <MessageView
      chatId="c1"
      message={shown}
      progress={null}
      busy={false}
      readOnly={false}
      onChanged={vi.fn()}
      memory={{ ...memory, onSaved: vi.fn(), onManage: vi.fn() }}
    />,
  );
  const mine = () =>
    [...view.container.querySelectorAll('[data-testid^="remember-"]')].flatMap(controlsIn);
  return { view, mine };
}

describe("Remember this", () => {
  it("is a button with a name and a test id, on a person's message and on an answer", () => {
    const person = remember(message(), { off: null });
    expect(person.mine()).toHaveLength(1);
    expect(lacking(person.mine(), person.view.container)).toEqual([]);
    person.view.unmount();
    const answer = remember(message({ id: "a1", role: "assistant", content: "Metric it is." }), {
      off: null,
    });
    expect(answer.mine()).toHaveLength(1);
    expect(lacking(answer.mine(), answer.view.container)).toEqual([]);
  });

  it("with the box for a message that is too long for a note", () => {
    const long = remember(message({ content: "A long message, said in many words. ".repeat(10) }), {
      off: null,
    });
    fireEvent.click(screen.getByTestId("remember-this"));
    expect(screen.getByTestId("remember-editor")).toBeInTheDocument();
    // The button, the box, its two buttons: all of them are checked.
    expect(long.mine().length).toBeGreaterThanOrEqual(4);
    expect(lacking(long.mine(), long.view.container)).toEqual([]);
  });

  it("with what it says when the person has turned Memory off", () => {
    const off = remember(message(), { off: "you" });
    fireEvent.click(screen.getByTestId("remember-this"));
    expect(screen.getByTestId("remember-off")).toBeInTheDocument();
    expect(off.mine().length).toBeGreaterThanOrEqual(2);
    expect(lacking(off.mine(), off.view.container)).toEqual([]);
  });
});
