/**
 * What Memory says, in one place: plain words ("Memory", "notes"), and the
 * few rules the page keeps that the server also keeps (a note is one line of
 * at most 240 characters). The server is the authority: where a rule is
 * repeated here it is only so the page can say so before a round trip.
 */

import type {
  ChatMemoryReadOnly,
  ChatMemoryView,
  ExternalChoice,
  MemoryNote,
  MemoryPage,
  NoteKind,
  Refusal,
} from "./types";

/** The longest a note may be (memory.py `MAX_NOTE_CHARS`), in characters as
 * the server counts them: code points, so an emoji is one. */
export const NOTE_LIMIT = 240;

/** The most a person may set their own budget to (`PUT /api/memory/prefs`). */
export const BUDGET_MAX = 20000;

/** How long an undo stays on offer, and how long a toast stays up. */
export const UNDO_MS = 10_000;

export const OWNER_OFF = "The owner of this Workbench has turned Memory off.";

/** The sentence the server says when "Remember that ..." finds memory off. */
export const YOU_OFF = "Memory is off for you, so nothing was kept. Turn it on in Memory.";

export const KINDS: { kind: NoteKind; label: string; help: string }[] = [
  { kind: "fact", label: "Fact", help: "Something true about you." },
  { kind: "preference", label: "Preference", help: "Something you like or prefer." },
  { kind: "instruction", label: "Instruction", help: "How you want answers written. Always used." },
  { kind: "project", label: "Project", help: "Something you are working on." },
  { kind: "event", label: "Event", help: "Something with a date." },
  {
    kind: "procedure",
    label: "Procedure",
    help: "Steps, a brief or a checklist. Can be long.",
  },
];

/** The kinds a model may suggest: a procedure is only ever the person's own. */
export const SUGGESTED_KINDS = KINDS.filter((k) => k.kind !== "procedure");

export function kindLabel(kind: NoteKind): string {
  return KINDS.find((k) => k.kind === kind)?.label ?? kind;
}

export const EXTERNAL_CHOICES: { value: ExternalChoice; label: string; help: string }[] = [
  {
    value: "ask",
    label: "Ask me in each chat",
    help: "Notes stay on your machines until you choose Send for this chat.",
  },
  {
    value: "never",
    label: "Never send them",
    help: "Notes only go to models that run on your own machines.",
  },
  {
    value: "always",
    label: "Always send them",
    help: "Notes go to every model, wherever it runs.",
  },
];

export function externalLabel(choice: ExternalChoice): string {
  return EXTERNAL_CHOICES.find((c) => c.value === choice)?.label ?? choice;
}

/** Learn from my chats (MR8): the two choices a person has. */
export const LEARN_CHOICES: { value: "off" | "suggest"; label: string; help: string }[] = [
  { value: "off", label: "Off", help: "Notes are only what you write yourself." },
  {
    value: "suggest",
    label: "Suggest notes",
    help: "When a chat goes quiet, its model may suggest up to three notes from what you wrote. Nothing is kept until you keep it.",
  },
];

/** Which notes a chat is given. */
export const USE_CHOICES: { value: "relevant" | "all"; label: string; help: string }[] = [
  {
    value: "relevant",
    label: "Only what is relevant",
    help: "Instructions and pinned notes, then notes about what the chat starts with. Needs a search model; without one, every note that fits is used.",
  },
  {
    value: "all",
    label: "Everything that fits",
    help: "Every note, newest first, as many as fit the room a model has for notes.",
  },
];

/** Whose word Learn from my chats is, as a sentence that follows it. */
export function learnSetByWords(setBy: "owner" | "you" | "measured" | "default"): string {
  if (setBy === "owner") return "The owner of this Workbench has turned suggestions off.";
  if (setBy === "you") return "You chose this.";
  if (setBy === "measured") return "On because this chat's model passed Eugene's measurement.";
  return "Nobody has chosen, so it is off. It turns on by itself only for models that passed Eugene's measurement of suggestions.";
}

