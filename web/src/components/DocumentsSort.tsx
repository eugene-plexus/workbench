/**
 * How the best passages of a question are sorted, under how the documents are searched, at the
 * top of the Documents screen (MR6b): which result sorter is in use and where it runs, how fast
 * it reads and what a sort adds to an answer beside Sort time (what a sort may be expected to
 * take; one that is asked is given up on after twice it); or why passages
 * are not sorted, in the server's own words with the numbers it was decided by ("Not sorted:
 * the sorter sorts about 820 tokens a second here, so ... would add about 7 s to each answer,
 * and Sort time is 3 s"), and what the person can do about it. A person who is not the owner is
 * told to ask them where the mending is in Eugene's console, which is the owner's.
 *
 * The page promises nothing the server did not say: a sorter that is chosen but too slow is not
 * said to be in use, and a sorter that was never in play (turned off, or none set up) is not
 * said at all.
 */

import { ExternalLink, ListOrdered, TriangleAlert } from "lucide-react";

import { CONSOLE_ROUTE, consolePage } from "../lib/documentMeaning";
import {
  hasSortWords,
  sorterInUse,
  sortConsoleStep,
  sortOwnerWords,
  sortReasonWords,
  sortSpeedFromWords,
  sortSpeedWords,
  sortTone,
  sortWhereWords,
  sortWhoseWords,
} from "../lib/documentSorting";
import type { Me, SortFacts } from "../lib/types";

export function DocumentsSort({
  sort,
  me,
}: {
  /** The sorter as the server's live look found it; null where there is no gateway to ask. */
  sort: SortFacts | null | undefined;
  /** Who is reading, and where Eugene's console is. Without it nothing is said of the console. */
  me?: Pick<Me, "owner" | "consoleUrl"> | null;
}) {
  if (!hasSortWords(sort)) return null;
  const warn = sortTone(sort) === "warn";
  const inUse = sorterInUse(sort);
  const reason = sortReasonWords(sort);
  const speed = sortSpeedWords(sort);
  const from = sortSpeedFromWords(sort);
  const step = sortConsoleStep(sort.code);
  const consoleUrl = me?.owner && me.consoleUrl ? me.consoleUrl : null;
  const asksOwner = me && !me.owner ? sortOwnerWords(sort.code) : null;
  const Glyph = warn ? TriangleAlert : ListOrdered;

  return (
    <div
      data-testid="documents-sort"
      data-code={sort.code}
      data-sorting={sort.sorting}
      className={`notice ${warn ? "notice--warn" : ""}`}
    >
      <Glyph size={16} aria-hidden />
      <div className="flex flex-col gap-1.5">
        <span data-testid="documents-sort-title" className="notice-title">
          Result sorter
        </span>
        {inUse && (
          <span data-testid="documents-sort-model" className="font-medium">
            {sort.label}
          </span>
        )}
        {inUse && (
          <span data-testid="documents-sort-where" className="text-muted">
            {[sortWhereWords(sort), sortWhoseWords(sort)].filter(Boolean).join(" ")}
          </span>
        )}
        {reason && <span data-testid="documents-sort-reason">{reason}</span>}
        {speed && <span data-testid="documents-sort-speed">{speed}</span>}
        {from && (
          <span data-testid="documents-sort-speed-from" className="text-muted">
            {from}
          </span>
        )}
        {step === "backends" && consoleUrl && (
          <a
            data-testid="documents-sort-open-backends"
            href={consolePage(consoleUrl, CONSOLE_ROUTE.backends)}
            target="_blank"
            rel="noopener noreferrer"
            className="link inline-flex min-h-7 w-fit items-center gap-1"
          >
            Open Backends
            <ExternalLink size={14} aria-hidden />
          </a>
        )}
        {asksOwner && <span data-testid="documents-sort-ask-owner">{asksOwner}</span>}
      </div>
    </div>
  );
}
