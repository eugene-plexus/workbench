/**
 * Formulas in an answer (workbench-rich-content-roadmap.md RC1): where they
 * are, read by the Markdown parser itself, never by a regex over the answer.
 * Typesetting them is lib/mathClient.ts.
 *
 * A model writes four delimiters, and each is read where Markdown reads
 * text, so code, inline code, link addresses and an escaped `\$` are never a
 * formula:
 *
 * - `$x$`, inline. A dollar is money more often than a formula, so it opens
 *   only before a non-space and not after a digit or another dollar, and it
 *   closes only after a non-space and not before a digit (Pandoc's rule, and
 *   the digit before for "5$/month"): "$5 and $10" stays money.
 * - `$$x$$`, display, inside a paragraph or as a block of its own lines.
 * - `\(x\)`, inline, and `\[x\]`, display. The parser would read `\(` as an
 *   escaped bracket and drop the backslash, so these are found at the
 *   backslash, before the escape.
 *
 * A block (`$$` or `\[` alone on its line) runs to its closing line, or to a
 * line that ends with the closing delimiter, like a code fence; one that is
 * never closed is marked open, and shown as its source until it is. A
 * formula in a paragraph that never closes is the text it was.
 *
 * Each becomes the element remark-math makes, so a ` ```math ` fence (as
 * GitHub writes one) is a display formula too: `code.language-math` with
 * `math-inline` or `math-display`, inside a `pre` for a block.
 */

import type { Element } from "hast";
import type {
  CompileContext,
  Extension as FromMarkdownExtension,
  Token,
} from "mdast-util-from-markdown";
import { factorySpace } from "micromark-factory-space";
import {
  asciiDigit,
  markdownLineEnding,
  markdownLineEndingOrSpace,
} from "micromark-util-character";
import type {
  Code,
  Construct,
  Effects,
  Extension,
  State,
  TokenizeContext,
} from "micromark-util-types";
import type { Processor } from "unified";

declare module "micromark-util-types" {
  interface TokenTypeMap {
    mathText: "mathText";
    mathTextSequence: "mathTextSequence";
    mathTextData: "mathTextData";
    mathFlow: "mathFlow";
    mathFlowFence: "mathFlowFence";
    mathFlowFenceSequence: "mathFlowFenceSequence";
    mathFlowValue: "mathFlowValue";
  }
}

declare module "mdast" {
  interface InlineMath extends Literal {
    type: "inlineMath";
  }
  interface Math extends Literal {
    type: "math";
  }
  interface PhrasingContentMap {
    inlineMath: InlineMath;
  }
  interface BlockContentMap {
    math: Math;
  }
  interface RootContentMap {
    inlineMath: InlineMath;
    math: Math;
  }
}

// What remark-parse declares, for a plugin that adds to the parser.
declare module "unified" {
  interface Data {
    micromarkExtensions?: Array<Extension>;
    fromMarkdownExtensions?: Array<Array<FromMarkdownExtension> | FromMarkdownExtension>;
  }
}

declare module "mdast-util-from-markdown" {
  interface CompileData {
    mathLines?: Array<[line: number, text: string]>;
    mathFences?: number;
  }
}

const DOLLAR = 36;
const BACKSLASH = 92;
const LEFT_PAREN = 40;
const RIGHT_PAREN = 41;
const LEFT_BRACKET = 91;
const RIGHT_BRACKET = 93;
const SPACE = 32;

/**
 * How far a formula in a paragraph is looked for. A dollar that is money has
 * no close, and each one is looked past to the end of its paragraph, on
 * every chunk of an answer being written: a short reach for `$`, which is
 * common, and a long one for the rest, which are not.
 */
export const DOLLAR_REACH = 1_000;
export const FORMULA_REACH = 20_000;

/**
 * What one parse may spend, in characters, looking for closes that were not
 * there. Text built to make every opener look far (`\(a \(a \(a ...`) took
 * seconds a chunk before this; once it is spent, no further formula opens in
 * that parse. A real answer spends a few hundred.
 */
