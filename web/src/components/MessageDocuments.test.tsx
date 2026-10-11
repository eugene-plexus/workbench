/**
 * What a message shows of documents (MR2): the files a person's message carried,
 * the files an answer lists under Sources (what was SENT, never what the model
 * used), and what an answer says while it waits for its files to be read.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";

import { chatMarkdown } from "../lib/conveniences";
import { fileText, fileUrl } from "../lib/api";
import type { AttachedFile, Message, Progress, Source } from "../lib/types";
import { partSource, reading, source } from "../test-documents";
import { MessageView } from "./MessageView";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fileText: vi.fn(),
  fileUrl: vi.fn(),
  post: vi.fn(),
}));

const message = (over: Partial<Message> = {}): Message => ({
  id: "a",
  seq: 2,
  role: "assistant",
  content: "The code word is TAMARIND-6042.",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

function show(shown: Message, progress: Progress | null = null, readOnly = false) {
  return render(
    <MessageView
      chatId="c"
      message={shown}
      progress={progress}
      busy={false}
      readOnly={readOnly}
      onChanged={() => undefined}
    />,
  );
}

const web: Source = { kind: "web", url: "https://example.org/a", title: "A page" };

beforeEach(() => {
  vi.mocked(fileText).mockReset();
  vi.mocked(fileUrl).mockReset();
});

describe("the files under an answer", () => {
  it("are listed by name and by the pages that were sent, of how many", () => {
    show(message({ sources: [source()] }));
    const files = screen.getByTestId("document-sources");
    expect(files).toHaveTextContent("Files sent to the model");
    const entry = within(files).getByTestId("document-source");
    expect(entry).toHaveTextContent(/^report\.pdf, pages 1 to 3 of 3 · about 77 tokens$/);
  });

  it("are headed Sources, with a file's glyph, and no search to count", () => {
    show(message({ sources: [source()] }));
    const sources = screen.getByTestId("sources");
    const heading = sources.querySelector("h3")!;
    expect(heading).toHaveClass("eyebrow");
    expect(heading.textContent).toBe("Sources");
    expect(heading.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(sources).toHaveClass("max-w-measure", "border-t");
  });

  it("say the lines that were sent, and mark a first part", () => {
    show(message({ sources: [partSource()] }));
    expect(screen.getByTestId("document-source")).toHaveTextContent(
      /^log\.txt, lines 1 to 290 of 900 · first part · about 2,997 tokens$/,
    );
  });

  it("say a file went whole when there is no page or line to name", () => {
    show(
      message({
        sources: [
          source({
            name: "notes.docx",
            type: "docx",
            page_start: null,
            page_end: null,
            pages_total: null,
          }),
        ],
      }),
    );
    expect(screen.getByTestId("document-source")).toHaveTextContent(
      /^notes\.docx, whole · about 77 tokens$/,
    );
  });

  it("say a file was sent as the file itself, and give no count of tokens for it", () => {
    show(message({ sources: [source({ native: true, tokens: null })] }));
    expect(screen.getByTestId("document-source")).toHaveTextContent(
      /^report\.pdf, sent as a file$/,
    );
  });

  it("say a file attached earlier in the chat, which is sent again with each message", () => {
    show(message({ sources: [source({ replayed: true })] }));
    expect(screen.getByTestId("document-source")).toHaveTextContent(
      /^report\.pdf, pages 1 to 3 of 3 · attached earlier · about 77 tokens$/,
    );
  });

  it("list every file that was sent, in the order they were sent", () => {
    show(message({ sources: [source({ replayed: true }), partSource()] }));
    const entries = screen.getAllByTestId("document-source");
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent("report.pdf");
    expect(entries[1]).toHaveTextContent("log.txt");
  });

  it("never say the model used them", () => {
    show(message({ sources: [source(), partSource()] }));
    // Each piece of text on its own, so one line's end is not run into the next one's start.
    const spoken = [...screen.getByTestId("sources").querySelectorAll("*")]
      .flatMap((el) => [...el.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE))
      .map((node) => node.textContent)
      .join(" | ");
    expect(spoken).toContain("Files sent to the model");
    expect(spoken).not.toMatch(/\b(used|uses|cited|cites|based on|according to|drew|relied)\b/i);
  });

  it("keep a long name inside the line", () => {
    const long = "a-very-long-report-name-that-would-otherwise-stretch-the-row-and-the-page.pdf";
    show(message({ sources: [source({ name: long })] }));
    expect(screen.getByTestId("document-sources").querySelector("ul")).toHaveClass("wrap-anywhere");
  });

  it("are shown as soon as they are known, while the answer is still being written", () => {
    show(message({ status: "running", content: "", finishedAt: null, sources: [source()] }));
    expect(screen.getByTestId("document-sources")).toBeInTheDocument();
  });
});

describe("the files under an answer, beside a search", () => {
  it("are listed apart from the pages the search found, which stay links", () => {
    show(message({ searches: 1, sources: [web, source()] }));
    const sources = screen.getByTestId("sources");
    expect(sources.querySelector("h3")!.textContent).toBe("Sources (1 search)");
    expect(screen.getByRole("link", { name: "A page" })).toHaveAttribute(
      "href",
      "https://example.org/a",
    );
    expect(sources.querySelector("ol")).toHaveTextContent("A page");
    expect(sources.querySelector("ol")).not.toHaveTextContent("report.pdf");
    // Only the page is a numbered link: a file is not one of the pages.
    expect(sources.querySelectorAll("ol > li")).toHaveLength(1);
    expect(sources.querySelectorAll("a")).toHaveLength(1);
    expect(
      within(screen.getByTestId("document-sources")).getByTestId("document-source"),
    ).toHaveTextContent("report.pdf");
    expect(screen.getByTestId("source-site")).toHaveTextContent("example.org");
  });

  it("still say a search found nothing the answer linked, when files were sent", () => {
    show(message({ searches: 1, sources: [source()] }));
    expect(screen.getByTestId("sources")).toHaveTextContent("does not link to any page");
    expect(screen.getByTestId("document-sources")).toBeInTheDocument();
  });

  it("say nothing of files when none was sent", () => {
    show(message({ searches: 1, sources: [web] }));
    expect(screen.queryByTestId("document-sources")).toBeNull();
    expect(screen.getByTestId("sources").querySelector("h3")!.textContent).toBe(
      "Sources (1 search)",
    );
  });
});

describe("while an answer waits for its files to be read", () => {
  const waiting = { stage: "documents", phase: "reading", files: 2 } as const;

  it("says so, in the working line, and not that it waits for the model", () => {
    show(message({ status: "running", content: "", finishedAt: null }), waiting);
    const line = screen.getByTestId("progress");
    expect(line).toHaveTextContent("Reading your files…");
    expect(line).not.toHaveTextContent("model");
    expect(line).toHaveAttribute("aria-live", "polite");
  });

  it("goes back to the model's own words once the model has begun", () => {
    show(message({ status: "running", content: "", reasoning: "Let me see.", finishedAt: null }), {
      stage: "generating",
    });
    expect(screen.getByTestId("progress")).toHaveTextContent(/^Thinking/);
  });
});

describe("the files a person's message carried", () => {
  const document: AttachedFile = {
    id: "f1",
    name: "report.pdf",
    mediaType: "application/pdf",
    reading: reading(),
  };
  const person = (files: AttachedFile[]) =>
    message({ role: "user", content: "What is the code word?", sources: [], files });

  it("show a document's name, what Workbench found in it, and a way to see the text", () => {
    show(person([document]));
    const found = screen.getByTestId("document-file");
    expect(found).toHaveTextContent("report.pdf");
    expect(within(found).getByTestId("document-file-meta")).toHaveTextContent(
      "PDF · 3 pages · about 77 tokens",
    );
    expect(within(found).getByTestId("document-text-toggle")).toHaveTextContent(
      "Show what Workbench read",
    );
    expect(screen.getByTitle("report.pdf")).toBeInTheDocument();
  });

  it("show the text Workbench read, in a box that scrolls, and close it", async () => {
    vi.mocked(fileText).mockResolvedValue("[page 3]\nThe code word is TAMARIND-6042.");
    show(person([document]));
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    const text = await screen.findByTestId("document-text");
    expect(fileText).toHaveBeenCalledWith("f1");
    expect(text).toHaveTextContent("TAMARIND-6042");
    expect(text).toHaveAccessibleName("Text Workbench read from report.pdf");
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    expect(screen.queryByTestId("document-text")).toBeNull();
  });

  it("show the text to someone reading another's chat, who may read what was attached", () => {
    show(person([document]), null, true);
    expect(screen.getByTestId("document-text-toggle")).toBeInTheDocument();
  });

  it("offer no text for a file that was skipped or could not be read", () => {
    show(
      person([
        { ...document, reading: reading({ state: "skipped", tokens: null, message: "Skipped." }) },
      ]),
    );
    expect(screen.getByTestId("document-file")).toBeInTheDocument();
    expect(screen.queryByTestId("document-text-toggle")).toBeNull();
  });

  it("keep an image as it was, beside the documents", async () => {
    vi.mocked(fileUrl).mockResolvedValue("blob:picture");
    show(person([{ id: "i", name: "photo.png", mediaType: "image/png" }, document]));
    expect(await screen.findByTestId("attachment-image")).toHaveAttribute("href", "blob:picture");
    expect(screen.getAllByTestId("document-file")).toHaveLength(1);
  });

  it("keep a file that was not read as the name it always was", () => {
    show(person([{ id: "p", name: "old.pdf", mediaType: "application/pdf" }]));
    expect(screen.queryByTestId("document-file")).toBeNull();
    expect(screen.getByTitle("old.pdf").className).toMatch(/max-w-/);
  });

  it("keep a long name on one line", () => {
    const long = "a-very-long-report-name-that-would-otherwise-stretch-the-row-and-the-page.pdf";
    show(person([{ ...document, name: long }]));
    expect(screen.getByText(long).className).toMatch(/truncate/);
  });
});

describe("a chat exported as text", () => {
  const detail = (sources: Source[]) => ({
    chat: {
      id: "c",
      title: "Annual report",
      model: "m",
      search: false,
      settings: {},
      createdAt: 1,
      updatedAt: 1,
      running: false,
      readOnly: false,
    },
    ownerName: null,
    messages: [message({ sources })],
  });

  it("lists a file as what was sent to the model, with its pages, and a page as a link", () => {
    const text = chatMarkdown(detail([web, source(), partSource({ replayed: true })]));
    expect(text).toContain("### Sources");
    expect(text).toContain("A page: https://example.org/a");
    expect(text).toContain("Sent to the model: report.pdf, pages 1 to 3 of 3");
    expect(text).toContain(
      "Sent to the model: log.txt, lines 1 to 290 of 900 (first part, attached earlier)",
    );
  });
});
