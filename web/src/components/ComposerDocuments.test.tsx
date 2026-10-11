/**
 * Attaching documents in the composer (MR2): what a person sees from the moment
 * a file is attached to the moment its message is sent, against a fake server
 * that answers as the real one does (src/eugene_plexus_workbench/documents_api.py).
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, api, del, patch, post, put } from "../lib/api";
import type { Chat, ChatFile, ChatFiles, Me, Message, Model, Models } from "../lib/types";
import { FIRST_POLL_MS, POLL_MS } from "../lib/useChatFiles";
import {
  choose,
  fresh,
  file,
  listing,
  MODEL,
  native,
  partial,
  READ_FIRST,
  scan,
  tooLong,
} from "../test-documents";
import { Composer } from "./Composer";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
};
const model = (over: Partial<Model> = {}): Model => ({
  id: "local",
  ready: true,
  onDemand: false,
  imageInput: true,
  fileInput: false,
  audioInput: false,
  webSearch: false,
  contextLength: 32768,
  ...over,
});
const models: Models = {
  models: [
    model(),
    model({ id: "small", contextLength: 4096 }),
    model({ id: "reads-pdfs", fileInput: true }),
  ],
  webSearch: { available: false, reason: "No search account." },
};
const sent: Message = {
  id: "u",
  seq: 1,
  role: "user",
  content: "Hello",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "local",
  finish: null,
  createdAt: 1,
  finishedAt: null,
};

/** What the fake server holds: the files it lists, and what an upload answers. */
let listed: ChatFile[];
let uploads: ChatFile[];
let asked: string[];

function serve() {
  vi.mocked(api).mockImplementation((async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") return uploads.shift();
    asked.push(path);
    const query = path.split("?model=")[1];
    return listing(listed, {
      model: { ...MODEL, id: query ? decodeURIComponent(query) : MODEL.id },
    }) as ChatFiles;
  }) as never);
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(api).mockReset();
  vi.mocked(del).mockReset().mockResolvedValue(undefined);
  vi.mocked(patch).mockReset().mockResolvedValue(chat);
  vi.mocked(post).mockReset().mockResolvedValue({ user: sent, answer: sent });
  vi.mocked(put).mockReset();
  listed = [];
  uploads = [];
  asked = [];
  serve();
});
afterEach(() => vi.useRealTimers());

function composer(over: { chat?: Chat; onSent?: () => void; initial?: ChatFile[] } = {}) {
  const onSent = over.onSent ?? vi.fn();
  const view = render(
    <Composer
      chat={over.chat ?? chat}
      initialAttachments={over.initial}
      me={me}
      models={models}
      modelsError={null}
      running={false}
      onSent={onSent}
      onChat={vi.fn()}
    />,
  );
  return { ...view, onSent };
}

const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

/** Drops a file on the composer, and lets the upload answer. */
async function drop(name: string, uploaded: ChatFile) {
  uploads.push(uploaded);
  fireEvent.drop(screen.getByTestId("composer-area"), {
    dataTransfer: { files: [new File(["x"], name, { type: "application/octet-stream" })] },
  });
  await wait(0);
}

const send = () => screen.getByTestId("send");
const chip = () => screen.getByTestId("document-chip");

/** The server reads the file: what it lists from now on. */
const read = (...now: ChatFile[]) => {
  listed = now;
};

