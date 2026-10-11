/**
 * What Documents say, in one place (MR2). Plain words: "files", "pages",
 * "tokens", "window". How a file is read underneath is never on the way a
 * person reads it (words.test.ts holds the files that write it to that).
 *
 * The server is the authority on every number, and on the sentence for a file
 * it skipped or could not read: that one is shown as it came. What is composed
 * here is composed only from the fields the server sent, so a number on the
 * screen is the number the decision was made on.
 */

import type {
  AttachedFile,
  ChatFile,
  FileDecision,
  DocumentSource,
  FilesModel,
  FilesRequest,
  Offer,
  Reading,
  ReadingChoice,
  Source,
  WebSource,
} from "./types";

// --- what the file picker is asked to show ---

/** Text files Workbench reads, by their names (`files.py`: Markdown, CSV, HTML,
 * code and data). A file's bytes decide what it is and the name only picks
 * among kinds of text; these are the names the picker is asked to show.
 * tests/test_documents_page.py keeps this list equal to the server's. */
// prettier-ignore
export const KNOWN_EXTENSIONS = [
  ".asm", ".bash", ".bat", ".c", ".cc", ".cfg", ".cjs", ".clj", ".cljs", ".cmake", ".cmd",
  ".conf", ".cpp", ".cs", ".css", ".csv", ".cxx", ".dart", ".erl", ".ex", ".exs", ".fish",
  ".fs", ".fsx", ".go", ".gradle", ".graphql", ".groovy", ".h", ".hh", ".hpp", ".hs", ".htm",
  ".html", ".ini", ".java", ".jl", ".js", ".json", ".jsonl", ".jsx", ".kt", ".kts", ".less",
  ".lua", ".m", ".markdown", ".md", ".mdown", ".mjs", ".mkd", ".mm", ".ndjson", ".nim", ".php",
  ".pl", ".pm", ".properties", ".proto", ".ps1", ".psm1", ".py", ".pyi", ".r", ".rb", ".rs",
  ".s", ".sass", ".scala", ".scss", ".sh", ".sol", ".sql", ".svelte", ".svg", ".swift", ".tab",
  ".tf", ".toml", ".ts", ".tsv", ".tsx", ".vb", ".vue", ".xhtml", ".xml", ".xsl", ".yaml",
  ".yml", ".zig", ".zsh",
] as const;

/** Text with no kind of its own: the server reads any text as text. */
export const PLAIN_EXTENSIONS = [".txt", ".text", ".log"] as const;

const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/** What the Attach button's file picker shows: images, audio, PDF, Word and
 * text. Dropping a file is not limited by this; the server says what it takes. */
export const ACCEPT = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "application/pdf",
  "audio/wav",
  "audio/x-wav",
  "audio/mpeg",
  DOCX,
  ".docx",
  "text/*",
  ...PLAIN_EXTENSIONS,
  ...KNOWN_EXTENSIONS,
].join(",");

// --- what a file is, and where it stands ---

/** A file Workbench reads as a document (the server's word for it). */
export const isDocument = (file: ChatFile): boolean => file.kind === "document";

/** Still being read: or being read again, because its text went missing. */
export const isReading = (file: ChatFile): boolean =>
  file.reading?.state === "reading" || file.decision?.kind === "pending";

/** Whether a file can go with the message as things stand. */
export const canGo = (file: ChatFile): boolean =>
  file.decision ? file.decision.send : !isReading(file);

export type ChipState =
  | "reading"
  | "whole"
  | "partial"
  | "native"
  | "choose"
  | "too_long"
  | "no_room"
  | "skipped"
  | "failed";

/** Which of its looks a file's chip has. */
export function chipState(file: ChatFile): ChipState {
  const decision = file.decision;
  if (decision) {
    switch (decision.kind) {
      case "pending":
        return "reading";
      case "unreadable":
        return file.reading?.state === "skipped" ? "skipped" : "failed";
      default:
        return decision.kind;
    }
  }
  switch (file.reading?.state) {
    case "skipped":
      return "skipped";
    case "failed":
      return "failed";
    case "ready":
      return "whole";
    default:
      return "reading";
  }
}

