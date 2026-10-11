/**
 * Syntax colour for a code block (workbench-reading.md 6.4), made of React
 * elements: a token is a `<span class="hljs-keyword">` and nothing is made from
 * a string a model wrote (no inner HTML anywhere, workbench-v1.md W5). The
 * colours are the stylesheet's, by class, so dark and light follow with no
 * script.
 *
 * What it costs is the reason for the shape. Colouring 300 lines of JSON takes
 * 13 ms here, but turning its tokens into elements and letting React compare
 * them took 70 ms, on every chunk of a streamed answer, for a block that had
 * long stopped changing. So a block is coloured once and remembered (here, by
 * language and text, and by `Markdown`'s `CodeBlock`, which React then skips),
 * never while its fence is still open, and never past a size where it stops
 * being worth it. A grammar that fails costs that block its colour and nothing
 * else: an answer is untrusted, and the page has no error boundary.
 */

import type { ElementContent } from "hast";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import powershell from "highlight.js/lib/languages/powershell";
import { common, createLowlight } from "lowlight";
import { createElement, type ReactNode } from "react";

// An unlabelled fence is never guessed at (lowlight's `highlightAuto` is never
// called): a guess can be wrong, and the colour would then say something false
// about the code.
const lowlight = createLowlight({ ...common, powershell, dockerfile });

/** Fences that name no language: shown as written. */
const PLAIN = new Set(["text", "txt", "plaintext", "log", "output", "no-highlight", "nohighlight"]);

/**
 * A block past either of these is shown plain. The size is a measured one, not
 * a round one: colouring is quadratic in the length of a block for some
 * grammars on some text. C, C++, C# and Arduino take 0.1 s on 8,000 characters
 * of bare identifiers, 1 s on 20,000, 2 s on 30,000 and 22 s on 100,000; INI
 * and TypeScript take 1.2 s on one word of 20,000. A stored answer like that
 * would freeze the page each time its chat was opened. At 8,000 the slowest of
 * 273 language and text pairs tried takes 140 ms (a phone, perhaps four times
 * that), and ordinary code a few.
 */
export const HIGHLIGHT_MAX_CHARS = 8_000;
export const HIGHLIGHT_MAX_LINES = 400;
/** The source text of every remembered block together; the longest unused go first. */
const MEMORY_CHARS = 200_000;

const memory = new Map<string, { chars: number; nodes: ReactNode[] | null }>();
let remembered = 0;

function recall(key: string) {
  const hit = memory.get(key);
  if (hit) {
    // The most recently used last, so the first in the map is the one to forget.
    memory.delete(key);
    memory.set(key, hit);
  }
  return hit;
}

function remember(key: string, chars: number, nodes: ReactNode[] | null): void {
  memory.set(key, { chars, nodes });
  remembered += chars;
  for (const [oldest, entry] of memory) {
    if (remembered <= MEMORY_CHARS || oldest === key) break;
    memory.delete(oldest);
    remembered -= entry.chars;
  }
}

/** The lines in a code block's text, which the parser ends with a line end. */
function linesOf(text: string): number {
  let lines = text.endsWith("\n") ? 0 : 1;
  for (let at = text.indexOf("\n"); at >= 0; at = text.indexOf("\n", at + 1)) lines += 1;
  return lines;
}

function toReact(children: ElementContent[]): ReactNode[] {
  return children.map((node, index) => {
    if (node.type === "text") return node.value;
    if (node.type !== "element") return null;
    const { className } = node.properties;
    return createElement(
      node.tagName,
      { key: index, className: Array.isArray(className) ? className.join(" ") : undefined },
      toReact(node.children),
    );
  });
}

/**
 * The code, coloured as `language`, or null when it is to be shown as written:
 * a language with nothing to colour, one this page does not know, a block past
 * the size cap, or a grammar that failed.
 */
export function colourCode(language: string, source: string): ReactNode[] | null {
  if (PLAIN.has(language.toLowerCase())) return null;
  if (source.length > HIGHLIGHT_MAX_CHARS || linesOf(source) > HIGHLIGHT_MAX_LINES) return null;
  const key = `${language}\u0000${source}`;
  const hit = recall(key);
  if (hit) return hit.nodes;
  let nodes: ReactNode[] | null = null;
  try {
    nodes = toReact(lowlight.highlight(language, source).children as ElementContent[]);
  } catch {
    // Shown as plain text, and not tried again for this text.
  }
  remember(key, source.length, nodes);
  return nodes;
}
