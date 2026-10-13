/**
 * What the Documents screen and Test a question say of the result sorter (MR6b): how each code
 * looks and what a person can do about it, where the sorter in use runs and whose word it is, what
 * its speed comes to beside Sort time, why passages were not sorted (the server's own sentence with
 * the numbers), and what a sort of a tried question did. Every sentence the server makes is the
 * one in `tests/fixtures/sort_codes.json`, which the server's own tests hold to what it says.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  noSorter,
  SORT_CODES,
  SORT_RUN_CODES,
  sortedRun,
  sortFacts,
  sortFor,
  sortRun,
  sortRunFor,
  sortSays,
  SORTER,
} from "../test-saved";
import {
  hasSortWords,
  rateWords,
  secondsWords,
  SORT_OWNER_FIXES,
  SORT_TONE,
  sortConsoleStep,
  sortedWords,
  sorterInUse,
  sortNote,
  sortOwnerWords,
  sortReasonWords,
  sortSpeedFromWords,
  sortSpeedWords,
  sortTone,
  sortWhereWords,
  sortWhoseWords,
} from "./documentSorting";
import type { SortCode } from "./types";

/** Every code the screen or a question can say, once. */
const EVERY: SortCode[] = [...new Set([...SORT_CODES, ...SORT_RUN_CODES])];
/** The codes that need somebody: a sorter that is stopped, named wrongly, refused or failing. */
const WARN: SortCode[] = [
  "not_running",
  "not_listed",
  "not_sorter",
  "outside",
  "limited",
  "key",
  "unreachable",
  "slow",
  "refused",
  "unusable",
  "error",
];

// --- a time and a speed, as the server says them ---

const TABLE = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../../tests/fixtures/sort_words.json"), "utf8"),
) as { seconds: [number, string][]; rates: [number, string][] };

describe("a time and a speed", () => {
  it("are said as the server says them: one table, which its own test reads too", () => {
    expect(TABLE.seconds.length).toBeGreaterThanOrEqual(15);
    expect(TABLE.rates.length).toBeGreaterThanOrEqual(10);
    for (const [seconds, words] of TABLE.seconds) {
      expect(secondsWords(seconds), String(seconds)).toBe(words);
    }
    for (const [rate, words] of TABLE.rates) {
      expect(rateWords(rate), String(rate)).toBe(words);
    }
  });

  it("have no more figures than they need: a whole second has no point, a small one two places", () => {
    expect(secondsWords(7)).toBe("7 s");
    expect(secondsWords(12)).toBe("12 s");
    expect(secondsWords(6.94)).toBe("6.9 s");
    expect(secondsWords(0.45)).toBe("0.45 s");
    expect(secondsWords(0.5)).toBe("0.5 s");
    expect(secondsWords(120)).toBe("120 s");
  });

  it("go to two figures once a speed is over a hundred, and are grouped by thousands", () => {
    expect(rateWords(849.9)).toBe("about 850 tokens a second");
    expect(rateWords(1234.5)).toBe("about 1,200 tokens a second");
    expect(rateWords(45.6)).toBe("about 46 tokens a second");
    expect(rateWords(123456.7)).toBe("about 120,000 tokens a second");
  });
});

// --- every code, and how it looks ---

describe("every code the sorter can say", () => {
  it("has a look, and a look is for no code the server does not say", () => {
    expect(Object.keys(SORT_TONE).sort()).toEqual([...EVERY].sort());
    expect(EVERY).toHaveLength(21);
  });

  it.each(EVERY)("%s: is a warning only where something needs somebody", (code) => {
    expect(SORT_TONE[code]).toBe(WARN.includes(code) ? "warn" : "info");
  });

  it.each(EVERY)(
    "%s: tells a person who is not the owner to ask them only where the owner mends it, in the console",
    (code) => {
      const owner = ["not_running", "not_listed", "not_sorter", "outside"].includes(code);
      expect(sortOwnerWords(code)).toBe(
        owner ? "Ask the owner of this Workbench to fix this in Eugene's console." : null,
      );
    },
  );

  it("names the owner's fixes as the codes it asks the owner about, and no others", () => {
    expect([...SORT_OWNER_FIXES].sort()).toEqual(
      ["not_running", "not_listed", "not_sorter", "outside"].sort(),
    );
  });

  it.each(EVERY)(
    "%s: sends the owner to Backends only for a sorter that is not running",
    (code) => {
      expect(sortConsoleStep(code)).toBe(code === "not_running" ? "backends" : null);
    },
  );
});

// --- the Documents screen ---

describe("whether the Documents screen has anything to say of the sorter", () => {
  it("has nothing where there is no gateway to ask, or nothing was looked at", () => {
    expect(hasSortWords(null)).toBe(false);
    expect(hasSortWords(undefined)).toBe(false);
  });

  it.each(["off", "none_set_up"] as const)(
    "has nothing for %s: a sorter that was never in play is not worth a notice",
    (code) => {
      expect(hasSortWords(sortFor(code))).toBe(false);
    },
  );

  it.each(SORT_CODES.filter((code) => code !== "off" && code !== "none_set_up"))(
    "has something to say for %s",
    (code) => {
      expect(hasSortWords(sortFor(code))).toBe(true);
    },
  );
});

