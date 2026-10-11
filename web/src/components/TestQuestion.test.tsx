/**
 * Test a question (MR4): the passages Workbench would send for a question, which of
 * the person's words each was found by, and that no model was asked.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, post } from "../lib/api";
import type { TestedQuestion } from "../lib/types";
import { hit, tested } from "../test-saved";
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
          why: { via: "words", terms: ["conclusion"], phrases: [], inHeading: ["conclusion"] },
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
      passages: [
        hit({ why: { via: "words", terms: ["code"], phrases: ["code word"], inHeading: [] } }),
      ],
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