describe("attaching a document", () => {
  it("is a card that says it is being read, and Send waits with the reason beside it", async () => {
    composer();
    await drop("report.pdf", fresh());
    expect(chip()).toHaveAttribute("data-state", "reading");
    expect(send()).toBeDisabled();
    const reason = screen.getByTestId("send-held");
    expect(reason).toHaveTextContent("Waiting for Workbench to read report.pdf.");
    expect(reason).not.toHaveAttribute("role");
    expect(send()).toHaveAccessibleDescription("Waiting for Workbench to read report.pdf.");
    // The reason is beside Send, not in the alert that says a model cannot take a file.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("asks the server about it for the model that is picked, from the upload on", async () => {
    composer();
    await drop("report.pdf", fresh());
    const [path, init] = vi.mocked(api).mock.calls[0]!;
    expect(path).toBe("/api/chats/one/files?model=local");
    expect(init).toMatchObject({ method: "POST" });
    read(file());
    await wait(FIRST_POLL_MS);
    expect(asked).toEqual(["/api/chats/one/files?model=local"]);
  });

  it("goes from reading to ready by itself, and Send goes with it", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(fresh());
    await wait(FIRST_POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "reading");
    read(file());
    await wait(POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "whole");
    expect(within(chip()).getByTestId("document-meta")).toHaveTextContent(
      "PDF · 3 pages · about 77 tokens",
    );
    expect(screen.getByTestId("document-status")).toHaveTextContent("Will be read whole.");
    expect(send()).toBeEnabled();
    expect(screen.queryByTestId("send-held")).toBeNull();
    expect(send()).not.toHaveAttribute("aria-describedby");
    // And it is quiet from then on.
    const calls = vi.mocked(api).mock.calls.length;
    await wait(30_000);
    expect(vi.mocked(api).mock.calls.length).toBe(calls);
  });

  it("tells a screen reader what the file came to when it has been read", async () => {
    composer();
    await drop("report.pdf", fresh());
    expect(screen.getByTestId("files-announcer")).toHaveTextContent("");
    read(file());
    await wait(FIRST_POLL_MS);
    expect(screen.getByTestId("files-announcer")).toHaveTextContent(
      "report.pdf: Will be read whole.",
    );
    expect(screen.getByTestId("files-announcer")).toHaveAttribute("aria-live", "polite");
  });

  it("does not send while it is read, by Send or by Enter", async () => {
    composer();
    await drop("report.pdf", fresh());
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "What is on page 3?" } });
    fireEvent.click(send());
    fireEvent.keyDown(screen.getByTestId("composer"), { key: "Enter" });
    expect(post).not.toHaveBeenCalled();
  });

  it("sends the file's id with the message, and the message shows how it was read", async () => {
    const { onSent } = composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "What is on page 3?" } });
    fireEvent.click(send());
    await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/chats/one/messages", {
      content: "What is on page 3?",
      attachments: ["f1"],
      model: "local",
      search: false,
    });
    expect(onSent).toHaveBeenCalledWith(
      expect.objectContaining({
        files: [
          {
            id: "f1",
            name: "report.pdf",
            mediaType: "application/pdf",
            reading: file().reading,
          },
        ],
      }),
      sent,
      undefined,
    );
    // The card goes with the message, and nothing is read again for it.
    expect(screen.queryByTestId("document-chip")).toBeNull();
    const calls = vi.mocked(api).mock.calls.length;
    await wait(30_000);
    expect(vi.mocked(api).mock.calls.length).toBe(calls);
  });

  it("can be sent alone, with no words, once it is read", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    expect(send()).toBeEnabled();
  });
});

describe("a document that needs a choice", () => {
  async function longFile() {
    composer();
    await drop("log.txt", fresh({ id: "f2", name: "log.txt", mediaType: "text/plain" }));
    read(choose());
    await wait(FIRST_POLL_MS);
  }

  it("holds Send, with what to do, until the person chooses", async () => {
    await longFile();
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(send()).toBeDisabled();
    expect(screen.getByTestId("send-held")).toHaveTextContent(
      "Choose how much of log.txt to read, search it instead, or remove it.",
    );
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "Summarize it." } });
    expect(send()).toBeDisabled();
    fireEvent.keyDown(screen.getByTestId("composer"), { key: "Enter" });
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps the choice with the file, shows it, and lets Send go", async () => {
    await longFile();
    vi.mocked(put).mockImplementation(async () => {
      read(partial());
      return partial();
    });
    fireEvent.click(screen.getByTestId("document-offer-first"));
    await wait(0);
    expect(put).toHaveBeenCalledExactlyOnceWith("/api/chats/one/files/f2/reading?model=local", {
      mode: "first",
      tokens: READ_FIRST.tokens,
    });
    expect(chip()).toHaveAttribute("data-state", "partial");
    expect(screen.getByTestId("document-choice")).toHaveTextContent(
      "Your choice: the first 3,000 tokens.",
    );
    expect(send()).toBeEnabled();
    expect(screen.queryByTestId("send-held")).toBeNull();
  });

  it("is taken back by Choose again, and the offers come back", async () => {
    await longFile();
    vi.mocked(put).mockImplementation(async () => {
      read(partial());
      return partial();
    });
    fireEvent.click(screen.getByTestId("document-offer-first"));
    await wait(0);
    vi.mocked(put).mockImplementation(async () => {
      read(choose());
      return choose();
    });
    fireEvent.click(screen.getByTestId("document-choose-again"));
    await wait(0);
    expect(vi.mocked(put).mock.calls[1]![1]).toEqual({ mode: null });
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(screen.getByTestId("document-offer-all")).toBeInTheDocument();
  });

  it("is refused with the server's words on the card, and the file stays as it was", async () => {
    await longFile();
    vi.mocked(put).mockRejectedValue(
      new ApiError("Too long.", 409, {
        message: "About 9,300 tokens. Choose how much to read.",
        code: "choice",
        files: [],
      }),
    );
    fireEvent.click(screen.getByTestId("document-offer-all"));
    await wait(0);
    const problem = screen.getByTestId("document-problem");
    expect(problem).toHaveTextContent("Your choice was not kept");
    expect(problem).toHaveTextContent("About 9,300 tokens. Choose how much to read.");
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(send()).toBeDisabled();
  });

  it("is taken back when the file is removed, and Send is free again", async () => {
    await longFile();
    fireEvent.click(screen.getByRole("button", { name: "Remove log.txt" }));
    await wait(0);
    expect(del).toHaveBeenCalledWith("/api/chats/one/files/f2");
    expect(screen.queryByTestId("document-chip")).toBeNull();
    expect(screen.queryByTestId("send-held")).toBeNull();
  });
});