describe("the sorter in use", () => {
  it("is one that is sorting, and not one that is chosen but too slow, or stopped, or being measured", () => {
    expect(sorterInUse(sortFacts())).toBe(true);
    expect(sorterInUse(sortFor("over_budget"))).toBe(false);
    expect(sorterInUse(sortFor("measuring"))).toBe(false);
    expect(sorterInUse(sortFor("not_running"))).toBe(false);
    // Whatever the server's flag says, a sorter with no name or no label is not one that is said.
    expect(sorterInUse(sortFacts({ model: null }))).toBe(false);
    expect(sorterInUse(sortFacts({ label: null }))).toBe(false);
  });

  it("is a warning in itself when it runs outside the person's machines, which is where the text goes", () => {
    expect(sortTone(sortFacts())).toBe("info");
    expect(
      sortTone(
        sortFacts({
          locality: "external",
          runsOn: "Cohere",
          allowExternal: true,
          automatic: false,
        }),
      ),
    ).toBe("warn");
    expect(sortTone(sortFacts({ locality: "unknown", allowExternal: true }))).toBe("warn");
  });

  it("is not a warning for one that is chosen and not in use, wherever it runs: nothing is sent to it", () => {
    expect(sortTone(sortFor("over_budget", { locality: "external" }))).toBe("info");
    expect(sortTone(sortFor("not_running"))).toBe("warn");
  });

  it("says where it runs, and what that means for what is in the person's documents", () => {
    expect(sortWhereWords(sortFacts())).toBe(
      "It runs on your own machines, so what is in your documents stays on them.",
    );
    expect(
      sortWhereWords(sortFacts({ locality: "external", runsOn: "Cohere", allowExternal: true })),
    ).toBe(
      "It runs on Cohere. The owner allowed each question and its best passages to be sent there.",
    );
    expect(sortWhereWords(sortFacts({ locality: "external", runsOn: null }))).toMatch(
      /^It runs outside your machines\. The owner allowed/,
    );
    expect(sortWhereWords(sortFacts({ locality: "unknown" }))).toBe(
      "Eugene cannot say where it runs. The owner allowed models outside your machines, so each question and its best passages may leave them.",
    );
    expect(sortWhereWords(sortFacts({ locality: null }))).toBeNull();
  });

  it("says whose word it is: automatic, or the owner's, in Eugene's console", () => {
    expect(sortWhoseWords(sortFacts())).toBe(
      "Automatic means the first result sorter that is running on your own machines.",
    );
    expect(sortWhoseWords(sortFacts({ automatic: false, setting: SORTER }))).toBe(
      "The owner of this Workbench chose it, in Eugene's console.",
    );
  });
});

describe("what the speed comes to", () => {
  it("is how fast it reads, what a usual sort adds to an answer, and Sort time", () => {
    expect(sortSpeedWords(sortFacts())).toBe(
      `${SORTER} sorts about 4,100 tokens a second here, so sorting the 18 passages a question usually finds (about 300 tokens each) adds about 1.4 s to each answer, within Sort time (3 s).`,
    );
  });

  it("is made of the numbers the server sent, and no others", () => {
    expect(
      sortSpeedWords(
        sortFacts({
          model: "other-sorter",
          ratePerSecond: 820,
          budgetSeconds: 10,
          typical: { passages: 12, passageTokens: 250, tokens: 3_840, seconds: 7.02 },
        }),
      ),
    ).toBe(
      "other-sorter sorts about 820 tokens a second here, so sorting the 12 passages a question usually finds (about 250 tokens each) adds about 7 s to each answer, within Sort time (10 s).",
    );
  });

  it("is not said for a sorter that is not in use: its own sentence says it, with the numbers", () => {
    expect(sortSpeedWords(sortFor("over_budget"))).toBeNull();
    expect(sortSpeedWords(sortFor("measuring"))).toBeNull();
    expect(sortSpeedWords(sortFor("not_running"))).toBeNull();
  });

  it("is not said for a speed that was not measured, or a time that was not worked out", () => {
    expect(sortSpeedWords(sortFacts({ ratePerSecond: null }))).toBeNull();
    expect(
      sortSpeedWords(
        sortFacts({ typical: { passages: 18, passageTokens: 300, tokens: 5_760, seconds: null } }),
      ),
    ).toBeNull();
  });

  it("says what the speed mostly comes from: quick tests, or the sorts Workbench has made", () => {
    // Said as the server says it (`rateFrom`: the quick tests' share of what the speed is made of
    // is more than half, or it is not). A sorter that is too slow is measured again by quick tests
    // alone, so a speed that began as sorts ends as theirs, and the page must say so.
    expect(sortSpeedFromWords(sortFacts({ rateFrom: "probe" }))).toBe(
      "That speed comes mostly from quick tests, not from sorts of your questions.",
    );
    expect(sortSpeedFromWords(sortFacts({ rateFrom: "sorts" }))).toBe(
      "That speed comes mostly from the sorts Workbench has made.",
    );
  });

  it("does not say where a speed came from when there is none, or the server did not say", () => {
    expect(sortSpeedFromWords(sortFacts({ ratePerSecond: null, rateFrom: "probe" }))).toBeNull();
    expect(sortSpeedFromWords(sortFacts({ rateFrom: null }))).toBeNull();
    expect(sortSpeedFromWords(sortFor("measuring"))).toBeNull();
  });
});

