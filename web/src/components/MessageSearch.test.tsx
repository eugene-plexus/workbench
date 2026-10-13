/**
 * What a message shows of the person's saved documents (MR4): the passages that were
 * sent with the question, listed as the file, the page and the heading each came from
 * with the words they matched and a mark on the ones the answer cited (what was SENT,
 * never what the model used); nothing matched, said quietly; a search that was not run
 * or that failed, in its own words; Workbench's own reply for a chat that answers only
 * from the documents; and what the owner sees, which is "a document".
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { post } from "../lib/api";
import { chatMarkdown } from "../lib/conveniences";
import type { ChatDetail, Message, Progress, SearchCode, Source } from "../lib/types";
import { workingWords } from "../lib/words";
import { source } from "../test-documents";
import {
  CODES,
  hidden,
  meaningRun,
  nothing,
  passage,
  ranByMeaning,
  record,
  searchFor,
  second,
} from "../test-saved";
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
  content: "The code word is TAMARIND-6042 [1].",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "local-model",
  finish: "stop",
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

function show(
  shown: Message,
  over: {
    progress?: Progress | null;
    readOnly?: boolean;
    onChanged?: () => void;
    onManage?: () => void;
    busy?: boolean;
  } = {},
) {
  const onChanged = over.onChanged ?? vi.fn();
  const onManage = over.onManage ?? vi.fn();
  const view = render(
    <MessageView
      chatId="c"
      message={shown}
      progress={over.progress ?? null}
      busy={over.busy ?? false}
      readOnly={over.readOnly ?? false}
      onChanged={onChanged}
      documents={over.readOnly ? undefined : { onManage }}
    />,
  );
  return { ...view, onChanged, onManage };
}

const used = (over: Partial<Message> = {}) =>
  message({ sources: [passage(), second()], retrieval: record(), ...over });

const web: Source = { kind: "web", url: "https://example.org/a", title: "A page" };

beforeEach(() => {
  vi.mocked(post).mockReset();
});

describe("the passages that were sent", () => {
  it("are listed under Sources by the file, the page and the heading each came from, and how many", () => {
    show(used());
    const sources = screen.getByTestId("sources");
    expect(within(sources).getByTestId("passage-sent")).toHaveTextContent(
      "Sent to the model: 2 passages from 2 documents",
    );
    const rows = within(sources).getAllByTestId("passage-source");
    expect(rows.map((r) => within(r).getByTestId("passage-title").textContent)).toEqual([
      "harbour-survey.pdf, page 4",
      "quarterly-report.docx, Conclusion",
    ]);
    expect(rows.map((r) => within(r).getByTestId("passage-number").textContent)).toEqual([
      "[1]",
      "[2]",
    ]);
    expect(sources.querySelector("h3")!.textContent).toBe("Sources");
  });

  it("says pages and lines the way the file has them", () => {
    show(
      message({
        sources: [
          passage({ page_start: 4, page_end: 6, heading: "Tides > Spring" }),
          passage({
            rank: 2,
            page_start: null,
            page_end: null,
            line_start: 10,
            line_end: 40,
            name: "log.txt",
          }),
        ],
        retrieval: record({ passages: 2, documents: 2 }),
      }),
    );
    const titles = screen.getAllByTestId("passage-title").map((t) => t.textContent);
    expect(titles).toEqual([
      "harbour-survey.pdf, pages 4 to 6, Tides > Spring",
      "log.txt, lines 10 to 40",
    ]);
  });

  it("count the documents the passages came from, not the passages again", () => {
    show(
      message({
        sources: [passage(), passage({ rank: 2, page_start: 5, page_end: 5 })],
        retrieval: record({ passages: 2, documents: 1 }),
      }),
    );
    expect(screen.getByTestId("passage-sent")).toHaveTextContent(
      "Sent to the model: 2 passages from 1 document",
    );
  });

  it("count one passage and one document in the singular", () => {
    show(message({ sources: [passage()], retrieval: record({ passages: 1, documents: 1 }) }));
    expect(screen.getByTestId("passage-sent")).toHaveTextContent(
      "Sent to the model: 1 passage from 1 document",
    );
  });

  it("mark the passage the answer cited, with its number, and no other", () => {
    show(used());
    const [first, other] = screen.getAllByTestId("passage-source");
    expect(first).toHaveAttribute("data-cited", "true");
    expect(within(first!).getByTestId("passage-cited")).toHaveTextContent("Cited");
    expect(within(first!).getByTestId("passage-cited")).toHaveAttribute(
      "title",
      "The answer's text has [1] in it",
    );
    expect(other).toHaveAttribute("data-cited", "false");
    expect(within(other!).queryByTestId("passage-cited")).toBeNull();
  });

  it("are not marked as cited until the answer is done and says which", () => {
    show(
      used({
        status: "running",
        sources: [passage({ cited: undefined }), second({ cited: undefined })],
      }),
    );
    expect(screen.queryAllByTestId("passage-cited")).toHaveLength(0);
    expect(screen.getAllByTestId("passage-source")).toHaveLength(2);
  });

  it("show the start of each passage and the words it matched, behind the passage's own row", () => {
    show(used());
    const first = screen.getAllByTestId("passage-source")[0]!;
    const details = first.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(within(first).getByTestId("passage-toggle"));
    expect(details.open).toBe(true);
    const snippet = within(first).getByTestId("passage-snippet");
    expect(snippet).toHaveTextContent(
      "The code word for the lighthouse inspection is TAMARIND-6042.",
    );
    expect([...snippet.querySelectorAll("mark")].map((m) => m.textContent)).toEqual([
      "code",
      "word",
      "lighthouse",
      "inspection",
    ]);
    expect(within(first).getByTestId("passage-matched")).toHaveTextContent(
      "Matched: code, word, lighthouse, inspection",
    );
  });

  it("name the row that opens it, with the number, the file and the page, so a screen reader can tell them apart", () => {
    show(used());
    const toggles = screen.getAllByTestId("passage-toggle");
    expect(toggles[0]).toHaveAccessibleName(
      "[1] harbour-survey.pdf, page 4, found by words, cited in the answer",
    );
    expect(toggles[1]).toHaveAccessibleName(
      "[2] quarterly-report.docx, Conclusion, found by words",
    );
  });

  it("say a passage came from a file attached to this chat", () => {
    show(
      message({
        sources: [passage({ scope: "chat" })],
        retrieval: record({ passages: 1, documents: 1 }),
      }),
    );
    expect(screen.getByTestId("passage-scope")).toHaveTextContent("a file of this chat");
  });

  it("say what was searched for, how, and what did not fit", () => {
    show(
      used({
        retrieval: record({
          didNotFit: 2,
          notes: [
            {
              code: "still_reading",
              message: "1 of your documents is still being read, so not searched yet.",
            },
          ],
        }),
      }),
    );
    const facts = screen.getByTestId("passage-facts");
    expect(facts).toHaveTextContent("Searched for: code, word, lighthouse, inspection");
    expect(facts).toHaveTextContent("Found by words only.");
    expect(screen.getByTestId("passage-left-out")).toHaveTextContent(
      "2 more passages matched but did not fit the room this chat had.",
    );
    expect(facts).toHaveTextContent(
      "1 of your documents is still being read, so not searched yet.",
    );
  });

  it("never say the model used them", () => {
    show(used());
    expect(screen.getByTestId("sources").textContent).not.toMatch(/\bused\b/i);
  });

  it("sit beside the web's sources and the files that were attached, each in its own part", () => {
    show(used({ sources: [web, source(), passage(), second()], searches: 1 }));
    const sources = screen.getByTestId("sources");
    expect(within(sources).getByRole("link", { name: "A page" })).toBeInTheDocument();
    expect(within(sources).getByTestId("document-sources")).toHaveTextContent(
      "Files sent to the model",
    );
    expect(within(sources).getByTestId("passage-sources")).toHaveTextContent(
      "Sent to the model: 2 passages",
    );
    // A passage is not one of the files: it is not listed as one.
    expect(
      within(within(sources).getByTestId("document-sources")).getAllByTestId("document-source"),
    ).toHaveLength(1);
  });

  it("are the words an answer's Sources is made of when there is nothing else to list", () => {
    show(used());
    const heading = screen.getByTestId("sources").querySelector("h3")!;
    expect(heading.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.queryByText(/The answer does not link/)).toBeNull();
  });
});

describe("what the owner sees of them", () => {
  const owners = () =>
    message({
      sources: [hidden(), hidden({ rank: 2 })],
      retrieval: record({ cited: [], searched: null, budget: null }),
    });

  it("is a document, for each passage, and nothing of which", () => {
    show(owners(), { readOnly: true });
    const rows = screen.getAllByTestId("redacted-passage");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("[1] a document");
    expect(rows[1]).toHaveTextContent("[2] a document");
    expect(screen.getByTestId("passages-redacted")).toHaveTextContent(
      "Sent to the model: 2 passages of the person's documents.",
    );
    expect(screen.getByTestId("passages-redacted")).toHaveTextContent(
      "A person's documents are private to them, even where you may read their chats.",
    );
    expect(screen.queryByTestId("passage-source")).toBeNull();
    expect(screen.queryByTestId("passage-snippet")).toBeNull();
  });

  it("has no name, no page and no words of the passage anywhere in it", () => {
    const { container } = show(owners(), { readOnly: true });
    // The answer is the model's own words, which the owner may read; the Sources are not.
    const sources = screen.getByTestId("sources");
    expect(sources.textContent).not.toMatch(
      /harbour|survey|TAMARIND|page \d|Conclusion|quarterly/i,
    );
    expect(container.querySelector("[data-testid=passage-toggle]")).toBeNull();
    expect(container.querySelector("details")).toBeNull();
  });

  it("says one passage in the singular", () => {
    show(
      message({
        sources: [hidden()],
        retrieval: record({ cited: [], searched: null, budget: null }),
      }),
      {
        readOnly: true,
      },
    );
    expect(screen.getByTestId("passages-redacted")).toHaveTextContent(
      "Sent to the model: 1 passage of the person's documents.",
    );
  });
});

describe("nothing matched", () => {
  it("is a normal result, said quietly, with the words that were searched for", () => {
    show(message({ retrieval: nothing(), model: "local-model" }));
    const quiet = screen.getByTestId("search-nothing");
    expect(quiet).toHaveTextContent("Nothing in your documents matched.");
    expect(quiet).toHaveTextContent("Searched for: zebra, giraffe, okapi");
    expect(screen.getByTestId("sources")).toContainElement(quiet);
    // It is not an error or a warning: no box, no alert.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(quiet).not.toHaveClass("notice");
    expect(screen.queryByTestId("search-failed")).toBeNull();
    expect(screen.queryByTestId("search-skipped")).toBeNull();
  });

  it("is said with the server's words, and a plain sentence if it sent none", () => {
    show(message({ retrieval: nothing({ message: null }) }));
    expect(screen.getByTestId("search-nothing")).toHaveTextContent(
      "Nothing in your documents matched.",
    );
  });
});

describe("a search that was not run", () => {
  const skipped = (over: Partial<ReturnType<typeof record>> = {}) =>
    message({
      retrieval: record({
        status: "skipped",
        code: "chat_full",
        message:
          "Documents were not used: this chat already fills about 7,900 of this model's 8,192 tokens, counting the room kept for the answer. Start a new chat or pick a model with a larger window.",
        passages: 0,
        documents: 0,
        cited: [],
        ...over,
      }),
    });

  it("is said in the server's words, in a notice, and never as nothing matched", () => {
    show(skipped());
    const notice = screen.getByTestId("search-skipped");
    expect(notice).toHaveClass("notice");
    expect(notice).not.toHaveClass("notice--warn");
    expect(notice).toHaveTextContent(
      "Documents were not used: this chat already fills about 7,900 of this model's 8,192 tokens",
    );
    expect(screen.queryByTestId("search-nothing")).toBeNull();
    expect(notice).not.toHaveAttribute("role", "alert");
    expect(notice.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("gives the server's hint for why documents were held back from a model outside the machines", () => {
    show(
      skipped({
        code: "outside_model",
        message:
          "Your documents were not sent: this chat uses OpenRouter, which runs outside your machines.",
        notes: [
          {
            code: "outside_model",
            message:
              "Your documents were not sent: this chat uses OpenRouter, which runs outside your machines.",
            hint: "Choose Send for this chat to send them anyway.",
          },
        ],
      }),
    );
    const notice = screen.getByTestId("search-skipped");
    expect(notice).toHaveTextContent("Your documents were not sent: this chat uses OpenRouter");
    expect(notice).toHaveTextContent("Choose Send for this chat to send them anyway.");
    expect(within(notice).queryByRole("button")).toBeNull();
  });

  it("leads a person whose chat answered with no documents to add some", () => {
    const { onManage } = show(
      skipped({ status: "no_documents", code: "no_documents", message: null }),
    );
    expect(screen.getByTestId("search-skipped")).toHaveTextContent(
      "You have no documents to search yet. Add some in Documents, then ask again.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Open Documents to add some" }));
    expect(onManage).toHaveBeenCalledOnce();
  });

  it("offers nobody else's chat a way into the screen", () => {
    show(skipped({ status: "no_documents", code: "no_documents", message: null }), {
      readOnly: true,
    });
    expect(screen.queryByTestId("search-fix")).toBeNull();
  });
});

describe("a search that failed", () => {
  const failed = (over: Partial<ReturnType<typeof record>> = {}) =>
    message({
      retrieval: record({
        status: "failed",
        code: "error",
        message:
          "Searching your documents failed (OperationalError), so this answer used none. See Workbench's log.",
        passages: 0,
        documents: 0,
        cited: [],
        ...over,
      }),
    });

  it("is said in its own words, as an alert, and never as nothing matched", () => {
    show(failed());
    const notice = screen.getByTestId("search-failed");
    expect(notice).toHaveAttribute("role", "alert");
    expect(notice).toHaveClass("notice", "notice--warn");
    expect(notice).toHaveTextContent(
      "Searching your documents failed (OperationalError), so this answer used none. See Workbench's log.",
    );
    expect(screen.queryByTestId("search-nothing")).toBeNull();
    expect(within(notice).queryByRole("button")).toBeNull();
  });

  it("offers to rebuild the search when that is what mends it, and opens the screen that does", () => {
    const { onManage } = show(
      failed({
        code: "index_damaged",
        message:
          "The search index for your documents is damaged (SQLite said: malformed). Your files are safe.",
      }),
    );
    expect(screen.getByTestId("search-failed")).toHaveTextContent("Your files are safe.");
    fireEvent.click(screen.getByRole("button", { name: "Open Documents to rebuild the search" }));
    expect(onManage).toHaveBeenCalledOnce();
  });

  it("offers nothing for what Rebuild cannot mend", () => {
    show(
      failed({
        code: "disk_full",
        message: "Workbench could not write to your documents' index: the disk is full.",
      }),
    );
    expect(within(screen.getByTestId("search-failed")).queryByRole("button")).toBeNull();
  });

  it("still shows the answer the model wrote without the passages", () => {
    show(failed());
    expect(screen.getByText(/The code word is TAMARIND-6042/)).toBeInTheDocument();
  });
});

describe("Workbench's own reply", () => {
  const reply = (over: Partial<Message> = {}) =>
    message({
      content: "I could not find that in your documents.",
      model: null,
      finish: "documents_only",
      retrieval: nothing({ documentsOnly: true, answeredBy: "workbench" }),
      ...over,
    });

  it("is shown plainly as Workbench speaking, not as a model's answer", () => {
    show(reply());
    const own = screen.getByTestId("workbench-reply");
    // It is signed, as Workbench's own, before what it says.
    expect(own.querySelector(".eyebrow")).toHaveTextContent(/^Workbench$/);
    expect(screen.getByTestId("workbench-reply-text")).toHaveTextContent(
      "I could not find that in your documents.",
    );
    expect(own).toHaveTextContent(
      "This chat answers only from your documents, so Workbench replied itself and did not ask the model.",
    );
    // Not an answer a model wrote: no markdown body, no model name.
    expect(document.querySelector(".answer")).toBeNull();
    expect(screen.queryByText("local-model")).toBeNull();
  });

  it("says nothing matched in Sources, quietly, once", () => {
    show(reply());
    expect(screen.getAllByTestId("search-nothing")).toHaveLength(1);
    expect(screen.queryByTestId("search-skipped")).toBeNull();
    expect(screen.queryByTestId("search-failed")).toBeNull();
  });

  it("does not say a search that failed twice: Workbench's reply has already said it", () => {
    show(
      reply({
        content:
          "The search index for your documents is damaged. This chat answers only from your documents, so it did not ask the model.",
        retrieval: record({
          status: "failed",
          code: "index_damaged",
          message: "The search index for your documents is damaged.",
          passages: 0,
          documentsOnly: true,
          answeredBy: "workbench",
        }),
      }),
    );
    expect(screen.queryByTestId("search-failed")).toBeNull();
    expect(screen.getByTestId("workbench-reply-text")).toHaveTextContent(
      "The search index for your documents is damaged.",
    );
  });

  it("can be asked of the model anyway: a new try that asks it, which the chat then shows", async () => {
    vi.mocked(post).mockResolvedValue({});
    const { onChanged } = show(reply());
    fireEvent.click(screen.getByRole("button", { name: "Ask the model anyway" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/chats/c/messages/a/retry", {
      askAnyway: true,
    });
  });

  it("is held while it asks, so it is not asked twice", async () => {
    let finish: (value: object) => void = () => undefined;
    vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    show(reply());
    const button = screen.getByRole("button", { name: "Ask the model anyway" });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(post).toHaveBeenCalledOnce();
    finish({});
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("says why it could not ask, in the server's words, and keeps the reply", async () => {
    vi.mocked(post).mockRejectedValue(new Error("The model is not running."));
    show(reply());
    fireEvent.click(screen.getByRole("button", { name: "Ask the model anyway" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The model is not running.");
    expect(screen.getByTestId("workbench-reply")).toBeInTheDocument();
  });

  it("is Workbench speaking for the person whose documents they are, to the owner reading the chat, who cannot ask", () => {
    show(reply(), { readOnly: true });
    expect(screen.getByTestId("workbench-reply")).toHaveTextContent(
      "This chat answers only from the person's documents, so Workbench replied itself and did not ask the model.",
    );
    expect(screen.queryByRole("button", { name: "Ask the model anyway" })).toBeNull();
  });

  it("does not look like one of the model's answers when the model's own words end in the same way", () => {
    show(message({ finish: "stop" }));
    expect(screen.queryByTestId("workbench-reply")).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask the model anyway" })).toBeNull();
  });
});

describe("while an answer waits for the search", () => {
  const running = (over: Partial<Message> = {}) =>
    message({ status: "running", content: "", finish: null, model: null, ...over });

  it("says Searching your documents in the working line, once, as the model's wait is not what it waits on", () => {
    show(running(), { progress: { stage: "documents", phase: "searching" } });
    const line = screen.getByTestId("progress");
    expect(line).toHaveTextContent("Searching your documents…");
    expect(line).toHaveAttribute("aria-live", "polite");
    expect(line.textContent).not.toMatch(/Waiting for the model/);
  });

  it("still says it reads files when that is what it is doing", () => {
    show(running(), { progress: { stage: "documents", phase: "reading", files: 2 } });
    expect(screen.getByTestId("progress")).toHaveTextContent("Reading your files…");
  });

  it("is a line that says one thing and has nothing that ticks", () => {
    expect(workingWords({ stage: "documents", phase: "searching" }, running(), 100)).toBe(
      "Searching your documents…",
    );
  });
});

describe("a chat exported", () => {
  const detail = (messages: Message[]): ChatDetail => ({
    chat: {
      id: "c",
      title: "Harbour",
      model: "local-model",
      search: false,
      settings: {},
      createdAt: 1,
      updatedAt: 1,
      running: false,
      readOnly: false,
    },
    messages,
    ownerName: null,
  });

  it("lists each passage that was sent: the file and the place, and whether the answer cited it", () => {
    const text = chatMarkdown(detail([used()]));
    expect(text).toContain("### Sources");
    expect(text).toContain(
      "Passage sent to the model: harbour-survey.pdf, page 4 (cited in the answer)",
    );
    expect(text).toContain("Passage sent to the model: quarterly-report.docx, Conclusion");
    expect(text).not.toContain("quarterly-report.docx, Conclusion (cited");
    expect(text).not.toContain("TAMARIND-6042.\n");
  });

  it("says a passage the owner may not read is a document", () => {
    const text = chatMarkdown(detail([message({ sources: [hidden()], retrieval: record() })]));
    expect(text).toContain("Passage sent to the model: a document");
  });

  it("lists the web's sources and the files beside the passages, each as it always was", () => {
    const text = chatMarkdown(detail([used({ sources: [web, source(), passage()] })]));
    expect(text).toContain("A page: https://example.org/a");
    expect(text).toContain("Sent to the model: report.pdf, pages 1 to 3 of 3");
    expect(text).toContain("Passage sent to the model: harbour-survey.pdf, page 4");
  });
});

// --- search by meaning (MR5) ---

/** A passage that only meaning found: none of the person's words are in it. */
const byMeaning = (over: Partial<ReturnType<typeof passage>> = {}) =>
  passage({
    doc_id: "d7",
    name: "harbour-office.md",
    type: "markdown",
    rank: 2,
    via: "meaning",
    page_start: null,
    page_end: null,
    heading: "Arrivals",
    snippet: "The harbour master keeps the vessel registry and the logbook of every ship.",
    chunk_ids: [41],
    terms: [],
    phrases: [],
    cited: false,
    ...over,
  });