/** `1 page`, `12 pages`, with thousands grouped. */
export const count = (n: number, one: string, many = `${one}s`): string =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** `about 8,400 tokens`. */
export const about = (tokens: number): string =>
  `about ${tokens.toLocaleString()} ${tokens === 1 ? "token" : "tokens"}`;

/** What Workbench found in a file: `PDF · 3 pages · about 77 tokens`. */
export function metaWords(file: ChatFile): string {
  const reading = file.reading;
  const parts: string[] = [];
  const label = reading?.label ?? (file.mediaType === "application/pdf" ? "PDF" : null);
  if (label) {
    // A language is what the file's name says it is written in.
    const language = reading?.language;
    parts.push(
      language && (reading?.kind === "code" || reading?.kind === "text")
        ? `${label} (${language})`
        : label,
    );
  }
  if (reading?.pages != null) parts.push(count(reading.pages, "page"));
  else if (reading?.lines != null) parts.push(count(reading.lines, "line"));
  if (reading?.tokens != null) parts.push(about(reading.tokens));
  return parts.join(" · ");
}

/** A choice, as a person would say it. */
export function choiceWords(choice: ReadingChoice): string {
  return choice.mode === "whole"
    ? "read it all"
    : `the first ${choice.tokens.toLocaleString()} tokens`;
}

/** The button for something a person may choose. */
export function offerWords(offer: Offer): string {
  return offer.action === "readAll"
    ? `Read it all (${about(offer.tokens)})`
    : `Read the first ${offer.tokens.toLocaleString()} tokens (${offer.where})`;
}

/** What choosing an offer says to the server. */
export function offerChoice(offer: Offer): ReadingChoice {
  return offer.action === "readAll" ? { mode: "whole" } : { mode: "first", tokens: offer.tokens };
}

export type Tone = "busy" | "ok" | "warn" | "error";

/** What a chip says of its file, apart from its name and numbers. */
export interface ChipWords {
  state: ChipState;
  tone: Tone;
  /** A notice's heading, for a file that needs the person. */
  title: string | null;
  /** The sentence: composed from the decision's numbers, or the server's own
   * for a file it could not read. */
  sentence: string;
  /** The choice in effect, when the person made one. */
  choice: string | null;
  notes: string[];
  /** What to do about a file that cannot go. */
  hint: string | null;
}

/** The server's own sentence for a decision, when there is one. */
const serverSentence = (file: ChatFile, fallback: string): string =>
  file.decision?.message || file.reading?.message || fallback;

