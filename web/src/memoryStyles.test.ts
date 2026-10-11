/**
 * The rules MR1 added to the stylesheet (the block marked "MR1: memory"), read
 * as text: jsdom lays nothing out, so these pin what each rule promises, and
 * the arithmetic behind it. The widths are the ones the reading check measured
 * in Chrome (it fails if the bar takes a second row on a phone).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(import.meta.dirname, "styles.css"), "utf8");
const MARK = "/* MR1: memory */";
const START = "/* ---- MR1: the top bar with a fifth page ---- */";
const END = "/* ---- end MR1 ---- */";
const block = css.slice(css.indexOf(START) + START.length, css.indexOf(END));
const plain = block.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\s+/g, " ");

describe("MR1's block", () => {
  it("is in the stylesheet once, marked, and in the components layer, which closes right after it", () => {
    expect(css.split(MARK)).toHaveLength(2);
    expect(css.split(START)).toHaveLength(2);
    expect(css.split(END)).toHaveLength(2);
    expect(block.length).toBeGreaterThan(50);
    // The mark, then the layer, then the rules, and the layer's close straight after the end mark
    // (a later slice may add its own block after this one).
    const layer = css.indexOf("@layer components {", css.indexOf(MARK));
    expect(layer).toBeGreaterThan(css.indexOf(MARK));
    expect(css.slice(css.indexOf(MARK) + MARK.length, layer).trim()).toBe("");
    expect(
      css
        .slice(css.indexOf(END) + END.length)
        .trimStart()
        .startsWith("}"),
    ).toBe(true);
  });

  it("comes after the rules it overrides, so it wins in the same layer", () => {
    const before = css.indexOf("  .nav-tab {\n      padding-inline: 0.5rem;\n    }");
    expect(before).toBeGreaterThan(0);
    expect(css.indexOf(START)).toBeGreaterThan(before);
  });
});

describe("the top bar with five pages on a phone", () => {
  it("tightens each tab's padding below 40 rem, where the icons are already gone", () => {
    expect(plain).toMatch(
      /@media \(max-width: 40rem\) \{ \.nav-tab \{ padding-inline: 0\.25rem; \} \}/,
    );
    // The same breakpoint as the rule that hides the icons and set the old padding.
    expect(css).toMatch(/@media \(max-width: 40rem\) \{\s*\.nav-tab \{\s*padding-inline: 0\.5rem;/);
  });

  it("goes to the 13 px floor only on a phone narrower than 368 px, and never below it", () => {
    const rule =
      /@media \(max-width: ([\d.]+)rem\) \{ \.nav-tab \{ font-size: ([\d.]+)rem; \} \}/.exec(plain);
    expect(rule).not.toBeNull();
    const [, width, size] = rule!;
    expect(Number(width) * 16).toBeLessThan(368);
    // A 360 px phone is inside it, a 375 px phone is not.
    expect(360).toBeLessThan(Number(width) * 16);
    expect(375).toBeGreaterThan(Number(width) * 16);
    expect(Number(size) * 16).toBe(13);
  });

  it("fits where the measurements say it does, and did not before", () => {
    // What a phone leaves for the bar: its width less the bar's 8 px either side.
    const room = (phone: number) => phone - 16;
    // Five labels, in px, measured in Chrome: padding of 8 px a side, of 4 px, and of 4 px at 13 px type.
    const was = 394;
    const padded = 354;
    const small = 334;
    expect(was).toBeGreaterThan(room(390));
    expect(padded).toBeLessThanOrEqual(room(390));
    expect(padded).toBeLessThanOrEqual(room(375));
    expect(padded).toBeGreaterThan(room(360));
    expect(small).toBeLessThanOrEqual(room(360));
    // Each tab saves 4 px a side of padding: 5 tabs, 8 px each, 40 px in all.
    expect(was - padded).toBe(5 * 8);
  });
});
