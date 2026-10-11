import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { fileText } from "../lib/api";
import type { Facts } from "../lib/documents";
import type { ChatFile } from "../lib/types";
import type { Problem } from "../lib/useChatFiles";
import {
  choose,
  damaged,
  everything,
  file,
  fresh,
  MODEL,
  native,
  noRoom,
  partial,
  scan,
  tooLong,
} from "../test-documents";
import { Attachments } from "./Attachments";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fileText: vi.fn(),
}));

const FACTS: Facts = {
  model: MODEL,
  request: { used: 4218, window: 32768, reserve: 1024, full: null },
};

function show(
  files: ChatFile[],
  over: {
    facts?: Facts | null;
    problems?: Record<string, Problem>;
    choosing?: string | null;
    locked?: boolean;
    removing?: string | null;
  } = {},
) {
  const onRemove = vi.fn();
  const onChoose = vi.fn().mockResolvedValue(undefined);
  const view = render(
    <Attachments
      files={files}
      facts={over.facts === undefined ? FACTS : over.facts}
      problems={over.problems ?? {}}
      choosing={over.choosing ?? null}
      locked={over.locked ?? false}
      removing={over.removing ?? null}
      onRemove={onRemove}
      onChoose={onChoose}
    />,
  );
  return { ...view, onRemove, onChoose };
}

const chip = (name?: string) => {
  const chips = screen.getAllByTestId("document-chip");
  return name ? chips.find((c) => within(c).queryByText(name))! : chips[0]!;
};

beforeEach(() => vi.mocked(fileText).mockReset());

it("draws nothing when nothing is attached", () => {
  const { container } = show([]);
  expect(container).toBeEmptyDOMElement();
});

