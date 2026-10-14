/**
 * The page's reading of what the diagram worker drew (diagramMarkup.ts):
 * only the worker's shape, only the allowlist, written out again escaped.
 */

import { MAX_ELEMENTS, Refused, safeValue, toPicture } from "./diagramMarkup";

type Tree = [string, Record<string, string>, Array<Tree | string>];

const svg = (children: Array<Tree | string>, attrs: Record<string, string> = {}): Tree => [
  "svg",
  { viewBox: "-8 -8 200 100", ...attrs },
  children,
];

function picture(children: Array<Tree | string>, attrs?: Record<string, string>) {
  return toPicture(svg(children, attrs), "Flowchart", "#1b1e22");
}

it("writes a document of its own: its size, a background, SVG's own names", () => {
  const p = picture([
    [
      "linearGradient",
      { id: "g1", gradientUnits: "objectBoundingBox" },
      [["stop", { offset: "0%" }, []]],
    ],
    ["rect", { x: "0", y: "0", width: "10", height: "10", style: "fill:url(#g1)" }, []],
  ]);
  expect(p.svg).toMatch(
    /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" width="200" height="100" viewBox="-8 -8 200 100">/,
  );
  expect(p.svg).toContain('<rect x="-8" y="-8" width="200" height="100" fill="#1b1e22"/>');
  expect(p.svg).toContain('<linearGradient id="g1" gradientUnits="objectBoundingBox">');
  expect(p.svg).toContain('style="fill:url(#g1)"');
  expect([p.width, p.height, p.kind]).toEqual([200, 100, "Flowchart"]);
  expect(decodeURIComponent(p.src.slice(p.src.indexOf(",") + 1))).toBe(p.svg);
});

it("reads the diagram's own title and description", () => {
  const p = picture([
    ["title", {}, ["Release  steps"]],
    ["desc", {}, ["How a release goes out"]],
  ]);
  expect([p.title, p.description]).toEqual(["Release steps", "How a release goes out"]);
});

it.each([
  ["a string", "<svg></svg>"],
  ["another root", ["g", { viewBox: "0 0 1 1" }, []]],
  ["a child of another shape", svg([["g", {}, [42 as unknown as string]]])],
  ["no size", ["svg", {}, []]],
  ["a size of nothing", svg([], { viewBox: "0 0 0 10" })],
  ["a size past the limit", svg([], { viewBox: "0 0 60000 10" })],
])("refuses %s", (_, drawing) => {
  expect(() => toPicture(drawing, "Flowchart", "#fff")).toThrow(Refused);
});

it.each(["script", "iframe", "html:div", "animate", "set", "style"])(
  "fails a drawing with a <%s>, saying so",
  (tag) => {
    expect(() => picture([[tag, {}, ["x"]]])).toThrow(`its drawing has a <${tag}> element`);
  },
);

it("drops HTML and pictures from elsewhere with all they hold", () => {
  const p = picture([
    [
      "switch",
      {},
      [
        ["foreignObject", { width: "10" }, [["div", {}, ["<b>hi</b>"]]]],
        ["text", {}, ["Fallback"]],
      ],
    ],
    ["image", { href: "https://evil.example/x.png" }, []],
  ]);
  expect(p.svg).not.toMatch(/foreignObject|div|image|evil/);
  expect(p.svg).toContain("<switch><text>Fallback</text></switch>");
});

it("keeps a link's drawing and place, not its address", () => {
  const p = picture([
    [
      "a",
      {
        href: "javascript:alert(1)",
        "xlink:href": "https://x.example",
        transform: "translate(98, 217)",
      },
      [["rect", { width: "5", height: "5" }, []]],
    ],
  ]);
  expect(p.svg).toContain(
    '<g transform="translate(98, 217)"><rect width="5" height="5"></rect></g>',
  );
  expect(p.svg).not.toMatch(/href|javascript|x\.example/);
});

it("drops handlers, outside addresses and what is not on the list", () => {
  const p = picture([
    [
      "rect",
      {
        onclick: "alert(1)",
        onload: "alert(1)",
        fill: "url(https://evil.example/p.png)",
        stroke: "javascript:alert(1)",
        "data-id": "n1",
        class: "node",
        filter: "url(#shadow)",
        width: "5",
      },
      [],
    ],
    ["use", { href: "https://evil.example/s.svg#a" }, []],
    ["use", { "xlink:href": "#shape" }, []],
    ["path", { href: "#shape", d: "M0 0L5 5" }, []],
  ]);
  expect(p.svg).not.toMatch(/onclick|onload|evil|javascript|data-id|class=/);
  expect(p.svg).toContain('<rect filter="url(#shadow)" width="5"></rect>');
  // SVG 2's href: an xlink: prefix needs a namespace the drawing does not declare.
  expect(p.svg).toContain('<use></use><use href="#shape"></use>');
  expect(p.svg).toContain('<path d="M0 0L5 5"></path>');
});

