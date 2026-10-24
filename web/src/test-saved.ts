/**
 * What the saved-documents tests share: documents, the screen's data, a chat's
 * documents, the passages under an answer and a question tried, as the server answers
 * with them (src/eugene_plexus_workbench/saved_api.py). The numbers are the ones its own
 * test world produced: a five-page survey whose code word is on page 4, and a Word
 * file. Read only by tests; the page never imports it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import type {
  AddingView,
  ChatDocuments,
  ChatDocumentsReadOnly,
  DocumentMeaning,
  DocumentsPage,
  DocumentsPrefs,
  HiddenPassage,
  MeaningRun,
  PassageSource,
  SavedDocument,
  SearchAction,
  SearchCode,
  SearchFacts,
  SearchModelView,
  SearchRecord,
  SortCode,
  SortFacts,
  SortRun,
  TestedPassage,
  TestedQuestion,
} from "./lib/types";

/** 2026-10-11 15:00 UTC, in epoch seconds: the day the documents were added. */
export const ADDED = Date.UTC(2026, 9, 11, 15, 0, 0) / 1000;

export function saved(over: Partial<SavedDocument> = {}): SavedDocument {
  return {
    readBy: null,
    id: "d1",
    name: "harbour-survey.pdf",
    mediaType: "application/pdf",
    kind: "pdf",
    label: "PDF",
    language: null,
    scope: "library",
    size: 9_400,
    pages: 5,
    lines: null,
    tokens: 1_480,
    state: "ready",
    searchable: true,
    message: null,
    code: null,
    passages: 5,
    embedded: 0,
    notes: [],
    addedAt: ADDED,
    readAt: ADDED + 1,
    indexedAt: ADDED + 1,
    meaning: null,
    ...over,
  };
}

// --- search by meaning (MR5) ---

/** A document's meaning, as `Meaning.document_view` says it: every passage of a ready one. */
export function meaningOf(over: Partial<DocumentMeaning> = {}): DocumentMeaning {
  return {
    embedded: 5,
    total: 5,
    etaSeconds: null,
    refused: 0,
    notes: [],
    state: "ready",
    message: null,
    ...over,
  };
}

/** A document that has all its meaning. */
export const withMeaning = (over: Partial<SavedDocument> = {}) =>
  saved({ embedded: 5, meaning: meaningOf(), ...over });

/** A document that can be found by words and is having its meaning added (the server's own sentence). */
export const adding = (over: Partial<SavedDocument> = {}) =>
  saved({
    state: "embedding",
    embedded: 2,
    meaning: meaningOf({
      embedded: 2,
      state: "adding",
      etaSeconds: 840,
      message: "Ready for words. Adding meaning: 2 of 5 passages, about 14 minutes left.",
    }),
    ...over,
  });

/** One that waits for the search model. */
export const waiting = (over: Partial<SavedDocument> = {}) =>
  saved({
    state: "searchable",
    meaning: meaningOf({
      embedded: 0,
      state: "waiting",
      message:
        "Ready for words. Meaning is waiting: the search model qwen3-embedding is not running. Start it in Eugene's console under Backends.",
    }),
    ...over,
  });

/** One held back by the key's limit. */
export const limited = (over: Partial<SavedDocument> = {}) =>
  saved({
    state: "embedding",
    embedded: 2,
    meaning: meaningOf({
      embedded: 2,
      state: "limited",
      message:
        "Ready for words. Adding meaning to your documents is slow right now: this Workbench's key is at its limit (HTTP 429) and chats use it too. It will carry on by itself. The owner can raise the key's limits in Eugene's console.",
    }),
    ...over,
  });

/** One read while no search model was running. */
export const wordsOnlyDoc = (over: Partial<SavedDocument> = {}) =>
  saved({ meaning: meaningOf({ embedded: 0, state: "words_only" }), ...over });

/** The search model in use, as `Meaning.status` says it. */
export function modelView(over: Partial<SearchModelView> = {}): SearchModelView {
  return {
    setting: "auto",
    automatic: true,
    resolved: "Qwen3-Embedding-0.6B-Q8_0",
    label: "In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)",
    locality: "local",
    runsOn: null,
    allowExternal: false,
    outside: [],
    builtWith: "Qwen3-Embedding-0.6B-Q8_0",
    dimension: 1024,
    prefixes: {
      known: true,
      family: "qwen3-embedding",
      query:
        "Instruct: Given a question, retrieve passages from the person's documents that answer it\nQuery:",
      document: "",
      note: null,
    },
    floor: 0.5,
    ...over,
  };
}