/** Whose word a setting is, as a sentence that follows it. */
export function setByWords(setBy: "owner" | "you" | "default"): string {
  if (setBy === "owner") {
    return "The owner of this Workbench set this, and it cannot be changed here.";
  }
  if (setBy === "you") return "You chose this.";
  return "Nobody has chosen, so the default applies.";
}

/** `1 note`, `3 notes`, with thousands grouped. */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

/** The Memory page's usage line: what the pinned block takes, and what relevant memory may. */
export function usageWords(usage: MemoryPage["usage"]): string {
  const pinned = `Pinned: ${usage.pinned.tokens.toLocaleString()} of ${usage.pinned.budget.toLocaleString()} tokens, in every chat.`;
  const { budget, from, how } = usage.relevant;
  const room = budget.toLocaleString();
  const relevant =
    how === "all"
      ? `Relevant: up to ${room} tokens a turn; every note that fits is used.`
      : `Relevant: up to ${room} tokens a turn, chosen from ${plural(from, "note")} by ${how}.`;
  return `${pinned} ${relevant}`;
}

/** Tags typed in one box, as a list: split at commas, trimmed, empty ones dropped, a repeat
 * (ignoring case) left out. */
export function tagsFrom(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const piece of text.split(",")) {
    const tag = piece.trim();
    const key = tagKey(tag);
    if (!tag || seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
  }
  return out;
}

/** How tags are matched: ignoring case, as the server does. */
export const tagKey = (tag: string): string => tag.normalize("NFKC").toLowerCase();

/** "Showing the newest 200 of 1,204". */
export function showingWords(shown: number, total: number): string {
  return `Showing the newest ${shown.toLocaleString()} of ${total.toLocaleString()}`;
}

/** The text of a file the person chose. */
export function readText(file: Blob): Promise<string> {
  if (typeof file.text === "function") return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("The file could not be read."));
    reader.readAsText(file);
  });
}

/** "Using 412 of 1,000 tokens". */
export function tokenWords(used: number, budget: number): string {
  return `Using ${used.toLocaleString()} of ${budget.toLocaleString()} tokens`;
}

