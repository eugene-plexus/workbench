/**
 * The Documents screen (MR4): what a person sees, from opening it to removing a
 * document, against a small fake Workbench that answers as the real one does
 * (src/eugene_plexus_workbench/saved_api.py): it lists documents, reads them in turn
 * (queued, then reading, then ready) as it is asked, keeps settings, and refuses what
 * the real one refuses.
 */

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, api, del, documentText, fileOf, post, put, upload } from "../lib/api";
import { saveFile } from "../lib/memory";
import { FIRST_POLL_MS, IDLE_POLL_MS, POLL_MS, WAITING_POLL_MS } from "../lib/savedDocuments";
import type {
  DocumentFolders,
  DocumentsPrefs,
  Me,
  SavedDocument,
  SearchFacts,
  TestedQuestion,
} from "../lib/types";
import {
  choice,
  counts as folderCounts,
  folder,
  foldersPage,
  problemFolder,
  readingFolder,
  waitingFolder,
} from "../test-folders";
import {
  adding,
  broken,
  byMeaning,
  limited,
  markdown,
  meaningOf,
  noSearchModel,
  page,
  paused,
  prefs,
  queued,
  reading,
  saved,
  scanned,
  searchFor,
  SORT_CODES,
  sortFacts,
  sortFor,
  SORTER,
  tested,
  waiting,
  withMeaning,
  word,
  wordsOnly,
  wordsOnlyDoc,
} from "../test-saved";
import { Documents } from "./Documents";
import { DocumentsSettings } from "./DocumentsSettings";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  documentText: vi.fn(),
  fileOf: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  upload: vi.fn(),
}));
vi.mock("../lib/memory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/memory")>()),
  saveFile: vi.fn(),
}));

// --- a Workbench that keeps documents, as far as the page can tell ---

interface Server {
  docs: SavedDocument[];
  prefs: DocumentsPrefs;
  installEnabled: boolean;
  ownerReadsChats: boolean;
  /** Set where a test says a headline of its own; else the one the state of the search has. */
  headline: string | null;
  meaning: boolean;
  /** More of the header than the two above: its code, detail, actions, model, adding, rebuild. */
  search: Partial<SearchFacts>;
  /** While set, every read of the documents is refused with it. */
  refusing: ApiError | null;
  tested: TestedQuestion;
  /** Ids the server will not read again: the original is gone. */
  gone: Set<string>;
  /** Ids it cannot remove. */
  stuck: Set<string>;
  rebuilt: { documents: number; left: number };
  /** While set, a request to add meaning again is refused with it. */
  meaningRefused: ApiError | null;
  /** How a document moves on each time the list is read. */
  advance: (docs: SavedDocument[]) => SavedDocument[];
  /** The folders (MR7): none, as most of these tests are about the person's own files. */
  folders: DocumentFolders;
}

let server: Server;
let reads: number;
/** What was asked of the test: its body, as the page sent it. */
let tries: Record<string, unknown>[];
/** How many times meaning was asked to be added again. */
let meaningRebuilds: number;

const fresh = (over: Partial<Server> = {}): Server => ({
  docs: [],
  prefs: prefs(),
  installEnabled: true,
  ownerReadsChats: false,
  headline: null,
  meaning: false,
  search: {},
  refusing: null,
  tested: tested(),
  gone: new Set(),
  stuck: new Set(),
  rebuilt: { documents: 2, left: 0 },
  meaningRefused: null,
  advance: (docs) => docs,
  folders: foldersPage(),
  ...over,
});

const seen = () =>
  page(server.docs, {
    installEnabled: server.installEnabled,
    ownerReadsChats: server.ownerReadsChats,
    prefs: server.prefs,
    search: {
      ...(server.meaning ? byMeaning() : wordsOnly()),
      ...(server.headline ? { headline: server.headline } : {}),
      ...server.search,
    },
  });

const refuse = (message: string, status: number, extra: object = {}) =>
  new ApiError(message, status, { message, ...extra });

function listen() {
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/documents/folders") return server.folders;
    if (path !== "/api/documents") throw new Error(`not served: ${path}`);
    reads += 1;
    if (server.refusing) throw server.refusing;
    const now = seen();
    server.docs = server.advance(server.docs);
    return now;
  }) as never);
  vi.mocked(put).mockImplementation((async (path: string, body: Record<string, unknown>) => {
    if (path !== "/api/documents/prefs") throw new Error(`not served: ${path}`);
    const next = { ...server.prefs };
    if ("newChats" in body) next.newChats = Boolean(body.newChats);
    if ("external" in body) next.external = (body.external ?? null) as DocumentsPrefs["external"];
    server.prefs = prefs({
      ...next,
      effective: {
        newChats: next.newChats,
        external: next.external ?? "ask",
        externalSetBy: next.external === null ? "default" : "you",
      },
    });
    return server.prefs;
  }) as never);
  vi.mocked(post).mockImplementation((async (path: string, body?: Record<string, unknown>) => {
    if (path === "/api/documents/rebuild") {
      server.refusing = null;
      server.docs = Array.from({ length: server.rebuilt.documents }, (_, n) =>
        queued({ id: `r${n}`, name: `again-${n}.md` }),
      );
      return server.rebuilt;
    }
    if (path === "/api/documents/meaning/rebuild") {
      meaningRebuilds += 1;
      if (server.meaningRefused) throw server.meaningRefused;
      // Every document is owed all of its meaning, and found by its words while it is added.
      const owed = server.docs.reduce((sum, d) => sum + (d.searchable ? d.passages : 0), 0);
      server.docs = server.docs.map((d) =>
        d.searchable
          ? {
              ...d,
              state: "searchable",
              embedded: 0,
              meaning: meaningOf({
                embedded: 0,
                total: d.passages,
                state: "adding",
                message: `Ready for words. Adding meaning: 0 of ${d.passages} passages.`,
              }),
            }
          : d,
      );
      server.meaning = true;
      server.search = {};
      server.headline = "Searching by meaning and words.";
      return { passages: owed, search: byMeaning() };
    }
    if (path === "/api/documents/test") {
      asked.push(String(body?.question));
      tries.push(body ?? {});
      return server.tested;
    }
    const again = /^\/api\/documents\/([^/]+)\/reread$/.exec(path);
    if (again) {
      const id = again[1]!;
      if (server.gone.has(id)) {
        throw refuse(
          "Workbench's copy of this file is not on its disk any more. Add the file again.",
          409,
        );
      }
      server.docs = server.docs.map((d) =>
        d.id === id ? { ...d, state: "queued", searchable: false, passages: 0, message: null } : d,
      );
      return server.docs.find((d) => d.id === id);
    }
    throw new Error(`not served: ${path}`);
  }) as never);
  vi.mocked(del).mockImplementation((async (path: string) => {
    const id = decodeURIComponent(path.split("/").pop()!);
    if (server.stuck.has(id))
      throw refuse("Workbench could not remove report.pdf (disk error).", 500);
    server.docs = server.docs.filter((d) => d.id !== id);
  }) as never);
}

let asked: string[];

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(put).mockReset();
  vi.mocked(del).mockReset();
  vi.mocked(upload).mockReset();
  vi.mocked(fileOf)
    .mockReset()
    .mockResolvedValue({ blob: new Blob(["pdf"]), name: "report.pdf" });
  vi.mocked(documentText)
    .mockReset()
    .mockResolvedValue("[page 4]\nThe code word is TAMARIND-6042.");
  vi.mocked(saveFile).mockReset();
  server = fresh();
  reads = 0;
  asked = [];
  tries = [];
  meaningRebuilds = 0;
  listen();
});
afterEach(() => vi.useRealTimers());

const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** The screen, opened, and the first read let in. `me` is who is reading, where it is known. */
async function opened(over: Partial<Server> = {}, me?: Pick<Me, "owner" | "consoleUrl">) {
  server = fresh(over);
  const onClose = vi.fn();
  const onAsk = vi.fn();
  const onOpenSettings = vi.fn();
  const view = render(
    <Documents onClose={onClose} onAsk={onAsk} onOpenSettings={onOpenSettings} me={me} />,
  );
  await wait(0);
  return { ...view, onClose, onAsk, onOpenSettings };
}

/** Documents' settings, opened, and the read let in. */
async function openedSettings(over: Partial<Server> = {}) {
  server = fresh(over);
  const onOpenDocuments = vi.fn();
  const view = render(<DocumentsSettings onOpenDocuments={onOpenDocuments} />);
  await wait(0);
  return { ...view, onOpenDocuments };
}