/** How adding meaning is going: nothing to add. */
export function addingView(over: Partial<AddingView> = {}): AddingView {
  return {
    state: "idle",
    message: null,
    passages: 5,
    embedded: 5,
    owed: 0,
    wordsOnly: 0,
    etaSeconds: null,
    ratePerSecond: null,
    ...over,
  };
}

/** A Workbench with no gateway to ask about a search model: words only, and nothing to say why.
 * It has no gateway to ask about a result sorter either, which the server says as `sort: null`. */
export function wordsOnly(over: Partial<SearchFacts> = {}): SearchFacts {
  return {
    mode: "words",
    words: true,
    meaning: false,
    headline: "Searching by words only.",
    code: "no_gateway",
    detail: null,
    actions: [],
    model: null,
    adding: null,
    rebuild: null,
    sort: null,
    ...over,
  };
}

/** No search model has been set up. */
export const noSearchModel = (over: Partial<SearchFacts> = {}) =>
  wordsOnly({
    headline: "Searching by words only. No search model is running.",
    code: "none_set_up",
    detail: "Searching by words only: no search model is set up.",
    actions: ["get_search_model"],
    ...over,
  });

/** Meaning and words: a search model is in use. */
export const byMeaning = (over: Partial<SearchFacts> = {}): SearchFacts => ({
  mode: "meaning and words",
  words: true,
  meaning: true,
  headline: "Searching by meaning and words.",
  code: "ok",
  detail: null,
  actions: [],
  model: modelView(),
  adding: addingView(),
  rebuild: null,
  // A gateway to ask, and no result sorter set up: which is quiet, and says nothing on the screen.
  sort: noSorter(),
  ...over,
});

/** This search was built with one search model and another is running now. */
export const paused = (over: Partial<SearchFacts> = {}) =>
  wordsOnly({
    headline:
      "Search by meaning is paused: this search was built with Qwen3-Embedding-0.6B-Q8_0 and nomic-embed-text-v1.5 is running now.",
    code: "changed",
    detail:
      "Your documents are searched by words until you choose Rebuild, which adds meaning again with the model that is running.",
    actions: ["rebuild"],
    model: modelView({
      resolved: "nomic-embed-text-v1.5",
      label: "In use: nomic-embed-text-v1.5 (automatic)",
    }),
    rebuild: { passages: 3000, seconds: 720, model: "nomic-embed-text-v1.5" },
    ...over,
  });

/** What the header says for each code, as the server's own functions make it: the headline, the
 * detail and what a person can do (`tests/fixtures/search_codes.json`, which the server's tests
 * hold to what it says: tests/test_meaning_page.py). */
const SAYS = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../tests/fixtures/search_codes.json"), "utf8"),
) as Record<SearchCode, { headline: string; detail: string | null; actions: SearchAction[] }>;

/** Every code the server can say. */
export const CODES = Object.keys(SAYS) as SearchCode[];

/** The header for a code, in the server's own words. */
export function searchFor(code: SearchCode, over: Partial<SearchFacts> = {}): SearchFacts {
  const said = SAYS[code];
  if (code === "ok") return byMeaning(over);
  const rebuild = said.actions.includes("rebuild");
  return wordsOnly({
    headline: said.headline,
    code,
    detail: said.detail,
    actions: said.actions,
    // A paused search has the search model that is running now, and what Rebuild would do.
    model: rebuild
      ? modelView({
          resolved: "nomic-embed-text-v1.5",
          label: "In use: nomic-embed-text-v1.5 (automatic)",
        })
      : null,
    rebuild: rebuild ? { passages: 3000, seconds: 720, model: "nomic-embed-text-v1.5" } : null,
    ...over,
  });
}

/** What a search by meaning came to for a question: it was not asked for. */
export function meaningRun(over: Partial<MeaningRun> = {}): MeaningRun {
  return {
    used: false,
    code: "not_asked",
    message: null,
    quiet: true,
    model: null,
    best: null,
    floor: null,
    belowFloor: false,
    ...over,
  };
}

