import {
  adding,
  addingView,
  broken,
  byMeaning,
  chatDocuments,
  held,
  hidden,
  limited,
  markdown,
  meaningOf,
  meaningRun,
  nothing,
  off,
  owners,
  page,
  passage,
  prefs,
  queued,
  ranByMeaning,
  reading,
  record,
  saved,
  scanned,
  second,
  tested,
  waiting,
  withMeaning,
  word,
  wordsOnly,
  wordsOnlyDoc,
} from "../test-saved";
import {
  addedWords,
  blockedBy,
  budgetWords,
  canRebuild,
  capsWords,
  chatChipWords,
  countsWords,
  docMeta,
  EXTERNAL_CHOICES,
  externalLabel,
  hideHint,
  hintCount,
  hintFor,
  hintHidden,
  hintWords,
  IDLE_POLL_MS,
  isBeingRead,
  isHidden,
  isPending,
  leftOutWords,
  marked,
  matchedWords,
  modeWords,
  newChatsWords,
  passageLine,
  passageTitle,
  placeWords,
  ranWords,
  readOnlyWords,
  readView,
  rebuiltWords,
  refusedBy,
  searchedCounts,
  searchedFor,
  sentWords,
  sizeWords,
  stateWords,
  testedTitle,
  testedWords,
  totalsWords,
  type Upload,
  uploadSummary,
  uploadWords,
  watching,
  WAITING_POLL_MS,
} from "./savedDocuments";

describe("where a document stands", () => {
  it("says each state in the design's words, with a tone that is not colour alone", () => {
    expect(stateWords(queued())).toEqual({
      state: "queued",
      tone: "quiet",
      label: "Waiting to be read",
    });
    expect(stateWords(reading())).toEqual({ state: "reading", tone: "busy", label: "Reading…" });
    expect(stateWords(saved({ state: "searchable" }))).toEqual({
      state: "searchable",
      tone: "ok",
      label: "Ready for words",
    });
    expect(stateWords(saved())).toEqual({ state: "ready", tone: "ok", label: "Ready" });
  });

  it("says how many pages a document that is being read has, once it is known, and never how far it has got", () => {
    expect(stateWords(reading({ pages: 40 })).label).toBe("Reading… 40 pages");
    expect(stateWords(reading({ pages: 1 })).label).toBe("Reading… 1 page");
    // The server does not say which page it is on, so the page does not.
    expect(stateWords(reading({ pages: 40 })).label).not.toMatch(/page \d+ of/);
  });

  it("is ready for words in a state it has no words for yet, rather than ready or nothing", () => {
    const later = saved({ state: "embedding" });
    expect(stateWords(later)).toEqual({ state: "embedding", tone: "ok", label: "Ready for words" });
  });

  it("gives the server's own sentence for a document that was skipped or failed, as it came", () => {
    expect(stateWords(scanned())).toEqual({
      state: "skipped",
      tone: "warn",
      label:
        "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.",
    });
    expect(stateWords(broken())).toEqual({
      state: "failed",
      tone: "error",
      label: "Failed: Workbench could not read this file (the PDF is cut off). It may be damaged.",
    });
  });

  it("puts the word in front of a sentence that came without it, so the line always says which it is", () => {
    expect(stateWords(scanned({ message: "this PDF has no text." })).label).toBe(
      "Skipped: this PDF has no text.",
    );
    expect(stateWords(broken({ message: "the file is cut off." })).label).toBe(
      "Failed: the file is cut off.",
    );
    // Said in any case, it is not said twice.
    expect(stateWords(broken({ message: "failed: the file is cut off." })).label).toBe(
      "failed: the file is cut off.",
    );
  });

  it("is never silent about a document that ended without a reason", () => {
    expect(stateWords(scanned({ message: null })).label).toBe(
      "Skipped: Workbench did not read this file.",
    );
    expect(stateWords(broken({ message: "  " })).label).toBe(
      "Failed: Workbench could not read this file.",
    );
  });

  it("is pending until it is ready, skipped or failed", () => {
    for (const state of ["queued", "reading", "searchable", "embedding"] as const) {
      expect(isPending(saved({ state })), state).toBe(true);
    }
    for (const state of ["ready", "skipped", "failed"] as const) {
      expect(isPending(saved({ state })), state).toBe(false);
    }
  });
});

