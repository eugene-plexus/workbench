/**
 * What the Folders section says (MR7): each state of a folder in words, the counts and how far the
 * reading has got, the time in plain words against Workbench's clock, what was left out and why,
 * the limits stated from the numbers in effect, the requests that make a page of files, the rules
 * as they are typed and sent, and how soon the folders are asked about again. The sentences a test
 * holds are the ones a person reads.
 */

import {
  agoWords,
  anySearchable,
  capsWords,
  checkedWords,
  countItems,
  doneCount,
  FILE_FILTERS,
  FILE_REASONS,
  fileRequests,
  filterCount,
  filterOptions,
  folderBusy,
  foldersTotals,
  foldersWait,
  holdsWords,
  ignoreText,
  lastWords,
  leaveOutWords,
  LEFT_HEADINGS,
  leftGroups,
  leftHeading,
  leftSummary,
  leftTotal,
  limitsWords,
  nextWords,
  nothingToAddWords,
  parseIgnore,
  patternsWords,
  rangeWords,
  readFolders,
  readingCount,
  readingStates,
  readingWords,
  reasonOptions,
  reasonWords,
  removeFolderWords,
  rulesChanged,
  stateBadge,
  stateLines,
  syncingWords,
  tookWords,
} from "./documentFolders";
import { IDLE_POLL_MS, POLL_MS, RETRY_MS } from "./savedDocuments";
import type { FolderState } from "./types";
import {
  counts,
  detail,
  folder,
  foldersPage,
  goneFolder,
  LIMITS,
  NOW,
  leftOut,
  problemFolder,
  readingFolder,
  syncingFolder,
  waitingFolder,
  withdrawnFolder,
} from "../test-folders";

describe("reading the answer", () => {
  it("takes a list of folders as the server makes it", () => {
    const page = foldersPage([folder()]);
    expect(readFolders(page)).toBe(page);
  });

  it.each([
    ["nothing", undefined],
    ["null", null],
    ["a string", "folders"],
    ["a page of documents", { documents: [], totals: {}, limits: {} }],
    ["no folders", { available: true, grants: [], limits: {} }],
    ["no grants", { available: true, folders: [], limits: {} }],
    ["no word on whether folders are available", { folders: [], grants: [], limits: {} }],
    ["no limits", { available: true, folders: [], grants: [] }],
  ])("refuses %s, so the page tells nothing of folders it did not read", (_name, value) => {
    expect(readFolders(value)).toBeNull();
  });
});

