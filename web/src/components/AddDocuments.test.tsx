/**
 * Adding files to the person's documents (MR4): a button or a drop, many files at
 * once, each in a request of its own with its own line, each refusal in the server's
 * own words, and what is left stopped when the screen is.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, upload } from "../lib/api";
import { DOCUMENT_ACCEPT } from "../lib/documents";
import type { AddedFiles } from "../lib/types";
import { saved } from "../test-saved";
import { AddDocuments } from "./AddDocuments";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  upload: vi.fn(),
}));

/** What the fake server was sent, in order, and what holds each answer back. */
let sent: { path: string; name: string; fields: number; signal?: AbortSignal }[];
let held: {
  name: string;
  finish: (answer: AddedFiles | Error) => void;
  progress: (n: number) => void;
}[];

beforeEach(() => {
  vi.useFakeTimers();
  sent = [];
  held = [];
  vi.mocked(upload)
    .mockReset()
    .mockImplementation(((
      path: string,
      form: FormData,
      options?: { onProgress?: (n: number) => void; signal?: AbortSignal },
    ) => {
      const file = form.get("files") as File;
      sent.push({
        path,
        name: file.name,
        fields: form.getAll("files").length,
        signal: options?.signal,
      });
      return new Promise<AddedFiles>((resolve, reject) => {
        held.push({
          name: file.name,
          progress: (n) => options?.onProgress?.(n),
          finish: (answer) => (answer instanceof Error ? reject(answer) : resolve(answer)),
        });
      });
    }) as never);
});
afterEach(() => vi.useRealTimers());

const wait = (ms = 0) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const file = (name: string, size = 10) => new File(["x".repeat(size)], name);
const kept = (
  name: string,
  over: Partial<ReturnType<typeof saved>> & { unchanged?: boolean; replaced?: string[] } = {},
): AddedFiles => ({
  documents: [{ ...saved({ name, state: "queued" }), unchanged: false, replaced: [], ...over }],
  refused: [],
});
const refusal = (name: string, message: string, status = 415) =>
  new ApiError(message, status, { message, refused: [{ name, status, message }] });

function opened() {
  const onChanged = vi.fn();
  const view = render(<AddDocuments onChanged={onChanged} />);
  return { ...view, onChanged };
}

const choose = (...files: File[]) =>
  fireEvent.change(screen.getByTestId("documents-input"), { target: { files } });
const dropping = () => screen.getByTestId("documents-drop");
const lines = () => screen.queryAllByTestId("documents-upload");
const words = (at: number) => within(lines()[at]!).getByTestId("documents-upload-words");

/** The file that is going now finishes. */
async function finish(answer: AddedFiles | Error, at = 0) {
  held[at]!.finish(answer);
  await wait();
}

describe("choosing files", () => {
  it("is a button that opens a picker for documents, many at a time, and not for pictures or sound", () => {
    opened();
    const input = screen.getByTestId("documents-input") as HTMLInputElement;
    expect(input).toHaveAttribute("type", "file");
    expect(input).toHaveAttribute("multiple");
    expect(input).toHaveClass("hidden");
    expect(input).toHaveAccessibleName("Choose files to add to your documents");
    const accept = input.getAttribute("accept")!;
    expect(accept).toBe(DOCUMENT_ACCEPT);
    expect(accept).toContain("application/pdf");
    expect(accept).toContain(".docx");
    expect(accept).toContain("text/*");
    expect(accept).not.toMatch(/image|audio/);
    const press = vi.spyOn(input, "click");
    fireEvent.click(screen.getByTestId("documents-add"));
    expect(press).toHaveBeenCalledOnce();
  });

  it("says what it does, and what to do while files are going", () => {
    opened();
    expect(screen.getByRole("heading", { level: 3, name: "Add files" })).toBeInTheDocument();
    expect(dropping()).toHaveTextContent(
      "Choose files, or drop them here. Workbench reads each one so a chat can search it.",
    );
    expect(screen.getByTestId("documents-add")).toHaveAccessibleDescription(
      /Choose files, or drop them here\./,
    );
    expect(screen.queryByTestId("documents-uploads")).toBeNull();
  });

  it("can choose the same file again", async () => {
    opened();
    const input = screen.getByTestId("documents-input") as HTMLInputElement;
    // A picker says nothing when the same file is chosen again unless it was emptied.
    const emptied = vi.spyOn(input, "value", "set");
    choose(file("a.pdf"));
    await wait();
    expect(emptied).toHaveBeenCalledWith("");
  });
});

