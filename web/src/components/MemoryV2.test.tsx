import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, del, patch, post, put } from "../lib/api";
import { noteDate, usageWords } from "../lib/memory";
import type { MemoryPage } from "../lib/types";
import { note, page, prefs, tag } from "../test-memory";
import { Memory } from "./Memory";
import { MemorySettings } from "./MemorySettings";

// Connections (MR9b) has its own tests; this page's do not serve it.
vi.mock("./Connections", () => ({ Connections: () => null }));
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  fileOf: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

let shown: MemoryPage;
let whole: Record<string, { note: ReturnType<typeof note>; earlier: ReturnType<typeof note>[] }>;

beforeEach(() => {
  for (const mocked of [api, del, patch, post, put]) vi.mocked(mocked).mockReset();
  whole = {};
  shown = page([note({ id: "n1", text: "Lives in Porto" })]);
  vi.mocked(api).mockImplementation((async (path: string, init?: RequestInit) => {
    if (path === "/api/memory") return shown;
    const one = /^\/api\/memory\/notes\/([^/?]+)$/.exec(path);
    if (one && !init?.method) {
      return whole[one[1]!] ?? { note: shown.sections.other[0], earlier: [] };
    }
    throw new Error(`not served: ${path}`);
  }) as never);
  vi.mocked(post).mockResolvedValue({ created: true } as never);
  vi.mocked(patch).mockResolvedValue({ note: note(), replaced: null } as never);
  vi.mocked(del).mockResolvedValue(undefined as never);
  vi.mocked(put).mockResolvedValue(prefs() as never);
});

