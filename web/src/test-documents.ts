/**
 * What the Documents tests share: files, how they were read and what a request
 * to a model would do with them, as the server answers with them
 * (src/eugene_plexus_workbench/documents_api.py). The numbers are the ones the
 * server's own test world produced (a three-page PDF of 77 tokens, a text of 900
 * lines and 9,300 tokens, a thirty-page PDF against a window of 4,096 tokens).
 * Read only by tests; the page never imports it.
 */

import type {
  ChatFile,
  ChatFiles,
  DocumentSource,
  FileDecision,
  FilesModel,
  Offer,
  Reading,
} from "./lib/types";

export function reading(over: Partial<Reading> = {}): Reading {
  return {
    state: "ready",
    kind: "pdf",
    label: "PDF",
    language: null,
    pages: 3,
    lines: null,
    tokens: 77,
    chars: 221,
    headings: 0,
    notes: [],
    truncated: false,
    code: null,
    message: null,
    chosen: null,
    startedAt: 1791729595.686625,
    finishedAt: 1791729595.857125,
    ...over,
  };
}

const ROOM = { history: 4, memory: 0, documents: 0, reserve: 1024, remaining: 31740 };

/** A decision to send a file whole, for a window of 32,768 tokens. */
export function decision(over: Partial<FileDecision> = {}): FileDecision {
  return {
    kind: "whole",
    send: true,
    message: "3 pages, about 77 tokens. Will be read whole.",
    tokens: 77,
    blockTokens: 214,
    native: false,
    replayed: false,
    chosen: null,
    choiceNoLongerFits: false,
    offers: [],
    coverage: {
      whole: true,
      tokens: 77,
      where: "3 pages",
      pages: [1, 3],
      totalPages: 3,
      partialPage: false,
      lines: null,
      totalLines: null,
      fraction: 1,
      heading: null,
    },
    notes: [],
    window: 32768,
    wholeLimit: 6000,
    readAllLimit: 22218,
    overhead: 137,
    room: ROOM,
    ...over,
  };
}

export function file(over: Partial<ChatFile> = {}): ChatFile {
  return {
    id: "f1",
    name: "report.pdf",
    mediaType: "application/pdf",
    size: 1332,
    createdAt: 1791729595.686625,
    kind: "document",
    sent: false,
    reading: reading(),
    decision: decision(),
    ...over,
  };
}

/** Just attached: being read, and nothing to decide yet. */
export function fresh(over: Partial<ChatFile> = {}): ChatFile {
  return file({
    reading: reading({
      state: "reading",
      pages: null,
      tokens: null,
      chars: null,
      headings: null,
      finishedAt: null,
    }),
    decision: decision({
      kind: "pending",
      send: false,
      message: "Reading…",
      tokens: null,
      blockTokens: 0,
      coverage: null,
      overhead: undefined,
    }),
    ...over,
  });
}

const LOG_READING = reading({
  kind: "text",
  label: "text",
  pages: null,
  lines: 900,
  tokens: 9300,
  chars: 27900,
});

export const READ_ALL: Offer = { action: "readAll", tokens: 9300, where: "lines 1 to 900 of 900" };
export const READ_FIRST: Offer = {
  action: "readFirst",
  tokens: 5859,
  where: "lines 1 to 567 of 900",
  cut: {
    whole: false,
    tokens: 5859,
    where: "lines 1 to 567 of 900",
    pages: null,
    totalPages: null,
    partialPage: false,
    lines: [1, 567],
    totalLines: 900,
    fraction: 0.63,
    heading: null,
  },
};

/** A text of 9,300 tokens: more than is read at once, and it fits the window. */
export function choose(over: Partial<ChatFile> = {}): ChatFile {
  return file({
    id: "f2",
    name: "log.txt",
    mediaType: "text/plain",
    size: 27900,
    reading: LOG_READING,
    decision: decision({
      kind: "choose",
      send: false,
      message:
        "About 9,300 tokens. That is more than Workbench reads at once by default (6,000). Choose how much to read, or remove the file.",
      tokens: 9300,
      blockTokens: 0,
      offers: [READ_ALL, READ_FIRST],
      coverage: null,
      overhead: 136,
    }),
    ...over,
  });
}

/** The same text after the person chose the first 3,000 tokens. */
export function partial(over: Partial<ChatFile> = {}): ChatFile {
  return choose({
    reading: { ...LOG_READING, chosen: { mode: "first", tokens: 3000 } },
    decision: decision({
      kind: "partial",
      send: true,
      message: "Reading the first 2,997 tokens: lines 1 to 290 of 900.",
      tokens: 9300,
      blockTokens: 3133,
      chosen: { mode: "first", tokens: 3000 },
      coverage: {
        whole: false,
        tokens: 2997,
        where: "lines 1 to 290 of 900",
        pages: null,
        totalPages: null,
        partialPage: false,
        lines: [1, 290],
        totalLines: 900,
        fraction: 0.3222,
        heading: null,
      },
      overhead: 136,
    }),
    ...over,
  });
}

