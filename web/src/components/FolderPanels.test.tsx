/**
 * What a folder's card opens (MR7): its files (a page at a time, with a filter for where a file
 * stands, one for why it was skipped, and a box for a word in its path), what the last check left
 * out and why, and its rules (the ignore list and the two switches, saved all at once). Against the
 * same small fake Workbench as DocumentFolders.test.tsx, which keeps every call so a test can say
 * what the page asked for.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { api, del, fileOf, post } from "../lib/api";
import { saveFile } from "../lib/memory";
import { useDocumentFolders } from "../lib/useDocumentFolders";
import {
  brokenFile,
  counts,
  detail,
  FakeFolders,
  folder,
  folderFile,
  leftOut,
  NOW,
  pictureFile,
  refusal,
  waitingFile,
} from "../test-folders";
import { DocumentFolders } from "./DocumentFolders";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  documentText: vi.fn(),
  fileOf: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));
vi.mock("../lib/memory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/memory")>()),
  saveFile: vi.fn(),
}));

let server: FakeFolders;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(del).mockReset();
  vi.mocked(fileOf).mockReset();
  vi.mocked(saveFile).mockReset();
  server = new FakeFolders([folder()]);
  server.files.set("g1", [folderFile(), pictureFile(), brokenFile(), waitingFile()]);
  server.install();
});
afterEach(() => vi.useRealTimers());

const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

function Section({ off = false }: { off?: boolean }) {
  const folders = useDocumentFolders();
  return (
    <DocumentFolders
      folders={folders}
      me={{ owner: false }}
      off={off}
      busy={false}
      askHere={false}
      onAsk={vi.fn()}
    />
  );
}

async function show(off = false) {
  const view = render(<Section off={off} />);
  await settle();
  return view;
}

const click = (id: string, at = 0) => fireEvent.click(screen.getAllByTestId(id)[at]!);
const text = (id: string, at = 0) => screen.getAllByTestId(id)[at]!.textContent ?? "";
const rows = () => screen.queryAllByTestId("folder-file");
const row = (path: string) => rows().find((r) => r.dataset.path === path)!;
/** The requests for a folder's files, as the page made them: their query. */
const asked = () =>
  server
    .made("GET")
    .filter((c) => c.path.includes("/files?"))
    .map((c) => Object.fromEntries(new URL(c.path, "http://w").searchParams));

async function openFiles() {
  await show();
  click("folder-files-toggle");
  await settle();
}