describe("adding many files", () => {
  it("sends one at a time, each in a request of its own, in the order they were chosen", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"), file("c.docx"));
    await wait();
    expect(sent).toEqual([
      { path: "/api/documents", name: "a.pdf", fields: 1, signal: expect.any(AbortSignal) },
    ]);
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual([
      "adding",
      "waiting",
      "waiting",
    ]);
    await finish(kept("a.pdf"));
    expect(sent.map((s) => s.name)).toEqual(["a.pdf", "b.md"]);
    await finish(kept("b.md"), 1);
    expect(sent.map((s) => s.name)).toEqual(["a.pdf", "b.md", "c.docx"]);
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual(["added", "added", "adding"]);
    await finish(kept("c.docx"), 2);
    expect(sent.every((s) => s.fields === 1 && s.path === "/api/documents")).toBe(true);
  });

  it("gives each file its own line: its name, its size, and where it is", async () => {
    opened();
    choose(file("a.pdf", 2048), file("b.md", 10));
    await wait();
    expect(within(lines()[0]!).getByText("a.pdf")).toBeInTheDocument();
    expect(within(lines()[0]!).getByText("2 KiB")).toBeInTheDocument();
    expect(words(0)).toHaveTextContent("Adding… 0%");
    expect(words(1)).toHaveTextContent("Waiting");
  });

  it("shows how much of the file that is going has gone, and gives the bar a name", async () => {
    opened();
    choose(file("a.pdf"));
    await wait();
    act(() => held[0]!.progress(0.4));
    expect(words(0)).toHaveTextContent("Adding… 40%");
    const bar = within(lines()[0]!).getByRole("progressbar", { name: "Adding a.pdf" });
    expect(bar).toHaveAttribute("value", "40");
    expect(bar).toHaveAttribute("max", "100");
    await finish(kept("a.pdf"));
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("says where it is while files go, asks to be stayed on, and says what came of them after", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent("Adding 1 of 2…");
    expect(screen.getByTestId("documents-uploads")).toHaveTextContent(
      "Stay on this page until every file is added.",
    );
    expect(screen.getByTestId("documents-upload-summary")).toHaveAttribute("role", "status");
    await finish(kept("a.pdf"));
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent("Adding 2 of 2…");
    expect(words(0)).toHaveTextContent("Added. Workbench is reading it.");
    await finish(kept("b.md"), 1);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent("Added 2 files.");
    expect(screen.getByTestId("documents-uploads")).not.toHaveTextContent("Stay on this page");
  });

  it("tells the list to read again after each file, so each shows up waiting to be read", async () => {
    const { onChanged } = opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    expect(onChanged).not.toHaveBeenCalled();
    await finish(kept("a.pdf"));
    expect(onChanged).toHaveBeenCalledTimes(1);
    await finish(kept("b.md"), 1);
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("says when a file is already there, and when it takes the place of an older one", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    await finish(kept("a.pdf", { unchanged: true }));
    await finish(kept("b.md", { replaced: ["old"] }), 1);
    expect(words(0)).toHaveTextContent("a.pdf is already in your documents, exactly as it is.");
    expect(words(1)).toHaveTextContent(
      "Added. It takes the place of the older file with the same name.",
    );
  });

  it("is added to while it goes: the new files wait behind the ones already there", async () => {
    opened();
    choose(file("a.pdf"));
    await wait();
    choose(file("b.md"), file("c.docx"));
    await wait();
    expect(lines()).toHaveLength(3);
    expect(sent.map((s) => s.name)).toEqual(["a.pdf"]);
    await finish(kept("a.pdf"));
    await finish(kept("b.md"), 1);
    await finish(kept("c.docx"), 2);
    expect(sent.map((s) => s.name)).toEqual(["a.pdf", "b.md", "c.docx"]);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent("Added 3 files.");
  });
});

describe("a file that is not added", () => {
  it("is refused in the server's own words, on its own line, and the files after it still go", async () => {
    opened();
    choose(file("a.pdf"), file("picture.png"), file("c.md"));
    await wait();
    await finish(kept("a.pdf"));
    await finish(
      refusal(
        "picture.png",
        "picture.png is an image, not a document. My documents keeps PDF, Word, Markdown, text, CSV, HTML and code files.",
      ),
      1,
    );
    expect(lines()[1]).toHaveAttribute("data-state", "refused");
    expect(words(1)).toHaveTextContent(
      "picture.png is an image, not a document. My documents keeps PDF, Word, Markdown, text, CSV, HTML and code files.",
    );
    expect(words(1)).toHaveClass("text-warn");
    await finish(kept("c.md"), 2);
    expect(sent.map((s) => s.name)).toEqual(["a.pdf", "picture.png", "c.md"]);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent(
      "Added 2 files. 1 file was not added.",
    );
  });

  it("says each kind of refusal in its own words: too large, too many passages, a disk that is full", async () => {
    opened();
    choose(file("big.pdf"), file("full.md"), file("disk.md"));
    await wait();
    await finish(
      refusal("big.pdf", "big.pdf is larger than the 50 MiB Workbench reads of one file.", 413),
    );
    await finish(refusal("full.md", "You already keep 100,000 passages, the limit.", 409), 1);
    await finish(
      refusal(
        "disk.md",
        "disk.md was not added: the disk Workbench keeps your documents on is full (1 MiB free).",
        507,
      ),
      2,
    );
    expect(words(0)).toHaveTextContent("larger than the 50 MiB");
    expect(words(1)).toHaveTextContent("You already keep 100,000 passages");
    expect(words(2)).toHaveTextContent(
      "the disk Workbench keeps your documents on is full (1 MiB free)",
    );
    // A refusal of the file is a warning; a disk that cannot take it is an error.
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual([
      "refused",
      "refused",
      "failed",
    ]);
    expect(words(2)).toHaveClass("text-error");
  });

  it("says the connection was lost when it was, and goes on to the next", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    await finish(new Error("The connection to Workbench was lost while the file was being added."));
    expect(lines()[0]).toHaveAttribute("data-state", "failed");
    expect(words(0)).toHaveTextContent("The connection to Workbench was lost");
    await finish(kept("b.md"), 1);
    // The file that was added is in the list of documents; the one that was lost stays here.
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual(["failed"]);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent(
      "Added 1 file. 1 file was not added.",
    );
  });

  it("is a file refused with no words at all said plainly", async () => {
    opened();
    choose(file("a.pdf"));
    await wait();
    await finish({
      documents: [],
      refused: [{ name: "a.pdf", status: 415, message: "a.pdf cannot be read." }],
    });
    expect(lines()[0]).toHaveAttribute("data-state", "refused");
    expect(words(0)).toHaveTextContent("a.pdf cannot be read.");
  });
});

