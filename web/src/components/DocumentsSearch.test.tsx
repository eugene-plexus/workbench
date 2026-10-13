/**
 * How the documents are searched, at the top of the Documents screen (MR5): the server's
 * headline from a live look, why in the code's own words, the search model in use and where it
 * runs, how adding meaning is going, and what a person can do about it, for each code the
 * server can say and for each kind of reader (the owner, who has the console; someone who is
 * not; and a screen that does not know).
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import type { Me, SearchCode } from "../lib/types";
import {
  addingView,
  byMeaning,
  CODES,
  modelView,
  noSearchModel,
  paused,
  searchFor,
  sortFacts,
  sortFor,
  SORTER,
  wordsOnly,
} from "../test-saved";
import { DocumentsSearch } from "./DocumentsSearch";

type Reader = Pick<Me, "owner" | "consoleUrl">;
const OWNER: Reader = { owner: true, consoleUrl: "http://nas:8279/" };
const OWNER_NO_CONSOLE: Reader = { owner: true, consoleUrl: null };
const PERSON: Reader = { owner: false, consoleUrl: null };

function shown(
  facts: ReturnType<typeof byMeaning>,
  me: Reader | null = OWNER,
  onRebuild = vi.fn(),
  busy = false,
) {
  const view = render(<DocumentsSearch facts={facts} me={me} busy={busy} onRebuild={onRebuild} />);
  return { ...view, onRebuild };
}

const box = () => screen.getByTestId("documents-search");
const has = (id: string) => screen.queryByTestId(id) !== null;

describe("for each code the server can say", () => {
  it.each(CODES)(
    "%s: says the server's headline, in a notice that is a warning only where something needs somebody",
    (code) => {
      const facts = searchFor(code);
      shown(facts);
      expect(box()).toHaveAttribute("data-code", code);
      expect(box()).toHaveAttribute("data-mode", facts.mode);
      expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(facts.headline);
      const quiet: SearchCode[] = ["ok", "none_set_up", "no_gateway"];
      if (quiet.includes(code)) expect(box()).not.toHaveClass("notice--warn");
      else expect(box()).toHaveClass("notice--warn");
    },
  );

  it.each(CODES)("%s: says why, in the code's own words, once", (code) => {
    const facts = searchFor(code);
    shown(facts);
    if (facts.detail === null) {
      expect(has("documents-search-detail")).toBe(false);
      return;
    }
    const detail = screen.getByTestId("documents-search-detail").textContent ?? "";
    // The server's sentence, less the words the headline has said.
    expect(facts.detail).toContain(detail.slice(1));
    expect(detail).not.toMatch(/^Searching by words only/);
    expect(box().textContent?.match(/Searching by words only: /g) ?? []).toHaveLength(0);
  });

  it.each(CODES)(
    "%s: says what words only finds, for as long as meaning is not in use, and not otherwise",
    (code) => {
      shown(searchFor(code));
      if (code === "ok") {
        expect(has("documents-search-help")).toBe(false);
        expect(box()).not.toHaveTextContent("it does not look for meaning");
      } else {
        expect(screen.getByTestId("documents-search-help")).toHaveTextContent(
          "“costs” does not find “cost”",
        );
        expect(box()).toHaveTextContent("it does not look for meaning");
      }
    },
  );

  it.each(CODES)(
    "%s: offers Rebuild only where the server offers it, with what it does",
    (code) => {
      const facts = searchFor(code);
      const { onRebuild } = shown(facts);
      const offered = facts.actions.includes("rebuild");
      expect(has("documents-meaning-rebuild")).toBe(offered);
      expect(has("documents-meaning-rebuild-words")).toBe(offered);
      if (!offered) return;
      expect(screen.getByTestId("documents-meaning-rebuild-words")).toHaveTextContent(
        "Rebuild search adds meaning again to 3,000 passages with nomic-embed-text-v1.5, which takes about 12 minutes.",
      );
      const button = screen.getByTestId("documents-meaning-rebuild");
      expect(button).toHaveAccessibleName("Rebuild search");
      expect(button).toHaveAccessibleDescription(/adds meaning again to 3,000 passages/);
      fireEvent.click(button);
      expect(onRebuild).toHaveBeenCalledOnce();
    },
  );

  it.each(CODES)(
    "%s: names the search model in use only when search by meaning is in use",
    (code) => {
      // A search model that is running, but not in use, is not said to be in use.
      shown(searchFor(code, { model: modelView() }));
      expect(has("documents-search-model")).toBe(code === "ok");
      expect(has("documents-search-where")).toBe(code === "ok");
    },
  );

  it.each(CODES)(
    "%s: gives the owner the console's pages that mend it, and a person who is not the owner someone to ask",
    (code) => {
      const facts = searchFor(code);
      const owner = shown(facts, OWNER);
      const wantsModel = facts.actions.includes("get_search_model");
      expect(has("documents-get-search-model")).toBe(wantsModel);
      expect(has("documents-open-backends")).toBe(code === "not_running");
      expect(has("documents-ask-owner")).toBe(false);
      owner.unmount();
      shown(facts, PERSON);
      expect(has("documents-get-search-model")).toBe(false);
      expect(has("documents-open-backends")).toBe(false);
      expect(has("documents-ask-owner")).toBe(
        [
          "none_set_up",
          "not_running",
          "not_listed",
          "not_search_model",
          "outside",
          "outside_only",
        ].includes(code),
      );
    },
  );
});

describe("meaning and words", () => {
  it("says which search model is in use, the server's label, and where it runs and whose word it is", () => {
    shown(byMeaning());
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by meaning and words.",
    );
    expect(screen.getByTestId("documents-search-model")).toHaveTextContent(
      "In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)",
    );
    expect(screen.getByTestId("documents-search-where")).toHaveTextContent(
      "It runs on your own machines, so what is in your documents stays on them. Automatic means the first search model that is running on your own machines.",
    );
    expect(box()).not.toHaveClass("notice--warn");
  });

  it("says the owner chose it, when the owner did", () => {
    shown(
      byMeaning({ model: modelView({ automatic: false, label: "In use: nomic-embed-text-v1.5" }) }),
    );
    expect(screen.getByTestId("documents-search-model")).toHaveTextContent(
      "In use: nomic-embed-text-v1.5",
    );
    expect(screen.getByTestId("documents-search-where")).toHaveTextContent(
      "The owner of this Workbench chose it, in Eugene's console.",
    );
  });

  it("warns that the text goes outside the machines, when the model runs there and the owner allowed it", () => {
    shown(
      byMeaning({
        model: modelView({
          locality: "external",
          runsOn: "OpenAI",
          allowExternal: true,
          automatic: false,
        }),
      }),
    );
    expect(box()).toHaveClass("notice--warn");
    expect(screen.getByTestId("documents-search-where")).toHaveTextContent(
      "It runs on OpenAI. The owner of this Workbench allowed the text of your documents and of your questions to be sent there.",
    );
  });

  it("says when the search model's prefixes are not known, in the server's words", () => {
    const note =
      "Workbench does not know which prefixes some-model wants on a question and on a passage, so it uses none. Search by meaning may be a little less exact.";
    shown(
      byMeaning({
        model: modelView({
          prefixes: { known: false, family: null, query: "", document: "", note },
        }),
      }),
    );
    expect(screen.getByTestId("documents-search-note")).toHaveTextContent(note);
  });

  it("says nothing of prefixes for a model whose are known", () => {
    shown(byMeaning());
    expect(has("documents-search-note")).toBe(false);
  });

  it("says how adding meaning is going, as the server says it", () => {
    shown(
      byMeaning({
        adding: addingView({
          state: "working",
          message: "Adding meaning: 1,240 of 3,000 passages, about 14 minutes left.",
        }),
      }),
    );
    expect(screen.getByTestId("documents-search-adding")).toHaveTextContent(
      "Adding meaning: 1,240 of 3,000 passages, about 14 minutes left.",
    );
  });

  it("says the key's limit, which nothing else on the screen says", () => {
    shown(
      byMeaning({
        adding: addingView({
          state: "limited",
          message:
            "Adding meaning to your documents is slow right now: this Workbench's key is at its limit.",
        }),
      }),
    );
    expect(screen.getByTestId("documents-search-adding")).toHaveTextContent("slow right now");
  });

  it("says nothing about adding when nothing is being added", () => {
    shown(byMeaning());
    expect(has("documents-search-adding")).toBe(false);
  });
});

describe("no search model set up", () => {
  it("links the owner to the Library in Eugene's console, and says Workbench cannot download models", () => {
    shown(noSearchModel(), OWNER);
    const link = screen.getByTestId("documents-get-search-model");
    expect(link).toHaveAttribute("href", "http://nas:8279/library/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAccessibleName("Get a search model");
    expect(box()).toHaveTextContent("Eugene's console downloads models; Workbench cannot.");
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Searching by words only. No search model is running.",
    );
    expect(screen.getByTestId("documents-search-detail")).toHaveTextContent(
      "No search model is set up.",
    );
  });

  it("tells the owner where to get one when the console's address is not known", () => {
    shown(noSearchModel(), OWNER_NO_CONSOLE);
    expect(has("documents-get-search-model")).toBe(false);
    expect(screen.getByTestId("documents-get-search-model-words")).toHaveTextContent(
      "Get a search model in Eugene's console, under Library. Workbench cannot download models.",
    );
  });

  it("tells a person who is not the owner to ask the owner", () => {
    shown(noSearchModel(), PERSON);
    expect(has("documents-get-search-model")).toBe(false);
    expect(screen.getByTestId("documents-ask-owner")).toHaveTextContent(
      "Ask the owner of this Workbench to set one up in Eugene's console.",
    );
  });

  it("says nothing of the console where it does not know who is reading", () => {
    shown(noSearchModel(), null);
    expect(has("documents-get-search-model")).toBe(false);
    expect(has("documents-get-search-model-words")).toBe(false);
    expect(has("documents-ask-owner")).toBe(false);
  });

  it("links an address with a path to the Library under it", () => {
    shown(noSearchModel(), { owner: true, consoleUrl: "https://hub.example/eugene" });
    expect(screen.getByTestId("documents-get-search-model")).toHaveAttribute(
      "href",
      "https://hub.example/eugene/library/",
    );
  });
});

describe("a search model that is not running", () => {
  it("names it, and links the owner to Backends, where it is started", () => {
    shown(searchFor("not_running"), OWNER);
    expect(screen.getByTestId("documents-search-detail")).toHaveTextContent(
      "The search model Qwen3-Embedding-0.6B-Q8_0 is not running. Start it in Eugene's console under Backends.",
    );
    expect(screen.getByTestId("documents-open-backends")).toHaveAttribute(
      "href",
      "http://nas:8279/inference/",
    );
  });
});

describe("a search that is paused", () => {
  it("says what it was built with and what is running, with Rebuild search, its passages and its time", () => {
    const { onRebuild } = shown(paused());
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Search by meaning is paused: this search was built with Qwen3-Embedding-0.6B-Q8_0 and nomic-embed-text-v1.5 is running now.",
    );
    expect(screen.getByTestId("documents-search-detail")).toHaveTextContent(
      "Your documents are searched by words until you choose Rebuild",
    );
    expect(screen.getByTestId("documents-meaning-rebuild-words")).toHaveTextContent(
      "Rebuild search adds meaning again to 3,000 passages with nomic-embed-text-v1.5, which takes about 12 minutes. Searching by words keeps working while it does.",
    );
    fireEvent.click(screen.getByTestId("documents-meaning-rebuild"));
    expect(onRebuild).toHaveBeenCalledOnce();
    expect(box()).toHaveClass("notice--warn");
  });

  it("is not a model in use: the running one is not said to be", () => {
    shown(paused());
    expect(has("documents-search-model")).toBe(false);
  });

  it("holds Rebuild while a change is made", () => {
    shown(paused(), OWNER, vi.fn(), true);
    expect(screen.getByTestId("documents-meaning-rebuild")).toBeDisabled();
  });

  it("does not say a time that was not measured, and says what to do when there is nothing to add to", () => {
    shown(paused({ rebuild: { passages: 3000, seconds: null, model: "m" } }));
    expect(screen.getByTestId("documents-meaning-rebuild-words")).not.toHaveTextContent("takes");
  });
});

describe("words only, said as what it is", () => {
  it("promises nothing: no 'soon', no 'later', no 'will', no 'coming'", () => {
    shown(wordsOnly());
    expect(box().textContent).not.toMatch(/\b(soon|later|will|coming|meaning and words)\b/i);
    expect(screen.getByTestId("documents-search-help")).toHaveTextContent(
      "A passage is found when it has the words of your question, spelled the same way: “costs” does not find “cost”, and it does not look for meaning.",
    );
  });

  it("says nothing of how it works underneath, whatever the code", () => {
    // A search model's name is its own and may have such a word in it (the server's sentences
    // name the ones in these samples): this holds the words the page says, so names are left out.
    const plain = modelView({
      resolved: "qwen3-search",
      label: "In use: qwen3-search (automatic)",
    });
    const names = /Qwen3-Embedding-0\.6B-Q8_0|text-embedding-3-small/g;
    for (const code of CODES) {
      const view = shown(searchFor(code, { model: plain }));
      const said = (box().textContent ?? "").replace(names, "a-model");
      expect(said, code).not.toMatch(/\b(RAG|vectors?|embeddings?|chunks?|retriev\w*)\b/i);
      view.unmount();
    }
  });
});

describe("what is offered is what the server offers, and to whom", () => {
  it("offers Rebuild only when the server lists it as something to do, whatever else it sent", () => {
    shown(byMeaning({ rebuild: { passages: 5, seconds: 60, model: "m" } }));
    expect(has("documents-meaning-rebuild")).toBe(false);
    expect(has("documents-meaning-rebuild-words")).toBe(false);
  });

  it("offers nothing when it lists Rebuild but sent nothing to say what it would do", () => {
    shown(paused({ rebuild: null }));
    expect(has("documents-meaning-rebuild")).toBe(false);
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      "Search by meaning is paused",
    );
  });

  it("gives no link to someone who is not the owner, even where the console's address is known", () => {
    const person = { owner: false, consoleUrl: "http://nas:8279/" };
    shown(noSearchModel(), person);
    expect(has("documents-get-search-model")).toBe(false);
    expect(has("documents-ask-owner")).toBe(true);
    cleanup();
    shown(searchFor("not_running"), person);
    expect(has("documents-open-backends")).toBe(false);
  });
});

// --- the result sorter (MR6b) ---

describe("how the documents are searched, with a result sorter", () => {
  const sorted = (over: Partial<ReturnType<typeof byMeaning>> = {}) =>
    byMeaning({ mode: `meaning and words, sorted by ${SORTER}`, sort: sortFacts(), ...over });

  it("says the sorter in the headline while it puts the best passages in order", () => {
    shown(sorted());
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      `Searching by meaning and words, sorted by ${SORTER}.`,
    );
    expect(box()).toHaveAttribute("data-mode", `meaning and words, sorted by ${SORTER}`);
    expect(box()).not.toHaveClass("notice--warn");
  });

  it("still says which search model is in use, and where it runs, as before", () => {
    shown(sorted());
    expect(screen.getByTestId("documents-search-model")).toHaveTextContent(
      "In use: Qwen3-Embedding-0.6B-Q8_0 (automatic)",
    );
    expect(screen.getByTestId("documents-search-where")).toHaveTextContent(
      "It runs on your own machines",
    );
  });

  it("says nothing of a sorter that is not sorting: the headline is the server's own", () => {
    shown(byMeaning({ sort: sortFor("over_budget") }));
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      /^Searching by meaning and words\.$/,
    );
    expect(box()).toHaveAttribute("data-mode", "meaning and words");
  });

  it("says it of a search by words, and keeps the rest of the server's sentence", () => {
    shown(
      noSearchModel({
        mode: `words, sorted by ${SORTER}` as never,
        sort: sortFacts(),
      }),
    );
    expect(screen.getByTestId("documents-search-headline")).toHaveTextContent(
      `Searching by words only, sorted by ${SORTER}. No search model is running.`,
    );
  });
});