/** Meaning ran, with this search model. */
export const ranByMeaning = (over: Partial<MeaningRun> = {}) =>
  meaningRun({
    used: true,
    code: "ok",
    model: "Qwen3-Embedding-0.6B-Q8_0",
    best: 0.74,
    floor: 0.5,
    ...over,
  });

// --- the result sorter (MR6b) ---

/** What the server's own functions say for each code the sorter can come to, as the Documents screen
 * and a tried question show it (`tests/fixtures/sort_codes.json`, which the server's tests hold to
 * what it says: tests/test_sorting_page.py). `over_budget` is the screen's (a usual sort) and
 * `over_budget_question` a tried question's (the passages that were found). */
const SORT_SAYS = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../tests/fixtures/sort_codes.json"), "utf8"),
) as Record<string, string>;

/** The sorter as those sentences name it. */
export const SORTER = "Qwen3-Reranker-0.6B-Q8_0";

/** The sentence the server says for a code. */
export function sortSays(code: string): string {
  const said = SORT_SAYS[code];
  if (!said) throw new Error(`the server says nothing for the code ${code}`);
  return said;
}

/** The codes the Documents screen can be told of the sorter. */
export const SORT_CODES: SortCode[] = [
  "ok",
  "measuring",
  "over_budget",
  "off",
  "none_set_up",
  "not_running",
  "not_listed",
  "not_sorter",
  "outside",
  "outside_only",
  "limited",
  "key",
  "unreachable",
  "slow",
  "refused",
  "unusable",
  "error",
];

/** The codes a tried question can come to when its passages were not sorted. */
export const SORT_RUN_CODES: SortCode[] = [
  "over_budget",
  "slow",
  "not_running",
  "refused",
  "limited",
  "key",
  "unreachable",
  "unusable",
  "error",
  "off",
  "none_set_up",
  "not_listed",
  "not_sorter",
  "outside",
  "outside_only",
  "nothing_to_sort",
  "no_question",
  "not_asked",
  "none",
];

/** A sort of the usual size: what the Documents screen prices the sorter's speed by. */
const USUAL = { passages: 18, passageTokens: 300, tokens: 5_760 };

/** The sorter, as the Documents screen is told of it (`Standing.view`) while it is in use and a
 * sort of the usual size is quick enough: automatic, on the install's own machines, measured by
 * a quick test at about 4,100 tokens a second, so that a usual sort adds 1.4 s. */
export function sortFacts(over: Partial<SortFacts> = {}): SortFacts {
  return {
    setting: "auto",
    automatic: true,
    sorting: true,
    code: "ok",
    message: null,
    quiet: false,
    model: SORTER,
    named: null,
    label: `In use: ${SORTER} (automatic)`,
    locality: "local",
    runsOn: null,
    allowExternal: false,
    outside: [],
    budgetSeconds: 3,
    ratePerSecond: 4_100,
    rateFrom: "probe",
    typical: { ...USUAL, seconds: 1.4 },
    ...over,
  };
}

/** No sorter set up: never in play, so quiet, and the screen says nothing of one. */
export const noSorter = (over: Partial<SortFacts> = {}): SortFacts =>
  sortFacts({
    sorting: false,
    code: "none_set_up",
    message: sortSays("none_set_up"),
    quiet: true,
    model: null,
    label: null,
    locality: null,
    ratePerSecond: null,
    rateFrom: null,
    typical: { ...USUAL, seconds: null },
    ...over,
  });

/** What the Documents screen is told for a code, in the server's own words. */
export function sortFor(code: SortCode, over: Partial<SortFacts> = {}): SortFacts {
  switch (code) {
    case "ok":
      return sortFacts(over);
    case "measuring":
      // Chosen, and being measured: no sentence from the server, and nothing to price it by yet.
      return sortFacts({
        sorting: false,
        code,
        ratePerSecond: null,
        rateFrom: null,
        typical: { ...USUAL, seconds: null },
        ...over,
      });
    case "over_budget":
      // Measured at 820 tokens a second, so that a usual sort adds about 7 s: over Sort time, 3 s.
      return sortFacts({
        sorting: false,
        code,
        message: sortSays("over_budget"),
        ratePerSecond: 820,
        typical: { ...USUAL, seconds: 7.02 },
        ...over,
      });
    default:
      return noSorter({
        code,
        message: sortSays(code),
        quiet: code === "off" || code === "none_set_up",
        ...over,
      });
  }
}

