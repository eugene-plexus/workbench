/**
 * Long chats (row 26): which runs of a chat's messages its model was sent as summaries, and
 * what the chat drawer says of how full the chat is. Workbench never changes a message: a run
 * the model now gets as a summary folds away, and Show them unfolds the originals.
 */

import type { LongChatView, Message, SummaryRecord } from "./types";

/** A run of the path shown folded: where it starts and ends, and the summary it was sent as. */
export interface Fold extends SummaryRecord {
  /** Positions in the messages shown, from 0. */
  start: number;
  end: number;
}

/** The runs the newest answer that went through a plan was sent as summaries, where both ends
 * are on the path shown, in order, never overlapping. None while no answer was. */
export function foldsOf(messages: Message[]): Fold[] {
  const latest = [...messages].reverse().find((m) => m.role === "assistant" && m.summaries != null);
  if (!latest?.summaries) return [];
  const at = new Map(messages.map((m, i) => [m.id, i]));
  const out: Fold[] = [];
  for (const record of latest.summaries) {
    const start = at.get(record.firstId);
    const end = at.get(record.lastId);
    if (start === undefined || end === undefined || start > end) continue;
    out.push({ ...record, start, end });
  }
  out.sort((a, b) => a.start - b.start);
  return out.filter((fold, i) => i === 0 || fold.start > out[i - 1]!.end);
}

/** The fold a message is in, by its id; undefined when it is in none. */
export function foldHolding(folds: Fold[], messages: Message[], id: string | null) {
  if (!id) return undefined;
  const index = messages.findIndex((m) => m.id === id);
  return folds.find((f) => index >= f.start && index <= f.end);
}

/** "Written by qwen3-8b on 21 Oct from about 18,400 tokens; 1,240 tokens." */
export function writtenWords(fold: {
  model: string;
  madeAt: number;
  sourceTokens: number;
  tokens: number;
  cut?: boolean;
}): string {
  const day = new Date(fold.madeAt * 1000).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
  const cut = fold.cut ? " It was cut at its limit." : "";
  return `Written by ${fold.model} on ${day} from about ${fold.sourceTokens.toLocaleString()} tokens; ${fold.tokens.toLocaleString()} tokens.${cut}`;
}

/** What an answer was sent of earlier messages: "A summary of messages 3-18 (1,240 tokens)". */
export function sentWords(records: SummaryRecord[]): string[] {
  return records.map(
    (r) => `A summary of messages ${r.from}-${r.to} (${r.tokens.toLocaleString()} tokens)`,
  );
}

/** The drawer's line: how full the chat is, where it is summarised, and how many summaries. */
export function fullnessWords(view: LongChatView): string {
  if (view.window == null && view.trigger == null) {
    return "This model's window is not known. Workbench summarises only when the model says the chat is too long.";
  }
  const parts: string[] = [];
  if (view.about != null) {
    parts.push(
      view.window != null
        ? `About ${view.about.toLocaleString()} of ${view.window.toLocaleString()} tokens`
        : `About ${view.about.toLocaleString()} tokens`,
    );
  }
  if (view.on && view.trigger != null) {
    parts.push(`summarises at ${view.trigger.toLocaleString()}`);
  }
  parts.push(`${view.summaries} ${view.summaries === 1 ? "summary" : "summaries"}`);
  return parts.join(" · ");
}

/** Whose word the share is, when it is not the install's own. */
export function percentWords(view: LongChatView): string | null {
  if (view.ceiling != null) {
    return `This model runs outside your machines: it is summarised at ${view.ceiling.toLocaleString()} tokens.`;
  }
  if (view.percentSetBy === "group") {
    return `Your group sets summarising at ${view.percent}% of the window.`;
  }
  return null;
}
