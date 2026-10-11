import {
  choose,
  damaged,
  decision,
  everything,
  file,
  fresh,
  listing,
  MODEL,
  native,
  noRoom,
  partial,
  partSource,
  READ_ALL,
  READ_FIRST,
  reading,
  scan,
  source,
  tooLong,
} from "../test-documents";
import {
  ACCEPT,
  announceWords,
  asAttached,
  canGo,
  chipState,
  chipWords,
  endSentence,
  holdWords,
  isDocument,
  isReading,
  KNOWN_EXTENSIONS,
  metaWords,
  offerChoice,
  offerWords,
  PLAIN_EXTENSIONS,
  readingMeta,
  refusalFor,
  requestWords,
  sourceLine,
  sourceTags,
  sourceWhere,
} from "./documents";
import type { ChatFile } from "./types";

describe("what the file picker is asked to show", () => {
  it("keeps what it showed (images, PDFs, audio) and adds Word, text and code", () => {
    const wanted = ACCEPT.split(",");
    for (const old of ["image/png", "image/jpeg", "application/pdf", "audio/wav", "audio/mpeg"]) {
      expect(wanted).toContain(old);
    }
    expect(wanted).toContain(".docx");
    expect(wanted).toContain("text/*");
    for (const extension of [".md", ".csv", ".html", ".py", ".json", ".txt", ".log"]) {
      expect(wanted, extension).toContain(extension);
    }
    expect(new Set(wanted).size).toBe(wanted.length);
  });

  it("names each extension once, in lower case, with its dot", () => {
    const all = [...KNOWN_EXTENSIONS, ...PLAIN_EXTENSIONS];
    expect(new Set(all).size).toBe(all.length);
    for (const extension of all) expect(extension).toMatch(/^\.[a-z0-9]+$/);
  });
});

describe("where a file stands", () => {
  it("is a document when the server says so, and an image or a sound is not", () => {
    expect(isDocument(file())).toBe(true);
    expect(isDocument(file({ kind: "image" }))).toBe(false);
    expect(isDocument({ id: "i", name: "a.png", mediaType: "image/png" })).toBe(false);
  });

  it("is being read until its reading ends, or while its text is read again", () => {
    expect(isReading(fresh())).toBe(true);
    expect(isReading(file())).toBe(false);
    expect(isReading(scan())).toBe(false);
    // The text beside the file went missing: reading says ready, the decision says pending.
    expect(isReading(file({ decision: decision({ kind: "pending", send: false }) }))).toBe(true);
  });

  it("goes when its decision says it can, and a file nobody has decided goes unless it is being read", () => {
    expect(canGo(file())).toBe(true);
    expect(canGo(choose())).toBe(false);
    expect(canGo(fresh())).toBe(false);
    expect(canGo(file({ decision: null }))).toBe(true);
    expect(canGo(file({ decision: null, reading: reading({ state: "reading" }) }))).toBe(false);
  });

  it.each([
    ["being read", fresh(), "reading"],
    ["read whole", file(), "whole"],
    ["read whole because the person chose to", everything(), "whole"],
    ["a part", partial(), "partial"],
    ["sent as the file", native(), "native"],
    ["to be chosen", choose(), "choose"],
    ["too long", tooLong(), "too_long"],
    ["with no room", noRoom(), "no_room"],
    ["skipped", scan(), "skipped"],
    ["failed", damaged(), "failed"],
  ] as [string, ChatFile, string][])("a file %s has the look %s", (_name, found, state) => {
    expect(chipState(found)).toBe(state);
  });

  it("is read from its reading when nothing has decided it", () => {
    expect(chipState(file({ decision: null }))).toBe("whole");
    expect(chipState(fresh({ decision: null }))).toBe("reading");
    expect(chipState(scan({ decision: null }))).toBe("skipped");
    expect(chipState(damaged({ decision: null }))).toBe("failed");
    expect(chipState(file({ decision: null, reading: null }))).toBe("reading");
  });
});

