/**
 * Test a question: type what you would ask in a chat, and see which passages of your
 * documents Workbench would send with it, and which of your words each was found by.
 * No model is asked and nothing leaves your machines. It is the place to look when an
 * answer misses something that is in a file: whether the words are found, and where.
 * "Nothing matched" is a normal result and is said so; a search that cannot run is
 * the server's own words, never an empty list.
 */

import { CircleAlert, FileSearch, Info } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { post } from "../lib/api";
import { failureWords } from "../lib/memory";
import {
  leftOutWords,
  matchedWords,
  searchedCounts,
  searchedFor,
  testedTitle,
  testedWords,
} from "../lib/savedDocuments";
import type { TestedPassage, TestedQuestion } from "../lib/types";
import { Marked } from "./Marked";
import { ScrollText } from "./ScrollText";

/** The longest question the server takes. */
const MAX_QUESTION = 2000;

function Hit({ hit }: { hit: TestedPassage }) {
  const uid = useId();
  const matched = matchedWords(hit.why.terms, hit.why.phrases);
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
      {matched && (
        <p data-testid="documents-test-matched" className="text-xs text-muted wrap-anywhere">
          Matched: {matched}
          {hit.why.inHeading.length > 0 && ` (in the heading: ${hit.why.inHeading.join(", ")})`}
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

export function TestQuestion() {
  const uid = useId();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<{ asked: string; found: TestedQuestion } | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function ask(event: FormEvent) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;
    setBusy(true);
    setProblem(null);
    // The last result answered another question: it is not left beside a new one that failed.
    setResult(null);
    try {
      const found = await post<TestedQuestion>("/api/documents/test", { question: asked });
      setResult({ asked, found });
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }

  const found = result?.found;
  const searched = found ? searchedFor(found.query.terms, found.query.phrases) : null;
  const left = found ? leftOutWords(found.didNotFit) : null;
  return (
    <section aria-labelledby={`${uid}-test`} className="section" data-testid="documents-test">
      <h2 id={`${uid}-test`} className="title-section">
        Test a question
      </h2>
      <p className="help">
        Type what you would ask in a chat, and see which passages Workbench would send with it and
        which of your words each was found by. No model is asked, and nothing leaves your machines.
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
            {searched && <li className="wrap-anywhere">{searched}</li>}
            {left && <li>{left}</li>}
            <li>{searchedCounts(found)}</li>
          </ul>
        </div>
      )}
    </section>
  );
}