describe("a folder's files", () => {
  it("opens from the card, and reads the first page in path order", async () => {
    await openFiles();
    expect(screen.getByTestId("folder-files-toggle")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("folder-files-toggle")).toHaveAccessibleName(
      "Hide files: Work notes",
    );
    expect(asked()).toEqual([{ offset: "0", limit: "50" }]);
    expect(rows().map((r) => r.dataset.path)).toEqual([
      "broken.pdf",
      "docs/harbour-notes.md",
      "later.md",
      "picture.png",
    ]);
    expect(screen.getByRole("group", { name: "Files of Work notes" })).toBeInTheDocument();
  });

  it("says each file's path, what it is and when it was added", async () => {
    await openFiles();
    expect(text("folder-file-path", 1)).toBe("docs/harbour-notes.md");
    expect(within(row("docs/harbour-notes.md")).getByTestId("folder-file-meta")).toHaveTextContent(
      "Markdown · 2 KiB · 24 lines · 3 passages · Added Oct 11",
    );
  });

  it("says where each file stands, and for a file that was skipped or failed the server's own reason", async () => {
    await openFiles();
    const status = (path: string) =>
      within(row(path)).getByTestId("folder-file-status").textContent;
    expect(status("docs/harbour-notes.md")).toBe("Ready");
    expect(status("picture.png")).toBe(
      "Skipped: this file is a picture, not a document. Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
    );
    expect(status("broken.pdf")).toBe(
      "Failed: Workbench could not read this file (the PDF is cut off).",
    );
    expect(status("later.md")).toBe("Waiting to be read");
    expect(row("picture.png")).toHaveAttribute("data-code", "not_document");
    expect(row("picture.png")).toHaveAttribute("data-state", "skipped");
  });

  it("says a file is being read as it is read, and as busy", async () => {
    server.files.set("g1", [waitingFile({ state: "reading" })]);
    await openFiles();
    expect(text("folder-file-status")).toBe("Reading…");
    expect(rows()[0]).toHaveAttribute("aria-busy", "true");
  });

  it("says how many files there are, and where this page is", async () => {
    await openFiles();
    expect(text("folder-files-range")).toBe("1 to 4 of 4");
  });

  it("says when no file has been read yet, and when none fits what was chosen", async () => {
    server.files.set("g1", []);
    server.page.folders = [folder({ counts: counts({ files: 0, byState: {}, byCode: {} }) })];
    await openFiles();
    expect(text("folder-files-empty")).toBe("Workbench has read no files from this folder yet.");
    expect(text("folder-files-range")).toBe("No files");
    // A folder that has files but a word that finds none.
    server.page.folders = [folder()];
    server.files.set("g1", [folderFile()]);
    await wait(30_000);
    fireEvent.change(screen.getByTestId("folder-files-find"), { target: { value: "zebra" } });
    await wait(300);
    expect(text("folder-files-empty")).toBe("No file fits what you chose.");
  });

  it("closes again, and the list goes", async () => {
    await openFiles();
    click("folder-files-toggle");
    expect(screen.queryByTestId("folder-files")).toBeNull();
    expect(screen.getByTestId("folder-files-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  it("never puts back a page of files that a newer one replaced", async () => {
    await show();
    const real = vi.mocked(api).getMockImplementation()!;
    // The first page, of every file, is slow; the one for the filter chosen after it is not.
    let release: (page: unknown) => void = () => undefined;
    vi.mocked(api).mockImplementation(((path: string, init?: RequestInit) =>
      path.includes("/files?") && !path.includes("state=")
        ? new Promise((done) => (release = done))
        : real(path, init)) as never);
    click("folder-files-toggle");
    await settle();
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "skipped" } });
    await settle();
    expect(rows().map((r) => r.dataset.path)).toEqual(["picture.png"]);
    await act(async () => {
      release({
        folderId: "g1",
        files: [folderFile(), pictureFile(), brokenFile(), waitingFile()],
        total: 4,
        offset: 0,
        limit: 50,
        counts: folder().counts,
      });
      await Promise.resolve();
    });
    expect(rows().map((r) => r.dataset.path)).toEqual(["picture.png"]);
    expect(text("folder-files-range")).toBe("1 to 1 of 1");
  });

  it("says why when it could not be read, and reads again with Try again", async () => {
    await show();
    server.refuse.set("GET /api/documents/folders/g1/files", new Error("Workbench answered 502."));
    click("folder-files-toggle");
    await settle();
    expect(text("folder-files-problem")).toBe(
      "Could not read the files" + "Workbench answered 502." + "Try again",
    );
    expect(rows()).toHaveLength(0);
    server.refuse.clear();
    click("folder-files-retry");
    await settle();
    expect(rows()).toHaveLength(4);
    expect(screen.queryByTestId("folder-files-problem")).toBeNull();
  });

  it("keeps the list it showed when a later read fails, and says why beside it", async () => {
    await openFiles();
    server.refuse.set("GET /api/documents/folders/g1/files", new Error("Workbench answered 502."));
    server.update("g1", { counts: { ...folder().counts, files: 13 } });
    await wait(30_000);
    expect(rows()).toHaveLength(4);
    expect(screen.getByTestId("folder-files-problem")).toBeInTheDocument();
  });

  it("is read again when the folder says something changed, so a file is seen to be read", async () => {
    server.files.set("g1", [waitingFile()]);
    server.update("g1", {
      counts: counts({ files: 1, byState: { queued: 1, ready: 0, skipped: 0 }, byCode: {} }),
    });
    await openFiles();
    expect(text("folder-file-status")).toBe("Waiting to be read");
    server.files.set("g1", [waitingFile({ state: "ready", searchable: true, passages: 2 })]);
    server.update("g1", {
      counts: counts({ files: 1, byState: { queued: 0, ready: 1, skipped: 0 }, byCode: {} }),
    });
    await wait(1_000);
    expect(text("folder-file-status")).toBe("Ready");
  });

  it("names the page for its folder", async () => {
    server.page.folders = [folder(), folder({ id: "g2", name: "Field reports" })];
    server.files.set("g2", []);
    await show();
    click("folder-files-toggle", 1);
    await settle();
    expect(screen.getByRole("group", { name: "Files of Field reports" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Files of Work notes" })).toBeNull();
  });
});

describe("filtering a folder's files", () => {
  it("offers the filters that have files, each with its count from the folder", async () => {
    await openFiles();
    const select = screen.getByTestId("folder-files-state");
    expect(select).toHaveAccessibleName("Show");
    expect([...within(select).getAllByRole("option")].map((o) => o.textContent)).toEqual([
      "All files (12)",
      "Ready (10)",
      "Skipped (2)",
    ]);
  });

  it("asks for the files in one state, from the first page", async () => {
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "skipped" } });
    await settle();
    expect(asked().at(-1)).toEqual({ state: "skipped", offset: "0", limit: "50" });
    expect(rows().map((r) => r.dataset.path)).toEqual(["picture.png"]);
    expect(text("folder-files-range")).toBe("1 to 1 of 1");
  });

  it("offers why files were skipped, most first, and asks for one reason", async () => {
    server.page.folders = [
      folder({
        counts: counts({
          byState: { ...counts().byState, skipped: 12, failed: 1, ready: 0 },
          byCode: { not_document: 11, damaged: 1, busy: 1 },
          files: 13,
        }),
      }),
    ];
    server.files.set("g1", [pictureFile(), pictureFile({ id: "f9", path: "z.png" }), brokenFile()]);
    await openFiles();
    const reason = screen.getByTestId("folder-files-reason");
    expect(reason).toHaveAccessibleName("Reason");
    expect([...within(reason).getAllByRole("option")].map((o) => o.textContent)).toEqual([
      "Any reason",
      "A picture or a sound recording (11)",
      "Damaged, or could not be read (1)",
      "In use by another program (1)",
    ]);
    fireEvent.change(reason, { target: { value: "damaged" } });
    await settle();
    expect(asked().at(-1)).toEqual({ code: "damaged", offset: "0", limit: "50" });
    expect(rows().map((r) => r.dataset.path)).toEqual(["broken.pdf"]);
  });

  it("offers no reason where no file was skipped or failed, and none for files that are ready", async () => {
    server.page.folders = [folder({ counts: counts({ byCode: {} }) })];
    await openFiles();
    expect(screen.queryByTestId("folder-files-reason")).toBeNull();
    server.page.folders = [folder()];
    await wait(30_000);
    expect(screen.getByTestId("folder-files-reason")).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "ready" } });
    expect(screen.queryByTestId("folder-files-reason")).toBeNull();
  });

  it("forgets the reason when the state is changed, since it would find nothing", async () => {
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-reason"), {
      target: { value: "not_document" },
    });
    await settle();
    expect(asked().at(-1)).toMatchObject({ code: "not_document" });
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "ready" } });
    await settle();
    expect(asked().at(-1)).toEqual({ state: "ready", offset: "0", limit: "50" });
  });

  it("keeps the filter in use in the list even when it has no files now", async () => {
    server.page.folders = [
      folder({ counts: counts({ byState: { ...counts().byState, failed: 1, ready: 9 } }) }),
    ];
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "failed" } });
    await settle();
    server.update("g1", { counts: counts() });
    await wait(30_000);
    expect(screen.getByTestId("folder-files-state")).toHaveValue("failed");
    expect(
      [...within(screen.getByTestId("folder-files-state")).getAllByRole("option")].map(
        (o) => o.textContent,
      ),
    ).toEqual(["All files (12)", "Ready (10)", "Skipped (2)", "Failed (0)"]);
  });

  it("makes the page of files being read from the steps they are at, the ones being read now first", async () => {
    const reading = counts({
      files: 12,
      byState: { queued: 8, reading: 1, searchable: 2, embedding: 0, ready: 1, skipped: 0 },
      byCode: {},
    });
    server.page.folders = [folder({ counts: reading })];
    server.files.set("g1", [
      waitingFile({ id: "a", path: "a-waiting.md" }),
      waitingFile({ id: "b", path: "b-reading.md", state: "reading" }),
      waitingFile({ id: "c", path: "c-found.md", state: "searchable", searchable: true }),
      folderFile({ id: "d", path: "d-ready.md" }),
    ]);
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-state"), { target: { value: "reading" } });
    await settle();
    expect(asked().slice(-3)).toEqual([
      { state: "reading", offset: "0", limit: "1" },
      { state: "searchable", offset: "0", limit: "2" },
      { state: "queued", offset: "0", limit: "8" },
    ]);
    // The ones being read now come first, then the ones found by words, then the ones that wait.
    expect(rows().map((r) => r.dataset.path)).toEqual([
      "b-reading.md",
      "c-found.md",
      "a-waiting.md",
    ]);
    expect(text("folder-files-range")).toBe("1 to 3 of 3");
  });

  it("finds a file by a word in its path, a moment after it is typed and not at every key", async () => {
    await openFiles();
    const box = screen.getByTestId("folder-files-find");
    expect(box).toHaveAccessibleName("Find a file");
    const before = asked().length;
    fireEvent.change(box, { target: { value: "ha" } });
    fireEvent.change(box, { target: { value: "harbour" } });
    await wait(299);
    expect(asked()).toHaveLength(before);
    await wait(1);
    expect(asked().at(-1)).toEqual({ q: "harbour", offset: "0", limit: "50" });
    expect(rows().map((r) => r.dataset.path)).toEqual(["docs/harbour-notes.md"]);
    expect(asked()).toHaveLength(before + 1);
  });

  it("does not send spaces round the word", async () => {
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-find"), { target: { value: "  harbour  " } });
    await wait(300);
    expect(asked().at(-1)).toMatchObject({ q: "harbour" });
  });

  it("asks for every file again once the word is cleared", async () => {
    await openFiles();
    fireEvent.change(screen.getByTestId("folder-files-find"), { target: { value: "harbour" } });
    await wait(300);
    fireEvent.change(screen.getByTestId("folder-files-find"), { target: { value: "" } });
    await wait(300);
    expect(asked().at(-1)).toEqual({ offset: "0", limit: "50" });
    expect(rows()).toHaveLength(4);
  });
});

