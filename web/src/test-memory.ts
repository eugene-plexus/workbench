/**
 * What the Memory tests share: notes, a chat's notes and the page's data as
 * the server answers with them (src/eugene_plexus_workbench/memory_api.py).
 * Read only by tests; the page never imports it.
 */

import type {
  ChatMemory,
  ChatMemoryReadOnly,
  MemoryNote,
  MemoryPage,
  MemoryPrefs,
  NotSentNote,
  SentNote,
} from "./lib/types";

export const NOW = Date.UTC(2026, 9, 11, 15, 0, 0) / 1000;

export function note(over: Partial<MemoryNote> = {}): MemoryNote {
  const kind = over.kind ?? "fact";
  const pinned = over.pinned ?? false;
  return {
    id: "n1",
    text: "I use metric units",
    kind,
    pinned,
    always: pinned || kind === "instruction",
    source: "typed",
    sourceChatId: null,
    sourceMessageId: null,
    createdAt: NOW - 3600,
    updatedAt: NOW - 3600,
    lastUsedAt: null,
    useCount: 0,
    ...over,
  };
}

export function prefs(
  over: Partial<Omit<MemoryPrefs, "effective">> & {
    effective?: Partial<MemoryPrefs["effective"]>;
  } = {},
): MemoryPrefs {
  const { effective, ...rest } = over;
  return {
    enabled: true,
    external: null,
    budgetTokens: null,
    ...rest,
    effective: {
      enabled: true,
      disabledBy: null,
      external: "ask",
      externalSetBy: "default",
      budgetTokens: 1000,
      ...effective,
    },
  };
}

/** `GET /api/memory`, from the notes a person has. */
export function page(
  notes: MemoryNote[] = [],
  over: Partial<Omit<MemoryPage, "sections" | "usage">> & {
    usage?: Partial<MemoryPage["usage"]>;
  } = {},
): MemoryPage {
  const { usage, ...rest } = over;
  return {
    installEnabled: true,
    ownerReadsChats: false,
    prefs: prefs(),
    sections: {
      always: notes.filter((n) => n.always),
      other: notes.filter((n) => !n.always),
    },
    usage: {
      tokens: notes.length ? 150 + notes.length * 12 : 0,
      budget: 1000,
      notes: notes.length,
      maxNotes: 300,
      sent: notes.length,
      notSent: 0,
      ...usage,
    },
    limits: {
      maxChars: 240,
      maxNotes: 300,
      kinds: ["fact", "preference", "instruction", "project", "event"],
      minWindow: 4096,
      budgetCeiling: 1000,
      budgetSharePercent: 8,
      budgetFloor: 200,
    },
    ...rest,
  };
}

export function sentNote(over: Partial<SentNote> = {}): SentNote {
  const kind = over.kind ?? "fact";
  const pinned = over.pinned ?? false;
  return {
    id: "n1",
    text: "I use metric units",
    kind,
    pinned,
    always: pinned || kind === "instruction",
    ...over,
  };
}

export function notSent(over: Partial<NotSentNote> = {}): NotSentNote {
  return {
    ...note(over),
    reason: "over_budget",
    reasonText: "Did not fit this model's memory budget (655 tokens).",
    ...over,
  };
}

export const BLOCK =
  "What you know about this person (notes they wrote or approved):\nAbout them\n- I use metric units\n\nUse a note only when it helps with what they are asking now.";

/** A chat's drawer when its notes are being sent: one note, taken at 15:00. */
export function sending(over: Partial<ChatMemory> = {}): ChatMemory {
  return {
    chatId: "c1",
    readOnly: false,
    sending: true,
    reason: null,
    reasonCode: null,
    hint: null,
    chatSwitch: "on",
    model: { id: "qwen3-8b", window: 32768, locality: "local", runsOn: null },
    outside: null,
    counts: { notes: 1, sent: 1, notSent: 0 },
    frozen: true,
    frozenAt: NOW,
    changed: false,
    budget: 655,
    tokens: 162,
    text: BLOCK,
    sent: [sentNote()],
    notSent: [],
    ...over,
  };
}

/** A chat's drawer when nothing is sent, and why. */
export function held(
  reasonCode: NonNullable<ChatMemory["reasonCode"]>,
  reason: string,
  over: Partial<ChatMemory> = {},
): ChatMemory {
  return sending({
    sending: false,
    reasonCode,
    reason,
    hint: null,
    counts: { notes: 1, sent: 0, notSent: 0 },
    frozen: false,
    frozenAt: null,
    budget: null,
    tokens: 0,
    text: null,
    sent: [],
    ...over,
  });
}

export function owners(over: Partial<ChatMemoryReadOnly> = {}): ChatMemoryReadOnly {
  return {
    chatId: "c1",
    readOnly: true,
    frozen: true,
    frozenAt: NOW,
    tokens: 162,
    budget: 655,
    counts: { sent: 2, notSent: 0 },
    ...over,
  };
}