describe("the counts", () => {
  it("counts a file that has stopped being read as done: read, skipped or failed", () => {
    expect(doneCount(counts())).toBe(12);
    expect(doneCount(readingFolder().counts)).toBe(389);
  });

  it("counts every file that is not at an end as still being read, whatever step it is at", () => {
    expect(readingCount(counts())).toBe(0);
    expect(readingCount(readingFolder().counts)).toBe(15);
    // A step the server adds later is still one of them: it is counted from the rest.
    expect(
      readingCount(
        counts({ files: 5, byState: { ready: 1, skipped: 1, failed: 0, unheard_of: 3 } }),
      ),
    ).toBe(3);
  });

  it("names the steps files are at, apart from the ones that are over", () => {
    expect(readingStates(counts())).toEqual([]);
    expect(readingStates(readingFolder().counts).sort()).toEqual([
      "embedding",
      "queued",
      "reading",
      "searchable",
    ]);
  });

  it("says files, ready, being read and skipped each time, so a zero is seen and not guessed at", () => {
    expect(countItems(counts()).map((i) => [i.kind, i.n, i.label])).toEqual([
      ["files", 12, "files"],
      ["ready", 10, "ready"],
      ["reading", 0, "being read"],
      ["skipped", 2, "skipped"],
    ]);
  });

  it("says one file, not one files", () => {
    expect(countItems(counts({ files: 1, byState: { ready: 1 } }))[0]!.label).toBe("file");
  });

  it("says failed only once a file has", () => {
    expect(countItems(counts()).some((i) => i.kind === "failed")).toBe(false);
    const failing = countItems(counts({ byState: { ...counts().byState, failed: 3 } }));
    expect(failing.at(-1)).toEqual({ kind: "failed", n: 3, label: "failed" });
  });

  it("says nothing of reading when every file is read, and how far it has got while some are not", () => {
    expect(readingWords(counts())).toBeNull();
    expect(readingWords(readingFolder().counts)).toBe(
      "Reading the files: 389 of 404 are done. 4 files can be searched by words already, and are having their meaning added.",
    );
  });

  it("says one file can be found by words in the singular", () => {
    expect(
      readingWords(
        counts({
          files: 3,
          byState: { queued: 1, reading: 0, searchable: 1, ready: 1, skipped: 0 },
        }),
      ),
    ).toBe(
      "Reading the files: 1 of 3 are done. 1 file can be searched by words already, and is having its meaning added.",
    );
  });

  it("says nothing of meaning while no file is found by words yet", () => {
    expect(
      readingWords(counts({ files: 5, byState: { queued: 3, reading: 2, ready: 0, skipped: 0 } })),
    ).toBe("Reading the files: 0 of 5 are done.");
  });

  it("says what a folder holds: its passages and its size", () => {
    expect(holdsWords(counts())).toBe("31 passages · 47 KiB");
    expect(holdsWords(readingFolder().counts)).toBe("1,904 passages · 4.2 MiB");
  });

  it("says no size for a folder that holds no bytes: the server's own words have none for it", () => {
    expect(holdsWords(counts({ files: 0, passages: 0, bytes: 0 }))).toBe("0 passages");
    expect(foldersTotals([withdrawnFolder()])).toBe("1 folder · 0 files · 0 passages");
    expect(foldersTotals([withdrawnFolder(), folder()])).toBe(
      "2 folders · 12 files · 31 passages · 47 KiB",
    );
  });

  it("totals every folder apart from the person's own files", () => {
    expect(foldersTotals([folder(), readingFolder({ id: "g2" })])).toBe(
      "2 folders · 416 files · 1,935 passages · 4.2 MiB",
    );
    expect(foldersTotals([folder()])).toBe("1 folder · 12 files · 31 passages · 47 KiB");
  });

  it("finds files that can be searched: read, or found by words while the rest of their reading goes on", () => {
    expect(anySearchable([])).toBe(false);
    expect(anySearchable([folder()])).toBe(true);
    expect(anySearchable([readingFolder()])).toBe(true);
    // Waiting, being read, skipped or failed: nothing yet.
    expect(
      anySearchable([
        folder({
          counts: counts({
            files: 6,
            byState: { queued: 3, reading: 1, ready: 0, skipped: 1, failed: 1 },
          }),
        }),
      ]),
    ).toBe(false);
    expect(anySearchable([waitingFolder()])).toBe(false);
  });
});