describe("paging a folder's files", () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      folderFile({ id: `p${i}`, path: `file-${String(i).padStart(3, "0")}.md` }),
    );

  it("shows fifty at a time, and moves by fifty", async () => {
    server.files.set("g1", many(120));
    server.page.folders = [
      folder({ counts: counts({ files: 120, byState: { ready: 120 }, byCode: {} }) }),
    ];
    await openFiles();
    expect(rows()).toHaveLength(50);
    expect(text("folder-files-range")).toBe("1 to 50 of 120");
    expect(screen.getByTestId("folder-files-previous")).toBeDisabled();
    expect(screen.getByTestId("folder-files-next")).toBeEnabled();
    click("folder-files-next");
    await settle();
    expect(asked().at(-1)).toEqual({ offset: "50", limit: "50" });
    expect(rows()[0]!.dataset.path).toBe("file-050.md");
    expect(text("folder-files-range")).toBe("51 to 100 of 120");
    click("folder-files-next");
    await settle();
    expect(rows()).toHaveLength(20);
    expect(text("folder-files-range")).toBe("101 to 120 of 120");
    expect(screen.getByTestId("folder-files-next")).toBeDisabled();
    click("folder-files-previous");
    await settle();
    expect(text("folder-files-range")).toBe("51 to 100 of 120");
  });

  it("names its buttons for the folder, and has none for a list that is one page", async () => {
    await openFiles();
    expect(screen.queryByTestId("folder-files-next")).toBeNull();
    server.files.set("g1", many(60));
    server.page.folders = [
      folder({ counts: counts({ files: 60, byState: { ready: 60 }, byCode: {} }) }),
    ];
    await wait(1_000);
    await wait(30_000);
    expect(screen.getByTestId("folder-files-next")).toHaveAccessibleName(
      "Later files of Work notes",
    );
    expect(screen.getByTestId("folder-files-previous")).toHaveAccessibleName(
      "Earlier files of Work notes",
    );
  });

  it("goes back to the first page when a filter or a word is chosen", async () => {
    server.files.set("g1", many(120));
    server.page.folders = [
      folder({ counts: counts({ files: 120, byState: { ready: 120 }, byCode: {} }) }),
    ];
    await openFiles();
    click("folder-files-next");
    await settle();
    fireEvent.change(screen.getByTestId("folder-files-find"), { target: { value: "file" } });
    await wait(300);
    expect(asked().at(-1)).toEqual({ q: "file", offset: "0", limit: "50" });
  });

  it("goes back to its last page when the list got shorter than the page it is on", async () => {
    server.files.set("g1", many(120));
    server.page.folders = [
      folder({ counts: counts({ files: 120, byState: { ready: 120 }, byCode: {} }) }),
    ];
    await openFiles();
    click("folder-files-next");
    click("folder-files-next");
    await settle();
    expect(text("folder-files-range")).toBe("101 to 120 of 120");
    // Most of them go while the page is open.
    server.files.set("g1", many(60));
    server.update("g1", { counts: counts({ files: 60, byState: { ready: 60 }, byCode: {} }) });
    await wait(30_000);
    await settle();
    expect(text("folder-files-range")).toBe("51 to 60 of 60");
  });
});

