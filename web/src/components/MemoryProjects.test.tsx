import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, post } from "../lib/api";
import type { MemoryNote, Project } from "../lib/types";
import { note, page } from "../test-memory";
import { Memory } from "./Memory";

vi.mock("./Connections", () => ({ Connections: () => null }));
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));
vi.mock("./MemoryServer", () => ({ MemoryServer: () => null }));
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

const project = (id: string, name: string): Project => ({
  id,
  name,
  generalMemory: true,
  generalDocuments: true,
  documents: 0,
  createdAt: 1,
  updatedAt: 1,
  chats: 0,
  notes: 0,
});
const projects = [project("p1", "Garden plans"), project("p2", "Taxes")];

let notes: MemoryNote[];
const asked: string[] = [];

beforeEach(() => {
  asked.length = 0;
  notes = [
    note({ id: "g", text: "I use metric units" }),
    note({ id: "a", text: "The beds face south", projectId: "p1" }),
  ];
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    asked.push(path);
    if (path === "/api/projects") return { projects };
    if (path === "/api/memory" || path.startsWith("/api/memory?")) {
      const where = new URLSearchParams(path.split("?")[1] ?? "").get("where") ?? "all";
      return page(
        notes.filter((n) =>
          where === "all" ? true : where === "general" ? !n.projectId : n.projectId === where,
        ),
      );
    }
    if (path.startsWith("/api/memory/notes?")) {
      const params = new URLSearchParams(path.split("?")[1]);
      const where = params.get("where") ?? "all";
      const found = notes.filter((n) =>
        where === "all" ? true : where === "general" ? !n.projectId : n.projectId === where,
      );
      return { notes: found, total: found.length, offset: 0, search: null };
    }
    throw new Error(`not served: ${path}`);
  }) as never);
});

async function opened() {
  render(<Memory chats={[]} chatsLoaded onOpenChat={vi.fn()} onClose={vi.fn()} />);
  await waitFor(() => expect(screen.queryByText("Loading your notes…")).toBeNull());
  // The projects are read beside the notes.
  await screen.findByTestId("memory-where");
}

const texts = () => screen.getAllByTestId("memory-note-text").map((n) => n.textContent);

it("says which project a note is kept to, and nothing for a general note", async () => {
  await opened();
  const rows = screen.getAllByTestId("memory-note");
  const general = rows.find((r) => r.textContent?.includes("metric"))!;
  const kept = rows.find((r) => r.textContent?.includes("beds"))!;
  expect(within(general).queryByTestId("memory-note-project")).toBeNull();
  expect(within(kept).getByTestId("memory-note-project")).toHaveTextContent("In Garden plans");
});

describe("the Where filter", () => {
  it("lists All, General and each project, beside the notes' search", async () => {
    await opened();
    const where = screen.getByRole("combobox", { name: "Where" });
    expect(
      within(where)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["All", "General", "Garden plans", "Taxes"]);
    expect(where).toHaveValue("all");
    expect(asked).toContain("/api/memory");
  });

  it("reads the page again with where=<project id> and shows only that place", async () => {
    await opened();
    fireEvent.change(screen.getByTestId("memory-where"), { target: { value: "p1" } });
    await waitFor(() => expect(asked).toContain("/api/memory?where=p1"));
    await waitFor(() => expect(texts()).toEqual(["The beds face south"]));
    expect(screen.getByTestId("memory-where")).toHaveValue("p1");
  });

  it("reads where=general for General", async () => {
    await opened();
    fireEvent.change(screen.getByTestId("memory-where"), { target: { value: "general" } });
    await waitFor(() => expect(asked).toContain("/api/memory?where=general"));
    await waitFor(() => expect(texts()).toEqual(["I use metric units"]));
  });

  it("keeps the filter on a search of the notes", async () => {
    await opened();
    fireEvent.change(screen.getByTestId("memory-where"), { target: { value: "p1" } });
    await waitFor(() => expect(texts()).toEqual(["The beds face south"]));
    fireEvent.change(screen.getByTestId("memory-search-input"), { target: { value: "beds" } });
    fireEvent.click(screen.getByTestId("memory-search-go"));
    await waitFor(() =>
      expect(asked.some((p) => p.startsWith("/api/memory/notes?") && p.includes("where=p1"))).toBe(
        true,
      ),
    );
  });

  it("keeps the filter when a place has no notes, so it can be changed back", async () => {
    await opened();
    fireEvent.change(screen.getByTestId("memory-where"), { target: { value: "p2" } });
    expect(await screen.findByTestId("memory-where-empty")).toHaveTextContent("No notes in Taxes.");
    expect(screen.getByTestId("memory-where")).toHaveValue("p2");
    expect(screen.queryByTestId("memory-empty")).toBeNull();
  });
});

describe("Move to project", () => {
  it("moves a general note to a project with the move endpoint, then reads again", async () => {
    await opened();
    const row = screen
      .getAllByTestId("memory-note")
      .find((r) => r.textContent?.includes("metric"))!;
    fireEvent.click(within(row).getByRole("button", { name: /Move note to a project/ }));
    const pick = within(row).getByRole("combobox", { name: "Move to project" });
    expect(
      within(pick)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["General", "Garden plans", "Taxes"]);
    expect(pick).toHaveValue("");
    expect(within(row).getByRole("button", { name: "Move" })).toBeDisabled();
    fireEvent.change(pick, { target: { value: "p2" } });
    vi.mocked(post).mockImplementation((async () => {
      notes = notes.map((n) => (n.id === "g" ? { ...n, projectId: "p2" } : n));
      return { note: notes[0] };
    }) as never);
    fireEvent.click(within(row).getByRole("button", { name: "Move" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes/g/move", { projectId: "p2" }),
    );
    await waitFor(() =>
      expect(
        screen
          .getAllByTestId("memory-note-project")
          .map((n) => n.textContent)
          .sort(),
      ).toEqual(["In Garden plans", "In Taxes"]),
    );
  });

  it("moves a project's note back to General with projectId null", async () => {
    await opened();
    const row = screen.getAllByTestId("memory-note").find((r) => r.textContent?.includes("beds"))!;
    fireEvent.click(within(row).getByRole("button", { name: /Move note to a project/ }));
    const pick = within(row).getByRole("combobox", { name: "Move to project" });
    expect(pick).toHaveValue("p1");
    fireEvent.change(pick, { target: { value: "" } });
    vi.mocked(post).mockResolvedValue({ note: notes[1] });
    fireEvent.click(within(row).getByRole("button", { name: "Move" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes/a/move", { projectId: null }),
    );
  });

  it("says the server's reason when the move is refused", async () => {
    await opened();
    const row = screen.getAllByTestId("memory-note")[0]!;
    fireEvent.click(within(row).getByRole("button", { name: /Move note to a project/ }));
    fireEvent.change(within(row).getByRole("combobox", { name: "Move to project" }), {
      target: { value: "p2" },
    });
    vi.mocked(post).mockRejectedValue(new ApiError("There is no such project.", 404));
    fireEvent.click(within(row).getByRole("button", { name: "Move" }));
    expect(await within(row).findByTestId("memory-note-problem")).toHaveTextContent(
      "There is no such project.",
    );
  });
});