describe("what Workbench found in a file", () => {
  it("is its kind, its pages or lines, and about how many tokens", () => {
    expect(metaWords(file())).toBe("PDF · 3 pages · about 77 tokens");
    expect(metaWords(file({ reading: reading({ pages: 1, tokens: 1 }) }))).toBe(
      "PDF · 1 page · about 1 token",
    );
    expect(metaWords(choose())).toBe("text · 900 lines · about 9,300 tokens");
    expect(metaWords(file({ reading: reading({ label: "Word", pages: null, tokens: 83 }) }))).toBe(
      "Word · about 83 tokens",
    );
  });

  it("names the language of code and data, and only of those", () => {
    const code = file({
      reading: reading({ kind: "code", label: "code", language: "python", pages: null, lines: 5 }),
    });
    expect(metaWords(code)).toBe("code (python) · 5 lines · about 77 tokens");
    const pdf = file({ reading: reading({ language: "python" }) });
    expect(metaWords(pdf)).toBe("PDF · 3 pages · about 77 tokens");
  });

  it("is only the kind while it is read, and a PDF is a PDF before it is read", () => {
    expect(metaWords(fresh())).toBe("PDF");
    expect(metaWords(file({ reading: null }))).toBe("PDF");
    expect(metaWords({ id: "t", name: "a.txt", mediaType: "text/plain", reading: null })).toBe("");
    expect(readingMeta(reading({ pages: 12, tokens: 8400 }))).toBe(
      "PDF · 12 pages · about 8,400 tokens",
    );
    expect(readingMeta(null)).toBe("");
  });
});