describe("a document that cannot be read", () => {
  it("holds Send with the file's name, and Remove frees it", async () => {
    composer();
    await drop("scan.pdf", fresh({ id: "f4", name: "scan.pdf" }));
    read(scan());
    await wait(FIRST_POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "skipped");
    expect(screen.getByTestId("send-held")).toHaveTextContent(
      "scan.pdf cannot be read. Remove it to send your message.",
    );
    expect(send()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove scan.pdf" }));
    await wait(0);
    expect(screen.queryByTestId("send-held")).toBeNull();
  });
});

describe("a different model", () => {
  it("asks about every file again, holds Send while it does, and shows the decisions for it", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "whole");
    asked.length = 0;
    let answer!: (listing: ChatFiles) => void;
    vi.mocked(api).mockImplementation(
      () => new Promise((resolve) => (answer = resolve as never)) as never,
    );
    fireEvent.change(screen.getByTestId("model-picker"), { target: { value: "small" } });
    await wait(0);
    expect(vi.mocked(api).mock.calls.at(-1)![0]).toBe("/api/chats/one/files?model=small");
    expect(send()).toBeDisabled();
    expect(screen.getByTestId("send-held")).toHaveTextContent("Checking your files against small…");
    await act(async () =>
      answer(
        listing([tooLong({ id: "f1", name: "report.pdf" })], {
          model: { ...MODEL, id: "small", window: 4096 },
        }),
      ),
    );
    expect(chip()).toHaveAttribute("data-state", "too_long");
    expect(screen.getByTestId("document-status")).toHaveTextContent("window is 4,096 tokens");
    expect(screen.getByTestId("send-held")).toHaveTextContent(
      "Choose how much of report.pdf to read, search it instead, or remove it.",
    );
  });

  it("asks nothing when there is no document", async () => {
    composer();
    fireEvent.change(screen.getByTestId("model-picker"), { target: { value: "small" } });
    await wait(5000);
    expect(api).not.toHaveBeenCalled();
  });

  it("sends the PDF as the file itself to a model that reads PDFs, and says so", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    read(native());
    fireEvent.change(screen.getByTestId("model-picker"), { target: { value: "reads-pdfs" } });
    await wait(0);
    expect(chip()).toHaveAttribute("data-state", "native");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "reads-pdfs reads PDFs itself, so Workbench sends the file as it is.",
    );
    expect(send()).toBeEnabled();
  });
});