describe("sizes and dates", () => {
  it("says a size as the server does", () => {
    expect(sizeWords(1023)).toBe("1 KiB");
    // The smallest a file is called is one KiB, as the server says it, never 0.
    expect(sizeWords(100)).toBe("1 KiB");
    expect(sizeWords(0)).toBe("1 KiB");
    expect(sizeWords(819_200)).toBe("800 KiB");
    expect(sizeWords(1024 * 1024)).toBe("1 MiB");
    expect(sizeWords(4.5 * 1024 * 1024)).toBe("4.5 MiB");
    expect(sizeWords(10.4 * 1024 * 1024)).toBe("10 MiB");
    expect(sizeWords(312 * 1024 * 1024)).toBe("312 MiB");
  });

  it("says the day a document was added, with the year only when it is not this one", () => {
    const now = new Date(2026, 9, 11, 15, 0);
    const at = (y: number, m: number, d: number) => new Date(y, m, d, 9, 5).getTime() / 1000;
    expect(addedWords(at(2026, 9, 3), now)).toBe("Added Oct 3");
    expect(addedWords(at(2025, 11, 31), now)).toBe("Added Dec 31, 2025");
    expect(addedWords(null)).toBeNull();
  });
});

describe("what a document is", () => {
  it("is its kind, size, pages or lines, and passages once it can be found", () => {
    expect(docMeta(saved())).toBe("PDF · 9 KiB · 5 pages · 5 passages");
    expect(docMeta(markdown())).toBe("Markdown · 1 KiB · 5 lines · 1 passage");
    expect(docMeta(word())).toBe("Word · 36 KiB · 3 passages");
  });

  it("says a language only for code and text, and leaves passages out until there are some", () => {
    expect(
      docMeta(saved({ kind: "code", label: "code", language: "Python", pages: null, lines: 40 })),
    ).toBe("code (Python) · 9 KiB · 40 lines · 5 passages");
    expect(docMeta(saved({ language: "French" }))).not.toContain("French");
    expect(docMeta(queued({ size: 2048 }))).toBe("Markdown · 2 KiB");
    // Passages that cannot be found yet are not counted as ones that can.
    expect(docMeta(queued({ size: 2048, passages: 4 }))).toBe("Markdown · 2 KiB");
  });

  it("says the pages of a document that has them, and its lines only when it has no pages", () => {
    expect(docMeta(saved({ pages: 5, lines: 40 }))).toBe("PDF · 9 KiB · 5 pages · 5 passages");
    expect(docMeta(markdown({ pages: null, lines: 40 }))).toBe(
      "Markdown · 1 KiB · 40 lines · 1 passage",
    );
  });

  it("says one page and one line in the singular", () => {
    expect(docMeta(saved({ pages: 1, passages: 1 }))).toBe("PDF · 9 KiB · 1 page · 1 passage");
    expect(docMeta(markdown({ lines: 1 }))).toContain("1 line ·");
  });
});

describe("the totals and the caps", () => {
  const data = page([saved(), markdown(), word()]);

  it("count documents, passages and bytes", () => {
    expect(totalsWords(data.totals)).toBe("3 documents · 9 passages · 46 KiB");
    expect(totalsWords(page([saved({ passages: 1 })]).totals)).toBe(
      "1 document · 1 passage · 9 KiB",
    );
  });

  it("state every cap, with the numbers the server gave", () => {
    expect(capsWords(data)).toEqual([
      "A file may be up to 50 MiB.",
      "Workbench keeps up to 100,000 passages for you (9 used).",
      "It reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
      "It stops reading a file that takes longer than 120 seconds.",
    ]);
    const over = page([saved()], {
      limits: {
        ...data.limits,
        maxFileBytes: 200 * 1024 * 1024,
        maxPassages: 1000,
        readTimeoutSeconds: 1,
      },
    });
    expect(capsWords(over)[0]).toBe("A file may be up to 200 MiB.");
    expect(capsWords(over)[1]).toBe("Workbench keeps up to 1,000 passages for you (5 used).");
    expect(capsWords(over)[3]).toBe("It stops reading a file that takes longer than 1 second.");
  });
});

describe("the person's settings", () => {
  it("offers the three choices about models outside their machines, in the order ask, never, always", () => {
    expect(EXTERNAL_CHOICES.map((c) => c.value)).toEqual(["ask", "never", "always"]);
    expect(externalLabel("ask")).toBe("Ask me in each chat");
    expect(externalLabel("never")).toBe("Never send them");
    expect(externalLabel("always")).toBe("Always send them");
  });

  it("says what a new chat starts with, as it is in effect", () => {
    expect(newChatsWords(prefs())).toBe(
      "New chats start with Documents off. You can turn it on in any chat, with its Documents button.",
    );
    expect(newChatsWords(prefs({ newChats: true, effective: { newChats: true } }))).toBe(
      "New chats start with Documents on. You can turn it off in any chat, with its Documents button.",
    );
    // What the owner allows is what is in effect, whatever the person once chose.
    expect(newChatsWords(prefs({ newChats: true, effective: { newChats: false } }))).toContain(
      "off.",
    );
  });
});

