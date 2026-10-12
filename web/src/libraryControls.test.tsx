/**
 * Every control on the saved-documents screens has a name a screen reader says and a
 * `data-testid` a check can find it by (MR4 asks for both, as Memory's and the files'
 * do). Each screen is drawn in each state it has, and every control on it is held to
 * both: a control added later with neither fails here, by name. The check is the one the
 * other gates use (test-controls.ts); the checks of the gate itself are in
 * memoryControls.test.tsx.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";

import { Attachments } from "./components/Attachments";
import { AddDocuments } from "./components/AddDocuments";
import { Documents } from "./components/Documents";
import { DocumentsChip, DocumentsDrawer, DocumentsHint } from "./components/DocumentsChip";
import { MessageView } from "./components/MessageView";
import { TestQuestion } from "./components/TestQuestion";
import { ApiError, api, documentText, post, upload } from "./lib/api";
import type { ChatDocumentsView, ChatFile, Me, Message, SavedDocument } from "./lib/types";
import { choose, file, fresh, MODEL, preparing, searched, tooLong } from "./test-documents";
import {
  brokenFile,
  choice,
  counts as folderCounts,
  detail,
  FakeFolders,
  folder,
  folderFile,
  foldersPage,
  goneFolder,
  pictureFile,
  problemFolder,
  readingFolder,
  syncingFolder,
  waitingFile,
  waitingFolder,
  withdrawnFolder,
} from "./test-folders";
import { controlsIn, everyControlIsNamed, lacking, wrong } from "./test-controls";
import {
  adding,
  addingView,
  broken,
  byMeaning,
  chatDocuments,
  CODES,
  held,
  hidden,
  limited,
  markdown,
  meaningOf,
  meaningRun,
  noSearchModel,
  nothing,
  off,
  owners,
  page,
  passage,
  paused,
  prefs,
  queued,
  ranByMeaning,
  reading,
  record,
  saved,
  scanned,
  searchFor,
  second,
  tested,
  waiting,
  why,
  withMeaning,
  word,
  wordsOnlyDoc,
  hit,
} from "./test-saved";

vi.mock("./lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  documentText: vi.fn(),
  fileText: vi.fn(),
  fileOf: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  upload: vi.fn(),
}));
vi.mock("./components/Tools", () => ({ ToolSelection: () => null }));
vi.mock("./components/Folders", () => ({ FolderSelection: () => null }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset().mockResolvedValue(tested());
  vi.mocked(upload)
    .mockReset()
    .mockReturnValue(new Promise(() => undefined) as never);
  vi.mocked(documentText)
    .mockReset()
    .mockResolvedValue("[page 4]\nThe code word is TAMARIND-6042.");
});
afterEach(() => vi.useRealTimers());

const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

// --- the Documents screen ---

async function screenOf(
  docs: SavedDocument[],
  over: Parameters<typeof page>[1] = {},
  me?: Pick<Me, "owner" | "consoleUrl">,
) {
  // The folders (MR7) are asked for apart from the documents: none, here.
  vi.mocked(api).mockImplementation((async (path: string) =>
    path === "/api/documents/folders" ? foldersPage() : page(docs, over)) as never);
  const view = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} me={me} />);
  await settle();
  return view;
}

const OWNER = { owner: true, consoleUrl: "http://nas:8279/" };
const PERSON = { owner: false, consoleUrl: null };

describe("the Documents screen", () => {
  it("empty: the way back, the settings, and Add files", async () => {
    const view = await screenOf([]);
    everyControlIsNamed(view, 7);
  });

  it("with a document in every state, each with its own buttons", async () => {
    const view = await screenOf([
      queued({ id: "q", name: "waiting.md" }),
      reading({ id: "r", name: "reading.md" }),
      saved({ id: "s", name: "searchable.pdf", state: "searchable" }),
      saved(),
      markdown(),
      word(),
      scanned(),
      broken(),
    ]);
    // Open original and Remove on each of eight, and more.
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(30);
    everyControlIsNamed(view, 30);
  });

  it("with a document's text open, and when it could not be opened", async () => {
    const view = await screenOf([saved()]);
    fireEvent.click(screen.getByTestId("saved-document-text-toggle"));
    await settle();
    expect(screen.getByTestId("saved-document-text")).toBeInTheDocument();
    everyControlIsNamed(view);
    fireEvent.click(screen.getByTestId("saved-document-text-toggle"));
    vi.mocked(documentText).mockRejectedValue(new Error("This document is still being read."));
    fireEvent.click(screen.getByTestId("saved-document-text-toggle"));
    await settle();
    expect(screen.getByTestId("saved-document-text-error")).toBeInTheDocument();
    everyControlIsNamed(view);
  });

  it("with a document being removed, asked about first", async () => {
    const view = await screenOf([saved(), markdown()]);
    fireEvent.click(screen.getAllByTestId("saved-document-remove")[0]!);
    expect(screen.getByTestId("saved-document-remove-confirm")).toBeInTheDocument();
    everyControlIsNamed(view);
  });

  it("where the owner has turned documents off, with documents and without", async () => {
    const withDocs = await screenOf([saved()], { installEnabled: false });
    everyControlIsNamed(withDocs);
    withDocs.unmount();
    const bare = await screenOf([], { installEnabled: false });
    everyControlIsNamed(bare);
  });

  it("with the owner's word on models outside the machines, and the owner able to read chats", async () => {
    const view = await screenOf([saved()], {
      ownerReadsChats: true,
      prefs: prefs({ external: "ask", effective: { external: "never", externalSetBy: "owner" } }),
    });
    expect(screen.getByTestId("documents-owner-reads")).toBeInTheDocument();
    everyControlIsNamed(view);
  });

  it("with the person's own choices, and Use the default to take one back", async () => {
    const view = await screenOf([saved()], {
      prefs: prefs({
        newChats: true,
        external: "never",
        effective: { newChats: true, external: "never", externalSetBy: "you" },
      }),
    });
    expect(screen.getByTestId("documents-external-default")).toBeInTheDocument();
    everyControlIsNamed(view);
  });

  it("with a search that is damaged, and with one Rebuild cannot mend", async () => {
    vi.mocked(api).mockRejectedValue(
      new ApiError("The search index for your documents is damaged.", 503, {
        message: "The search index for your documents is damaged.",
        code: "index_damaged",
        canRebuild: true,
      }),
    );
    const damaged = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await settle();
    expect(screen.getByTestId("documents-rebuild")).toBeInTheDocument();
    everyControlIsNamed(damaged, 3);
    damaged.unmount();
    vi.mocked(api).mockRejectedValue(
      new ApiError("Workbench cannot search documents.", 503, {
        message: "Workbench cannot search documents.",
        code: "fts_missing",
        canRebuild: false,
      }),
    );
    const other = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await settle();
    expect(screen.queryByTestId("documents-rebuild")).toBeNull();
    everyControlIsNamed(other, 2);
  });

  it("when the documents could not be read, before and after it had read them", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502."));
    const first = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await settle();
    expect(screen.getByTestId("documents-retry")).toBeInTheDocument();
    everyControlIsNamed(first, 2);
    first.unmount();
    vi.mocked(api)
      .mockResolvedValueOnce(page([queued()]) as never)
      .mockRejectedValue(new Error("Down."));
    const later = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
    await settle();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    expect(screen.getByTestId("documents-retry")).toBeInTheDocument();
    everyControlIsNamed(later, 10);
  });
});

describe("adding files", () => {
  const file1 = (name: string) => new File(["x"], name);

  it("before any, with its file input", () => {
    const view = render(<AddDocuments onChanged={vi.fn()} />);
    everyControlIsNamed(view, 2);
    expect(screen.getByTestId("documents-input")).toHaveAttribute("aria-label");
  });

  it("with a file going, one waiting, and the bar that says how far", async () => {
    const view = render(<AddDocuments onChanged={vi.fn()} />);
    fireEvent.change(screen.getByTestId("documents-input"), {
      target: { files: [file1("a.pdf"), file1("b.md")] },
    });
    await settle();
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    everyControlIsNamed(view);
  });

  it("with a file added, one refused, one lost and a folder, and Clear this list", async () => {
    vi.mocked(upload).mockImplementation(((_path: string, form: FormData) => {
      const name = (form.get("files") as File).name;
      if (name === "ok.pdf") {
        return Promise.resolve({
          documents: [{ ...saved(), unchanged: false, replaced: [] }],
          refused: [],
        });
      }
      if (name === "lost.md")
        return Promise.reject(new Error("The connection to Workbench was lost."));
      return Promise.reject(
        new ApiError("a.png is an image.", 415, {
          message: "a.png is an image.",
          refused: [{ name, status: 415, message: "a.png is an image." }],
        }),
      );
    }) as never);
    const view = render(<AddDocuments onChanged={vi.fn()} />);
    fireEvent.drop(screen.getByTestId("documents-drop"), {
      dataTransfer: {
        types: ["Files"],
        files: [file1("ok.pdf"), file1("a.png"), file1("lost.md"), new File([], "scans")],
        items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, name: "scans" }) }],
      },
    });
    await settle();
    expect(screen.getByTestId("documents-uploads-clear")).toBeInTheDocument();
    // The file that was added is in the list of documents, and is not said again here.
    expect(screen.getAllByTestId("documents-upload")).toHaveLength(3);
    everyControlIsNamed(view, 3);
  });
});

describe("Test a question", () => {
  const tries = async (result: ReturnType<typeof tested> | Error) => {
    vi.mocked(post).mockReset();
    if (result instanceof Error) vi.mocked(post).mockRejectedValue(result);
    else vi.mocked(post).mockResolvedValue(result);
    const view = render(<TestQuestion />);
    fireEvent.change(screen.getByTestId("documents-test-question"), {
      target: { value: "code word" },
    });
    fireEvent.click(screen.getByTestId("documents-test-ask"));
    await settle();
    return view;
  };

  it("before a question is asked", () => {
    everyControlIsNamed(render(<TestQuestion />), 2);
  });

  it("with passages found, each with its whole text behind a row that opens", async () => {
    const view = await tries(tested());
    expect(screen.getAllByTestId("documents-test-whole")).toHaveLength(2);
    everyControlIsNamed(view, 4);
  });

  it.each([
    ["nothing matched", { ...tested(), status: "nothing_matched" as const, passages: [] }],
    ["a question with no words", { ...tested(), status: "no_words" as const, passages: [] }],
    ["no documents to search", { ...tested(), status: "no_documents" as const, passages: [] }],
  ])("%s", async (_name, result) => {
    const view = await tries(result);
    everyControlIsNamed(view, 2);
  });

  it("when it could not be tried", async () => {
    const view = await tries(new Error("Workbench answered 502."));
    expect(screen.getByTestId("documents-test-problem")).toBeInTheDocument();
    everyControlIsNamed(view, 2);
  });
});

// --- a chat's Documents ---

describe("the Documents chip", () => {
  it.each([
    ["not read yet", null],
    ["searching", chatDocuments()],
    ["off", off()],
    ["held back from an outside model", held("outside_model", "Not sent.")],
    ["someone else's chat", owners()],
  ] as [string, ChatDocumentsView | null][])("%s", (_name, view) => {
    const shown = render(
      <DocumentsChip view={view} failed={false} open={false} onToggle={vi.fn()} />,
    );
    everyControlIsNamed(shown, 1);
  });

  it("when it could not read", () => {
    everyControlIsNamed(render(<DocumentsChip view={null} failed open onToggle={vi.fn()} />));
  });
});

describe("the Documents drawer", () => {
  const drawer = (view: ChatDocumentsView | null, error: string | null = null) =>
    render(
      <DocumentsDrawer
        chatId="c1"
        view={view}
        error={error}
        onChat={vi.fn()}
        onReload={vi.fn().mockResolvedValue(undefined)}
        onManage={vi.fn()}
        onClose={vi.fn()}
      />,
    );
  const outside = held("outside_model", "Your documents were not sent.", {
    model: { id: "gpt-5", window: 128000, locality: "external", runsOn: "OpenRouter" },
    outside: { choice: "ask", setBy: "default", canSendForThisChat: true, sendForThisChat: false },
  });

  it.each([
    ["reading", null, null],
    ["failing to read", null, "Workbench answered 502."],
    ["searching", chatDocuments(), null],
    ["Documents only", chatDocuments({ documentsOnly: true }), null],
    ["off", off(), null],
    [
      "with nothing to search",
      chatDocuments({
        counts: {
          library: { documents: 0, passages: 0, waiting: 0 },
          chat: { documents: 0, passages: 0, waiting: 0 },
        },
      }),
      null,
    ],
    [
      "with documents being read",
      chatDocuments({
        counts: {
          library: { documents: 0, passages: 0, waiting: 2 },
          chat: { documents: 0, passages: 0, waiting: 0 },
        },
      }),
      null,
    ],
    ["owner's off", held("install_off", "The owner turned them off."), null],
    ["no model", held("no_model", "Choose a model."), null],
    ["a model not known", held("model_unknown", "Could not ask."), null],
    ["a window too small", held("window_too_small", "Too small."), null],
    ["an outside model, to send for this chat", outside, null],
    [
      "an outside model, sending because the person chose to",
      chatDocuments({
        model: outside.model,
        outside: { ...outside.outside!, sendForThisChat: true },
      }),
      null,
    ],
    [
      "an outside model that always gets them",
      chatDocuments({ model: outside.model, outside: { ...outside.outside!, choice: "always" } }),
      null,
    ],
    [
      "this chat's own files, searched whatever the switch says",
      off({
        sending: true,
        searching: { library: false, chat: true },
        reason: null,
        reasonCode: null,
        chatDocuments: [saved({ scope: "chat" }), queued({ scope: "chat" })],
      }),
      null,
    ],
    ["someone else's chat", owners(), null],
  ] as [string, ChatDocumentsView | null, string | null][])("%s", (_name, view, error) => {
    everyControlIsNamed(drawer(view, error), 1);
  });

  it("with the hint in the composer, and the problem it had", () => {
    const view = render(
      <DocumentsHint
        documents={3}
        busy={false}
        problem="This chat is gone."
        onTurnOn={vi.fn()}
        onHide={vi.fn()}
      />,
    );
    everyControlIsNamed(view, 2);
  });
});

// --- under an answer ---

describe("under an answer", () => {
  const message = (over: Partial<Message> = {}): Message => ({
    id: "a",
    seq: 2,
    role: "assistant",
    content: "The code word is TAMARIND-6042 [1].",
    reasoning: "",
    attachments: [],
    status: "done",
    error: null,
    sources: [],
    searches: 0,
    search: false,
    model: "m",
    finish: "stop",
    createdAt: 0,
    finishedAt: 1,
    ...over,
  });
  const shown = (m: Message, readOnly = false) =>
    render(
      <MessageView
        chatId="c"
        message={m}
        progress={null}
        busy={false}
        readOnly={readOnly}
        onChanged={vi.fn()}
        documents={readOnly ? undefined : { onManage: vi.fn() }}
      />,
    );

  /** What this slice adds under an answer: the passages, the notices and the reply. */
  const mine = (view: { container: HTMLElement }) =>
    [
      ...view.container.querySelectorAll(
        '[data-testid="passage-sources"], [data-testid="search-nothing"], [data-testid="search-skipped"], [data-testid="search-failed"], [data-testid="workbench-reply"]',
      ),
    ].flatMap(controlsIn);

  it("the passages that were sent, each a row that opens, one cited", () => {
    const view = shown(message({ sources: [passage(), second()], retrieval: record() }));
    expect(mine(view)).toHaveLength(2);
    expect(lacking(mine(view), view.container)).toEqual([]);
    fireEvent.click(screen.getAllByTestId("passage-toggle")[0]!);
    expect(lacking(mine(view), view.container)).toEqual([]);
  });

  it("the passages the owner sees: no control at all", () => {
    const view = shown(
      message({
        sources: [hidden(), hidden({ rank: 2 })],
        retrieval: record({ cited: [], searched: null, budget: null }),
      }),
      true,
    );
    expect(mine(view)).toHaveLength(0);
  });

  it("nothing matched: no control", () => {
    expect(mine(shown(message({ retrieval: nothing() })))).toHaveLength(0);
  });

  it("a search not run, and one that failed, with and without a way to mend it", () => {
    const skipped = shown(
      message({
        retrieval: record({
          status: "no_documents",
          code: "no_documents",
          message: null,
          passages: 0,
          cited: [],
        }),
      }),
    );
    expect(mine(skipped)).toHaveLength(1);
    expect(lacking(mine(skipped), skipped.container)).toEqual([]);
    skipped.unmount();
    const damaged = shown(
      message({
        retrieval: record({
          status: "failed",
          code: "index_damaged",
          message: "Damaged.",
          passages: 0,
          cited: [],
        }),
      }),
    );
    expect(mine(damaged)).toHaveLength(1);
    expect(lacking(mine(damaged), damaged.container)).toEqual([]);
    damaged.unmount();
    const other = shown(
      message({
        retrieval: record({
          status: "failed",
          code: "busy",
          message: "Busy.",
          passages: 0,
          cited: [],
        }),
      }),
    );
    expect(mine(other)).toHaveLength(0);
  });

  it("Workbench's own reply, with Ask the model anyway, and without it for the owner", () => {
    const reply = message({
      content: "I could not find that in your documents.",
      model: null,
      finish: "documents_only",
      retrieval: nothing({ documentsOnly: true, answeredBy: "workbench" }),
    });
    const own = shown(reply);
    expect(mine(own)).toHaveLength(1);
    expect(lacking(mine(own), own.container)).toEqual([]);
    own.unmount();
    expect(mine(shown(reply, true))).toHaveLength(0);
  });
});

