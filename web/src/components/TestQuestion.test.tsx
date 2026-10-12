/**
 * Test a question (MR4): the passages Workbench would send for a question, which of
 * the person's words each was found by, and that no model was asked.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, post } from "../lib/api";
import type { TestedPassage, TestedQuestion } from "../lib/types";
import {
  CODES,
  hit,
  meaningRun,
  noSearchModel,
  ranByMeaning,
  searchFor,
  SORT_RUN_CODES,
  sortedRun,
  sortRunFor,
  SORTER,
  tested,
  why,
} from "../test-saved";
import { TestQuestion } from "./TestQuestion";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  post: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(post).mockReset().mockResolvedValue(tested());
});

const settle = () => act(async () => undefined);
const question = () => screen.getByTestId("documents-test-question") as HTMLInputElement;
const ask = () => screen.getByTestId("documents-test-ask");

/** Types a question and presses the button, and lets the answer come. */
async function tries(words: string, answer?: TestedQuestion | Error) {
  if (answer instanceof Error) vi.mocked(post).mockRejectedValue(answer);
  else if (answer) vi.mocked(post).mockResolvedValue(answer);
  fireEvent.change(question(), { target: { value: words } });
  fireEvent.click(ask());
  await settle();
}

const hits = () => screen.queryAllByTestId("documents-test-hit");

describe("the box", () => {
  it("is a heading, what it does in two lines, and a question with a button", () => {
    render(<TestQuestion />);
    expect(screen.getByRole("heading", { level: 2, name: "Test a question" })).toBeInTheDocument();
    expect(screen.getByTestId("documents-test")).toHaveTextContent(
      "No model is asked, and nothing leaves your machines.",
    );
    expect(question()).toHaveAccessibleName("Your question");
    expect(question()).toHaveAttribute("maxlength", "2000");
    expect(ask()).toHaveTextContent("Show the passages");
    expect(screen.queryByTestId("documents-test-result")).toBeNull();
  });

  it("will not ask an empty question, or one of spaces", () => {
    render(<TestQuestion />);
    expect(ask()).toBeDisabled();
    fireEvent.change(question(), { target: { value: "   " } });
    expect(ask()).toBeDisabled();
    fireEvent.change(question(), { target: { value: "code" } });
    expect(ask()).toBeEnabled();
  });

  it("asks the server, without a chat or a model, with the question as it was trimmed", async () => {
    render(<TestQuestion />);
    await tries("  What is the code word?  ");
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/documents/test", {
      question: "What is the code word?",
    });
  });

  it("asks on Enter too", async () => {
    render(<TestQuestion />);
    fireEvent.change(question(), { target: { value: "code word" } });
    fireEvent.submit(question().closest("form")!);
    await settle();
    expect(post).toHaveBeenCalledOnce();
  });

  it("is held while it asks, so it is not asked twice", async () => {
    render(<TestQuestion />);
    let answer: (found: TestedQuestion) => void = () => undefined;
    vi.mocked(post).mockReturnValue(
      new Promise<TestedQuestion>((resolve) => (answer = resolve)) as never,
    );
    fireEvent.change(question(), { target: { value: "code" } });
    fireEvent.click(ask());
    expect(ask()).toBeDisabled();
    expect(question()).toBeDisabled();
    fireEvent.click(ask());
    // Pressing Enter in a form that is held is not another question either.
    fireEvent.submit(question().closest("form")!);
    expect(post).toHaveBeenCalledOnce();
    await act(async () => answer(tested()));
    expect(ask()).toBeEnabled();
  });
});

