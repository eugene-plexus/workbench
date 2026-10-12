/**
 * What the Documents screen, a chat's Documents and the passages under an answer say about
 * search by meaning (MR5), in one place. Plain words: "search model", "meaning and words",
 * "words only", "Rebuild search". How it works underneath is never on the way a person reads
 * it (words.test.ts holds this file to that).
 *
 * The server is the authority on every fact: which search model is in use and where it runs,
 * why meaning is not in use, what the person can do about it, how much meaning is added and
 * how long the rest takes (measured from real batches, never a constant), and each document's
 * state. Those are shown as they came. What is composed here is composed only from the
 * fields it sent, so a number on the screen is a number the server counted, and a scale is
 * never shown as another's: words and meaning score on scales that mean nothing beside each
 * other, so a passage is told by the place each put it, and by how it was found.
 */

import { count } from "./documents";
import type {
  AddingView,
  DocumentsPage,
  MeaningRun,
  Places,
  RebuildView,
  SavedDocument,
  SearchCode,
  SearchFacts,
  SearchModelView,
  Via,
  Way,
} from "./types";

// --- how documents are searched, and why ---

/** How a code looks: a state that is as it should be is quiet, one that needs somebody is a
 * warning (with its glyph and its words: never colour alone). */
export type SearchTone = "info" | "warn";

/** Every code the server can say, and how it looks. A code that is added to the server is a
 * failure of the test that reads the server's source, and of the compiler here. */
export const SEARCH_TONE: Record<SearchCode, SearchTone> = {
  ok: "info",
  // Not having set one up is where most people begin, not a fault.
  none_set_up: "info",
  no_gateway: "info",
  not_running: "warn",
  not_listed: "warn",
  not_search_model: "warn",
  outside: "warn",
  outside_only: "warn",
  unreachable: "warn",
  key: "warn",
  limited: "warn",
  slow: "warn",
  refused: "warn",
  error: "warn",
  changed: "warn",
  dimension: "warn",
  damaged: "warn",
};

/** The codes whose mending is the owner's, in Eugene's console: a person who is not the owner
 * is told to ask them, since the server's words name the console. */
export const OWNER_FIXES: readonly SearchCode[] = [
  "none_set_up",
  "not_running",
  "not_listed",
  "not_search_model",
  "outside",
  "outside_only",
];

/** What a person who is not the owner is told, for a code the owner mends in Eugene's console. */
export function ownerWords(code: SearchCode): string | null {
  if (!OWNER_FIXES.includes(code)) return null;
  return code === "none_set_up"
    ? "Ask the owner of this Workbench to set one up in Eugene's console."
    : "Ask the owner of this Workbench to fix this in Eugene's console.";
}

/** The page of Eugene's console that mends a code, when there is one. */
export function consoleStep(code: SearchCode): "library" | "backends" | null {
  if (code === "none_set_up") return "library";
  if (code === "not_running") return "backends";
  return null;
}

/** Where each of those pages is in Eugene's console. What the console calls Backends (the page
 * that starts and stops models) keeps the route it had as Inference, `/inference`, because
 * bookmarks and scripts carry it (the console's own `lib/navigation.ts`); the Library is `/library`. */
export const CONSOLE_ROUTE = { library: "library", backends: "inference" } as const;

/** A page of Eugene's console, from its address: `http://nas:8279/` and `library` make
 * `http://nas:8279/library/`. */
export function consolePage(consoleUrl: string, path: string): string {
  const base = consoleUrl.endsWith("/") ? consoleUrl : `${consoleUrl}/`;
  try {
    return new URL(`${path.replace(/^\/+|\/+$/g, "")}/`, base).toString();
  } catch {
    return `${base}${path}/`;
  }
}

/** The search model that is doing the searching, or null: one that is running and not in use
 * (a search that is paused, or a model that is failing) is not the one in use. */
export function modelInUse(facts: SearchFacts): SearchModelView | null {
  return facts.code === "ok" && facts.model?.resolved && facts.model.label ? facts.model : null;
}

/** How a person is told it looks: a search model in use that runs outside their machines is a
 * warning in itself, whatever else is so, because the text of their documents goes there. */
export function searchTone(facts: SearchFacts): SearchTone {
  const model = modelInUse(facts);
  if (model && model.locality !== "local") return "warn";
  return SEARCH_TONE[facts.code] ?? "warn";
}

