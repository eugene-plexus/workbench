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
import type { ChatDocumentsView, ChatFile, Message, SavedDocument } from "./lib/types";
import { choose, file, fresh, MODEL, preparing, searched, tooLong } from "./test-documents";
import { controlsIn, everyControlIsNamed, lacking } from "./test-controls";
import {
  broken,
  chatDocuments,
  held,
  hidden,
  markdown,
  nothing,
  off,
  owners,
  page,
  passage,
  prefs,
  queued,
  reading,
  record,
  saved,
  scanned,
  second,
  tested,
  word,
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

async function screenOf(docs: SavedDocument[], over: Parameters<typeof page>[1] = {}) {
  vi.mocked(api).mockResolvedValue(page(docs, over) as never);
  const view = render(<Documents onClose={vi.fn()} onAsk={vi.fn()} />);
  await settle();
  return view;
}

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
});
