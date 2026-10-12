/**
 * How the best passages are sorted, on the Documents screen (MR6b): the result sorter in use and
 * where it runs, its speed beside Sort time, or why passages are not sorted in the server's own
 * words with the numbers, for each code the server can say and for each kind of reader (the
 * owner, who has the console; someone who is not; and a screen that does not know). Every sentence
 * of the server's is the one in `tests/fixtures/sort_codes.json`.
 */

import { render, screen } from "@testing-library/react";

import type { Me, SortCode } from "../lib/types";
import { noSorter, SORT_CODES, sortFacts, sortFor, sortSays, SORTER } from "../test-saved";
import { DocumentsSort } from "./DocumentsSort";

type Reader = Pick<Me, "owner" | "consoleUrl">;
const OWNER: Reader = { owner: true, consoleUrl: "http://nas:8279/" };
const OWNER_NO_CONSOLE: Reader = { owner: true, consoleUrl: null };
const PERSON: Reader = { owner: false, consoleUrl: null };

function shown(sort: Parameters<typeof DocumentsSort>[0]["sort"], me: Reader | null = OWNER) {
  return render(<DocumentsSort sort={sort} me={me} />);
}

const box = () => screen.getByTestId("documents-sort");
const has = (id: string) => screen.queryByTestId(id) !== null;
const said = (id: string) => screen.getByTestId(id).textContent ?? "";

/** The codes that are worth a notice: a sorter that was never in play is not. */
const IN_PLAY = SORT_CODES.filter((code) => code !== "off" && code !== "none_set_up");
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

describe("where there is nothing to say of a sorter", () => {
  it("says nothing where there is no gateway to ask about one", () => {
    shown(null);
    expect(has("documents-sort")).toBe(false);
    expect(document.body.textContent).toBe("");
  });

  it("says nothing where the screen was not told of one", () => {
    shown(undefined);
    expect(has("documents-sort")).toBe(false);
  });

  it.each(["off", "none_set_up"] as const)(
    "says nothing for %s, which is never in play and not worth a notice beside every answer",
    (code) => {
      shown(sortFor(code));
      expect(has("documents-sort")).toBe(false);
    },
  );

  it("says nothing of a Workbench that has no sorter set up", () => {
    shown(noSorter());
    expect(has("documents-sort")).toBe(false);
  });
});

describe("for each code the server can say", () => {
  it.each(IN_PLAY)(
    "%s: is a titled notice, a warning only where something needs somebody",
    (code) => {
      shown(sortFor(code));
      expect(box()).toHaveAttribute("data-code", code);
      expect(box()).toHaveAttribute("data-sorting", String(code === "ok"));
      expect(screen.getByTestId("documents-sort-title")).toHaveTextContent(/^Result sorter$/);
      if (WARN.includes(code)) expect(box()).toHaveClass("notice--warn");
      else expect(box()).not.toHaveClass("notice--warn");
    },
  );

  it.each(IN_PLAY.filter((code) => code !== "ok" && code !== "measuring"))(
    "%s: says why, in the server's own sentence, once",
    (code) => {
      shown(sortFor(code));
      expect(said("documents-sort-reason")).toBe(sortSays(code));
      expect(box().textContent?.match(/Not sorted: /g) ?? []).toHaveLength(1);
    },
  );

  it.each(IN_PLAY)("%s: names the sorter as in use only when it is sorting", (code) => {
    shown(sortFor(code));
    expect(has("documents-sort-model")).toBe(code === "ok");
    expect(has("documents-sort-where")).toBe(code === "ok");
    expect(box().textContent?.includes("In use:") ?? false).toBe(code === "ok");
  });

  it.each(IN_PLAY)(
    "%s: gives the owner the console's page that mends it, and a person who is not the owner someone to ask",
    (code) => {
      const owner = shown(sortFor(code), OWNER);
      expect(has("documents-sort-open-backends")).toBe(code === "not_running");
      expect(has("documents-sort-ask-owner")).toBe(false);
      owner.unmount();
      shown(sortFor(code), PERSON);
      expect(has("documents-sort-open-backends")).toBe(false);
      expect(has("documents-sort-ask-owner")).toBe(
        ["not_running", "not_listed", "not_sorter", "outside"].includes(code),
      );
    },
  );
});

