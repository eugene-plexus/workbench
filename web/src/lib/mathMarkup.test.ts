/**
 * KaTeX's markup made into elements (mathMarkup.ts): what KaTeX writes is
 * read whole, and nothing that fetches, links, runs or moves text across the
 * page gets through.
 */

import { render } from "@testing-library/react";
import { createElement } from "react";
import { MAX_TEX, typeset } from "./mathTypeset";
import { readMarkup, Refused, toReact } from "./mathMarkup";

function markupOf(tex: string, display = false): string {
  const result = typeset(tex, display);
  if (!("markup" in result)) throw new Error(result.error);
  return result.markup;
}

function draw(markup: string): HTMLElement {
  return render(createElement("div", null, toReact(markup))).container;
}

describe("what KaTeX writes", () => {
  it.each([
    ["E = mc^2", false],
    ["x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}", true],
    ["\\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}", true],
    ["\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}", true],
    ["\\overrightarrow{AB} + \\widehat{xyz} + \\sqrt[3]{x}", false],
    ["\\color{red}{x} + \\colorbox{blue}{y} + \\fcolorbox{red}{green}{z} + \\pmb{w}", false],
    ["α + β = γ \\text{ naïve café}", false],
    ["\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}", true],
    ["\\cancel{x} + \\boxed{y} + \\underbrace{a+b}_{c}", true],
  ])("draws %s", (tex, display) => {
    const container = draw(markupOf(tex, display));
    expect(container.querySelector(".katex")).not.toBeNull();
    // MathML for a screen reader, with the source as its annotation.
    const math = container.querySelector("math");
    expect(math?.namespaceURI).toBe("http://www.w3.org/1998/Math/MathML");
    expect(container.querySelector("annotation")?.textContent).toBe(tex);
    expect(container.querySelector(".katex-html")).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector(".katex-display") !== null).toBe(display);
  });

  it("draws a stretchy symbol's svg in the svg namespace", () => {
    const container = draw(markupOf("\\overrightarrow{AB}"));
    const svg = container.querySelector("svg");
    expect(svg?.namespaceURI).toBe("http://www.w3.org/2000/svg");
    expect(svg?.getAttribute("viewBox")).toBeTruthy();
    expect(container.querySelector("path")?.getAttribute("d")).toBeTruthy();
  });

  it("keeps KaTeX's sizes as styles", () => {
    const container = draw(markupOf("\\frac{1}{2}"));
    const struts = [...container.querySelectorAll<HTMLElement>(".katex-strut")];
    expect(struts.length).toBeGreaterThan(0);
    expect(struts.every((s) => s.style.height.endsWith("em"))).toBe(true);
  });

  it("decodes what KaTeX escapes", () => {
    const container = draw(markupOf("a < b > c \\& d"));
    expect(container.querySelector("annotation")?.textContent).toBe("a < b > c \\& d");
  });
});