describe("adding files", () => {
  const row = (state: Upload["state"], over: Partial<Upload> = {}): Upload => ({
    key: "u1",
    name: "a.pdf",
    size: 10,
    state,
    progress: 0,
    message: null,
    ...over,
  });

  it("says each row's words: waiting, how much has gone, added, and a refusal as the server said it", () => {
    expect(uploadWords(row("waiting"))).toBe("Waiting");
    expect(uploadWords(row("adding", { progress: 0.456 }))).toBe("Adding… 46%");
    expect(uploadWords(row("added"))).toBe("Added. Workbench is reading it.");
    expect(uploadWords(row("added", { message: "Already there." }))).toBe("Already there.");
    expect(uploadWords(row("refused", { message: "a.pdf is an image." }))).toBe(
      "a.pdf is an image.",
    );
    expect(uploadWords(row("failed", { message: "The connection was lost." }))).toBe(
      "The connection was lost.",
    );
    expect(uploadWords(row("refused"))).toBe("Not added.");
  });

  it("says where it is while files go, and what came of them after", () => {
    const rows = [row("added"), row("adding", { key: "u2" }), row("waiting", { key: "u3" })];
    expect(uploadSummary(rows)).toBe("Adding 2 of 3…");
    expect(uploadSummary([row("added"), row("added", { key: "u2" })])).toBe("Added 2 files.");
    expect(uploadSummary([row("added"), row("refused", { key: "u2" })])).toBe(
      "Added 1 file. 1 file was not added.",
    );
    expect(uploadSummary([row("refused"), row("failed", { key: "u2" })])).toBe(
      "2 files were not added.",
    );
  });

  it("reads a refusal off an ApiError: the file's own sentence, else the first, else what the error said", () => {
    const failure = (detail: unknown, status = 415, message = "refused") =>
      Object.assign(new Error(message), { status, detail });
    const both = failure({
      message: "first",
      refused: [
        { name: "b.png", status: 415, message: "b.png is an image." },
        { name: "a.pdf", status: 415, message: "a.pdf is damaged." },
      ],
    });
    expect(refusedBy(both, "a.pdf")).toEqual({ message: "a.pdf is damaged.", status: 415 });
    expect(refusedBy(both, "c.txt").message).toBe("b.png is an image.");
    expect(refusedBy(failure(undefined, 507, "The disk is full."), "a.pdf")).toEqual({
      message: "The disk is full.",
      status: 507,
    });
    expect(refusedBy("lost", "a.pdf")).toEqual({ message: "lost", status: 0 });
  });
});

describe("a search that cannot be used", () => {
  const fail = (status: number, detail?: unknown) =>
    Object.assign(new Error("x"), { status, detail });

  it("is only a 503, with the server's words, its code and whether it can be made again", () => {
    expect(
      blockedBy(
        fail(503, {
          message: "The search index for your documents is damaged.",
          code: "index_damaged",
          canRebuild: true,
        }),
      ),
    ).toEqual({
      message: "The search index for your documents is damaged.",
      code: "index_damaged",
      canRebuild: true,
    });
    expect(blockedBy(fail(503, { message: "Busy.", code: "busy", canRebuild: false }))).toEqual({
      message: "Busy.",
      code: "busy",
      canRebuild: false,
    });
    expect(blockedBy(fail(500, { message: "No.", canRebuild: true }))).toBeNull();
    expect(blockedBy(new Error("offline"))).toBeNull();
    expect(blockedBy(null)).toBeNull();
  });

  it("falls back to the error's own words for a 503 that came without any", () => {
    expect(blockedBy(Object.assign(new Error("Workbench answered 503."), { status: 503 }))).toEqual(
      {
        message: "Workbench answered 503.",
        code: null,
        canRebuild: false,
      },
    );
  });

  it("says what a rebuild came to", () => {
    expect(rebuiltWords({ documents: 3, left: 0 })).toBe(
      "Workbench is making the search again from 3 documents. They are read again one at a time.",
    );
    expect(rebuiltWords({ documents: 1, left: 2 })).toBe(
      "Workbench is making the search again from 1 document. They are read again one at a time. 2 files could not be made into documents.",
    );
    expect(rebuiltWords({ documents: 0, left: 0 })).toBe(
      "Workbench found no files to make the search from.",
    );
    expect(rebuiltWords({ documents: 0, left: 1 })).toContain(
      "1 file could not be made into a document.",
    );
  });
});

