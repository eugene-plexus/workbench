/**
 * One formula laid out by KaTeX, as its markup (workbench-rich-content-roadmap.md
 * RC1). Runs in the formula worker (mathWorker.ts), never on the page: a
 * formula can be made to take minutes (a 488-character one that defines and
 * repeats a macro took 36 s here), and only a worker can be stopped.
 *
 * What the model wrote is untrusted: KaTeX's `trust` stays off (no links,
 * classes, ids, styles or pictures from the formula), a size the formula
 * names is capped, and macros live and die with the formula that defines
 * them (no `macros` object is shared, so none can reach the next formula).
 * The markup is read into elements by mathMarkup.ts, through its allowlist.
 */

import katex from "katex";

/** A longer formula is shown as its source. */
export const MAX_TEX = 8_000;
/**
 * A larger layout is shown as its source: reading it into the page would
 * take the page itself too long. A 20 x 20 matrix is 358 KB; 25 x 25, 555 KB.
 */
export const MAX_MARKUP = 600_000;

export type Typeset = { markup: string } | { error: string };

export function typeset(tex: string, display: boolean): Typeset {
  if (tex.length > MAX_TEX) {
    return { error: `it is longer than ${MAX_TEX.toLocaleString("en")} characters` };
  }
  try {
    const markup = katex.renderToString(tex, {
      displayMode: display,
      output: "htmlAndMathml",
      throwOnError: true,
      trust: false,
      strict: "ignore",
      maxSize: 20,
      maxExpand: 1000,
    });
    if (markup.length > MAX_MARKUP) return { error: "its layout is too large to show here" };
    return { markup };
  } catch (error) {
    if (error instanceof katex.ParseError) return { error: error.rawMessage };
    return { error: error instanceof Error ? error.message : String(error) };
  }
}
