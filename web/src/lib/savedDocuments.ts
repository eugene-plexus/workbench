/**
 * What the Documents screen, a chat's Documents and the passages under an answer
 * say, in one place (MR4). Plain words: "Documents", "passages", "words only".
 * How a file is read and searched underneath is never on the way a person reads
 * it (words.test.ts holds the files that write it to that).
 *
 * The server is the authority on every number and on the sentence for a document
 * it skipped or could not read, and for why a chat sends nothing: those are shown
 * as they came. What is composed here is composed only from the fields it sent,
 * so a number on the screen is the number the server counted.
 */

import { foundByWords, meaningRow, unavailableWords } from "./documentMeaning";
import { count } from "./documents";
import { type ChipTone, setByWords } from "./memory";
import type {
  ChatDocumentsView,
  DocumentsPage,
  DocumentsPrefs,
  ExternalChoice,
  HiddenPassage,
  MeaningRun,
  PassageSource,
  SavedDocument,
  SavedState,
  SearchRecord,
  TestedPassage,
  TestedQuestion,
} from "./types";

export { type ChipTone, setByWords };

// --- reading: where a document stands ---

/** Where a document's reading ends: it is read, or it could not be. */
const FINISHED: readonly SavedState[] = ["ready", "skipped", "failed"];

/** A document that is not finished: it is asked about again until none is. A
 * document that can be found by words but is owed more work is one of these too. */
export const isPending = (doc: SavedDocument): boolean => !FINISHED.includes(doc.state);

/** A document that is waiting to be read, or being read. */
export const isBeingRead = (doc: SavedDocument): boolean =>
  doc.state === "queued" || doc.state === "reading";

/** The first read of the list comes soon, because most files are read in a moment. */
export const FIRST_POLL_MS = 500;
export const POLL_MS = 1000;
/** After a read that failed, a pause before the next. */
export const RETRY_MS = 3000;
/** While meaning waits for the search model or for the key's limit to ease, which the
 * server looks at again every few seconds itself. */
export const WAITING_POLL_MS = 5000;
/** When nothing is going on: the header is a live look at the search model, so it is read
 * again each half minute for as long as the screen is open (design 4.2). */
export const IDLE_POLL_MS = 30_000;

/** What the screen is waiting on, which sets how soon it asks again: documents being read
 * or being given their meaning (`busy`), meaning that waits for the search model or for the
 * key's limit, or a result sorter that is being measured (`waiting`), or nothing (`idle`). */
export type Watching = "busy" | "waiting" | "idle";

export function watching(page: DocumentsPage): Watching {
  const docs = page.documents;
  if (docs.some(isBeingRead)) return "busy";
  const held = (doc: SavedDocument) =>
    doc.meaning?.state === "waiting" || doc.meaning?.state === "limited";
  const owing = docs.filter(isPending);
  const header = page.search.adding?.state;
  // Passages read while no search model ran are taken up as soon as one is running.
  const takenUp = page.search.meaning && docs.some((doc) => doc.meaning?.state === "words_only");
  if (owing.some((doc) => !held(doc)) || header === "working" || takenUp) return "busy";
  // A sorter that has not been measured is measured behind the look that found it: the screen
  // says so and asks again in a few seconds, not in half a minute, so what was measured is shown.
  const measuring = page.search.sort?.code === "measuring";
  if (owing.length > 0 || header === "waiting" || header === "limited" || measuring) {
    return "waiting";
  }
  return "idle";
}

export type Tone = "quiet" | "busy" | "ok" | "warn" | "error";

export interface StateWords {
  state: SavedState;
  tone: Tone;
  /** What the document's line says. For a document that was skipped or could not
   * be read it is the server's sentence, which names the cause. */
  label: string;
}

/** The server's sentence for a document it ended without text: it starts with
 * `Skipped:` or `Failed:`; if one came without, the word is put in front, so the
 * line always says which it is. */
function endedWords(doc: SavedDocument): string {
  const said = (doc.message ?? "").trim();
  const word = doc.state === "skipped" ? "Skipped" : "Failed";
  if (!said) {
    return doc.state === "skipped"
      ? "Skipped: Workbench did not read this file."
      : "Failed: Workbench could not read this file.";
  }
  return new RegExp(`^${word}\\b`, "i").test(said) ? said : `${word}: ${said}`;
}