export const SEARCH_BUDGET = 200_000;

type Search = { spent: number; dead: Map<string, readonly [number, number]> };
const searches = new WeakMap<object, Search>();

/**
 * One parse's search: what it has spent, and for each kind of opener the
 * stretch where one looked to the end of its text and found no close. A later
 * opener of that kind inside the stretch would look at the same characters
 * and find none either, so it does not look.
 */
function searchOf(context: TokenizeContext): Search {
  let search = searches.get(context.parser);
  if (!search) searches.set(context.parser, (search = { spent: 0, dead: new Map() }));
  return search;
}

function hopeless(search: Search, kind: string, from: number): boolean {
  const dead = search.dead.get(kind);
  return search.spent > SEARCH_BUDGET || (dead !== undefined && from > dead[0] && from < dead[1]);
}

// --- in a paragraph ----------------------------------------------------------

const dollarText: Construct = {
  name: "mathText",
  tokenize: tokenizeDollarText,
  // Not inside a run of dollars, and not after a digit: "US 5$/month".
  previous: (code) => code !== DOLLAR && !asciiDigit(code),
};

const backslashText: Construct = {
  name: "mathText",
  tokenize: tokenizeBackslashText,
};

function lineEnding(effects: Effects, code: Code): void {
  effects.enter("lineEnding");
  effects.consume(code);
  effects.exit("lineEnding");
}

function tokenizeDollarText(this: TokenizeContext, effects: Effects, ok: State, nok: State): State {
  const search = searchOf(this);
  const offset = () => this.now().offset;
  const from = offset();
  let open = 0;
  let size = 0;
  let length = 0;
  let filled = false;
  // The character before the one being read, inside the formula.
  let before: Code = null;
  let closing: Token | undefined;
  return start;

  /** No close: what was looked at is spent, and the end of the text remembered. */
  function fail(code: Code): State | undefined {
    search.spent += length;
    if (code === null) search.dead.set("$".repeat(open), [from, offset()]);
    return nok(code);
  }

  function start(code: Code): State | undefined {
    effects.enter("mathText");
    effects.enter("mathTextSequence");
    return opening(code);
  }

  function opening(code: Code): State | undefined {
    if (code === DOLLAR) {
      if (open === 2) return nok(code);
      effects.consume(code);
      open++;
      return opening;
    }
    effects.exit("mathTextSequence");
    if (code === null) return nok(code);
    // "$ 5": a single dollar before a space opens nothing.
    if (open === 1 && markdownLineEndingOrSpace(code)) return nok(code);
    if (hopeless(search, "$".repeat(open), from)) return nok(code);
    return between(code);
  }

  function between(code: Code): State | undefined {
    if (code === null) return fail(code);
    if (markdownLineEnding(code)) {
      lineEnding(effects, code);
      before = SPACE;
      return between;
    }
    if (code === DOLLAR) return closeStart(code);
    effects.enter("mathTextData");
    return data(code);
  }

  function data(code: Code): State | undefined {
    if (code === null || markdownLineEnding(code) || code === DOLLAR) {
      effects.exit("mathTextData");
      return between(code);
    }
    if (++length > (open === 1 ? DOLLAR_REACH : FORMULA_REACH)) return fail(code);
    effects.consume(code);
    if (!markdownLineEndingOrSpace(code)) filled = true;
    before = code;
    return code === BACKSLASH ? escaped : data;
  }

  // `\$` and `\\` are the formula's own: the character after a backslash
  // never closes it.
  function escaped(code: Code): State | undefined {
    if (code === null || markdownLineEnding(code)) return data(code);
    effects.consume(code);
    length++;
    before = code;
    return data;
  }

  function closeStart(code: Code): State | undefined {
    closing = effects.enter("mathTextSequence");
    size = 0;
    return closeSequence(code);
  }

  function closeSequence(code: Code): State | undefined {
    if (code === DOLLAR && size < open) {
      effects.consume(code);
      size++;
      return closeSequence;
    }
    const closes =
      size === open &&
      filled &&
      (open === 2 || (!markdownLineEndingOrSpace(before) && !asciiDigit(code)));
    if (closes) {
      effects.exit("mathTextSequence");
      effects.exit("mathText");
      return ok(code);
    }
    // TeX ends `$x$` at its next bare `$`, so one that cannot close it means
    // the first was money: "$5, then $x$" is money, then a formula.
    if (open === 1) return fail(code);
    // Not a close of `$$`: the dollars are part of the formula.
    if (closing) closing.type = "mathTextData";
    length += size;
    before = DOLLAR;
    return data(code);
  }
}