const WORDS_ONLY_LEAD = "Searching by words only: ";
const capital = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1);

/** What was a clause after a colon, as a sentence: a plain word at its start has a capital, and
 * a name (`gemma-3`, `qwen3-0.6b`) is left as it is written. */
const sentence = (text: string): string => (/^[a-z]+(?:\s|$)/.test(text) ? capital(text) : text);

/** The code's own words, as a sentence after the headline: the server's `detail`, without
 * the "Searching by words only: " that the headline has already said. Null when there is none
 * or it says what the headline says. */
export function detailWords(facts: SearchFacts): string | null {
  const said = (facts.detail ?? "").trim();
  if (!said) return null;
  const rest = said.startsWith(WORDS_ONLY_LEAD)
    ? sentence(said.slice(WORDS_ONLY_LEAD.length))
    : said;
  return rest === facts.headline.trim() ? null : rest;
}

/** How adding meaning is going, as the server says it, for the header: working, waiting for
 * the search model, or held back by the key's limit. A search model that is not there is said
 * once, in the detail, and not again here. Null when nothing is being added. */
export function addingWords(facts: SearchFacts): string | null {
  const adding: AddingView | null = facts.adding;
  if (!adding || adding.state === "idle" || !adding.message) return null;
  if (adding.state === "waiting" && facts.code !== "ok") return null;
  return adding.message;
}

/** What a chat's drawer says of how documents are searched: the server's headline, why in the
 * code's own words, and where the Rebuild is that mends a paused search. */
export function drawerSearchWords(facts: SearchFacts): { headline: string; more: string[] } {
  const more: string[] = [];
  const detail = detailWords(facts);
  if (detail) more.push(detail);
  if (facts.actions.includes("rebuild")) more.push("Rebuild search is on the Documents screen.");
  return { headline: facts.headline, more };
}

/** Where the search model in use runs, and what that means for the person's text. */
export function whereWords(model: SearchModelView): string | null {
  switch (model.locality) {
    case "local":
      return "It runs on your own machines, so what is in your documents stays on them.";
    case "external":
      return `It runs ${model.runsOn ? `on ${model.runsOn}` : "outside your machines"}. The owner of this Workbench allowed the text of your documents and of your questions to be sent there.`;
    case "unknown":
      return "Eugene cannot say where it runs. The owner of this Workbench allowed models outside your machines, so the text of your documents and of your questions may leave them.";
    default:
      return null;
  }
}

/** Whose word it is that this search model is the one in use. */
export function whoseWords(model: SearchModelView): string {
  return model.automatic
    ? "Automatic means the first search model that is running on your own machines."
    : "The owner of this Workbench chose it, in Eugene's console.";
}

/** Rounded as the server rounds (Python's `round`): a half goes to the even number, so the
 * time in the header and the time in a row never differ by one at a half. */