describe("dropping files", () => {
  const drag = (types: string[]) => ({ dataTransfer: { types, files: [], dropEffect: "" } });

  it("shows where to drop while files are over the screen, and not for anything else", () => {
    opened();
    fireEvent.dragEnter(dropping(), drag(["text/plain"]));
    expect(dropping()).not.toHaveClass("ring-accent");
    fireEvent.dragEnter(dropping(), drag(["Files"]));
    expect(dropping()).toHaveClass("ring-accent");
    expect(dropping()).toHaveTextContent("Drop the files to add them.");
    fireEvent.dragLeave(dropping());
    expect(dropping()).not.toHaveClass("ring-accent");
    expect(dropping()).toHaveTextContent("Choose files, or drop them here.");
  });

  it("stays marked while the files move over what is inside it", () => {
    opened();
    fireEvent.dragEnter(dropping(), drag(["Files"]));
    fireEvent.dragEnter(dropping(), drag(["Files"]));
    fireEvent.dragLeave(dropping());
    expect(dropping()).toHaveClass("ring-accent");
    fireEvent.dragLeave(dropping());
    expect(dropping()).not.toHaveClass("ring-accent");
  });

  it("adds what is dropped, as if it had been chosen", async () => {
    opened();
    fireEvent.drop(dropping(), {
      dataTransfer: { types: ["Files"], files: [file("a.pdf"), file("b.md")] },
    });
    await wait();
    expect(lines()).toHaveLength(2);
    expect(sent.map((s) => s.name)).toEqual(["a.pdf"]);
    expect(dropping()).not.toHaveClass("ring-accent");
  });

  it("refuses a folder in plain words, and adds the files dropped with it", async () => {
    opened();
    const folder = new File([], "scans");
    fireEvent.drop(dropping(), {
      dataTransfer: {
        types: ["Files"],
        files: [folder, file("a.pdf")],
        items: [
          { kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, name: "scans" }) },
          { kind: "file", webkitGetAsEntry: () => ({ isDirectory: false, name: "a.pdf" }) },
        ],
      },
    });
    await wait();
    const states = lines().map((l) => [
      within(l).getByTestId("documents-upload-name").textContent,
      l.getAttribute("data-state"),
    ]);
    expect(states).toEqual([
      ["scans", "refused"],
      ["a.pdf", "adding"],
    ]);
    expect(words(0)).toHaveTextContent("scans is a folder. Choose or drop the files in it.");
    expect(sent.map((s) => s.name)).toEqual(["a.pdf"]);
    // A folder has no size to say; a file has.
    expect(lines()[0]).not.toHaveTextContent("KiB");
    expect(lines()[1]).toHaveTextContent("1 KiB");
  });

  it("is not fooled by a file that has the name of a folder", async () => {
    opened();
    fireEvent.drop(dropping(), {
      dataTransfer: {
        types: ["Files"],
        files: [new File(["x"], "scans", { type: "application/pdf" })],
        items: [{ kind: "file", webkitGetAsEntry: () => ({ isDirectory: true, name: "scans" }) }],
      },
    });
    await wait();
    // The folder is said, and the file that has its name and a type is a file.
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual(["refused", "adding"]);
  });
});

