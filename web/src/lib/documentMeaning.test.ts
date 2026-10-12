/**
 * What the screens say about search by meaning (MR5): each code the server can say, how each
 * looks and what a person can do about it; each state a document's meaning can be in; how a
 * question and an answer say which way found which passage. The sentences the server writes
 * are the server's own (tests/fixtures/search_codes.json, which its tests hold to what it
 * says); a time is said the way the server says it (tests/fixtures/duration_words.json).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  addingView,
  adding,
  byMeaning,
  CODES,
  limited,
  meaningOf,
  meaningRun,
  modelView,
  page,
  paused,
  ranByMeaning,
  saved,
  searchFor,
  waiting,
  withMeaning,
  wordsOnly,
  wordsOnlyDoc,
} from "../test-saved";
import {
  addingWords,
  closenessWords,
  CONSOLE_ROUTE,
  consolePage,
  consoleStep,
  detailWords,
  drawerSearchWords,
  durationWords,
  emptyWords,
  foundByWords,
  introWords,
  isMeaningNote,
  meaningNote,
  meaningRebuiltWords,
  meaningRow,
  meaningTotalsWords,
  modelInUse,
  ownerWords,
  OWNER_FIXES,
  placesWords,
  rebuildWords,
  searchedByWords,
  SEARCH_TONE,
  searchTone,
  unavailableWords,
  viaTag,
  viaWords,
  WAY_CHOICES,
  waysOf,
  whereWords,
  whoseWords,
  WORDS_ONLY_ROW,
} from "./documentMeaning";
import type { SearchCode } from "./types";

describe("every code the server can say", () => {
  it("is a code the page has a look for, and the other way round", () => {
    expect([...CODES].sort()).toEqual(Object.keys(SEARCH_TONE).sort());
    expect(CODES).toHaveLength(17);
  });

  it.each(CODES)(
    "%s: a state as it should be is quiet and one that needs somebody is a warning",
    (code) => {
      const quiet: SearchCode[] = ["ok", "none_set_up", "no_gateway"];
      expect(SEARCH_TONE[code]).toBe(quiet.includes(code) ? "info" : "warn");
      expect(searchTone(searchFor(code))).toBe(SEARCH_TONE[code]);
    },
  );

  it.each(CODES)(
    "%s: its detail is the server's, after the headline has said what it says",
    (code) => {
      const facts = searchFor(code);
      const detail = detailWords(facts);
      if (facts.detail === null) {
        expect(detail).toBeNull();
        return;
      }
      expect(detail).not.toBeNull();
      // Never the "Searching by words only: " that the headline has already said, and never less
      // than the rest of the server's sentence.
      expect(detail).not.toMatch(/^Searching by words only:/);
      expect(facts.detail).toContain((detail ?? "").slice(1));
    },
  );

  it("says the detail as a sentence of its own, with a capital", () => {
    expect(detailWords(searchFor("not_running"))).toBe(
      "The search model Qwen3-Embedding-0.6B-Q8_0 is not running. Start it in Eugene's console under Backends.",
    );
    expect(detailWords(searchFor("none_set_up"))).toBe("No search model is set up.");
    expect(detailWords(searchFor("outside_only"))).toBe(
      "The only search models Eugene lists run outside your machines (text-embedding-3-small), and the owner has not allowed sending your documents' text there.",
    );
    // A sentence that does not begin with those words is as the server wrote it.
    expect(detailWords(searchFor("changed"))).toBe(
      "Your documents are searched by words until you choose Rebuild, which adds meaning again with the model that is running.",
    );
  });

  it("leaves a model's name as it is written when a sentence begins with one", () => {
    expect(detailWords(searchFor("outside"))).toBe(
      "text-embedding-3-small runs on OpenAI, outside your machines, and the owner has not allowed sending your documents' text there.",
    );
    expect(detailWords(searchFor("not_search_model"))).toBe(
      "gemma-3 is not a search model. Choose one in the Search model setting in Eugene's console.",
    );
    expect(
      unavailableWords(
        meaningRun({
          message: "Searching by words only: gemma-3 is not a search model.",
          quiet: false,
        }),
      ),
    ).toBe("Nothing was searched: search by meaning could not run. gemma-3 is not a search model.");
  });

  it("says no detail when there is none, or when it says what the headline says", () => {
    expect(detailWords(byMeaning())).toBeNull();
    expect(detailWords(wordsOnly({ detail: "  " }))).toBeNull();
    expect(
      detailWords(
        wordsOnly({ headline: "Nothing more.", detail: "Searching by words only: nothing more." }),
      ),
    ).toBeNull();
  });

  it.each(CODES)("%s: offers what the server says a person can do, and nothing else", (code) => {
    const facts = searchFor(code);
    expect(facts.actions).toEqual(
      code === "none_set_up"
        ? ["get_search_model"]
        : ["changed", "dimension", "damaged"].includes(code)
          ? ["rebuild"]
          : [],
    );
  });

  it("names the page of Eugene's console that mends a code: the Library to get a model, Backends to start one", () => {
    expect(CODES.filter((code) => consoleStep(code) === "library")).toEqual(["none_set_up"]);
    expect(CODES.filter((code) => consoleStep(code) === "backends")).toEqual(["not_running"]);
  });

  it("tells a person who is not the owner to ask them, for the codes the owner mends in the console", () => {
    const told = CODES.filter((code) => ownerWords(code) !== null);
    expect(told.sort()).toEqual([...OWNER_FIXES].sort());
    expect(ownerWords("none_set_up")).toBe(
      "Ask the owner of this Workbench to set one up in Eugene's console.",
    );
    expect(ownerWords("not_running")).toBe(
      "Ask the owner of this Workbench to fix this in Eugene's console.",
    );
    expect(ownerWords("limited")).toBeNull();
    expect(ownerWords("ok")).toBeNull();
  });
});

describe("Eugene's console", () => {
  it("is linked to by its pages, whatever the address ends with", () => {
    expect(consolePage("http://nas:8279/", "library")).toBe("http://nas:8279/library/");
    expect(consolePage("http://nas:8279", "library")).toBe("http://nas:8279/library/");
    expect(consolePage("https://hub.example/eugene/", "inference")).toBe(
      "https://hub.example/eugene/inference/",
    );
    expect(consolePage("http://nas:8279/", "/library/")).toBe("http://nas:8279/library/");
  });

  it("keeps its pages where the console has them: the Library at /library, and Backends at the route it kept, /inference", () => {
    expect(CONSOLE_ROUTE).toEqual({ library: "library", backends: "inference" });
    expect(consolePage("http://nas:8279/", CONSOLE_ROUTE.backends)).toBe(
      "http://nas:8279/inference/",
    );
  });

  it("is not given up on for an address that is not one", () => {
    expect(consolePage("not a url", "library")).toBe("not a url/library/");
  });
});

describe("the search model in use", () => {
  it("is the one the server says is in use, when search by meaning is in use", () => {
    expect(modelInUse(byMeaning())?.label).toBe("In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)");
  });

  it("is not one that is running while the search is paused, or failing, which is not in use", () => {
    expect(paused().model?.resolved).toBe("nomic-embed-text-v1.5");
    expect(modelInUse(paused())).toBeNull();
    expect(modelInUse(searchFor("refused", { model: modelView() }))).toBeNull();
    expect(modelInUse(wordsOnly())).toBeNull();
    expect(modelInUse(byMeaning({ model: modelView({ label: null, resolved: null }) }))).toBeNull();
  });

  it("is a warning in itself when it runs outside the person's machines", () => {
    expect(searchTone(byMeaning())).toBe("info");
    expect(searchTone(byMeaning({ model: modelView({ locality: "external" }) }))).toBe("warn");
    expect(searchTone(byMeaning({ model: modelView({ locality: "unknown" }) }))).toBe("warn");
  });

  it("says where it runs and what that means for the person's text", () => {
    expect(whereWords(modelView())).toBe(
      "It runs on your own machines, so what is in your documents stays on them.",
    );
    expect(whereWords(modelView({ locality: "external", runsOn: "OpenAI" }))).toBe(
      "It runs on OpenAI. The owner of this Workbench allowed the text of your documents and of your questions to be sent there.",
    );
    expect(whereWords(modelView({ locality: "external", runsOn: null }))).toContain(
      "It runs outside your machines.",
    );
    expect(whereWords(modelView({ locality: "unknown" }))).toBe(
      "Eugene cannot say where it runs. The owner of this Workbench allowed models outside your machines, so the text of your documents and of your questions may leave them.",
    );
    expect(whereWords(modelView({ locality: null }))).toBeNull();
  });

  it("says whose word it is that it is the one", () => {
    expect(whoseWords(modelView())).toBe(
      "Automatic means the first search model that is running on your own machines.",
    );
    expect(whoseWords(modelView({ automatic: false, setting: "nomic" }))).toBe(
      "The owner of this Workbench chose it, in Eugene's console.",
    );
  });
});

describe("how adding meaning is going", () => {
  const working = addingView({
    state: "working",
    message: "Adding meaning: 2 of 5 passages, about 14 minutes left.",
  });

  it("is the server's own sentence while meaning is added", () => {
    expect(addingWords(byMeaning({ adding: working }))).toBe(
      "Adding meaning: 2 of 5 passages, about 14 minutes left.",
    );
  });

  it("says a wait for the key's limit, which nothing else has said", () => {
    expect(
      addingWords(
        byMeaning({ adding: addingView({ state: "limited", message: "Slow right now." }) }),
      ),
    ).toBe("Slow right now.");
  });

  it("does not say a search model that is not there twice: the detail has said it", () => {
    const stopped = {
      state: "waiting",
      message: "Meaning is waiting: the search model is not running.",
    } as const;
    expect(addingWords(searchFor("not_running", { adding: addingView(stopped) }))).toBeNull();
    expect(addingWords(searchFor("changed", { adding: addingView(stopped) }))).toBeNull();
    // A wait that the header does not say the cause of is said.
    expect(addingWords(byMeaning({ adding: addingView(stopped) }))).toBe(stopped.message);
  });

  it("says nothing when nothing is being added, or the server did not say", () => {
    expect(addingWords(byMeaning())).toBeNull();
    expect(addingWords(byMeaning({ adding: null }))).toBeNull();
    expect(
      addingWords(byMeaning({ adding: addingView({ state: "working", message: null }) })),
    ).toBeNull();
  });
});

describe("a time", () => {
  const table = JSON.parse(
    readFileSync(join(import.meta.dirname, "../../../tests/fixtures/duration_words.json"), "utf8"),
  ) as [number, string][];

  it("is said the way the server says it, for every time in the table the server's test holds", () => {
    expect(table.length).toBeGreaterThanOrEqual(20);
    for (const [seconds, words] of table)
      expect(durationWords(seconds), String(seconds)).toBe(words);
  });

  it("goes to the even number at a half, as the server's rounding does", () => {
    // 2.5 minutes is about 2, and 4.5 minutes about 4: a half is not always rounded up.
    expect(durationWords(150)).toBe("about 2 minutes");
    expect(durationWords(270)).toBe("about 4 minutes");
    expect(durationWords(210)).toBe("about 4 minutes");
  });
});

describe("Rebuild search", () => {
  it("says how many passages it adds meaning to again, with which search model, and how long it takes", () => {
    expect(rebuildWords({ passages: 3000, seconds: 720, model: "nomic-embed-text-v1.5" })).toBe(
      "Rebuild search adds meaning again to 3,000 passages with nomic-embed-text-v1.5, which takes about 12 minutes. Searching by words keeps working while it does.",
    );
  });

  it("does not say a time that was not measured", () => {
    const said = rebuildWords({ passages: 1, seconds: null, model: "m" });
    expect(said).toBe(
      "Rebuild search adds meaning again to 1 passage with m. Searching by words keeps working while it does.",
    );
    expect(said).not.toMatch(/takes/);
  });

  it("says the search model that is running when the server did not name one", () => {
    expect(rebuildWords({ passages: 5, seconds: null, model: null })).toContain(
      "with the search model that is running.",
    );
  });

  it("says when there is nothing to add meaning to: it only lets Workbench use the model", () => {
    expect(rebuildWords({ passages: 0, seconds: null, model: "m" })).toBe(
      "Rebuild search lets Workbench use m. There are no passages to add meaning to yet.",
    );
  });

  it("says what a rebuild that was asked for came to", () => {
    expect(meaningRebuiltWords({ passages: 3000, model: "nomic-embed-text-v1.5" })).toBe(
      "Adding meaning again to 3,000 passages with nomic-embed-text-v1.5. Searching by words keeps working while it does.",
    );
    expect(meaningRebuiltWords({ passages: 1, model: null })).toBe(
      "Adding meaning again to 1 passage. Searching by words keeps working while it does.",
    );
    expect(meaningRebuiltWords({ passages: 0, model: "m" })).toBe(
      "There are no passages to add meaning to yet.",
    );
  });
});

describe("what the screen says it is", () => {
  it("is searched for the words of a question, and for what it means when that is in use", () => {
    expect(introWords(wordsOnly())).toBe(
      "Your own files, searched for the words in your question. Only you can see them.",
    );
    expect(introWords(byMeaning())).toBe(
      "Your own files, searched for the words in your question and for what it means. Only you can see them.",
    );
    // A search that is paused is by words, and says so.
    expect(introWords(paused())).toContain("for the words in your question. Only");
  });

  it("says what an empty list is for, as it is searched", () => {
    expect(emptyWords(wordsOnly())).toBe(
      "Add files above. Workbench reads each one, so a chat with Documents on can find the passages that have the words of your question.",
    );
    expect(emptyWords(byMeaning())).toContain("or say what it asks.");
  });
});

describe("a document's meaning, on its row", () => {
  it("is the server's own sentence while meaning is being added", () => {
    expect(meaningRow(adding())).toEqual({
      tone: "ok",
      label: "Ready for words. Adding meaning: 2 of 5 passages, about 14 minutes left.",
    });
  });

  it("is a wait, with a clock, in the server's words, for a search model that is not there or a key at its limit", () => {
    const stopped = meaningRow(waiting());
    expect(stopped?.tone).toBe("quiet");
    expect(stopped?.label).toBe(
      "Ready for words. Meaning is waiting: the search model qwen3-embedding is not running. Start it in Eugene's console under Backends.",
    );
    const slow = meaningRow(limited());
    expect(slow?.tone).toBe("quiet");
    expect(slow?.label).toContain(
      "Ready for words. Adding meaning to your documents is slow right now",
    );
  });

  it("is found by words only, and says why, for a document read while no search model ran", () => {
    expect(meaningRow(wordsOnlyDoc())).toEqual({ tone: "ok", label: WORDS_ONLY_ROW });
    expect(WORDS_ONLY_ROW).toBe(
      "Ready for words only: no search model was running when it was read.",
    );
  });

  it("counts what the search model took and what it refused, and says the refused are found by words only", () => {
    const some = saved({
      meaning: meaningOf({ embedded: 5, total: 5, refused: 1, notes: ["It refused one."] }),
    });
    expect(meaningRow(some)?.label).toBe(
      "Ready. Meaning was added to 4 of 5 passages; 1 passage is found by words only.",
    );
    const more = saved({ meaning: meaningOf({ embedded: 5, total: 5, refused: 2 }) });
    expect(meaningRow(more)?.label).toBe(
      "Ready. Meaning was added to 3 of 5 passages; 2 passages are found by words only.",
    );
  });

  it("is nothing more than ready for a document that has all its meaning, or none that is read", () => {
    expect(meaningRow(withMeaning())).toBeNull();
    expect(meaningRow(saved())).toBeNull();
    // A document that is waiting with no sentence from the server has none to show.
    expect(
      meaningRow(
        saved({ state: "searchable", meaning: meaningOf({ state: "waiting", message: null }) }),
      ),
    ).toBeNull();
  });
});

describe("the totals of meaning", () => {
  it("count the passages that have it and say how many are still to do", () => {
    const list = page([adding(), withMeaning({ id: "d9", name: "b.pdf" })]);
    expect(list.totals.embedded).toBe(7);
    expect(list.totals.owed).toBe(3);
    expect(meaningTotalsWords(list)).toBe(
      "Meaning is added to 7 of 10 passages; 3 are still to do.",
    );
  });

  it("say meaning is added to every passage, or to one", () => {
    expect(meaningTotalsWords(page([withMeaning()]))).toBe("Meaning is added to every passage.");
    expect(
      meaningTotalsWords(
        page([withMeaning({ passages: 1, meaning: meaningOf({ embedded: 1, total: 1 }) })]),
      ),
    ).toBe("Meaning is added to the passage.");
  });

  it("do not count a passage the search model refused as one that has it, and say so", () => {
    const refused = saved({
      embedded: 5,
      meaning: meaningOf({ embedded: 5, total: 5, refused: 1, notes: ["x"] }),
    });
    expect(meaningTotalsWords(page([refused]))).toBe(
      "Meaning is added to 4 of 5 passages. The rest are found by words only. 1 passage was refused by the search model and is found by words only.",
    );
  });

  it("are nothing when no passage has meaning and none is owed it: the header says words only", () => {
    expect(
      meaningTotalsWords(page([saved(), wordsOnlyDoc({ id: "d2", name: "b.md" })])),
    ).toBeNull();
    expect(meaningTotalsWords(page([]))).toBeNull();
  });

  it("leave a chat's own files out of what they refuse", () => {
    const own = saved({
      scope: "chat",
      embedded: 5,
      meaning: meaningOf({ embedded: 5, total: 5, refused: 3 }),
    });
    expect(meaningTotalsWords(page([withMeaning(), own]))).not.toMatch(/refused/);
  });
});

describe("how one way at a time is asked for", () => {
  it("offers both ways as a chat does, words only and meaning only, in that order", () => {
    expect(WAY_CHOICES.map((c) => c.value)).toEqual(["both", "words", "meaning"]);
    expect(WAY_CHOICES.map((c) => c.label)).toEqual([
      "Words and meaning, as a chat does",
      "Words only",
      "Meaning only",
    ]);
  });

  it("names none for both, so a chat's own rule applies, and the one way otherwise", () => {
    expect(waysOf("both")).toBeNull();
    expect(waysOf("words")).toEqual(["words"]);
    expect(waysOf("meaning")).toEqual(["meaning"]);
  });
});

describe("how a passage was found", () => {
  it("says words, meaning, or both, in a row's own short words", () => {
    expect(viaTag("words")).toBe("found by words");
    expect(viaTag("meaning")).toBe("found by meaning");
    expect(viaTag("both")).toBe("found by both");
  });

  it("says it in full, and that a passage found by meaning alone has none of the person's words", () => {
    expect(viaWords("words", true)).toBe("Found by words");
    expect(viaWords("both", true)).toBe("Found by both words and meaning");
    expect(viaWords("meaning", false)).toBe("Found by meaning: none of your words are in it");
    expect(viaWords("meaning", true)).toBe("Found by meaning");
  });

  it("says where each way put it, in its own list, and how near the question it was by meaning", () => {
    expect(placesWords({ words: 3, meaning: 1 }, 0.62)).toBe(
      "Words put it 3rd, meaning put it 1st (closeness 0.62).",
    );
    expect(placesWords({ words: 2 }, null)).toBe("Words put it 2nd.");
    expect(placesWords({ meaning: 4 }, 0.5)).toBe("Meaning put it 4th (closeness 0.50).");
    expect(placesWords({ meaning: 4 }, null)).toBe("Meaning put it 4th.");
    expect(placesWords({}, null)).toBeNull();
  });

  it("says a place the way a person does", () => {
    const said = (n: number) => placesWords({ words: n }, null);
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(said)).toEqual([
      "Words put it 1st.",
      "Words put it 2nd.",
      "Words put it 3rd.",
      "Words put it 4th.",
      "Words put it 11th.",
      "Words put it 12th.",
      "Words put it 13th.",
      "Words put it 21st.",
      "Words put it 22nd.",
      "Words put it 23rd.",
      "Words put it 101st.",
      "Words put it 111th.",
    ]);
  });
});

describe("how a question was searched", () => {
  it.each([
    ["meaning and words", "Searched by meaning and words."],
    ["words", "Searched by words only."],
    ["meaning", "Searched by meaning only."],
  ])("says %s", (mode, said) => {
    expect(searchedByWords(mode, false)).toBe(said);
    expect(searchedByWords(mode, true)).toBe(`${said} You chose this.`);
  });

  it("says nothing of a search that was none", () => {
    expect(searchedByWords("none", false)).toBeNull();
    expect(searchedByWords("whatever else", true)).toBeNull();
  });

  it("says why meaning did not run, in the server's words, unless it was never in play", () => {
    const off = meaningRun({
      code: "not_running",
      message: "Searching by words only: x is not running.",
      quiet: false,
    });
    expect(meaningNote(off, false)).toBe("Searching by words only: x is not running.");
    expect(meaningNote(meaningRun({ ...off, quiet: true }), false)).toBeNull();
    // Asked for by name, it is said all the same.
    expect(meaningNote(meaningRun({ ...off, quiet: true }), true)).toBe(off.message);
    expect(meaningNote(ranByMeaning(), false)).toBeNull();
    expect(meaningNote(meaningRun(), true)).toBeNull();
  });

  it("says a search none of whose ways could run, with why, and never that nothing matched", () => {
    expect(
      unavailableWords(
        meaningRun({ message: "Searching by words only: no search model is set up.", quiet: true }),
      ),
    ).toBe("Nothing was searched: search by meaning could not run. No search model is set up.");
    expect(unavailableWords(meaningRun())).toBe(
      "Nothing was searched: search by meaning could not run.",
    );
  });

  it("says how near the nearest passage was by meaning, and what counts when no word matched", () => {
    expect(closenessWords(ranByMeaning())).toBe(
      "By meaning, the nearest passage scored 0.74. When no word matches, a passage needs 0.50 to count.",
    );
    expect(closenessWords(ranByMeaning({ best: 0.27, belowFloor: true }))).toBe(
      "By meaning, the nearest passage scored 0.27. When no word matches, a passage needs 0.50 to count, so none was kept.",
    );
    expect(closenessWords(ranByMeaning({ floor: null }))).toBe(
      "By meaning, the nearest passage scored 0.74.",
    );
    expect(closenessWords(meaningRun())).toBeNull();
  });
});

describe("under an answer", () => {
  it("says how the passages were found, and by which search model when meaning ran", () => {
    expect(foundByWords("words", null)).toBe("Found by words only.");
    expect(foundByWords("meaning and words", ranByMeaning())).toBe(
      "Found by meaning and words, with the search model Qwen3-Embedding-0.6B-Q8_0.",
    );
    expect(foundByWords("meaning and words", null)).toBe("Found by meaning and words.");
    expect(foundByWords("meaning", ranByMeaning({ model: "m" }))).toBe(
      "Found by meaning only, with the search model m.",
    );
    // Meaning that did not run names no search model.
    expect(foundByWords("words", meaningRun({ model: "m" }))).toBe("Found by words only.");
    expect(foundByWords("none", null)).toBeNull();
  });

  it("knows a note about meaning by its code", () => {
    expect(isMeaningNote("meaning_not_running")).toBe(true);
    expect(isMeaningNote("meaning_changed")).toBe(true);
    expect(isMeaningNote("still_reading")).toBe(false);
    expect(isMeaningNote("outside_model")).toBe(false);
  });
});

describe("a chat's drawer", () => {
  it("says the server's headline, and nothing more for meaning and words", () => {
    expect(drawerSearchWords(byMeaning())).toEqual({
      headline: "Searching by meaning and words.",
      more: [],
    });
  });

  it.each(CODES.filter((code) => code !== "ok"))(
    "%s: says why, in the server's words, and where Rebuild is",
    (code) => {
      const facts = searchFor(code);
      const said = drawerSearchWords(facts);
      expect(said.headline).toBe(facts.headline);
      expect(said.more.includes("Rebuild search is on the Documents screen.")).toBe(
        facts.actions.includes("rebuild"),
      );
      expect(said.more.length > 0).toBe(facts.detail !== null || facts.actions.includes("rebuild"));
    },
  );
});