function roundHalfEven(n: number): number {
  const floor = Math.floor(n);
  const rest = n - floor;
  if (rest < 0.5) return floor;
  if (rest > 0.5) return floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** `less than a minute`, `about 14 minutes`, `about 2 hours`, `about 3 days`: a time as a
 * person says it, the way the server says the time left in each row (a table both read:
 * tests/fixtures/duration_words.json). */
export function durationWords(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = roundHalfEven(seconds / 60);
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = seconds / 3600;
  if (hours < 48) {
    const whole = roundHalfEven(hours * 2) / 2;
    return `about ${whole} hour${whole === 1 ? "" : "s"}`;
  }
  return `about ${roundHalfEven(hours / 24)} days`;
}

/** What Rebuild search does, with its passages and time, so a person knows what they ask for. */
export function rebuildWords(rebuild: RebuildView): string {
  const model = rebuild.model ?? "the search model that is running";
  if (rebuild.passages === 0) {
    return `Rebuild search lets Workbench use ${model}. There are no passages to add meaning to yet.`;
  }
  const time = rebuild.seconds != null ? `, which takes ${durationWords(rebuild.seconds)}` : "";
  return `Rebuild search adds meaning again to ${count(rebuild.passages, "passage")} with ${model}${time}. Searching by words keeps working while it does.`;
}

/** What a rebuild that was asked for came to. */
export function meaningRebuiltWords(made: { passages: number; model: string | null }): string {
  if (made.passages === 0) return "There are no passages to add meaning to yet.";
  const model = made.model ? ` with ${made.model}` : "";
  return `Adding meaning again to ${count(made.passages, "passage")}${model}. Searching by words keeps working while it does.`;
}

/** What the screen is, in one line, for how it searches now. */
export function introWords(facts: SearchFacts): string {
  return facts.meaning
    ? "Your own files, searched for the words in your question and for what it means. Only you can see them."
    : "Your own files, searched for the words in your question. Only you can see them.";
}

/** What an empty list says to do, for how it searches now. */
export function emptyWords(facts: SearchFacts): string {
  return facts.meaning
    ? "Add files above. Workbench reads each one, so a chat with Documents on can find the passages that have the words of your question or say what it asks."
    : "Add files above. Workbench reads each one, so a chat with Documents on can find the passages that have the words of your question.";
}

// --- each document's meaning ---

/** A document read while no search model was running: the reason is the whole of it. */
export const WORDS_ONLY_ROW = "Ready for words only: no search model was running when it was read.";

/** A document's line, from its meaning, for the states that have more to say than "Ready"
 * or "Ready for words": the server's own sentence for a document that is having meaning
 * added (or is waiting for it, or held back by the key's limit), and the count for one whose
 * passages were refused. Null where there is nothing more to say. `tone` is `quiet` for a
 * wait, which a clock says, and `ok` where words are ready and nothing is wrong. */
export function meaningRow(
  doc: Pick<SavedDocument, "state" | "meaning">,
): { tone: "ok" | "quiet"; label: string } | null {
  const meaning = doc.meaning;
  if (!meaning) return null;
  if (doc.state === "ready") {
    if (meaning.state === "words_only") return { tone: "ok", label: WORDS_ONLY_ROW };
    if (meaning.refused > 0) {
      const found = Math.max(0, meaning.embedded - meaning.refused);
      return {
        tone: "ok",
        label: `Ready. Meaning was added to ${found.toLocaleString()} of ${count(meaning.total, "passage")}; ${count(meaning.refused, "passage")} ${meaning.refused === 1 ? "is" : "are"} found by words only.`,
      };
    }
    return null;
  }
  if (!meaning.message) return null;
  return {
    tone: meaning.state === "waiting" || meaning.state === "limited" ? "quiet" : "ok",
    label: meaning.message,
  };
}

/** The totals of meaning at the foot of the list: how many passages have it, and how many
 * are still owed it. Null when no passage has it and none is owed (a search by words only
 * says so in its header). Refused passages are not counted as having it. */
export function meaningTotalsWords(
  page: Pick<DocumentsPage, "totals" | "documents">,
): string | null {
  const { embedded, owed, passages } = page.totals;
  if (embedded === 0 && owed === 0) return null;
  const refused = page.documents.reduce(
    (sum, doc) => sum + (doc.scope === "library" ? (doc.meaning?.refused ?? 0) : 0),
    0,
  );
  const has = Math.max(0, embedded - refused);
  const said: string[] = [];
  if (owed > 0) {
    said.push(
      `Meaning is added to ${has.toLocaleString()} of ${count(passages, "passage")}; ${owed.toLocaleString()} ${owed === 1 ? "is" : "are"} still to do.`,
    );
  } else if (has >= passages) {
    said.push(
      passages === 1 ? "Meaning is added to the passage." : "Meaning is added to every passage.",
    );
  } else {
    said.push(
      `Meaning is added to ${has.toLocaleString()} of ${count(passages, "passage")}. The rest are found by words only.`,
    );
  }
  if (refused > 0) {
    said.push(
      `${count(refused, "passage")} ${refused === 1 ? "was" : "were"} refused by the search model and ${refused === 1 ? "is" : "are"} found by words only.`,
    );
  }
  return said.join(" ");
}

// --- Test a question ---

/** What a tried question may ask to be searched by: both ways, as a chat does, or one of them. */
export type WayChoice = "both" | Way;

export const WAY_CHOICES: { value: WayChoice; label: string }[] = [
  { value: "both", label: "Words and meaning, as a chat does" },
  { value: "words", label: "Words only" },
  { value: "meaning", label: "Meaning only" },
];

/** The ways the server is asked for: none named means both, and then a chat's own rule about
 * a passage that is not close enough applies. */
export const waysOf = (choice: WayChoice): Way[] | null => (choice === "both" ? null : [choice]);

/** How a passage was found, and the words it has of the question: `Found by words`, `Found by
 * meaning`, `Found by both words and meaning`. A passage found by meaning that has none of the
 * words searched for says so, which is the whole point of searching by meaning. */
export function viaWords(via: Via, hasWords: boolean): string {
  if (via === "words") return "Found by words";
  if (via === "both") return "Found by both words and meaning";
  return hasWords ? "Found by meaning" : "Found by meaning: none of your words are in it";
}

/** The row's short form: `found by words`, `found by meaning`, `found by both`. */
export const viaTag = (via: Via): string => `found by ${via}`;

/** A note an answer's search kept about meaning: `meaning_not_running`, `meaning_changed`, and
 * so on, each in the server's own words (why this answer was searched by words only). */
export const isMeaningNote = (code: string): boolean => code.startsWith("meaning_");

const ordinal = (n: number): string => {
  const teen = n % 100 >= 11 && n % 100 <= 13;
  const ending = teen ? "th" : ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] ?? "th");
  return `${n}${ending}`;
};

