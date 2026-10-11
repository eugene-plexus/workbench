/**
 * The Documents screen (MR4): what a person sees, from opening it to removing a
 * document, against a small fake Workbench that answers as the real one does
 * (src/eugene_plexus_workbench/saved_api.py): it lists documents, reads them in turn
 * (queued, then reading, then ready) as it is asked, keeps settings, and refuses what
 * the real one refuses.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, api, del, documentText, fileOf, post, put, upload } from "../lib/api";
import { saveFile } from "../lib/memory";
import { FIRST_POLL_MS, POLL_MS } from "../lib/savedDocuments";
import type { DocumentsPrefs, SavedDocument, TestedQuestion } from "../lib/types";
import {
  broken,
  markdown,
  page,
  prefs,
  queued,
  reading,
  saved,
  scanned,
  tested,
  word,
} from "../test-saved";
import { Documents } from "./Documents";

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
  headline: string;
  meaning: boolean;
  /** While set, every read of the documents is refused with it. */
  refusing: ApiError | null;
  tested: TestedQuestion;
  /** Ids the server will not read again: the original is gone. */
  gone: Set<string>;
  /** Ids it cannot remove. */
  stuck: Set<string>;
  rebuilt: { documents: number; left: number };
  /** How a document moves on each time the list is read. */
  advance: (docs: SavedDocument[]) => SavedDocument[];
}

let server: Server;
let reads: number;

const fresh = (over: Partial<Server> = {}): Server => ({
  docs: [],
  prefs: prefs(),
  installEnabled: true,
  ownerReadsChats: false,
  headline: "Searching by words only.",
  meaning: false,
  refusing: null,
  tested: tested(),
  gone: new Set(),
  stuck: new Set(),
  rebuilt: { documents: 2, left: 0 },
  advance: (docs) => docs,
  ...over,
});

const seen = () =>
  page(server.docs, {
    installEnabled: server.installEnabled,
    ownerReadsChats: server.ownerReadsChats,
    prefs: server.prefs,
    search: {
      mode: server.meaning ? "meaning and words" : "words",
      words: true,
      meaning: server.meaning,
      headline: server.headline,
    },
  });

const refuse = (message: string, status: number, extra: object = {}) =>
  new ApiError(message, status, { message, ...extra });

function listen() {
  vi.mocked(api).mockImplementation((async (path: string) => {
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
    if (path === "/api/documents/test") {
      asked.push(String(body?.question));
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
  listen();
});
afterEach(() => vi.useRealTimers());

const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** The screen, opened, and the first read let in. */
async function opened(over: Partial<Server> = {}) {
  server = fresh(over);
  const onClose = vi.fn();
  const onAsk = vi.fn();
  const view = render(<Documents onClose={onClose} onAsk={onAsk} />);
  await wait(0);
  return { ...view, onClose, onAsk };
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
    expect(headline).toHaveTextContent("it does not look for meaning");
    expect(headline.textContent).not.toMatch(/\b(soon|later|will|coming|meaning and words)\b/i);
  });

  it("says what the server says of the search, and no more, when it searches by meaning too", async () => {
    await opened({ headline: "Searching by meaning and words.", meaning: true });
    const headline = screen.getByTestId("documents-search");
    expect(headline).toHaveTextContent("Searching by meaning and words.");
    expect(headline).not.toHaveTextContent("it does not look for meaning");
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
    await wait(60_000);
    expect(reads).toBe(done);
  });

  it("does not ask again while every document has stopped", async () => {
    await opened({ docs: [saved(), scanned(), broken()] });
    await wait(60_000);
    expect(reads).toBe(1);
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
      "The owner of this Workbench can read your chats, and an answer in a chat can quote your documents. The owner cannot open this page or your files.",
    );
  });

  it("is not said where the owner cannot read chats", async () => {
    await opened({ ownerReadsChats: false });
    expect(screen.queryByTestId("documents-owner-reads")).toBeNull();
  });
});

describe("the settings", () => {
  it("show Documents in new chats as it is in effect", async () => {
    await opened();
    const box = screen.getByTestId("documents-new-chats-switch");
    expect(box).toHaveAttribute("role", "switch");
    expect(box).not.toBeChecked();
    expect(screen.getByTestId("documents-new-chats-effective")).toHaveTextContent(
      "New chats start with Documents off. You can turn it on in any chat, with its Documents button.",
    );
  });

  it("turn it on for new chats, show it on, and say so", async () => {
    await opened();
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
    await opened();
    vi.mocked(put).mockRejectedValueOnce(refuse("Workbench could not keep that.", 500));
    fireEvent.click(screen.getByTestId("documents-new-chats-switch"));
    await wait(0);
    expect(screen.getByTestId("documents-new-chats-switch")).not.toBeChecked();
    expect(screen.getByTestId("documents-problem")).toHaveTextContent(
      "Workbench could not keep that.",
    );
  });

  it("show the choice about models outside the person's machines, and whose word it is", async () => {
    await opened();
    expect(screen.getByTestId("documents-external-ask")).toBeChecked();
    expect(screen.getByTestId("documents-external-never")).not.toBeChecked();
    expect(screen.getByTestId("documents-external-effective")).toHaveTextContent(
      "In effect: Ask me in each chat. Nobody has chosen, so the default applies.",
    );
    expect(screen.queryByTestId("documents-external-default")).toBeNull();
  });

  it("let the person choose, show the choice in effect with their own name on it, and take it back", async () => {
    await opened();
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
    await opened({
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
    await opened();
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
      "Remove harbour-survey.pdf? Workbench deletes its copy of the file and the text it read from it, and no chat can find it again. This cannot be undone.",
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