describe("a send the server refuses", () => {
  const refusal = (files: unknown[], message = "log.txt: About 9,300 tokens.") =>
    new ApiError(message, 409, { message, code: "documents", files });

  it("names the file on its own card, keeps the words, and sends nothing", async () => {
    const { onSent } = composer();
    await drop("log.txt", fresh({ id: "f2", name: "log.txt" }));
    // The page believed the file would go.
    read(file({ id: "f2", name: "log.txt" }));
    await wait(FIRST_POLL_MS);
    expect(send()).toBeEnabled();
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "Summarize it." } });
    vi.mocked(post).mockRejectedValue(
      refusal([{ id: "f2", name: "log.txt", decision: choose().decision }]),
    );
    read(choose());
    fireEvent.click(send());
    await wait(0);
    const problem = screen.getByTestId("document-problem");
    expect(problem).toHaveAttribute("role", "alert");
    expect(problem).toHaveTextContent("Your message was not sent");
    expect(problem).toHaveTextContent(choose().decision!.message);
    // The card is what the server says now, with the way out.
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(screen.getByTestId("document-offer-first")).toBeInTheDocument();
    expect(screen.getByTestId("composer")).toHaveValue("Summarize it.");
    expect(onSent).not.toHaveBeenCalled();
    // Not repeated in the alert that is for the composer as a whole.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(send()).toBeDisabled();
  });

  it("names each file it names, and no other", async () => {
    composer();
    await drop("a.txt", fresh({ id: "a", name: "a.txt" }));
    await drop("b.pdf", fresh({ id: "b", name: "b.pdf" }));
    await drop("c.pdf", fresh({ id: "c", name: "c.pdf" }));
    read(
      file({ id: "a", name: "a.txt" }),
      file({ id: "b", name: "b.pdf" }),
      file({ id: "c", name: "c.pdf" }),
    );
    await wait(FIRST_POLL_MS);
    vi.mocked(post).mockRejectedValue(
      refusal([
        { id: "a", name: "a.txt", decision: choose({ id: "a" }).decision },
        { id: "c", name: "c.pdf", decision: scan({ id: "c" }).decision },
      ]),
    );
    read(
      choose({ id: "a", name: "a.txt" }),
      file({ id: "b", name: "b.pdf" }),
      scan({ id: "c", name: "c.pdf" }),
    );
    fireEvent.click(send());
    await wait(0);
    const cards = screen.getAllByTestId("document-chip");
    expect(within(cards[0]!).getByTestId("document-problem")).toBeInTheDocument();
    expect(within(cards[1]!).queryByTestId("document-problem")).toBeNull();
    expect(within(cards[2]!).getByTestId("document-problem")).toHaveTextContent(
      "Skipped: this PDF has no text",
    );
  });

  it("says a file that was still being read was only not read yet", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    vi.mocked(post).mockRejectedValue(
      refusal(
        [{ id: "f1", name: "report.pdf", decision: fresh().decision }],
        "report.pdf is still being read. Try again in a moment.",
      ),
    );
    fireEvent.click(send());
    await wait(0);
    expect(screen.getByTestId("document-problem")).toHaveTextContent(
      "This file was still being read. Try again in a moment.",
    );
  });

  it("says a refusal that names no file, in the composer's own alert", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    vi.mocked(post).mockRejectedValue(
      refusal(
        [],
        "This chat and its documents come to about 40,000 tokens, more than this model's window of 32,768 leaves room for.",
      ),
    );
    fireEvent.click(send());
    await wait(0);
    expect(screen.queryByTestId("document-problem")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("come to about 40,000 tokens");
  });

  it("says any other failure as it always did", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    vi.mocked(post).mockRejectedValue(new Error("The model is not loaded."));
    fireEvent.click(send());
    await wait(0);
    expect(screen.getByRole("alert")).toHaveTextContent("The model is not loaded.");
    expect(screen.queryByTestId("document-problem")).toBeNull();
  });
});

describe("what is attached is what is carried", () => {
  it("finds a file that was attached before a reload when the page first asks, and sends it too", async () => {
    composer();
    listed = [choose({ id: "old", name: "forgotten.txt" })];
    await drop("report.pdf", fresh());
    listed = [choose({ id: "old", name: "forgotten.txt" }), file()];
    await wait(FIRST_POLL_MS);
    const names = screen.getAllByTestId("document-name").map((n) => n.textContent);
    expect(names).toEqual(["report.pdf", "forgotten.txt"]);
    // The forgotten file needs a choice, and it holds Send like any other.
    expect(send()).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove forgotten.txt" }));
    await wait(0);
    expect(del).toHaveBeenCalledWith("/api/chats/one/files/old");
  });

  it("hands over a file that was sent to this chat from the media area, as a chip like any other", () => {
    composer({ initial: [{ id: "m", name: "square.png", mediaType: "image/png" }] });
    expect(screen.getByRole("button", { name: "Remove square.png" })).toBeInTheDocument();
    expect(screen.queryByTestId("document-chip")).toBeNull();
    expect(api).not.toHaveBeenCalled();
  });

  it("keeps an image's chip beside a document's, in the order they were attached", async () => {
    composer();
    await drop("shot.png", { id: "i", name: "shot.png", mediaType: "image/png", kind: "image" });
    await drop("report.pdf", fresh());
    const list = screen.getByLabelText("Attached");
    expect(Array.from(list.children).map((c) => c.getAttribute("data-testid"))).toEqual([
      null,
      "document-chip",
    ]);
  });
});