describe("what a chip says of a file", () => {
  const words = (found: ChatFile, model = MODEL) => chipWords(found, model);

  it("is that it is being read, in the server's words", () => {
    expect(words(fresh())).toMatchObject({ state: "reading", tone: "busy", sentence: "Reading…" });
    const again = file({
      decision: decision({
        kind: "pending",
        send: false,
        message:
          "Workbench's copy of this file's text is missing, so it is reading the file again.",
      }),
    });
    expect(words(again).sentence).toContain("reading the file again");
  });

  it("is that it will be read whole", () => {
    expect(words(file())).toMatchObject({
      state: "whole",
      tone: "ok",
      sentence: "Will be read whole.",
      choice: null,
    });
  });

  it("is that it will be read whole as the person chose, and the choice", () => {
    expect(words(everything())).toMatchObject({
      sentence: "Will be read whole, as you chose.",
      choice: null,
    });
    // A choice of the first part that came to all of it is still the choice.
    const all = file({ decision: decision({ chosen: { mode: "first", tokens: 99999 } }) });
    expect(words(all)).toMatchObject({
      sentence: "Will be read whole.",
      choice: "Your choice: the first 99,999 tokens.",
    });
  });

  it("is which part will be read, how many tokens, that the rest is not sent, and the choice", () => {
    expect(words(partial())).toMatchObject({
      state: "partial",
      tone: "ok",
      sentence:
        "Will read lines 1 to 290 of 900 (about 2,997 tokens). The rest of the file is not sent.",
      choice: "Your choice: the first 3,000 tokens.",
    });
    const nothing = partial({ decision: decision({ kind: "partial", coverage: null }) });
    expect(words(nothing).sentence).toBe(
      "Will read only the first part. The rest of the file is not sent.",
    );
  });

  it("is that the model reads PDFs itself, naming the model", () => {
    expect(words(native())).toMatchObject({
      state: "native",
      tone: "ok",
      sentence: "local-model reads PDFs itself, so Workbench sends the file as it is.",
    });
    expect(words(native(), { ...MODEL, id: null }).sentence).toBe(
      "This model reads PDFs itself, so Workbench sends the file as it is.",
    );
  });

  it("is the server's sentence for a PDF that was never read and goes as the file", () => {
    const unread = native({
      reading: null,
      decision: decision({
        kind: "native",
        message: "Workbench sends this PDF as the file.",
        native: true,
      }),
    });
    expect(words(unread).sentence).toBe("Workbench sends this PDF as the file.");
  });

  it("is to choose how much to read, with the limit it is longer than", () => {
    expect(words(choose())).toMatchObject({
      state: "choose",
      tone: "warn",
      title: "Choose how much to read",
      sentence: "This file is longer than Workbench reads at once by default (6,000 tokens).",
    });
  });

  it("says a choice that no longer fits, so the person knows why they are asked again", () => {
    const stale = choose({
      decision: {
        ...choose().decision!,
        choiceNoLongerFits: true,
        chosen: { mode: "first", tokens: 8000 },
      },
    });
    expect(words(stale).sentence).toBe(
      "Your earlier choice (the first 8,000 tokens) no longer fits this model. This file is longer than Workbench reads at once by default (6,000 tokens).",
    );
  });

  it("is that it is too long for the window, with the window and the room", () => {
    expect(words(tooLong(), { ...MODEL, window: 4096 })).toMatchObject({
      state: "too_long",
      tone: "warn",
      title: "Too long for this model",
      sentence:
        "This model's window is 4,096 tokens, so there is room for about 2,147 tokens of this file.",
    });
  });

  it("says what to do when no part of a file fits", () => {
    const none = tooLong({ decision: { ...tooLong().decision!, offers: [] } });
    expect(words(none).sentence).toContain(
      "Pick a model with a larger window, or remove the file.",
    );
    expect(words(tooLong()).sentence).not.toContain("Pick a model");
  });

  it("is the server's sentence when there is no room", () => {
    expect(words(noRoom())).toMatchObject({
      state: "no_room",
      tone: "warn",
      title: "No room for this file",
      sentence: noRoom().decision!.message,
    });
  });

  it("is the server's own sentence for a file it skipped, and for one it could not read", () => {
    expect(words(scan())).toMatchObject({
      state: "skipped",
      tone: "warn",
      title: null,
      sentence:
        "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.",
      hint: "Remove it to send your message.",
    });
    expect(words(damaged())).toMatchObject({ state: "failed", tone: "error" });
    expect(words(damaged()).sentence).toBe(
      "Failed: could not read this file (the PDF is cut off). It may be damaged.",
    );
  });

  it("carries what the reader noted about the file", () => {
    const noted = file({
      decision: decision({ notes: ["Page 2 has no text (it may be a picture)."] }),
    });
    expect(words(noted).notes).toEqual(["Page 2 has no text (it may be a picture)."]);
    const bare = file({ decision: null, reading: reading({ notes: ["Read as Windows-1252."] }) });
    expect(words(bare).notes).toEqual(["Read as Windows-1252."]);
  });

  it("offers each choice as a button, and a button chooses what it says", () => {
    expect(offerWords(READ_ALL)).toBe("Read it all (about 9,300 tokens)");
    expect(offerWords(READ_FIRST)).toBe("Read the first 5,859 tokens (lines 1 to 567 of 900)");
    expect(offerWords(tooLong().decision!.offers[0]!)).toBe(
      "Read the first 1,614 tokens (pages 1 to 2 of 30)",
    );
    expect(offerChoice(READ_ALL)).toEqual({ mode: "whole" });
    expect(offerChoice(READ_FIRST)).toEqual({ mode: "first", tokens: 5859 });
  });

  it("is told to a screen reader with the file's name, and the title when it has one", () => {
    expect(announceWords(file(), MODEL)).toBe("report.pdf: Will be read whole.");
    expect(announceWords(choose(), MODEL)).toBe(
      "log.txt: Choose how much to read. This file is longer than Workbench reads at once by default (6,000 tokens).",
    );
  });
});