const rows = () => screen.queryAllByTestId("saved-document");
const row = (name: string) =>
  rows().find((r) => within(r).getByTestId("saved-document-name").textContent === name)!;
const status = (name: string) => within(row(name)).getByTestId("saved-document-status");

describe("opening the screen", () => {
  it("says it is reading until the documents are here", async () => {
    server = fresh();
    render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading your documents…");
    expect(screen.queryByTestId("documents-intro")).toBeNull();
    await wait(0);
    expect(screen.queryByText("Loading your documents…")).toBeNull();
  });

  it("is a page with a heading that takes the keyboard, and a way back to the chat", async () => {
    const { onClose } = await opened();
    const section = screen.getByTestId("documents-page");
    expect(section).toHaveAttribute("aria-label", "Documents");
    const heading = screen.getByRole("heading", { level: 1, name: "Documents" });
    expect(heading).toHaveAttribute("id", "view-heading");
    expect(heading).toHaveAttribute("tabindex", "-1");
    fireEvent.click(screen.getByTestId("documents-back"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("says in one line what it is, and says search is by words only, in the server's own headline", async () => {
    await opened();
    expect(screen.getByTestId("documents-intro")).toHaveTextContent(
      "Your own files, searched for the words in your question. Only you can see them.",
    );
    const headline = screen.getByTestId("documents-search");
    expect(headline).toHaveTextContent("Searching by words only.");
    // It says what words only means and promises nothing more.
    expect(headline).toHaveTextContent("It does not look for meaning");
    expect(headline.textContent).not.toMatch(/\b(soon|later|will|coming|meaning and words)\b/i);
  });

  it("says what the server says of the search, and no more, when it searches by meaning too", async () => {
    await opened({ headline: "Searching by meaning and words.", meaning: true });
    const headline = screen.getByTestId("documents-search");
    expect(headline).toHaveTextContent("Searching by meaning and words.");
    expect(headline).not.toHaveTextContent("It does not look for meaning");
  });

  it("is empty at first: where to start, and nothing to test or ask yet", async () => {
    await opened();
    const empty = screen.getByTestId("documents-empty");
    expect(empty).toHaveTextContent("No documents yet");
    expect(empty).toHaveTextContent("Add files above.");
    expect(screen.getByTestId("documents-add")).toBeInTheDocument();
    expect(screen.queryByTestId("documents-list")).toBeNull();
    expect(screen.queryByTestId("documents-test")).toBeNull();
    expect(screen.queryByTestId("documents-ask")).toBeNull();
    expect(screen.queryByTestId("documents-totals")).toBeNull();
  });

  it("says nothing of how it works underneath", async () => {
    await opened({ docs: [saved(), markdown()] });
    expect(screen.getByTestId("documents-page").textContent).not.toMatch(
      /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*)\b/i,
    );
  });
});

describe("the list", () => {
  it("is each document with its name, what it is, where it stands and the day it was added", async () => {
    await opened({ docs: [saved(), markdown(), word()] });
    expect(rows().map((r) => within(r).getByTestId("saved-document-name").textContent)).toEqual([
      "harbour-survey.pdf",
      "field-notes.md",
      "quarterly-report.docx",
    ]);
    const pdf = row("harbour-survey.pdf");
    expect(within(pdf).getByTestId("saved-document-meta")).toHaveTextContent(
      /^PDF · 9 KiB · 5 pages · 5 passages · Added Oct 11$/,
    );
    expect(within(pdf).getByTestId("saved-document-meta").querySelector("time")).toHaveAttribute(
      "datetime",
      "2026-10-11T15:00:00.000Z",
    );
    expect(within(row("field-notes.md")).getByTestId("saved-document-meta")).toHaveTextContent(
      /^Markdown · 1 KiB · 5 lines · 1 passage · /,
    );
    expect(status("harbour-survey.pdf")).toHaveTextContent("Ready");
    expect(pdf).toHaveAttribute("data-state", "ready");
  });

  it("says each state in the design's words, and a skip or a failure in the server's own", async () => {
    await opened({
      docs: [
        queued({ id: "q", name: "waiting.md" }),
        reading({ id: "r", name: "reading.md", pages: 40 }),
        saved({ id: "s", name: "searchable.pdf", state: "searchable" }),
        saved({ id: "ok", name: "ready.pdf" }),
        scanned(),
        broken(),
      ],
    });
    expect(status("waiting.md")).toHaveTextContent("Waiting to be read");
    expect(status("reading.md")).toHaveTextContent("Reading… 40 pages");
    expect(status("searchable.pdf")).toHaveTextContent("Ready for words");
    expect(status("ready.pdf")).toHaveTextContent("Ready");
    const skipped = status("scan.pdf");
    expect(skipped).toHaveTextContent(
      "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.",
    );
    expect(skipped).toHaveClass("notice", "notice--warn");
    const failed = status("broken.pdf");
    expect(failed).toHaveTextContent(
      "Failed: Workbench could not read this file (the PDF is cut off). It may be damaged.",
    );
    expect(failed).toHaveClass("notice", "notice--error");
    expect(row("reading.md")).toHaveAttribute("aria-busy", "true");
    expect(row("ready.pdf")).not.toHaveAttribute("aria-busy");
  });

  it("lists the reader's notes about a document that was read", async () => {
    await opened({ docs: [saved({ notes: ["Page 2 has no text (it may be a picture)."] })] });
    expect(within(row("harbour-survey.pdf")).getByTestId("saved-document-notes")).toHaveTextContent(
      "Page 2 has no text (it may be a picture).",
    );
  });

  it("leaves the reader's notes out for a document that was skipped or failed: its own words say why", async () => {
    await opened({
      docs: [
        scanned({ notes: ["Page 1 has no text."] }),
        broken({ notes: ["Page 2 is cut off."] }),
      ],
    });
    expect(within(row("scan.pdf")).queryByTestId("saved-document-notes")).toBeNull();
    expect(within(row("broken.pdf")).queryByTestId("saved-document-notes")).toBeNull();
  });

  it("offers what can be done with each: the original and removing always, the text once it can be found, reading again once it has stopped", async () => {
    await opened({
      docs: [queued({ id: "q", name: "waiting.md" }), saved(), scanned(), broken()],
    });
    const names = (name: string) =>
      within(row(name))
        .getAllByRole("button")
        .map((b) => b.getAttribute("data-testid"));
    expect(names("waiting.md")).toEqual(["saved-document-open", "saved-document-remove"]);
    expect(names("harbour-survey.pdf")).toEqual([
      "saved-document-open",
      "saved-document-text-toggle",
      "saved-document-reread",
      "saved-document-remove",
    ]);
    // A scan has no text to show, and can be read again; so can a file that could not be read.
    for (const name of ["scan.pdf", "broken.pdf"]) {
      expect(names(name), name).toEqual([
        "saved-document-open",
        "saved-document-reread",
        "saved-document-remove",
      ]);
    }
  });

  it("names each button for the document it acts on, so a row of buttons can be told apart", async () => {
    await opened({ docs: [saved(), markdown()] });
    expect(
      within(row("harbour-survey.pdf")).getByRole("button", {
        name: "Open original: harbour-survey.pdf",
      }),
    ).toBeInTheDocument();
    expect(
      within(row("field-notes.md")).getByRole("button", { name: "Remove: field-notes.md" }),
    ).toBeInTheDocument();
    expect(
      within(row("field-notes.md")).getByRole("button", { name: "Re-read: field-notes.md" }),
    ).toBeInTheDocument();
    expect(
      within(row("field-notes.md")).getByRole("button", { name: "Show indexed text" }),
    ).toHaveAccessibleDescription("field-notes.md");
  });

  it("states the totals and the caps", async () => {
    await opened({ docs: [saved(), markdown(), word()] });
    expect(screen.getByTestId("documents-totals")).toHaveTextContent(
      "3 documents · 9 passages · 46 KiB",
    );
    const caps = screen.getByTestId("documents-caps");
    expect(
      within(caps)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      "A file may be up to 50 MiB.",
      "Workbench keeps up to 100,000 passages for you (9 used).",
      "It reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
      "It stops reading a file that takes longer than 120 seconds.",
    ]);
  });
});

describe("watching documents being read", () => {
  /** Each time the list is read, every document moves one step along. */
  const step = (docs: SavedDocument[]): SavedDocument[] =>
    docs.map((d) =>
      d.state === "queued"
        ? { ...d, state: "reading" }
        : d.state === "reading"
          ? { ...d, state: "ready", searchable: true, passages: 3, pages: null, lines: 9 }
          : d,
    );

  it("asks again by itself, shows each document go from waiting to reading to ready, and stops", async () => {
    await opened({ docs: [queued({ id: "q", name: "a.md" })], advance: step });
    expect(status("a.md")).toHaveTextContent("Waiting to be read");
    await wait(FIRST_POLL_MS);
    expect(status("a.md")).toHaveTextContent("Reading…");
    await wait(POLL_MS);
    expect(status("a.md")).toHaveTextContent("Ready");
    expect(within(row("a.md")).getByTestId("saved-document-meta")).toHaveTextContent("3 passages");
    const done = reads;
    // Nothing is going on: only the half-minute look at the search model is left.
    await wait(IDLE_POLL_MS - 1);
    expect(reads).toBe(done);
    await wait(1);
    expect(reads).toBe(done + 1);
  });

  it("asks only for the half-minute look while every document has stopped", async () => {
    await opened({ docs: [saved(), scanned(), broken()] });
    await wait(IDLE_POLL_MS - 1);
    expect(reads).toBe(1);
    await wait(1);
    expect(reads).toBe(2);
  });
});

describe("when the documents cannot be read", () => {
  it("says why, in the server's words, and tries again on request", async () => {
    server = fresh({ refusing: new Error("Workbench answered 502.") as ApiError });
    render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await wait(0);
    const failure = screen.getByRole("alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure).toHaveTextContent("Could not read your documents");
    expect(failure).toHaveTextContent("Workbench answered 502.");
    server.refusing = null;
    server.docs = [saved()];
    fireEvent.click(screen.getByTestId("documents-retry"));
    await wait(0);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(rows()).toHaveLength(1);
  });

  it("keeps what it showed when a later read fails, and says so", async () => {
    await opened({ docs: [queued({ id: "q", name: "a.md" })] });
    server.refusing = new Error("Workbench answered 502.") as ApiError;
    await wait(FIRST_POLL_MS);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not read your documents again");
    expect(rows()).toHaveLength(1);
    server.refusing = null;
    fireEvent.click(screen.getByTestId("documents-retry"));
    await wait(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says a damaged search is damaged, in the server's words, shows nothing that may be out of date, and offers Rebuild search", async () => {
    await opened({
      docs: [saved()],
      refusing: refuse(
        "The search index for your documents is damaged (SQLite said: malformed). Your files are safe.",
        503,
        {
          code: "index_damaged",
          canRebuild: true,
        },
      ),
    });
    await wait(POLL_MS);
    const notice = screen.getByTestId("documents-damaged");
    expect(notice).toHaveAttribute("role", "alert");
    expect(notice).toHaveClass("notice--error");
    expect(notice).toHaveTextContent("Your documents' search is damaged");
    expect(notice).toHaveTextContent(
      "The search index for your documents is damaged (SQLite said: malformed). Your files are safe.",
    );
    expect(notice).toHaveTextContent("Nothing you added is lost.");
    expect(screen.getByRole("button", { name: "Rebuild search" })).toBeInTheDocument();
    expect(screen.queryByTestId("documents-list")).toBeNull();
    expect(screen.queryByTestId("documents-test")).toBeNull();
  });

  it("makes the search again from the person's files, and shows them being read", async () => {
    await opened({
      refusing: refuse(
        "The search index for your documents is damaged. Your files are safe.",
        503,
        {
          code: "index_damaged",
          canRebuild: true,
        },
      ),
      rebuilt: { documents: 2, left: 1 },
    });
    fireEvent.click(screen.getByTestId("documents-rebuild"));
    await wait(0);
    expect(vi.mocked(post)).toHaveBeenCalledWith("/api/documents/rebuild");
    expect(screen.queryByTestId("documents-damaged")).toBeNull();
    expect(screen.getByTestId("documents-rebuilt")).toHaveTextContent(
      "Workbench is making the search again from 2 documents. They are read again one at a time. 1 file could not be made into a document.",
    );
    expect(rows().map((r) => r.getAttribute("data-state"))).toEqual(["queued", "queued"]);
  });

  it("offers no Rebuild for what Rebuild cannot mend, and still says why and lets the person try again", async () => {
    await opened({
      refusing: refuse(
        "Workbench cannot search documents: this Python was built without SQLite full-text search. Reinstall Workbench from Eugene's console.",
        503,
        { code: "fts_missing", canRebuild: false },
      ),
    });
    const notice = screen.getByTestId("documents-damaged");
    expect(notice).toHaveTextContent("Your documents cannot be searched right now");
    expect(notice).toHaveTextContent("Reinstall Workbench from Eugene's console.");
    expect(screen.queryByTestId("documents-rebuild")).toBeNull();
    expect(screen.getByTestId("documents-retry")).toBeInTheDocument();
  });

  it("says why a rebuild that was refused was", async () => {
    await opened({
      refusing: refuse("The search index for your documents is damaged.", 503, {
        code: "index_damaged",
        canRebuild: true,
      }),
    });
    vi.mocked(post).mockRejectedValueOnce(
      refuse("The owner of this Workbench has turned off documents.", 403),
    );
    fireEvent.click(screen.getByTestId("documents-rebuild"));
    await wait(0);
    expect(screen.getByTestId("documents-problem")).toHaveTextContent(
      "The owner of this Workbench has turned off documents.",
    );
  });
});

describe("where the owner has turned documents off", () => {
  it("says so, and offers nothing that would keep a document", async () => {
    await opened({
      installEnabled: false,
      docs: [saved()],
      prefs: prefs({ effective: { enabled: false, disabledBy: "owner" } }),
    });
    const notice = screen.getByTestId("documents-owner-off");
    expect(notice).toHaveTextContent("The owner of this Workbench has turned off documents.");
    expect(notice).toHaveTextContent("No document is searched, and no file can be added.");
    expect(notice).toHaveTextContent("The documents you already have stay yours");
    expect(screen.queryByTestId("documents-intro")).toBeNull();
    expect(screen.queryByTestId("documents-add")).toBeNull();
    expect(screen.queryByTestId("documents-input")).toBeNull();
    expect(screen.queryByTestId("documents-test")).toBeNull();
    expect(screen.queryByTestId("documents-ask")).toBeNull();
    expect(screen.queryByTestId("documents-new-chats-switch")).toBeNull();
    expect(screen.queryByTestId("documents-external-ask")).toBeNull();
  });

  it("leaves what the person already has to open, read and remove, and not to read again", async () => {
    await opened({ installEnabled: false, docs: [saved()] });
    const buttons = within(row("harbour-survey.pdf"))
      .getAllByRole("button")
      .map((b) => b.getAttribute("data-testid"));
    expect(buttons).toEqual([
      "saved-document-open",
      "saved-document-text-toggle",
      "saved-document-remove",
    ]);
  });

  it("is only that line for a person with nothing kept", async () => {
    await opened({ installEnabled: false, docs: [] });
    expect(screen.getByTestId("documents-owner-off")).not.toHaveTextContent("stay yours");
    expect(screen.queryByTestId("documents-list")).toBeNull();
    expect(screen.queryByTestId("documents-empty")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Your documents" })).toBeNull();
  });
});

describe("what the owner can read", () => {
  it("is said where the owner may read chats: an answer can quote a document, and the owner cannot open this page", async () => {
    await opened({ ownerReadsChats: true });
    expect(screen.getByTestId("documents-owner-reads")).toHaveTextContent(
      "The owner of this Workbench can read your chats, including any passages an answer quotes. The owner cannot open this page or your files.",
    );
  });

  it("is not said where the owner cannot read chats", async () => {
    await opened({ ownerReadsChats: false });
    expect(screen.queryByTestId("documents-owner-reads")).toBeNull();
  });
});

describe("the page holds no settings, and points to them", () => {
  it("has none of the controls, and Documents settings opens them", async () => {
    const { onOpenSettings } = await opened();
    expect(screen.queryByTestId("documents-new-chats-switch")).toBeNull();
    expect(screen.queryByTestId("documents-external-ask")).toBeNull();
    expect(screen.queryByTestId("documents-external-effective")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Settings" })).toBeNull();
    fireEvent.click(screen.getByTestId("documents-open-settings"));
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });
});

describe("the settings (now on the Settings page)", () => {
  it("say so when the owner has turned documents off, and hold no control", async () => {
    await openedSettings({
      installEnabled: false,
      prefs: prefs({ effective: { enabled: false, disabledBy: "owner" } }),
    });
    expect(screen.getByTestId("documents-owner-off")).toBeInTheDocument();
    expect(screen.queryByTestId("documents-new-chats-switch")).toBeNull();
    expect(screen.queryByTestId("documents-external-ask")).toBeNull();
  });

  it("show Documents in new chats as it is in effect", async () => {
    await openedSettings();
    const box = screen.getByTestId("documents-new-chats-switch");
    expect(box).toHaveAttribute("role", "switch");
    expect(box).not.toBeChecked();
    expect(screen.getByTestId("documents-new-chats-effective")).toHaveTextContent(
      "New chats start with Documents off. Turn it on in any chat with its Documents button.",
    );
  });

  it("turn it on for new chats, show it on, and say so", async () => {
    await openedSettings();
    fireEvent.click(screen.getByTestId("documents-new-chats-switch"));
    expect(screen.getByTestId("documents-new-chats-switch")).toBeChecked();
    await wait(0);
    expect(vi.mocked(put)).toHaveBeenCalledExactlyOnceWith("/api/documents/prefs", {
      newChats: true,
    });
    expect(screen.getByTestId("documents-new-chats-switch")).toBeChecked();
    expect(screen.getByTestId("documents-new-chats-effective")).toHaveTextContent(
      "New chats start with Documents on.",
    );
  });

  it("put the old value back when the change was refused, and say why", async () => {
    await openedSettings();
    vi.mocked(put).mockRejectedValueOnce(refuse("Workbench could not keep that.", 500));
    fireEvent.click(screen.getByTestId("documents-new-chats-switch"));
    await wait(0);
    expect(screen.getByTestId("documents-new-chats-switch")).not.toBeChecked();
    expect(screen.getByTestId("documents-settings-problem")).toHaveTextContent(
      "Workbench could not keep that.",
    );
  });

  it("show the choice about models outside the person's machines, and whose word it is", async () => {
    await openedSettings();
    expect(screen.getByTestId("documents-external-ask")).toBeChecked();
    expect(screen.getByTestId("documents-external-never")).not.toBeChecked();
    expect(screen.getByTestId("documents-external-effective")).toHaveTextContent(
      "In effect: Ask me in each chat. Nobody has chosen, so the default applies.",
    );
    expect(screen.queryByTestId("documents-external-default")).toBeNull();
  });

  it("let the person choose, show the choice in effect with their own name on it, and take it back", async () => {
    await openedSettings();
    fireEvent.click(screen.getByTestId("documents-external-never"));
    await wait(0);
    expect(vi.mocked(put)).toHaveBeenCalledWith("/api/documents/prefs", { external: "never" });
    expect(screen.getByTestId("documents-external-never")).toBeChecked();
    expect(screen.getByTestId("documents-external-effective")).toHaveTextContent(
      "In effect: Never send them. You chose this.",
    );
    fireEvent.click(screen.getByTestId("documents-external-default"));
    await wait(0);
    expect(vi.mocked(put)).toHaveBeenLastCalledWith("/api/documents/prefs", { external: null });
    expect(screen.getByTestId("documents-external-ask")).toBeChecked();
    expect(screen.queryByTestId("documents-external-default")).toBeNull();
  });

  it("are the owner's where the owner set them: shown, said, and not for the person to change", async () => {
    await openedSettings({
      prefs: prefs({ external: "ask", effective: { external: "never", externalSetBy: "owner" } }),
    });
    expect(screen.getByTestId("documents-external-never")).toBeChecked();
    for (const choice of ["ask", "never", "always"]) {
      expect(screen.getByTestId(`documents-external-${choice}`)).toBeDisabled();
    }
    expect(screen.getByTestId("documents-external-effective")).toHaveTextContent(
      "In effect: Never send them. The owner of this Workbench set this, and it cannot be changed here.",
    );
    // Not even to use the default: the owner's word stands.
    expect(screen.queryByTestId("documents-external-default")).toBeNull();
  });

  it("are held while a change is made", async () => {
    await openedSettings();
    let finish: () => void = () => undefined;
    vi.mocked(put).mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)) as never);
    fireEvent.click(screen.getByTestId("documents-external-always"));
    expect(screen.getByTestId("documents-external-always")).toBeChecked();
    expect(screen.getByTestId("documents-external-ask")).toBeDisabled();
    expect(screen.getByTestId("documents-new-chats-switch")).toBeDisabled();
    await act(async () => finish());
    await wait(0);
    expect(screen.getByTestId("documents-external-ask")).toBeEnabled();
  });
});

describe("asking about the documents", () => {
  it("is offered once something can be searched, and starts a chat with Documents on", async () => {
    const { onAsk } = await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("documents-ask"));
    expect(onAsk).toHaveBeenCalledOnce();
  });

  it("is not offered while nothing can be searched yet", async () => {
    await opened({ docs: [queued(), scanned()] });
    expect(screen.queryByTestId("documents-ask")).toBeNull();
  });
});

describe("removing a document", () => {
  it("asks first, says it is for real, and keeps the document if the person keeps it", async () => {
    await opened({ docs: [saved(), markdown()] });
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove"));
    const ask = within(row("harbour-survey.pdf")).getByTestId("saved-document-remove-confirm");
    expect(ask).toHaveAttribute("role", "group");
    expect(ask).toHaveAccessibleName("Remove harbour-survey.pdf?");
    expect(ask).toHaveTextContent(
      "Remove harbour-survey.pdf? Workbench deletes its copy, so no chat can find it again. This cannot be undone.",
    );
    // The safe answer has the keyboard.
    expect(within(ask).getByTestId("saved-document-remove-keep")).toHaveFocus();
    fireEvent.click(within(ask).getByTestId("saved-document-remove-keep"));
    expect(
      within(row("harbour-survey.pdf")).queryByTestId("saved-document-remove-confirm"),
    ).toBeNull();
    expect(vi.mocked(del)).not.toHaveBeenCalled();
    expect(rows()).toHaveLength(2);
    // And the keyboard goes back to the button that asked.
    expect(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove")).toHaveFocus();
  });

  it("is backed out of with Escape", async () => {
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-remove"));
    fireEvent.keyDown(screen.getByTestId("saved-document-remove-confirm"), { key: "Escape" });
    expect(screen.queryByTestId("saved-document-remove-confirm")).toBeNull();
    expect(vi.mocked(del)).not.toHaveBeenCalled();
    // The keyboard goes back to the button that asked.
    expect(screen.getByTestId("saved-document-remove")).toHaveFocus();
  });

  it("removes the document at its own address, and it is gone from the list and from the totals", async () => {
    await opened({ docs: [saved(), markdown()] });
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove"));
    fireEvent.click(screen.getByTestId("saved-document-remove-go"));
    await wait(0);
    expect(vi.mocked(del)).toHaveBeenCalledExactlyOnceWith("/api/documents/d1");
    expect(rows().map((r) => within(r).getByTestId("saved-document-name").textContent)).toEqual([
      "field-notes.md",
    ]);
    expect(screen.getByTestId("documents-totals")).toHaveTextContent("1 document · 1 passage");
  });

  it("gives the keyboard to the list's heading once the document it was on is gone", async () => {
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-remove"));
    fireEvent.click(screen.getByTestId("saved-document-remove-go"));
    await wait(0);
    expect(screen.getByRole("heading", { name: "Your documents" })).toHaveFocus();
  });

  it("says why on the document's own row when it could not be removed, and keeps it", async () => {
    await opened({
      docs: [saved({ id: "stuck", name: "report.pdf" }), markdown()],
      stuck: new Set(["stuck"]),
    });
    fireEvent.click(within(row("report.pdf")).getByTestId("saved-document-remove"));
    fireEvent.click(screen.getByTestId("saved-document-remove-go"));
    await wait(0);
    expect(within(row("report.pdf")).getByTestId("saved-document-problem")).toHaveTextContent(
      "Workbench could not remove report.pdf (disk error).",
    );
    expect(within(row("field-notes.md")).queryByTestId("saved-document-problem")).toBeNull();
    expect(rows()).toHaveLength(2);
  });

  it("keeps another question that is open held while a removal is done, and lets it go afterwards", async () => {
    await opened({ docs: [saved(), markdown()] });
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove"));
    fireEvent.click(within(row("field-notes.md")).getByTestId("saved-document-remove"));
    let finish: () => void = () => undefined;
    vi.mocked(del).mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove-go"));
    expect(within(row("field-notes.md")).getByTestId("saved-document-remove-go")).toBeDisabled();
    await act(async () => finish());
    await wait(0);
    expect(within(row("field-notes.md")).getByTestId("saved-document-remove-go")).toBeEnabled();
  });

  it("is held while it is done, so it is not asked for twice", async () => {
    await opened({ docs: [saved(), markdown()] });
    let finish: () => void = () => undefined;
    vi.mocked(del).mockReturnValueOnce(new Promise<void>((resolve) => (finish = resolve)));
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-remove"));
    fireEvent.click(screen.getByTestId("saved-document-remove-go"));
    // What changes a document is held, for every document; reading its text changes nothing.
    for (const id of ["saved-document-open", "saved-document-reread", "saved-document-remove"]) {
      expect(within(row("field-notes.md")).getByTestId(id), id).toBeDisabled();
    }
    await act(async () => finish());
    await wait(0);
  });
});

describe("reading a document again", () => {
  it("asks the server to, and the document waits to be read again", async () => {
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-reread"));
    await wait(0);
    expect(vi.mocked(post)).toHaveBeenCalledWith("/api/documents/d1/reread");
    expect(status("harbour-survey.pdf")).toHaveTextContent("Waiting to be read");
    expect(within(row("harbour-survey.pdf")).queryByTestId("saved-document-reread")).toBeNull();
  });

  it("says on the document's row that its file is gone, in the server's words", async () => {
    await opened({ docs: [saved(), markdown()], gone: new Set(["d1"]) });
    fireEvent.click(within(row("harbour-survey.pdf")).getByTestId("saved-document-reread"));
    await wait(0);
    expect(
      within(row("harbour-survey.pdf")).getByTestId("saved-document-problem"),
    ).toHaveTextContent(
      "Workbench's copy of this file is not on its disk any more. Add the file again.",
    );
    expect(status("harbour-survey.pdf")).toHaveTextContent("Ready");
  });

  it("clears the words about a document when something else is done to it", async () => {
    await opened({ docs: [saved()], gone: new Set(["d1"]) });
    fireEvent.click(screen.getByTestId("saved-document-reread"));
    await wait(0);
    expect(screen.getByTestId("saved-document-problem")).toBeInTheDocument();
    server.gone.clear();
    fireEvent.click(screen.getByTestId("saved-document-reread"));
    await wait(0);
    expect(screen.queryByTestId("saved-document-problem")).toBeNull();
  });
});

describe("opening the original", () => {
  it("fetches the file with the secret and saves it under the name it was added with", async () => {
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-open"));
    await wait(0);
    expect(vi.mocked(fileOf)).toHaveBeenCalledExactlyOnceWith("/api/documents/d1/original");
    expect(vi.mocked(saveFile)).toHaveBeenCalledOnce();
    expect(vi.mocked(saveFile).mock.calls[0]![1]).toBe("report.pdf");
  });

  it("falls back to the document's own name when the server gave none", async () => {
    vi.mocked(fileOf).mockResolvedValue({ blob: new Blob(["pdf"]), name: null });
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-open"));
    await wait(0);
    expect(vi.mocked(saveFile).mock.calls[0]![1]).toBe("harbour-survey.pdf");
  });

  it("says why on the row when the file is no longer there", async () => {
    vi.mocked(fileOf).mockRejectedValue(refuse("That file is no longer on disk.", 404));
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-open"));
    await wait(0);
    expect(screen.getByTestId("saved-document-problem")).toHaveTextContent(
      "That file is no longer on disk.",
    );
    expect(vi.mocked(saveFile)).not.toHaveBeenCalled();
  });
});

describe("the text Workbench indexed", () => {
  it("opens under the document, from its own address, and closes again", async () => {
    await opened({ docs: [saved()] });
    const toggle = screen.getByTestId("saved-document-text-toggle");
    expect(toggle).toHaveTextContent("Show indexed text");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    await wait(0);
    expect(vi.mocked(documentText)).toHaveBeenCalledExactlyOnceWith("d1");
    const text = screen.getByTestId("saved-document-text");
    expect(text).toHaveTextContent("[page 4] The code word is TAMARIND-6042.");
    expect(text).toHaveAccessibleName("Text Workbench indexed from harbour-survey.pdf");
    expect(toggle).toHaveTextContent("Hide indexed text");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("saved-document-text")).toBeNull();
  });

  it("says why it could not be opened, in the server's words", async () => {
    vi.mocked(documentText).mockRejectedValue(new Error("This document is still being read."));
    await opened({ docs: [saved()] });
    fireEvent.click(screen.getByTestId("saved-document-text-toggle"));
    await wait(0);
    expect(screen.getByTestId("saved-document-text-error")).toHaveTextContent(
      "This document is still being read.",
    );
  });
});

describe("Test a question, on the screen", () => {
  it("is there once there is something to test, and asks no model", async () => {
    await opened({ docs: [saved()] });
    expect(screen.getByRole("heading", { level: 2, name: "Test a question" })).toBeInTheDocument();
    fireEvent.change(screen.getByTestId("documents-test-question"), {
      target: { value: "  What is the code word?  " },
    });
    fireEvent.click(screen.getByTestId("documents-test-ask"));
    await wait(0);
    expect(asked).toEqual(["What is the code word?"]);
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute("data-status", "found");
  });
});

// --- search by meaning (MR5) ---

const OWNER = { owner: true, consoleUrl: "http://nas:8279/" };
const PERSON = { owner: false, consoleUrl: null };

describe("how the screen says it is searched", () => {
  it("says meaning and words, the search model in use and where it runs, and says what the files are searched for", async () => {
    await opened({
      docs: [withMeaning()],
      meaning: true,
      headline: "Searching by meaning and words.",
    });
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
    expect(screen.getByTestId("documents-search-model")).toHaveTextContent(
      "In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)",
    );
    expect(screen.getByTestId("documents-search-where")).toHaveTextContent(
      "runs on your own machines",
    );
    expect(screen.getByTestId("documents-intro")).toHaveTextContent(
      "searched for the words in your question and for what it means",
    );
    expect(screen.queryByTestId("documents-search-help")).toBeNull();
  });

  it("says the words-only help only while the mode is words, and not once meaning is in use", async () => {
    await opened({ docs: [saved()] });
    expect(screen.getByTestId("documents-search")).toHaveTextContent(
      "“costs” does not find “cost”",
    );
    cleanup();
    await opened({ docs: [withMeaning()], meaning: true });
    expect(screen.getByTestId("documents-search")).not.toHaveTextContent(
      "“costs” does not find “cost”",
    );
    cleanup();
    // A search that is paused is by words, and says so, whatever the model that is running.
    await opened({ docs: [withMeaning()], search: paused() });
    expect(screen.getByTestId("documents-search")).toHaveTextContent(
      "“costs” does not find “cost”",
    );
  });

  it("says what an empty list is for, as it is searched", async () => {
    await opened({ meaning: true });
    expect(screen.getByTestId("documents-empty")).toHaveTextContent(
      "the words or the meaning of your question",
    );
    cleanup();
    await opened({});
    expect(screen.getByTestId("documents-empty")).not.toHaveTextContent(
      "or the meaning of your question",
    );
  });

  it("says no search model is set up, and links the owner to the Library in Eugene's console", async () => {
    await opened({ docs: [wordsOnlyDoc()], search: noSearchModel() }, OWNER);
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by words only. No search model is running.",
    );
    const link = screen.getByTestId("documents-get-search-model");
    expect(link).toHaveAttribute("href", "http://nas:8279/library/");
    expect(link).toHaveAccessibleName("Get a search model");
  });

  it("tells a person who is not the owner to ask the owner, and gives them no link", async () => {
    await opened({ docs: [wordsOnlyDoc()], search: noSearchModel() }, PERSON);
    expect(screen.queryByTestId("documents-get-search-model")).toBeNull();
    expect(screen.getByTestId("documents-ask-owner")).toHaveTextContent(
      "Ask the owner of this Workbench to set one up in Eugene's console.",
    );
  });

  it("is a live look: it says a search model that stopped, at the next look, with nothing else changed", async () => {
    await opened({ docs: [withMeaning()], meaning: true });
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
    server.meaning = false;
    server.search = searchFor("not_running");
    await wait(IDLE_POLL_MS);
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by words only. No search model is running.",
    );
    expect(screen.getByTestId("documents-search-detail")).toHaveTextContent(
      "The search model Qwen3-Embedding-0.6B-Q8_0 is not running. Start it in Eugene's console under Backends.",
    );
    expect(screen.queryByTestId("documents-search-model")).toBeNull();
    // And back.
    server.search = {};
    server.meaning = true;
    await wait(IDLE_POLL_MS);
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
  });

  it("says nothing of a search at all where the owner has turned documents off", async () => {
    await opened({ installEnabled: false, docs: [saved()], search: noSearchModel() }, OWNER);
    expect(screen.queryByTestId("documents-search")).toBeNull();
  });
});