/** What a tried question says of sorting when no sorter was in play: quiet, and no words. */
export function sortRun(over: Partial<SortRun> = {}): SortRun {
  return {
    used: false,
    code: "none_set_up",
    message: sortSays("none_set_up"),
    quiet: true,
    model: null,
    setting: "auto",
    passages: 0,
    tokens: null,
    ratePerSecond: null,
    expectedSeconds: null,
    budgetSeconds: 3,
    seconds: null,
    ...over,
  };
}

/** A question whose passages were put in a sorter's order: 18 of them, in 1.4 s. */
export const sortedRun = (over: Partial<SortRun> = {}) =>
  sortRun({
    used: true,
    code: null,
    message: null,
    quiet: false,
    model: SORTER,
    passages: 18,
    tokens: 5_760,
    ratePerSecond: 4_100,
    expectedSeconds: 1.4,
    seconds: 1.4,
    ...over,
  });

/** What a tried question says for a code, in the server's own words: for the codes that were
 * never in play it is quiet. */
export function sortRunFor(code: SortCode, over: Partial<SortRun> = {}): SortRun {
  if (code === "over_budget") {
    return sortRun({
      code,
      message: sortSays("over_budget_question"),
      quiet: false,
      model: SORTER,
      passages: 18,
      tokens: 5_760,
      ratePerSecond: 820,
      expectedSeconds: 7.02,
      ...over,
    });
  }
  const never = ["off", "none_set_up", "nothing_to_sort", "no_question", "not_asked", "none"];
  return sortRun({
    code,
    message: SORT_SAYS[code] ?? null,
    quiet: never.includes(code),
    ...over,
  });
}

/** Waiting its turn to be read. */
export const queued = (over: Partial<SavedDocument> = {}) =>
  saved({
    id: "d2",
    name: "notes.md",
    mediaType: "text/markdown",
    kind: "markdown",
    label: "Markdown",
    pages: null,
    lines: null,
    tokens: null,
    state: "queued",
    searchable: false,
    passages: 0,
    readAt: null,
    indexedAt: null,
    ...over,
  });

/** Being read now. */
export const reading = (over: Partial<SavedDocument> = {}) => queued({ state: "reading", ...over });

/** A Markdown file, read. */
export const markdown = (over: Partial<SavedDocument> = {}) =>
  saved({
    id: "d3",
    name: "field-notes.md",
    mediaType: "text/markdown",
    kind: "markdown",
    label: "Markdown",
    pages: null,
    lines: 5,
    size: 1_100,
    tokens: 120,
    passages: 1,
    ...over,
  });

/** A Word file, read. */
export const word = (over: Partial<SavedDocument> = {}) =>
  saved({
    id: "d4",
    name: "quarterly-report.docx",
    mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    kind: "docx",
    label: "Word",
    pages: null,
    lines: null,
    size: 36_800,
    tokens: 300,
    passages: 3,
    ...over,
  });

/** A scan: a PDF with no text, skipped with the server's own words. */
export const scanned = (over: Partial<SavedDocument> = {}) =>
  saved({
    id: "d5",
    name: "scan.pdf",
    pages: 2,
    tokens: null,
    state: "skipped",
    searchable: false,
    passages: 0,
    code: "no_text",
    message:
      "Skipped: this PDF has no text, only pictures of pages. Workbench cannot read scanned pages yet.",
    ...over,
  });

/** A file Workbench tried to read and could not. */
export const broken = (over: Partial<SavedDocument> = {}) =>
  saved({
    id: "d6",
    name: "broken.pdf",
    pages: null,
    tokens: null,
    state: "failed",
    searchable: false,
    passages: 0,
    code: "damaged",
    message: "Failed: Workbench could not read this file (the PDF is cut off). It may be damaged.",
    ...over,
  });

export const PREFS: DocumentsPrefs = {
  newChats: false,
  external: null,
  effective: {
    enabled: true,
    disabledBy: null,
    newChats: false,
    external: "ask",
    externalSetBy: "default",
  },
};

export function prefs(
  over: Partial<Omit<DocumentsPrefs, "effective">> & {
    effective?: Partial<DocumentsPrefs["effective"]>;
  } = {},
): DocumentsPrefs {
  const { effective, ...rest } = over;
  return { ...PREFS, ...rest, effective: { ...PREFS.effective, ...effective } };
}