function tokenizeBackslashText(
  this: TokenizeContext,
  effects: Effects,
  ok: State,
  nok: State,
): State {
  const search = searchOf(this);
  const offset = () => this.now().offset;
  const from = offset();
  let close = 0;
  let length = 0;
  let filled = false;
  let closing: Token | undefined;
  const kind = () => (close === RIGHT_PAREN ? "\\(" : "\\[");
  return start;

  /** No close: what was looked at is spent, and the end of the text remembered. */
  function fail(code: Code): State | undefined {
    search.spent += length;
    if (code === null) search.dead.set(kind(), [from, offset()]);
    return nok(code);
  }

  function start(code: Code): State | undefined {
    effects.enter("mathText");
    effects.enter("mathTextSequence");
    effects.consume(code);
    return bracket;
  }

  function bracket(code: Code): State | undefined {
    if (code === LEFT_PAREN) close = RIGHT_PAREN;
    else if (code === LEFT_BRACKET) close = RIGHT_BRACKET;
    else return nok(code);
    if (hopeless(search, kind(), from)) return nok(code);
    effects.consume(code);
    effects.exit("mathTextSequence");
    return between;
  }

  function between(code: Code): State | undefined {
    if (code === null) return fail(code);
    if (markdownLineEnding(code)) {
      lineEnding(effects, code);
      return between;
    }
    if (code === BACKSLASH) return backslash(code);
    effects.enter("mathTextData");
    return data(code);
  }

  function data(code: Code): State | undefined {
    if (code === null || markdownLineEnding(code) || code === BACKSLASH) {
      effects.exit("mathTextData");
      return between(code);
    }
    if (++length > FORMULA_REACH) return fail(code);
    effects.consume(code);
    if (!markdownLineEndingOrSpace(code)) filled = true;
    return data;
  }

  function backslash(code: Code): State | undefined {
    closing = effects.enter("mathTextSequence");
    effects.consume(code);
    return afterBackslash;
  }

  function afterBackslash(code: Code): State | undefined {
    if (code === close) {
      // `\(\)` holds no formula.
      if (!filled) return nok(code);
      effects.consume(code);
      effects.exit("mathTextSequence");
      effects.exit("mathText");
      return ok;
    }
    // A command (`\frac`) or an escaped character (`\\`, `\)` of an empty
    // formula): the formula's own.
    if (closing) closing.type = "mathTextData";
    filled = true;
    length++;
    if (code === null || markdownLineEnding(code)) return data(code);
    effects.consume(code);
    length++;
    return data;
  }
}

// --- a block of its own lines --------------------------------------------------

const nonLazyContinuation: Construct = { tokenize: tokenizeNonLazyContinuation, partial: true };

function tokenizeNonLazyContinuation(
  this: TokenizeContext,
  effects: Effects,
  ok: State,
  nok: State,
): State {
  // A lazy line (one that continues a paragraph without its container's
  // marks) is not the block's.
  const lazy = () => this.parser.lazy[this.now().line];
  return start;

  function start(code: Code): State | undefined {
    if (code === null) return ok(code);
    lineEnding(effects, code);
    return lineStart;
  }

  function lineStart(code: Code): State | undefined {
    return lazy() ? nok(code) : ok(code);
  }
}