describe("passages that are sorted", () => {
  it("says which sorter is in use, the server's label, and where it runs and whose word it is", () => {
    shown(sortFacts());
    expect(said("documents-sort-model")).toBe(`In use: ${SORTER} (automatic)`);
    expect(said("documents-sort-where")).toBe(
      "It runs on your own machines, so what is in your documents stays on them. Automatic means the first result sorter that is running on your own machines.",
    );
    expect(box()).not.toHaveClass("notice--warn");
    expect(has("documents-sort-reason")).toBe(false);
  });

  it("says the owner chose it, when the owner did", () => {
    shown(sortFacts({ automatic: false, label: `In use: ${SORTER}`, setting: SORTER }));
    expect(said("documents-sort-model")).toBe(`In use: ${SORTER}`);
    expect(said("documents-sort-where")).toContain(
      "The owner of this Workbench chose it, in Eugene's console.",
    );
    expect(said("documents-sort-where")).not.toContain("Automatic means");
  });

  it("says its speed and what a usual sort adds to an answer, beside Sort time: the screen shows the speed and the budget", () => {
    shown(sortFacts());
    expect(said("documents-sort-speed")).toBe(
      `${SORTER} sorts about 4,100 tokens a second here, so sorting the 18 passages a question usually finds (about 300 tokens each) adds about 1.4 s to each answer, within Sort time (3 s).`,
    );
    expect(said("documents-sort-speed-from")).toBe(
      "How fast it reads was measured by a quick test before any question was sorted.",
    );
  });

  it("says the speed is measured from its sorts once it has made some", () => {
    shown(sortFacts({ rateFrom: "sorts", ratePerSecond: 5_300, budgetSeconds: 6 }));
    expect(said("documents-sort-speed")).toContain("sorts about 5,300 tokens a second here");
    expect(said("documents-sort-speed")).toContain("within Sort time (6 s)");
    expect(said("documents-sort-speed-from")).toBe(
      "How fast it reads is measured from the sorts Workbench has made.",
    );
  });

  it("warns that the best passages go outside the machines, when the sorter runs there and the owner allowed it", () => {
    shown(
      sortFacts({
        automatic: false,
        locality: "external",
        runsOn: "Cohere",
        allowExternal: true,
        label: "In use: rerank-v3",
      }),
    );
    expect(box()).toHaveClass("notice--warn");
    expect(said("documents-sort-where")).toContain(
      "It runs on Cohere. The owner of this Workbench allowed the best passages of your documents for each question, and the question, to be sent there.",
    );
  });
});

describe("passages that are not sorted because the sorter is too slow", () => {
  it("says why in the server's words, with the numbers, and that it is Sort time to raise", () => {
    shown(sortFor("over_budget"));
    expect(said("documents-sort-reason")).toBe(
      `Not sorted: ${SORTER} sorts about 820 tokens a second here, so sorting the 18 passages a question usually finds (about 300 tokens each) would add about 7 s to each answer, and Sort time is 3 s. Raise Sort time in Workbench's settings (in Eugene's console) to sort anyway.`,
    );
    // Nothing is said to be in use, and the numbers are not said twice.
    expect(box().textContent).not.toContain("In use");
    expect(has("documents-sort-speed")).toBe(false);
    expect(box().textContent?.match(/820 tokens a second/g) ?? []).toHaveLength(1);
  });

  it("is not a warning: nothing is broken, and the passages are as the search found them", () => {
    shown(sortFor("over_budget"));
    expect(box()).not.toHaveClass("notice--warn");
  });

  it("says where its speed came from, beside the sentence that says what it is", () => {
    const probe = shown(sortFor("over_budget"));
    expect(said("documents-sort-speed-from")).toBe(
      "How fast it reads was measured by a quick test before any question was sorted.",
    );
    probe.unmount();
    shown(sortFor("over_budget", { rateFrom: "sorts" }));
    expect(said("documents-sort-speed-from")).toBe(
      "How fast it reads is measured from the sorts Workbench has made.",
    );
  });

  it("does not tell a person who is not the owner to ask for it: the server's sentence says where Sort time is", () => {
    shown(sortFor("over_budget"), PERSON);
    expect(has("documents-sort-ask-owner")).toBe(false);
  });
});