/** The day a note was kept: `Oct 11`, with the year when it is not this one. */
export function noteDate(seconds: number, now = new Date()): string {
  const date = new Date(seconds * 1000);
  return date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/** The time a chat took its notes, as a message's time is said. */
export function noteTime(seconds: number, now = new Date()): string {
  const date = new Date(seconds * 1000);
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (date.toDateString() === now.toDateString()) return time;
  return `${noteDate(seconds, now)}, ${time}`;
}

// --- a note is one line, at most 240 characters ---

/** Characters as the server counts them (code points), not UTF-16 units. */
export function characters(text: string): number {
  return Array.from(text).length;
}

/** The words as one line: runs of spacing and line breaks become one space. */
export function oneLine(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** The first `limit` characters of `text`, ending at a word when there is one
 * near enough, never inside an emoji. */
export function cutNote(text: string, limit = NOTE_LIMIT): string {
  const letters = Array.from(oneLine(text));
  if (letters.length <= limit) return letters.join("");
  const cut = letters.slice(0, limit).join("");
  const at = cut.lastIndexOf(" ");
  return (at > limit * 0.6 ? cut.slice(0, at) : cut).trim();
}

/** What a message would make of itself as a note: one line, and whether that
 * line is too long to keep as it is. */
export function noteFrom(
  text: string,
  limit = NOTE_LIMIT,
): { text: string; length: number; over: boolean } {
  const line = oneLine(text);
  const length = characters(line);
  return { text: line, length, over: length > limit };
}

// --- refusals ---

/** A refusal the server sent, read off an `ApiError` without importing it (a
 * test that replaces the module has no class to test against). Null for any
 * other failure. */
export function refusalOf(error: unknown): Refusal | null {
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const found = detail as Record<string, unknown>;
  if (typeof found.message !== "string") return null;
  return {
    message: found.message,
    reason: typeof found.reason === "string" ? (found.reason as Refusal["reason"]) : undefined,
    looksLike: typeof found.looksLike === "string" ? found.looksLike : undefined,
    limit: typeof found.limit === "number" ? found.limit : undefined,
  };
}

/** The sentence a person reads when words were not kept: the server's, and for
 * a secret what it looks like. */
export function refusalWords(refusal: Refusal): string {
  const said = /[.!?]$/.test(refusal.message) ? refusal.message : `${refusal.message}.`;
  return refusal.looksLike ? `${said} It looks like ${refusal.looksLike}.` : said;
}

/** Any failure, in words: a refusal's own sentence, else what the error said. */
export function failureWords(error: unknown): string {
  const refusal = refusalOf(error);
  if (refusal) return refusalWords(refusal);
  return error instanceof Error ? error.message : String(error);
}

// --- the chip ---

export type ChipTone = "quiet" | "on" | "warn";

/** What the chip in a chat's header says, and how it looks. `short` is what a
 * phone keeps beside the glyph; the full words stay its name. */
export function chipWords(
  view: ChatMemoryView | null,
  failed = false,
): { label: string; short: string; tone: ChipTone } {
  if (!view) {
    return failed
      ? { label: "Memory: not available", short: "", tone: "warn" }
      : { label: "Memory", short: "", tone: "quiet" };
  }
  if (view.readOnly) {
    const used = view.counts.sent;
    return view.frozen && used > 0
      ? { label: `Memory: ${plural(used, "note")} used`, short: String(used), tone: "quiet" }
      : { label: "Memory: no notes used", short: "", tone: "quiet" };
  }
  if (view.sending) {
    const { sent, notSent } = view.counts;
    if (sent === 0) return { label: "Memory: no notes fit", short: "", tone: "warn" };
    const how = notSent > 0 ? `${sent} of ${sent + notSent} notes` : plural(sent, "note");
    return {
      label: `Memory: ${how} ${view.frozen ? "sent" : "ready"}`,
      short: String(sent),
      tone: "on",
    };
  }
  switch (view.reasonCode) {
    case "install_off":
    case "person_off":
      return { label: "Memory off", short: "", tone: "quiet" };
    case "chat_off":
      return { label: "Memory off in this chat", short: "", tone: "quiet" };
    case "no_model":
      return { label: "Memory: choose a model", short: "", tone: "quiet" };
    case "no_notes":
      return { label: "Memory: no notes yet", short: "", tone: "quiet" };
    case "outside_model":
      // A model the gateway cannot place is not said to be outside: it is not known.
      return {
        label:
          view.model?.locality === "unknown"
            ? "Memory not sent: model's location unknown"
            : "Memory not sent: outside model",
        short: "",
        tone: "warn",
      };
    case "window_too_small":
      return { label: "Memory not sent: small window", short: "", tone: "warn" };
    case "model_unknown":
      return { label: "Memory not sent", short: "", tone: "warn" };
    default:
      return { label: "Memory", short: "", tone: "quiet" };
  }
}

/** What the owner, reading someone else's chat, is told of its notes: how many
 * went to the model, and nothing of what they said. */
export function readOnlyWords(view: ChatMemoryReadOnly): string {
  const used = view.frozen ? view.counts.sent : 0;
  if (used === 0) return "No notes went to the model in this chat.";
  const left =
    view.counts.notSent > 0 ? `, and ${plural(view.counts.notSent, "note")} did not fit` : "";
  const room = view.budget ? ` ${tokenWords(view.tokens, view.budget)}.` : "";
  return `${plural(used, "note")} went to the model in this chat${left}.${room}`;
}

/** A note named for a button that acts on it: its first words. */
export function excerpt(note: Pick<MemoryNote, "text">, length = 40): string {
  const letters = Array.from(note.text);
  return letters.length <= length ? note.text : `${letters.slice(0, length).join("").trim()}…`;
}

/** A blob saved as a file, the way the page saves a chat. */
export function saveFile(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
