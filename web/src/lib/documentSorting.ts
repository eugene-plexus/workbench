/**
 * What the Documents screen and Test a question say about the result sorter (MR6b), in one
 * place. Plain words: "result sorter", "sorted by", "Sort time". How it works underneath is never
 * on the way a person reads it (words.test.ts holds this file to that), and a sorter's score is
 * never shown, only the place it gave a passage.
 *
 * The server is the authority on every fact: which sorter is in use and where it runs, how fast
 * it reads (measured, never assumed), the time a sort may add to an answer, and why passages were
 * not sorted, with the numbers the decision was made of, in its own words. Those are shown as they
 * came. What is composed here is composed only from the fields it sent, so a number on the screen
 * is one the server measured or was set to.
 */

import type { SearchTone } from "./documentMeaning";
import { count } from "./documents";
import type { SortCode, SortFacts, SortRun } from "./types";

// --- how it looks, and what a person can do about it ---

/** Every code the sorter can say, and how it looks: a state that is as it should be is quiet and
 * one that needs somebody is a warning. A code that is added to the server is a failure of the
 * test that reads the server's source, and of the compiler here. */
export const SORT_TONE: Record<SortCode, SearchTone> = {
  ok: "info",
  measuring: "info",
  // Too slow for the time allowed is how a sorter begins on a processor, and nothing is broken:
  // the passages are in the order the search found them, and the words say how to sort anyway.
  over_budget: "info",
  // Never in play, or nothing to sort: not a fault.
  off: "info",
  none_set_up: "info",
  nothing_to_sort: "info",
  no_question: "info",
  not_asked: "info",
  none: "info",
  // Only sorters outside the machines are listed and nobody named one: nothing was asked for that
  // is not done, so it is said and not warned of.
  outside_only: "info",
  // A sorter that is stopped, named wrongly, refused, out of reach or failing needs somebody.
  not_running: "warn",
  not_listed: "warn",
  not_sorter: "warn",
  outside: "warn",
  limited: "warn",
  key: "warn",
  unreachable: "warn",
  slow: "warn",
  refused: "warn",
  unusable: "warn",
  error: "warn",
};

/** The codes whose mending is the owner's, in Eugene's console, and which the server's words do
 * not say whose it is: a person who is not the owner is told to ask them. */
export const SORT_OWNER_FIXES: readonly SortCode[] = [
  "not_running",
  "not_listed",
  "not_sorter",
  "outside",
];

/** What a person who is not the owner is told, for a code the owner mends in Eugene's console. */
export function sortOwnerWords(code: SortCode): string | null {
  return SORT_OWNER_FIXES.includes(code)
    ? "Ask the owner of this Workbench to fix this in Eugene's console."
    : null;
}

/** The page of Eugene's console that mends a code, when there is one: a sorter that is not running
 * is started under Backends. */
export function sortConsoleStep(code: SortCode): "backends" | null {
  return code === "not_running" ? "backends" : null;
}

// --- the Documents screen ---

/** Whether the Documents screen has anything to say of the sorter: not without a look at it, and
 * not when it was never in play (turned off, or none set up), which is not worth a notice. */
export const hasSortWords = (sort: SortFacts | null | undefined): sort is SortFacts =>
  sort != null && !sort.quiet;

/** The sorter that is doing the sorting, or null: one that is chosen and not in use (too slow,
 * stopped, still being measured) is not the one in use. */
export const sorterInUse = (sort: SortFacts): boolean =>
  sort.sorting && sort.model != null && sort.label != null;

/** How a person is told it looks: a sorter in use that runs outside their machines is a warning in
 * itself, whatever else is so, because the best passages of their documents go there. */
export function sortTone(sort: SortFacts): SearchTone {
  if (sorterInUse(sort) && sort.locality !== "local") return "warn";
  return SORT_TONE[sort.code] ?? "warn";
}

/** Where the sorter in use runs, and what that means for the person's text. */
export function sortWhereWords(sort: SortFacts): string | null {
  switch (sort.locality) {
    case "local":
      return "It runs on your own machines, so what is in your documents stays on them.";
    case "external":
      return `It runs ${sort.runsOn ? `on ${sort.runsOn}` : "outside your machines"}. The owner of this Workbench allowed the best passages of your documents for each question, and the question, to be sent there.`;
    case "unknown":
      return "Eugene cannot say where it runs. The owner of this Workbench allowed models outside your machines, so the best passages of your documents for each question, and the question, may leave them.";
    default:
      return null;
  }
}