describe("time in plain words", () => {
  it.each([
    [0, "just now"],
    [44, "just now"],
    [45, "a minute ago"],
    [89, "a minute ago"],
    [90, "2 minutes ago"],
    [180, "3 minutes ago"],
    [44 * 60, "44 minutes ago"],
    [45 * 60, "an hour ago"],
    [89 * 60, "an hour ago"],
    [90 * 60, "2 hours ago"],
    [3 * 3600, "3 hours ago"],
    [21 * 3600, "21 hours ago"],
    [22 * 3600, "a day ago"],
    [35 * 3600, "a day ago"],
    [36 * 3600, "2 days ago"],
    [5 * 86400, "5 days ago"],
  ])("says %i seconds as %s", (seconds, said) => {
    expect(agoWords(seconds)).toBe(said);
  });

  it.each([
    [0.4, "under a second"],
    [1, "1 second"],
    [3.4, "3 seconds"],
    [59, "59 seconds"],
    [60, "1 minute"],
    [150, "3 minutes"],
  ])("says a check that took %f seconds as %s", (seconds, said) => {
    expect(tookWords(seconds)).toBe(said);
  });

  it("says when a folder was checked, against the clock it is given", () => {
    expect(checkedWords(NOW - 180, NOW)).toBe("Checked 3 minutes ago.");
    expect(checkedWords(NOW - 180, NOW + 3600)).toBe("Checked an hour ago.");
    expect(checkedWords(null, NOW)).toBeNull();
  });

  it("says when the next check is: in how long, or that it is due", () => {
    expect(nextWords(NOW + 420, NOW)).toBe("Next check in about 7 minutes.");
    expect(nextWords(NOW + 20, NOW)).toBe("Next check in less than a minute.");
    expect(nextWords(NOW + 7200, NOW)).toBe("Next check in about 2 hours.");
    expect(nextWords(NOW, NOW)).toBe("The next check is due now.");
    expect(nextWords(NOW - 5, NOW)).toBe("The next check is due now.");
    expect(nextWords(null, NOW)).toBeNull();
  });

  it("says a check took as long as the server says", () => {
    expect(lastWords(folder().last)).toContain("took under a second");
  });
});

