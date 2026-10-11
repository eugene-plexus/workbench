/**
 * The rules R2 added to the stylesheet for the chat's surroundings (the block
 * between its two markers), read as text: jsdom lays nothing out, so these pin
 * what each rule promises, and each fails if the rule goes or is widened.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(import.meta.dirname, "styles.css"), "utf8");
const START = "/* ---- R2: the chat's surroundings ---- */";
const END = "/* ---- end R2 ---- */";
const block = css.slice(css.indexOf(START) + START.length, css.indexOf(END));
const plain = block.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ");

describe("R2's block", () => {
  it("is in the stylesheet, once, between its markers, in the components layer", () => {
    expect(css.split(START)).toHaveLength(2);
    expect(css.split(END)).toHaveLength(2);
    expect(block.length).toBeGreaterThan(50);
    const layer = css.lastIndexOf("@layer components {", css.indexOf(START));
    expect(layer).toBeGreaterThan(0);
    // Nothing between the layer's opening and the marker but whitespace and a comment.
    expect(css.slice(layer, css.indexOf(START)).replace(/\/\*[\s\S]*?\*\//g, "")).toMatch(
      /^@layer components \{\s*$/,
    );
  });
});

describe("the skip link", () => {
  it("waits above the window, out of sight but focusable, and drops in when focused", () => {
    expect(plain).toMatch(/\.skip-link \{[^}]*position: absolute;/);
    expect(plain).toMatch(/\.skip-link \{[^}]*transform: translateY\(calc\(-100% - 1rem\)\);/);
    expect(plain).toMatch(/\.skip-link:focus-visible \{ transform: none; \}/);
    // Not display: none or visibility: hidden, which a keyboard cannot reach.
    const rule = /\.skip-link \{[^}]*\}/.exec(plain)![0];
    expect(rule).not.toMatch(/display:\s*none|visibility:\s*hidden/);
    expect(rule).toMatch(/z-index: 40;/);
  });
});

describe("a row that opens and closes", () => {
  it("is 28 px tall under a mouse and 44 px under a finger", () => {
    expect(plain).toMatch(/\.disclosure \{ padding-block: 0\.25rem; \}/);
    expect(plain).toMatch(
      /@media \(pointer: coarse\) \{ \.disclosure \{ padding-block: 0\.8125rem; \} \}/,
    );
    // 20 px of line and 2 x 13 px of padding is 46: over the 44 px a finger needs.
    expect(0.8125 * 16 * 2 + 20).toBeGreaterThanOrEqual(44);
    expect(0.25 * 16 * 2 + 20).toBeGreaterThanOrEqual(24);
  });
});

describe("the keyboard shortcuts button", () => {
  it("leaves only a touch screen narrower than a tablet", () => {
    expect(plain).toMatch(
      /@media \(hover: none\) and \(max-width: 47\.99rem\) \{ \.nav-keys \{ display: none; \} \}/,
    );
  });

  it("is not hidden by every screen that cannot hover: a tablet with a keyboard keeps it", () => {
    // R1's rule hides a button on any (hover: none) at any width; this one is narrower.
    expect(plain).not.toMatch(/@media \(hover: none\) \{/);
    expect(plain).not.toContain("needs-keyboard");
  });
});