describe("a file of a folder", () => {
  it("can be opened as Workbench holds it, under the name the server gives it", async () => {
    await openFiles();
    const open = within(row("docs/harbour-notes.md")).getByTestId("folder-file-open");
    expect(open).toHaveAccessibleName("Open original: docs/harbour-notes.md");
    fireEvent.click(open);
    await settle();
    expect(server.made("GET").some((c) => c.path === "/api/documents/f1/original")).toBe(true);
    expect(saveFile).toHaveBeenCalledTimes(1);
    expect(vi.mocked(saveFile).mock.calls[0]![1]).toBe("harbour-notes.md");
  });

  it("has no original to open when it was skipped or failed: it was never copied", async () => {
    await openFiles();
    expect(within(row("picture.png")).queryByTestId("folder-file-open")).toBeNull();
    expect(within(row("broken.pdf")).queryByTestId("folder-file-open")).toBeNull();
    expect(within(row("later.md")).getByTestId("folder-file-open")).toBeInTheDocument();
  });

  it("says why when it could not be opened, on its own row", async () => {
    await openFiles();
    vi.mocked(fileOf).mockRejectedValue(
      new Error("Workbench's copy of this file is not on its disk."),
    );
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-open"));
    await settle();
    expect(
      within(row("docs/harbour-notes.md")).getByTestId("folder-file-problem"),
    ).toHaveTextContent("Workbench's copy of this file is not on its disk.");
    expect(within(row("later.md")).queryByTestId("folder-file-problem")).toBeNull();
  });

  it("can show the text Workbench read, only once it can be searched", async () => {
    await openFiles();
    expect(
      within(row("docs/harbour-notes.md")).getByTestId("folder-file-text-toggle"),
    ).toBeInTheDocument();
    expect(within(row("picture.png")).queryByTestId("folder-file-text-toggle")).toBeNull();
    expect(within(row("later.md")).queryByTestId("folder-file-text-toggle")).toBeNull();
  });

  it("asks before it is left out, and says what that does", async () => {
    await openFiles();
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave"));
    expect(
      screen.getByRole("group", { name: "Leave out docs/harbour-notes.md?" }),
    ).toBeInTheDocument();
    expect(text("folder-file-leave-confirm")).toContain(
      "Leave out docs/harbour-notes.md? Workbench deletes its copy of the file and the text it read from it, and adds /docs/harbour-notes.md to this folder's ignore list, so that it does not come back at the next check. The file in your folder is not touched.",
    );
    expect(server.made("DELETE")).toEqual([]);
  });

  it("puts the keyboard on Keep it, and back on Leave out when the question is closed", async () => {
    await openFiles();
    const leave = within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave");
    fireEvent.click(leave);
    expect(screen.getByTestId("folder-file-leave-keep")).toHaveFocus();
    fireEvent.click(screen.getByTestId("folder-file-leave-keep"));
    expect(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave")).toHaveFocus();
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave"));
    fireEvent.keyDown(screen.getByTestId("folder-file-leave-confirm"), { key: "Escape" });
    expect(screen.queryByTestId("folder-file-leave-confirm")).toBeNull();
    expect(server.made("DELETE")).toEqual([]);
  });

  it("is left out: it goes from the list, and its path is on the folder's ignore list", async () => {
    await openFiles();
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave"));
    fireEvent.click(screen.getByTestId("folder-file-leave-go"));
    await settle();
    expect(server.made("DELETE")).toEqual([{ path: "/api/documents/f1", body: undefined }]);
    expect(rows().map((r) => r.dataset.path)).not.toContain("docs/harbour-notes.md");
    expect(server.folder("g1").ignore).toEqual(["/docs/harbour-notes.md"]);
  });

  it("says why when it could not be left out, as the server says: the ignore list is full", async () => {
    await openFiles();
    server.refuse.set(
      "DELETE /api/documents/f1",
      refusal(
        409,
        "This folder's ignore list is full, so the file would come back at the next check.",
      ),
    );
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave"));
    fireEvent.click(screen.getByTestId("folder-file-leave-go"));
    await settle();
    expect(
      within(row("docs/harbour-notes.md")).getByTestId("folder-file-problem"),
    ).toHaveTextContent(
      "This folder's ignore list is full, so the file would come back at the next check.",
    );
    expect(rows()).toHaveLength(4);
  });

  it("cannot be left out where the owner has turned documents off", async () => {
    await show(true);
    click("folder-files-toggle");
    await settle();
    expect(rows()).toHaveLength(4);
    expect(screen.queryByTestId("folder-file-leave")).toBeNull();
    expect(
      within(row("docs/harbour-notes.md")).getByTestId("folder-file-open"),
    ).toBeInTheDocument();
  });

  it("is told apart for a screen reader by its path, on every button", async () => {
    await openFiles();
    const names = within(row("docs/harbour-notes.md"))
      .getAllByRole("button")
      .map((b) => b.getAttribute("aria-label") ?? b.textContent);
    expect(names).toContain("Open original: docs/harbour-notes.md");
    expect(names).toContain("Leave out: docs/harbour-notes.md");
    expect(
      within(row("docs/harbour-notes.md")).getByTestId("folder-file-text-toggle"),
    ).toHaveAccessibleDescription("docs/harbour-notes.md");
  });
});

describe("what was left out of a folder", () => {
  async function openLeft() {
    await show();
    click("folder-left-toggle");
    await settle();
  }

  it("opens from the card and reads what the last check left out", async () => {
    await openLeft();
    expect(screen.getByTestId("folder-left-toggle")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("folder-left-toggle")).toHaveAccessibleName(
      "Hide what was left out: Work notes",
    );
    expect(server.made("GET").some((c) => c.path === "/api/documents/folders/g1")).toBe(true);
    expect(
      screen.getByRole("group", { name: "What was left out of Work notes" }),
    ).toBeInTheDocument();
  });

  it("groups it by the rule or limit that left it out, each with how many", async () => {
    await openLeft();
    const groups = screen.getAllByTestId("folder-left-group");
    expect(groups.map((g) => g.dataset.code)).toEqual([
      "ignored_default",
      "ignored_hidden",
      "ignored_gitignore",
      "link",
    ]);
    expect(groups.map((g) => within(g).getByRole("heading", { level: 4 }).textContent)).toEqual([
      "Always left out (1)",
      "Hidden (1)",
      "A .gitignore file (1)",
      "Links, not followed (1)",
    ]);
  });

  it("says each thing's path, and its reason in the server's words", async () => {
    await openLeft();
    const items = screen.getAllByTestId("folder-left-item");
    expect(
      items.map((i) => i.querySelector("[data-testid=folder-left-path]")!.textContent),
    ).toEqual(["node_modules/", ".env", "build/out.js", "shortcut"]);
    expect(items[0]!.querySelector("[data-testid=folder-left-reason]")!.textContent).toBe(
      "Ignored: a folder that holds other programs' files (node_modules).",
    );
    expect(items[3]!.querySelector("[data-testid=folder-left-reason]")!.textContent).toBe(
      "Not followed: this is a link. Workbench never follows links out of the folder.",
    );
  });

  it("says nothing was left out when nothing was", async () => {
    server.details.set("g1", detail({ left: [], leftCounts: {}, unlisted: [] }));
    await openLeft();
    expect(text("folder-left-nothing")).toBe(
      "Nothing was left out. Every name Workbench found was read or tried.",
    );
    expect(screen.queryByTestId("folder-left-group")).toBeNull();
  });

  it("says how many are not named when the server names only the first of many", async () => {
    server.details.set(
      "g1",
      detail({
        left: [leftOut({ path: "a" }), leftOut({ path: "b" })],
        leftCounts: { ignored_default: 450, over_files: 12 },
      }),
    );
    await openLeft();
    expect(screen.getAllByTestId("folder-left-total").map((t) => t.textContent)).toEqual([
      "(450)",
      "(12)",
    ]);
    expect(text("folder-left-group", 0)).toContain("2 of 450 are named here.");
    expect(text("folder-left-group", 1)).toContain("12 names not named here.");
    expect(text("folder-left-more")).toBe(
      "Workbench names the first 2 of the 462 things it left out.",
    );
  });

  it("says a folder that could not be looked into, with what was seen, apart from the rest", async () => {
    server.details.set(
      "g1",
      detail({
        unlisted: [
          { path: "private", cause: "permission denied" },
          { path: "", cause: "the disk is busy" },
        ],
      }),
    );
    server.update("g1", { last: { ...folder().last!, unlistedTotal: 2 } });
    await openLeft();
    const section = screen.getByTestId("folder-left-unlisted");
    expect(within(section).getByRole("heading", { level: 4 })).toHaveTextContent(
      "Could not be looked into (2)",
    );
    const items = within(section).getAllByTestId("folder-left-unlisted-item");
    expect(items[0]).toHaveTextContent("private");
    expect(items[0]).toHaveTextContent(
      "Workbench could not look in it (permission denied). Its files are as they were at the last check, and it looks again at the next.",
    );
    expect(items[1]).toHaveTextContent("The folder itself");
  });

  it("says a reason it does not know, under its own name", async () => {
    server.details.set(
      "g1",
      detail({
        left: [
          leftOut({ code: "brand_new_reason", path: "x", kind: "file", reason: "Left out: new." }),
        ],
        leftCounts: { brand_new_reason: 1 },
      }),
    );
    await openLeft();
    expect(screen.getByRole("heading", { level: 4 })).toHaveTextContent("brand new reason (1)");
  });

  it("says why when it could not be read, and reads again with Try again", async () => {
    await show();
    server.refuse.set("GET /api/documents/folders/g1", new Error("Workbench answered 502."));
    click("folder-left-toggle");
    await settle();
    expect(screen.getByTestId("folder-left-problem")).toHaveTextContent(
      "Could not read what was left out" + "Workbench answered 502.",
    );
    server.refuse.clear();
    click("folder-left-retry");
    await settle();
    expect(screen.queryByTestId("folder-left-problem")).toBeNull();
    expect(screen.getAllByTestId("folder-left-group")).toHaveLength(4);
  });

  it("is read again after each check, so it never says what an older one found", async () => {
    await openLeft();
    const reads = () =>
      server.made("GET").filter((c) => c.path === "/api/documents/folders/g1").length;
    expect(reads()).toBe(1);
    server.details.set(
      "g1",
      detail({ left: [leftOut({ path: "dist" })], leftCounts: { ignored_default: 1 } }),
    );
    server.update("g1", { checkedAt: NOW + 600, syncedAt: NOW + 600, nextAt: NOW + 1_200 });
    await wait(30_000);
    expect(reads()).toBe(2);
    expect(text("folder-left-path")).toBe("dist/");
  });

  it("closes again, and the list goes", async () => {
    await openLeft();
    click("folder-left-toggle");
    expect(screen.queryByTestId("folder-left")).toBeNull();
  });

  it("never puts back what an older check found over what a newer one did", async () => {
    await show();
    const real = vi.mocked(api).getMockImplementation()!;
    let release: (found: unknown) => void = () => undefined;
    let slow = true;
    vi.mocked(api).mockImplementation(((path: string, init?: RequestInit) => {
      if (path === "/api/documents/folders/g1" && slow) {
        slow = false;
        return new Promise((done) => (release = done));
      }
      return real(path, init);
    }) as never);
    click("folder-left-toggle");
    await settle();
    // A check ends while the first read is still on its way: the list is read again.
    server.details.set(
      "g1",
      detail({ left: [leftOut({ path: "dist" })], leftCounts: { ignored_default: 1 } }),
    );
    server.update("g1", { checkedAt: NOW + 600, syncedAt: NOW + 600, nextAt: NOW + 1_200 });
    await wait(30_000);
    expect(text("folder-left-path")).toBe("dist/");
    await act(async () => {
      release(detail());
      await Promise.resolve();
    });
    expect(screen.getAllByTestId("folder-left-path").map((p) => p.textContent)).toEqual(["dist/"]);
  });
});

describe("the rules of a folder", () => {
  async function openRules(over: Parameters<typeof folder>[0] = {}) {
    server.page.folders = [folder(over)];
    await show();
    click("folder-rules-toggle");
    await settle();
  }

  it("opens from the card, and shows the rules in effect", async () => {
    await openRules({ ignore: ["*.log", "/archive/"], includeHidden: true, useGitignore: false });
    expect(screen.getByTestId("folder-rules-toggle")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("folder-rules-toggle")).toHaveAccessibleName(
      "Hide rules: Work notes",
    );
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("*.log\n/archive/");
    expect(screen.getByTestId("folder-rules-hidden")).toBeChecked();
    expect(screen.getByTestId("folder-rules-gitignore")).not.toBeChecked();
    expect(text("folder-rules-count")).toBe("2 of 100 patterns");
    expect(screen.getByRole("group", { name: "What Work notes leaves out" })).toBeInTheDocument();
  });

  it("shows the defaults for a folder that has none set: .gitignore respected, hidden files not read", async () => {
    await openRules();
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("");
    expect(screen.getByTestId("folder-rules-hidden")).not.toBeChecked();
    expect(screen.getByTestId("folder-rules-gitignore")).toBeChecked();
  });

  it("explains each field, from the server's own words where it has them", async () => {
    await openRules();
    expect(screen.getByTestId("folder-rules-ignore")).toHaveAccessibleName(
      "Leave out (one pattern on each line)",
    );
    expect(screen.getByTestId("folder-rules-ignore")).toHaveAccessibleDescription(
      /Written like a .gitignore file\..*A pattern cannot start with ! or #\..*0 of 100 patterns/,
    );
    expect(screen.getByTestId("folder-rules-hidden")).toHaveAccessibleDescription(
      "Names that start with a dot, and files and folders Windows marks hidden. They are left out unless you turn this on.",
    );
    expect(screen.getByTestId("folder-rules-gitignore")).toHaveAccessibleDescription(
      "A folder's own .gitignore files, at any depth. A project's build output is not what you mean to search, so this is on unless you turn it off.",
    );
  });

  it("says what saving does before it is done", async () => {
    await openRules();
    expect(screen.getByTestId("folder-rules").textContent).toContain(
      "Workbench checks the folder again at once. Files that are left out now leave your documents, and files that are not are read.",
    );
  });

  it("can be saved only once something is changed, and put back", async () => {
    await openRules({ ignore: ["*.log"] });
    expect(screen.getByTestId("folder-rules-save")).toBeDisabled();
    expect(screen.getByTestId("folder-rules-revert")).toBeDisabled();
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), {
      target: { value: "*.log\n*.tmp" },
    });
    expect(screen.getByTestId("folder-rules-save")).toBeEnabled();
    expect(screen.getByTestId("folder-rules-revert")).toBeEnabled();
    expect(text("folder-rules-count")).toBe("2 of 100 patterns");
    fireEvent.click(screen.getByTestId("folder-rules-revert"));
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("*.log");
    expect(screen.getByTestId("folder-rules-save")).toBeDisabled();
  });

  it("sends the list, a pattern on each line, and the two switches, all at once", async () => {
    await openRules({ ignore: ["*.log"] });
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), {
      target: { value: "*.log\n\n/archive/\n  \n" },
    });
    fireEvent.click(screen.getByTestId("folder-rules-hidden"));
    fireEvent.click(screen.getByTestId("folder-rules-gitignore"));
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(server.calls.filter((c) => c.method === "PATCH")).toEqual([
      {
        method: "PATCH",
        path: "/api/documents/folders/g1",
        body: { ignore: ["*.log", "/archive/"], includeHidden: true, useGitignore: false },
      },
    ]);
  });

  it("says it was saved and that the folder is checked again, and shows the card waiting for it", async () => {
    await openRules();
    fireEvent.click(screen.getByTestId("folder-rules-hidden"));
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(screen.getByTestId("folder-rules-saved")).toHaveTextContent(
      "Saved. Workbench is checking the folder again.",
    );
    expect(screen.getByTestId("folder-rules-saved")).toHaveAttribute("role", "status");
    expect(text("folder-badge")).toBe("Waiting");
    // The editor shows what the server now has, and is not changed.
    expect(screen.getByTestId("folder-rules-hidden")).toBeChecked();
    expect(screen.getByTestId("folder-rules-save")).toBeDisabled();
    // Typing again takes the message away.
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), { target: { value: "x" } });
    expect(screen.queryByTestId("folder-rules-saved")).toBeNull();
  });

  it("says a pattern the server cannot use under the patterns, in its words, and keeps what was typed", async () => {
    await openRules();
    server.refuse.set(
      "PATCH /api/documents/folders/g1",
      refusal(422, "A pattern cannot start with #, which is a comment. Write \\# for one."),
    );
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), { target: { value: "# comment" } });
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(text("folder-rules-error")).toBe(
      "A pattern cannot start with #, which is a comment. Write \\# for one.",
    );
    expect(screen.getByTestId("folder-rules-error")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("folder-rules-ignore")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("# comment");
    expect(screen.queryByTestId("folder-rules-saved")).toBeNull();
    // Nothing changed on the server, and the card does not say it is checking.
    expect(server.folder("g1").ignore).toEqual([]);
    expect(text("folder-badge")).toBe("Synced");
    // Typing clears it.
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), {
      target: { value: "\\# comment" },
    });
    expect(screen.queryByTestId("folder-rules-error")).toBeNull();
  });

  it("says a refusal for a folder the owner withdrew, in the same place", async () => {
    await openRules();
    server.refuse.set(
      "PATCH /api/documents/folders/g1",
      refusal(409, "The owner took away your access to this folder."),
    );
    fireEvent.click(screen.getByTestId("folder-rules-hidden"));
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(text("folder-rules-error")).toBe("The owner took away your access to this folder.");
  });

  it("is held while it saves", async () => {
    await openRules();
    fireEvent.click(screen.getByTestId("folder-rules-hidden"));
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    expect(screen.getByTestId("folder-rules-save")).toBeDisabled();
    expect(screen.getByTestId("folder-rules-ignore")).toBeDisabled();
    await settle();
    expect(screen.getByTestId("folder-rules-ignore")).toBeEnabled();
  });

  it("takes up rules that changed under it: a file left out from the list is on the list at once", async () => {
    server.files.set("g1", [folderFile()]);
    server.page.folders = [folder()];
    await show();
    click("folder-rules-toggle");
    click("folder-files-toggle");
    await settle();
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("");
    fireEvent.click(within(row("docs/harbour-notes.md")).getByTestId("folder-file-leave"));
    fireEvent.click(screen.getByTestId("folder-file-leave-go"));
    await settle();
    expect(screen.getByTestId("folder-rules-ignore")).toHaveValue("/docs/harbour-notes.md");
    expect(screen.getByTestId("folder-rules-save")).toBeDisabled();
  });

  it("says what is always left out, from the server's list, whatever is set", async () => {
    await openRules();
    expect(text("folder-rules-always")).toContain("Folders: .git, node_modules");
    expect(text("folder-rules-always")).toContain(
      "These stay left out even when hidden files are read.",
    );
    expect(screen.getByTestId("folder-rules-always-toggle")).toHaveAccessibleName(
      "What is always left out",
    );
  });

  it("closes again, and the editor goes", async () => {
    await openRules();
    click("folder-rules-toggle");
    expect(screen.queryByTestId("folder-rules")).toBeNull();
  });
});