describe("where a folder stands", () => {
  it.each([
    ["waiting", "Waiting", "quiet"],
    ["syncing", "Syncing", "quiet"],
    ["synced", "Synced", "ok"],
    ["problem", "Problem", "warn"],
    ["unavailable", "Not available", "warn"],
    ["withdrawn", "Withdrawn", "warn"],
  ] as [FolderState, string, string][])("%s is said as %s", (state, label, tone) => {
    expect(stateBadge(state)).toEqual({ label, tone });
  });

  it("says a state it does not know as unknown, and as a warning", () => {
    expect(stateBadge("sideways" as FolderState)).toEqual({ label: "Unknown", tone: "warn" });
  });

  it("says a check that is still looking, and then how far it has read", () => {
    expect(syncingWords(null)).toBe("Looking at this folder…");
    expect(syncingWords(syncingFolder({ phase: "looking", total: 0, done: 0 }).syncing)).toBe(
      "Looking at this folder…",
    );
    expect(syncingWords(syncingFolder().syncing)).toBe(
      "Reading this folder: 1,200 of 4,000 files.",
    );
    expect(syncingWords(syncingFolder({ done: 0, total: 1 }).syncing)).toBe(
      "Reading this folder: 0 of 1 file.",
    );
  });

  it("says a check the person asked for, and a deep one", () => {
    expect(syncingWords(syncingFolder({ manual: true }).syncing)).toBe(
      "Sync now: Reading this folder: 1,200 of 4,000 files.",
    );
    expect(
      syncingWords(syncingFolder({ manual: true, deep: true, phase: "looking" }).syncing),
    ).toBe("Deep check: Looking at this folder…");
  });

  it("tells a folder that was never checked it will be soon, and one that was that it will be in a moment", () => {
    expect(stateLines(waitingFolder(), NOW)).toEqual([
      "Workbench will look at this folder soon, for the first time.",
    ]);
    expect(stateLines(waitingFolder({ checkedAt: NOW - 600 }), NOW)).toEqual([
      "Workbench will check this folder in a moment.",
    ]);
  });

  it("tells a synced folder when it was checked and when it will be next", () => {
    expect(stateLines(folder(), NOW)).toEqual([
      "Checked 3 minutes ago.",
      "Next check in about 7 minutes.",
    ]);
    // Time has moved on since the last read: the words follow the clock, not the answer.
    expect(stateLines(folder(), NOW + 300)).toEqual([
      "Checked 8 minutes ago.",
      "Next check in about 2 minutes.",
    ]);
    expect(stateLines(folder(), NOW + 500)).toEqual([
      "Checked 11 minutes ago.",
      "The next check is due now.",
    ]);
  });

  it("tells a synced folder with no next check only when it was checked", () => {
    expect(stateLines(folder({ nextAt: null }), NOW)).toEqual(["Checked 3 minutes ago."]);
  });

  it("tells a check that is under way", () => {
    expect(stateLines(syncingFolder(), NOW)).toEqual([
      "Reading this folder: 1,200 of 4,000 files.",
    ]);
  });

  it("tells a problem in the server's words, and when the last check that worked was", () => {
    expect(stateLines(problemFolder(), NOW)).toEqual([
      "Workbench could not look at this folder (permission denied). Its files are as they were at the last check.",
      "The last check that worked was 3 minutes ago.",
    ]);
    expect(stateLines(problemFolder({ syncedAt: null }), NOW)[1]).toBe(
      "It has not been checked well yet.",
    );
  });

  it("tells a folder that is not there in the server's words, and that it looks again", () => {
    const lines = stateLines(goneFolder(), NOW);
    expect(lines[0]).toBe(
      "This folder is not there any more. 12 documents from it were taken out of your search, and they come back if the folder does.",
    );
    expect(lines[1]).toBe("Workbench last looked 3 minutes ago, and looks again at each check.");
    expect(stateLines(goneFolder({ checkedAt: null }), NOW)[1]).toBe(
      "Workbench looks again at each check.",
    );
  });

  it("tells a withdrawn folder in the server's words, and when", () => {
    expect(stateLines(withdrawnFolder(), NOW)).toEqual([
      "The owner took away your access to this folder. 12 documents from it were taken out of your search.",
      "Taken away 2 days ago.",
    ]);
  });

  it("falls back to the server's own headline for what it does not know, and for a problem with no words", () => {
    expect(stateLines(folder({ state: "sideways" as FolderState, headline: "Odd." }), NOW)).toEqual(
      ["Odd."],
    );
    expect(stateLines(problemFolder({ problem: null, headline: "It broke." }), NOW)[0]).toBe(
      "It broke.",
    );
  });

  it("says what the last check did, in a sentence", () => {
    expect(lastWords(null)).toBeNull();
    expect(lastWords(folder().last)).toBe(
      "Last check, a scheduled one: nothing had changed. It looked at 12 files in 3 folders, and took under a second.",
    );
    expect(
      lastWords({
        ...folder().last!,
        mode: "now",
        added: 3,
        changed: 1,
        removed: 2,
        seconds: 4.2,
        files: 1,
        directories: 1,
      }),
    ).toBe(
      "Last check, one you asked for: 3 new, 1 changed, 2 removed. It looked at 1 file in 1 folder, and took 4 seconds.",
    );
    expect(lastWords({ ...folder().last!, mode: "deep", moved: 7, seconds: null })).toBe(
      "Last check, a deep check: 7 moved. It looked at 12 files in 3 folders.",
    );
    // A file looked at and not taken is a thing the check did: "nothing had changed" would not be so.
    expect(lastWords({ ...folder().last!, added: 3, skipped: 1 })).toBe(
      "Last check, a scheduled one: 3 new, 1 skipped. It looked at 12 files in 3 folders, and took under a second.",
    );
    expect(lastWords({ ...folder().last!, skipped: 2 })).toContain(
      "Last check, a scheduled one: 2 skipped.",
    );
  });
});