describe("an image or a sound", () => {
  const picture: ChatFile = { id: "i", name: "shot.png", mediaType: "image/png", kind: "image" };

  it("is a small chip with its name, as it always was, and Remove", () => {
    const { onRemove } = show([picture]);
    const list = screen.getByLabelText("Attached");
    expect(list).toHaveAttribute("data-testid", "attached");
    const pill = list.firstElementChild!;
    expect(pill).toHaveClass("max-w-full", "min-w-0", "wrap-anywhere");
    expect(pill).toHaveTextContent("shot.png");
    expect(screen.queryByTestId("document-chip")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove shot.png" }));
    expect(onRemove).toHaveBeenCalledWith(picture);
    expect(screen.getByRole("button", { name: "Remove shot.png" })).toHaveAttribute(
      "data-testid",
      "attachment-remove",
    );
  });

  it("is held while a message is sent or another file is taken back", () => {
    show([picture], { locked: true });
    expect(screen.getByRole("button", { name: "Remove shot.png" })).toBeDisabled();
    document.body.innerHTML = "";
    show([picture], { removing: "other" });
    expect(screen.getByRole("button", { name: "Remove shot.png" })).toBeDisabled();
  });

  it("says nothing of reading, the window or the request when there is no document", () => {
    show([picture]);
    expect(screen.queryByTestId("files-request")).toBeNull();
    expect(screen.queryByTestId("files-window-unknown")).toBeNull();
  });
});

describe("a document", () => {
  it("is a card with its name, what was found in it, and Remove", () => {
    const { onRemove } = show([file()]);
    const card = chip();
    expect(within(card).getByTestId("document-name")).toHaveTextContent("report.pdf");
    expect(within(card).getByTestId("document-meta")).toHaveTextContent(
      "PDF · 3 pages · about 77 tokens",
    );
    expect(card).toHaveClass("w-full");
    fireEvent.click(within(card).getByRole("button", { name: "Remove report.pdf" }));
    expect(onRemove).toHaveBeenCalledWith(file());
  });

  it("is held, and so is its Remove, while a choice is kept or a message is sent", () => {
    show([choose()], { choosing: "f2" });
    expect(screen.getByRole("button", { name: "Remove log.txt" })).toBeDisabled();
    expect(screen.getByTestId("document-offer-all")).toBeDisabled();
    expect(screen.getByTestId("document-offer-first")).toBeDisabled();
    expect(chip()).toHaveAttribute("aria-busy", "true");
    document.body.innerHTML = "";
    show([choose()], { locked: true });
    expect(screen.getByTestId("document-offer-all")).toBeDisabled();
  });

  it("keeps a long name inside the card", () => {
    const long = "a-very-long-report-name-that-would-otherwise-stretch-the-row-and-the-page.pdf";
    show([file({ name: long })]);
    expect(screen.getByTestId("document-name")).toHaveClass("wrap-anywhere");
    expect(chip()).toHaveClass("min-w-0");
  });
});

describe("a document being read", () => {
  it("says so, with a glyph that is only a glyph, and offers nothing to choose", () => {
    show([fresh()]);
    expect(chip()).toHaveAttribute("data-state", "reading");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveTextContent("Reading…");
    expect(status.querySelector(".working-dots")).toHaveAttribute("aria-hidden", "true");
    expect(within(chip()).getByTestId("document-meta")).toHaveTextContent(/^PDF$/);
    expect(screen.queryByTestId("document-text-toggle")).toBeNull();
    expect(screen.queryByTestId("document-offer-all")).toBeNull();
  });

  it("says how long it has been read once that is worth saying, and does not say it to a screen reader", () => {
    vi.useFakeTimers();
    try {
      show([fresh()]);
      const status = screen.getByTestId("document-status");
      act(() => void vi.advanceTimersByTime(2000));
      expect(status).not.toHaveTextContent("2 s");
      act(() => void vi.advanceTimersByTime(7000));
      expect(status).toHaveTextContent(/Reading….*9 s/);
      const clock = [...status.querySelectorAll("span")].find((s) =>
        s.textContent?.includes("9 s"),
      )!;
      expect(clock).toHaveAttribute("aria-hidden", "true");
    } finally {
      vi.useRealTimers();
    }
  });

  it("says what the server said when its text is being read again", () => {
    show([
      file({
        decision: {
          ...fresh().decision!,
          message:
            "Workbench's copy of this file's text is missing, so it is reading the file again.",
        },
      }),
    ]);
    expect(screen.getByTestId("document-status")).toHaveTextContent("it is reading the file again");
  });
});

describe("a document that will be read", () => {
  it("whole: a tick and the words", () => {
    show([file()]);
    expect(chip()).toHaveAttribute("data-state", "whole");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveTextContent("Will be read whole.");
    expect(status.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(status.closest(".notice")).toBeNull();
    expect(screen.queryByTestId("document-choice")).toBeNull();
    expect(screen.queryByTestId("document-choose-again")).toBeNull();
  });

  it("whole, because the person chose to read it all: says so, and lets them choose again", () => {
    const { onChoose } = show([everything()]);
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Will be read whole, as you chose.",
    );
    fireEvent.click(screen.getByTestId("document-choose-again"));
    expect(onChoose).toHaveBeenCalledWith(everything(), null);
  });

  it("a part: which part, how many tokens, that the rest is not sent, and the choice in effect", () => {
    const { onChoose } = show([partial()]);
    expect(chip()).toHaveAttribute("data-state", "partial");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Will read lines 1 to 290 of 900 (about 2,997 tokens). The rest of the file is not sent.",
    );
    expect(screen.getByTestId("document-choice")).toHaveTextContent(
      "Your choice: the first 3,000 tokens.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose again" }));
    expect(onChoose).toHaveBeenCalledExactlyOnceWith(partial(), null);
  });

  it("sent as the file: the model that reads PDFs is named", () => {
    show([native()]);
    expect(chip()).toHaveAttribute("data-state", "native");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "local-model reads PDFs itself, so Workbench sends the file as it is.",
    );
  });

  it("lists what the reader noted, quietly", () => {
    show([
      file({
        decision: { ...file().decision!, notes: ["Page 2 has no text (it may be a picture)."] },
      }),
    ]);
    const notes = screen.getByTestId("document-notes");
    expect(notes).toHaveTextContent("Page 2 has no text (it may be a picture).");
    expect(notes).toHaveClass("text-muted");
  });
});

