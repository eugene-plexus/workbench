/**
 * What the stylesheet promises about reading (workbench-reading.md 5, 6, 8, 9),
 * read as text. jsdom lays nothing out, so these pin the rules that carry a
 * promise; the numbers behind them were measured in Chrome (the reading check),
 * and each of these fails if the rule goes or is overridden by its neighbour.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(import.meta.dirname, "styles.css"), "utf8");
const plain = css.replace(/\/\*[\s\S]*?\*\//g, "");

/** The text between the braces that open at `open`, nested braces counted. */
function braces(text: string, open: number): string {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(open + 1, i);
  }
  throw new Error("styles.css: a block is never closed");
}

type Rule = { selector: string; body: string; at: number };
/** Every rule that holds declarations (not an at-rule around others), in order. */
const RULES: Rule[] = [...plain.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
  selector: m[1]!.trim().replace(/\s+/g, " "),
  body: m[2]!.replace(/\s+/g, " ").trim(),
  at: m.index,
}));

/** The declarations of every rule with exactly this selector. */
const bodiesOf = (selector: string) => RULES.filter((r) => r.selector === selector);
const declares = (selector: string, declaration: string) =>
  bodiesOf(selector).some((r) => r.body.includes(declaration));

const layer = plain.indexOf("@layer components {");
const layerEnd = layer + braces(plain, plain.indexOf("{", layer)).length + 2;
const inLayer = (rule: Rule) => rule.at > layer && rule.at < layerEnd;

describe("the measure is a count of characters", () => {
  it("is in em of the text's own size, so a 14 px box holds as many characters as a 17 px answer", () => {
    expect(css).toMatch(/--measure:\s*33\.88em;/);
    // 33.88 em is 36 rem at the 17 px reading size.
    expect(33.88 * 17).toBeCloseTo(576, 0);
  });

  it("caps the prose, and each heading at the same width: the measure over its own scale", () => {
    expect(
      declares(".answer > :is(p, ul, ol, blockquote, hr, h4)", "max-width: var(--measure)"),
    ).toBe(true);
    for (const [heading, selector, cap] of [
      ["h1", ".answer h1", ".answer > h1"],
      ["h2", ".answer h2", ".answer > h2"],
      ["h3", ".answer h3", ".answer > h3"],
      ["h5 and h6", ".answer :is(h5, h6)", ".answer > :is(h5, h6)"],
    ] as const) {
      const scale = /font-size:\s*([\d.]+)em/.exec(bodiesOf(selector)[0]?.body ?? "")?.[1];
      expect(scale, heading).toBeDefined();
      expect(declares(cap, `max-width: calc(var(--measure) / ${scale})`), heading).toBe(true);
    }
  });
});

describe("a quiet draft keeps everything at 13 px or more", () => {
  it.each([
    [".answer.answer-quiet :not(pre) > code", "0.88em"],
    [".answer.answer-quiet table", "0.9em"],
    [".answer.answer-quiet :is(h5, h6)", "0.8125em"],
  ])("%s", (selector, em) => {
    expect(declares(selector, `font-size: max(0.8125rem, ${em})`)).toBe(true);
  });
});

describe("an invalid field's edge", () => {
  const rule = bodiesOf(':is(input, select, textarea)[aria-invalid="true"]');

  it("is the danger edge (3:1 on the field), set outside the layers so a border-error-line utility cannot dim it", () => {
    expect(rule).toHaveLength(1);
    expect(rule[0]!.body).toBe("border-color: var(--danger-edge);");
    expect(inLayer(rule[0]!)).toBe(false);
    // The layered recipe must not be what it relies on: nothing in the layer sets it.
    expect(RULES.filter((r) => inLayer(r) && r.selector.includes("aria-invalid"))).toEqual([]);
  });
});

describe("words hidden for a screen reader cannot lengthen the page", () => {
  it("a Markdown artifact's box holds what its Markdown hides (a footnotes heading)", () => {
    expect(declares(".artifact-doc", "overflow: auto")).toBe(true);
    expect(declares(".artifact-doc", "position: relative")).toBe(true);
  });
});

describe("a long string cannot widen the page", () => {
  it.each([".notice", ".check", ".title-page", ".title-section"])("%s wraps it", (selector) => {
    for (const declaration of ["overflow-wrap: anywhere", "min-width: 0"]) {
      expect(declares(selector, declaration), `${selector} ${declaration}`).toBe(true);
    }
  });

  it.each([
    [".notice > :not(svg)", "min-width: 0"],
    [".check > :not(input)", "min-width: 0"],
  ])("%s may shrink", (selector, declaration) => {
    expect(declares(selector, declaration)).toBe(true);
  });

  it("a button wraps below the sm breakpoint, and is never wider than what holds it", () => {
    expect(declares(".btn", "max-width: 100%")).toBe(true);
    // Not wrapping at desktop width, where its label is a fixed word.
    expect(declares(".btn", "white-space: nowrap")).toBe(true);
    const at = plain.indexOf("@media (max-width: 39.99rem) {");
    expect(at).toBeGreaterThan(0);
    const inner = braces(plain, plain.indexOf("{", at)).replace(/\s+/g, " ");
    expect(inner).toContain(".btn { white-space: normal;");
    expect(inner).toContain("overflow-wrap: anywhere");
  });
});

describe("an answer carries a few warm marks", () => {
  it("inline code is warm text on its soft fill, and plain in a link and in a quiet draft", () => {
    expect(declares(".answer :not(pre) > code", "color: var(--accent-right-text)")).toBe(true);
    expect(declares(".answer :not(pre) > code", "background: var(--panel-soft)")).toBe(true);
    expect(declares(".answer a code", "color: inherit")).toBe(true);
    expect(declares(".answer.answer-quiet :not(pre) > code", "color: inherit")).toBe(true);
  });

  it("a first-level bullet is apricot, a nested one muted, a numeral bold in the heading's colour", () => {
    expect(declares(".answer li::marker", "color: var(--muted)")).toBe(true);
    expect(declares(".answer > ul > li::marker", "color: var(--accent-right)")).toBe(true);
    expect(declares(".answer ol > li::marker", "color: var(--heading)")).toBe(true);
    expect(declares(".answer ol > li::marker", "font-weight: 600")).toBe(true);
    expect(declares(".answer.answer-quiet > ul > li::marker", "color: var(--muted)")).toBe(true);
  });

  it("an alert's bar is a mark and its label is the warm text", () => {
    expect(declares(".answer blockquote.alert-important", "--alert: var(--accent-right)")).toBe(
      true,
    );
    expect(
      declares(".answer blockquote.alert-important", "--alert-text: var(--accent-right-text)"),
    ).toBe(true);
    expect(
      declares(".answer blockquote.alert::before", "color: var(--alert-text, var(--alert))"),
    ).toBe(true);
  });
});