/** The delimiter's characters: `$$` opens and closes; `\[` opens and `\]` closes. */
type Fence = { open: readonly [number, number]; close: readonly [number, number] };
const DOLLAR_FENCE: Fence = { open: [DOLLAR, DOLLAR], close: [DOLLAR, DOLLAR] };
const BRACKET_FENCE: Fence = { open: [BACKSLASH, LEFT_BRACKET], close: [BACKSLASH, RIGHT_BRACKET] };

function mathFlow(fence: Fence): Construct {
  return { name: "mathFlow", concrete: true, tokenize };

  function tokenize(this: TokenizeContext, effects: Effects, ok: State, nok: State): State {
    const tail = this.events[this.events.length - 1];
    const indent =
      tail && tail[1].type === "linePrefix" ? tail[2].sliceSerialize(tail[1], true).length : 0;
    const interrupting = () => this.interrupt;
    // An indented closing line is code unless indented code is turned off.
    const closingIndent = this.parser.constructs.disable.null?.includes("codeIndented")
      ? undefined
      : 4;
    // The closing delimiter, alone on its line or at the end of the last one.
    const closingLine: Construct = { tokenize: tokenizeClosing(true), partial: true };
    const closingTail: Construct = { tokenize: tokenizeClosing(false), partial: true };
    return start;

    function start(code: Code): State | undefined {
      if (code !== fence.open[0]) return nok(code);
      effects.enter("mathFlow");
      effects.enter("mathFlowFence");
      effects.enter("mathFlowFenceSequence");
      effects.consume(code);
      return second;
    }

    function second(code: Code): State | undefined {
      if (code !== fence.open[1]) return nok(code);
      effects.consume(code);
      effects.exit("mathFlowFenceSequence");
      return factorySpace(effects, fenceEnd, "whitespace");
    }

    // The opening delimiter is the whole line: `$$ x^2 $$` and `\[ x \]` are
    // formulas in a paragraph instead. Nor does it end a paragraph: in
    // "Total: $$" then "x" then "$$", the paragraph's own `$$` pair is the
    // formula, and the last line opening a block would swallow the rest of
    // the answer.
    function fenceEnd(code: Code): State | undefined {
      if (code !== null && !markdownLineEnding(code)) return nok(code);
      if (interrupting()) return nok(code);
      effects.exit("mathFlowFence");
      return effects.attempt(nonLazyContinuation, lineStart, after)(code);
    }

    function lineStart(code: Code): State | undefined {
      return effects.attempt(closingLine, after, contentStart)(code);
    }

    function contentStart(code: Code): State | undefined {
      return (indent ? factorySpace(effects, beforeChunk, "linePrefix", indent + 1) : beforeChunk)(
        code,
      );
    }

    function beforeChunk(code: Code): State | undefined {
      if (code === null) return after(code);
      if (markdownLineEnding(code)) {
        return effects.attempt(nonLazyContinuation, lineStart, after)(code);
      }
      effects.enter("mathFlowValue");
      return consumed(code);
    }

    function consumed(code: Code): State | undefined {
      effects.consume(code);
      return code === BACKSLASH ? escaped : chunk;
    }

    function chunk(code: Code): State | undefined {
      if (code === null || markdownLineEnding(code)) {
        effects.exit("mathFlowValue");
        return beforeChunk(code);
      }
      if (code === fence.close[0]) {
        effects.exit("mathFlowValue");
        return effects.attempt(closingTail, after, resume)(code);
      }
      return consumed(code);
    }

    // The character after a backslash is the formula's (`\\`, `\$`).
    function escaped(code: Code): State | undefined {
      if (code === null || markdownLineEnding(code)) return chunk(code);
      effects.consume(code);
      return chunk;
    }

    function resume(code: Code): State | undefined {
      effects.enter("mathFlowValue");
      return consumed(code);
    }

    function after(code: Code): State | undefined {
      effects.exit("mathFlow");
      return ok(code);
    }

    function tokenizeClosing(ownLine: boolean) {
      return function (effects: Effects, ok: State, nok: State): State {
        return ownLine ? factorySpace(effects, first, "linePrefix", closingIndent) : first;

        function first(code: Code): State | undefined {
          if (code !== fence.close[0]) return nok(code);
          effects.enter("mathFlowFence");
          effects.enter("mathFlowFenceSequence");
          effects.consume(code);
          return last;
        }

        function last(code: Code): State | undefined {
          if (code !== fence.close[1]) return nok(code);
          effects.consume(code);
          effects.exit("mathFlowFenceSequence");
          return factorySpace(effects, end, "whitespace");
        }

        function end(code: Code): State | undefined {
          if (code !== null && !markdownLineEnding(code)) return nok(code);
          effects.exit("mathFlowFence");
          return ok(code);
        }
      };
    }
  }
}