describe("a search that is paused", () => {
  it("offers Rebuild search with its passages and time, and adds meaning again when it is pressed", async () => {
    await opened({
      docs: [withMeaning(), withMeaning({ id: "d9", name: "b.pdf" })],
      search: paused(),
    });
    expect(screen.getByTestId("documents-meaning-rebuild-words")).toHaveTextContent(
      "Rebuild search adds meaning again to 3,000 passages with nomic-embed-text-v1.5, which takes about 12 minutes.",
    );
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    await wait(0);
    expect(meaningRebuilds).toBe(1);
    expect(vi.mocked(post)).toHaveBeenCalledWith("/api/documents/meaning/rebuild");
    // It says what it is doing, and the documents are found by words and say how it is going.
    expect(screen.getByTestId("documents-meaning-rebuilt")).toHaveTextContent(
      "Adding meaning again to 10 passages",
    );
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
    expect(status("harbour-survey.pdf")).toHaveTextContent(
      "Ready for words. Adding meaning: 0 of 5 passages.",
    );
    expect(screen.queryByTestId("documents-meaning-rebuild")).toBeNull();
  });

  it("is held while it is asked, so it is not asked for twice", async () => {
    await opened({ docs: [withMeaning()], search: paused() });
    let finish: () => void = () => undefined;
    vi.mocked(post).mockReturnValueOnce(
      new Promise(
        (resolve) => (finish = () => resolve({ passages: 5, search: byMeaning() })),
      ) as never,
    );
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    expect(screen.getByTestId("documents-meaning-rebuild")).toBeDisabled();
    await act(async () => finish());
    await wait(0);
  });

  it("says why when it was refused, in the server's words, and stays as it was", async () => {
    await opened({
      docs: [withMeaning()],
      search: paused(),
      meaningRefused: refuse("The owner of this Workbench has turned off documents.", 403),
    });
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    await wait(0);
    expect(screen.getByTestId("documents-problem")).toHaveTextContent(
      "The owner of this Workbench has turned off documents.",
    );
    expect(screen.queryByTestId("documents-meaning-rebuilt")).toBeNull();
    expect(screen.getByTestId("documents-meaning-rebuild")).toBeEnabled();
  });

  it("stops saying meaning is being added again once none is owed", async () => {
    // Each read adds meaning to two more passages, until they all have it.
    const step = (docs: SavedDocument[]): SavedDocument[] =>
      docs.map((d) => {
        if (d.meaning?.state !== "adding") return d;
        const embedded = Math.min(d.passages, d.embedded + 2);
        return embedded >= d.passages
          ? withMeaning({
              ...d,
              state: "ready",
              embedded,
              meaning: meaningOf({ embedded, total: d.passages }),
            })
          : {
              ...d,
              embedded,
              meaning: meaningOf({
                embedded,
                total: d.passages,
                state: "adding",
                message: `Ready for words. Adding meaning: ${embedded} of ${d.passages} passages.`,
              }),
            };
      });
    await opened({ docs: [withMeaning()], search: paused(), advance: step });
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    await wait(0);
    expect(screen.getByTestId("documents-meaning-rebuilt")).toBeInTheDocument();
    // One look at a time: the screen draws what it read before it asks again.
    await wait(FIRST_POLL_MS);
    await wait(POLL_MS);
    expect(screen.getByTestId("documents-meaning-rebuilt")).toBeInTheDocument();
    await wait(POLL_MS);
    expect(status("harbour-survey.pdf")).toHaveTextContent(/^Ready$/);
    expect(screen.queryByTestId("documents-meaning-rebuilt")).toBeNull();
    // It does not come back for a document that is added later and is given its meaning.
    server.docs = [...server.docs, adding({ id: "d9", name: "later.pdf", embedded: 0 })];
    await wait(IDLE_POLL_MS);
    expect(screen.queryByTestId("documents-meaning-rebuilt")).toBeNull();
  });
});