/** The same text after the person chose to read it all anyway. */
export function everything(over: Partial<ChatFile> = {}): ChatFile {
  return choose({
    reading: { ...LOG_READING, chosen: { mode: "whole" } },
    decision: decision({
      kind: "whole",
      send: true,
      message: "about 9,300 tokens. Will be read whole.",
      tokens: 9300,
      blockTokens: 9436,
      chosen: { mode: "whole" },
      coverage: {
        whole: true,
        tokens: 9300,
        where: "all of it",
        pages: null,
        totalPages: null,
        partialPage: false,
        lines: [1, 900],
        totalLines: 900,
        fraction: 1,
        heading: null,
      },
      overhead: 136,
    }),
    ...over,
  });
}

/** A thirty-page PDF of 24,524 tokens against a window of 4,096. */
export function tooLong(over: Partial<ChatFile> = {}): ChatFile {
  return file({
    id: "f3",
    name: "long.pdf",
    size: 91107,
    reading: reading({ pages: 30, tokens: 24524, chars: 73443 }),
    decision: decision({
      kind: "too_long",
      send: false,
      message:
        "About 24,524 tokens. That is too long for this model's window of 4,096 tokens: it has room for 2,147 here. Read the first 1,614 tokens (pages 1 to 2 of 30), or remove the file.",
      tokens: 24524,
      blockTokens: 0,
      offers: [
        {
          action: "readFirst",
          tokens: 1614,
          where: "pages 1 to 2 of 30",
          cut: {
            whole: false,
            tokens: 1614,
            where: "pages 1 to 2 of 30",
            pages: [1, 2],
            totalPages: 30,
            partialPage: false,
            lines: null,
            totalLines: null,
            fraction: 0.0658,
            heading: null,
          },
        },
      ],
      coverage: null,
      window: 4096,
      wholeLimit: 1024,
      readAllLimit: 2147,
      overhead: 136,
      room: { history: 4, memory: 0, documents: 0, reserve: 1024, remaining: 3068 },
    }),
    ...over,
  });
}

/** A scan: a PDF with no text, skipped with the server's own words. */
export function scan(over: Partial<ChatFile> = {}): ChatFile {
  const message =
    "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.";
  return file({
    id: "f4",
    name: "scan.pdf",
    size: 684,
    reading: reading({
      state: "skipped",
      pages: 2,
      tokens: null,
      chars: null,
      headings: null,
      code: "no_text",
      message,
    }),
    decision: decision({
      kind: "unreadable",
      send: false,
      message,
      tokens: null,
      blockTokens: 0,
      coverage: null,
      overhead: undefined,
    }),
    ...over,
  });
}

/** A file Workbench tried to read and could not. */
export function damaged(over: Partial<ChatFile> = {}): ChatFile {
  const message = "Failed: could not read this file (the PDF is cut off). It may be damaged.";
  return scan({
    id: "f5",
    name: "broken.pdf",
    reading: reading({
      state: "failed",
      pages: null,
      tokens: null,
      chars: null,
      headings: null,
      code: "damaged",
      message,
    }),
    decision: decision({
      kind: "unreadable",
      send: false,
      message,
      tokens: null,
      blockTokens: 0,
      coverage: null,
      overhead: undefined,
    }),
    ...over,
  });
}

/** A PDF sent as the file itself, to a model that reads PDFs. */
export function native(over: Partial<ChatFile> = {}): ChatFile {
  return file({
    decision: decision({
      kind: "native",
      send: true,
      message: "This model reads PDFs itself, so Workbench sends the file as it is.",
      native: true,
      blockTokens: 1500,
      coverage: null,
      overhead: undefined,
    }),
    ...over,
  });
}

/** A file with no room: the chat already fills the window. */
export function noRoom(over: Partial<ChatFile> = {}): ChatFile {
  return file({
    decision: decision({
      kind: "no_room",
      send: false,
      message:
        "This chat already fills about 6,060 of this model's 4,096 tokens, so there is no room for this document. Start a new chat or pick a model with a larger window.",
      blockTokens: 0,
      coverage: null,
      window: 4096,
      wholeLimit: 1024,
      readAllLimit: 0,
    }),
    ...over,
  });
}

export const MODEL: FilesModel = {
  id: "local-model",
  known: true,
  window: 32768,
  fileInput: false,
};

/** `GET /api/chats/{id}/files`, for these files. */
export function listing(files: ChatFile[], over: Partial<ChatFiles> = {}, used = 218): ChatFiles {
  return {
    model: MODEL,
    files,
    request: files.some((f) => f.kind === "document")
      ? { used, window: 32768, reserve: 1024, full: null }
      : null,
    ...over,
  };
}

/** A document source, as an answer lists it: the three-page PDF, sent whole. */
export function source(over: Partial<DocumentSource> = {}): DocumentSource {
  return {
    kind: "document",
    doc_id: "f1",
    name: "report.pdf",
    type: "pdf",
    native: false,
    partial: false,
    tokens: 77,
    replayed: false,
    page_start: 1,
    page_end: 3,
    pages_total: 3,
    line_start: null,
    line_end: null,
    lines_total: null,
    ...over,
  };
}

/** The first 290 lines of a 900-line text, the part that was chosen. */
export function partSource(over: Partial<DocumentSource> = {}): DocumentSource {
  return source({
    doc_id: "f2",
    name: "log.txt",
    type: "text",
    partial: true,
    tokens: 2997,
    page_start: null,
    page_end: null,
    pages_total: null,
    line_start: 1,
    line_end: 290,
    lines_total: 900,
    fraction: 0.3222,
    ...over,
  });
}