describe("a document that needs the person", () => {
  it("longer than the default: a warning, the limit, and both offers", () => {
    const { onChoose } = show([choose()]);
    expect(chip()).toHaveAttribute("data-state", "choose");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveClass("notice", "notice--warn");
    expect(status).toHaveTextContent("Choose how much to read");
    expect(status).toHaveTextContent(
      "longer than Workbench reads at once by default (6,000 tokens)",
    );
    expect(status.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    const all = screen.getByTestId("document-offer-all");
    const first = screen.getByTestId("document-offer-first");
    expect(all).toHaveTextContent("Read it all (about 9,300 tokens)");
    expect(first).toHaveTextContent("Read the first 5,859 tokens (lines 1 to 567 of 900)");
    fireEvent.click(first);
    expect(onChoose).toHaveBeenLastCalledWith(choose(), { mode: "first", tokens: 5859 });
    fireEvent.click(all);
    expect(onChoose).toHaveBeenLastCalledWith(choose(), { mode: "whole" });
  });

  it("too long for the window: the numbers, and only the part that fits is offered", () => {
    const { onChoose } = show([tooLong()]);
    expect(chip()).toHaveAttribute("data-state", "too_long");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveTextContent("Too long for this model");
    expect(status).toHaveTextContent(
      "This model's window is 4,096 tokens, so there is room for about 2,147 tokens of this file.",
    );
    expect(screen.queryByTestId("document-offer-all")).toBeNull();
    const first = screen.getByTestId("document-offer-first");
    expect(first).toHaveTextContent("Read the first 1,614 tokens (pages 1 to 2 of 30)");
    fireEvent.click(first);
    expect(onChoose).toHaveBeenCalledWith(tooLong(), { mode: "first", tokens: 1614 });
  });

  it("too long, and no part fits: nothing is offered but to go", () => {
    show([tooLong({ decision: { ...tooLong().decision!, offers: [] } })]);
    expect(screen.queryByTestId("document-offer-first")).toBeNull();
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Pick a model with a larger window, or remove the file.",
    );
    expect(screen.getByRole("button", { name: "Remove long.pdf" })).toBeEnabled();
  });

  it("no room: the server's sentence", () => {
    show([noRoom()]);
    expect(chip()).toHaveAttribute("data-state", "no_room");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveClass("notice--warn");
    expect(status).toHaveTextContent("No room for this file");
    expect(status).toHaveTextContent(
      "This chat already fills about 6,060 of this model's 4,096 tokens",
    );
  });

  it("skipped: the server's own words, a warning, and what to do", () => {
    show([scan()]);
    expect(chip()).toHaveAttribute("data-state", "skipped");
    const status = screen.getByTestId("document-status");
    expect(status).toHaveClass("notice--warn");
    expect(status).toHaveTextContent(
      "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.",
    );
    expect(status).toHaveTextContent("Remove it to send your message.");
    expect(screen.queryByTestId("document-text-toggle")).toBeNull();
  });

  it("failed: the server's own words, in the colour of a failure", () => {
    show([damaged()]);
    expect(chip()).toHaveAttribute("data-state", "failed");
    expect(screen.getByTestId("document-status")).toHaveClass("notice--error");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Failed: could not read this file",
    );
  });

  it("says that an earlier choice no longer fits, and offers the choice again", () => {
    show([
      choose({
        decision: {
          ...choose().decision!,
          choiceNoLongerFits: true,
          chosen: { mode: "first", tokens: 8000 },
        },
      }),
    ]);
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Your earlier choice (the first 8,000 tokens) no longer fits this model.",
    );
    expect(screen.queryByTestId("document-choose-again")).toBeNull();
    expect(screen.getByTestId("document-offer-first")).toBeInTheDocument();
  });

  it("says why a message or a choice was refused, as an alert on its own chip", () => {
    show([choose(), file()], {
      problems: { f2: { title: "Your message was not sent", text: "About 9,300 tokens." } },
    });
    const problem = screen.getByTestId("document-problem");
    expect(problem).toHaveAttribute("role", "alert");
    expect(problem).toHaveClass("notice", "notice--error");
    expect(problem).toHaveTextContent("Your message was not sent");
    expect(problem).toHaveTextContent("About 9,300 tokens.");
    expect(chip("log.txt")).toContainElement(problem);
    expect(within(chip("report.pdf")).queryByTestId("document-problem")).toBeNull();
  });

  it("keeps the keyboard on the card when a choice is made, since what was pressed is gone", async () => {
    let finish!: () => void;
    const { onChoose } = show([choose()]);
    onChoose.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const offer = screen.getByTestId("document-offer-first");
    offer.focus();
    fireEvent.click(offer);
    // The button is held while the choice is kept, and the browser lets go of it.
    offer.blur();
    await act(async () => finish());
    await waitFor(() => expect(chip()).toHaveFocus());
  });

  it("does not take the keyboard from someone who went elsewhere", async () => {
    let finish!: () => void;
    const { onChoose } = show([choose()]);
    onChoose.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const offer = screen.getByTestId("document-offer-all");
    offer.focus();
    fireEvent.click(offer);
    const elsewhere = screen.getByRole("button", { name: "Remove log.txt" });
    elsewhere.focus();
    await act(async () => finish());
    expect(elsewhere).toHaveFocus();
  });
});

