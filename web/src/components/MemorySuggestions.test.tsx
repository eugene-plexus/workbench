import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post, put } from "../lib/api";
import type { MemoryPage, MemorySuggestion } from "../lib/types";
import { note, page, prefs } from "../test-memory";
import { Memory } from "./Memory";
import { MemorySettings } from "./MemorySettings";
import { MemoryOffer } from "./MemoryOffer";

// Connections (MR9b) has its own tests; this page's do not serve it.
vi.mock("./Connections", () => ({ Connections: () => null }));
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

function suggestion(over: Partial<MemorySuggestion> = {}): MemorySuggestion {
  return {
    ...note({ id: "s1", text: "Works as a nurse.", source: "suggested", sourceChatId: "c1" }),
    quote: "I'm a nurse working nights",
    replaces: null,
    proposedTags: [],
    ...over,
  };
}

let shown: MemoryPage;

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(put).mockReset();
  shown = page([note({ id: "n1", text: "Lives in Paris." })], {
    suggestions: [suggestion()],
  });
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/memory") return shown;
    if (path === "/api/memory/offer") return { show: true, chats: 5 };
    throw new Error(`unexpected ${path}`);
  }) as never);
  vi.mocked(post).mockResolvedValue({} as never);
  vi.mocked(put).mockResolvedValue(prefs() as never);
});

const chats = [
  {
    id: "c1",
    title: "Night shift meals",
    model: "local",
    search: false,
    settings: {},
    createdAt: 1,
    updatedAt: 1,
    running: false,
    readOnly: false,
  },
];

async function opened() {
  render(<Memory chats={chats} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
}

async function openedSettings() {
  render(<MemorySettings onOpenMemory={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your memory settings…")).toBeNull());
}

describe("suggested notes", () => {
  it("shows each with the person's own words and its chat, and keeps or turns it away", async () => {
    await opened();
    const item = screen.getByTestId("memory-suggestion");
    expect(within(item).getByTestId("memory-suggestion-words")).toHaveTextContent(
      "Works as a nurse.",
    );
    expect(within(item).getByTestId("memory-suggestion-quote")).toHaveTextContent(
      "You wrote: “I'm a nurse working nights” From Night shift meals",
    );
    fireEvent.click(within(item).getByTestId("memory-suggestion-keep"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/suggestions/s1/keep", { replace: false }),
    );
    fireEvent.click(
      within(screen.getByTestId("memory-suggestion")).getByTestId("memory-suggestion-dismiss"),
    );
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/suggestions/s1/dismiss", {}),
    );
  });

  it("keeps the person's edit of the words and the kind", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-suggestion-edit"));
    fireEvent.change(screen.getByTestId("memory-suggestion-text"), {
      target: { value: "A night-shift nurse." },
    });
    fireEvent.change(screen.getByTestId("memory-suggestion-kind"), {
      target: { value: "project" },
    });
    fireEvent.click(screen.getByTestId("memory-suggestion-keep"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/suggestions/s1/keep", {
        replace: false,
        text: "A night-shift nurse.",
        kind: "project",
      }),
    );
  });

  it("offers Keep new and Keep both for one that replaces a note", async () => {
    shown = page([], {
      suggestions: [
        suggestion({
          text: "Lives in Lyon.",
          replaces: note({ id: "n1", text: "Lives in Paris." }),
        }),
      ],
    });
    await opened();
    expect(screen.getByTestId("memory-suggestion-replaces")).toHaveTextContent(
      "Replaces your note: Lives in Paris.",
    );
    expect(screen.queryByTestId("memory-suggestion-keep")).toBeNull();
    fireEvent.click(screen.getByTestId("memory-suggestion-keep-new"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/suggestions/s1/keep", { replace: true }),
    );
  });

  it("keeps a proposed tag that is on the list, and one that is not only when Add to my tags is chosen", async () => {
    shown = page([], {
      suggestions: [
        suggestion({
          proposedTags: [
            { tag: "health", listed: true },
            { tag: "night shifts", listed: false },
          ],
        }),
      ],
    });
    await opened();
    const item = screen.getByTestId("memory-suggestion");
    const add = within(item).getByRole("checkbox", { name: /Add to my tags/ });
    expect(add).not.toBeChecked();
    expect(within(item).getAllByTestId("memory-suggestion-tag")).toHaveLength(2);
    // Left alone, the tag that is not on the list is dropped.
    fireEvent.click(within(item).getByTestId("memory-suggestion-keep"));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith("/api/memory/suggestions/s1/keep", {
        replace: false,
        tags: ["health"],
      }),
    );
    // Chosen, it is kept and joins the list.
    fireEvent.click(add);
    expect(add).toBeChecked();
    fireEvent.click(within(item).getByTestId("memory-suggestion-keep"));
    await waitFor(() =>
      expect(post).toHaveBeenLastCalledWith("/api/memory/suggestions/s1/keep", {
        replace: false,
        tags: ["health", "night shifts"],
      }),
    );
  });

  it("does not offer Procedure as a kind to keep it as", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-suggestion-edit"));
    const kinds = within(screen.getByTestId("memory-suggestion-kind"))
      .getAllByRole("option")
      .map((option) => option.textContent);
    expect(kinds).toEqual(["Fact", "Preference", "Instruction", "Project", "Event"]);
  });

  it("is not there when nothing is waiting", async () => {
    shown = page([note()]);
    await opened();
    expect(screen.queryByTestId("memory-suggestions")).toBeNull();
  });
});