describe("a formula with trust off", () => {
  it.each([
    "\\href{javascript:alert(1)}{x}",
    "\\url{https://example.com}",
    "\\includegraphics{https://example.com/a.png}",
    "\\htmlClass{fixed inset-0}{x}",
    "\\htmlStyle{position:fixed}{x}",
    "\\htmlId{main}{x}",
    "\\htmlData{x=1}{y}",
  ])("%s makes no link, picture, class, style or id of its own", (tex) => {
    const container = draw(markupOf(tex));
    expect(container.querySelector("a, img, [id], [data-x]")).toBeNull();
    expect(container.querySelector(".fixed, .inset-0")).toBeNull();
    for (const el of container.querySelectorAll<HTMLElement>("[style]")) {
      expect(el.style.position).not.toBe("fixed");
    }
  });

  it("refuses a formula that would pull the line across the page", () => {
    expect(() => toReact(markupOf("a\\kern{-500em}b"))).toThrow(Refused);
    expect(() => toReact(markupOf("a\\kern{-500em}b"))).toThrow(/-500.*em/);
  });

  it("refuses many small pulls that add up to a long one", () => {
    const pulls = `${"\\kern{-20em}".repeat(300)}\\rule{20em}{20em}`;
    expect(() => toReact(markupOf(pulls))).toThrow(/pulls text back by 60em in all/);
    expect(() => toReact(markupOf(`${"\\kern{-2em}".repeat(21)}x`))).toThrow(/in all/);
  });

  it("draws what real formulas pull and raise", () => {
    // Sixty negative thin spaces (19 em of pulls in all), and a 40-row
    // derivation whose rows sit up to 70 em from its middle.
    expect(() =>
      toReact(markupOf("\\int\\!\\!\\!\\int_D f(x,y)\\,dx\\,dy".repeat(20))),
    ).not.toThrow();
    const rows = Array.from({ length: 40 }, (_, i) => `f_{${i}}(x) &= \\binom{${i}}{k} x^k`);
    const aligned = `\\begin{aligned} ${rows.join(" \\\\ ")} \\end{aligned}`;
    expect(markupOf(aligned, true)).toMatch(/top:-[4-9]\d\.\d+em/);
    expect(() => toReact(markupOf(aligned, true))).not.toThrow();
  });

  it("says a formula is too long before laying it out", () => {
    expect(typeset("x".repeat(MAX_TEX + 1), false)).toEqual({
      error: "it is longer than 8,000 characters",
    });
  });

  it("names the error KaTeX found", () => {
    expect(typeset("\\foo{x}", false)).toEqual({ error: "Undefined control sequence: \\foo" });
  });

  it("keeps no macro between formulas", () => {
    expect("markup" in typeset("\\gdef\\shared{X}\\shared", false)).toBe(true);
    expect(typeset("\\shared", false)).toEqual({ error: "Undefined control sequence: \\shared" });
  });

  it("stops at the expansion limit", () => {
    const loop = "\\def\\a{\\a\\a}\\a";
    expect(typeset(loop, false)).toEqual({
      error: expect.stringMatching(/Too many expansions/),
    });
  });
});

describe("markup that is not KaTeX's", () => {
  it.each([
    ['<span class="katex"><script>alert(1)</script></span>', /<script>/],
    ['<span><img src="https://example.com/x.png"/></span>', /<img>/],
    ['<span><a href="https://example.com">x</a></span>', /<a>/],
    ["<svg><foreignObject><div>x</div></foreignObject></svg>", /<foreignObject>/],
    ['<math><mglyph src="https://example.com/x.png"></mglyph></math>', /<mglyph>/],
    ['<iframe src="https://example.com"></iframe>', /<iframe>/],
    ["<style>body{display:none}</style>", /<style>/],
  ])("refuses %s", (markup, reason) => {
    expect(() => toReact(markup)).toThrow(Refused);
    expect(() => toReact(markup)).toThrow(reason);
  });

  it.each([
    "<span>",
    "<span></div>",
    "</span>",
    "<span class=katex>x</span>",
    '<span class="a">x</span></span>',
    "<span <b>>x</span>",
    "<!-- x --><span>x</span>",
  ])("cannot read %s", (markup) => {
    expect(readMarkup(markup)).toBeNull();
    expect(() => toReact(markup)).toThrow(/could not be read/);
  });

  it("drops attributes off the list, and keeps the ones on it", () => {
    const container = draw(
      '<span class="katex" onclick="alert(1)" id="main" aria-hidden="true" data-x="1" tabindex="0">x</span>',
    );
    const span = container.querySelector("span");
    expect(span?.getAttributeNames().sort()).toEqual(["aria-hidden", "class"]);
  });

  it("drops a class that is not a plain name", () => {
    const container = draw('<span class="katex a&quot;b x:y">x</span>');
    expect(container.querySelector("span")?.className).toBe("katex");
  });

  it("drops a style that could fetch, overlay or reach outside", () => {
    const container = draw(
      '<span style="background-color:url(https://example.com/x);position:fixed;top:1em;color:red;behavior:url(x);width:var(--x);height:1em">x</span>',
    );
    const style = container.querySelector("span")?.style;
    expect(style?.backgroundColor).toBe("");
    expect(style?.position).toBe("");
    expect(style?.width).toBe("");
    expect(style?.top).toBe("1em");
    expect(style?.color).toBe("red");
    expect(style?.height).toBe("1em");
  });

  it("drops an attribute value that names a script or an address", () => {
    const container = draw('<math><mi mathvariant="javascript:x" mathcolor="url(x)">x</mi></math>');
    const mi = container.querySelector("mi");
    expect(mi?.getAttribute("mathvariant")).toBeNull();
    expect(mi?.getAttribute("mathcolor")).toBeNull();
  });
});