describe("where in a file, and which words", () => {
  it("says a page, pages, a line or lines, and nothing for a file with neither", () => {
    expect(placeWords(4, 4, null, null)).toBe("page 4");
    expect(placeWords(4, null, null, null)).toBe("page 4");
    expect(placeWords(4, 6, null, null)).toBe("pages 4 to 6");
    expect(placeWords(null, null, 12, 12)).toBe("line 12");
    expect(placeWords(null, null, 12, 40)).toBe("lines 12 to 40");
    expect(placeWords(null, null, 1200, 1500)).toBe("lines 1,200 to 1,500");
    expect(placeWords(null, null, null, null)).toBeNull();
    // Pages win where a file has both.
    expect(placeWords(2, 3, 10, 20)).toBe("pages 2 to 3");
  });

  it("names a passage by its file, its place and its heading", () => {
    expect(passageTitle(passage())).toBe("harbour-survey.pdf, page 4");
    expect(passageTitle(second())).toBe("quarterly-report.docx, Conclusion");
    expect(passageTitle(passage({ page_start: 4, page_end: 5, heading: "Tides > Spring" }))).toBe(
      "harbour-survey.pdf, pages 4 to 5, Tides > Spring",
    );
    expect(
      passageTitle(passage({ page_start: null, page_end: null, line_start: 3, line_end: 9 })),
    ).toBe("harbour-survey.pdf, lines 3 to 9");
    expect(passageTitle(passage({ page_start: null, page_end: null }))).toBe("harbour-survey.pdf");
  });

  it("names a passage that a question tried found the same way", () => {
    const hit = tested().passages[0]!;
    expect(testedTitle(hit)).toBe("harbour-survey.pdf, page 4");
    expect(
      testedTitle({
        ...hit,
        pageStart: null,
        pageEnd: null,
        lineStart: 7,
        lineEnd: 9,
        heading: "Notes",
      }),
    ).toBe("harbour-survey.pdf, lines 7 to 9, Notes");
  });

  it("lists the words a passage was found by, a phrase in quotes", () => {
    expect(matchedWords(["code", "word"], [])).toBe("code, word");
    expect(matchedWords(["code"], ["code word"])).toBe("code, “code word”");
    expect(matchedWords([], [])).toBeNull();
  });

  it("marks those words where they are: whole, in any case, in any alphabet, longest first", () => {
    const parts = (text: string, words: string[]) =>
      marked(text, words)
        .map((p) => (p.hit ? `[${p.text}]` : p.text))
        .join("");
    expect(parts("The code word is here", ["code", "word"])).toBe("The [code] [word] is here");
    expect(parts("The CODE word", ["code"])).toBe("The [CODE] word");
    // A word inside another word is not the word: the search does not stem.
    expect(parts("codes and decode and code", ["code"])).toBe("codes and decode and [code]");
    expect(parts("the code-word", ["code"])).toBe("the [code]-word");
    expect(parts("Café au lait", ["café"])).toBe("[Café] au lait");
    expect(parts("naïve cafés", ["café"])).toBe("naïve cafés");
    expect(parts("a code word and a code", ["code", "code word"])).toBe(
      "a [code word] and a [code]",
    );
  });

  it("is the whole text, unmarked, when there is nothing to mark or nothing to mark in", () => {
    expect(marked("plain", [])).toEqual([{ text: "plain", hit: false }]);
    expect(marked("plain", ["", "  "])).toEqual([{ text: "plain", hit: false }]);
    expect(marked("", ["a"])).toEqual([{ text: "", hit: false }]);
    expect(marked("nothing here", ["zebra"])).toEqual([{ text: "nothing here", hit: false }]);
    // Spaces and punctuation are not a word that was found: no empty mark is made of them.
    expect(marked("a, b — c.", [])).toEqual([{ text: "a, b — c.", hit: false }]);
    expect(marked("a, b — c.", ["", " "])).toEqual([{ text: "a, b — c.", hit: false }]);
  });

  it("does not take a word with a regular expression's own marks for one", () => {
    expect(
      marked("a.b a+b", ["a.b"])
        .filter((p) => p.hit)
        .map((p) => p.text),
    ).toEqual(["a.b"]);
    expect(marked("anything", [".*"])).toEqual([{ text: "anything", hit: false }]);
  });
});

