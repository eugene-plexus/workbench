/**
 * How the person's documents are searched, at the top of the Documents screen (MR5): the
 * server's own headline from a live look ("Searching by meaning and words.", "Searching by
 * words only. No search model is running.", "Search by meaning is paused: ..."), why in the
 * code's own words, which search model is in use and where it runs, how adding meaning is
 * going, and what the person can do: Rebuild search, which says how many passages and how
 * long, or get a search model, which is Eugene's console's to do (Workbench cannot download
 * models, so it links there and says so). A person who is not the owner is told to ask them,
 * since the console is the owner's.
 *
 * The page promises nothing the server did not say: words only is said as what it is, with
 * what it does and does not find, for as long as meaning is not in use.
 */

import { ExternalLink, Search, TriangleAlert } from "lucide-react";
import { useId } from "react";

import {
  addingWords,
  CONSOLE_ROUTE,
  consolePage,
  consoleStep,
  detailWords,
  modelInUse,
  ownerWords,
  rebuildWords,
  searchTone,
  whereWords,
  whoseWords,
} from "../lib/documentMeaning";
import { WORDS_ONLY_HELP } from "../lib/savedDocuments";
import type { Me, SearchFacts } from "../lib/types";

export function DocumentsSearch({
  facts,
  me,
  busy,
  onRebuild,
}: {
  facts: SearchFacts;
  /** Who is reading, and where Eugene's console is. Without it nothing is said of the console. */
  me?: Pick<Me, "owner" | "consoleUrl"> | null;
  busy: boolean;
  onRebuild: () => void;
}) {
  const uid = useId();
  const warn = searchTone(facts) === "warn";
  const detail = detailWords(facts);
  const adding = addingWords(facts);
  const model = modelInUse(facts);
  const where = model ? whereWords(model) : null;
  const note = model?.prefixes.note ?? null;
  const rebuild = facts.actions.includes("rebuild") ? facts.rebuild : null;
  const wantsModel = facts.actions.includes("get_search_model");
  const step = consoleStep(facts.code);
  const consoleUrl = me?.owner && me.consoleUrl ? me.consoleUrl : null;
  const asksOwner = me && !me.owner ? ownerWords(facts.code) : null;
  const Glyph = warn ? TriangleAlert : Search;

  return (
    <div
      data-testid="documents-search"
      data-mode={facts.mode}
      data-code={facts.code}
      className={`notice ${warn ? "notice--warn" : ""}`}
    >
      <Glyph size={16} aria-hidden />
      <div className="flex flex-col gap-1.5">
        <span data-testid="documents-search-headline" className="notice-title">
          {facts.headline}
        </span>
        {detail && <span data-testid="documents-search-detail">{detail}</span>}
        {model && (
          <span data-testid="documents-search-model" className="font-medium">
            {model.label}
          </span>
        )}
        {model && (
          <span data-testid="documents-search-where" className="text-muted">
            {[where, whoseWords(model)].filter(Boolean).join(" ")}
          </span>
        )}
        {note && (
          <span data-testid="documents-search-note" className="text-muted">
            {note}
          </span>
        )}
        {adding && <span data-testid="documents-search-adding">{adding}</span>}
        {!facts.meaning && (
          <span data-testid="documents-search-help" className="text-muted">
            {WORDS_ONLY_HELP}
          </span>
        )}
        {rebuild && (
          <div className="mt-1 flex flex-col items-start gap-2">
            <span id={`${uid}-rebuild`} data-testid="documents-meaning-rebuild-words">
              {rebuildWords(rebuild)}
            </span>
            <button
              type="button"
              data-testid="documents-meaning-rebuild"
              aria-describedby={`${uid}-rebuild`}
              disabled={busy}
              className="btn btn-primary"
              onClick={onRebuild}
            >
              Rebuild search
            </button>
          </div>
        )}
        {wantsModel && consoleUrl && (
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <a
              data-testid="documents-get-search-model"
              href={consolePage(consoleUrl, CONSOLE_ROUTE.library)}
              target="_blank"
              rel="noopener noreferrer"
              className="link inline-flex min-h-7 items-center gap-1"
            >
              Get a search model
              <ExternalLink size={14} aria-hidden />
            </a>
            <span className="text-muted">
              Eugene&apos;s console downloads models; Workbench cannot.
            </span>
          </span>
        )}
        {wantsModel && me?.owner && !consoleUrl && (
          <span data-testid="documents-get-search-model-words">
            Get a search model in Eugene&apos;s console, under Library. Workbench cannot download
            models.
          </span>
        )}
        {step === "backends" && consoleUrl && (
          <a
            data-testid="documents-open-backends"
            href={consolePage(consoleUrl, CONSOLE_ROUTE.backends)}
            target="_blank"
            rel="noopener noreferrer"
            className="link inline-flex min-h-7 w-fit items-center gap-1"
          >
            Open Backends
            <ExternalLink size={14} aria-hidden />
          </a>
        )}
        {asksOwner && <span data-testid="documents-ask-owner">{asksOwner}</span>}
      </div>
    </div>
  );
}