describe("each document's meaning", () => {
  it("is said on its row: being added, waiting for the search model, held back by the key's limit, found by words only", async () => {
    await opened({
      docs: [
        adding({ id: "a", name: "adding.pdf" }),
        waiting({ id: "w", name: "waiting.pdf" }),
        limited({ id: "l", name: "limited.pdf" }),
        wordsOnlyDoc({ id: "o", name: "words.pdf" }),
        withMeaning({ id: "r", name: "ready.pdf" }),
      ],
      meaning: true,
    });
    expect(status("adding.pdf")).toHaveTextContent(
      "Ready for words. Adding meaning: 2 of 5 passages, about 14 minutes left.",
    );
    expect(status("waiting.pdf")).toHaveTextContent(
      "Ready for words. Meaning is waiting: the search model qwen3-embedding is not running.",
    );
    expect(status("limited.pdf")).toHaveTextContent(
      "Adding meaning to your documents is slow right now",
    );
    expect(status("words.pdf")).toHaveTextContent(
      "Ready for words only: no search model was running when it was read.",
    );
    expect(status("ready.pdf")).toHaveTextContent(/^Ready$/);
    // Waiting has a clock, being added has the check that says words are ready; neither is a failure.
    expect(within(status("waiting.pdf")).queryByRole("alert")).toBeNull();
    expect(status("waiting.pdf")).not.toHaveClass("notice--error");
    expect(row("adding.pdf")).toHaveAttribute("data-state", "embedding");
  });

  it("notes each passage the search model refused, in the server's words, and counts what has its meaning", async () => {
    const note =
      "The search model refused one passage (1,631 characters; it said: input is too large): skipped harbour-survey.pdf, page 4.";
    await opened({
      docs: [
        saved({
          embedded: 5,
          meaning: meaningOf({ embedded: 5, total: 5, refused: 1, notes: [note] }),
          notes: ["Page 2 has no text (it may be a picture)."],
        }),
      ],
      meaning: true,
    });
    expect(status("harbour-survey.pdf")).toHaveTextContent(
      "Ready. Meaning was added to 4 of 5 passages; 1 passage is found by words only.",
    );
    const notes = within(row("harbour-survey.pdf")).getByTestId("saved-document-notes");
    expect(notes).toHaveTextContent("Page 2 has no text (it may be a picture).");
    expect(notes).toHaveTextContent(note);
  });

  it("is not said of a document that was skipped or could not be read", async () => {
    await opened({ docs: [scanned(), broken()], meaning: true });
    expect(status("scan.pdf")).toHaveTextContent(/^Skipped:/);
    expect(status("broken.pdf")).toHaveTextContent(/^Failed:/);
  });

  it("is totalled at the foot: how many passages have it, and how many are still to do", async () => {
    await opened({ docs: [adding(), withMeaning({ id: "d9", name: "b.pdf" })], meaning: true });
    expect(screen.getByTestId("documents-totals")).toHaveTextContent("2 documents · 10 passages");
    expect(screen.getByTestId("documents-meaning-totals")).toHaveTextContent(
      "Meaning is added to 7 of 10 passages; 3 are still to do.",
    );
  });

  it("is not totalled while no passage has it and none is owed it: the header says words only", async () => {
    await opened({ docs: [saved(), wordsOnlyDoc({ id: "d9", name: "b.pdf" })] });
    expect(screen.queryByTestId("documents-meaning-totals")).toBeNull();
  });
});

