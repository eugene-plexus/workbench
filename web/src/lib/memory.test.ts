import { held, notSent, owners, sending } from "../test-memory";
import {
  characters,
  chipWords,
  cutNote,
  excerpt,
  externalLabel,
  failureWords,
  kindLabel,
  noteDate,
  noteFrom,
  noteTime,
  NOTE_LIMIT,
  oneLine,
  plural,
  readOnlyWords,
  refusalOf,
  refusalWords,
  setByWords,
  tokenWords,
} from "./memory";
import { ApiError } from "./api";

describe("a note is one line of at most 240 characters", () => {
  it("counts characters as the server does, so an emoji is one", () => {
    expect(characters("I like 🐦")).toBe(8);
    expect("I like 🐦".length).toBe(9);
    // 200 emoji are 400 UTF-16 units but 200 characters: well inside the limit.
    expect(noteFrom("🐦".repeat(200)).over).toBe(false);
    expect(noteFrom("🐦".repeat(241)).over).toBe(true);
    expect(noteFrom("a".repeat(240)).over).toBe(false);
    expect(noteFrom("a".repeat(241))).toMatchObject({ length: 241, over: true });
  });

  it("turns line breaks and runs of spacing into one space", () => {
    expect(oneLine("  I use\n\nmetric\t units  ")).toBe("I use metric units");
    expect(noteFrom("Line one\nLine two").text).toBe("Line one Line two");
  });

  it("is cut at a word when there is one near the end, and never inside an emoji", () => {
    // 240 characters fall inside "Jackdaws": the cut goes back to the end of "quartz.".
    const sentence = "Jackdaws love my big sphinx of quartz. ";
    const cut = cutNote(sentence.repeat(10));
    expect(characters(cut)).toBeLessThanOrEqual(NOTE_LIMIT);
    expect(cut).toBe(sentence.repeat(6).trimEnd());
    // The last space is too far back to be worth it (under 60%): cut where the limit falls.
    const far = cutNote(`${"x".repeat(100)} ${"y".repeat(300)}`);
    expect(far).toBe(`${"x".repeat(100)} ${"y".repeat(139)}`);
    // Nothing to break at: cut where the limit falls, on a whole character.
    const run = cutNote("🐦".repeat(300));
    expect(characters(run)).toBe(NOTE_LIMIT);
    expect(run).toBe("🐦".repeat(NOTE_LIMIT));
    // A short text is only made one line.
    expect(cutNote("Short\nnote")).toBe("Short note");
  });

  it("names a note by its first words, with a mark where it was cut", () => {
    expect(excerpt({ text: "I use metric units" })).toBe("I use metric units");
    const long = excerpt({ text: "x".repeat(100) });
    expect(long).toBe(`${"x".repeat(40)}…`);
    expect(excerpt({ text: "x".repeat(100) }, 10)).toBe(`${"x".repeat(10)}…`);
  });
});

describe("words for what the server refused", () => {
  it("reads a refusal off an error without needing its class", () => {
    const refused = new ApiError(
      "That looks like a password or key, so Workbench will not keep it",
      422,
      {
        message: "That looks like a password or key, so Workbench will not keep it",
        reason: "secret",
        looksLike: "a card number",
      },
    );
    expect(refusalOf(refused)).toEqual({
      message: "That looks like a password or key, so Workbench will not keep it",
      reason: "secret",
      looksLike: "a card number",
      limit: undefined,
    });
    expect(refusalOf(new Error("The network is down."))).toBeNull();
    expect(refusalOf(new ApiError("Workbench answered 502.", 502))).toBeNull();
    expect(refusalOf(null)).toBeNull();
    expect(refusalOf({ detail: { reason: "full" } })).toBeNull();
  });

  it("says what a secret looks like, after the sentence, and ends every sentence", () => {
    expect(
      refusalWords({
        message: "That looks like a password or key, so Workbench will not keep it",
        looksLike: "an API key",
      }),
    ).toBe(
      "That looks like a password or key, so Workbench will not keep it. It looks like an API key.",
    );
    expect(refusalWords({ message: "Write what to remember." })).toBe("Write what to remember.");
    expect(refusalWords({ message: "A note is at most 240 characters; this one is 300." })).toBe(
      "A note is at most 240 characters; this one is 300.",
    );
  });

  it("falls back to what any failure said", () => {
    expect(failureWords(new Error("The disk is full."))).toBe("The disk is full.");
    expect(failureWords("plain")).toBe("plain");
    expect(
      failureWords(new ApiError("x", 422, { message: "Write what to remember.", reason: "empty" })),
    ).toBe("Write what to remember.");
  });
});