export const mathSyntax: Extension = {
  flow: { [DOLLAR]: mathFlow(DOLLAR_FENCE), [BACKSLASH]: mathFlow(BRACKET_FENCE) },
  text: { [DOLLAR]: dollarText, [BACKSLASH]: backslashText },
};

// --- into the tree ---------------------------------------------------------------

function codeElement(value: string, display: boolean): Element {
  return {
    type: "element",
    tagName: "code",
    properties: { className: ["language-math", display ? "math-display" : "math-inline"] },
    children: [{ type: "text", value }],
  };
}

export const mathFromMarkdown: FromMarkdownExtension = {
  enter: {
    mathText(this: CompileContext, token: Token) {
      this.enter({ type: "inlineMath", value: "" }, token);
    },
    mathFlow(this: CompileContext, token: Token) {
      this.enter({ type: "math", value: "" }, token);
      this.data.mathLines = [];
      this.data.mathFences = 0;
    },
  },
  exit: {
    mathText(this: CompileContext, token: Token) {
      const node = this.stack[this.stack.length - 1];
      this.exit(token);
      if (node?.type !== "inlineMath") return;
      const raw = this.sliceSerialize(token);
      const display = raw.startsWith("$$") || raw.startsWith("\\[");
      const edge = raw.startsWith("$$") || raw.startsWith("\\") ? 2 : 1;
      node.value = raw
        .slice(edge, raw.length - edge)
        .replace(/\r\n?/g, "\n")
        .trim();
      const code = codeElement(node.value, display);
      node.data = { hName: "code", hProperties: code.properties, hChildren: code.children };
    },
    mathFlowFence(this: CompileContext) {
      this.data.mathFences = (this.data.mathFences ?? 0) + 1;
    },
    mathFlowValue(this: CompileContext, token: Token) {
      this.data.mathLines?.push([token.start.line, this.sliceSerialize(token)]);
    },
    mathFlow(this: CompileContext, token: Token) {
      const node = this.stack[this.stack.length - 1];
      this.exit(token);
      if (node?.type !== "math") return;
      // A value split where a closing delimiter was looked for and not found
      // is one line again.
      let value = "";
      let line: number | null = null;
      for (const [at, text] of this.data.mathLines ?? []) {
        value += line === null || at === line ? text : `\n${text}`;
        line = at;
      }
      node.value = value.trim();
      const code = codeElement(node.value, true);
      // Never closed: still being written, or never finished. Shown as its
      // source, like an open code fence.
      if ((this.data.mathFences ?? 0) < 2) code.properties.dataFence = "open";
      node.data = { hName: "pre", hChildren: [code] };
      this.data.mathLines = undefined;
      this.data.mathFences = undefined;
    },
  },
};

/** The remark plugin: formulas are read with the rest of the answer. */
export function remarkMath(this: Processor): undefined {
  const data = this.data();
  (data.micromarkExtensions ??= []).push(mathSyntax);
  (data.fromMarkdownExtensions ??= []).push(mathFromMarkdown);
  return undefined;
}