describe("Test a question", () => {
  it("says what a found question would send, and that no model was asked", () => {
    expect(testedWords(tested())).toBe(
      "Workbench would send 2 passages with this question, about 350 tokens. No model was asked.",
    );
    expect(testedWords(tested({ passages: [tested().passages[0]!] }))).toBe(
      "Workbench would send 1 passage with this question, about 290 tokens. No model was asked.",
    );
  });

  it("says nothing matched is a normal result, and what a question with no words and no documents mean", () => {
    expect(testedWords(tested({ status: "nothing_matched", passages: [] }))).toBe(
      "Nothing in your documents matched, so Workbench would send no passages. No model was asked.",
    );
    expect(testedWords(tested({ status: "no_words", passages: [] }))).toBe(
      "There are no words in that question to search for. Try a few of the words the file uses.",
    );
    expect(
      testedWords(
        tested({
          status: "no_documents",
          passages: [],
          searched: { documents: 0, passages: 0, waiting: 0 },
        }),
      ),
    ).toBe("You have no documents that can be searched yet.");
    expect(
      testedWords(
        tested({
          status: "no_documents",
          passages: [],
          searched: { documents: 0, passages: 0, waiting: 2 },
        }),
      ),
    ).toBe("None of your documents can be searched yet: 2 documents are still being read.");
    expect(
      testedWords(
        tested({
          status: "no_documents",
          passages: [],
          searched: { documents: 0, passages: 0, waiting: 1 },
        }),
      ),
    ).toBe("None of your documents can be searched yet: 1 document is still being read.");
  });

  it("says how much was searched, and what is still being read", () => {
    expect(searchedCounts(tested())).toBe("Searched 3 documents (9 passages).");
    expect(searchedCounts(tested({ searched: { documents: 1, passages: 3, waiting: 2 } }))).toBe(
      "Searched 1 document (3 passages). 2 documents are still being read, so not searched yet.",
    );
  });

  it("says the words it searched for", () => {
    expect(searchedFor(["code", "word"], [])).toBe("Searched for: code, word");
    expect(searchedFor([], [])).toBeNull();
  });
});

describe("a chat's Documents chip", () => {
  it("says plain Documents before it has read anything, and why when it could not", () => {
    expect(chatChipWords(null)).toEqual({ label: "Documents", short: "", tone: "quiet" });
    expect(chatChipWords(null, true)).toEqual({
      label: "Documents: not available",
      short: "",
      tone: "warn",
    });
  });

  it("says how many documents the next message searches", () => {
    expect(chatChipWords(chatDocuments())).toEqual({
      label: "Documents (3)",
      short: "3",
      tone: "on",
    });
    expect(
      chatChipWords(
        chatDocuments({
          counts: {
            library: { documents: 1234, passages: 9, waiting: 0 },
            chat: { documents: 0, passages: 0, waiting: 0 },
          },
        }),
      ).label,
    ).toBe("Documents (1,234)");
  });

  it("counts this chat's own searched files with the person's documents, and each only when it is searched", () => {
    const both = chatDocuments({
      searching: { library: true, chat: true },
      counts: {
        library: { documents: 3, passages: 9, waiting: 0 },
        chat: { documents: 1, passages: 4, waiting: 0 },
      },
    });
    expect(chatChipWords(both).label).toBe("Documents (4)");
    const files = off({
      sending: true,
      searching: { library: false, chat: true },
      reasonCode: null,
      reason: null,
      counts: {
        library: { documents: 3, passages: 9, waiting: 0 },
        chat: { documents: 1, passages: 4, waiting: 0 },
      },
    });
    expect(chatChipWords(files).label).toBe("Documents (1)");
  });

  it("says when it is on and there is nothing to search yet, or it is still being read", () => {
    const none = chatDocuments({
      counts: {
        library: { documents: 0, passages: 0, waiting: 0 },
        chat: { documents: 0, passages: 0, waiting: 0 },
      },
    });
    expect(chatChipWords(none)).toEqual({
      label: "Documents: none yet",
      short: "0",
      tone: "quiet",
    });
    const waiting = chatDocuments({
      counts: {
        library: { documents: 0, passages: 0, waiting: 2 },
        chat: { documents: 0, passages: 0, waiting: 0 },
      },
    });
    expect(chatChipWords(waiting).label).toBe("Documents: being read");
  });

  it.each([
    ["chat_off", "Documents off", "Off", "quiet"],
    ["install_off", "Documents off", "Off", "quiet"],
    ["no_model", "Documents: choose a model", "", "quiet"],
    ["outside_model", "Documents not sent: outside model", "", "warn"],
    ["window_too_small", "Documents not sent: small window", "", "warn"],
    ["model_unknown", "Documents not sent", "", "warn"],
  ] as const)("for %s says %s", (code, label, short, tone) => {
    expect(chatChipWords(held(code, "Why."))).toEqual({ label, short, tone });
  });

  it("does not call a model it cannot place outside", () => {
    const unknown = held("outside_model", "Why.", {
      model: { id: "m", window: 4096, locality: "unknown", runsOn: null },
    });
    expect(chatChipWords(unknown).label).toBe("Documents not sent: model's location unknown");
  });

  it("says only whether Documents was on, for the owner reading someone's chat", () => {
    expect(chatChipWords(owners())).toEqual({ label: "Documents on", short: "On", tone: "quiet" });
    expect(chatChipWords(owners({ switch: "off" }))).toEqual({
      label: "Documents off",
      short: "Off",
      tone: "quiet",
    });
    expect(readOnlyWords(owners())).toBe("Documents are on in this chat.");
    expect(readOnlyWords(owners({ switch: "off", documentsOnly: true }))).toBe(
      "Documents are off in this chat, and it answers only from them.",
    );
  });
});