describe("watching meaning being added", () => {
  // Each read adds meaning to two more passages of every document that is having it added.
  const step = (docs: SavedDocument[]): SavedDocument[] =>
    docs.map((d) => {
      if (d.meaning?.state !== "adding") return d;
      const embedded = Math.min(d.passages, d.embedded + 2);
      return embedded >= d.passages
        ? withMeaning({
            ...d,
            state: "ready",
            embedded,
            meaning: meaningOf({ embedded, total: d.passages }),
          })
        : adding({
            ...d,
            embedded,
            meaning: meaningOf({
              embedded,
              total: d.passages,
              state: "adding",
              message: `Ready for words. Adding meaning: ${embedded} of ${d.passages} passages.`,
            }),
          });
    });

  it("asks again by itself and shows each count go up, then Ready, and then asks only each half minute", async () => {
    await opened({ docs: [adding({ embedded: 0 })], meaning: true, advance: step });
    expect(status("harbour-survey.pdf")).toHaveTextContent("Adding meaning: 2 of 5 passages");
    await wait(FIRST_POLL_MS);
    expect(status("harbour-survey.pdf")).toHaveTextContent("Adding meaning: 2 of 5 passages");
    await wait(POLL_MS);
    expect(status("harbour-survey.pdf")).toHaveTextContent("Adding meaning: 4 of 5 passages");
    await wait(POLL_MS);
    expect(status("harbour-survey.pdf")).toHaveTextContent(/^Ready$/);
    expect(screen.getByTestId("documents-meaning-totals")).toHaveTextContent(
      "Meaning is added to every passage.",
    );
    const done = reads;
    await wait(IDLE_POLL_MS - 1);
    expect(reads).toBe(done);
    await wait(1);
    expect(reads).toBe(done + 1);
  });

  it("goes on asking while meaning waits for the search model, every few seconds, and shows it going on when it is back", async () => {
    server = fresh({ docs: [waiting()], search: searchFor("not_running"), advance: step });
    render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await wait(0);
    expect(status("harbour-survey.pdf")).toHaveTextContent("Meaning is waiting");
    const before = reads;
    await wait(4999);
    expect(reads).toBe(before);
    // The search model is back: the next look says so and meaning is added.
    server.search = {};
    server.meaning = true;
    server.docs = [adding({ embedded: 0 })];
    await wait(1);
    expect(reads).toBe(before + 1);
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
    expect(status("harbour-survey.pdf")).toHaveTextContent("Adding meaning");
  });
});