async function opened() {
  render(<Memory chats={[]} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
}

async function openedSettings() {
  render(<MemorySettings onOpenMemory={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your memory settings…")).toBeNull());
}

const add = () => screen.getByRole("form", { name: "Add a note" });

describe("writing a note with details and tags", () => {
  it("sends the details and the tags, and counts the details against the limit", async () => {
    await opened();
    fireEvent.change(within(add()).getByLabelText("What should Workbench remember?"), {
      target: { value: "Release checklist" },
    });
    fireEvent.change(within(add()).getByLabelText("Details (optional)"), {
      target: { value: "1. Tag\n2. Ship" },
    });
    expect(screen.getByTestId("memory-add-body-count")).toHaveTextContent("14 of 20,000");
    fireEvent.change(within(add()).getByLabelText("Tags"), {
      target: { value: "work, Release ,work," },
    });
    fireEvent.change(within(add()).getByRole("combobox", { name: "Kind" }), {
      target: { value: "procedure" },
    });
    expect(within(add()).getByText("Steps, a brief or a checklist. Can be long.")).toBeVisible();
    fireEvent.click(within(add()).getByRole("button", { name: "Add note" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes", {
        text: "Release checklist",
        kind: "procedure",
        pinned: false,
        body: "1. Tag\n2. Ship",
        tags: ["work", "Release"],
      }),
    );
  });

  it("offers the person's tags as they type, and will not send details over the limit", async () => {
    shown = page([note({ id: "n1" })], { tags: [tag({ tag: "work", description: "Job" })] });
    shown.limits.maxBodyChars = 5;
    await opened();
    const options = add().querySelectorAll("datalist option");
    expect([...options].map((o) => o.getAttribute("value"))).toEqual(["work"]);
    fireEvent.change(within(add()).getByLabelText("What should Workbench remember?"), {
      target: { value: "Long" },
    });
    fireEvent.change(within(add()).getByLabelText("Details (optional)"), {
      target: { value: "123456" },
    });
    expect(screen.getByTestId("memory-add-body-count")).toHaveClass("text-error");
    expect(within(add()).getByRole("button", { name: "Add note" })).toBeDisabled();
  });

  it("says the server's refusal of a tag", async () => {
    vi.mocked(post).mockRejectedValue(
      new ApiError("A tag is at most 40 characters.", 422, {
        message: "A tag is at most 40 characters.",
        reason: "too_long",
      }),
    );
    await opened();
    fireEvent.change(within(add()).getByLabelText("What should Workbench remember?"), {
      target: { value: "A note" },
    });
    fireEvent.change(within(add()).getByLabelText("Tags"), { target: { value: "x".repeat(41) } });
    fireEvent.click(within(add()).getByRole("button", { name: "Add note" }));
    expect(await screen.findByTestId("memory-add-problem")).toHaveTextContent(
      "A tag is at most 40 characters.",
    );
  });
});

describe("a note's tags and details", () => {
  it("shows tags as chips and how long the details are", async () => {
    shown = page([note({ id: "n1", tags: ["work", "Release"], bodyChars: 1234 })]);
    await opened();
    expect(screen.getAllByTestId("memory-note-tag").map((t) => t.textContent)).toEqual([
      "work",
      "Release",
    ]);
    expect(screen.getByTestId("memory-note-body-size")).toHaveTextContent(
      "Details: 1,234 characters",
    );
  });

  it("opens the details and the earlier versions, each with the day it was replaced", async () => {
    shown = page([note({ id: "n1", text: "Lives in Lisbon", bodyChars: 9 })]);
    whole.n1 = {
      note: { ...shown.sections.other[0]!, body: "Moved in 2026" },
      earlier: [
        note({
          id: "old",
          text: "Lives in Porto",
          status: "superseded",
          supersededAt: 1_700_000_000,
        }),
      ],
    };
    await opened();
    const open = screen.getByRole("button", { name: "Show details of note: Lives in Lisbon" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(open);
    expect(await screen.findByTestId("memory-note-body")).toHaveTextContent("Moved in 2026");
    expect(screen.getByRole("heading", { name: "Earlier versions" })).toBeInTheDocument();
    const earlier = screen.getByTestId("memory-earlier-version");
    expect(earlier).toHaveTextContent("Lives in Porto");
    expect(within(earlier).getByTestId("memory-earlier-date")).toHaveTextContent(
      `Replaced on ${noteDate(1_700_000_000)}`,
    );
    expect(
      screen.getByRole("button", { name: "Hide details of note: Lives in Lisbon" }),
    ).toHaveAttribute("aria-expanded", "true");
    // An earlier version is deleted alone.
    fireEvent.click(screen.getByRole("button", { name: "Delete earlier version: Lives in Porto" }));
    await waitFor(() => expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/old"));
  });

  it("says a note has no details and no earlier versions", async () => {
    await opened();
    fireEvent.click(screen.getByTestId("memory-note-details"));
    expect(await screen.findByTestId("memory-note-no-body")).toBeInTheDocument();
    expect(screen.getByTestId("memory-earlier-none")).toHaveTextContent("No earlier versions.");
  });
});

describe("Save and Save as new version", () => {
  const form = () => screen.getByRole("form", { name: "Edit this note" });

  async function edit() {
    shown = page([note({ id: "n1", text: "Lives in Porto", bodyChars: 5, tags: ["home"] })]);
    whole.n1 = { note: { ...shown.sections.other[0]!, body: "Since" }, earlier: [] };
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Edit note: Lives in Porto" }));
    await screen.findByLabelText("Details");
  }

  it("reads the details whole to edit them, and Save changes the note where it is", async () => {
    await edit();
    expect(within(form()).getByLabelText("Details")).toHaveValue("Since");
    expect(within(form()).getByLabelText("Tags")).toHaveValue("home");
    fireEvent.change(within(form()).getByRole("textbox", { name: "Note" }), {
      target: { value: "Lives in Porto." },
    });
    fireEvent.click(within(form()).getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/n1", {
        text: "Lives in Porto.",
        kind: "fact",
      }),
    );
  });

  it("Save as new version says so, and sends what changed", async () => {
    await edit();
    fireEvent.change(within(form()).getByRole("textbox", { name: "Note" }), {
      target: { value: "Lives in Lisbon" },
    });
    fireEvent.change(within(form()).getByLabelText("Details"), { target: { value: "Since May" } });
    fireEvent.change(within(form()).getByLabelText("Tags"), { target: { value: "home, move" } });
    expect(form()).toHaveTextContent("the old one is kept under Earlier versions");
    fireEvent.click(within(form()).getByRole("button", { name: "Save as new version" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/n1", {
        text: "Lives in Lisbon",
        kind: "fact",
        body: "Since May",
        tags: ["home", "move"],
        asNewVersion: true,
      }),
    );
    expect(await screen.findByTestId("memory-said")).toHaveTextContent(
      "Saved as a new version. The old one is under Earlier versions.",
    );
  });
});

describe("deleting a note that has earlier versions", () => {
  it("names them, and deletes them with it", async () => {
    whole.n1 = {
      note: shown.sections.other[0]!,
      earlier: [note({ id: "o1", status: "superseded" }), note({ id: "o2", status: "superseded" })],
    };
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: Lives in Porto" }));
    const ask = await screen.findByRole("group", {
      name: "Delete this note and its earlier versions?",
    });
    expect(ask).toHaveTextContent(
      "This note has 2 earlier versions. Deleting the note deletes them too.",
    );
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(within(ask).getByRole("button", { name: "Delete note and earlier versions" }));
    await waitFor(() => expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/n1"));
    expect(await screen.findByTestId("memory-said")).toHaveTextContent(
      "Deleted the note and 2 earlier versions.",
    );
    // Only the note could come back: there is no Undo for what went with it.
    expect(screen.queryByTestId("memory-undo")).toBeNull();
  });

  it("can be kept instead", async () => {
    whole.n1 = { note: shown.sections.other[0]!, earlier: [note({ id: "o1" })] };
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: Lives in Porto" }));
    fireEvent.click(await screen.findByRole("button", { name: "Keep the note" }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.queryByTestId("memory-note-delete-confirm")).toBeNull();
  });

  it("says the server's words when nothing was deleted", async () => {
    vi.mocked(del).mockRejectedValue(
      new ApiError("Workbench could not remove this note's words from your search index.", 503),
    );
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: Lives in Porto" }));
    expect(await screen.findByTestId("memory-problem")).toHaveTextContent(
      "Workbench could not remove this note's words from your search index.",
    );
  });

  it("puts back a deleted note's details and tags with Undo", async () => {
    whole.n1 = {
      note: { ...shown.sections.other[0]!, body: "Since May", tags: ["home"], bodyChars: 9 },
      earlier: [],
    };
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete note: Lives in Porto" }));
    fireEvent.click(await screen.findByTestId("memory-undo"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes", {
        text: "Lives in Porto",
        kind: "fact",
        pinned: false,
        body: "Since May",
        tags: ["home"],
      }),
    );
  });
});

describe("the tag list", () => {
  const tags = [
    tag({ tag: "cooking", notes: 3, description: "Food and recipes" }),
    tag({ tag: "Cook", notes: 1 }),
  ];
  const rows = () => screen.getAllByTestId("memory-tag");

  beforeEach(() => {
    shown = page([note({ id: "n1" })], { tags });
  });

  it("lists each tag with its description and how many notes have it", async () => {
    await opened();
    expect(rows()[0]).toHaveTextContent("cooking");
    expect(rows()[0]).toHaveTextContent("3 notes");
    expect(rows()[0]).toHaveTextContent("Food and recipes");
    expect(rows()[1]).toHaveTextContent("1 note");
  });

  it("adds a tag with its description", async () => {
    await opened();
    fireEvent.change(screen.getByLabelText("Tag"), { target: { value: "garden" } });
    fireEvent.change(screen.getByLabelText("Description (optional)"), {
      target: { value: "Plants" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add tag" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/tags", {
        tag: "garden",
        description: "Plants",
      }),
    );
  });

  it("asks before it deletes a tag, saying how many notes lose it and that they stay", async () => {
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete tag: cooking" }));
    const ask = screen.getByRole("group", { name: "Delete tag cooking?" });
    expect(ask).toHaveTextContent("3 notes lose it. The notes stay.");
    expect(api).not.toHaveBeenCalledWith("/api/memory/tags/cooking", expect.anything());
    fireEvent.click(within(ask).getByRole("button", { name: "Delete tag" }));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/api/memory/tags/cooking", { method: "DELETE" }),
    );
  });

  it("keeps the tag when the question is turned down", async () => {
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Delete tag: cooking" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep the tag" }));
    expect(screen.queryByTestId("memory-tag-delete-confirm")).toBeNull();
  });

  it("renames a tag at once, and sends only what changed", async () => {
    vi.mocked(patch).mockResolvedValue({
      tag: tag({ tag: "food" }),
      merged: false,
      notes: 3,
    } as never);
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Edit tag: cooking" }));
    fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "food" } });
    fireEvent.click(screen.getByRole("button", { name: "Save tag" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/tags/cooking", { tag: "food" }),
    );
  });

  it("asks before a rename merges two tags, naming both and the notes", async () => {
    vi.mocked(patch).mockResolvedValue({ tag: tags[0], merged: true, notes: 1 } as never);
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Edit tag: Cook" }));
    fireEvent.change(screen.getByLabelText("Tag name"), { target: { value: "Cooking" } });
    fireEvent.click(screen.getByRole("button", { name: "Save tag" }));
    const ask = screen.getByRole("group", { name: "Merge tag Cook into cooking?" });
    expect(ask).toHaveTextContent(
      "“cooking” is already on your list. Merge “Cook” into it? 1 note",
    );
    expect(patch).not.toHaveBeenCalled();
    fireEvent.click(within(ask).getByRole("button", { name: "Merge tags" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/tags/Cook", { tag: "Cooking" }),
    );
    expect(await screen.findByTestId("memory-tags-said")).toHaveTextContent(
      "Merged Cook into Cooking.",
    );
  });

  it("changes only the description when only that changed", async () => {
    await opened();
    fireEvent.click(screen.getByRole("button", { name: "Edit tag: cooking" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Meals" } });
    fireEvent.click(screen.getByRole("button", { name: "Save tag" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledExactlyOnceWith("/api/memory/tags/cooking", {
        description: "Meals",
      }),
    );
  });
});

describe("importing tags", () => {
  const upload = (name: string, text: string) =>
    fireEvent.change(screen.getByLabelText("Tag file (.csv or .txt)"), {
      target: { files: [new File([text], name)] },
    });

  beforeEach(() => {
    shown = page([note({ id: "n1" })], { tags: [tag({ tag: "cooking", notes: 3 })] });
  });

  it("takes a file that adds and updates, names the lines it could not take, and removes nothing", async () => {
    vi.mocked(post).mockResolvedValue({
      confirmNeeded: false,
      added: 2,
      updated: 1,
      removed: [],
      problems: [{ line: 4, text: "a,b,c", message: "A row has at most two cells." }],
    } as never);
    await opened();
    expect(screen.getByRole("button", { name: "Import tags" })).toBeDisabled();
    upload("tags.csv", "tag,description\nwork,Job\n");
    fireEvent.click(screen.getByRole("button", { name: "Import tags" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/tags/import", {
        text: "tag,description\nwork,Job\n",
        format: "csv",
        replace: false,
      }),
    );
    expect(await screen.findByTestId("memory-tags-said")).toHaveTextContent("Added 2, updated 1.");
    expect(screen.getByTestId("memory-tags-problems")).toHaveTextContent("1 line not taken");
    expect(screen.getByTestId("memory-tags-problems")).toHaveTextContent(
      "Line 4: A row has at most two cells. “a,b,c”",
    );
  });

  it("reads a .txt as one tag a line", async () => {
    vi.mocked(post).mockResolvedValue({
      confirmNeeded: false,
      added: 1,
      updated: 0,
      removed: [],
      problems: [],
    } as never);
    await opened();
    upload("tags.TXT", "work\n");
    fireEvent.click(screen.getByRole("button", { name: "Import tags" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/memory/tags/import",
        expect.objectContaining({ format: "txt" }),
      ),
    );
  });

  it("asks before Replace my list removes tags, then sends the answer back", async () => {
    vi.mocked(post)
      .mockResolvedValueOnce({
        confirmNeeded: true,
        wouldRemove: [
          { tag: "cooking", notes: 3 },
          { tag: "old", notes: 1 },
        ],
        rows: 1,
        problems: [],
      } as never)
      .mockResolvedValueOnce({
        confirmNeeded: false,
        added: 1,
        updated: 0,
        removed: [
          { tag: "cooking", notes: 3 },
          { tag: "old", notes: 1 },
        ],
        problems: [],
      } as never);
    await opened();
    upload("tags.csv", "work\n");
    fireEvent.click(screen.getByRole("checkbox", { name: "Replace my list" }));
    fireEvent.click(screen.getByRole("button", { name: "Import tags" }));
    const ask = await screen.findByRole("group", { name: "Replace my list?" });
    expect(ask).toHaveTextContent("removes 2 tags that are not in the file");
    expect(screen.getByTestId("memory-tags-would-remove")).toHaveTextContent("cooking: 3 notes");
    expect(screen.getByTestId("memory-tags-would-remove")).toHaveTextContent("old: 1 note");
    expect(post).toHaveBeenCalledTimes(1);
    expect(vi.mocked(post).mock.calls[0]![1]).toMatchObject({ replace: true });
    fireEvent.click(within(ask).getByRole("button", { name: "Yes, replace my list" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(vi.mocked(post).mock.calls[1]![1]).toEqual({
      text: "work\n",
      format: "csv",
      replace: true,
      confirm: ["cooking", "old"],
    });
    expect(await screen.findByTestId("memory-tags-said")).toHaveTextContent(
      "Added 1, updated 0, removed 2.",
    );
  });

  it("changes nothing when the question is turned down", async () => {
    vi.mocked(post).mockResolvedValue({
      confirmNeeded: true,
      wouldRemove: [{ tag: "cooking", notes: 3 }],
      rows: 1,
      problems: [],
    } as never);
    await opened();
    upload("tags.csv", "work\n");
    fireEvent.click(screen.getByRole("checkbox", { name: "Replace my list" }));
    fireEvent.click(screen.getByRole("button", { name: "Import tags" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("memory-tags-replace-confirm")).toBeNull();
    expect(post).toHaveBeenCalledTimes(1);
  });
});

describe("the notes the page does not list", () => {
  beforeEach(() => {
    shown = page([note({ id: "n1" })], { tags: [tag({ tag: "woodwork", notes: 2 })] });
    shown.sections.otherTotal = 450;
  });

  it("says how many are shown, and finds the rest by a search and a tag", async () => {
    await opened();
    expect(screen.getByTestId("memory-showing")).toHaveTextContent("Showing the newest 1 of 450");
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/memory") return shown;
      if (path.startsWith("/api/memory/notes?")) {
        return {
          notes: [note({ id: "f1", text: "A birdhouse out of cedar" })],
          total: 1,
          offset: 0,
          search: { how: "words", why: "Found by words: no search model is running." },
        };
      }
      throw new Error(`not served: ${path}`);
    }) as never);
    fireEvent.change(screen.getByLabelText("Search your notes", { selector: "input" }), {
      target: { value: "cedar #woodwork" },
    });
    fireEvent.change(screen.getByLabelText("Only notes tagged"), { target: { value: "woodwork" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("A birdhouse out of cedar")).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith(
      "/api/memory/notes?query=cedar+%23woodwork&tag=woodwork&offset=0&limit=50",
    );
    expect(screen.getByTestId("memory-search-count")).toHaveTextContent(
      "1 note found. Found by words: no search model is running.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.queryByTestId("memory-search-results")).toBeNull();
  });

  it("is offered with only a few notes, and says nothing of notes not shown when every note is", async () => {
    shown.sections.otherTotal = 1;
    await opened();
    expect(screen.getByTestId("memory-search")).toBeInTheDocument();
    expect(screen.queryByTestId("memory-showing")).toBeNull();
  });

  it("says why a search failed", async () => {
    await opened();
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/memory") return shown;
      throw new ApiError("The search could not run.", 500);
    }) as never);
    fireEvent.change(screen.getByLabelText("Search your notes", { selector: "input" }), {
      target: { value: "cedar" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByTestId("memory-search-problem")).toHaveTextContent(
      "The search could not run.",
    );
  });
});

describe("the two budgets and the search switch, on the Settings page", () => {
  async function room() {
    await openedSettings();
    return screen.getByTestId("memory-budget-details");
  }

  it("shows each budget's value in effect and whose word it is", async () => {
    shown = page([note()], {
      prefs: prefs({
        budgetTokens: 800,
        relevantBudgetTokens: null,
        effective: { budgetTokens: 800, relevantBudgetTokens: 6000 },
      }),
    });
    const details = await room();
    expect(screen.getByTestId("memory-budget-effective")).toHaveTextContent(
      "In effect: 800 tokens. You chose this.",
    );
    expect(screen.getByTestId("memory-relevant-budget-effective")).toHaveTextContent(
      "In effect: 6,000 tokens. Nobody has chosen, so the default applies.",
    );
    expect(details).toHaveTextContent("never more than 10% of its model's window");
    expect(screen.getByTestId("memory-relevant-budget-input")).toHaveAttribute(
      "placeholder",
      "6,000 (the default)",
    );
  });

  it("saves the relevant budget on its own, and takes it back to the default", async () => {
    await room();
    fireEvent.change(screen.getByLabelText("Relevant notes: most tokens a turn"), {
      target: { value: "3000" },
    });
    fireEvent.click(screen.getByTestId("memory-relevant-budget-save"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledExactlyOnceWith("/api/memory/prefs", {
        relevantBudgetTokens: 3000,
      }),
    );
  });

  it("will not save a relevant budget outside 200 to 20,000", async () => {
    await room();
    fireEvent.change(screen.getByTestId("memory-relevant-budget-input"), {
      target: { value: "100" },
    });
    fireEvent.click(screen.getByTestId("memory-relevant-budget-save"));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a whole number from 200 to 20,000, or use the default.",
    );
    expect(put).not.toHaveBeenCalled();
  });

  it("offers Use the default for a relevant budget that was set", async () => {
    shown = page([note()], {
      prefs: prefs({ relevantBudgetTokens: 3000, effective: { relevantBudgetTokens: 3000 } }),
    });
    await room();
    fireEvent.click(screen.getByTestId("memory-relevant-budget-default"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/memory/prefs", { relevantBudgetTokens: null }),
    );
  });

  it("has a switch for letting models search the notes, which shows what is in effect", async () => {
    await openedSettings();
    const search = screen.getByRole("switch", { name: "Let models search my notes" });
    expect(search).toBeChecked();
    fireEvent.click(search);
    await waitFor(() =>
      expect(put).toHaveBeenCalledExactlyOnceWith("/api/memory/prefs", { searchTool: false }),
    );
  });

  it("shows the switch off when it is off", async () => {
    shown = page([note()], { prefs: prefs({ searchTool: false }) });
    await openedSettings();
    expect(screen.getByRole("switch", { name: "Let models search my notes" })).not.toBeChecked();
    expect(screen.getByTestId("memory-search-effective")).toHaveTextContent(
      "Models are only given the notes Workbench chooses for them.",
    );
  });
});

describe("the usage line", () => {
  const usage = (relevant: MemoryPage["usage"]["relevant"]) =>
    page([note()], {
      usage: { pinned: { tokens: 412, budget: 2000, notes: 3 }, relevant },
    }).usage;

  it("says what is pinned, and what relevant memory may take and chooses from", () => {
    expect(usageWords(usage({ budget: 6000, from: 1204, how: "meaning and words" }))).toBe(
      "Pinned: 412 of 2,000 tokens, in every chat. Relevant: up to 6,000 tokens a turn, chosen from 1,204 notes by meaning and words.",
    );
    expect(usageWords(usage({ budget: 3000, from: 1, how: "words" }))).toBe(
      "Pinned: 412 of 2,000 tokens, in every chat. Relevant: up to 3,000 tokens a turn, chosen from 1 note by words.",
    );
  });

  it("says every note that fits is used when that is the choice", () => {
    expect(usageWords(usage({ budget: 6000, from: 40, how: "all" }))).toBe(
      "Pinned: 412 of 2,000 tokens, in every chat. Relevant: up to 6,000 tokens a turn; every note that fits is used.",
    );
  });

  it("is on the page", async () => {
    shown = page([note()], {
      usage: {
        pinned: { tokens: 412, budget: 2000, notes: 3 },
        relevant: { budget: 6000, from: 9, how: "meaning and words" },
      },
    });
    await opened();
    expect(screen.getByTestId("memory-usage-tokens")).toHaveTextContent(
      "Pinned: 412 of 2,000 tokens, in every chat. Relevant: up to 6,000 tokens a turn, chosen from 9 notes by meaning and words.",
    );
  });
});

describe("the Memory page, with its settings gone (MV4)", () => {
  const suggested = {
    ...note({ id: "s1", text: "Works as a nurse.", source: "suggested", sourceChatId: "c1" }),
    quote: "I'm a nurse working nights",
    replaces: null,
    proposedTags: [],
  };

  function draw(props: { onOpenSettings?: () => void; onSearchChats?: (q: string) => void } = {}) {
    render(<Memory chats={[]} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} {...props} />);
    return waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
  }

  it("lists its sections in order: Suggested notes, Your notes, Past chats, Tags", async () => {
    shown = page([note({ id: "n1" })], {
      suggestions: [suggested],
      tags: [tag({ tag: "work" })],
    });
    await draw({ onSearchChats: vi.fn() });
    const names = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    const at = (name: string) => names.findIndex((n) => n?.includes(name));
    expect(at("Suggested notes")).toBeGreaterThanOrEqual(0);
    expect(at("Suggested notes")).toBeLessThan(at("Your notes"));
    expect(at("Your notes")).toBeLessThan(at("Past chats"));
    expect(at("Past chats")).toBeLessThan(at("Tags"));
    expect(at("Tags")).toBeLessThan(at("Export or delete"));
  });

  it("holds no settings: no Use memory switch, no budget, no models that learn, no connections", async () => {
    await draw({ onOpenSettings: vi.fn() });
    expect(screen.queryByTestId("memory-use-switch")).toBeNull();
    expect(screen.queryByRole("switch", { name: "Use memory" })).toBeNull();
    expect(screen.queryByTestId("memory-budget-details")).toBeNull();
    expect(screen.queryByTestId("memory-relevant-budget-input")).toBeNull();
    expect(screen.queryByTestId("memory-models")).toBeNull();
    expect(screen.queryByText("Models that learn")).toBeNull();
    expect(screen.queryByTestId("memory-connections")).toBeNull();
    expect(screen.queryByText("Connections to other apps")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Settings" })).toBeNull();
  });

  it("opens the settings from its head", async () => {
    const onOpenSettings = vi.fn();
    await draw({ onOpenSettings });
    expect(screen.getByTestId("memory-open-settings")).toHaveTextContent("Memory settings");
    fireEvent.click(screen.getByTestId("memory-open-settings"));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });

  it("says Memory is off for the person, only while the person's own Use memory is off", async () => {
    await draw({ onOpenSettings: vi.fn() });
    expect(screen.queryByTestId("memory-person-off")).toBeNull();
    cleanup();
    shown = page([note({ id: "n1" })], {
      prefs: prefs({ enabled: false, effective: { enabled: false, disabledBy: "you" } }),
    });
    await draw({ onOpenSettings: vi.fn() });
    expect(screen.getByTestId("memory-person-off")).toHaveTextContent(
      "Memory is off: no model is given your notes. Turn it on in Memory settings.",
    );
    // The notes stay, and can still be read.
    expect(screen.getAllByTestId("memory-note")).toHaveLength(1);
  });

  it("searches the person's past chats: the trimmed words go to onSearchChats, and an empty box cannot", async () => {
    const onSearchChats = vi.fn();
    await draw({ onSearchChats });
    const go = screen.getByTestId("memory-past-chats-go");
    const box = screen.getByTestId("memory-past-chats-input");
    expect(go).toBeDisabled();
    fireEvent.change(box, { target: { value: "   " } });
    expect(go).toBeDisabled();
    fireEvent.change(box, { target: { value: "  birdhouse cedar  " } });
    expect(go).toBeEnabled();
    fireEvent.click(go);
    expect(onSearchChats).toHaveBeenCalledExactlyOnceWith("birdhouse cedar");
  });

  it("has the note search at the top of Your notes with only a few notes", async () => {
    shown = page([note({ id: "n1" }), note({ id: "n2", text: "Likes tea" })]);
    await draw();
    const search = screen.getByTestId("memory-search");
    const notes = screen.getByRole("heading", { level: 2, name: "Your notes" });
    expect(notes.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId("memory-showing")).toBeNull();
  });
});