describe("what a question found", () => {
  it("says how many passages would be sent, and that no model was asked, and echoes the question", async () => {
    render(<TestQuestion />);
    await tries("What is the code word for the lighthouse inspection?");
    const result = screen.getByTestId("documents-test-result");
    expect(result).toHaveAttribute("data-status", "found");
    const words = screen.getByTestId("documents-test-words");
    expect(words).toHaveAttribute("role", "status");
    expect(words).toHaveTextContent("What is the code word for the lighthouse inspection?");
    expect(words).toHaveTextContent(
      "Workbench would send 2 passages with this question, about 350 tokens. No model was asked.",
    );
  });

  it("lists each passage by its file, its page or heading, and its number", async () => {
    render(<TestQuestion />);
    await tries("code word");
    expect(hits().map((h) => within(h).getByTestId("documents-test-title").textContent)).toEqual([
      "harbour-survey.pdf, page 4",
      "quarterly-report.docx, Conclusion",
    ]);
    expect(hits()[0]).toHaveTextContent("[1]");
    expect(hits()[1]).toHaveTextContent("[2]");
  });

  it("shows the start of each passage with the words it was found by marked, and names them", async () => {
    render(<TestQuestion />);
    await tries("code word");
    const first = hits()[0]!;
    const marks = [
      ...within(first).getByTestId("documents-test-snippet").querySelectorAll("mark"),
    ].map((m) => m.textContent);
    expect(marks).toEqual(["code", "word", "lighthouse", "inspection"]);
    expect(within(first).getByTestId("documents-test-matched")).toHaveTextContent(
      "Matched: code, word, lighthouse, inspection",
    );
    // Nothing is said of the heading where no word was found in it.
    expect(within(hits()[1]!).getByTestId("documents-test-matched")).toHaveTextContent(
      /^Matched: code, word$/,
    );
  });

  it("says a passage is from a file of a chat when it is, and says nothing of it when it is not", async () => {
    render(<TestQuestion />);
    await tries("code word", { ...tested(), passages: [hit({ scope: "chat" }), hit({ rank: 2 })] });
    expect(hits()[0]).toHaveTextContent("a file of a chat");
    expect(hits()[1]).not.toHaveTextContent("a file of a chat");
  });

  it("says a word that was found in a passage's heading", async () => {
    render(<TestQuestion />);
    await tries("conclusion", {
      ...tested(),
      passages: [
        hit({
          heading: "Conclusion",
          why: why({ terms: ["conclusion"], inHeading: ["conclusion"] }),
        }),
      ],
    });
    expect(screen.getByTestId("documents-test-matched")).toHaveTextContent(
      "Matched: conclusion (in the heading: conclusion)",
    );
  });

  it("quotes a phrase it was found by", async () => {
    render(<TestQuestion />);
    await tries('"code word"', {
      ...tested(),
      passages: [hit({ why: why({ terms: ["code"], phrases: ["code word"] }) })],
    });
    expect(screen.getByTestId("documents-test-matched")).toHaveTextContent(
      "Matched: code, “code word”",
    );
    // The phrase is marked whole in the passage, where it stands.
    const marks = [...screen.getByTestId("documents-test-snippet").querySelectorAll("mark")];
    expect(marks.map((m) => m.textContent)).toEqual(["code word"]);
  });

  it("opens the whole passage in a quiet box, and closes it again", async () => {
    render(<TestQuestion />);
    await tries("code word");
    const first = hits()[0]!;
    const details = first.querySelector("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    const toggle = within(first).getByTestId("documents-test-whole");
    expect(toggle).toHaveTextContent("Show the whole passage");
    fireEvent.click(toggle);
    expect(details.open).toBe(true);
    const text = within(first).getByTestId("documents-test-text");
    expect(text).toHaveTextContent("Page 4 of the harbour survey records ordinary observations.");
    expect(text).toHaveAccessibleName("The whole passage 1");
    fireEvent.click(toggle);
    expect(details.open).toBe(false);
  });

  it("says what was searched for, how much, and what did not fit", async () => {
    render(<TestQuestion />);
    await tries("code word", { ...tested(), didNotFit: 2 });
    const facts = screen.getByTestId("documents-test-facts");
    expect(facts).toHaveTextContent("Searched for: code, word, lighthouse, inspection");
    expect(facts).toHaveTextContent(
      "2 more passages matched but did not fit the room this chat had.",
    );
    expect(facts).toHaveTextContent("Searched 3 documents (9 passages).");
  });
});

describe("a question that found nothing", () => {
  it("is a normal result, said so, with the words that were searched for", async () => {
    render(<TestQuestion />);
    await tries("zebra giraffe", {
      ...tested(),
      status: "nothing_matched",
      passages: [],
      found: 0,
      query: { text: "zebra giraffe", terms: ["zebra", "giraffe"], phrases: [] },
    });
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute(
      "data-status",
      "nothing_matched",
    );
    expect(screen.getByTestId("documents-test-words")).toHaveTextContent(
      "Nothing in your documents matched, so Workbench would send no passages. No model was asked.",
    );
    expect(screen.getByTestId("documents-test-facts")).toHaveTextContent(
      "Searched for: zebra, giraffe",
    );
    expect(hits()).toHaveLength(0);
    // It is not an error, and does not look like one.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("documents-test-words")).not.toHaveClass("notice--error");
  });

  it("says a question with no words in it has none to search for", async () => {
    render(<TestQuestion />);
    await tries("?!", {
      ...tested(),
      status: "no_words",
      passages: [],
      query: { text: "", terms: [], phrases: [] },
    });
    expect(screen.getByTestId("documents-test-words")).toHaveTextContent(
      "There are no words in that question to search for.",
    );
    expect(screen.getByTestId("documents-test-facts")).not.toHaveTextContent("Searched for");
    // Nothing is listed for words there were none of: only how much was searched.
    expect(screen.getByTestId("documents-test-facts").querySelectorAll("li")).toHaveLength(1);
  });

  it("says when nothing can be searched yet", async () => {
    render(<TestQuestion />);
    await tries("code", {
      ...tested(),
      status: "no_documents",
      passages: [],
      searched: { documents: 0, passages: 0, waiting: 2 },
    });
    expect(screen.getByTestId("documents-test-words")).toHaveTextContent(
      "None of your documents can be searched yet: 2 documents are still being read.",
    );
    expect(screen.getByTestId("documents-test-facts")).toHaveTextContent(
      "2 documents are still being read, so not searched yet.",
    );
  });
});

describe("a question that could not be tried", () => {
  it("says why in the server's own words, and is never an empty list", async () => {
    render(<TestQuestion />);
    await tries(
      "code",
      new ApiError("The search index for your documents is damaged.", 503, {
        message: "The search index for your documents is damaged.",
        code: "index_damaged",
        canRebuild: true,
      }),
    );
    const problem = screen.getByTestId("documents-test-problem");
    expect(problem).toHaveAttribute("role", "alert");
    expect(problem).toHaveTextContent("The search index for your documents is damaged.");
    expect(screen.queryByTestId("documents-test-result")).toBeNull();
    expect(question()).toHaveAttribute("aria-invalid", "true");
    expect(question()).toHaveAccessibleDescription(
      "The search index for your documents is damaged.",
    );
  });

  it("keeps the last result off the screen when the next try fails, and clears the words when the next works", async () => {
    render(<TestQuestion />);
    await tries("code word");
    expect(hits()).toHaveLength(2);
    vi.mocked(post).mockRejectedValue(new ApiError("Down.", 500, { message: "Down." }));
    fireEvent.click(ask());
    await settle();
    expect(screen.getByTestId("documents-test-problem")).toHaveTextContent("Down.");
    // What was found for the last question is not left beside the failure of this one.
    expect(screen.queryByTestId("documents-test-result")).toBeNull();
    expect(hits()).toHaveLength(0);
    vi.mocked(post).mockResolvedValue(tested());
    fireEvent.click(ask());
    await settle();
    expect(screen.queryByTestId("documents-test-problem")).toBeNull();
    expect(screen.getByTestId("documents-test-result")).toBeInTheDocument();
    expect(question()).not.toHaveAttribute("aria-invalid");
  });
});

// --- search by meaning (MR5) ---

const dialog = () => screen.getByTestId("documents-test-ways");
const way = (which: string) =>
  screen.getByTestId(`documents-test-way-${which}`) as HTMLInputElement;

/** A passage that only meaning found: none of the question's words are in it. */
const byMeaning = (over: Partial<TestedPassage> = {}) =>
  hit({
    rank: 1,
    docId: "d7",
    name: "harbour-office.md",
    pageStart: null,
    pageEnd: null,
    heading: "Arrivals",
    snippet: "The harbour master keeps the vessel registry and the logbook of every ship.",
    text: "The harbour master keeps the vessel registry and the logbook of every ship.",
    via: "meaning",
    why: why({ via: "meaning", ranks: { meaning: 1 }, similarity: 0.62 }),
    ...over,
  });

describe("how each passage was found", () => {
  it("says words for a passage that has the words, with the places of that way", async () => {
    render(<TestQuestion />);
    await tries("code word");
    const first = hits()[0]!;
    expect(within(first).getByTestId("documents-test-via")).toHaveTextContent(/^Found by words$/);
    expect(within(first).getByTestId("documents-test-via")).toHaveAttribute("data-via", "words");
    expect(within(first).getByTestId("documents-test-places")).toHaveTextContent(
      "Words put it 1st.",
    );
  });

  it("says meaning for a passage with none of the words, with no words matched and none marked, and how near it was", async () => {
    render(<TestQuestion />);
    await tries("who records the boats arriving?", {
      ...tested({ mode: "meaning and words", meaning: ranByMeaning() }),
      passages: [byMeaning()],
    });
    const only = hits()[0]!;
    expect(within(only).getByTestId("documents-test-via")).toHaveTextContent(
      "Found by meaning: none of your words are in it",
    );
    expect(within(only).queryByTestId("documents-test-matched")).toBeNull();
    expect(only.querySelectorAll("mark")).toHaveLength(0);
    expect(within(only).getByTestId("documents-test-places")).toHaveTextContent(
      "Meaning put it 1st (closeness 0.62).",
    );
  });

  it("says both, with the words it has and where each way put it", async () => {
    render(<TestQuestion />);
    await tries("code word", {
      ...tested({ mode: "meaning and words", meaning: ranByMeaning() }),
      passages: [
        hit({
          via: "both",
          why: why({
            via: "both",
            terms: ["code", "word"],
            ranks: { words: 3, meaning: 1 },
            similarity: 0.71,
          }),
        }),
      ],
    });
    const both = hits()[0]!;
    expect(within(both).getByTestId("documents-test-via")).toHaveTextContent(
      /^Found by both words and meaning$/,
    );
    expect(within(both).getByTestId("documents-test-matched")).toHaveTextContent(
      "Matched: code, word",
    );
    expect(within(both).getByTestId("documents-test-places")).toHaveTextContent(
      "Words put it 3rd, meaning put it 1st (closeness 0.71).",
    );
  });

  it("lists a passage found by words and one found by meaning side by side, each said as it was found", async () => {
    render(<TestQuestion />);
    await tries("code word boats", {
      ...tested({ mode: "meaning and words", meaning: ranByMeaning() }),
      passages: [hit(), byMeaning({ rank: 2 })],
    });
    expect(
      hits().map((h) => within(h).getByTestId("documents-test-via").getAttribute("data-via")),
    ).toEqual(["words", "meaning"]);
  });

  it("says a passage that meaning found and that has some of the words, with those words", async () => {
    render(<TestQuestion />);
    await tries("code", {
      ...tested({ mode: "meaning and words", meaning: ranByMeaning() }),
      passages: [
        byMeaning({ why: why({ via: "meaning", terms: ["code"], ranks: { meaning: 2 } }) }),
      ],
    });
    expect(screen.getByTestId("documents-test-via")).toHaveTextContent(/^Found by meaning$/);
    expect(screen.getByTestId("documents-test-matched")).toHaveTextContent("Matched: code");
  });
});

describe("trying one way at a time", () => {
  it("is an expert option, closed to begin with, and not said to be in use", () => {
    render(<TestQuestion />);
    const ways = dialog() as HTMLDetailsElement;
    expect(ways.open).toBe(false);
    expect(screen.getByTestId("documents-test-ways-toggle")).toHaveTextContent(
      "Try one way at a time",
    );
    expect(way("both")).toBeChecked();
  });

  it("offers both, words only and meaning only, and says what each is for", () => {
    render(<TestQuestion />);
    fireEvent.click(screen.getByTestId("documents-test-ways-toggle"));
    expect(way("both")).toHaveAccessibleName("Words and meaning, as a chat does");
    expect(way("words")).toHaveAccessibleName("Words only");
    expect(way("meaning")).toHaveAccessibleName("Meaning only");
    const group = screen.getByRole("group", { name: "Search by" });
    expect(group).toHaveAccessibleDescription(/sets aside a passage that is not near enough/);
    expect(group).toHaveAccessibleDescription(/here, nothing is set aside/);
  });

  it("asks the server for the one way that was chosen, by its name", async () => {
    render(<TestQuestion />);
    fireEvent.click(way("words"));
    await tries("code word");
    expect(post).toHaveBeenLastCalledWith("/api/documents/test", {
      question: "code word",
      ways: ["words"],
    });
    fireEvent.click(way("meaning"));
    fireEvent.click(ask());
    await settle();
    expect(post).toHaveBeenLastCalledWith("/api/documents/test", {
      question: "code word",
      ways: ["meaning"],
    });
  });

  it("asks both ways, naming none, when it is put back, so a chat's own rule applies", async () => {
    render(<TestQuestion />);
    fireEvent.click(way("words"));
    fireEvent.click(way("both"));
    await tries("code word");
    expect(post).toHaveBeenLastCalledWith("/api/documents/test", { question: "code word" });
  });

  it("says in its name which way is chosen when it is closed, so a choice is not hidden", () => {
    render(<TestQuestion />);
    fireEvent.click(way("meaning"));
    expect(screen.getByTestId("documents-test-ways-toggle")).toHaveTextContent(
      "Try one way at a time: meaning only",
    );
  });

  it("is held while a question is asked", async () => {
    render(<TestQuestion />);
    let answer: (found: TestedQuestion) => void = () => undefined;
    vi.mocked(post).mockReturnValue(
      new Promise<TestedQuestion>((resolve) => (answer = resolve)) as never,
    );
    fireEvent.change(question(), { target: { value: "code" } });
    fireEvent.click(ask());
    expect(way("words")).toBeDisabled();
    await act(async () => answer(tested()));
    expect(way("words")).toBeEnabled();
  });

  it("says a question was searched the way it was chosen", async () => {
    render(<TestQuestion />);
    fireEvent.click(way("words"));
    await tries("code word", tested({ mode: "words" }));
    expect(screen.getByTestId("documents-test-mode")).toHaveTextContent(
      "Searched by words only. You chose this.",
    );
  });

  it.each(CODES)("is offered for %s unless no search model has been set up", (code) => {
    render(<TestQuestion search={searchFor(code)} />);
    const none = code === "none_set_up" || code === "no_gateway";
    expect(screen.queryByTestId("documents-test-ways") === null).toBe(none);
  });

  it("is not asked for where it is not offered, whatever was chosen before", async () => {
    const view = render(<TestQuestion search={searchFor("ok")} />);
    fireEvent.click(way("meaning"));
    view.rerender(<TestQuestion search={noSearchModel()} />);
    await tries("code word");
    expect(post).toHaveBeenLastCalledWith("/api/documents/test", { question: "code word" });
  });
});

describe("how a tried question was searched", () => {
  it.each([
    ["meaning and words", "Searched by meaning and words."],
    ["words", "Searched by words only."],
    ["meaning", "Searched by meaning only."],
  ] as const)("says %s", async (mode, said) => {
    render(<TestQuestion />);
    await tries("code word", tested({ mode }));
    expect(screen.getByTestId("documents-test-mode")).toHaveTextContent(said);
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute("data-mode", mode);
    expect(screen.getByTestId("documents-test-mode")).not.toHaveTextContent("You chose");
  });

  it("says why meaning did not run, in the server's words, when a search model was in play", async () => {
    render(<TestQuestion />);
    await tries(
      "code word",
      tested({
        mode: "words",
        meaning: meaningRun({
          code: "not_running",
          message:
            "Searching by words only: the search model m is not running. Start it in Eugene's console under Backends.",
          quiet: false,
        }),
      }),
    );
    expect(screen.getByTestId("documents-test-meaning")).toHaveTextContent(
      "Searching by words only: the search model m is not running. Start it in Eugene's console under Backends.",
    );
  });

  it("does not say it when a search model was never in play", async () => {
    render(<TestQuestion />);
    await tries(
      "code word",
      tested({
        meaning: meaningRun({
          code: "none_set_up",
          message: "Searching by words only: no search model is set up.",
          quiet: true,
        }),
      }),
    );
    expect(screen.queryByTestId("documents-test-meaning")).toBeNull();
  });

  it("says how near the nearest passage was by meaning, and what counts when no word matched", async () => {
    render(<TestQuestion />);
    await tries(
      "zebra",
      tested({
        status: "nothing_matched",
        passages: [],
        mode: "meaning and words",
        meaning: ranByMeaning({ best: 0.27, belowFloor: true }),
      }),
    );
    expect(screen.getByTestId("documents-test-closeness")).toHaveTextContent(
      "By meaning, the nearest passage scored 0.27. When no word matches, a passage needs 0.50 to count, so none was kept.",
    );
    expect(screen.getByTestId("documents-test-words")).toHaveTextContent(
      "Nothing in your documents matched",
    );
  });

  it("says a search that none of its ways could run, and that nothing was searched, once", async () => {
    render(<TestQuestion />);
    fireEvent.click(way("meaning"));
    await tries(
      "code word",
      tested({
        status: "unavailable",
        mode: "none",
        passages: [],
        meaning: meaningRun({
          code: "none_set_up",
          message: "Searching by words only: no search model is set up.",
          quiet: true,
        }),
      }),
    );
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute(
      "data-status",
      "unavailable",
    );
    expect(screen.getByTestId("documents-test-words")).toHaveTextContent(
      "Nothing was searched: search by meaning could not run. No search model is set up.",
    );
    expect(screen.getByTestId("documents-test-words")).not.toHaveTextContent("matched");
    // Not said twice, and no mode for a search that was none.
    expect(screen.queryByTestId("documents-test-meaning")).toBeNull();
    expect(screen.queryByTestId("documents-test-mode")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says how it was searched only for a question that was searched: not one with no words or no documents", async () => {
    render(<TestQuestion />);
    await tries("?!", {
      ...tested({ mode: "meaning and words" }),
      status: "no_words",
      passages: [],
    });
    expect(screen.queryByTestId("documents-test-mode")).toBeNull();
    await tries("code", {
      ...tested({ mode: "meaning and words" }),
      status: "no_documents",
      passages: [],
    });
    expect(screen.queryByTestId("documents-test-mode")).toBeNull();
  });
});

// --- the result sorter (MR6b) ---

describe("how a tried question was sorted", () => {
  /** Three passages: one the sorter put first, one second, and one it was not given. */
  const passages = () => [
    hit({
      rank: 1,
      via: "both",
      why: why({
        via: "both",
        terms: ["code", "word"],
        ranks: { words: 3, meaning: 1, sorter: 1 },
        similarity: 0.71,
      }),
    }),
    hit({
      rank: 2,
      docId: "d4",
      name: "quarterly-report.docx",
      pageStart: null,
      pageEnd: null,
      heading: "Conclusion",
      snippet: "The code word is PLUMBAGO-77.",
      text: "The code word is PLUMBAGO-77.",
      why: why({ terms: ["code", "word"], ranks: { words: 1, sorter: 2 } }),
    }),
    hit({
      rank: 3,
      docId: "d9",
      name: "later-notes.md",
      pageStart: null,
      pageEnd: null,
      snippet: "A word about the lighthouse.",
      text: "A word about the lighthouse.",
      why: why({ terms: ["word"], ranks: { words: 14 } }),
    }),
  ];
  const sorted = (over: Partial<TestedQuestion> = {}) =>
    tested({
      mode: `meaning and words, sorted by ${SORTER}` as never,
      meaning: ranByMeaning(),
      sort: sortedRun(),
      passages: passages(),
      ...over,
    });
  const places = () =>
    hits().map((h) => within(h).getByTestId("documents-test-places").textContent);

  it("says where the sorter put each passage, beside where words and meaning put it", async () => {
    render(<TestQuestion />);
    await tries("code word", sorted());
    expect(places()).toEqual([
      "Words put it 3rd, meaning put it 1st (closeness 0.71), the sorter put it 1st.",
      "Words put it 1st, the sorter put it 2nd.",
      "Words put it 14th. The sorter was not given it.",
    ]);
  });

  it("says how it was searched, with the sorter, and keeps the mode where a check can read it", async () => {
    render(<TestQuestion />);
    await tries("code word", sorted());
    expect(screen.getByTestId("documents-test-mode")).toHaveTextContent(
      `Searched by meaning and words, sorted by ${SORTER}.`,
    );
    expect(screen.getByTestId("documents-test-mode")).not.toHaveTextContent("You chose");
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute(
      "data-mode",
      `meaning and words, sorted by ${SORTER}`,
    );
  });

  it("says how many passages it put in order and how long that took, beside Sort time", async () => {
    render(<TestQuestion />);
    await tries("code word", sorted());
    expect(screen.getByTestId("documents-test-sorted")).toHaveTextContent(
      "The sorter put 18 passages in order in 1.4 s (Sort time is 3 s).",
    );
    expect(screen.queryByTestId("documents-test-sort")).toBeNull();
  });

  it("says a question was sorted the way it was chosen, when one way was named", async () => {
    render(<TestQuestion />);
    fireEvent.click(way("words"));
    await tries("code word", sorted({ mode: `words, sorted by ${SORTER}` as never }));
    expect(screen.getByTestId("documents-test-mode")).toHaveTextContent(
      `Searched by words only, sorted by ${SORTER}. You chose this.`,
    );
  });

  it("puts the sorter's line after how it was searched", async () => {
    render(<TestQuestion />);
    await tries("code word", sorted());
    const mode = screen.getByTestId("documents-test-mode");
    const sort = screen.getByTestId("documents-test-sorted");
    expect(mode.compareDocumentPosition(sort) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  describe("passages that were not sorted", () => {
    const slow = (over: Partial<TestedQuestion> = {}) =>
      tested({
        mode: "meaning and words",
        meaning: ranByMeaning(),
        sort: sortRunFor("over_budget"),
        passages: passages().map((p) => ({
          ...p,
          why: { ...p.why, ranks: { ...p.why.ranks, sorter: undefined } },
        })),
        ...over,
      });

    it("says why, in the server's words, with the numbers of these passages", async () => {
      render(<TestQuestion />);
      await tries("code word", slow());
      const reason = screen.getByTestId("documents-test-sort");
      expect(reason).toHaveTextContent(
        `Not sorted: ${SORTER} sorts about 820 tokens a second here, so sorting these 18 passages (5,760 tokens) would take about 7 s, and Sort time is 3 s. Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway.`,
      );
      expect(reason).toHaveAttribute("data-code", "over_budget");
      expect(screen.queryByTestId("documents-test-sorted")).toBeNull();
    });

    it("does not say they were sorted, and does not say a passage was not given to a sorter", async () => {
      render(<TestQuestion />);
      await tries("code word", slow());
      expect(screen.getByTestId("documents-test-mode")).toHaveTextContent(
        /^Searched by meaning and words\.$/,
      );
      expect(places()).toEqual([
        "Words put it 3rd, meaning put it 1st (closeness 0.71).",
        "Words put it 1st.",
        "Words put it 14th.",
      ]);
    });

    it.each(SORT_RUN_CODES)(
      "%s: says what the server says, unless the sorter was never in play",
      async (code) => {
        render(<TestQuestion />);
        await tries("code word", tested({ mode: "meaning and words", sort: sortRunFor(code) }));
        const run = sortRunFor(code);
        if (run.quiet) {
          expect(screen.queryByTestId("documents-test-sort")).toBeNull();
        } else {
          expect(screen.getByTestId("documents-test-sort")).toHaveTextContent(run.message ?? "x");
          expect(screen.getByTestId("documents-test-sort")).toHaveAttribute("data-code", code);
        }
        expect(screen.queryByTestId("documents-test-sorted")).toBeNull();
      },
    );
  });

  it("says nothing of a sorter that was never in play, and the question is as it was", async () => {
    render(<TestQuestion />);
    await tries("code word");
    expect(screen.queryByTestId("documents-test-sort")).toBeNull();
    expect(screen.queryByTestId("documents-test-sorted")).toBeNull();
    expect(places()).toEqual(["Words put it 1st.", "Words put it 2nd."]);
  });

  it("is not stopped by an answer from a server before sorting, which has no word of one", async () => {
    render(<TestQuestion />);
    await tries("code word", tested({ sort: undefined }));
    expect(screen.getByTestId("documents-test-result")).toBeInTheDocument();
    expect(screen.queryByTestId("documents-test-sort")).toBeNull();
    expect(screen.queryByTestId("documents-test-sorted")).toBeNull();
  });

  it("says nothing of sorting for a question none of whose ways could run", async () => {
    render(<TestQuestion />);
    await tries(
      "code word",
      tested({
        status: "unavailable",
        mode: "none",
        passages: [],
        sort: sortRunFor("over_budget"),
      }),
    );
    expect(screen.getByTestId("documents-test-result")).toHaveAttribute(
      "data-status",
      "unavailable",
    );
    expect(screen.queryByTestId("documents-test-sort")).toBeNull();
  });
});