describe("what a chat's drawer says there is", () => {
  it("counts the person's documents, and this chat's own files, and what is still being read", () => {
    expect(countsWords(chatDocuments())).toEqual([
      "Your documents: 3 documents ready to search (9 passages).",
    ]);
    expect(
      countsWords(
        chatDocuments({
          counts: {
            library: { documents: 3, passages: 9, waiting: 2 },
            chat: { documents: 1, passages: 4, waiting: 1 },
          },
        }),
      ),
    ).toEqual([
      "Your documents: 3 documents ready to search (9 passages), 2 more being read.",
      "Files of this chat you chose to search: 1 document ready to search (4 passages), 1 more being read.",
    ]);
    expect(
      countsWords(
        chatDocuments({
          counts: {
            library: { documents: 0, passages: 0, waiting: 2 },
            chat: { documents: 0, passages: 0, waiting: 1 },
          },
        }),
      ),
    ).toEqual([
      "Your documents: 2 documents being read.",
      "Files of this chat you chose to search: none ready yet, 1 more being read.",
    ]);
    expect(
      countsWords(
        chatDocuments({
          counts: {
            library: { documents: 0, passages: 0, waiting: 0 },
            chat: { documents: 0, passages: 0, waiting: 0 },
          },
        }),
      ),
    ).toEqual(["Your documents: 0 documents ready to search (0 passages)."]);
  });

  it("says the room passages may take, of the window when it is known", () => {
    expect(budgetWords({ limit: 3000, window: 32768 })).toBe(
      "Passages may take up to 3,000 tokens of this model's 32,768-token window, and fewer when the chat is already long.",
    );
    expect(budgetWords({ limit: 3000, window: null })).toBe(
      "Passages may take up to 3,000 tokens, and fewer when the chat is already long.",
    );
  });
});

describe("the hint in the composer", () => {
  it("is for a person who has documents to search and a chat that has Documents off", () => {
    const quiet = off();
    expect(hintCount(quiet)).toBe(3);
    expect(hintWords(3)).toBe("Your 3 documents are off here.");
    expect(hintWords(1)).toBe("Your document is off here.");
    expect(hintWords(1234)).toBe("Your 1,234 documents are off here.");
  });

  it("is not there while Documents are on, when the person has none that can be searched, when the owner turned them off, or for someone else's chat", () => {
    expect(hintCount(chatDocuments())).toBeNull();
    expect(
      hintCount(
        off({
          counts: {
            library: { documents: 0, passages: 0, waiting: 2 },
            chat: { documents: 0, passages: 0, waiting: 0 },
          },
        }),
      ),
    ).toBeNull();
    expect(
      hintCount(held("install_off", "The owner turned them off.", { switch: "off" })),
    ).toBeNull();
    expect(hintCount(owners({ switch: "off" }))).toBeNull();
    expect(hintCount(null)).toBeNull();
  });

  it("is hidden for good in this browser by the person who hid it, and for nobody else", () => {
    window.localStorage.removeItem("workbench-documents-hint:ada");
    expect(hintHidden("ada")).toBe(false);
    hideHint("ada");
    expect(hintHidden("ada")).toBe(true);
    expect(hintHidden("bo")).toBe(false);
    window.localStorage.removeItem("workbench-documents-hint:ada");
  });

  it("is not lost to a browser that will not keep it: it is shown again, and nothing breaks", () => {
    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const keeping = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(hintHidden("ada")).toBe(false);
    expect(() => hideHint("ada")).not.toThrow();
    blocked.mockRestore();
    keeping.mockRestore();
  });
});

describe("a view that is not a chat's documents", () => {
  it("is not taken for one: the chip says nothing it did not read", () => {
    expect(readView(chatDocuments())).not.toBeNull();
    expect(readView(owners())).not.toBeNull();
    for (const odd of [
      null,
      undefined,
      "x",
      {},
      { chatId: "c1" },
      { chatId: "c1", switch: "maybe" },
      // A switch that is neither on nor off is not one of ours, however much else it has.
      { ...chatDocuments(), switch: "maybe" },
      { ...chatDocuments(), switch: undefined },
      // A chat's notes are not its documents.
      { chatId: "c1", readOnly: false, sending: true, chatSwitch: "on", counts: { notes: 1 } },
      { chatId: "c1", readOnly: false, switch: "on", counts: {} },
    ]) {
      expect(readView(odd), JSON.stringify(odd)).toBeNull();
    }
  });
});