export function chipWords(file: ChatFile, model: FilesModel | null): ChipWords {
  const state = chipState(file);
  const decision = file.decision ?? null;
  const notes = decision ? decision.notes : (file.reading?.notes ?? []);
  const chosen = decision?.chosen ?? file.reading?.chosen ?? null;
  const own =
    chosen && !decision?.choiceNoLongerFits ? `Your choice: ${choiceWords(chosen)}.` : null;
  const size = decision?.window ?? model?.window ?? null;
  const base = { state, notes, choice: null, title: null, hint: null };
  switch (state) {
    case "reading":
      return { ...base, tone: "busy", sentence: decision?.message || "Reading…" };
    case "whole":
      return {
        ...base,
        tone: "ok",
        sentence: decision
          ? chosen?.mode === "whole"
            ? "Will be read whole, as you chose."
            : "Will be read whole."
          : "Read. Workbench checks it against the model when you send.",
        choice: chosen?.mode === "first" ? own : null,
      };
    case "partial": {
      const cut = decision?.coverage;
      return {
        ...base,
        tone: "ok",
        sentence: cut
          ? `Will read ${cut.where} (${about(cut.tokens)}). The rest of the file is not sent.`
          : "Will read only the first part. The rest of the file is not sent.",
        choice: own,
      };
    }
    case "native":
      return {
        ...base,
        tone: "ok",
        sentence: file.reading
          ? `${model?.id ?? "This model"} reads PDFs itself, so Workbench sends the file as it is.`
          : serverSentence(file, "Workbench sends this PDF as the file."),
      };
    case "choose": {
      const limit = decision?.wholeLimit;
      return {
        ...base,
        tone: "warn",
        title: "Choose how much to read",
        sentence: [
          decision?.choiceNoLongerFits && chosen
            ? `Your earlier choice (${choiceWords(chosen)}) no longer fits this model.`
            : null,
          limit != null
            ? `This file is longer than Workbench reads at once by default (${count(limit, "token")}).`
            : "This file is longer than Workbench reads at once by default.",
        ]
          .filter(Boolean)
          .join(" "),
      };
    }
    case "too_long": {
      const room = decision?.readAllLimit;
      const parts = [
        decision?.choiceNoLongerFits && chosen
          ? `Your earlier choice (${choiceWords(chosen)}) no longer fits this model.`
          : null,
        size != null
          ? `This model's window is ${count(size, "token")}, so there is room for ${
              room != null ? `about ${room.toLocaleString()} tokens` : "only part"
            } of this file.`
          : "This file is too long for this model.",
        decision && decision.offers.length === 0
          ? "Pick a model with a larger window, or remove the file."
          : null,
      ];
      return {
        ...base,
        tone: "warn",
        title: "Too long for this model",
        sentence: parts.filter(Boolean).join(" "),
      };
    }
    case "no_room":
      return {
        ...base,
        tone: "warn",
        title: "No room for this file",
        sentence: serverSentence(file, "There is no room for this file in this model's window."),
      };
    case "skipped":
    case "failed":
      return {
        ...base,
        tone: state === "skipped" ? "warn" : "error",
        sentence: serverSentence(file, "Workbench could not read this file."),
        hint: "Remove it to send your message.",
      };
  }
}

// --- why Send waits ---

/** What the page knows of the model's room, from the last read of the files. */
export interface Facts {
  model: FilesModel;
  request: FilesRequest | null;
}

const ATTENTION: Record<ChipState, number> = {
  reading: 0,
  choose: 1,
  too_long: 1,
  skipped: 2,
  failed: 2,
  no_room: 3,
  whole: 9,
  partial: 9,
  native: 9,
};

/** Why Send waits, as one sentence beside it, or null when nothing holds it.
 * A file still being read comes first (what it says may change what the others
 * need), then one that needs a choice, then one that cannot go. */
export function holdWords(
  files: ChatFile[],
  facts: Facts | null,
  checking: boolean,
  modelId: string | null,
): string | null {
  if (checking) return `Checking your files against ${modelId ?? "this model"}…`;
  const blocked = files
    .filter((f) => isDocument(f) && !canGo(f))
    .map((f, order) => ({ f, order, rank: ATTENTION[chipState(f)] }))
    .sort((a, b) => a.rank - b.rank || a.order - b.order);
  const first = blocked[0];
  if (first) {
    const name = first.f.name;
    const sentence = {
      reading: `Waiting for Workbench to read ${name}.`,
      choose: `Choose how much of ${name} to read, or remove it.`,
      too_long: `Choose how much of ${name} to read, or remove it.`,
      skipped: `${name} cannot be read. Remove it to send your message.`,
      failed: `${name} cannot be read. Remove it to send your message.`,
      no_room: `There is no room for ${name} in this model's window. Remove it, or pick a model with a larger window.`,
      whole: `${name} cannot be sent yet.`,
      partial: `${name} cannot be sent yet.`,
      native: `${name} cannot be sent yet.`,
    }[chipState(first.f)];
    const more = blocked.length - 1;
    return more > 0
      ? `${sentence} (${more === 1 ? "1 more file needs" : `${more} more files need`} attention too.)`
      : sentence;
  }
  if (facts?.request?.full) {
    return "This chat and your files are more than this model's window holds. Remove a file, or pick a model with a larger window.";
  }
  return null;
}

/** How much of the model's window the message and the chat come to, in one line. */
export function requestWords(request: FilesRequest | null): string | null {
  if (!request || request.window == null || request.full) return null;
  return `What would be sent now comes to about ${request.used.toLocaleString()} of this model's ${request.window.toLocaleString()} tokens, and ${request.reserve.toLocaleString()} are kept for the answer.`;
}

