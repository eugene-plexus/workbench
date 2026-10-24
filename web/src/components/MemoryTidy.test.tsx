import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, del, post } from "../lib/api";
import { noteDate } from "../lib/memory";
import type { MemoryPage, NoteDocument, TidyProposal } from "../lib/types";
import { note, page } from "../test-memory";
import { Memory } from "./Memory";
import { placeWords } from "./MemoryDetails";

vi.mock("./Connections", () => ({ Connections: () => null }));
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));
vi.mock("./MemoryServer", () => ({ MemoryServer: () => null }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  post: vi.fn(),
}));

let shown: MemoryPage;
let linked: NoteDocument[];

beforeEach(() => {
  for (const mocked of [api, del, post]) vi.mocked(mocked).mockReset();
  linked = [];
  shown = page([note({ id: "n1", text: "Lives in Porto" })]);
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/memory") return shown;
    if (path === "/api/documents") {
      return {
        documents: [
          { id: "d1", name: "plan.pdf", state: "ready" },
          { id: "d2", name: "notes.txt", state: "searchable" },
          { id: "d3", name: "broken.pdf", state: "failed" },
        ],
      };
    }
    if (/^\/api\/memory\/notes\/n1$/.test(path)) {
      return { note: shown.sections.other[0], earlier: [], documents: linked };
    }
    throw new Error(`not served: ${path}`);
  }) as never);
  vi.mocked(post).mockResolvedValue({} as never);
  vi.mocked(del).mockResolvedValue(undefined as never);
});

async function opened() {
  render(<Memory chats={[]} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
}

const twins = (): TidyProposal => ({
  id: "t1",
  kind: "duplicate",
  notes: [
    note({ id: "a", text: "Lives in Porto" }),
    note({ id: "b", text: "Lives in Porto, Portugal", kind: "preference" }),
  ],
});

it("keeps model wording only after the person chooses it", async () => {
  shown = page([], {
    tidy: [{ ...twins(), model: "qwen", merged: { title: "Lives in Porto", body: "Portugal" } }],
  });
  await opened();
  expect(screen.getByText("qwen thinks these say the same thing")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Keep this wording" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/memory/tidy/t1", { action: "keep_wording" }),
  );
});

describe("tidy: notes that look the same", () => {
  it("shows both and keeps the one chosen", async () => {
    shown = page([], { tidy: [twins()] });
    await opened();
    expect(screen.getByText("These look like the same note")).toBeTruthy();
    const keeps = screen.getAllByTestId("memory-tidy-keep");
    expect(keeps).toHaveLength(2);
    expect(screen.getAllByTestId("memory-tidy-note")[1]).toHaveTextContent("Preference");
    fireEvent.click(keeps[1]!);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/tidy/t1", { action: "keep", keepId: "b" }),
    );
  });

  it("keeps both", async () => {
    shown = page([], { tidy: [twins()] });
    await opened();
    fireEvent.click(screen.getByTestId("memory-tidy-keep-both"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/tidy/t1", { action: "keep_both" }),
    );
  });

  it("names the cause when the notes changed", async () => {
    shown = page([], { tidy: [twins()] });
    vi.mocked(post).mockRejectedValue(
      new ApiError("These notes changed since. Look again.", 409) as never,
    );
    await opened();
    fireEvent.click(screen.getByTestId("memory-tidy-keep-both"));
    expect(await screen.findByTestId("memory-problem")).toHaveTextContent(/changed since/);
  });
});

describe("tidy: an event that has passed", () => {
  const past = (): TidyProposal => ({
    id: "t2",
    kind: "past",
    notes: [note({ id: "e1", text: "Dentist visit", kind: "event", expiresAt: "2026-09-30" })],
  });

  it("removes it", async () => {
    shown = page([], { tidy: [past()] });
    await opened();
    const day = noteDate(Date.parse("2026-09-30T12:00:00") / 1000);
    expect(screen.getByTestId("memory-tidy-past-title")).toHaveTextContent(
      `This was on ${day}. Remove it?`,
    );
    fireEvent.click(screen.getByTestId("memory-tidy-remove"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/tidy/t2", { action: "remove" }),
    );
  });

  it("keeps it", async () => {
    shown = page([], { tidy: [past()] });
    await opened();
    fireEvent.click(screen.getByTestId("memory-tidy-keep-event"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/tidy/t2", { action: "keep_event" }),
    );
  });

  it("is not there when nothing is proposed", async () => {
    await opened();
    expect(screen.queryByTestId("memory-tidy")).toBeNull();
  });
});

describe("Based on a document", () => {
  async function details() {
    await opened();
    fireEvent.click(screen.getByTestId("memory-note-details"));
    return screen.findByTestId("memory-documents");
  }

  it("lists the documents with where an answer found it, and unlinks one", async () => {
    linked = [
      {
        docId: "d1",
        name: "plan.pdf",
        scope: "library",
        how: "answer",
        place: [
          { page_start: 4, page_end: 4, heading: "Costs" },
          { line_start: 9, line_end: 12 },
        ],
        linkedAt: 1,
      },
      { docId: "d2", name: "notes.txt", scope: "library", how: "person", place: [], linkedAt: 2 },
    ];
    const section = await details();
    expect(within(section).getAllByTestId("memory-document")).toHaveLength(2);
    expect(within(section).getByTestId("memory-document-place")).toHaveTextContent(
      "page 4, Costs; lines 9-12",
    );
    fireEvent.click(within(section).getAllByTestId("memory-doc-unlink")[0]!);
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/memory/notes/n1/documents/d1"));
  });

  it("links one of the documents that can be read", async () => {
    const section = await details();
    await waitFor(() => expect(within(section).getByTestId("memory-doc-pick")).toBeTruthy());
    const options = within(within(section).getByTestId("memory-doc-pick"))
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toEqual(["Choose a document", "plan.pdf", "notes.txt"]);
    expect(within(section).getByTestId("memory-doc-link")).toBeDisabled();
    fireEvent.change(within(section).getByTestId("memory-doc-pick"), { target: { value: "d1" } });
    fireEvent.click(within(section).getByTestId("memory-doc-link"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes/n1/documents", { docId: "d1" }),
    );
  });
});

test("a place reads as pages, lines and the heading", () => {
  expect(placeWords({ page_start: 4, page_end: 6 })).toBe("pages 4-6");
  expect(placeWords({ line_start: 3 })).toBe("line 3");
  expect(placeWords({ snippet: "x" })).toBe("");
});