describe("what was left out", () => {
  it("says what the last check left out in a line, from its numbers", () => {
    expect(leftSummary(null)).toBeNull();
    expect(leftSummary(folder().last)).toBeNull();
    expect(
      leftSummary({
        ...folder().last!,
        ignored: { files: 31, folders: 4 },
        links: 2,
        unlistedTotal: 1,
      }),
    ).toBe(
      "Left out: 31 files and 4 folders ignored, 2 links not followed, 1 folder that could not be looked into.",
    );
    expect(leftSummary({ ...folder().last!, ignored: { files: 1, folders: 0 } })).toBe(
      "Left out: 1 file ignored.",
    );
    expect(leftSummary({ ...folder().last!, ignored: { files: 0, folders: 1 } })).toBe(
      "Left out: 1 folder ignored.",
    );
    expect(leftSummary({ ...folder().last!, links: 1 })).toBe("Left out: 1 link not followed.");
  });

  it("says each limit a folder has run into, and nothing for one it has not", () => {
    expect(capsWords(folder().caps)).toEqual([]);
    const reached = {
      files: { limit: 10_000, found: 10_000, reached: true },
      bytes: { limit: 2048 * 1024 * 1024, used: 2048 * 1024 * 1024, reached: true },
      depth: { limit: 12, reached: 3 },
      entries: { reached: true },
      fileBytes: { limit: 50 * 1024 * 1024 },
    };
    expect(capsWords(reached)).toEqual([
      "This folder has more than the 10,000 files Workbench keeps for one folder, so the files that come last are left out.",
      "The copies Workbench keeps of this folder's files reached 2048 MiB, so files after that are skipped.",
      "3 folders more than 12 folders deep are not looked at.",
      "This folder has more names than Workbench looks at in one check, so some are not looked at.",
    ]);
    expect(capsWords({ ...reached, depth: { limit: 12, reached: 1 } })[2]).toBe(
      "1 folder more than 12 folders deep is not looked at.",
    );
  });

  it("groups what was left out by the rule or limit, in a fixed order, each with how many there were", () => {
    const groups = leftGroups(detail());
    expect(groups.map((g) => [g.code, g.heading, g.total, g.items.length])).toEqual([
      ["ignored_default", "Always left out", 1, 1],
      ["ignored_hidden", "Hidden", 1, 1],
      ["ignored_gitignore", "A .gitignore file", 1, 1],
      ["link", "Links, not followed", 1, 1],
    ]);
    expect(leftTotal(groups)).toBe(4);
  });

  it("counts every one that was left out even where the server named only some", () => {
    const groups = leftGroups(
      detail({
        left: [leftOut({ path: "a" }), leftOut({ path: "b" })],
        leftCounts: { ignored_default: 450, over_files: 12 },
      }),
    );
    expect(groups.map((g) => [g.code, g.total, g.items.length])).toEqual([
      ["ignored_default", 450, 2],
      ["over_files", 12, 0],
    ]);
    expect(leftTotal(groups)).toBe(462);
  });

  it("puts a reason it does not know last, under its own name", () => {
    const groups = leftGroups(
      detail({
        left: [leftOut({ code: "brand_new_reason", path: "x", reason: "Left out: new." })],
        leftCounts: { brand_new_reason: 1, too_deep: 2 },
      }),
    );
    expect(groups.map((g) => g.code)).toEqual(["too_deep", "brand_new_reason"]);
    expect(groups.at(-1)!.heading).toBe("brand new reason");
    expect(leftHeading("link")).toBe("Links, not followed");
  });

  it("has a heading for every kind of thing a check can leave out", () => {
    expect(Object.keys(LEFT_HEADINGS)).toEqual([
      "ignored_default",
      "ignored_hidden",
      "ignored_temporary",
      "ignored_list",
      "ignored_gitignore",
      "link",
      "cloud",
      "special",
      "odd_name",
      "too_deep",
      "over_files",
    ]);
  });
});