describe("how each passage was found", () => {
  const found = () =>
    message({
      sources: [
        passage(),
        byMeaning(),
        passage({ rank: 3, via: "both", doc_id: "d4", name: "report.docx", cited: false }),
      ],
      retrieval: record({
        mode: "meaning and words",
        meaning: ranByMeaning(),
        passages: 3,
        documents: 3,
      }),
    });

  it("is said on each row, short: found by words, found by meaning, found by both", () => {
    show(found());
    const rows = screen.getAllByTestId("passage-source");
    expect(rows.map((r) => within(r).getByTestId("passage-via").textContent?.trim())).toEqual([
      "· found by words",
      "· found by meaning",
      "· found by both",
    ]);
    expect(rows.map((r) => r.getAttribute("data-via"))).toEqual(["words", "meaning", "both"]);
  });

  it("is in the row's name too, so a screen reader says it, with the number, the file and whether it was cited", () => {
    show(found());
    const toggles = screen.getAllByTestId("passage-toggle");
    expect(toggles[0]).toHaveAccessibleName(
      "[1] harbour-survey.pdf, page 4, found by words, cited in the answer",
    );
    expect(toggles[1]).toHaveAccessibleName("[2] harbour-office.md, Arrivals, found by meaning");
    expect(toggles[2]).toHaveAccessibleName("[3] report.docx, page 4, found by both");
  });

  it("says in full, once the row is open, that a passage found by meaning has none of the person's words", () => {
    show(found());
    const meaning = screen.getAllByTestId("passage-source")[1]!;
    fireEvent.click(within(meaning).getByTestId("passage-toggle"));
    expect(within(meaning).getByTestId("passage-found")).toHaveTextContent(
      "Found by meaning: none of your words are in it",
    );
    expect(within(meaning).queryByTestId("passage-matched")).toBeNull();
    expect(meaning.querySelectorAll("mark")).toHaveLength(0);
  });

  it("says both ways, with the words it has, for a passage found by both", () => {
    show(found());
    const both = screen.getAllByTestId("passage-source")[2]!;
    expect(within(both).getByTestId("passage-found")).toHaveTextContent(
      "Found by both words and meaning",
    );
    expect(within(both).getByTestId("passage-matched")).toHaveTextContent("Matched: code, word");
  });

  it("says nothing more of a passage found by words: its words are what it matched", () => {
    show(found());
    const words = screen.getAllByTestId("passage-source")[0]!;
    expect(within(words).queryByTestId("passage-found")).toBeNull();
    expect(within(words).getByTestId("passage-matched")).toBeInTheDocument();
  });

  it("says which way found it for a passage that meaning found and that has some of the words", () => {
    show(
      message({
        sources: [byMeaning({ rank: 1, terms: ["code"] })],
        retrieval: record({ mode: "meaning and words", meaning: ranByMeaning(), passages: 1 }),
      }),
    );
    fireEvent.click(screen.getByTestId("passage-toggle"));
    expect(screen.getByTestId("passage-found")).toHaveTextContent(/^Found by meaning$/);
    expect(screen.getByTestId("passage-matched")).toHaveTextContent("Matched: code");
  });

  it("says nothing the owner is not to read: a passage in someone else's chat is a document, and how it was found is not told", () => {
    show(
      message({
        sources: [hidden({ via: "meaning" })],
        retrieval: record({ mode: "meaning and words", passages: 1, searched: null, budget: null }),
      }),
      { readOnly: true },
    );
    expect(screen.queryByTestId("passage-via")).toBeNull();
    expect(screen.getByTestId("redacted-passage")).toHaveTextContent("a document");
  });
});

