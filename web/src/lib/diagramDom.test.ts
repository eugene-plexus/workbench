// @vitest-environment node
/**
 * The diagram worker's stand-in DOM (diagramDom.ts): the sizes Mermaid reads
 * back, its stylesheet read and written into each element, and the tree the
 * page is sent. Text is measured at a fixed width a letter here.
 */

import {
  inlineStyles,
  install,
  parseCss,
  parseTransform,
  pathPoints,
  resolveVars,
  specificity,
  toTree,
  type Font,
} from "./diagramDom";

const measured: Font[] = [];
install((text, font) => {
  measured.push(font);
  return {
    width: text.length * font.size * 0.5,
    ascent: font.size * 0.8,
    descent: font.size * 0.2,
  };
});

/** A drawing read as the worker reads Mermaid's (an SVG document, its root `#d`). */
function svg(markup: string): Element {
  return new DOMParser().parseFromString(
    `<svg xmlns="http://www.w3.org/2000/svg" id="d">${markup}</svg>`,
    "image/svg+xml",
  ).documentElement;
}

afterEach(() => {
  measured.length = 0;
});

type Boxed = Element & { getBBox(): { x: number; y: number; width: number; height: number } };
const box = (el: Element | null) => (el as Boxed).getBBox();

describe("Mermaid's stylesheet", () => {
  it("reads rules in the order they apply, and its variables", () => {
    const sheet = parseCss(
      ":root{--font:Arial}#d .node rect{fill:red}#d{font-size:16px}" +
        "#d .a,#d .b:hover{stroke:blue}@keyframes dash{to{x:1}}#d .node .label{fill:green}",
    );
    expect(sheet.rules.map((r) => r.selector)).toEqual([
      "#d",
      "#d .a",
      "#d .node rect",
      "#d .node .label",
    ]);
    expect(sheet.vars.get("--font")).toBe("Arial");
    expect(resolveVars("var(--font, x) var(--none, Helvetica)", sheet.vars)).toBe(
      "Arial Helvetica",
    );
  });

  it("weighs a selector: ids, then classes, then types", () => {
    expect(specificity("#d .node rect")).toBe(10_101);
    expect(specificity(".a.b")).toBeGreaterThan(specificity("g rect text"));
  });

  it("is written into each element; an own style beats a rule unless the rule is important", () => {
    const root = svg(
      "<style>#d .n{fill:red;stroke:blue !important;font-family:var(--f)}:root{--f:Arial}</style>" +
        '<rect class="n" style="fill:green;stroke:black"></rect><rect class="n"></rect>',
    );
    inlineStyles(root);
    const [own, plain] = [...root.querySelectorAll("rect")];
    expect(root.querySelector("style")).toBeNull();
    expect(own?.getAttribute("style")).toBe("fill:green;stroke:blue;font-family:Arial");
    expect(plain?.getAttribute("style")).toBe("fill:red;stroke:blue;font-family:Arial");
  });
});

describe("sizes", () => {
  it("measures text in the size its stylesheet gives it, from its anchor and lines", () => {
    const root = svg(
      "<style>#d .big{font-size:20px;font-weight:bold}</style>" +
        '<text class="big" x="100" y="50" text-anchor="middle">' +
        '<tspan x="100" dy="1em">abcd</tspan><tspan x="100" dy="1em">ab</tspan></text>',
    );
    const text = root.querySelector("text");
    // 4 letters at 10 px each, centred on 100; two lines, 20 px apart.
    expect(box(text)).toEqual({ x: 80, y: 54, width: 40, height: 40 });
    expect(measured.at(-1)).toMatchObject({ size: 20, weight: "bold" });
  });

  it("centres text on a middle baseline, and takes em from its parent", () => {
    const root = svg(
      '<g style="font-size:10px"><text y="0" dominant-baseline="middle" font-size="2em">ab</text></g>',
    );
    expect(box(root.querySelector("text"))).toEqual({ x: 0, y: -10, width: 20, height: 20 });
  });

  it("measures shapes, and a group through its children's transforms", () => {
    const root = svg(
      '<g><rect x="0" y="0" width="10" height="20" transform="translate(5, 5)"></rect>' +
        '<circle cx="50" cy="50" r="5"></circle>' +
        '<rect width="10" height="10" transform="rotate(90)"></rect></g>',
    );
    expect(box(root.querySelector("g"))).toEqual({ x: -10, y: 0, width: 65, height: 55 });
  });

  it("measures a path by its points and its curves' control points", () => {
    expect(pathPoints("M0,0 C10,-10 20,-10 30,0 l5 5 h10 v-20 Z")).toEqual([
      [0, 0],
      [10, -10],
      [20, -10],
      [30, 0],
      [35, 5],
      [45, 5],
      [45, -15],
    ]);
  });

  it("passes a number after Z instead of reading it for good", () => {
    expect(pathPoints("M0 0 Z 5 L 1 1")).toEqual([
      [0, 0],
      [1, 1],
    ]);
  });

  it("composes transforms as SVG does", () => {
    const [a, b, c, d, e, f] = parseTransform("translate(10, 20) scale(2)");
    expect([a, b, c, d, e, f]).toEqual([2, 0, 0, 2, 10, 20]);
  });
});

describe("the tree sent to the page", () => {
  it("keeps names' case, attributes and text, and stops past its limit", () => {
    const root = svg(
      '<linearGradient id="g"><stop offset="0"></stop></linearGradient><text>hi</text>',
    );
    root.setAttribute("viewBox", "0 0 1 1");
    const tree = toTree(root, 10);
    expect(tree?.[0]).toBe("svg");
    expect(tree?.[1].viewBox).toBe("0 0 1 1");
    expect(JSON.stringify(tree)).toContain('"linearGradient"');
    expect(JSON.stringify(tree)).toContain('"hi"');
    expect(toTree(root, 3)).toBeNull();
  });
});
