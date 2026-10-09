/**
 * What Workbench says, in one place: plain words (Troy, C3 call 1), and
 * Eugene's own terms where they are Eugene's (model, key, gateway).
 */

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
      return message.finish === "length" ? "The answer reached its length limit." : null;
  }
}

export const SEARCH_LABEL = "Search the web";
export const SEARCH_HINT =
  "Search the web before answering. The words searched for go to the internet.";
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
    ? `Eugene switched to developer mode on ${when}. Its owner can now see what job sites return in chats made from now on.`
    : `Eugene switched to production mode on ${when}. Its owner can no longer see what job sites return.`;
}
export function redactedNote(site: string): string {
  return `The rest of this chat used files on ${site}. It is private in production mode.`;
}

/** Under an edit: the edit is a new version, and nothing is replaced (V3). */
export const EDIT_NOTE =
  "Your earlier version and what followed it are kept. Use the arrows to go back.";

/** How to save or leave an edit from the keyboard. */
export const EDIT_KEYS = "Ctrl+Enter saves; Escape cancels.";

/** Why the arrows wait: a running answer stays where its Stop is. */
export const VERSIONS_WAIT = "Wait for the answer, or stop it.";

/** The line an export adds when the chat has other branches. */
export const EXPORT_BRANCHES_NOTE =
  "Exported as shown; other versions of some messages are not included.";