/** Where each way put a passage in its own list: `Words put it 3rd, meaning put it 1st
 * (closeness 0.62).` Never one number made of two scales. */
export function placesWords(places: Places, similarity: number | null): string | null {
  const said: string[] = [];
  if (places.words != null) said.push(`words put it ${ordinal(places.words)}`);
  if (places.meaning != null) {
    said.push(
      `meaning put it ${ordinal(places.meaning)}${similarity != null ? ` (closeness ${similarity.toFixed(2)})` : ""}`,
    );
  }
  return said.length > 0 ? `${capital(said.join(", "))}.` : null;
}

/** How a question was searched. Said as what was done, and what was asked for. */
export function searchedByWords(mode: string, chose: boolean): string | null {
  const how =
    mode === "meaning and words"
      ? "Searched by meaning and words."
      : mode === "words"
        ? "Searched by words only."
        : mode === "meaning"
          ? "Searched by meaning only."
          : null;
  return how && chose ? `${how} You chose this.` : how;
}

/** What it says when meaning did not run for a tried question, in the server's words: the
 * reason, when there is one worth saying (a search model that was never in play is not). */
export function meaningNote(run: MeaningRun, askedFor: boolean): string | null {
  if (run.used || !run.message) return null;
  return run.quiet && !askedFor ? null : run.message;
}

/** A search that none of the ways asked for could run: said, and never "nothing matched". */
export function unavailableWords(run: MeaningRun): string {
  const why = (run.message ?? "").startsWith(WORDS_ONLY_LEAD)
    ? sentence((run.message ?? "").slice(WORDS_ONLY_LEAD.length))
    : (run.message ?? "");
  return `Nothing was searched: search by meaning could not run.${why ? ` ${why}` : ""}`;
}

/** How close the nearest passage was by meaning, and what the least is that counts when no
 * word matched. Each number is meaning's own: it is not beside words'. */
export function closenessWords(run: MeaningRun): string | null {
  if (run.best == null) return null;
  const best = run.best.toFixed(2);
  if (run.floor == null) return `By meaning, the nearest passage scored ${best}.`;
  const least = run.floor.toFixed(2);
  return run.belowFloor
    ? `By meaning, the nearest passage scored ${best}. When no word matches, a passage needs ${least} to count, so none was kept.`
    : `By meaning, the nearest passage scored ${best}. When no word matches, a passage needs ${least} to count.`;
}

// --- under an answer ---

/** How the passages sent with an answer were found, and by which search model: `Found by
 * meaning and words, with the search model X.` Nothing for a way it does not know. */
export function foundByWords(mode: string, run: MeaningRun | null): string | null {
  const how =
    mode === "words"
      ? "Found by words only"
      : mode === "meaning and words"
        ? "Found by meaning and words"
        : mode === "meaning"
          ? "Found by meaning only"
          : null;
  if (!how) return null;
  return run?.used && run.model ? `${how}, with the search model ${run.model}.` : `${how}.`;
}