// --- Search it instead ---

describe("a file's card, with Search it instead", () => {
  const cards = (files: ChatFile[]) =>
    render(
      <Attachments
        files={files}
        facts={{ model: MODEL, request: { used: 4218, window: 32768, reserve: 1024, full: null } }}
        problems={{}}
        choosing={null}
        locked={false}
        removing={null}
        onRemove={vi.fn()}
        onChoose={vi.fn().mockResolvedValue(undefined)}
      />,
    );

  // Each card's controls at the least: Remove, what it offers or lets the person undo, and its text.
  it.each([
    ["offering it beside the others", choose(), 5],
    ["offering it beside the part that fits", tooLong(), 4],
    ["once it is chosen", searched(), 3],
    ["while its own document is made", preparing(), 2],
    ["an ordinary file, as before", file(), 2],
    ["a file just attached", fresh(), 1],
  ] as [string, ChatFile, number][])("%s", (_name, found, controls) => {
    const view = cards([found]);
    everyControlIsNamed(view, controls);
  });
});

// --- search by meaning (MR5) ---

describe("the Documents screen, with search by meaning", () => {
  const working = addingView({
    state: "working",
    message: "Adding meaning: 2 of 5 passages, about 14 minutes left.",
    embedded: 2,
    owed: 3,
  });

  it("with a search model in use, and a document in each state of its meaning", async () => {
    const view = await screenOf(
      [
        adding({ id: "a", name: "adding.pdf" }),
        waiting({ id: "w", name: "waiting.pdf" }),
        limited({ id: "l", name: "limited.pdf" }),
        wordsOnlyDoc({ id: "o", name: "words.pdf" }),
        withMeaning({ id: "r", name: "ready.pdf" }),
        saved({
          id: "f",
          name: "refused.pdf",
          meaning: meaningOf({ embedded: 5, total: 5, refused: 1, notes: ["It refused one."] }),
        }),
      ],
      { search: byMeaning({ adding: working }) },
      OWNER,
    );
    expect(screen.getByTestId("documents-meaning-totals")).toBeInTheDocument();
    everyControlIsNamed(view, 30);
  });

  it.each(CODES)("for the code %s, to the owner and to a person who is not", async (code) => {
    const owner = await screenOf([withMeaning()], { search: searchFor(code) }, OWNER);
    everyControlIsNamed(owner, 5);
    owner.unmount();
    const person = await screenOf([withMeaning()], { search: searchFor(code) }, PERSON);
    everyControlIsNamed(person, 5);
  });

  it("with no search model set up, the link to Eugene's console is named and has an id", async () => {
    const view = await screenOf([wordsOnlyDoc()], { search: noSearchModel() }, OWNER);
    expect(screen.getByTestId("documents-get-search-model")).toHaveAccessibleName(
      "Get a search model",
    );
    everyControlIsNamed(view, 5);
  });

  it("with a search model that is not running, the link to Backends is named and has an id", async () => {
    const view = await screenOf([withMeaning()], { search: searchFor("not_running") }, OWNER);
    expect(screen.getByTestId("documents-open-backends")).toHaveAccessibleName("Open Backends");
    everyControlIsNamed(view, 5);
  });

  it("with a search that is paused, Rebuild search is named and described", async () => {
    const view = await screenOf([withMeaning()], { search: paused() }, OWNER);
    const button = screen.getByTestId("documents-meaning-rebuild");
    expect(button).toHaveAccessibleName("Rebuild search");
    expect(button).toHaveAccessibleDescription(/adds meaning again to 3,000 passages/);
    everyControlIsNamed(view, 5);
  });

  it("with Rebuild search just pressed, and its words", async () => {
    vi.mocked(post).mockResolvedValue({ passages: 5, search: byMeaning() });
    const view = await screenOf([withMeaning()], { search: paused() }, OWNER);
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    await settle();
    everyControlIsNamed(view, 5);
  });

  it("with a search model that runs outside the machines", async () => {
    const view = await screenOf(
      [withMeaning()],
      {
        search: byMeaning({
          model: {
            ...byMeaning().model!,
            locality: "external",
            runsOn: "OpenAI",
            allowExternal: true,
          },
        }),
      },
      OWNER,
    );
    everyControlIsNamed(view, 5);
  });
});

