// @vitest-environment node
/**
 * Mermaid in the diagram worker's stand-in DOM (diagramRender.ts), run in
 * Node: text is measured at a fixed width a letter, so sizes are not the
 * browser's, but every rule about what is drawn and what is refused is.
 */

import { install, type Tree } from "./diagramDom";

install((text, font) => ({
  width: text.length * font.size * 0.55,
  ascent: font.size * 0.9,
  descent: font.size * 0.22,
}));
const { draw, KINDS, MAX_SOURCE } = await import("./diagramRender");

const PALETTE = { primaryColor: "#2b353d", primaryTextColor: "#f4f6f7", lineColor: "#b3b9bf" };

async function drawn(source: string) {
  const result = await draw(source, PALETTE, true);
  if ("error" in result) throw new Error(`not drawn: ${result.error}`);
  return result;
}

function walk(tree: Tree, visit: (t: Tree) => void): void {
  visit(tree);
  for (const child of tree[2]) if (typeof child !== "string") walk(child, visit);
}

function tags(tree: Tree): Set<string> {
  const found = new Set<string>();
  walk(tree, (t) => found.add(t[0].toLowerCase()));
  return found;
}

function text(tree: Tree): string {
  let out = "";
  walk(tree, (t) => {
    for (const child of t[2]) if (typeof child === "string") out += child;
  });
  return out;
}

/** Every attribute of every element, as `name=value` lines (not the text). */
function attributes(tree: Tree): string {
  const out: string[] = [];
  walk(tree, (t) => {
    for (const [name, value] of Object.entries(t[1])) out.push(`${name}=${value}`);
  });
  return out.join("\n");
}

const FLOW = `flowchart TD
  A[Start] --> B{Is it working?}
  B -- Yes --> C[Ship it]
  B -- No --> D[Debug]
  D --> B`;

it.each([
  ["flowchart", FLOW, "Flowchart"],
  ["graph", "graph LR\n  a --> b", "Flowchart"],
  ["sequence", "sequenceDiagram\n  Alice->>Bob: Hello\n  Bob-->>Alice: Hi", "Sequence diagram"],
  [
    "class",
    "classDiagram\n  Animal <|-- Dog\n  class Animal {\n    +String name\n  }",
    "Class diagram",
  ],
  [
    "state",
    "stateDiagram-v2\n  [*] --> Idle\n  Idle --> Busy : go\n  Busy --> [*]",
    "State diagram",
  ],
  ["state v1", "stateDiagram\n  [*] --> Idle", "State diagram"],
  ["er", "erDiagram\n  CUSTOMER ||--o{ ORDER : places", "Entity relationship diagram"],
  ["pie", 'pie title Time\n  "Code" : 60\n  "Meetings" : 40', "Pie chart"],
  [
    "gantt",
    "gantt\n  dateFormat YYYY-MM-DD\n  section A\n  Task :a1, 2026-01-01, 3d",
    "Gantt chart",
  ],
  ["journey", "journey\n  title Day\n  section Morning\n    Tea: 5: Me", "User journey"],
  ["timeline", "timeline\n  2025 : one\n  2026 : two", "Timeline"],
  ["gitGraph", "gitGraph\n  commit\n  branch b\n  commit", "Git graph"],
  ["mindmap", "mindmap\n  root((Root))\n    A\n    B", "Mind map"],
  [
    "quadrant",
    "quadrantChart\n  x-axis Low --> High\n  y-axis Low --> High\n  A: [0.3, 0.6]",
    "Quadrant chart",
  ],
  ["xychart", "xychart-beta\n  x-axis [a, b]\n  bar [1, 2]", "XY chart"],
])(
  "draws a %s",
  async (_, source, kind) => {
    const result = await drawn(source);
    expect(result.kind).toBe(kind);
    expect(result.tree[0]).toBe("svg");
    expect(result.tree[1].viewBox).toMatch(/^-?[\d.]+ -?[\d.]+ [\d.]+ [\d.]+$/);
    // Mermaid's stylesheet is written into the elements and goes.
    expect(tags(result.tree).has("style")).toBe(false);
  },
  // The first drawing loads Mermaid's diagram modules on a busy runner. These
  // cases check output; diagramClient tests separately enforce worker deadlines.
  15_000,
);

