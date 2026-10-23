/**
 * What Workbench says, in one place: plain words (Troy, C3 call 1), and
 * Eugene's own terms where they are Eugene's (model, key, gateway).
 */

import { elapsedWords } from "./media";
import type { Message, Model, Progress } from "./types";

/** `32k`, as the console says it (ui/src/lib/starter.ts `contextLabel`). */
export function contextLabel(tokens: number): string {
  if (tokens >= 1024 && tokens % 1024 === 0) return `${tokens / 1024}k`;
  return tokens.toLocaleString();
}

/** `qwen3-14b · 32k context`, the console's wording for a served model. */
export function modelLabel(model: Model): string {
  const context =
    model.contextLength != null && model.contextLength > 0
      ? `${contextLabel(model.contextLength)} context`
      : "context unknown";
  return `${model.id} · ${context}`;
}

/** What a model takes besides text, for the picker. */
export function takes(model: Model): string[] {
  const out: string[] = [];
  if (model.imageInput) out.push("images");
  if (model.fileInput) out.push("PDFs");
  if (model.audioInput) out.push("audio");
  return out;
}

/** What an answer that has not started is waiting on, said plainly. */
export function progressWords(progress: Progress | null | undefined): string {
  if (!progress) return "Waiting for the model…";
  // The answer is held for its files to be read (MR2), or for the person's
  // documents to be searched (MR4): not the model's wait.
  if (progress.stage === "documents") {
    return progress.phase === "searching" ? "Searching your documents…" : "Reading your files…";
  }
  // A long chat's earlier messages are summarised before it is sent (row 26).
  if (progress.stage === "summarising") {
    const n = progress.messages ?? 0;
    return `Summarising ${n} earlier message${n === 1 ? "" : "s"} to fit ${progress.model ?? "the model"}'s window…`;
  }
  if (progress.stage === "tool") {
    if (progress.phase === "approval") return "Review the tool calls to continue.";
    if (progress.tool !== "web_search") return `Using ${progress.tool}…`;
    return progress.phase === "finished" ? "Reading what the search found…" : "Searching the web…";
  }
  if (progress.stage === "prompt" && progress.prompt_tokens) {
    const total = progress.prompt_tokens - (progress.cached_tokens ?? 0);
    const done = (progress.processed_tokens ?? 0) - (progress.cached_tokens ?? 0);
    if (total > 0 && done > 0) {
      const share = Math.min(100, Math.round((100 * done) / total));
      return `Reading your message: ${share}%`;
    }
    return "Reading your message…";
  }
  return "The model is working…";
}

/**
 * What an answer still being written is doing (2026-10-10, Troy: it said
 * "Waiting for the model" through minutes of reasoning). Thinking, or
 * writing the answer, with the backend's own count of tokens where it
 * gives one, and otherwise how long the answer has run. Never an estimate.
 * `now` is in seconds, as `createdAt` is.
 */
export function workingWords(
  progress: Progress | null | undefined,
  message: Pick<Message, "content" | "reasoning" | "createdAt">,
  now: number,
): string {
  if (progress && progress.stage !== "generating" && progress.stage !== "working") {
    return progressWords(progress);
  }
  const doing = message.content ? "Writing the answer" : message.reasoning ? "Thinking" : null;
  const written = progress?.stage === "generating" ? progress.generated_tokens : undefined;
  if (written) {
    const speed = progress?.tokens_per_second;
    return `${doing ?? "Writing"} · ${written.toLocaleString()} tokens${speed ? ` · ${speed.toFixed(1)} tok/s` : ""}`;
  }
  if (doing) return `${doing} · ${elapsedWords(Math.max(0, now - message.createdAt))}`;
  return progressWords(progress?.stage === "working" ? progress : null);
}

/**
 * A working line split into what is said and what ticks: "Thinking · 1 s" is
 * said once as "Thinking", and the tail (" · 1 s", ": 25%") is what changes
 * every second, so it is shown and not spoken (a polite region whose text
 * changes every second is read out every second).
 */
export function splitWorking(line: string): { say: string; tail: string } {
  const at = line.search(/ · |: (?=[0-9])/);
  return at < 0 ? { say: line, tail: "" } : { say: line.slice(0, at), tail: line.slice(at) };
}

/**
 * What a screen reader is told when the state of the newest answer changes:
 * the phase, and for a stop the cause that was observed. A failure is already
 * a role="alert" and is not said twice.
 */
export function announce(message?: Message): string {
  if (!message || message.role !== "assistant") return "";
  switch (message.status) {
    case "running":
      return message.content
        ? "Writing the answer."
        : message.reasoning
          ? "Thinking."
          : "Waiting for the model.";
    case "done":
      return message.finish === "length"
        ? "Answer finished at its length limit."
        : "Answer finished.";
    case "stopped":
      return message.finish === "repetition_detected"
        ? "Answer stopped because it was repeating."
        : "Answer stopped.";
    case "interrupted":
      return "Answer interrupted: Workbench restarted while it was being written.";
    default:
      return "";
  }
}