describe("why a file was skipped or failed", () => {
  it("says each reason in a few plain words, and a reason it does not know as its own name", () => {
    expect(reasonWords("not_document")).toBe("A picture or a sound recording");
    expect(reasonWords("busy")).toBe("In use by another program");
    expect(reasonWords("a_new_reason")).toBe("a new reason");
    expect(Object.keys(FILE_REASONS)).toContain("over_folder_size");
  });

  it("offers the reasons a folder's files were skipped for, the most common first", () => {
    // By how many, then by their words: the five that need more memory come before the ones in use
    // by another program, which an alphabet would put first.
    expect(
      reasonOptions(
        counts({ byCode: { busy: 1, memory: 5, not_document: 9, damaged: 1, zero: 0 } }),
      ),
    ).toEqual([
      { code: "not_document", label: "A picture or a sound recording", n: 9 },
      { code: "memory", label: "Needs more memory than a reader is given", n: 5 },
      { code: "damaged", label: "Damaged, or could not be read", n: 1 },
      { code: "busy", label: "In use by another program", n: 1 },
    ]);
    expect(reasonOptions(counts({ byCode: {} }))).toEqual([]);
  });
});

describe("the list of a folder's files", () => {
  it("counts each filter from the folder's own counts", () => {
    const held = readingFolder().counts;
    expect(FILE_FILTERS.map((f) => [f.value, filterCount(f.value, held)])).toEqual([
      ["all", 404],
      ["ready", 380],
      ["reading", 15],
      ["skipped", 9],
      ["failed", 0],
    ]);
  });

  it("offers the filters that have files, and the one in use even when it has none", () => {
    const options = (current: Parameters<typeof filterOptions>[1]) =>
      filterOptions(readingFolder().counts, current).map((o) => o.value);
    expect(options("all")).toEqual(["all", "ready", "reading", "skipped"]);
    expect(options("failed")).toEqual(["all", "ready", "reading", "skipped", "failed"]);
    expect(filterOptions(counts({ files: 0, byState: {} }), "all").map((o) => o.value)).toEqual([
      "all",
    ]);
  });

  it("asks for a page of all of a folder's files in one request, in the server's own order", () => {
    expect(fileRequests("all", counts(), 100, 50)).toEqual([
      { state: null, offset: 100, limit: 50 },
    ]);
    expect(fileRequests("skipped", counts(), 0, 50)).toEqual([
      { state: "skipped", offset: 0, limit: 50 },
    ]);
  });

  it("makes a page of the files being read from the steps they are at, the ones being read now first and the waiting last", () => {
    const held = counts({
      files: 100,
      byState: { queued: 90, reading: 2, searchable: 5, embedding: 3, ready: 0, skipped: 0 },
    });
    // The first page falls across the steps: 2 reading, 5 and 3 found by words, 40 waiting.
    expect(fileRequests("reading", held, 0, 50)).toEqual([
      { state: "reading", offset: 0, limit: 2 },
      { state: "searchable", offset: 0, limit: 5 },
      { state: "embedding", offset: 0, limit: 3 },
      { state: "queued", offset: 0, limit: 40 },
    ]);
    // A later page falls inside one step, at the place in it that the page has reached.
    expect(fileRequests("reading", held, 50, 50)).toEqual([
      { state: "queued", offset: 40, limit: 50 },
    ]);
    // The last page is short.
    expect(fileRequests("reading", held, 100, 50)).toEqual([]);
  });

  it("asks for nothing when no file is being read", () => {
    expect(fileRequests("reading", counts(), 0, 50)).toEqual([]);
  });

  it("says where a page is in the list", () => {
    expect(rangeWords(0, 50, 412)).toBe("1 to 50 of 412");
    expect(rangeWords(400, 12, 412)).toBe("401 to 412 of 412");
    expect(rangeWords(0, 3, 3)).toBe("1 to 3 of 3");
    expect(rangeWords(0, 0, 0)).toBe("No files");
    expect(rangeWords(0, 1_000, 12_345)).toBe("1 to 1,000 of 12,345");
  });
});

