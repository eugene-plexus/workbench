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
    return progress.tool === "web_search" ? "Searching the web…" : `Using ${progress.tool}…`;
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