describe("how the passages were found, said once, with the search model", () => {
  it("says meaning and words, and which search model, when meaning ran", () => {
    show(
      message({
        sources: [passage(), byMeaning()],
        retrieval: record({ mode: "meaning and words", meaning: ranByMeaning() }),
      }),
    );
    expect(screen.getByTestId("passage-facts")).toHaveTextContent(
      "Found by meaning and words, with the search model Qwen3-Embedding-0.6B-Q8_0.",
    );
  });

  it("says words only, with no search model, when meaning did not run", () => {
    show(used({ retrieval: record({ mode: "words", meaning: meaningRun() }) }));
    const facts = screen.getByTestId("passage-facts");
    expect(facts).toHaveTextContent("Found by words only.");
    expect(facts).not.toHaveTextContent("search model");
  });
});

describe("a note about meaning, for each code the server says", () => {
  // What an answer keeps when meaning could not run for it: the server's own sentence, under the
  // code `meaning_<code>`, with the answer searched by words only.
  const said = (code: SearchCode) => {
    const facts = searchFor(code);
    // A paused search and an unreadable one say their headline; the others say their detail.
    return ["changed", "dimension", "damaged"].includes(code)
      ? facts.headline
      : (facts.detail as string);
  };
  const OFF = CODES.filter((code) => code !== "ok" && code !== "no_gateway");

  it.each([...OFF, "unusable"] as const)(
    "meaning_%s is shown in its own words, beside how it was found",
    (code) => {
      const note =
        code === "unusable"
          ? "Searching by words only: the search model m gave numbers Workbench cannot use (not a number)."
          : said(code);
      show(
        used({
          retrieval: record({
            mode: "words",
            meaning: meaningRun({ code, message: note, quiet: false }),
            notes: [
              { code: "still_reading", message: "1 of your documents is still being read." },
              { code: `meaning_${code}`, message: note },
            ],
          }),
        }),
      );
      const shown = screen.getAllByTestId("passage-meaning-note");
      // Only the note about meaning has its own mark: the other notes are as they were.
      expect(shown).toHaveLength(1);
      expect(shown[0]).toHaveTextContent(note);
      expect(shown[0]).toHaveAttribute("data-code", `meaning_${code}`);
      expect(screen.getByTestId("passage-facts")).toHaveTextContent("Found by words only.");
      expect(screen.getByTestId("passage-facts")).toHaveTextContent(
        "1 of your documents is still being read.",
      );
    },
  );

  it("is also said under a search that was not run for another reason, beside that reason", () => {
    show(
      message({
        retrieval: record({
          status: "skipped",
          code: "chat_full",
          message: "Documents were not used: this chat already fills about 7,900 tokens.",
          passages: 0,
          documents: 0,
          cited: [],
          notes: [
            {
              code: "meaning_not_running",
              message: said("not_running"),
            },
          ],
        }),
      }),
    );
    const notice = screen.getByTestId("search-skipped");
    expect(notice).toHaveTextContent("Documents were not used");
    expect(within(notice).getByTestId("search-meaning-note")).toHaveTextContent(
      "the search model Qwen3-Embedding-0.6B-Q8_0 is not running.",
    );
  });
});