describe("a sorter that is being measured", () => {
  it("says what is being done, which is not a failure, and says no sorter is in use yet", () => {
    shown(sortFor("measuring"));
    expect(said("documents-sort-reason")).toBe(
      `Measuring how fast ${SORTER} sorts, to tell whether it is quick enough.`,
    );
    expect(box()).not.toHaveClass("notice--warn");
    expect(box().textContent).not.toContain("In use");
    expect(has("documents-sort-speed")).toBe(false);
    expect(has("documents-sort-speed-from")).toBe(false);
  });
});

describe("a sorter that is not running", () => {
  it("names it, and links the owner to Backends, where it is started", () => {
    shown(sortFor("not_running", { named: SORTER }), OWNER);
    expect(said("documents-sort-reason")).toBe(sortSays("not_running"));
    const link = screen.getByTestId("documents-sort-open-backends");
    expect(link).toHaveAttribute("href", "http://nas:8279/inference/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAccessibleName("Open Backends");
    expect(box()).toHaveClass("notice--warn");
  });

  it("tells a person who is not the owner to ask the owner, and gives them no link", () => {
    shown(sortFor("not_running"), PERSON);
    expect(has("documents-sort-open-backends")).toBe(false);
    expect(said("documents-sort-ask-owner")).toBe(
      "Ask the owner of this Workbench to fix this in Eugene's console.",
    );
  });

  it("gives the owner no link where the console's address is not known, and the server's words still say where", () => {
    shown(sortFor("not_running"), OWNER_NO_CONSOLE);
    expect(has("documents-sort-open-backends")).toBe(false);
    expect(has("documents-sort-ask-owner")).toBe(false);
    expect(said("documents-sort-reason")).toContain("Start it in Eugene's console under Backends.");
  });

  it("says nothing of the console where it does not know who is reading", () => {
    shown(sortFor("not_running"), null);
    expect(has("documents-sort-open-backends")).toBe(false);
    expect(has("documents-sort-ask-owner")).toBe(false);
  });

  it("links an address with a path to Backends under it", () => {
    shown(sortFor("not_running"), { owner: true, consoleUrl: "https://hub.example/eugene" });
    expect(screen.getByTestId("documents-sort-open-backends")).toHaveAttribute(
      "href",
      "https://hub.example/eugene/inference/",
    );
  });

  it("gives no link to someone who is not the owner, even where the console's address is known", () => {
    shown(sortFor("not_running"), { owner: false, consoleUrl: "http://nas:8279/" });
    expect(has("documents-sort-open-backends")).toBe(false);
    expect(has("documents-sort-ask-owner")).toBe(true);
  });
});

describe("a sorter that cannot be used for a reason that is not the owner's to mend", () => {
  it.each(["limited", "key", "unreachable", "slow", "refused", "unusable", "error"] as const)(
    "%s: says what was observed, and offers the owner nothing in the console to open",
    (code) => {
      shown(sortFor(code), OWNER);
      expect(said("documents-sort-reason")).toBe(sortSays(code));
      expect(has("documents-sort-open-backends")).toBe(false);
      expect(has("documents-sort-ask-owner")).toBe(false);
    },
  );
});

describe("words only a person reads", () => {
  it("says nothing of how it works underneath, in any state", () => {
    // A sorter's name is its own and may have such a word in it: this holds the words the page says.
    const names = /[\w.-]*rerank[\w.-]*/gi;
    for (const code of IN_PLAY) {
      const view = shown(sortFor(code));
      const text = (box().textContent ?? "").replace(names, "a-model");
      expect(text, code).not.toMatch(
        /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*|rerank\w*)\b/i,
      );
      view.unmount();
    }
  });
});