it("names every kind it draws", () => {
  expect(new Set(Object.values(KINDS)).size).toBe(13);
});

it("writes the stylesheet into each element, in the page's colours", async () => {
  const { tree } = await drawn(FLOW);
  const styles: string[] = [];
  walk(tree, (t) => {
    if (t[1].style) styles.push(t[1].style);
  });
  expect(styles.some((s) => /fill:#2b353d/i.test(s))).toBe(true);
  expect(styles.join(";")).not.toMatch(/var\(/);
  expect(text(tree)).toContain("working?");
});

it("says why a diagram that does not parse is not drawn", async () => {
  const result = await draw("flowchart TD\n  A --> ", PALETTE, true);
  expect(result).toEqual({ error: expect.stringMatching(/^Parse error on line \d+:/) });
  expect("error" in result && result.error.length).toBeLessThanOrEqual(300);
});

it("refuses a kind of diagram it has not been checked against", async () => {
  const result = await draw("sankey-beta\n  a,b,10", PALETTE, true);
  expect(result).toEqual({
    error: "Workbench does not draw this kind of diagram (sankey)",
  });
});

it("says so when the source is no kind of diagram", async () => {
  const result = await draw("%% a note\nhello world\n  a --> b", PALETTE, true);
  expect(result).toEqual({ error: "Mermaid has no kind of diagram called “hello”" });
});

it("refuses a flowchart laid out by ELK, and loads none of ELK", async () => {
  const result = await draw("flowchart-elk TD\n  a --> b", PALETTE, true);
  expect(result).toEqual({
    error: "Workbench does not draw this kind of diagram (flowchart-elk)",
  });
});

it("refuses a source longer than its limit, before Mermaid sees it", async () => {
  const result = await draw(
    `flowchart TD\n${"  a --> b\n".repeat(MAX_SOURCE / 10)}`,
    PALETTE,
    true,
  );
  expect(result).toEqual({ error: "it is longer than 20,000 characters" });
});

describe("a diagram cannot change the configuration", () => {
  const LOOSE = `%%{init: {"securityLevel": "loose", "htmlLabels": true, "flowchart": {"htmlLabels": true}, "theme": "dark", "layout": "elk", "look": "handDrawn", "themeVariables": {"primaryColor": "#ff0000"}, "themeCSS": ".node rect { fill: url(https://evil.example/x) }"}}%%
flowchart TD
  A["<b onclick='alert(1)'>bold</b> <img src='https://evil.example/i.png'>"] --> B[Plain]
  click A call alert(1)
  click B href "javascript:alert(2)"`;

  it("by a directive", async () => {
    const { tree } = await drawn(LOOSE);
    const all = attributes(tree);
    // No HTML labels, no theme, layout or look of its own; the label's markup
    // is its text, and the link goes nowhere.
    expect(tags(tree).has("foreignobject")).toBe(false);
    expect(tags(tree).has("img")).toBe(false);
    expect(all).not.toMatch(/evil\.example/);
    expect(all).not.toMatch(/#ff0000/i);
    expect(all).not.toMatch(/^on|javascript:/im);
    expect(all).toMatch(/#2b353d/i);
    expect(text(tree)).toContain("onclick");
  });

  it("by front matter", async () => {
    const { tree } = await drawn(`---
config:
  securityLevel: loose
  htmlLabels: true
  theme: dark
  themeVariables:
    primaryColor: "#ff0000"
---
flowchart TD
  A[One] --> B[Two]`);
    expect(tags(tree).has("foreignobject")).toBe(false);
    expect(attributes(tree)).not.toMatch(/#ff0000/i);
  });
});

it("keeps a node's own style, and the page reads it later", async () => {
  const { tree } = await drawn("flowchart TD\n  A --> B\n  style A fill:#123456,stroke:#654321");
  expect(attributes(tree)).toMatch(/fill:#123456/);
});