/**
 * A reply split where its last web search fell (workbench#1). A model told
 * to search can write a whole answer first; that text is a draft, shown
 * folded, and the answer is what it wrote after reading the results. A reply
 * that ended before writing anything after its search is all answer.
 */
export function answerParts(message: Message): { draft: string; answer: string } {
  const at = message.answerFrom;
  if (at == null) return { draft: "", answer: message.content };
  const answer = message.content.slice(at).replace(/^\s+/, "");
  if (!answer && message.status !== "running") return { draft: "", answer: message.content };
  return { draft: message.content.slice(0, at).trim(), answer };
}

/** The reasoning before and after the last web search, without empty parts. */
export function reasoningParts(message: Message): string[] {
  const at = message.reasoningFrom;
  if (at == null) return [message.reasoning];
  return [message.reasoning.slice(0, at), message.reasoning.slice(at)].filter((p) => p.trim());
}

export const DRAFT_LABEL = "Written before searching";
export const DRAFT_HINT = "The model wrote this before it read what the search found.";
export const SEARCHED_MARK = "Searched the web";

/**
 * Why an answer stopped at a length, naming the setting that made it when
 * the gateway said (2026-10-10: a Strata answer stopped at the install's
 * 4,096 and this told Troy to raise his chat's own setting).
 */
export function lengthLimitNote(message: Pick<Message, "outputCap">): string {
  const cap = message.outputCap;
  if (!cap) {
    return "The answer reached a length limit: an output cap, the model's context window, or its backend's own limit. Ask it to continue.";
  }
  const tokens = cap.tokens.toLocaleString();
  switch (cap.source) {
    case "request":
      return `The answer reached this chat's “Longest answer (tokens)”, ${tokens}. Raise it in Chat settings, or ask it to continue.`;
    case "profile":
      return `The answer reached the ${tokens}-token cap on this model's Library profile (Maximum output tokens, in the console). Change it there, or ask it to continue.`;
    case "install":
      return `The answer reached the install's ${tokens}-token output cap (Default max output tokens, in the console's gateway settings). Clear it there for no cap, or ask it to continue.`;
  }
}

/** Why an answer is not a finished one, or null when it is. */
export function statusWords(message: Message): string | null {
  switch (message.status) {
    case "stopped":
      if (message.finish === "repetition_detected") {
        return "Stopped because the response appears to be repeating. Your partial answer is kept. To request intentional repetition, turn protection off in Chat settings.";
      }
      return "Stopped.";
    case "interrupted":
      return "Workbench restarted while this was being written, so it ends here.";
    case "failed":
      return message.error ?? "This answer failed.";
    default:
      return message.finish === "length" ? lengthLimitNote(message) : null;
  }
}

export const SEARCH_LABEL = "Search the web";
/** Where the search words go: said on the screen while the switch is on, and
 * not only in the switch's tooltip, which a phone never shows. */
export const SEARCH_PRIVACY = "The words searched for go to the internet.";
export const SEARCH_HINT = `Search the web before answering. ${SEARCH_PRIVACY}`;
export const OWNER_READS = "The owner of this Workbench can read your chats and media.";
export const PRODUCTION_MODE = "Production mode";
export const DEV_MODE = "Developer mode";
export const PRODUCTION_MODE_EXPLAINED =
  "Eugene's owner cannot see what your job sites' tools return.";
export const DEV_MODE_EXPLAINED =
  "Eugene's owner can see all tool information, including what your job sites return.";
export function modeChanged(mode: "production" | "dev", at: string): string {
  const when = new Date(at).toLocaleString();
  return mode === "dev"
    ? `Eugene switched to developer mode on ${when}. Its owner can see what job sites return in new chats.`
    : `Eugene switched to production mode on ${when}. Its owner can no longer see what job sites return.`;
}
export function redactedNote(site: string): string {
  return `The rest of this chat used files on ${site}. It is private in production mode.`;
}

/** Under an edit: the edit is a new version, and nothing is replaced (V3). */
export const EDIT_NOTE =
  "Your earlier version and what followed it are kept. Use the arrows to go back.";

/** How to save or leave an edit from the keyboard. */
export const EDIT_KEYS = "Ctrl+Enter (Cmd+Enter on a Mac) saves; Escape cancels.";

/** Why the arrows wait: a running answer stays where its Stop is. */
export const VERSIONS_WAIT = "Wait for the answer, or stop it.";

/** The line an export adds when the chat has other branches. */
export const EXPORT_BRANCHES_NOTE =
  "Exported as shown; other versions of some messages are not included.";
