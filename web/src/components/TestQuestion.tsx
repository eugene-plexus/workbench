/**
 * Test a question: type what you would ask in a chat, and see which passages of your
 * documents Workbench would send with it, how each was found (by its words, by what it
 * says, or by both), and which of your words it has. No model is asked and nothing leaves
 * your machines. It is the place to look when an answer misses something that is in a
 * file: whether it is found, where, and by which way.
 *
 * As an expert option a question can be tried one way at a time, by words only or by meaning
 * only, to see what each finds by itself: a run that names its way sets nothing aside, so a
 * passage is listed however near it is. "Nothing matched" is a normal result and is said so;
 * a search that cannot run is the server's own words, never an empty list.
 */

import { ChevronRight, CircleAlert, FileSearch, Info } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { post } from "../lib/api";
import {
  closenessWords,
  meaningNote,
  placesWords,
  searchedByWords,
  viaWords,
  WAY_CHOICES,
  type WayChoice,
  waysOf,
} from "../lib/documentMeaning";
import { failureWords } from "../lib/memory";
import {
  leftOutWords,
  matchedWords,
  searchedCounts,
  searchedFor,
  testedTitle,
  testedWords,
} from "../lib/savedDocuments";
import type { SearchFacts, TestedPassage, TestedQuestion } from "../lib/types";
import { Marked } from "./Marked";
import { ScrollText } from "./ScrollText";

/** The longest question the server takes. */
const MAX_QUESTION = 2000;

/** What was searched: a question that had no words or no documents searched nothing, and the
 * mode the server names then is only how it is set up. */
const SEARCHED: readonly TestedQuestion["status"][] = ["found", "nothing_matched"];

function Hit({ hit }: { hit: TestedPassage }) {
  const uid = useId();
  const matched = matchedWords(hit.why.terms, hit.why.phrases);
  const places = placesWords(hit.why.ranks, hit.why.similarity);
  return (
    <li data-testid="documents-test-hit" className="flex flex-col gap-1 px-4 py-3">
      <p className="wrap-anywhere">
        <span className="tabular-nums text-muted">[{hit.rank}]</span>{" "}
        <span id={`${uid}-title`} data-testid="documents-test-title" className="font-medium">
          {testedTitle(hit)}
        </span>
        {hit.scope === "chat" && <span className="text-muted"> · a file of a chat</span>}
      </p>
      <p
        dir="auto"
        data-testid="documents-test-snippet"
        className="text-sm text-muted wrap-anywhere"
      >
        <Marked text={hit.snippet} words={[...hit.why.terms, ...hit.why.phrases]} />
      </p>
      <p data-testid="documents-test-via" data-via={hit.via} className="text-xs text-muted">
        {viaWords(hit.via, matched !== null)}
      </p>
      {matched && (
        <p data-testid="documents-test-matched" className="text-xs text-muted wrap-anywhere">
          Matched: {matched}
          {hit.why.inHeading.length > 0 && ` (in the heading: ${hit.why.inHeading.join(", ")})`}
        </p>
      )}
      {places && (
        <p data-testid="documents-test-places" className="text-xs text-muted">
          {places}
        </p>
      )}
      <details className="group/w text-sm">
        <summary
          data-testid="documents-test-whole"
          aria-describedby={`${uid}-title`}
          className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
        >
          <FileSearch size={14} aria-hidden />
          Show the whole passage
        </summary>
        <ScrollText
          label={`The whole passage ${hit.rank}`}
          testId="documents-test-text"
          text={hit.text}
        />
      </details>
    </li>
  );
}

/** The expert option: try the question one way at a time. */
function Ways({
  way,
  busy,
  onWay,
}: {
  way: WayChoice;
  busy: boolean;
  onWay: (choice: WayChoice) => void;
}) {
  const uid = useId();
  const chosen = WAY_CHOICES.find((choice) => choice.value === way);
  return (
    <details data-testid="documents-test-ways" className="group/ways text-sm">
      <summary
        data-testid="documents-test-ways-toggle"
        className="disclosure flex w-fit list-none items-center gap-1.5 rounded-plexus pl-1 pr-2 text-muted hover:bg-hover hover:text-fg [&::-webkit-details-marker]:hidden"
      >
        <ChevronRight
          size={14}
          aria-hidden
          className="shrink-0 transition-transform group-open/ways:rotate-90"
        />
        <span>
          Try one way at a time
          {way !== "both" && chosen && <span>: {chosen.label.toLowerCase()}</span>}
        </span>
      </summary>
      <fieldset className="mt-2 flex min-w-0 flex-col gap-1" aria-describedby={`${uid}-help`}>
        <legend className="field-label mb-1">Search by</legend>
        {WAY_CHOICES.map((choice) => (
          <label key={choice.value} className="check">
            <input
              type="radio"
              name={`${uid}-way`}
              value={choice.value}
              data-testid={`documents-test-way-${choice.value}`}
              checked={way === choice.value}
              disabled={busy}
              onChange={() => onWay(choice.value)}
            />
            <span>{choice.label}</span>
          </label>
        ))}
        <p id={`${uid}-help`} className="help">
          A chat uses both ways together, and sets aside a passage that is not near enough in
          meaning when no word matched. Choose one way to see what it finds by itself: here, nothing
          is set aside.
        </p>
      </fieldset>
    </details>
  );
}