/** `GET /api/documents`, for these documents. */
export function page(
  documents: SavedDocument[] = [],
  over: Partial<Omit<DocumentsPage, "documents">> = {},
): DocumentsPage {
  const byState: Record<string, number> = {
    queued: 0,
    reading: 0,
    searchable: 0,
    embedding: 0,
    ready: 0,
    skipped: 0,
    failed: 0,
  };
  for (const doc of documents) byState[doc.state] = (byState[doc.state] ?? 0) + 1;
  const passages = documents.reduce((sum, doc) => sum + doc.passages, 0);
  // What the server counts: passages that have their meaning, and passages a document that can
  // be found by words is still owed.
  const embedded = documents.reduce((sum, doc) => sum + (doc.meaning?.embedded ?? 0), 0);
  const owed = documents.reduce(
    (sum, doc) =>
      sum +
      (doc.state === "searchable" || doc.state === "embedding"
        ? doc.passages - (doc.meaning?.embedded ?? 0)
        : 0),
    0,
  );
  return {
    documents,
    totals: {
      documents: documents.length,
      passages,
      bytes: documents.reduce((sum, doc) => sum + doc.size, 0),
      byState,
      passagesKept: passages,
      embedded,
      owed,
    },
    limits: {
      maxFileBytes: 50 * 1024 * 1024,
      maxFileMB: 50,
      maxPassages: 100_000,
      readsKinds: ["pdf", "docx", "markdown", "csv", "html", "code", "text"],
      reads:
        "Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files, and sends PNG or JPEG images and WAV or MP3 audio to models that take them.",
      readTimeoutSeconds: 120,
    },
    search: wordsOnly(),
    prefs: PREFS,
    installEnabled: true,
    ownerReadsChats: false,
    ...over,
  };
}

/** A chat whose Documents are on, for a model that runs here: three documents to search. */
export function chatDocuments(over: Partial<ChatDocuments> = {}): ChatDocuments {
  return {
    chatId: "c1",
    readOnly: false,
    switch: "on",
    documentsOnly: false,
    sending: true,
    searching: { library: true, chat: false },
    reason: null,
    reasonCode: null,
    hint: null,
    model: { id: "qwen3-8b", window: 32768, locality: "local", runsOn: null },
    outside: null,
    counts: {
      library: { documents: 3, passages: 9, waiting: 0 },
      chat: { documents: 0, passages: 0, waiting: 0 },
    },
    chatDocuments: [],
    budget: { limit: 3000, window: 32768 },
    search: wordsOnly(),
    ...over,
  };
}

/** A chat that sends nothing, and the server's words for why. */
export function held(
  code: NonNullable<ChatDocuments["reasonCode"]>,
  reason: string,
  over: Partial<ChatDocuments> = {},
): ChatDocuments {
  return chatDocuments({
    sending: false,
    searching: { library: false, chat: false },
    reason,
    reasonCode: code,
    ...over,
  });
}

/** A chat with Documents off. */
export const off = (over: Partial<ChatDocuments> = {}) =>
  held("chat_off", "Documents are off for this chat.", { switch: "off", ...over });

/** The install's owner reading someone else's chat: whether it was on, and nothing else. */
export function owners(over: Partial<ChatDocumentsReadOnly> = {}): ChatDocumentsReadOnly {
  return { chatId: "c1", readOnly: true, switch: "on", documentsOnly: false, ...over };
}

/** A passage under an answer: the survey's page 4, found by words, cited by the answer. */
export function passage(over: Partial<PassageSource> = {}): PassageSource {
  return {
    kind: "document",
    passage: true,
    doc_id: "d1",
    name: "harbour-survey.pdf",
    type: "pdf",
    scope: "library",
    rank: 1,
    via: "words",
    page_start: 4,
    page_end: 4,
    line_start: null,
    line_end: null,
    heading: null,
    snippet:
      "…describes the weather and the tide. The code word for the lighthouse inspection is TAMARIND-6042. Observation 4.4 describes the…",
    chunk_ids: [17],
    tokens: 290,
    terms: ["code", "word", "lighthouse", "inspection"],
    phrases: [],
    cited: true,
    ...over,
  };
}