it("keeps only the styles a picture draws with, and nothing that reaches out", () => {
  const p = picture([
    [
      "rect",
      {
        style:
          "fill:#2b353d;stroke:hsl(206, 0%, 30%);animation:dash 2s infinite;cursor:pointer;" +
          "background-color:red;background-image:url(https://evil.example/b.png);" +
          "stroke:url(https://evil.example/s.png);fill-opacity:0.5 !important;" +
          "font-family:\\75 rl(x);filter:url(#ok);font-size:14",
      },
      [],
    ],
  ]);
  expect(p.svg).toContain(
    'style="fill:#2b353d;stroke:hsl(206, 0%, 30%);filter:url(#ok);font-size:14px"',
  );
});

it("escapes text and values, and drops what XML cannot hold", () => {
  const p = picture([
    [
      "text",
      { "font-family": 'Arial, "Liberation Sans"' },
      [`<b onclick="x">&amp;</b>\u0001 \ud800 ok`],
    ],
    // A value with markup or `=` in it is no value of a drawing.
    ["text", { "font-family": 'Arial" onload="alert(1)' }, ["two"]],
  ]);
  expect(p.svg).toContain(
    '<text font-family="Arial, &quot;Liberation Sans&quot;">&lt;b onclick="x"&gt;&amp;amp;&lt;/b&gt;  ok</text>',
  );
  expect(p.svg).toContain("<text>two</text>");
  // An unpaired surrogate would make the address throw.
  expect(() => decodeURIComponent(p.src.slice(p.src.indexOf(",") + 1))).not.toThrow();
});

it("finds nothing under a name every object has", () => {
  expect(() => picture([["constructor", {}, []]])).toThrow(
    "its drawing has a <constructor> element",
  );
  const p = picture([["rect", { constructor: "x", toString: "y", hasOwnProperty: "z" }, []]]);
  expect(p.svg).toContain("<rect></rect>");
});

it("writes one attribute once, however it is spelled", () => {
  const p = picture([
    ["use", { HREF: "#a", href: "#b", "xlink:href": "#c", ID: "x", id: "y" }, []],
  ]);
  expect(p.svg).toContain('<use href="#a" id="x"></use>');
});

it("drops a style whose quote the split left open", () => {
  const p = picture([["text", { style: "font-family:'a;b';fill:#123456" }, ["t"]]]);
  expect(p.svg).toContain('<text style="fill:#123456">');
});

it("refuses a drawing with too many elements, or nested too deeply", () => {
  const many: Tree[] = Array.from({ length: MAX_ELEMENTS }, () => ["g", {}, []]);
  expect(() => picture(many)).toThrow("its drawing is too large to show here");
  let deep: Tree = ["g", {}, []];
  for (let i = 0; i < 250; i++) deep = ["g", {}, [deep]];
  expect(() => picture([deep])).toThrow("its drawing is nested too deeply to show here");
});

it("paints white behind a drawing when the colour is not one it knows", () => {
  const p = toPicture(svg([]), "Pie chart", "red; x");
  expect(p.svg).toContain('fill="#ffffff"');
});

it.each([
  ["#2b353d", true],
  ["rgb(1 2 3 / 0.5)", true],
  ["hsl(206.6, 0%, 30.39%)", true],
  ["translate(1, 2) rotate(-45) scale(2)", true],
  ['Arial, "Liberation Sans", sans-serif', true],
  ["url(#marker-1)", true],
  ["M98,143.92L98,148.087C98,152.253,98,160.587,98,168.253", true],
  ["url(https://evil.example)", false],
  ["url(data:image/png;base64,AAAA)", false],
  ["javascript:alert(1)", false],
  ["expression(alert(1))", false],
  ["image-set(x)", false],
  ["var(--x)", false],
  ["\\75 rl(x)", false],
  ["a<b", false],
  ["@import", false],
])("a value %j is %s", (value, ok) => {
  expect(safeValue(value)).toBe(ok);
});