describe("when the page cannot ask", () => {
  it("says so beside the files, and keeps trying", async () => {
    composer();
    await drop("report.pdf", fresh());
    vi.mocked(api).mockRejectedValue(new Error("Workbench answered 502"));
    await wait(FIRST_POLL_MS);
    const note = screen.getByTestId("files-error");
    expect(note).toHaveAttribute("role", "alert");
    expect(note).toHaveTextContent(
      "Workbench could not check on your files: Workbench answered 502. It keeps trying.",
    );
    // What it knew is still shown, and Send still waits for it.
    expect(chip()).toHaveAttribute("data-state", "reading");
    expect(send()).toBeDisabled();
    serve();
    read(file());
    await wait(5000);
    expect(screen.queryByTestId("files-error")).toBeNull();
    expect(chip()).toHaveAttribute("data-state", "whole");
  });
});

describe("a PDF is not refused for a model that cannot read one itself", () => {
  it("because Workbench reads it, and sends its text", async () => {
    composer();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "whole");
    expect(screen.queryByText(/does not take PDFs/)).toBeNull();
    expect(send()).toBeEnabled();
  });

  it("but one Workbench did not read is, as it was: only some models take it as a file", async () => {
    composer();
    await drop("report.pdf", file({ reading: null, decision: native({ reading: null }).decision }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "local does not take PDFs. Pick a model that does, or remove the file.",
    );
    expect(send()).toBeDisabled();
  });
});

describe("the Attach button", () => {
  it("offers Word and text files beside PDFs and images, and has a name", () => {
    composer();
    const input = screen.getByTestId("attach-input");
    expect(input).toHaveAttribute("aria-label", "Choose files to attach");
    const accept = input.getAttribute("accept")!.split(",");
    for (const wanted of [
      "application/pdf",
      "image/png",
      ".docx",
      ".md",
      ".csv",
      ".py",
      "text/*",
    ]) {
      expect(accept).toContain(wanted);
    }
    expect(input).toHaveAttribute("multiple");
  });

  it("takes a dropped Markdown file as it takes a PDF", async () => {
    composer();
    await drop("notes.md", fresh({ id: "n", name: "notes.md", mediaType: "text/markdown" }));
    expect(chip()).toHaveTextContent("notes.md");
  });

  it("says what the server says when it does not take a file, and attaches nothing", async () => {
    composer();
    vi.mocked(api).mockRejectedValueOnce(
      new ApiError(
        "This file is a program. Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
        415,
      ),
    );
    fireEvent.drop(screen.getByTestId("composer-area"), {
      dataTransfer: { files: [new File(["x"], "tool.exe")] },
    });
    await wait(0);
    expect(screen.getByRole("alert")).toHaveTextContent("tool.exe: This file is a program.");
    expect(screen.queryByTestId("document-chip")).toBeNull();
  });
});

describe("taking a file back from the keyboard", () => {
  async function withChoice() {
    composer();
    await drop("log.txt", fresh({ id: "f2", name: "log.txt" }));
    read(choose());
    await wait(FIRST_POLL_MS);
  }

  it("returns the keyboard to the message box, since the button that had it is gone", async () => {
    await withChoice();
    const remove = screen.getByRole("button", { name: "Remove log.txt" });
    remove.focus();
    fireEvent.click(remove);
    await wait(0);
    expect(screen.queryByTestId("document-chip")).toBeNull();
    expect(screen.getByTestId("composer")).toHaveFocus();
  });

  it("does not take the keyboard from someone who was writing", async () => {
    await withChoice();
    const box = screen.getByTestId("composer");
    box.focus();
    fireEvent.click(screen.getByRole("button", { name: "Remove log.txt" }));
    await wait(0);
    expect(box).toHaveFocus();
  });

  it("keeps the keyboard where it was when the file could not be taken back", async () => {
    await withChoice();
    vi.mocked(del).mockRejectedValueOnce(new Error("The disk is read only."));
    const remove = screen.getByRole("button", { name: "Remove log.txt" });
    remove.focus();
    fireEvent.click(remove);
    await wait(0);
    expect(screen.getByRole("alert")).toHaveTextContent("log.txt was not removed");
    expect(screen.getByTestId("document-chip")).toBeInTheDocument();
    expect(screen.getByTestId("composer")).not.toHaveFocus();
  });
});

describe("the keyboard while files are read", () => {
  it("is not moved, and the text box keeps the focus it had", async () => {
    composer();
    const box = screen.getByTestId("composer");
    box.focus();
    await drop("report.pdf", fresh());
    read(file());
    await wait(FIRST_POLL_MS);
    expect(box).toHaveFocus();
  });
});