describe("clearing and leaving", () => {
  it("clears the list once nothing is going, and not before", async () => {
    opened();
    choose(file("a.pdf"));
    await wait();
    expect(screen.queryByTestId("documents-uploads-clear")).toBeNull();
    await finish(kept("a.pdf"));
    fireEvent.click(screen.getByTestId("documents-uploads-clear"));
    expect(screen.queryByTestId("documents-uploads")).toBeNull();
  });

  it("keeps the words about a refusal until the person has read them and cleared them", async () => {
    opened();
    choose(file("a.png"));
    await wait();
    await finish(refusal("a.png", "a.png is an image."));
    await wait(60_000);
    expect(words(0)).toHaveTextContent("a.png is an image.");
  });

  it("stops what is going, and sends nothing more, when the screen is left", async () => {
    const view = opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    const signal = sent[0]!.signal!;
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
    held[0]!.finish(new DOMException("The upload was stopped.", "AbortError") as never);
    await wait();
    expect(sent.map((s) => s.name)).toEqual(["a.pdf"]);
    // Nothing is said of the file that was stopped, and the list is not asked to read again for it.
    expect(view.onChanged).not.toHaveBeenCalled();
  });
});

describe("once every file has gone", () => {
  it("does not say again, in a list of its own, a file that was added with nothing more to say: it is in the list of documents", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    await finish(kept("a.pdf"));
    await finish(kept("b.md"), 1);
    expect(lines()).toHaveLength(0);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent("Added 2 files.");
    expect(screen.getByTestId("documents-uploads").querySelector("ul")).toBeNull();
    // What is left is to clear the line that says so.
    expect(screen.getByTestId("documents-uploads-clear")).toBeInTheDocument();
  });

  it("keeps what the person has to read: a refusal, a file that was already there, one that took another's place", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"), file("c.docx"), file("d.txt"));
    await wait();
    await finish(kept("a.pdf"));
    await finish(refusal("b.md", "b.md is damaged."), 1);
    await finish(kept("c.docx", { unchanged: true }), 2);
    await finish(kept("d.txt", { replaced: ["old"] }), 3);
    expect(
      lines().map((l) => [
        within(l).getByTestId("documents-upload-name").textContent,
        l.getAttribute("data-state"),
      ]),
    ).toEqual([
      ["b.md", "refused"],
      ["c.docx", "added"],
      ["d.txt", "added"],
    ]);
    expect(screen.getByTestId("documents-upload-summary")).toHaveTextContent(
      "Added 3 files. 1 file was not added.",
    );
  });

  it("shows every line while files are still going, the added ones too", async () => {
    opened();
    choose(file("a.pdf"), file("b.md"));
    await wait();
    await finish(kept("a.pdf"));
    expect(lines().map((l) => l.getAttribute("data-state"))).toEqual(["added", "adding"]);
  });
});