/** The Word file's conclusion, found by two of the words and not cited. */
export const second = (over: Partial<PassageSource> = {}) =>
  passage({
    doc_id: "d4",
    name: "quarterly-report.docx",
    type: "docx",
    rank: 2,
    page_start: null,
    page_end: null,
    heading: "Conclusion",
    snippet: "The code word is PLUMBAGO-77.",
    chunk_ids: [31],
    tokens: 60,
    terms: ["code", "word"],
    cited: false,
    ...over,
  });

/** What the install's owner sees of a passage. */
export const hidden = (over: Partial<HiddenPassage> = {}): HiddenPassage => ({
  kind: "document",
  passage: true,
  name: "a document",
  scope: "library",
  rank: 1,
  via: "words",
  redacted: true,
  ...over,
});

/** What searching came to for an answer that was sent these passages. */
export function record(over: Partial<SearchRecord> = {}): SearchRecord {
  return {
    status: "used",
    code: null,
    message: null,
    query: "code word lighthouse inspection",
    terms: ["code", "word", "lighthouse", "inspection"],
    phrases: [],
    mode: "words",
    scope: { library: true, chat: false },
    documentsOnly: false,
    askedAnyway: false,
    answeredBy: null,
    passages: 2,
    documents: 2,
    found: 2,
    didNotFit: 0,
    tokens: 350,
    budget: { window: 32768, used: 120, reserve: 1024, limit: 3000, room: 31624, allowed: 3000 },
    searched: { documents: 3, passages: 9, waiting: 0 },
    notes: [],
    cited: [1],
    ms: 4,
    meaning: null,
    ...over,
  };
}

/** A search that found nothing: a normal result. */
export const nothing = (over: Partial<SearchRecord> = {}) =>
  record({
    status: "nothing_matched",
    message: "Nothing in your documents matched.",
    terms: ["zebra", "giraffe", "okapi"],
    query: "zebra giraffe okapi",
    passages: 0,
    documents: 0,
    found: 0,
    tokens: 0,
    cited: [],
    ...over,
  });

/** Why a passage `Test a question` found matched: by its words, unless said. */
export const why = (over: Partial<TestedPassage["why"]> = {}): TestedPassage["why"] => ({
  via: "words",
  terms: [],
  phrases: [],
  inHeading: [],
  ranks: { words: 1 },
  similarity: null,
  ...over,
});

/** A passage `Test a question` found. */
export function hit(over: Partial<TestedPassage> = {}): TestedPassage {
  const found = passage();
  return {
    rank: 1,
    docId: "d1",
    name: "harbour-survey.pdf",
    scope: "library",
    pageStart: 4,
    pageEnd: 4,
    lineStart: null,
    lineEnd: null,
    heading: null,
    snippet: found.snippet,
    text: `Page 4 of the harbour survey records ordinary observations.\n${found.snippet}`,
    tokens: 290,
    chunkIds: [17],
    via: "words",
    why: {
      via: "words",
      terms: found.terms,
      phrases: [],
      inHeading: [],
      ranks: { words: 1 },
      similarity: null,
    },
    ...over,
  };
}

/** A question tried: the survey's page 4 and the Word file's conclusion. */
export function tested(over: Partial<TestedQuestion> = {}): TestedQuestion {
  return {
    status: "found",
    query: {
      text: "code word lighthouse inspection",
      terms: ["code", "word", "lighthouse", "inspection"],
      phrases: [],
    },
    mode: "words",
    meaning: meaningRun(),
    sort: sortRun(),
    passages: [
      hit(),
      hit({
        rank: 2,
        docId: "d4",
        name: "quarterly-report.docx",
        pageStart: null,
        pageEnd: null,
        heading: "Conclusion",
        snippet: "The code word is PLUMBAGO-77.",
        text: "The code word is PLUMBAGO-77.",
        tokens: 60,
        why: {
          via: "words",
          terms: ["code", "word"],
          phrases: [],
          inHeading: [],
          ranks: { words: 2 },
          similarity: null,
        },
      }),
    ],
    found: 2,
    didNotFit: 0,
    budget: {
      window: null,
      used: 0,
      reserve: 1024,
      limit: 3000,
      room: null,
      allowed: 3000,
      block: 90,
      perPassage: 80,
    },
    searched: { documents: 3, passages: 9, waiting: 0 },
    ...over,
  };
}
