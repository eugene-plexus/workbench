/**
 * Under an answer: what searching the person's documents came to (MR4). The
 * passages that were sent to the model are listed as the file, the page (or the
 * lines) and the heading each came from, with the start of the passage and the
 * words it was found by. The list says what was SENT, never what the model used:
 * a passage is marked only when the answer's own text has its number in it, like
 * [2]. Nothing found is a normal result and is said quietly; a search that was
 * not run, or that failed, is said in its own words, never as "nothing matched".
 *
 * The install's owner, reading someone else's chat, sees "a document" for each
 * passage and nothing of which (saved documents are never the owner's to read).
 */

import { ChevronRight, CircleCheck, FileSearch, FileText, Info, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { post } from "../lib/api";
import { failureWords } from "../lib/memory";

import { isMeaningNote, searchedByWords, viaTag, viaWords } from "../lib/documentMeaning";
import type { HiddenPassage, PassageSource, SearchRecord } from "../lib/types";
import {
  canRebuild,
  hintFor,
  isHidden,
  leftOutWords,
  matchedWords,
  modeWords,
  passageTitle,
  ranWords,
  searchedFor,
  sentWords,
} from "../lib/savedDocuments";
import { Marked } from "./Marked";
import { PageView } from "./PageView";

function Passage({ source }: { source: PassageSource }) {
  const [pageOpen, setPageOpen] = useState(false);
  const matched = matchedWords(source.terms, source.phrases);
  const cited = source.cited === true;
  return (
    <li
      data-testid="passage-source"
      data-cited={cited ? "true" : "false"}
      data-via={source.via}
      // A citation's [n] in the answer opens this one (lib/citations.ts).
      data-passage-rank={source.rank}
    >
      <details className="group/p">
        <summary
          data-testid="passage-toggle"
          // Its name is the number, the file and where in it, how it was found, and whether
          // the answer cites it: said the same wherever the pieces of the row are laid out.
          aria-label={`[${source.rank}] ${passageTitle(source)}, ${viaTag(source.via)}${cited ? ", cited in the answer" : ""}`}
          className="disclosure flex list-none items-start gap-1.5 rounded-plexus pl-1 pr-2 hover:bg-hover [&::-webkit-details-marker]:hidden"
        >
          <ChevronRight
            size={14}
            aria-hidden
            className="mt-1 shrink-0 transition-transform group-open/p:rotate-90"
          />
          <span className="min-w-0 flex-1 wrap-anywhere">
            <span data-testid="passage-number" className="tabular-nums text-muted">
              [{source.rank}]
            </span>{" "}
            <span data-testid="passage-title">{passageTitle(source)}</span>
            {source.scope === "chat" && (
              <span className="text-muted" data-testid="passage-scope">
                {" "}
                · a file of this chat
              </span>
            )}
            <span className="text-muted" data-testid="passage-via">
              {" "}
              · <span className="whitespace-nowrap">{viaTag(source.via)}</span>
            </span>
          </span>
          {cited && (
            <span
              data-testid="passage-cited"
              className="badge badge--ok shrink-0"
              title={`The answer's text has [${source.rank}] in it`}
            >
              <CircleCheck size={12} aria-hidden />
              Cited
            </span>
          )}
        </summary>
        <div className="ml-6 mt-1 flex flex-col gap-1">
          {source.wholeParts && <p>Read whole, in {source.wholeParts} parts</p>}
          {source.readBy && (
            <p>
              {source.page_end != null && source.page_end !== source.page_start
                ? `pages ${source.page_start} to ${source.page_end} (includes pages read from pictures)`
                : `page ${source.page_start} (read from a picture)`}{" "}
              · {source.readBy}
            </p>
          )}
          <p dir="auto" data-testid="passage-snippet" className="text-muted wrap-anywhere">
            <Marked text={source.snippet} words={[...source.terms, ...source.phrases]} />
          </p>
          {source.via !== "words" && (
            <p data-testid="passage-found" className="text-xs text-muted wrap-anywhere">
              {viaWords(source.via, matched !== null)}
            </p>
          )}
          {matched && (
            <p data-testid="passage-matched" className="text-xs text-muted wrap-anywhere">
              Matched: {matched}
            </p>
          )}
          {source.type === "pdf" && source.page_start != null && (
            <button
              type="button"
              data-testid="passage-show-page"
              className="btn self-start"
              onClick={() => setPageOpen(true)}
            >
              Show page {source.page_start}
            </button>
          )}
          {pageOpen && (
            <PageView
              document={source.doc_id}
              name={source.name}
              initialPage={source.page_start ?? 1}
              passage={source.chunk_ids[0]}
              onClose={() => setPageOpen(false)}
            />
          )}
        </div>
      </details>
    </li>
  );
}

function WholeNotice({ record, documents }: { record: SearchRecord; documents: string[] }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const offer = record.whole;
  if (!offer) return null;
  async function read() {
    if (!offer?.action) return;
    setBusy(true);
    setProblem(null);
    try {
      await post(offer.action, {
        documents: offer.selectByQuestion ? [] : [...new Set(documents)],
        cost: offer.cost,
      });
    } catch (failure) {
      setProblem(failureWords(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="my-2 flex flex-col items-start gap-2" data-testid="whole-document-notice">
      <p>{offer.line}</p>
      {offer.state === "offered" && (
        <>
          <p>{offer.cost}</p>
          <button
            type="button"
            data-testid="whole-document-read"
            className="btn"
            disabled={busy}
            onClick={() => void read()}
          >
            Read all of it
          </button>
        </>
      )}
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}

/** The passages that went with the question: the file, the page, the heading. */
export function PassageList({
  passages,
  record,
}: {
  passages: (PassageSource | HiddenPassage)[];
  record: SearchRecord | null;
}) {
  const shown = passages.filter((p): p is PassageSource => !isHidden(p));
  const hidden = passages.length - shown.length;
  const documents = new Set(shown.map((p) => p.doc_id)).size;
  const searched = record ? searchedFor(record.terms, record.phrases) : null;
  const how = record ? modeWords(record.mode, record.meaning) : null;
  const left = record ? leftOutWords(record.didNotFit) : null;
  return (
    <div data-testid="passage-sources">
      {record && shown.length > 0 && (
        <WholeNotice record={record} documents={shown.map((p) => p.doc_id)} />
      )}
      {shown.length > 0 && (
        <>
          {record?.whole?.state !== "read" && (
            <p className="text-muted" data-testid="passage-sent">
              {sentWords(shown.length, documents)}
            </p>
          )}
          <ol className="mt-1 flex flex-col gap-1">
            {shown.map((source) => (
              <Passage key={`${source.doc_id}:${source.rank}`} source={source} />
            ))}
          </ol>
        </>
      )}
      {hidden > 0 && (
        <div data-testid="passages-redacted" className={shown.length > 0 ? "mt-2" : ""}>
          <p className="text-muted">
            Sent to the model: {hidden === 1 ? "1 passage" : `${hidden} passages`} of the
            person&apos;s documents.
          </p>
          <ol className="mt-1 flex flex-col gap-1">
            {passages.filter(isHidden).map((source, at) => (
              <li
                key={at}
                data-testid="redacted-passage"
                className="flex items-center gap-1.5 pl-1"
              >
                <FileText size={14} aria-hidden className="shrink-0 text-muted" />
                <span className="tabular-nums text-muted">
                  {source.rank != null ? `[${source.rank}]` : ""}
                </span>{" "}
                {source.name}
              </li>
            ))}
          </ol>
          <p className="mt-1 text-xs text-muted">
            A person&apos;s documents are private to them, even where you may read their chats.
          </p>
        </div>
      )}
      {(searched || how || left || record?.notes.length) && (
        <ul data-testid="passage-facts" className="mt-2 flex flex-col gap-0.5 text-xs text-muted">
          {searched && <li className="wrap-anywhere">{searched}</li>}
          {how && <li>{how}</li>}
          {left && <li data-testid="passage-left-out">{left}</li>}
          {record?.notes.map((note, at) => (
            <li
              key={`${note.code}:${at}`}
              data-testid={isMeaningNote(note.code) ? "passage-meaning-note" : undefined}
              data-code={note.code}
              className="wrap-anywhere"
            >
              {note.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Nothing in the documents matched: a normal result, said quietly. */
export function NothingMatched({ record }: { record: SearchRecord }) {
  const searched = searchedFor(record.terms, record.phrases);
  const how = searchedByWords(record.mode, false);
  return (
    <div data-testid="search-nothing">
      <p className="flex items-start gap-1.5 text-muted">
        <FileSearch size={14} aria-hidden className="mt-1 shrink-0" />
        {ranWords(record)}
      </p>
      {searched && <p className="ml-5 mt-0.5 text-xs text-muted wrap-anywhere">{searched}</p>}
      {how && (
        <p data-testid="search-nothing-mode" className="ml-5 mt-0.5 text-xs text-muted">
          {how}
        </p>
      )}
    </div>
  );
}

/** A search that was not run, or that failed: in its own words, with what can be done. */
export function SearchNotice({
  record,
  onOpenDocuments,
}: {
  record: SearchRecord;
  onOpenDocuments?: () => void;
}) {
  const failed = record.status === "failed";
  const hint = hintFor(record);
  return (
    <div
      role={failed ? "alert" : undefined}
      data-testid={failed ? "search-failed" : "search-skipped"}
      className={`notice max-w-measure ${failed ? "notice--warn" : ""}`}
    >
      {failed ? <TriangleAlert size={16} aria-hidden /> : <Info size={16} aria-hidden />}
      <div>
        {record.whole ? (
          <WholeNotice record={record} documents={[]} />
        ) : (
          <span className="wrap-anywhere">{ranWords(record)}</span>
        )}
        {hint && <span className="mt-1 block text-muted">{hint}</span>}
        {record.notes
          .filter((note) => isMeaningNote(note.code))
          .map((note, at) => (
            <span
              key={`${note.code}:${at}`}
              data-testid="search-meaning-note"
              data-code={note.code}
              className="mt-1 block text-muted wrap-anywhere"
            >
              {note.message}
            </span>
          ))}
        {onOpenDocuments && (canRebuild(record) || record.code === "no_documents") && (
          <div className="mt-2">
            <button
              type="button"
              data-testid="search-fix"
              className="btn"
              onClick={onOpenDocuments}
            >
              Open Documents
              {canRebuild(record) ? " to rebuild the search" : " to add some"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Workbench's own reply, for a chat that answers only from the person's documents
 * when there is nothing to answer from: it is Workbench speaking, not a model, and
 * no model was asked. */
export function WorkbenchReply({
  text,
  mine,
  askAnyway,
  asking,
}: {
  text: string;
  /** Whose documents the chat answers from: the asker's own, or someone else's chat
   * the owner is reading. */
  mine: boolean;
  /** Ask the model all the same; absent where the person cannot (someone else's chat). */
  askAnyway?: () => void;
  asking: boolean;
}) {
  return (
    <div
      data-testid="workbench-reply"
      className="flex max-w-measure flex-col gap-2 rounded-plexus border border-line bg-soft px-4 py-3"
    >
      <p className="eyebrow text-muted">Workbench</p>
      <p dir="auto" data-testid="workbench-reply-text" className="text-read wrap-anywhere">
        {text}
      </p>
      <p className="help">
        This chat answers only from {mine ? "your" : "the person's"} documents, so Workbench replied
        itself and did not ask the model.
      </p>
      {askAnyway && (
        <div>
          <button
            type="button"
            data-testid="ask-anyway"
            disabled={asking}
            onClick={askAnyway}
            className="btn"
          >
            Ask the model anyway
          </button>
        </div>
      )}
    </div>
  );
}