describe("small words", () => {
  it("names kinds and choices in plain words", () => {
    expect(kindLabel("instruction")).toBe("Instruction");
    expect(kindLabel("event")).toBe("Event");
    expect(externalLabel("ask")).toBe("Ask me in each chat");
    expect(externalLabel("never")).toBe("Never send them");
    expect(externalLabel("always")).toBe("Always send them");
  });

  it("says whose word a setting is, and never a stand-in", () => {
    expect(setByWords("you")).toBe("You chose this.");
    expect(setByWords("owner")).toContain("The owner of this Workbench set this");
    expect(setByWords("owner")).toContain("cannot be changed here");
    expect(setByWords("default")).toBe("Nobody has chosen, so the default applies.");
  });

  it("counts and groups", () => {
    expect(plural(1, "note")).toBe("1 note");
    expect(plural(3, "note")).toBe("3 notes");
    expect(plural(1234, "note")).toBe("1,234 notes");
    expect(tokenWords(412, 1000)).toBe("Using 412 of 1,000 tokens");
  });

  it("says the day a note was kept, with the year only when it is not this one", () => {
    const now = new Date(2026, 9, 11, 15, 0);
    const at = (y: number, m: number, d: number) => new Date(y, m, d, 9, 5).getTime() / 1000;
    expect(noteDate(at(2026, 9, 3), now)).toMatch(/Oct 3$/);
    expect(noteDate(at(2025, 11, 31), now)).toMatch(/Dec 31, 2025$/);
    expect(noteTime(at(2026, 9, 11), now)).toMatch(/^9:05/);
    expect(noteTime(at(2026, 9, 3), now)).toMatch(/^Oct 3, 9:05/);
  });
});

describe("the chip", () => {
  it("says nothing it has not read: plain Memory while loading, and why when it could not", () => {
    expect(chipWords(null)).toEqual({ label: "Memory", short: "", tone: "quiet" });
    expect(chipWords(null, true)).toEqual({
      label: "Memory: not available",
      short: "",
      tone: "warn",
    });
  });

  it("says how many notes the next message carries, and whether the chat has sent them yet", () => {
    expect(
      chipWords(sending({ frozen: false, counts: { notes: 1, sent: 1, notSent: 0 } })),
    ).toEqual({
      label: "Memory: 1 note ready",
      short: "1",
      tone: "on",
    });
    expect(chipWords(sending({ counts: { notes: 7, sent: 7, notSent: 0 } }))).toEqual({
      label: "Memory: 7 notes sent",
      short: "7",
      tone: "on",
    });
  });

  it("says how many did not fit", () => {
    expect(chipWords(sending({ counts: { notes: 9, sent: 7, notSent: 2 } })).label).toBe(
      "Memory: 7 of 9 notes sent",
    );
    expect(
      chipWords(sending({ frozen: false, counts: { notes: 9, sent: 7, notSent: 2 } })).label,
    ).toBe("Memory: 7 of 9 notes ready");
  });

  it("does not say a model is outside when it is only not known", () => {
    const unplaced = held("outside_model", "Eugene cannot tell where this model runs.", {
      model: { id: "mystery", window: 8192, locality: "unknown", runsOn: null },
    });
    expect(chipWords(unplaced)).toEqual({
      label: "Memory not sent: model's location unknown",
      short: "",
      tone: "warn",
    });
    const outside = held("outside_model", "Your notes were not sent.", {
      model: { id: "gpt", window: 8192, locality: "external", runsOn: "OpenRouter" },
    });
    expect(chipWords(outside).label).toBe("Memory not sent: outside model");
  });

  it("warns when notes were wanted and not one fit", () => {
    expect(chipWords(sending({ counts: { notes: 2, sent: 0, notSent: 2 } }))).toEqual({
      label: "Memory: no notes fit",
      short: "",
      tone: "warn",
    });
  });

  it.each([
    ["install_off", "Memory off", "quiet"],
    ["person_off", "Memory off", "quiet"],
    ["chat_off", "Memory off in this chat", "quiet"],
    ["no_model", "Memory: choose a model", "quiet"],
    ["no_notes", "Memory: no notes yet", "quiet"],
    ["outside_model", "Memory not sent: outside model", "warn"],
    ["window_too_small", "Memory not sent: small window", "warn"],
    ["model_unknown", "Memory not sent", "warn"],
  ] as const)("says %s as '%s'", (code, label, tone) => {
    expect(chipWords(held(code, "A sentence."))).toEqual({ label, short: "", tone });
  });

  it("says what the owner reading someone's chat may know: how many, never which", () => {
    expect(chipWords(owners({ counts: { sent: 2, notSent: 0 } }))).toEqual({
      label: "Memory: 2 notes used",
      short: "2",
      tone: "quiet",
    });
    expect(chipWords(owners({ frozen: false, counts: { sent: 0, notSent: 0 } })).label).toBe(
      "Memory: no notes used",
    );
    expect(readOnlyWords(owners({ counts: { sent: 2, notSent: 1 } }))).toBe(
      "2 notes went to the model in this chat, and 1 note did not fit. Using 162 of 655 tokens.",
    );
    expect(readOnlyWords(owners({ counts: { sent: 1, notSent: 0 }, budget: null }))).toBe(
      "1 note went to the model in this chat.",
    );
    expect(readOnlyWords(owners({ frozen: false }))).toBe(
      "No notes went to the model in this chat.",
    );
  });

  it("is built from what a drawer lists, not from a count it was not given", () => {
    const view = sending({ counts: { notes: 1, sent: 1, notSent: 0 }, notSent: [notSent()] });
    expect(chipWords(view).label).toBe("Memory: 1 note sent");
  });
});
