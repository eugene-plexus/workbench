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
export const OWNER_READS = "The owner of this Workbench can read your chats.";