describe("Test a question, on the screen, with a search model", () => {
  it("offers to try one way at a time, and asks the server for the way that was chosen", async () => {
    await opened({ docs: [withMeaning()], meaning: true });
    fireEvent.click(screen.getByTestId("documents-test-ways-toggle"));
    fireEvent.click(screen.getByTestId("documents-test-way-meaning"));
    fireEvent.change(screen.getByTestId("documents-test-question"), {
      target: { value: "boats" },
    });
    fireEvent.click(screen.getByTestId("documents-test-ask"));
    await wait(0);
    expect(tries).toEqual([{ question: "boats", ways: ["meaning"] }]);
  });

  it("asks both ways, as a chat does, without naming any, when none is chosen", async () => {
    await opened({ docs: [withMeaning()], meaning: true });
    fireEvent.change(screen.getByTestId("documents-test-question"), {
      target: { value: "boats" },
    });
    fireEvent.click(screen.getByTestId("documents-test-ask"));
    await wait(0);
    expect(tries).toEqual([{ question: "boats" }]);
  });

  it("does not offer one way at a time where no search model has been set up: there is no second way to try", async () => {
    await opened({ docs: [wordsOnlyDoc()], search: noSearchModel() });
    expect(screen.queryByTestId("documents-test-ways")).toBeNull();
    cleanup();
    await opened({ docs: [wordsOnlyDoc()] });
    expect(screen.queryByTestId("documents-test-ways")).toBeNull();
    cleanup();
    // But it is offered for a search model that is stopped, which is when a person wants to know.
    await opened({ docs: [withMeaning()], search: searchFor("not_running") });
    expect(screen.getByTestId("documents-test-ways")).toBeInTheDocument();
  });
});