describe("why passages are not sorted, on the Documents screen", () => {
  it.each(SORT_CODES.filter((code) => code !== "ok" && code !== "measuring"))(
    "%s: is the server's own sentence, as it came",
    (code) => {
      const sort = sortFor(code);
      expect(sortReasonWords(sort)).toBe(sortSays(code));
      expect(sortReasonWords(sort)).toMatch(/^Not sorted: /);
    },
  );

  it("has the numbers the decision was made of: the speed, the time and Sort time", () => {
    expect(sortReasonWords(sortFor("over_budget"))).toBe(
      `Not sorted: ${SORTER} sorts about 820 tokens a second here, so sorting the 18 passages a question usually finds (about 300 tokens each) would add about 7 s to each answer, and Sort time is 3 s. Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway.`,
    );
  });

  it("says what is being done while the sorter is measured, which the server has no sentence for", () => {
    expect(sortReasonWords(sortFor("measuring"))).toBe(
      `Measuring how fast ${SORTER} sorts, to tell whether it is quick enough.`,
    );
    expect(sortReasonWords(sortFor("measuring", { model: null }))).toBe(
      "Measuring how fast the result sorter sorts, to tell whether it is quick enough.",
    );
  });

  it("is nothing when the passages are sorted", () => {
    expect(sortReasonWords(sortFacts())).toBeNull();
    // Not the server's sentence for another code, whatever a sorter in use was sent with it.
    expect(sortReasonWords(sortFacts({ message: "Not sorted: stale." }))).toBeNull();
  });
});

// --- Test a question ---

describe("why the passages of a tried question were not sorted", () => {
  it.each(SORT_RUN_CODES)(
    "%s: is the server's own sentence, unless the sorter was never in play",
    (code) => {
      const run = sortRunFor(code);
      const never = ["off", "none_set_up", "nothing_to_sort", "no_question", "not_asked", "none"];
      expect(never.includes(code)).toBe(run.quiet);
      // A question that was too slow for Sort time says it of these passages, not of a usual sort.
      const said = () => sortSays(code === "over_budget" ? "over_budget_question" : code);
      expect(sortNote(run)).toBe(run.quiet ? null : said());
    },
  );

  it("has the numbers of these passages: the tokens, the speed, the time and Sort time", () => {
    expect(sortNote(sortRunFor("over_budget"))).toBe(
      `Not sorted: ${SORTER} sorts about 820 tokens a second here, so sorting these 18 passages (5,760 tokens) would take about 7 s, and Sort time is 3 s. Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway.`,
    );
  });

  it("is nothing for passages that were sorted, or for a run the server did not send", () => {
    expect(sortNote(sortedRun())).toBeNull();
    expect(sortNote(undefined)).toBeNull();
    expect(sortNote(null)).toBeNull();
  });

  it("is nothing for passages that were sorted, whatever else came with them: sorted is what the server said", () => {
    expect(
      sortNote(sortedRun({ message: "Not sorted: left over from a slower look." })),
    ).toBeNull();
  });

  it("is nothing where the server has no sentence for it, even when the sorter was in play", () => {
    expect(sortNote(sortRun({ code: "error", quiet: false, message: null }))).toBeNull();
  });
});

describe("what a sort of a tried question did", () => {
  it("is how many passages it put in order and how long that took, beside Sort time", () => {
    expect(sortedWords(sortedRun())).toBe(
      "The sorter put 18 passages in order in 1.4 s (Sort time is 3 s).",
    );
    expect(sortedWords(sortedRun({ passages: 1, seconds: 0.45, budgetSeconds: 10 }))).toBe(
      "The sorter put 1 passage in order in 0.45 s (Sort time is 10 s).",
    );
  });

  it("says no more than it was told: a time that was not kept, or a Sort time that was not sent", () => {
    expect(sortedWords(sortedRun({ seconds: null }))).toBe(
      "The sorter put 18 passages in order (Sort time is 3 s).",
    );
    expect(sortedWords(sortedRun({ budgetSeconds: null }))).toBe(
      "The sorter put 18 passages in order in 1.4 s.",
    );
  });

  it("is nothing for passages that were not sorted, or a run the server did not send", () => {
    expect(sortedWords(sortRunFor("over_budget"))).toBeNull();
    expect(sortedWords(sortRun())).toBeNull();
    expect(sortedWords(undefined)).toBeNull();
    expect(sortedWords(null)).toBeNull();
  });
});

describe("a screen with no sorter", () => {
  it("says nothing of one", () => {
    expect(hasSortWords(noSorter())).toBe(false);
    expect(sortNote(sortRun())).toBeNull();
    expect(sortedWords(sortRun())).toBeNull();
  });
});
