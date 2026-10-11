/**
 * What an answer's Markdown gets beyond CommonMark and GFM
 * (workbench-reading.md 6.4): the four HTML tags a model really writes,
 * GitHub's alert blocks, and which code fences are still being written (the
 * colour itself is lib/highlight.ts). Each stays inside the rule that an
 * answer is untrusted (workbench-v1.md W5): no attribute of anything the model
 * wrote reaches the page, and nothing here makes a string into markup.
 */

import type { Element, Root as HastRoot } from "hast";
import type { Parent, PhrasingContent, Root, Text } from "mdast";

/** What a node's children are, whatever kind of node it is. */
function childrenOf(node: unknown): unknown[] | null {
  const children = (node as { children?: unknown }).children;
  return Array.isArray(children) ? children : null;
}

// --- <br>, <sub>, <sup>, <kbd> ----------------------------------------------
//
// `skipHtml` turns every tag into nothing, so `one<br>two` in a table cell
// read "onetwo" and `H<sub>2</sub>O` read "H2O". These four, with no
// attributes at all, become what they say. Anything else (`<b>`, `<img
// onerror=...>`, `<br class=x>`) is left for `skipHtml` to drop, as before.

/** Inline elements that hold other inline content, which may hold a tag. */
const INLINE_PARENTS = new Set([
  "paragraph",
  "heading",
  "tableCell",
  "emphasis",
  "strong",
  "delete",
  "link",
  "linkReference",
]);
const BREAK = /^<br\s*\/?>$/i;
const OPENING = /^<(sub|sup|kbd)>$/i;
const CLOSING = /^<\/(sub|sup|kbd)\s*>$/i;

type Open = { name: string; children: PhrasingContent[] };

function allowTags(children: PhrasingContent[]): PhrasingContent[] {
  const out: PhrasingContent[] = [];
  const open: Open[] = [];
  const into = () => open[open.length - 1]?.children ?? out;
  // `one<br>` then a line end: the break is already made, so the line end
  // after it must not make a second one (remark-breaks would).
  let afterBreak = false;
  for (const node of children) {
    const wasAfterBreak = afterBreak;
    afterBreak = false;
    if (node.type === "text" && wasAfterBreak) {
      const text: Text = { ...node, value: node.value.replace(/^\r?\n/, "") };
      if (text.value) into().push(text);
      continue;
    }
    if (node.type !== "html") {
      into().push(node);
      continue;
    }
    const tag = node.value.trim();
    const opening = OPENING.exec(tag)?.[1];
    const closing = CLOSING.exec(tag)?.[1];
    if (BREAK.test(tag)) {
      into().push({ type: "break" });
      afterBreak = true;
    } else if (opening) {
      open.push({ name: opening.toLowerCase(), children: [] });
    } else if (closing) {
      // A closing tag that does not close the innermost opening one is a
      // mistake in the answer; it is dropped and the opening one stays open.
      const top = open[open.length - 1];
      if (top && top.name === closing.toLowerCase()) {
        open.pop();
        // An emphasis node that names another element: mdast-util-to-hast
        // makes `<sub>`, `<sup>` or `<kbd>` of it, with no properties.
        into().push({
          type: "emphasis",
          data: { hName: top.name },
          children: top.children,
        });
      }
    } else {
      into().push(node);
    }
  }
  // A tag never closed shows its content as plain text, as `skipHtml` would.
  for (let top = open.pop(); top; top = open.pop()) into().push(...top.children);
  return out;
}

function allowTagsIn(node: unknown): void {
  const children = childrenOf(node);
  if (!children) return;
  for (const child of children) allowTagsIn(child);
  if (INLINE_PARENTS.has((node as Parent).type)) {
    (node as Parent).children = allowTags(children as PhrasingContent[]);
  }
}

export function remarkAllowedHtml() {
  return (tree: Root) => allowTagsIn(tree);
}

// --- > [!NOTE] ---------------------------------------------------------------

const ALERT_MARKER = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\r?\n|$)/i;
const ALERT_LABELS: Record<string, string> = {
  note: "Note",
  tip: "Tip",
  important: "Important",
  warning: "Warning",
  caution: "Caution",
};

function markAlerts(node: unknown): void {
  const children = childrenOf(node);
  if (!children) return;
  for (const child of children) markAlerts(child);
  if ((node as Parent).type !== "blockquote") return;
  const quote = node as Parent;
  const first = quote.children[0];
  if (first?.type !== "paragraph") return;
  const lead = first.children[0];
  if (lead?.type !== "text") return;
  const marker = ALERT_MARKER.exec(lead.value);
  if (!marker) return;
  const kind = (marker[1] ?? "").toLowerCase();
  // The marker is a label, not words of the answer: it goes, and the label
  // is drawn by the stylesheet from the attribute.
  lead.value = lead.value.slice(marker[0].length);
  if (!lead.value) first.children.shift();
  if (first.children.length === 0) quote.children.shift();
  quote.data = {
    ...quote.data,
    hProperties: { className: ["alert", `alert-${kind}`], "data-alert": ALERT_LABELS[kind] },
  };
}

export function remarkAlerts() {
  return (tree: Root) => markAlerts(tree);
}

// --- a fence that is still being written -------------------------------------

/**
 * Whether `raw`, the source of a fenced block (from its opening line to where
 * the parser ended it), is a fence that was never closed: the model is still
 * writing it, or never finished. An indented block has no fence to wait for.
 */
export function fenceIsOpen(raw: string): boolean {
  const lines = raw.split(/\r?\n/);
  const opening = /^[ \t>]*(`{3,}|~{3,})/.exec(lines[0] ?? "")?.[1];
  if (!opening) return false;
  if (lines.length < 2) return true;
  // The closing line may carry the marks of what the block sits in ("> ```").
  const closing = /^[ \t>]*(`{3,}|~{3,})[ \t]*$/.exec(lines[lines.length - 1] ?? "")?.[1];
  return !closing || closing[0] !== opening[0] || closing.length < opening.length;
}

function markOpenFences(node: HastRoot | Element, source: unknown): void {
  for (const child of node.children) {
    if (child.type !== "element") continue;
    if (child.tagName !== "pre") {
      markOpenFences(child, source);
      continue;
    }
    const code = child.children[0];
    const from = child.position?.start.offset;
    const to = child.position?.end.offset;
    if (code?.type !== "element" || code.tagName !== "code") continue;
    if (typeof source !== "string" || from === undefined || to === undefined) continue;
    // `data-fence="open"` on the code: a block is coloured when it closes, and
    // not on every chunk of the answer while it is being written.
    if (fenceIsOpen(source.slice(from, to))) code.properties.dataFence = "open";
  }
}

export function rehypeOpenFences() {
  return (tree: HastRoot, file: { value: unknown }) => markOpenFences(tree, file.value);
}