describe("nothing matched, by words and meaning", () => {
  it("says how it was searched", () => {
    show(message({ retrieval: nothing({ mode: "meaning and words", meaning: ranByMeaning() }) }));
    expect(screen.getByTestId("search-nothing-mode")).toHaveTextContent(
      "Searched by meaning and words.",
    );
  });

  it("says words only for a search that was by words only, and nothing for one that was none", () => {
    const view = show(message({ retrieval: nothing({ mode: "words" }) }));
    expect(screen.getByTestId("search-nothing-mode")).toHaveTextContent("Searched by words only.");
    view.unmount();
    show(message({ retrieval: nothing({ mode: "none" }) }));
    expect(screen.queryByTestId("search-nothing-mode")).toBeNull();
  });

  it("says, in the server's words, that it was by words only because meaning could not run", () => {
    const why =
      "Searching by words only: the search model m is not running. Start it in Eugene's console under Backends.";
    show(
      message({
        retrieval: nothing({
          mode: "words",
          message: `Nothing in your documents matched. ${why}`,
          meaning: meaningRun({ code: "not_running", message: why, quiet: false }),
        }),
      }),
    );
    expect(screen.getByTestId("search-nothing")).toHaveTextContent(
      "Nothing in your documents matched. Searching by words only: the search model m is not running.",
    );
  });
});