describe("the Folders section, on the screen (MR7)", () => {
  const first = (id: string) => screen.getByTestId(id);
  /** Whether `a` comes before `b` on the page. */
  const before = (a: string, b: string) =>
    Boolean(first(a).compareDocumentPosition(first(b)) & Node.DOCUMENT_POSITION_FOLLOWING);
  const folderReads = () =>
    vi.mocked(api).mock.calls.filter(([path]) => path === "/api/documents/folders").length;

  it("is below the person's own files and above Test a question", async () => {
    await opened({ docs: [saved()], folders: foldersPage([folder()]) });
    expect(first("folders-section")).toBeInTheDocument();
    expect(before("documents-list", "folders-section")).toBe(true);
    expect(before("folders-section", "documents-test")).toBe(true);
  });

  it("is there for a person with no folder, to add one", async () => {
    await opened({ folders: foldersPage([], { grants: [choice()] }) });
    expect(first("folders-section")).toBeInTheDocument();
    expect(first("folders-add-go")).toBeInTheDocument();
    expect(screen.queryAllByTestId("folder")).toHaveLength(0);
  });

  it("counts a folder's files apart from the files the person added themselves", async () => {
    await opened({ docs: [saved(), markdown()], folders: foldersPage([readingFolder()]) });
    // The list and its totals are the person's own files, as before.
    expect(first("documents-totals")).toHaveTextContent(/^2 documents · /);
    expect(rows()).toHaveLength(2);
    // The folders are counted in their own place.
    expect(first("folders-totals")).toHaveTextContent(
      "1 folder · 404 files · 1,904 passages · 4.2 MiB",
    );
    expect(within(first("folders-section")).getAllByTestId("folder")).toHaveLength(1);
    expect(within(first("documents-list")).queryByTestId("folder")).toBeNull();
  });

  it("reads folders on their own schedule, apart from the list", async () => {
    await opened({ docs: [saved()], folders: foldersPage([folder()]) });
    const listReads = reads;
    expect(folderReads()).toBe(1);
    await wait(30_000);
    expect(reads).toBeGreaterThan(listReads);
    expect(folderReads()).toBeGreaterThan(1);
  });

  it("offers Test a question to a person who has only folder files, and not to one who has nothing", async () => {
    await opened({ folders: foldersPage([folder()]) });
    expect(first("documents-test")).toBeInTheDocument();
    cleanup();
    await opened({ folders: foldersPage([]) });
    expect(screen.queryByTestId("documents-test")).toBeNull();
    cleanup();
    // A folder that has read no file yet has nothing to try a question on.
    await opened({ folders: foldersPage([waitingFolder()]) });
    expect(screen.queryByTestId("documents-test")).toBeNull();
  });

  it("has Ask about your documents once: above, with the person's own files, when they can be asked about", async () => {
    await opened({ docs: [saved()], folders: foldersPage([folder()]) });
    expect(screen.getAllByTestId("documents-ask")).toHaveLength(1);
    expect(within(first("folders-section")).queryByTestId("documents-ask")).toBeNull();
    expect(before("documents-ask", "documents-list")).toBe(true);
  });

  it("has Ask about your documents in Folders for a person who has files only in folders, and it asks", async () => {
    const { onAsk } = await opened({ folders: foldersPage([folder()]) });
    expect(screen.getAllByTestId("documents-ask")).toHaveLength(1);
    expect(within(first("folders-section")).getByTestId("documents-ask")).toBeInTheDocument();
    fireEvent.click(first("documents-ask"));
    expect(onAsk).toHaveBeenCalledTimes(1);
  });

  it("has Ask about your documents in Folders when the person's own files cannot be asked about yet", async () => {
    await opened({ docs: [queued()], folders: foldersPage([folder()]) });
    expect(screen.getAllByTestId("documents-ask")).toHaveLength(1);
    expect(within(first("folders-section")).getByTestId("documents-ask")).toBeInTheDocument();
  });

  it("has no Ask about your documents when nothing can be searched yet", async () => {
    await opened({
      folders: foldersPage([
        folder({
          counts: folderCounts({
            files: 3,
            byState: { queued: 2, reading: 1, ready: 0, skipped: 0 },
            byCode: {},
          }),
        }),
      ]),
    });
    expect(screen.queryByTestId("documents-ask")).toBeNull();
  });

  it("keeps the person's own files, and says in the section that the folders could not be read", async () => {
    server = fresh({ docs: [saved()] });
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/documents/folders") throw new Error("Workbench answered 502.");
      return seen();
    }) as never);
    render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await wait(0);
    expect(rows()).toHaveLength(1);
    expect(first("folders-problem")).toHaveTextContent(
      "Could not read your folders" + "Workbench answered 502.",
    );
    // It is the section's own, not the screen's: the screen's problem is not said.
    expect(screen.queryByTestId("documents-retry")).toBeNull();
    expect(screen.queryByTestId("documents-problem")).toBeNull();
  });

  it("says nothing of folders where the search is damaged, as of everything else", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => {
      if (path === "/api/documents/folders") return foldersPage([folder()]);
      throw refuse("The search index for your documents is damaged.", 503, {
        code: "index_damaged",
        canRebuild: true,
      });
    }) as never);
    render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await wait(0);
    expect(first("documents-damaged")).toBeInTheDocument();
    expect(screen.queryByTestId("folders-section")).toBeNull();
  });

  it("where the owner has turned documents off: nothing of folders for a person who has none", async () => {
    await opened({ installEnabled: false, folders: foldersPage([]) });
    expect(first("documents-owner-off")).toBeInTheDocument();
    expect(screen.queryByTestId("folders-section")).toBeNull();
    expect(screen.queryByTestId("documents-test")).toBeNull();
  });

  it("where the owner has turned documents off: the person's folders are there to be looked at and removed", async () => {
    await opened({ installEnabled: false, folders: foldersPage([folder()]) });
    expect(first("folders-section")).toBeInTheDocument();
    expect(first("folder-remove")).toBeInTheDocument();
    expect(screen.queryByTestId("folder-sync")).toBeNull();
    expect(screen.queryByTestId("folders-add")).toBeNull();
    expect(screen.queryByTestId("documents-test")).toBeNull();
    expect(screen.queryByTestId("documents-ask")).toBeNull();
  });

  it("opens Toolbox · Tools for the owner who was given no folder, and gives anyone else the words to ask", async () => {
    server = fresh({ folders: foldersPage([], { grants: [] }) });
    const opensTools = vi.fn();
    render(
      <Documents
        onClose={vi.fn()}
        onAsk={vi.fn()}
        onTools={opensTools}
        me={{ owner: true, consoleUrl: "http://nas:8279/" }}
      />,
    );
    await wait(0);
    fireEvent.click(first("folders-open-tools"));
    expect(opensTools).toHaveBeenCalledTimes(1);
    cleanup();
    server = fresh({ folders: foldersPage([], { grants: [] }) });
    render(
      <Documents
        onClose={vi.fn()}
        onAsk={vi.fn()}
        onTools={opensTools}
        me={{ owner: false, consoleUrl: null }}
      />,
    );
    await wait(0);
    expect(screen.queryByTestId("folders-open-tools")).toBeNull();
    expect(first("folders-add-none")).toHaveTextContent(
      "Ask the owner of this Workbench to give you one.",
    );
  });

  it("says a folder with a problem in its section, and leaves the screen's own problems alone", async () => {
    await opened({ docs: [saved()], folders: foldersPage([problemFolder()]) });
    expect(first("folder-status")).toHaveClass("notice--error");
    expect(screen.queryByTestId("documents-problem")).toBeNull();
  });

  it("says nothing of how it works underneath, in any state a folder is in", async () => {
    await opened({
      docs: [saved()],
      folders: foldersPage([folder(), readingFolder({ id: "g2" }), problemFolder({ id: "g3" })]),
    });
    expect(first("documents-page").textContent).not.toMatch(
      /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*)\b/i,
    );
  });
});