describe("Test a question, with search by meaning", () => {
  const triesWith = async (result: ReturnType<typeof tested>, open = false) => {
    vi.mocked(post).mockReset();
    vi.mocked(post).mockResolvedValue(result);
    const view = render(<TestQuestion search={byMeaning()} />);
    if (open) fireEvent.click(screen.getByTestId("documents-test-ways-toggle"));
    fireEvent.change(screen.getByTestId("documents-test-question"), {
      target: { value: "boats" },
    });
    fireEvent.click(screen.getByTestId("documents-test-ask"));
    await settle();
    return view;
  };

  it("with one way at a time offered, closed and open: a summary and three radios, each named", () => {
    const view = render(<TestQuestion search={byMeaning()} />);
    everyControlIsNamed(view, 3);
    fireEvent.click(screen.getByTestId("documents-test-ways-toggle"));
    expect(screen.getAllByRole("radio")).toHaveLength(3);
    everyControlIsNamed(view, 6);
  });

  it("with passages found by words, by meaning and by both", async () => {
    const view = await triesWith(
      tested({
        mode: "meaning and words",
        meaning: ranByMeaning(),
        passages: [
          hit(),
          hit({
            rank: 2,
            via: "meaning",
            why: why({ via: "meaning", ranks: { meaning: 1 }, similarity: 0.6 }),
          }),
          hit({
            rank: 3,
            via: "both",
            why: why({ via: "both", terms: ["code"], ranks: { words: 2, meaning: 2 } }),
          }),
        ],
      }),
      true,
    );
    expect(screen.getAllByTestId("documents-test-via")).toHaveLength(3);
    everyControlIsNamed(view, 9);
  });

  it("with a search none of whose ways could run", async () => {
    const view = await triesWith(
      tested({
        status: "unavailable",
        mode: "none",
        passages: [],
        meaning: meaningRun({ message: "Searching by words only: no search model is set up." }),
      }),
      true,
    );
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute(
      "data-status",
      "unavailable",
    );
    everyControlIsNamed(view, 5);
  });

  it("where no search model has been set up, with no second way to try", () => {
    const view = render(<TestQuestion search={noSearchModel()} />);
    expect(screen.queryByTestId("documents-test-ways")).toBeNull();
    everyControlIsNamed(view, 2);
  });
});