describe("Still true?", () => {
  it("asks about the project that is due and sends the answer", async () => {
    const nas = note({ id: "p1", text: "Building a NAS.", kind: "project" });
    shown = page([nas], { revisit: [nas] });
    await opened();
    expect(screen.getByTestId("memory-revisit")).toHaveTextContent("Still true?“Building a NAS.”");
    fireEvent.click(screen.getByTestId("memory-revisit-yes"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/revisit", { noteId: "p1", answer: "yes" }),
    );
  });
});

describe("the settings, on the Settings page", () => {
  it("say whose word Learn from my chats is, and send a choice", async () => {
    await openedSettings();
    expect(screen.getByTestId("memory-learn-off")).toBeChecked();
    expect(screen.getByTestId("memory-learn-effective")).toHaveTextContent(
      "Nobody has chosen, so it is off.",
    );
    fireEvent.click(screen.getByTestId("memory-learn-suggest"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/memory/prefs", { learn: "suggest" }),
    );
    expect(screen.getByTestId("memory-use-relevant")).toBeChecked();
    fireEvent.click(screen.getByTestId("memory-use-all"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("/api/memory/prefs", { useMode: "all" }));
  });

  it("cannot be changed when the owner has turned suggestions off", async () => {
    shown = page([], {
      prefs: prefs({ effective: { learnSetBy: "owner", suggestionsAllowed: false } }),
    });
    await openedSettings();
    expect(screen.getByTestId("memory-learn-suggest")).toBeDisabled();
    expect(screen.getByTestId("memory-learn-effective")).toHaveTextContent(
      "The owner of this Workbench has turned suggestions off.",
    );
  });
});

describe("past events", () => {
  it("show apart", async () => {
    const past = note({ id: "e1", text: "Exam on Friday.", kind: "event" });
    shown = { ...page([]), sections: { always: [], other: [], otherTotal: 0, past: [past] } };
    await opened();
    expect(screen.getByTestId("memory-section-past")).toHaveTextContent("Exam on Friday.");
  });
});

describe("which notes are used", () => {
  it('says "used when relevant" only while a search model can do it, and why not otherwise', async () => {
    shown = page([note({ id: "n1", text: "Lives in Paris." })], {
      relevance: { available: true, why: null },
    });
    await opened();
    expect(screen.getByTestId("memory-section-other")).toHaveTextContent("Used when relevant");
  });

  it("says every note that fits is used, and why, when no search model runs", async () => {
    shown = page([note({ id: "n1", text: "Lives in Paris." })], {
      relevance: { available: false, why: "Using every note that fits: no search model." },
    });
    await opened();
    expect(screen.getByTestId("memory-section-other")).toHaveTextContent("Used when there is room");
    expect(screen.getByTestId("memory-relevance-why")).toHaveTextContent("no search model");
  });
});

describe("the offer", () => {
  it("is made when the server says so, and an answer is remembered", async () => {
    render(<MemoryOffer onOpenMemory={vi.fn()} />);
    await screen.findByTestId("memory-offer");
    fireEvent.click(screen.getByTestId("memory-offer-yes"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/memory/prefs", {
        offerDismissed: true,
        learn: "suggest",
      }),
    );
    expect(await screen.findByTestId("memory-offer-done")).toHaveTextContent("Suggestions are on.");
  });

  it("is not made when the server says not", async () => {
    vi.mocked(api).mockResolvedValue({ show: false } as never);
    render(<MemoryOffer onOpenMemory={vi.fn()} />);
    await waitFor(() => expect(api).toHaveBeenCalled());
    expect(screen.queryByTestId("memory-offer")).toBeNull();
  });
});