// --- the result sorter (MR6b) ---

describe("how the screen says the best passages are sorted", () => {
  const sortedMode = `meaning and words, sorted by ${SORTER}`;
  /** A search model in use and a sorter that is sorting, as the server's live look says it. */
  const sorting = (over: Partial<SearchFacts> = {}): Partial<SearchFacts> => ({
    mode: sortedMode as never,
    sort: sortFacts(),
    ...over,
  });
  const sortBox = () => screen.getByTestId("documents-sort");

  it("says the sorter in the headline, and under it which sorter is in use, where it runs and how fast it reads", async () => {
    await opened({ docs: [withMeaning()], meaning: true, search: sorting() });
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      `Searching by meaning and words, sorted by ${SORTER}.`,
    );
    expect(screen.getByTestId("documents-sort-model")).toHaveTextContent(
      `In use: ${SORTER} (automatic)`,
    );
    expect(screen.getByTestId("documents-sort-where")).toHaveTextContent(
      "It runs on your own machines",
    );
    expect(screen.getByTestId("documents-sort-speed")).toHaveTextContent(
      "sorts about 4,100 tokens a second here",
    );
    // Beside how the documents are searched, and after it.
    const after = screen
      .getByTestId("documents-search")
      .compareDocumentPosition(screen.getByTestId("documents-sort"));
    expect(after & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says why passages are not sorted, with the numbers, and the headline does not say they are", async () => {
    await opened({
      docs: [withMeaning()],
      meaning: true,
      search: { sort: sortFor("over_budget") },
    });
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      /^Searching by meaning and words\.$/,
    );
    expect(screen.getByTestId("documents-sort-reason")).toHaveTextContent(
      `Not sorted: ${SORTER} sorts about 820 tokens a second here, so sorting the 18 passages a question usually finds (about 300 tokens each) would add about 7 s to each answer, and Sort time is 3 s.`,
    );
    expect(sortBox()).not.toHaveTextContent("In use");
  });

  it("says nothing of a sorter that was never set up, and the screen is as it was", async () => {
    await opened({ docs: [withMeaning()], meaning: true });
    expect(screen.queryByTestId("documents-sort")).toBeNull();
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      /^Searching by meaning and words\.$/,
    );
  });

  it("says nothing of one where it was not told of one: no gateway, or a server before sorting", async () => {
    await opened({ docs: [saved()], search: { sort: null } });
    expect(screen.queryByTestId("documents-sort")).toBeNull();
    cleanup();
    await opened({ docs: [saved()], meaning: true, search: { sort: undefined } });
    expect(screen.queryByTestId("documents-sort")).toBeNull();
  });

  it("is a live look: a sorter that stopped is named at the next look, and sorting is said again when it is back", async () => {
    await opened({ docs: [withMeaning()], meaning: true, search: sorting() });
    expect(screen.getByTestId("documents-sort-model")).toBeInTheDocument();
    server.search = { sort: sortFor("not_running") };
    await wait(IDLE_POLL_MS);
    expect(screen.getByTestId("documents-sort-reason")).toHaveTextContent(
      `Not sorted: the sorter ${SORTER} is not running. Start it in Eugene's console under Backends.`,
    );
    expect(screen.queryByTestId("documents-sort-model")).toBeNull();
    expect(sortBox()).toHaveClass("notice--warn");
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      /^Searching by meaning and words\.$/,
    );
    server.search = sorting();
    await wait(IDLE_POLL_MS);
    expect(screen.getByTestId("documents-sort-model")).toBeInTheDocument();
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      `sorted by ${SORTER}`,
    );
  });

  it("looks again in seconds while the sorter is measured, not in half a minute, and shows what was measured", async () => {
    await opened({ docs: [withMeaning()], meaning: true, search: { sort: sortFor("measuring") } });
    expect(screen.getByTestId("documents-sort-reason")).toHaveTextContent(
      `Measuring how fast ${SORTER} sorts, to tell whether it is quick enough.`,
    );
    const before = reads;
    server.search = { sort: sortFor("over_budget") };
    await wait(WAITING_POLL_MS);
    expect(reads).toBe(before + 1);
    expect(screen.getByTestId("documents-sort-reason")).toHaveTextContent(
      "sorts about 820 tokens a second here",
    );
    // Measured: it goes back to looking each half minute, and not each few seconds.
    await wait(WAITING_POLL_MS * 4);
    expect(reads).toBe(before + 1);
    await wait(IDLE_POLL_MS);
    expect(reads).toBe(before + 2);
  });

  it("tells the owner where a sorter that stopped is started, and a person who is not the owner who to ask", async () => {
    await opened(
      { docs: [withMeaning()], meaning: true, search: { sort: sortFor("not_running") } },
      OWNER,
    );
    expect(screen.getByTestId("documents-sort-open-backends")).toHaveAttribute(
      "href",
      "http://nas:8279/inference/",
    );
    cleanup();
    await opened(
      { docs: [withMeaning()], meaning: true, search: { sort: sortFor("not_running") } },
      PERSON,
    );
    expect(screen.queryByTestId("documents-sort-open-backends")).toBeNull();
    expect(screen.getByTestId("documents-sort-ask-owner")).toHaveTextContent(
      "Ask the owner of this Workbench to fix this in Eugene's console.",
    );
  });

  it("says nothing of a sorter where the owner has turned documents off", async () => {
    await opened({
      installEnabled: false,
      docs: [saved()],
      meaning: true,
      search: sorting(),
    });
    expect(screen.queryByTestId("documents-sort")).toBeNull();
  });

  it("says nothing of how it works underneath, with a sorter in each state", async () => {
    // A model's name is its own and may have such a word in it: this holds the words the page says.
    const names = /Qwen3-Embedding-0\.6B-Q8_0|[\w.-]*rerank[\w.-]*/gi;
    for (const code of SORT_CODES) {
      await opened({ docs: [withMeaning()], meaning: true, search: { sort: sortFor(code) } });
      const said = (screen.getByTestId("documents-page").textContent ?? "").replace(
        names,
        "a-model",
      );
      expect(said, code).not.toMatch(
        /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*|rerank\w*)\b/i,
      );
      cleanup();
    }
  });
});