describe("the rules, as they are typed and as they are sent", () => {
  it("writes the list a pattern on each line, and reads it back the same", () => {
    expect(ignoreText(["*.log", "/old/"])).toBe("*.log\n/old/");
    expect(parseIgnore("*.log\n/old/")).toEqual(["*.log", "/old/"]);
    expect(parseIgnore(ignoreText(["a b", "c\\ "]))).toEqual(["a b", "c\\ "]);
  });

  it("does not send a blank line, and leaves everything else for the server to judge", () => {
    expect(parseIgnore("")).toEqual([]);
    expect(parseIgnore("\n  \n*.log\r\n\n!keep\n# note\n")).toEqual(["*.log", "!keep", "# note"]);
  });

  it("says when what is typed is not what the folder has", () => {
    const held = { ignore: ["*.log"], includeHidden: false, useGitignore: true };
    expect(rulesChanged(held, { ...held })).toBe(false);
    expect(rulesChanged(held, { ...held, ignore: ["*.log", "x"] })).toBe(true);
    expect(rulesChanged(held, { ...held, ignore: ["*.txt"] })).toBe(true);
    expect(rulesChanged(held, { ...held, ignore: [] })).toBe(true);
    expect(rulesChanged(held, { ...held, includeHidden: true })).toBe(true);
    expect(rulesChanged(held, { ...held, useGitignore: false })).toBe(true);
  });

  it("says how many patterns are typed, of how many are allowed", () => {
    expect(patternsWords(3, 100)).toBe("3 of 100 patterns");
    expect(patternsWords(1_000, 2_000)).toBe("1,000 of 2,000 patterns");
  });
});

describe("the limits, stated from the numbers in effect", () => {
  const said = limitsWords(LIMITS);

  it("says there is no file watcher, and how often it looks", () => {
    expect(said[0]).toBe(
      "Workbench does not watch your folders. It looks at each one again every 10 minutes, and whenever you choose Sync now, so a change can take that long to show.",
    );
    expect(limitsWords({ ...LIMITS, syncMinutes: 1 })[0]).toContain("every 1 minute,");
    expect(limitsWords({ ...LIMITS, syncMinutes: 90 })[0]).toContain("every 90 minutes,");
  });

  it("says a quick check misses an edit that keeps a file's size and time, and that a deep check finds it", () => {
    expect(said[1]).toBe(
      "A quick check compares each file's size and time with what Workbench read, so an edit that keeps both is missed. Sync now with a deep check reads every file again and finds it.",
    );
  });

  it("says folders on job sites cannot be added", () => {
    expect(said[2]).toBe(
      "Only folders on the machine Workbench runs on can be added. Folders on your job sites cannot be added yet.",
    );
  });

  it("says a first read of a big folder takes a while, and where to see how far it has got", () => {
    expect(said[3]).toBe(
      "The first read of a big folder takes a while. Files can be searched as each one is ready, and the counts on the folder say how far it has got.",
    );
  });

  it("states each cap from the server's numbers, and what it reads", () => {
    expect(said[4]).toBe(
      "Workbench keeps its own copy of each file it reads, so a search never touches your folder. From one folder it takes up to 10,000 files and 2,048 MB, 12 folders deep, and a file may be up to 50 MiB. Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
    );
    expect(said[5]).toBe(
      "With the files you add yourself, Workbench keeps up to 100,000 passages for you.",
    );
    expect(limitsWords({ ...LIMITS, maxFiles: 500, maxMB: 10, maxDepth: 3 })[4]).toContain(
      "it takes up to 500 files and 10 MB, 3 folders deep,",
    );
  });

  it("says whose limits they are", () => {
    expect(said.at(-1)).toBe("The owner of this Workbench sets these limits.");
  });
});