describe("the Documents drawer, with search by meaning", () => {
  it.each(CODES)("with the search at code %s", (code) => {
    const view = render(
      <DocumentsDrawer
        chatId="c1"
        view={chatDocuments({ search: searchFor(code) })}
        error={null}
        onChat={vi.fn()}
        onReload={vi.fn().mockResolvedValue(undefined)}
        onManage={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("documents-search-mode")).toBeInTheDocument();
    everyControlIsNamed(view, 3);
  });

  it("with meaning being added, and a file of this chat having its meaning added", () => {
    const view = render(
      <DocumentsDrawer
        chatId="c1"
        view={chatDocuments({
          search: byMeaning({
            adding: addingView({ state: "working", message: "Adding meaning." }),
          }),
          chatDocuments: [adding({ scope: "chat" })],
        })}
        error={null}
        onChat={vi.fn()}
        onReload={vi.fn().mockResolvedValue(undefined)}
        onManage={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    everyControlIsNamed(view, 3);
  });
});

describe("under an answer, with search by meaning", () => {
  const answer = (over: Partial<Message> = {}): Message => ({
    id: "a",
    seq: 2,
    role: "assistant",
    content: "The harbour master keeps the registry [1].",
    reasoning: "",
    attachments: [],
    status: "done",
    error: null,
    sources: [],
    searches: 0,
    search: false,
    model: "m",
    finish: "stop",
    createdAt: 0,
    finishedAt: 1,
    ...over,
  });

  it("each passage a row that opens, whichever way it was found, and each named with how", () => {
    const view = render(
      <MessageView
        chatId="c"
        message={answer({
          sources: [
            passage(),
            passage({ rank: 2, via: "meaning", terms: [], phrases: [], cited: false }),
            passage({ rank: 3, via: "both", cited: false }),
          ],
          retrieval: record({
            mode: "meaning and words",
            meaning: ranByMeaning(),
            passages: 3,
            notes: [{ code: "meaning_not_running", message: "Searching by words only: x." }],
          }),
        })}
        progress={null}
        busy={false}
        readOnly={false}
        onChanged={vi.fn()}
        documents={{ onManage: vi.fn() }}
      />,
    );
    const mine = [...view.container.querySelectorAll('[data-testid="passage-sources"]')].flatMap(
      controlsIn,
    );
    expect(mine).toHaveLength(3);
    expect(lacking(mine, view.container)).toEqual([]);
    for (const toggle of screen.getAllByTestId("passage-toggle")) {
      expect(toggle.getAttribute("aria-label")).toMatch(/, found by (words|meaning|both)/);
    }
  });
});

// --- folders (MR7) ---

describe("the Folders section", () => {
  /** The Documents screen with these folders, from a fake Workbench that answers each route the
   * section uses; `docs` are the person's own files. */
  async function foldersOf(
    fake: FakeFolders,
    me: Pick<Me, "owner" | "consoleUrl"> = PERSON,
    docs: SavedDocument[] = [saved()],
    over: Parameters<typeof page>[1] = {},
  ) {
    fake.install();
    const folders = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation((async (path: string, init?: RequestInit) =>
      path === "/api/documents" ? page(docs, over) : folders(path, init)) as never);
    const view = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} onTools={vi.fn()} me={me} />);
    await settle();
    return view;
  }

  const click = (id: string, at = 0) => fireEvent.click(screen.getAllByTestId(id)[at]!);

  it("with a folder in every state, each with its own buttons", async () => {
    const view = await foldersOf(
      new FakeFolders(
        [
          folder(),
          waitingFolder({ id: "g2", name: "Waiting" }),
          syncingFolder({}, { id: "g3", name: "Syncing" }),
          readingFolder({ id: "g4", name: "Reading" }),
          problemFolder({ id: "g5", name: "Problem" }),
          goneFolder({ id: "g6", name: "Gone" }),
          withdrawnFolder({ id: "g7", name: "Withdrawn" }),
        ],
        { grants: [choice()] },
      ),
    );
    expect(screen.getAllByTestId("folder")).toHaveLength(7);
    // Sync now, a deep check, files, what was left out, rules and Remove on most of seven, the
    // way back, the settings, the add card, and more.
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(50);
    everyControlIsNamed(view, 50);
  });

  it("with no folder given: the owner is offered Toolbox · Tools, and anyone else is told whom to ask", async () => {
    const owner = await foldersOf(new FakeFolders([], { grants: [] }), OWNER);
    expect(screen.getByTestId("folders-open-tools")).toBeInTheDocument();
    everyControlIsNamed(owner, 5);
    owner.unmount();
    const person = await foldersOf(new FakeFolders([], { grants: [] }), PERSON);
    expect(screen.queryByTestId("folders-open-tools")).toBeNull();
    everyControlIsNamed(person, 5);
  });

  it("with Add a folder: the choice, what to leave out first (closed and open), and a refusal", async () => {
    const fake = new FakeFolders([folder()], {
      grants: [
        choice({ id: "g1", name: "Work notes", added: true }),
        choice(),
        choice({ id: "g3", name: "Archive" }),
      ],
    });
    const view = await foldersOf(fake);
    expect(screen.getByTestId("folders-add-select")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
    fireEvent.click(screen.getByTestId("folders-add-rules-toggle"));
    everyControlIsNamed(view, 20);
    fake.refuse.set(
      "POST /api/documents/folders",
      new ApiError("A pattern cannot start with #.", 422, {
        message: "A pattern cannot start with #.",
      }),
    );
    fireEvent.change(screen.getByTestId("folders-add-ignore"), { target: { value: "# x" } });
    click("folders-add-go");
    await settle();
    expect(screen.getByTestId("folders-add-error")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
    fake.refuse.set(
      "POST /api/documents/folders",
      new ApiError("This folder is already in your documents.", 409, {
        message: "This folder is already in your documents.",
      }),
    );
    click("folders-add-go");
    await settle();
    expect(screen.getByTestId("folders-add-problem")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with a folder being removed, asked about first, and one that was withdrawn", async () => {
    const view = await foldersOf(
      new FakeFolders([folder(), withdrawnFolder({ id: "g2", name: "Gone away" })]),
    );
    click("folder-remove");
    expect(screen.getByTestId("folder-remove-confirm")).toBeInTheDocument();
    everyControlIsNamed(view, 17);
  });

  it("with a deep check opened, and a refusal on the card", async () => {
    const fake = new FakeFolders([folder()]);
    const view = await foldersOf(fake);
    fireEvent.click(screen.getByTestId("folder-deep-toggle"));
    everyControlIsNamed(view, 20);
    fake.refuse.set(
      "POST /api/documents/folders/g1/sync",
      new ApiError("The owner took away your access to this folder.", 409, {
        message: "The owner took away your access to this folder.",
      }),
    );
    click("folder-sync");
    await settle();
    expect(screen.getByTestId("folder-problem")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with a folder's files open: the filters, the find box, each file's buttons and the pages", async () => {
    const fake = new FakeFolders([
      folder({
        counts: folderCounts({
          files: 120,
          byState: { ready: 118, skipped: 1, failed: 1 },
          byCode: { not_document: 1, damaged: 1 },
        }),
      }),
    ]);
    fake.files.set("g1", [
      folderFile(),
      pictureFile(),
      brokenFile(),
      waitingFile(),
      ...Array.from({ length: 60 }, (_, i) =>
        folderFile({ id: `p${i}`, path: `more/file-${String(i).padStart(2, "0")}.md` }),
      ),
    ]);
    const view = await foldersOf(fake);
    click("folder-files-toggle");
    await settle();
    expect(screen.getAllByTestId("folder-file").length).toBe(50);
    expect(screen.getByTestId("folder-files-next")).toBeInTheDocument();
    // Open original, the text, and Leave out on fifty, the filters, the find box and the pages.
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(150);
    everyControlIsNamed(view, 150);
  });

  it("with a file's text open, and a file being left out, asked about first", async () => {
    const fake = new FakeFolders([folder()]);
    fake.files.set("g1", [folderFile(), pictureFile()]);
    const view = await foldersOf(fake);
    click("folder-files-toggle");
    await settle();
    fireEvent.click(screen.getByTestId("folder-file-text-toggle"));
    await settle();
    expect(screen.getByTestId("folder-file-text")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
    fireEvent.click(screen.getAllByTestId("folder-file-leave")[0]!);
    expect(screen.getByTestId("folder-file-leave-confirm")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with its files that could not be read, and the retry", async () => {
    const fake = new FakeFolders([folder()]);
    const view = await foldersOf(fake);
    fake.refuse.set("GET /api/documents/folders/g1/files", new Error("Workbench answered 502."));
    click("folder-files-toggle");
    await settle();
    expect(screen.getByTestId("folder-files-retry")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with what was left out open, and when it could not be read", async () => {
    const fake = new FakeFolders([folder()]);
    const view = await foldersOf(fake);
    click("folder-left-toggle");
    await settle();
    expect(screen.getAllByTestId("folder-left-item").length).toBeGreaterThan(0);
    everyControlIsNamed(view, 20);
    click("folder-left-toggle");
    fake.refuse.set("GET /api/documents/folders/g1", new Error("Workbench answered 502."));
    click("folder-left-toggle");
    await settle();
    expect(screen.getByTestId("folder-left-retry")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with what was left out, long and with folders that could not be looked into", async () => {
    const fake = new FakeFolders([folder()]);
    fake.details.set(
      "g1",
      detail({
        unlisted: [{ path: "private", cause: "permission denied" }],
        leftCounts: { ignored_default: 500 },
      }),
    );
    const view = await foldersOf(fake);
    click("folder-left-toggle");
    await settle();
    expect(screen.getByTestId("folder-left-unlisted")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("with the rules open, changed, saved, and refused", async () => {
    const fake = new FakeFolders([folder({ ignore: ["*.log"] })]);
    const view = await foldersOf(fake);
    click("folder-rules-toggle");
    everyControlIsNamed(view, 20);
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), {
      target: { value: "*.log\n*.tmp" },
    });
    everyControlIsNamed(view, 20);
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(screen.getByTestId("folder-rules-saved")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
    fake.refuse.set(
      "PATCH /api/documents/folders/g1",
      new ApiError("A pattern is at most 200 characters.", 422, {
        message: "A pattern is at most 200 characters.",
      }),
    );
    fireEvent.change(screen.getByTestId("folder-rules-ignore"), {
      target: { value: "x".repeat(300) },
    });
    fireEvent.click(screen.getByTestId("folder-rules-save"));
    await settle();
    expect(screen.getByTestId("folder-rules-error")).toBeInTheDocument();
    everyControlIsNamed(view, 20);
  });

  it("where this Workbench cannot read folders, and where the folders could not be read at all", async () => {
    const view = await foldersOf(
      new FakeFolders([folder()], {
        available: false,
        reason: "Local servers need Workbench's own OS account.",
      }),
    );
    expect(screen.getByTestId("folders-unavailable")).toBeInTheDocument();
    everyControlIsNamed(view, 10);
    view.unmount();
    const fake = new FakeFolders([folder()]);
    fake.refuse.set("GET /api/documents/folders", new Error("Workbench answered 502."));
    const failed = await foldersOf(fake);
    expect(screen.getByTestId("folders-retry")).toBeInTheDocument();
    everyControlIsNamed(failed, 5);
  });

  it("where the owner has turned documents off, with a folder to look at and to remove", async () => {
    const view = await foldersOf(
      new FakeFolders([folder(), withdrawnFolder({ id: "g2", name: "Gone away" })]),
      PERSON,
      [saved()],
      {
        installEnabled: false,
      },
    );
    expect(screen.queryByTestId("folder-sync")).toBeNull();
    everyControlIsNamed(view, 7);
  });

  it("with the files of the person's own and the folders both, there is one Ask about your documents, named", async () => {
    const view = await foldersOf(new FakeFolders([folder()]), PERSON, []);
    expect(screen.getAllByTestId("documents-ask")).toHaveLength(1);
    everyControlIsNamed(view, 15);
  });
});

// --- the gate can fail ---

describe("the gate itself", () => {
  it("fails for a control that has no test id, or no name, and passes for one that has both", () => {
    const noId = render(<button type="button">Save</button>);
    expect(() => everyControlIsNamed(noId)).toThrow(/has no data-testid/);
    noId.unmount();
    const noName = render(<input data-testid="x" />);
    expect(() => everyControlIsNamed(noName)).toThrow(/has no accessible name/);
    noName.unmount();
    const fine = render(
      <button type="button" data-testid="save">
        Save
      </button>,
    );
    expect(() => everyControlIsNamed(fine)).not.toThrow();
  });

  it("fails for a screen it finds no control on, since it has then checked nothing", () => {
    const bare = render(<p>Nothing to press.</p>);
    expect(() => everyControlIsNamed(bare)).toThrow(/controls on the screen/);
  });

  it("fails for a screen that has fewer controls than it should, so a control that goes missing is seen", () => {
    const one = render(
      <button type="button" data-testid="only">
        Only
      </button>,
    );
    expect(() => everyControlIsNamed(one, 2)).toThrow(/controls on the screen/);
    expect(() => everyControlIsNamed(one, 1)).not.toThrow();
  });

  it("fails for a link that opens a new tab and has no rel=noopener, and passes for one that says noopener or noreferrer", () => {
    const loose = render(
      <a href="http://nas:8279/library/" target="_blank" data-testid="console">
        Get a search model
      </a>,
    );
    expect(() => everyControlIsNamed(loose)).toThrow(/opens a new tab and has no rel="noopener"/);
    expect(wrong(loose)).toHaveLength(1);
    loose.unmount();
    for (const rel of ["noopener noreferrer", "noreferrer", "noopener"]) {
      const safe = render(
        <a href="http://nas:8279/library/" target="_blank" rel={rel} data-testid="console">
          Get a search model
        </a>,
      );
      expect(() => everyControlIsNamed(safe), rel).not.toThrow();
      expect(wrong(safe), rel).toEqual([]);
      safe.unmount();
    }
    // A link that stays in this tab is not held to it.
    const same = render(
      <a href="/documents" data-testid="same">
        Documents
      </a>,
    );
    expect(() => everyControlIsNamed(same)).not.toThrow();
  });
});