/** Whose word it is that this sorter is the one in use. */
export function sortWhoseWords(sort: SortFacts): string {
  return sort.automatic
    ? "Automatic means the first result sorter that is running on your own machines."
    : "The owner of this Workbench chose it, in Eugene's console.";
}

/** A time as a person reads it, the way the server says one in its own sentences: `7 s`, `6.9 s`,
 * `0.45 s`, never more figures than it has (tests/fixtures/sort_words.json, which both read). */
export function secondsWords(seconds: number): string {
  const places = seconds >= 10 ? 0 : seconds >= 1 ? 1 : 2;
  const text = seconds.toFixed(places);
  return `${text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text} s`;
}

/** A speed as a person reads it, to two figures: `about 820 tokens a second`, as the server says
 * it (tests/fixtures/sort_words.json). */
export function rateWords(rate: number): string {
  const whole = String(Math.trunc(rate)).length;
  const step = rate >= 100 ? 10 ** (whole - 2) : 1;
  return `about ${(Math.round(rate / step) * step).toLocaleString()} tokens a second`;
}

/** What the sorter in use costs, from the numbers it was measured at: how fast it reads, what a
 * sort of the usual size (the 18 passages a question usually finds) adds to an answer, and Sort
 * time, how long a sort may be expected to take. Null where there is no sorter in use, or nothing
 * measured yet.
 * A sorter that is too slow says this in the server's own sentence, which is not said twice. It
 * says what a usual sort costs: a question whose passages are longer may cost more, and says so
 * for itself in Test a question. */
export function sortSpeedWords(sort: SortFacts): string | null {
  const { typical } = sort;
  if (!sorterInUse(sort) || sort.ratePerSecond == null || typical.seconds == null) return null;
  return `${sort.model} sorts ${rateWords(sort.ratePerSecond)} here, so sorting the ${count(typical.passages, "passage")} a question usually finds (about ${typical.passageTokens.toLocaleString()} tokens each) adds about ${secondsWords(typical.seconds)} to each answer, within Sort time (${secondsWords(sort.budgetSeconds)}).`;
}

/** Where the speed came from: a quick test of the sorter before any question was sorted, or the
 * sorts Workbench has made since. Null when there is no speed. */
export function sortSpeedFromWords(sort: SortFacts): string | null {
  if (sort.ratePerSecond == null) return null;
  if (sort.rateFrom === "probe") {
    return "How fast it reads was measured by a quick test before any question was sorted.";
  }
  if (sort.rateFrom === "sorts") {
    return "How fast it reads is measured from the sorts Workbench has made.";
  }
  return null;
}

/** Why passages are not sorted, in the server's own words with the numbers (`Not sorted: ...`);
 * and, while the sorter has not been measured, what is being done, which the server has no
 * sentence for. Null where passages are sorted. */
export function sortReasonWords(sort: SortFacts): string | null {
  if (sorterInUse(sort)) return null;
  if (sort.code === "measuring") {
    return `Measuring how fast ${sort.model ?? "the result sorter"} sorts, to tell whether it is quick enough.`;
  }
  return sort.message;
}

// --- Test a question ---

/** Why the passages of a tried question were not sorted, in the server's words with the numbers
 * (`Not sorted: ...`), when there is one worth saying: a sorter that was never in play (turned
 * off, none set up, nothing to sort) is not. There is no way to ask for it, so nothing is said
 * for a question that did not need one. Null when they were sorted. */
export function sortNote(run: SortRun | null | undefined): string | null {
  if (!run || run.used || run.quiet) return null;
  return run.message;
}

/** What a sort did, for a question whose passages were sorted: how many it put in order and how
 * long that took (and Sort time, what a sort may be expected to take). Null when they were not. */
export function sortedWords(run: SortRun | null | undefined): string | null {
  if (!run?.used) return null;
  const took = run.seconds != null ? ` in ${secondsWords(run.seconds)}` : "";
  const allowed =
    run.budgetSeconds != null ? ` (Sort time is ${secondsWords(run.budgetSeconds)})` : "";
  return `The sorter put ${count(run.passages, "passage")} in order${took}${allowed}.`;
}