describe("adding and removing", () => {
  it("tells the owner where folders are given, and what to choose", () => {
    const words = nothingToAddWords(true, false);
    expect(words).toContain("No folder has been given to you yet.");
    expect(words).toContain("Toolbox · Tools");
    expect(words).toContain("Folders · File tools");
    expect(words).toContain("Grant an existing folder");
  });

  it("tells anyone else to ask the owner", () => {
    expect(nothingToAddWords(false, false)).toBe(
      "No folder has been given to you yet. The owner of this Workbench gives folders to people, in Toolbox · Tools. Ask them to give you one.",
    );
  });

  it("says when every folder given is added already, and tells the owner how to be given another", () => {
    expect(nothingToAddWords(true, true)).toBe(
      "Every folder you were given is added. To add another, give yourself one in Toolbox · Tools, under Folders · File tools.",
    );
    expect(nothingToAddWords(false, true)).toBe("Every folder you were given is added.");
  });

  it("says what Remove folder does, and that the folder is never touched", () => {
    expect(removeFolderWords(folder())).toBe(
      "Remove Work notes? Workbench stops checking it, and takes the 12 files it has from it out of your documents: its copies and the text it read from them are deleted, so no chat can find them again. The folder, the files in it and your access to it are never touched. You can add it again.",
    );
    expect(removeFolderWords(folder({ counts: counts({ files: 1 }) }))).toContain(
      "takes the 1 file it has from it out of your documents",
    );
  });

  it("says what Remove folder does for a folder with no files read from it", () => {
    expect(removeFolderWords(waitingFolder())).toBe(
      "Remove Work notes? Workbench stops checking it. The folder, the files in it and your access to it are never touched. You can add it again.",
    );
  });

  it("says what leaving one file out does, and that the folder's file is not touched", () => {
    expect(leaveOutWords("docs/a.md")).toBe(
      "Leave out docs/a.md? Workbench deletes its copy of the file and the text it read from it, and adds /docs/a.md to this folder's ignore list, so that it does not come back at the next check. The file in your folder is not touched.",
    );
  });
});

describe("how soon the folders are asked about again", () => {
  it("is busy while a check is under way or due, or files are being read", () => {
    expect(folderBusy(folder())).toBe(false);
    expect(folderBusy(syncingFolder())).toBe(true);
    expect(folderBusy(waitingFolder())).toBe(true);
    expect(folderBusy(readingFolder())).toBe(true);
    expect(folderBusy(problemFolder())).toBe(false);
    expect(folderBusy(goneFolder())).toBe(false);
    expect(folderBusy(withdrawnFolder())).toBe(false);
  });

  it("asks each second while anything is changing, and after a pause when the last read failed", () => {
    expect(foldersWait(foldersPage([folder(), syncingFolder({}, { id: "g2" })]), NOW, false)).toBe(
      POLL_MS,
    );
    expect(foldersWait(foldersPage([readingFolder()]), NOW, false)).toBe(POLL_MS);
    expect(foldersWait(foldersPage([waitingFolder()]), NOW, true)).toBe(RETRY_MS);
  });

  it("asks each half minute when nothing is going on", () => {
    expect(foldersWait(foldersPage([]), NOW, false)).toBe(IDLE_POLL_MS);
    expect(foldersWait(foldersPage([problemFolder()]), NOW, false)).toBe(IDLE_POLL_MS);
    // Even after a read that failed: there is nothing to hurry for.
    expect(foldersWait(foldersPage([folder({ nextAt: NOW + 3600 })]), NOW, true)).toBe(
      IDLE_POLL_MS,
    );
  });

  it("asks when a check is due, if that is sooner, a moment after so that it has begun", () => {
    expect(foldersWait(foldersPage([folder({ nextAt: NOW + 10 })]), NOW, false)).toBe(11_500);
    expect(foldersWait(foldersPage([folder({ nextAt: NOW + 20 })]), NOW, false)).toBe(21_500);
    // The soonest of several.
    expect(
      foldersWait(
        foldersPage([folder({ nextAt: NOW + 25 }), folder({ id: "g2", nextAt: NOW + 5 })]),
        NOW,
        false,
      ),
    ).toBe(6_500);
  });

  it("does not ask more often than each second when a check is due already", () => {
    expect(foldersWait(foldersPage([folder({ nextAt: NOW - 30 })]), NOW, false)).toBe(POLL_MS);
  });
});