export function stateWords(doc: SavedDocument): StateWords {
  const state = doc.state;
  switch (state) {
    case "queued":
      return { state, tone: "quiet", label: "Waiting to be read" };
    case "reading":
      return {
        state,
        tone: "busy",
        label: doc.pages != null ? `Reading… ${count(doc.pages, "page")}` : "Reading…",
      };
    case "ready": {
      // Ready, and what is true of its meaning when that is more than "Ready".
      const more = meaningRow(doc);
      return { state, tone: "ok", label: more ? more.label : "Ready" };
    }
    case "skipped":
      return { state, tone: "warn", label: endedWords(doc) };
    case "failed":
      return { state, tone: "error", label: endedWords(doc) };
    default: {
      // Found by words, and owed its meaning before it is ready: the server says how that is going.
      const more = meaningRow(doc);
      return more
        ? { state, tone: more.tone, label: more.label }
        : { state, tone: "ok", label: "Ready for words" };
    }
  }
}

/** A size as the server says it (`files.describe_size`): `312 MiB`, `4.5 MiB`, `800 KiB`. */
export function sizeWords(bytes: number): string {
  const mib = 1024 * 1024;
  if (bytes >= mib) {
    const value = bytes / mib;
    return `${value >= 10 || Number.isInteger(value) ? Math.round(value) : value.toFixed(1)} MiB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

/** The day a document was added: `Oct 11`, with the year when it is not this one. */
export function addedWords(seconds: number | null, now = new Date()): string | null {
  if (seconds == null) return null;
  const date = new Date(seconds * 1000);
  const day = date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `Added ${day}`;
}

/** What a document is: `PDF · 1.2 MiB · 5 pages · 12 passages`. Its passages are
 * what a chat can be sent, so they are counted once it can be found. */
export function docMeta(doc: SavedDocument): string {
  const parts: string[] = [];
  if (doc.label) {
    // A language is what the file's name says it is written in.
    const language = doc.language;
    parts.push(
      language && (doc.kind === "code" || doc.kind === "text")
        ? `${doc.label} (${language})`
        : doc.label,
    );
  }
  parts.push(sizeWords(doc.size));
  if (doc.pages != null) parts.push(count(doc.pages, "page"));
  else if (doc.lines != null) parts.push(count(doc.lines, "line"));
  if (doc.searchable && doc.passages > 0) parts.push(count(doc.passages, "passage"));
  return parts.join(" · ");
}

/** The totals at the foot of the list: `3 documents · 128 passages · 4.2 MiB`. */
export function totalsWords(totals: DocumentsPage["totals"]): string {
  return [
    count(totals.documents, "document"),
    count(totals.passages, "passage"),
    sizeWords(totals.bytes),
  ].join(" · ");
}

/** The caps, stated: what one file may be, how many passages a person keeps, what
 * is read, and how long a file may take. */
export function capsWords(page: Pick<DocumentsPage, "limits" | "totals">): string[] {
  const { limits, totals } = page;
  return [
    `A file may be up to ${sizeWords(limits.maxFileBytes)}.`,
    `Workbench keeps up to ${limits.maxPassages.toLocaleString()} passages for you (${totals.passagesKept.toLocaleString()} used).`,
    "It reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
    `It stops reading a file that takes longer than ${count(limits.readTimeoutSeconds, "second")}.`,
  ];
}

export const OWNER_OFF = "The owner of this Workbench has turned off documents.";
export const OWNER_OFF_EFFECT = "No document is searched, and no file can be added.";

/** What words only means, said so it does not promise more: for as long as the
 * server says it is not searching by meaning. */
export const WORDS_ONLY_HELP =
  "Passages are found by the words in your question, spelled the same: “costs” does not find “cost”. It does not look for meaning.";

// --- the person's own settings ---

export const EXTERNAL_CHOICES: { value: ExternalChoice; label: string; help: string }[] = [
  {
    value: "ask",
    label: "Ask me in each chat",
    help: "Passages stay on your machines until you choose Send for this chat.",
  },
  {
    value: "never",
    label: "Never send them",
    help: "Passages only go to models that run on your own machines.",
  },
  {
    value: "always",
    label: "Always send them",
    help: "Passages go to every model, wherever it runs.",
  },
];

export function externalLabel(choice: ExternalChoice): string {
  return EXTERNAL_CHOICES.find((c) => c.value === choice)?.label ?? choice;
}

/** What a new chat starts with, as it is now. */
export function newChatsWords(prefs: DocumentsPrefs): string {
  return prefs.effective.newChats
    ? "New chats start with Documents on. Turn it off in any chat with its Documents button."
    : "New chats start with Documents off. Turn it on in any chat with its Documents button.";
}

// --- adding files ---

/** The file input's picker: documents only. A file dropped is not limited by this;
 * the server says what it takes. */
export { DOCUMENT_ACCEPT } from "./documents";

export type UploadState = "waiting" | "adding" | "added" | "refused" | "failed";

export interface Upload {
  key: string;
  name: string;
  size: number;
  state: UploadState;
  /** How much of the file has gone, 0 to 1. */
  progress: number;
  /** For a file that was not added, the server's words; for one that was, what
   * became of a file like it already there. */
  message: string | null;
}

/** What a row of the upload list says. */
export function uploadWords(upload: Upload): string {
  switch (upload.state) {
    case "waiting":
      return "Waiting";
    case "adding":
      return `Adding… ${Math.round(upload.progress * 100)}%`;
    case "added":
      return upload.message ?? "Added. Workbench is reading it.";
    case "refused":
    case "failed":
      return upload.message ?? "Not added.";
  }
}

/** The line over the list while files are being added, and after. */
export function uploadSummary(uploads: Upload[]): string {
  const added = uploads.filter((u) => u.state === "added").length;
  const left = uploads.filter((u) => u.state === "waiting" || u.state === "adding").length;
  const refused = uploads.filter((u) => u.state === "refused" || u.state === "failed").length;
  if (left > 0) return `Adding ${uploads.length - left + 1} of ${uploads.length}…`;
  const said: string[] = [];
  if (added > 0 || refused === 0) said.push(`Added ${count(added, "file")}.`);
  if (refused > 0)
    said.push(`${count(refused, "file")} ${refused === 1 ? "was" : "were"} not added.`);
  return said.join(" ");
}

/** What the server said of a file in a request it refused in full. */
export function refusedBy(failure: unknown, name: string): { message: string; status: number } {
  const error = failure as { status?: unknown; detail?: unknown; message?: unknown } | null;
  const detail = error?.detail as { refused?: { name?: string; message?: string }[] } | undefined;
  const refused = detail?.refused;
  const message =
    refused?.find((r) => r.name === name)?.message ??
    refused?.[0]?.message ??
    (typeof error?.message === "string" ? error.message : String(failure));
  return { message, status: typeof error?.status === "number" ? error.status : 0 };
}

// --- a damaged or unavailable search ---

/** What the server said when it could not read the documents: its words, its code
 * and whether the search can be made again. Null for any other failure. */
export function blockedBy(
  failure: unknown,
): { message: string; code: string | null; canRebuild: boolean } | null {
  const error = failure as { status?: unknown; detail?: unknown; message?: unknown } | null;
  if (!error || error.status !== 503) return null;
  const detail = (error.detail ?? {}) as {
    message?: unknown;
    code?: unknown;
    canRebuild?: unknown;
  };
  return {
    message: typeof detail.message === "string" ? detail.message : String(error.message ?? ""),
    code: typeof detail.code === "string" ? detail.code : null,
    canRebuild: detail.canRebuild === true,
  };
}

/** What a rebuild came to. */
export function rebuiltWords(made: { documents: number; left: number }): string {
  const said = [
    made.documents === 0
      ? "Workbench found no files to make the search from."
      : `Workbench is making the search again from ${count(made.documents, "document")}. They are read again one at a time.`,
  ];
  if (made.left > 0) {
    said.push(
      `${count(made.left, "file")} could not be made into ${made.left === 1 ? "a document" : "documents"}.`,
    );
  }
  return said.join(" ");
}

// --- where in a file, and which words ---

/** `page 4`, `pages 4 to 5`, `lines 12 to 40`, or null when the file has neither. */
export function placeWords(
  pageStart: number | null,
  pageEnd: number | null,
  lineStart: number | null,
  lineEnd: number | null,
): string | null {
  if (pageStart != null) {
    return pageEnd != null && pageEnd !== pageStart
      ? `pages ${pageStart} to ${pageEnd}`
      : `page ${pageStart}`;
  }
  if (lineStart != null) {
    return lineEnd != null && lineEnd !== lineStart
      ? `lines ${lineStart.toLocaleString()} to ${lineEnd.toLocaleString()}`
      : `line ${lineStart.toLocaleString()}`;
  }
  return null;
}

const titled = (parts: (string | null | undefined)[]): string =>
  parts.filter((part): part is string => Boolean(part)).join(", ");

/** A passage under an answer: `report.pdf, page 4, Observations > Harbour`. */
export function passageTitle(source: PassageSource): string {
  return titled([
    source.name,
    placeWords(source.page_start, source.page_end, source.line_start, source.line_end),
    source.heading,
  ]);
}

/** A passage `Test a question` found, named the same way. */
export function testedTitle(hit: TestedPassage): string {
  return titled([
    hit.name,
    placeWords(hit.pageStart, hit.pageEnd, hit.lineStart, hit.lineEnd),
    hit.heading,
  ]);
}

/** The words a passage was found by: `code, word, “code word”`, or null. */
export function matchedWords(terms: string[], phrases: string[]): string | null {
  const said = [...terms, ...phrases.map((phrase) => `“${phrase}”`)];
  return said.length > 0 ? said.join(", ") : null;
}

export interface Segment {
  text: string;
  hit: boolean;
}

const escaped = (word: string): string => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` cut into the stretches that are one of `words` and those that are not, so
 * the words a passage was found by can be marked where they are. A word matches
 * whole, in any case (the search does not stem). */
export function marked(text: string, words: string[]): Segment[] {
  const list = [...new Set(words.map((word) => word.trim()).filter(Boolean))].sort(
    (a, b) => b.length - a.length,
  );
  if (list.length === 0 || !text) return [{ text, hit: false }];
  const edge = "[\\p{L}\\p{N}_]";
  const pattern = new RegExp(`(?<!${edge})(?:${list.map(escaped).join("|")})(?!${edge})`, "giu");
  const out: Segment[] = [];
  let at = 0;
  for (const found of text.matchAll(pattern)) {
    const start = found.index ?? 0;
    if (start > at) out.push({ text: text.slice(at, start), hit: false });
    out.push({ text: found[0], hit: true });
    at = start + found[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at), hit: false });
  return out.length > 0 ? out : [{ text, hit: false }];
}

// --- Test a question ---

/** What a tried question came to, in one sentence (the passages follow it). */
export function testedWords(result: TestedQuestion): string {
  const { searched } = result;
  switch (result.status) {
    case "found": {
      const tokens = result.passages.reduce((sum, hit) => sum + hit.tokens, 0);
      return `Workbench would send ${count(result.passages.length, "passage")} with this question, about ${count(tokens, "token")}. No model was asked.`;
    }
    case "nothing_matched":
      return "Nothing in your documents matched, so Workbench would send no passages. No model was asked.";
    case "no_words":
      return "There are no words in that question to search for. Try a few of the words the file uses.";
    case "no_documents":
      return searched.waiting > 0
        ? `None of your documents can be searched yet: ${count(searched.waiting, "document")} ${searched.waiting === 1 ? "is" : "are"} still being read.`
        : "You have no documents that can be searched yet.";
    case "unavailable":
      // None of the ways the person asked for could run: said, never "nothing matched".
      return unavailableWords(result.meaning);
  }
}

/** How much was searched: `Searched 3 documents (128 passages).` */
export function searchedCounts(result: Pick<TestedQuestion, "searched">): string {
  const { documents, passages, waiting } = result.searched;
  const said = [`Searched ${count(documents, "document")} (${count(passages, "passage")}).`];
  if (waiting > 0) {
    said.push(
      `${count(waiting, "document")} ${waiting === 1 ? "is" : "are"} still being read, so not searched yet.`,
    );
  }
  return said.join(" ");
}

/** The words that were searched for. */
export function searchedFor(terms: string[], phrases: string[]): string | null {
  const said = matchedWords(terms, phrases);
  return said ? `Searched for: ${said}` : null;
}

// --- a chat's Documents: the chip ---

/** The view a server answered a chat's documents with, or null when it answered with
 * something else: it tells the page nothing, and the chip never claims what it did not read. */
export function readView(value: unknown): ChatDocumentsView | null {
  const view = value as Partial<ChatDocumentsView> | null | undefined;
  if (!view || typeof view !== "object" || typeof view.chatId !== "string") return null;
  if (view.switch !== "on" && view.switch !== "off") return null;
  if (view.readOnly === true) return view as ChatDocumentsView;
  const counts = (view as { counts?: { library?: unknown; chat?: unknown } }).counts;
  return counts && typeof counts === "object" && counts.library && counts.chat
    ? (view as ChatDocumentsView)
    : null;
}

/** What the chip in a chat's header says, and how it looks. `short` is what a
 * narrow window keeps beside the glyph; the full words stay its name. */
export function chatChipWords(
  view: ChatDocumentsView | null,
  failed = false,
): { label: string; short: string; tone: ChipTone } {
  if (!view) {
    return failed
      ? { label: "Documents: not available", short: "", tone: "warn" }
      : { label: "Documents", short: "", tone: "quiet" };
  }
  if (view.readOnly) {
    return view.switch === "on"
      ? { label: "Documents on", short: "On", tone: "quiet" }
      : { label: "Documents off", short: "Off", tone: "quiet" };
  }
  if (view.sending) {
    const { library, chat } = view.counts;
    const here =
      (view.searching.library ? library.documents : 0) + (view.searching.chat ? chat.documents : 0);
    if (here === 0) {
      const waiting =
        (view.searching.library ? library.waiting : 0) + (view.searching.chat ? chat.waiting : 0);
      return {
        label: waiting > 0 ? "Documents: being read" : "Documents: none yet",
        short: "0",
        tone: "quiet",
      };
    }
    return {
      label: `Documents (${here.toLocaleString()})`,
      short: here.toLocaleString(),
      tone: "on",
    };
  }
  switch (view.reasonCode) {
    case "install_off":
    case "chat_off":
      return { label: "Documents off", short: "Off", tone: "quiet" };
    case "no_model":
      return { label: "Documents: choose a model", short: "", tone: "quiet" };
    case "outside_model":
      // A model the gateway cannot place is not said to be outside: it is not known.
      return {
        label:
          view.model?.locality === "unknown"
            ? "Documents not sent: model's location unknown"
            : "Documents not sent: outside model",
        short: "",
        tone: "warn",
      };
    case "window_too_small":
      return { label: "Documents not sent: small window", short: "", tone: "warn" };
    case "model_unknown":
      return { label: "Documents not sent", short: "", tone: "warn" };
    default:
      return { label: "Documents", short: "", tone: "quiet" };
  }
}

/** What the owner, reading someone else's chat, is told: whether Documents was on,
 * and nothing of what is in them. */
export function readOnlyWords(view: Extract<ChatDocumentsView, { readOnly: true }>): string {
  const how = view.switch === "on" ? "on" : "off";
  return `Documents are ${how} in this chat${view.documentsOnly ? ", and it answers only from them" : ""}.`;
}

/** What is there to search, in the drawer: the person's documents, and this chat's own files. */
export function countsWords(view: Extract<ChatDocumentsView, { readOnly: false }>): string[] {
  const { library, chat } = view.counts;
  const said: string[] = [];
  const ready = (n: number, passages: number) =>
    `${count(n, "document")} ready to search (${count(passages, "passage")})`;
  said.push(
    library.documents > 0 || library.waiting === 0
      ? `Your documents: ${ready(library.documents, library.passages)}${library.waiting > 0 ? `, ${library.waiting.toLocaleString()} more being read` : ""}.`
      : `Your documents: ${count(library.waiting, "document")} being read.`,
  );
  if (chat.documents > 0 || chat.waiting > 0) {
    said.push(
      `Files of this chat you chose to search: ${chat.documents > 0 ? ready(chat.documents, chat.passages) : "none ready yet"}${chat.waiting > 0 ? `, ${chat.waiting.toLocaleString()} more being read` : ""}.`,
    );
  }
  return said;
}

/** The room passages may take: the most tokens, of the model's window when it is known. */
export function budgetWords(budget: { limit: number; window: number | null }): string {
  return budget.window != null
    ? `Passages may take up to ${count(budget.limit, "token")} of this model's ${budget.window.toLocaleString()}-token window, and fewer when the chat is already long.`
    : `Passages may take up to ${count(budget.limit, "token")}, and fewer when the chat is already long.`;
}

/** The hint in the composer: the person has documents to ask about and this chat has them
 * off. One short line, so on a phone it stays one line beside its two buttons. */
export function hintWords(documents: number): string {
  return `Your ${documents === 1 ? "document is" : `${documents.toLocaleString()} documents are`} off here.`;
}

/** How many documents the hint speaks of, or null when there is no hint: the person has
 * some that can be searched now, and this chat has Documents off (and the owner has
 * not turned them off for everyone). */
export function hintCount(view: ChatDocumentsView | null): number | null {
  if (!view || view.readOnly) return null;
  if (view.switch === "on" || view.reasonCode === "install_off") return null;
  return view.counts.library.documents > 0 ? view.counts.library.documents : null;
}

const HINT_KEY = "workbench-documents-hint";

/** Whether this person has hidden the hint. A browser that will not say has not. */
export function hintHidden(person: string): boolean {
  try {
    return window.localStorage.getItem(`${HINT_KEY}:${person}`) === "hidden";
  } catch {
    return false;
  }
}

/** Hide the hint for this person, in this browser. It is a convenience: where it
 * cannot be kept, the hint is hidden until the page is opened again. */
export function hideHint(person: string): void {
  try {
    window.localStorage.setItem(`${HINT_KEY}:${person}`, "hidden");
  } catch {
    // Remembering that it was hidden is a convenience.
  }
}

// --- under an answer ---

/** Whether a source is a passage found in saved documents. */
export const isHidden = (source: PassageSource | HiddenPassage): source is HiddenPassage =>
  "redacted" in source && source.redacted === true;

/** `Sent to the model: 3 passages from 2 documents`: what was sent, never what the
 * model used. */
export function sentWords(passages: number, documents: number): string {
  return `Sent to the model: ${count(passages, "passage")} from ${count(documents, "document")}`;
}

/** How the passages were found, when the record says, and by which search model when
 * meaning ran. Nothing for a way it does not know. */
export function modeWords(mode: string, meaning: MeaningRun | null = null): string | null {
  return foundByWords(mode, meaning);
}

/** What did not fit: matched, and left out for want of room. */
export function leftOutWords(didNotFit: number): string | null {
  return didNotFit > 0
    ? `${count(didNotFit, "more passage")} matched but did not fit the room this chat had.`
    : null;
}

/** Words for a search that was not run or failed: the server's, or a plain one if
 * it sent none. */
export function ranWords(record: SearchRecord): string {
  if (record.message) return record.message;
  switch (record.status) {
    case "nothing_matched":
      return "Nothing in your documents matched.";
    case "failed":
      return "Searching your documents failed, so this answer used none. See Workbench's log.";
    case "no_documents":
      return "You have no documents to search yet. Add some in Documents, then ask again.";
    default:
      return "Your documents were not searched for this answer.";
  }
}

/** A failure the person can mend in Documents: the search index is damaged, and
 * Workbench can make it again from their files. */
export const canRebuild = (record: SearchRecord): boolean =>
  record.status === "failed" && record.code === "index_damaged";

/** The hint the server kept with a note about why documents were held back. */
export function hintFor(record: SearchRecord): string | null {
  const noted = record.notes.find((note) => note.code === record.code && note.hint);
  return noted?.hint ?? null;
}

/** A passage as one line of text, for a chat exported as Markdown. */
export function passageLine(source: PassageSource | HiddenPassage): string {
  if (isHidden(source)) return "Passage sent to the model: a document";
  return `Passage sent to the model: ${passageTitle(source)}${source.cited ? " (cited in the answer)" : ""}`;
}