export function TestQuestion({ search }: { search?: SearchFacts }) {
  const uid = useId();
  const [question, setQuestion] = useState("");
  const [way, setWay] = useState<WayChoice>("both");
  const [result, setResult] = useState<{
    asked: string;
    found: TestedQuestion;
    chose: boolean;
    meaning: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // Trying one way at a time is for a search that has a second way: not offered where no search
  // model has ever been set up, which has nothing to compare.
  const offerWays = !search || (search.code !== "none_set_up" && search.code !== "no_gateway");

  async function ask(event: FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;
    const ways = offerWays ? waysOf(way) : null;
    setBusy(true);
    setProblem(null);
    // The last result answered another question: it is not left beside a new one that failed.
    setResult(null);
    try {
      const found = await post<TestedQuestion>(
        "/api/documents/test",
        ways ? { question: asked, ways } : { question: asked },
      );
      setResult({
        asked,
        found,
        chose: ways !== null,
        meaning: ways?.includes("meaning") ?? false,
      });
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const found = result?.found;
  const searched = found ? searchedFor(found.query.terms, found.query.phrases) : null;
  const left = found ? leftOutWords(found.didNotFit) : null;
  const how =
    found && SEARCHED.includes(found.status)
      ? searchedByWords(found.mode, result?.chose ?? false)
      : null;
  // A search that could not run says why in its own sentence; it is not said twice.
  const note =
    found && found.status !== "unavailable"
      ? meaningNote(found.meaning, result?.meaning ?? false)
      : null;
  const closeness = found && SEARCHED.includes(found.status) ? closenessWords(found.meaning) : null;
  return (
    <section aria-labelledby={`${uid}-test`} className="section" data-testid="documents-test">
      <h2 id={`${uid}-test`} className="title-section">
        Test a question
      </h2>
      <p className="help">
        Type what you would ask in a chat, and see which passages Workbench would send with it, how
        each was found, and which of your words it has. No model is asked, and nothing leaves your
        machines.
      </p>
      <form className="flex flex-col gap-3" onSubmit={(event) => void ask(event)}>
        <div className="field">
          <label htmlFor={`${uid}-question`} className="field-label">
            Your question
          </label>
          <input
            id={`${uid}-question`}
            data-testid="documents-test-question"
            dir="auto"
            autoComplete="off"
            maxLength={MAX_QUESTION}
            value={question}
            disabled={busy}
            placeholder="What is the code word for the lighthouse inspection?"
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${uid}-problem` : undefined}
            onChange={(event) => setQuestion(event.target.value)}
            className="control"
          />
        </div>
        {offerWays && <Ways way={way} busy={busy} onWay={setWay} />}
        <div>
          <button
            type="submit"
            data-testid="documents-test-ask"
            disabled={busy || !question.trim()}
            className="btn"
          >
            Show the passages
          </button>
        </div>
      </form>
      {problem && (
        <p
          id={`${uid}-problem`}
          role="alert"
          data-testid="documents-test-problem"
          className="notice notice--error"
        >
          <CircleAlert size={16} aria-hidden />
          {problem}
        </p>
      )}
      {result && found && (
        <div
          data-testid="documents-test-result"
          data-status={found.status}
          data-mode={found.mode}
          className="flex flex-col gap-2"
        >
          <p role="status" className="notice" data-testid="documents-test-words">
            <Info size={16} aria-hidden />
            <span>
              <span className="notice-title wrap-anywhere">{result.asked}</span>
              {testedWords(found)}
            </span>
          </p>
          {found.passages.length > 0 && (
            <ol className="divide-y divide-line rounded-plexus border border-line bg-panel">
              {found.passages.map((hit) => (
                <Hit key={`${hit.docId}:${hit.rank}`} hit={hit} />
              ))}
            </ol>
          )}
          <ul
            className="flex flex-col gap-0.5 text-xs text-muted"
            data-testid="documents-test-facts"
          >
            {how && <li data-testid="documents-test-mode">{how}</li>}
            {note && (
              <li data-testid="documents-test-meaning" className="wrap-anywhere">
                {note}
              </li>
            )}
            {closeness && <li data-testid="documents-test-closeness">{closeness}</li>}
            {searched && <li className="wrap-anywhere">{searched}</li>}
            {left && <li>{left}</li>}
            <li>{searchedCounts(found)}</li>
          </ul>
        </div>
      )}
    </section>
  );
}
