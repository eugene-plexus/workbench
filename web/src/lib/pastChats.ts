/**
 * Past chats (MV2): what `GET /api/past-chats` answers, and opening a chat at an exchange.
 */

/** One exchange found: what the person asked and how the answer began. */
export interface PastChatResult {
  chatId: string;
  /** The answer's id: the exchange is known by it. */
  messageId: string;
  title: string;
  date: string;
  question: string;
  answer: string;
  via: "words" | "meaning" | "both";
}

/** How far the person's past chats are in search. */
export interface PastChatsStatus {
  backfill: { running: false } | { running: true; chats: number; done: number };
  exchanges: number | null;
  owed: number | null;
  /** Whether search by meaning can run now, and why not. */
  meaning?: { available: boolean; why: string | null };
  problem?: string;
}

export interface PastChatsSearch {
  results: PastChatResult[];
  total: number;
  how: "words" | "meaning and words" | null;
  why: string | null;
  status: PastChatsStatus;
}

export type When = "any" | "week" | "month" | "year";
export type Order = "best" | "newest";

/** What a chat's drawer says of past chats in it. */
export interface PastChatsState {
  on: boolean;
  allowed: boolean;
  reason: string | null;
  reasonCode: string | null;
  hint: string | null;
  searchTool: boolean;
  budget: number | null;
}

/** What one turn was sent of past chats: which exchanges, never their words. */
export interface PastChatsTurn {
  tokens: number;
  budget: number;
  how: "words" | "meaning and words";
  why: string | null;
  didNotFit: number;
  forgotten?: number;
  exchanges: { chatId: string; messageId: string; title: string; date: string; via: string }[];
}

export const VIA_WORDS: Record<string, string> = {
  words: "Found by words",
  meaning: "Found by meaning",
  both: "Found by meaning and words",
};

/** The address of a chat opened at one exchange. */
export function chatAt(chatId: string, messageId: string): string {
  return `/chats/${encodeURIComponent(chatId)}?at=${encodeURIComponent(messageId)}`;
}

/** Open a chat at one exchange, from anywhere: the page follows the address. */
export function openChatAt(chatId: string, messageId: string): void {
  window.history.pushState(null, "", chatAt(chatId, messageId));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** The exchange an address asks to open at, if any. */
export function atFromSearch(search: string): string | null {
  const at = new URLSearchParams(search).get("at");
  return at && /^[A-Za-z0-9_-]{1,64}$/.test(at) ? at : null;
}

/** What the status line under the search box says, or null for nothing. */
export function statusWords(status: PastChatsStatus): string | null {
  if (status.problem) return `Your past chats cannot be searched right now: ${status.problem}`;
  if (status.backfill.running) {
    const { done, chats } = status.backfill;
    return `Adding your past chats to search: ${done.toLocaleString()} of ${chats.toLocaleString()}.`;
  }
  if (status.meaning && !status.meaning.available) {
    return status.meaning.why ?? "Searching by words: no search model is running.";
  }
  if (status.owed) {
    return `${status.owed.toLocaleString()} ${status.owed === 1 ? "passage is" : "passages are"} still being added to search by meaning.`;
  }
  return null;
}