describe("why Send waits", () => {
  const facts = { model: MODEL, request: { used: 1, window: 32768, reserve: 1024, full: null } };
  const hold = (files: ChatFile[], over: { checking?: boolean; full?: string | null } = {}) =>
    holdWords(
      files,
      { ...facts, request: { ...facts.request, full: over.full ?? null } },
      over.checking ?? false,
      "local-model",
    );

  it("does not, when every file can go", () => {
    expect(hold([file(), partial(), native()])).toBeNull();
    expect(hold([])).toBeNull();
    expect(hold([{ id: "i", name: "a.png", mediaType: "image/png" }])).toBeNull();
    expect(holdWords([file()], null, false, null)).toBeNull();
  });

  it("is that Workbench is still reading a file", () => {
    expect(hold([fresh()])).toBe("Waiting for Workbench to read report.pdf.");
  });

  it("is that a file needs a choice, whether it fits whole or not", () => {
    expect(hold([choose()])).toBe("Choose how much of log.txt to read, or remove it.");
    expect(hold([tooLong()])).toBe("Choose how much of long.pdf to read, or remove it.");
  });

  it("is that a file cannot be read, or has no room", () => {
    expect(hold([scan()])).toBe("scan.pdf cannot be read. Remove it to send your message.");
    expect(hold([damaged()])).toBe("broken.pdf cannot be read. Remove it to send your message.");
    expect(hold([noRoom()])).toBe(
      "There is no room for report.pdf in this model's window. Remove it, or pick a model with a larger window.",
    );
  });

  it("says the file still being read first, then a choice, then what cannot go, and counts the rest", () => {
    expect(hold([scan(), choose(), fresh()])).toBe(
      "Waiting for Workbench to read report.pdf. (2 more files need attention too.)",
    );
    expect(hold([scan(), choose()])).toBe(
      "Choose how much of log.txt to read, or remove it. (1 more file needs attention too.)",
    );
    expect(hold([noRoom(), damaged()])).toContain("broken.pdf cannot be read");
  });

  it("is that the whole message does not fit, when every file alone does", () => {
    expect(hold([file(), file({ id: "g", name: "again.pdf" })], { full: "too much" })).toBe(
      "This chat and your files are more than this model's window holds. Remove a file, or pick a model with a larger window.",
    );
  });

  it("is that the files are being checked against another model, before anything else", () => {
    expect(hold([fresh()], { checking: true })).toBe("Checking your files against local-model…");
    expect(holdWords([file()], null, true, null)).toBe("Checking your files against this model…");
  });
});

describe("how much of the window the message comes to", () => {
  it("says what it uses, of what, and what is kept for the answer", () => {
    expect(requestWords({ used: 4218, window: 32768, reserve: 1024, full: null })).toBe(
      "With your files, this chat comes to about 4,218 of this model's 32,768 tokens, and 1,024 are kept for the answer.",
    );
  });

  it("says nothing when the window is not known, or when the request does not fit and says so", () => {
    expect(requestWords({ used: 1, window: null, reserve: 1024, full: null })).toBeNull();
    expect(requestWords({ used: 9, window: 4096, reserve: 1024, full: "too much" })).toBeNull();
    expect(requestWords(null)).toBeNull();
    expect(listing([file()]).request?.full).toBeNull();
  });
});

