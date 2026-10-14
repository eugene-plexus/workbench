/**
 * Citations in an answer (workbench-rich-content-roadmap.md RC4).
 *
 * - **A web source** the answer cites in a place the gateway named (`marks`,
 *   kept only where the text there holds the address: answers.py `cite`) gets
 *   its number after the link at that place. The number moves focus to the
 *   source in the answer's Sources. A source with no place stays in the
 *   Sources list only: no place is made up.
 * - **A passage** of the person's documents is cited as `[n]`, as Workbench
 *   asks the model to (context.py). Only a number of a passage that was sent
 *   with the question is a link, and only in the answer's words: not in code,
 *   not inside a link, not a footnote. The link opens that passage under the
 *   answer.
 *
 * Places are in the message's content as the page counts (UTF-16); the
 * Markdown is the answer after its draft, so each is taken from where the
 * answer begins (`citationsFor`).
 */

import type { Link, Parent, PhrasingContent, Root, RootContent } from "mdast";

import { isPassageSource, isWebSource } from "./documents";
import { isHidden } from "./savedDocuments";
import type { Message } from "./types";

export type Citations = {
  /** A web source's number in Sources, and where in this text it is cited: [start, end). */
  web: Array<{ n: number; start: number; end: number }>;
  /** The numbers of the passages sent with the question. */
  passages: number[];
};

const MARKER = /\[(\d{1,3})\]/g;

/** Where the answer's text begins in the message's content (words.ts `answerParts`). */
export function answerStart(message: Message): number {
  const at = message.answerFrom;
  if (at == null) return 0;
  const rest = message.content.slice(at);
  const trimmed = rest.replace(/^\s+/, "");
  if (!trimmed && message.status !== "running") return 0;
  return at + (rest.length - trimmed.length);
}

/** What the answer cites, in its own text's places; undefined when nothing. */
export function citationsFor(message: Message): Citations | undefined {
  if (message.role !== "assistant") return undefined;
  const start = answerStart(message);
  const web = message.sources
    .filter(isWebSource)
    .flatMap((source, i) =>
      (source.marks ?? [])
        .filter(([a, b]) => a >= start && b > a)
        .map(([a, b]) => ({ n: i + 1, start: a - start, end: b - start })),
    );
  const passages = message.sources
    .filter(isPassageSource)
    .filter((source) => !isHidden(source))
    .map((source) => ("rank" in source ? source.rank : NaN))
    .filter((rank): rank is number => typeof rank === "number" && Number.isInteger(rank));
  return web.length || passages.length ? { web, passages } : undefined;
}

function marker(kind: "web" | "passage", n: number): Link {
  return {
    type: "link",
    url: `#${kind}-${n}`,
    children: [{ type: "text", value: `[${n}]` }],
    data: { hProperties: { dataCite: kind, dataN: String(n) } },
  };
}

type Node = RootContent | Root;

/** The remark plugin for these citations. */
export function remarkCitations(citations: Citations) {
  return () => (tree: Root) => {
    const passages = new Set(citations.passages);
    const placed = new Set<(typeof citations.web)[number]>();

    function walk(node: Parent, inLink: boolean): void {
      const out: Node[] = [];
      for (const child of node.children as Node[]) {
        // Not code, and not a footnote: its numbers are the note's own.
        if (
          child.type === "code" ||
          child.type === "inlineCode" ||
          child.type === "html" ||
          child.type === "footnoteDefinition"
        ) {
          out.push(child);
          continue;
        }
        if (child.type === "link" || child.type === "linkReference") {
          walk(child, true);
          out.push(child);
          const start = child.position?.start.offset;
          const end = child.position?.end.offset;
          if (start === undefined || end === undefined || inLink) continue;
          for (const cited of citations.web) {
            if (!placed.has(cited) && cited.start < end && cited.end > start) {
              placed.add(cited);
              out.push(marker("web", cited.n));
            }
          }
          continue;
        }
        if (child.type === "text" && !inLink && passages.size) {
          const parts: PhrasingContent[] = [];
          let last = 0;
          for (const found of child.value.matchAll(MARKER)) {
            const n = Number(found[1]);
            const at = found.index ?? 0;
            if (!passages.has(n)) continue;
            if (at > last) parts.push({ type: "text", value: child.value.slice(last, at) });
            parts.push(marker("passage", n));
            last = at + found[0].length;
          }
          if (!parts.length) {
            out.push(child);
            continue;
          }
          if (last < child.value.length)
            parts.push({ type: "text", value: child.value.slice(last) });
          out.push(...parts);
          continue;
        }
        if ("children" in child) walk(child as Parent, inLink);
        out.push(child);
      }
      node.children = out as Parent["children"];
    }

    walk(tree, false);
  };
}

/**
 * A citation's number, followed: focus on the web source in the answer's
 * Sources, or the passage opened under it. Found inside the same answer, and
 * the address is left alone.
 */
export function followCitation(link: HTMLElement): void {
  const kind = link.dataset.cite;
  const n = link.dataset.n;
  const answer = link.closest<HTMLElement>("[data-testid=answer]");
  if (!answer || !n || !/^\d+$/.test(n)) return;
  if (kind === "web") {
    answer.querySelector<HTMLElement>(`[data-source-n="${n}"] a`)?.focus();
    return;
  }
  const passage = answer.querySelector<HTMLElement>(`[data-passage-rank="${n}"]`);
  const details = passage?.querySelector("details");
  if (details) details.open = true;
  passage?.querySelector<HTMLElement>("summary")?.focus();
}

const kept = new Map<string, { key: string; value: Citations | undefined }>();

/**
 * `citationsFor`, the same object while it says the same: the answer's
 * Markdown is memoised on it, and a streamed answer renders on every token.
 */
export function stableCitations(message: Message): Citations | undefined {
  const value = citationsFor(message);
  const key = JSON.stringify(value ?? null);
  const before = kept.get(message.id);
  if (before && before.key === key) return before.value;
  kept.set(message.id, { key, value });
  if (kept.size > 500) kept.delete(kept.keys().next().value ?? "");
  return value;
}