describe("what Workbench read", () => {
  it("is offered for a file that was read, and shows its text in a box that scrolls", async () => {
    vi.mocked(fileText).mockResolvedValue(
      "[page 1]\nAnnual report.\n\n[page 3]\nThe code word is TAMARIND-6042.",
    );
    show([file()]);
    const toggle = screen.getByTestId("document-text-toggle");
    expect(toggle).toHaveTextContent("Show what Workbench read");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAccessibleDescription("report.pdf");
    fireEvent.click(toggle);
    const text = await screen.findByTestId("document-text");
    expect(fileText).toHaveBeenCalledWith("f1");
    expect(text).toHaveTextContent("[page 3]");
    expect(text).toHaveTextContent("TAMARIND-6042");
    expect(text).toHaveAccessibleName("Text Workbench read from report.pdf");
    expect(text).toHaveClass("reasoning-body");
    expect(toggle).toHaveTextContent("Hide what Workbench read");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.getAttribute("aria-controls")).toBe(text.parentElement!.id);
    fireEvent.click(toggle);
    expect(screen.queryByTestId("document-text")).toBeNull();
    expect(toggle).toHaveTextContent("Show what Workbench read");
  });

  it("is offered for a file that needs a choice, because its text is read all the same", () => {
    show([choose()]);
    expect(screen.getByTestId("document-text-toggle")).toBeInTheDocument();
  });

  it("says so while it opens, and why when it cannot", async () => {
    let open!: (text: string) => void;
    vi.mocked(fileText).mockReturnValueOnce(new Promise((resolve) => (open = resolve)));
    show([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    expect(screen.getByRole("status")).toHaveTextContent("Opening the text of report.pdf…");
    await act(async () => open("text"));
    expect(screen.queryByRole("status")).toBeNull();
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    vi.mocked(fileText).mockRejectedValueOnce(
      new Error("The text read from this file is no longer on disk."),
    );
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    const failure = await screen.findByTestId("document-text-error");
    expect(failure).toHaveAttribute("role", "alert");
    expect(failure).toHaveTextContent("The text read from this file is no longer on disk.");
    expect(screen.queryByTestId("document-text")).toBeNull();
  });

  it("shows no text it had read when it is opened again, until the new text arrives", async () => {
    vi.mocked(fileText).mockResolvedValueOnce("the first reading");
    show([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await screen.findByText("the first reading");
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    vi.mocked(fileText).mockReturnValueOnce(new Promise(() => undefined));
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    expect(screen.getByRole("status")).toHaveTextContent("Opening the text of report.pdf…");
    expect(screen.queryByTestId("document-text")).toBeNull();
    expect(screen.queryByText("the first reading")).toBeNull();
  });

  it("shows no text that arrived after the box was closed, when it is opened again", async () => {
    let late!: (text: string) => void;
    vi.mocked(fileText).mockReturnValueOnce(new Promise((resolve) => (late = resolve)));
    show([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await act(async () => late("an answer for a box that was closed"));
    vi.mocked(fileText).mockReturnValueOnce(new Promise(() => undefined));
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    expect(screen.queryByTestId("document-text")).toBeNull();
    expect(screen.queryByText(/an answer for a box that was closed/)).toBeNull();
  });

  it("does not show an answer that arrives after the box was closed", async () => {
    let late!: (text: string) => void;
    vi.mocked(fileText).mockReturnValueOnce(new Promise((resolve) => (late = resolve)));
    show([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await act(async () => late("too late"));
    expect(screen.queryByTestId("document-text")).toBeNull();
    expect(screen.queryByText(/too late/)).toBeNull();
  });
});

describe("many files", () => {
  it("scroll inside a box that leaves the window its message box and Send, with what is said under them", () => {
    show([file(), choose(), scan(), tooLong()]);
    const box = screen.getByTestId("attachments");
    expect(box).toHaveClass("overflow-y-auto", "overscroll-contain");
    // What the window has left after 32 rem, and at least 8 rem of files to look at.
    expect(box.className).toContain("max-h-[max(8rem,calc(100dvh-32rem))]");
    expect(box).toContainElement(screen.getByLabelText("Attached"));
    expect(box).toContainElement(screen.getByTestId("files-request"));
    expect(screen.getAllByTestId("document-chip")).toHaveLength(4);
  });
});

describe("what is said under the files", () => {
  it("is how much of the window the message comes to", () => {
    show([file()]);
    expect(screen.getByTestId("files-request")).toHaveTextContent(
      "With your files, this chat comes to about 4,218 of this model's 32,768 tokens, and 1,024 are kept for the answer.",
    );
  });

  it("is that the whole does not fit, in place of the line above", () => {
    show([file()], {
      facts: {
        model: MODEL,
        request: {
          used: 40000,
          window: 32768,
          reserve: 1024,
          full: "This chat and its documents come to about 40,000 tokens.",
        },
      },
    });
    expect(screen.getByTestId("files-full")).toHaveClass("notice--warn");
    expect(screen.getByTestId("files-full")).toHaveTextContent("come to about 40,000 tokens");
    expect(screen.queryByTestId("files-request")).toBeNull();
  });

  it("is that Eugene does not say how large the window is", () => {
    show([file()], {
      facts: { model: { ...MODEL, known: false, window: null }, request: null },
    });
    expect(screen.getByTestId("files-window-unknown")).toHaveTextContent(
      "Eugene does not say how large this model's window is, so Workbench cannot check that your files fit.",
    );
  });

  it("is nothing before the page has asked", () => {
    show([fresh()], { facts: null });
    expect(screen.queryByTestId("files-request")).toBeNull();
    expect(screen.queryByTestId("files-full")).toBeNull();
    expect(screen.queryByTestId("files-window-unknown")).toBeNull();
  });
});