describe("under an answer", () => {
  it("says what was sent, and never what the model used", () => {
    expect(sentWords(3, 2)).toBe("Sent to the model: 3 passages from 2 documents");
    expect(sentWords(1, 1)).toBe("Sent to the model: 1 passage from 1 document");
    expect(sentWords(2, 2)).not.toMatch(/\bused\b/);
  });

  it("says how they were found only where it knows", () => {
    expect(modeWords("words")).toBe("Found by words only.");
    expect(modeWords("meaning and words")).toBe("Found by meaning and words.");
    expect(modeWords("whatever else")).toBeNull();
  });

  it("says what matched and did not fit the room", () => {
    expect(leftOutWords(0)).toBeNull();
    expect(leftOutWords(1)).toBe("1 more passage matched but did not fit the room this chat had.");
    expect(leftOutWords(3)).toBe("3 more passages matched but did not fit the room this chat had.");
  });

  it("says nothing matched in the server's words, and a plain one if it sent none", () => {
    expect(ranWords(nothing())).toBe("Nothing in your documents matched.");
    expect(ranWords(nothing({ message: null }))).toBe("Nothing in your documents matched.");
    expect(ranWords(record({ status: "failed", message: "Searching failed (OSError)." }))).toBe(
      "Searching failed (OSError).",
    );
    expect(ranWords(record({ status: "failed", message: null }))).toBe(
      "Searching your documents failed, so this answer used none. See Workbench's log.",
    );
    expect(ranWords(record({ status: "no_documents", message: null }))).toBe(
      "You have no documents to search yet. Add some in Documents, then ask again.",
    );
    expect(ranWords(record({ status: "skipped", message: null }))).toBe(
      "Your documents were not searched for this answer.",
    );
  });

  it("can be mended in Documents only when the index is damaged and Workbench can make it again", () => {
    expect(canRebuild(record({ status: "failed", code: "index_damaged" }))).toBe(true);
    expect(canRebuild(record({ status: "failed", code: "busy" }))).toBe(false);
    expect(canRebuild(record({ status: "skipped", code: "index_damaged" }))).toBe(false);
    expect(canRebuild(record({ status: "failed", code: null }))).toBe(false);
  });

  it("keeps the hint the server gave for why documents were held back", () => {
    const held = record({
      status: "skipped",
      code: "outside_model",
      message: "Your documents were not sent.",
      notes: [
        { code: "other", message: "x", hint: "No." },
        {
          code: "outside_model",
          message: "Your documents were not sent.",
          hint: "Choose Send for this chat.",
        },
      ],
    });
    expect(hintFor(held)).toBe("Choose Send for this chat.");
    expect(hintFor(record())).toBeNull();
    expect(hintFor(record({ code: "x", notes: [{ code: "x", message: "m" }] }))).toBeNull();
  });

  it("is a line in a chat exported as Markdown: the file, the place, and whether the answer cited it", () => {
    expect(passageLine(passage())).toBe(
      "Passage sent to the model: harbour-survey.pdf, page 4 (cited in the answer)",
    );
    expect(passageLine(second())).toBe(
      "Passage sent to the model: quarterly-report.docx, Conclusion",
    );
    expect(passageLine(hidden())).toBe("Passage sent to the model: a document");
  });

  it("tells a passage the owner may not read from one that is", () => {
    expect(isHidden(hidden())).toBe(true);
    expect(isHidden(passage())).toBe(false);
    // One that says it is not hidden is readable, whatever else it says.
    expect(isHidden({ ...passage(), redacted: false } as never)).toBe(false);
  });
});

// --- search by meaning (MR5) ---

describe("where a document stands, once its meaning is being added", () => {
  it("is ready for words, with the server's own sentence for how its meaning is going", () => {
    expect(stateWords(adding())).toEqual({
      state: "embedding",
      tone: "ok",
      label: "Ready for words. Adding meaning: 2 of 5 passages, about 14 minutes left.",
    });
  });

  it("is a wait, with a clock and the server's words, when the search model is not there or the key is at its limit", () => {
    const stopped = stateWords(waiting());
    expect(stopped.state).toBe("searchable");
    expect(stopped.tone).toBe("quiet");
    expect(stopped.label).toMatch(/^Ready for words\. Meaning is waiting: the search model/);
    const slow = stateWords(limited());
    expect(slow.tone).toBe("quiet");
    expect(slow.label).toMatch(
      /^Ready for words\. Adding meaning to your documents is slow right now/,
    );
  });

  it("is plain ready for words while the server has said nothing of its meaning", () => {
    expect(stateWords(saved({ state: "searchable" })).label).toBe("Ready for words");
    expect(
      stateWords(
        saved({ state: "embedding", meaning: meaningOf({ state: "adding", message: null }) }),
      ).label,
    ).toBe("Ready for words");
  });

  it("is ready when every passage has its meaning, and says why a document read without any has none", () => {
    expect(stateWords(withMeaning())).toEqual({ state: "ready", tone: "ok", label: "Ready" });
    expect(stateWords(wordsOnlyDoc())).toEqual({
      state: "ready",
      tone: "ok",
      label: "Ready for words only: no search model was running when it was read.",
    });
  });

  it("says how many passages were refused, in a document that is ready", () => {
    const some = saved({ meaning: meaningOf({ embedded: 5, total: 5, refused: 1 }) });
    expect(stateWords(some).label).toBe(
      "Ready. Meaning was added to 4 of 5 passages; 1 passage is found by words only.",
    );
  });

  it("does not touch a document that was skipped or could not be read", () => {
    expect(stateWords(scanned({ meaning: meaningOf({ state: "words_only" }) })).label).toMatch(
      /^Skipped:/,
    );
    expect(stateWords(broken({ meaning: meaningOf({ state: "words_only" }) })).label).toMatch(
      /^Failed:/,
    );
  });
});