describe("what a server refusal names", () => {
  const refusal = (status: number, detail: unknown) =>
    Object.assign(new Error("no"), { status, detail });
  const named = {
    message: "log.txt: About 9,300 tokens.",
    code: "documents",
    files: [{ id: "f2", name: "log.txt", decision: choose().decision }],
  };

  it("is the files a send was refused for, each with its decision", () => {
    const found = refusalFor(refusal(409, named), "documents");
    expect(found?.message).toBe("log.txt: About 9,300 tokens.");
    expect(found?.files).toHaveLength(1);
    expect(found?.files[0]).toMatchObject({ id: "f2", name: "log.txt" });
    expect(found?.files[0]?.decision?.kind).toBe("choose");
  });

  it("is the files a choice was refused for", () => {
    expect(refusalFor(refusal(409, { ...named, code: "choice" }), "choice")?.files).toHaveLength(1);
  });

  it("is nothing for any other failure, or another code, or a plain error", () => {
    expect(refusalFor(refusal(409, named), "choice")).toBeNull();
    expect(refusalFor(refusal(422, named), "documents")).toBeNull();
    expect(refusalFor(refusal(409, undefined), "documents")).toBeNull();
    expect(refusalFor(refusal(409, "text"), "documents")).toBeNull();
    expect(refusalFor(new Error("offline"), "documents")).toBeNull();
    expect(refusalFor(null, "documents")).toBeNull();
  });

  it("keeps the sentence when the refusal names no file, and skips what is not a file", () => {
    const bare = refusalFor(
      refusal(409, { message: "Too much.", code: "documents", files: [1, { id: 2 }, null] }),
      "documents",
    );
    expect(bare).toEqual({ message: "Too much.", files: [] });
    expect(refusalFor(refusal(409, { code: "documents" }), "documents")?.message).toBe(
      "Your files cannot go as they are.",
    );
  });

  it("ends a sentence that has no end", () => {
    expect(endSentence("Workbench answered 502")).toBe("Workbench answered 502.");
    expect(endSentence("It did not answer.")).toBe("It did not answer.");
    expect(endSentence("Reading…")).toBe("Reading…");
  });
});

describe("a file, as a message carries it", () => {
  it("keeps its name, its type and how it was read, and none of what was decided", () => {
    expect(asAttached(file())).toEqual({
      id: "f1",
      name: "report.pdf",
      mediaType: "application/pdf",
      reading: reading(),
    });
    expect(asAttached({ id: "i", name: "a.png", mediaType: "image/png" }).reading).toBeNull();
  });
});

describe("what an answer lists of the files that were sent", () => {
  it("is the pages that went, of how many", () => {
    expect(sourceWhere(source())).toBe("pages 1 to 3 of 3");
    expect(sourceWhere(source({ page_start: 2, page_end: 2, pages_total: 9 }))).toBe("page 2 of 9");
    expect(sourceWhere(source({ page_start: 1, page_end: 12, pages_total: 30 }))).toBe(
      "pages 1 to 12 of 30",
    );
  });

  it("is the lines that went, grouped like every number", () => {
    expect(sourceWhere(partSource())).toBe("lines 1 to 290 of 900");
    expect(sourceWhere(partSource({ line_start: 1, line_end: 1200, lines_total: 4500 }))).toBe(
      "lines 1 to 1,200 of 4,500",
    );
  });

  it("is whole when the file has neither pages nor lines to say", () => {
    const plain = source({ page_start: null, page_end: null, pages_total: null });
    expect(sourceWhere(plain)).toBe("whole");
  });

  it("is how much of the text, when only that is known of a part", () => {
    const part = source({
      page_start: null,
      page_end: null,
      pages_total: null,
      partial: true,
      fraction: 0.124,
    });
    expect(sourceWhere(part)).toBe("the first 12 percent");
  });

  it("is that the file itself was sent, for a model that reads PDFs", () => {
    expect(sourceWhere(source({ native: true, tokens: null }))).toBe("sent as a file");
  });

  it("tags only the first part, and a file attached earlier", () => {
    expect(sourceTags(source())).toEqual([]);
    expect(sourceTags(partSource())).toEqual(["first part"]);
    expect(sourceTags(partSource({ replayed: true }))).toEqual(["first part", "attached earlier"]);
    expect(sourceTags(source({ replayed: true }))).toEqual(["attached earlier"]);
  });

  it("is one line, for a chat exported as text", () => {
    expect(sourceLine(source())).toBe("report.pdf, pages 1 to 3 of 3");
    expect(sourceLine(partSource())).toBe("log.txt, lines 1 to 290 of 900 (first part)");
    expect(sourceLine(source({ native: true, replayed: true }))).toBe(
      "report.pdf, sent as a file (attached earlier)",
    );
  });
});