/** A sentence that ends: the server's words, or an error's, with a full stop if it has none. */
export const endSentence = (text: string): string => (/[.!?…]$/.test(text) ? text : `${text}.`);

/** What the page says when Eugene does not give the model's window. */
export const WINDOW_UNKNOWN =
  "Eugene does not say how large this model's window is, so Workbench cannot check that your files fit.";

/** What a screen reader is told when a file stops being read, or turns up already
 * read: the file's name, and its sentence. */
export function announceWords(file: ChatFile, model: FilesModel | null): string {
  const words = chipWords(file, model);
  return `${file.name}: ${words.title ? `${words.title}. ` : ""}${words.sentence}`;
}

// --- a refusal from the server that names files ---

export interface RefusedFile {
  id: string;
  name: string;
  decision: FileDecision | null;
}

export interface Refusal {
  message: string;
  files: RefusedFile[];
}

/** What a send (`documents`) or a choice (`choice`) was refused for, read off an
 * `ApiError` without importing it (a test that replaces the module has no class
 * to test against). Null for any other failure. */
export function refusalFor(error: unknown, code: "documents" | "choice"): Refusal | null {
  const found = error as { status?: unknown; detail?: unknown } | null;
  if (!found || found.status !== 409 || !found.detail || typeof found.detail !== "object") {
    return null;
  }
  const detail = found.detail as Record<string, unknown>;
  if (detail.code !== code) return null;
  const files = Array.isArray(detail.files) ? detail.files : [];
  return {
    message:
      typeof detail.message === "string" ? detail.message : "Your files cannot go as they are.",
    files: files.flatMap((item): RefusedFile[] => {
      const row = item as { id?: unknown; name?: unknown; decision?: unknown } | null;
      if (!row || typeof row.id !== "string" || typeof row.name !== "string") return [];
      const decision =
        row.decision && typeof row.decision === "object" ? (row.decision as FileDecision) : null;
      return [{ id: row.id, name: row.name, decision }];
    }),
  };
}

// --- a file, as a message carries it ---

/** What a message keeps of a file it carried. */
export function asAttached(file: ChatFile): AttachedFile {
  return { id: file.id, name: file.name, mediaType: file.mediaType, reading: file.reading ?? null };
}

// --- the sources under an answer ---

export const isDocumentSource = (source: Source): source is DocumentSource =>
  source.kind === "document";
export const isWebSource = (source: Source): source is WebSource => source.kind !== "document";

const grouped = (n: number): string => n.toLocaleString();

/** Which part of a file went, as the server counted it: `pages 1 to 3 of 3`,
 * `lines 1 to 290 of 900`, `whole`, or `sent as a file` for a model that reads
 * PDFs itself. */
export function sourceWhere(source: DocumentSource): string {
  if (source.native) return "sent as a file";
  const { page_start: first, page_end: last, pages_total: pages } = source;
  if (first != null && last != null && pages != null) {
    return first === last ? `page ${first} of ${pages}` : `pages ${first} to ${last} of ${pages}`;
  }
  const { line_start: from, line_end: to, lines_total: lines } = source;
  if (from != null && to != null && lines != null) {
    return `lines ${grouped(from)} to ${grouped(to)} of ${grouped(lines)}`;
  }
  if (source.partial && source.fraction != null) {
    return `the first ${Math.round(source.fraction * 100)} percent`;
  }
  return "whole";
}

/** What else is worth saying of a source, beside where: only the first part,
 * attached earlier in the chat. */
export function sourceTags(source: DocumentSource): string[] {
  return [source.partial ? "first part" : null, source.replayed ? "attached earlier" : null].filter(
    (tag): tag is string => tag !== null,
  );
}

/** A document source as one line of text, for a chat exported as Markdown. */
export function sourceLine(source: DocumentSource): string {
  const tags = sourceTags(source);
  return `${source.name}, ${sourceWhere(source)}${tags.length ? ` (${tags.join(", ")})` : ""}`;
}

/** The reading of a file a person's own message carried, for its small print. */
export function readingMeta(reading: Reading | null | undefined): string {
  return metaWords({ id: "", name: "", mediaType: "", reading });
}