describe("what the screen is waiting on", () => {
  const states = (list: ReturnType<typeof saved>[], search = wordsOnly()) =>
    watching(page(list, { search }));

  it("is documents being read: soon, and each second", () => {
    expect(states([queued()])).toBe("busy");
    expect(states([reading(), saved()])).toBe("busy");
    expect(isBeingRead(queued())).toBe(true);
    expect(isBeingRead(reading())).toBe(true);
    for (const done of [saved(), adding(), waiting(), scanned(), broken()]) {
      expect(isBeingRead(done), done.name).toBe(false);
    }
  });

  it("is meaning being added: each second, like reading", () => {
    expect(states([adding()], byMeaning())).toBe("busy");
    // A document that can be found by words and has no word on its meaning yet is being added to.
    expect(states([saved({ state: "searchable" })], byMeaning())).toBe("busy");
    expect(
      states(
        [saved()],
        byMeaning({ adding: addingView({ state: "working", message: "Adding meaning." }) }),
      ),
    ).toBe("busy");
  });

  it("is meaning that waits, which is asked about every few seconds: for a search model, or the key's limit", () => {
    expect(states([waiting()], byMeaning({ code: "not_running" }))).toBe("waiting");
    expect(states([limited()], byMeaning())).toBe("waiting");
    expect(
      states(
        [saved()],
        byMeaning({ adding: addingView({ state: "waiting", message: "Waiting." }) }),
      ),
    ).toBe("waiting");
    expect(
      states([saved()], byMeaning({ adding: addingView({ state: "limited", message: "Slow." }) })),
    ).toBe("waiting");
  });

  it("is quick for a document read before any search model ran, when one is running now: it is taken up", () => {
    expect(states([wordsOnlyDoc()], byMeaning())).toBe("busy");
    // With none running it waits for the half-minute look, and is not asked after each second.
    expect(states([wordsOnlyDoc()], wordsOnly())).toBe("idle");
  });

  it("is nothing, but the half-minute look, when everything is read and has its meaning", () => {
    expect(states([saved(), withMeaning(), scanned(), broken()], byMeaning())).toBe("idle");
    expect(states([], byMeaning())).toBe("idle");
    expect(states([saved()], wordsOnly({ adding: null }))).toBe("idle");
  });

  it("is reading before it is anything else", () => {
    expect(states([queued(), waiting()], byMeaning({ code: "not_running" }))).toBe("busy");
  });

  it("asks how often the design says: each half minute when there is nothing to watch, every five seconds when waiting", () => {
    expect(IDLE_POLL_MS).toBe(30_000);
    expect(WAITING_POLL_MS).toBe(5_000);
  });
});

describe("a tried question that none of its ways could answer", () => {
  it("says nothing was searched, and why, in the server's words, and never that nothing matched", () => {
    const none = tested({
      status: "unavailable",
      mode: "none",
      passages: [],
      meaning: meaningRun({
        code: "none_set_up",
        message: "Searching by words only: no search model is set up.",
        quiet: true,
      }),
    });
    expect(testedWords(none)).toBe(
      "Nothing was searched: search by meaning could not run. No search model is set up.",
    );
    expect(testedWords(none)).not.toMatch(/matched/);
  });
});

describe("how the passages under an answer were found, with the model", () => {
  it("says the search model when meaning ran, and not when it did not", () => {
    expect(modeWords("meaning and words", ranByMeaning())).toBe(
      "Found by meaning and words, with the search model Qwen3-Embedding-0.6B-Q8_0.",
    );
    expect(modeWords("meaning and words")).toBe("Found by meaning and words.");
    expect(modeWords("meaning")).toBe("Found by meaning only.");
    expect(modeWords("words", meaningRun())).toBe("Found by words only.");
  });
});
