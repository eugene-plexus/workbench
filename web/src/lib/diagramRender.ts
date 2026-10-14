/**
 * One Mermaid diagram drawn, in the diagram worker (workbench-rich-content-
 * roadmap.md RC2). Load it only after diagramDom.ts's `install`: Mermaid's
 * sanitizer looks for a window when it loads.
 *
 * The configuration is Workbench's and the diagram cannot change it: strict
 * security (no click callbacks), no HTML labels, the dagre layout, the classic
 * look, the page's colours and a font the page's image will have. Every key
 * is on Mermaid's `secure` list, so a `%%{init}%%` directive or a front
 * matter `config:` in the source changes nothing.
 *
 * Only the kinds of diagram this page has been checked against are drawn;
 * another kind says so and shows its source.
 */

import mermaid, { type MermaidConfig } from "mermaid";
import { inlineStyles, toTree, type Tree } from "./diagramDom";

/** A longer source is shown as itself (a 500-step flowchart is 17,540). */
export const MAX_SOURCE = 20_000;
/** A drawing with more elements is not shown (a 300-class diagram has 12,093). */
export const MAX_ELEMENTS = 20_000;
/**
 * The font every diagram is measured and drawn in. The page shows a diagram
 * as an image, which can use only the fonts the computer has, so this is a
 * family every computer has (Linux's Liberation Sans has Arial's widths): a
 * downloaded diagram measured here fits its boxes elsewhere too.
 */
export const FONT = 'Arial, "Liberation Sans", Helvetica, sans-serif';

/** The kinds drawn, by Mermaid's name for each, and what a person calls them. */
export const KINDS: Record<string, string> = {
  "flowchart-v2": "Flowchart",
  flowchart: "Flowchart",
  sequence: "Sequence diagram",
  classDiagram: "Class diagram",
  class: "Class diagram",
  stateDiagram: "State diagram",
  state: "State diagram",
  er: "Entity relationship diagram",
  gantt: "Gantt chart",
  pie: "Pie chart",
  journey: "User journey",
  timeline: "Timeline",
  gitGraph: "Git graph",
  mindmap: "Mind map",
  quadrantChart: "Quadrant chart",
  xychart: "XY chart",
};

/** Colours from the page's theme, as Mermaid's theme variables. */
export type Palette = Record<string, string>;

export type Drawing = { tree: Tree; kind: string } | { error: string };

/** The configuration, every key of which a diagram's own directives cannot touch. */
function configure(palette: Palette, dark: boolean): MermaidConfig {
  const config: MermaidConfig = {
    startOnLoad: false,
    securityLevel: "strict",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
    layout: "dagre",
    look: "classic",
    theme: "base",
    darkMode: dark,
    themeVariables: {
      ...palette,
      darkMode: dark,
      fontFamily: FONT,
      fontSize: "16px",
      // An XY chart's series in the pie's colours (the page's accents).
      xyChart: {
        plotColorPalette: [palette.pie1, palette.pie2, palette.pie3, palette.pie4]
          .filter(Boolean)
          .join(","),
      },
    },
    fontFamily: FONT,
    maxTextSize: MAX_SOURCE,
    maxEdges: 500,
    suppressErrorRendering: true,
    arrowMarkerAbsolute: false,
    deterministicIds: true,
  };
  mermaid.initialize(config);
  const keys = new Set([
    ...Object.keys(mermaid.mermaidAPI.getSiteConfig()),
    ...Object.keys(config),
  ]);
  config.secure = [...keys];
  mermaid.initialize(config);
  return config;
}

/** What Mermaid said went wrong, short: the first line and what it expected. */
export function reason(error: unknown): string {
  const text = (error instanceof Error ? error.message : String(error)).trim();
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const first = lines[0] ?? "no reason was given";
  const expected = lines.find((line) => /^Expecting /.test(line));
  const said = expected && expected !== first ? `${first} ${expected}` : first;
  return said.length > 300 ? `${said.slice(0, 299)}…` : said;
}

/**
 * Mermaid's diagram kinds registered, and the flowchart's code loaded: the
 * worker does this before it says it is ready, so the first flowchart's
 * time is its own.
 */
export function warm(): Promise<void> {
  warmed ??= (async () => {
    configure({}, false);
    await mermaid.parse("flowchart TD\n  a --> b");
  })();
  return warmed;
}
let warmed: Promise<void> | undefined;

/** Draws `source` in `palette`'s colours: a tree of the drawing, or why not. */
export async function draw(source: string, palette: Palette, dark: boolean): Promise<Drawing> {
  if (source.length > MAX_SOURCE) {
    return { error: `it is longer than ${MAX_SOURCE.toLocaleString("en")} characters` };
  }
  await warm();
  configure(palette, dark);
  // The kind first, so a kind not drawn here loads none of its code.
  let type: string;
  try {
    type = mermaid.detectType(source, mermaid.mermaidAPI.getConfig());
  } catch {
    const first = /\S+/.exec(source.replace(/^---[\s\S]*?\n---\s*\n|%%.*\n/g, ""))?.[0] ?? "";
    return { error: `Mermaid has no kind of diagram called “${first.slice(0, 40)}”` };
  }
  const kind = KINDS[type];
  if (!kind) return { error: `Workbench does not draw this kind of diagram (${type})` };
  let svg: string;
  try {
    // One id for every drawing: each is a picture of its own, so its ids
    // meet no other's, and the same diagram is the same picture.
    ({ svg } = await mermaid.render("diagram", source));
  } catch (error) {
    return { error: reason(error) };
  }
  const document = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = document.documentElement;
  if (!root || root.tagName.toLowerCase() !== "svg") return { error: "it drew nothing" };
  inlineStyles(root);
  // A user journey's section names are HTML with a text beside it for where
  // HTML is not drawn; Mermaid colours that text like the box behind it.
  for (const text of root.querySelectorAll("switch > text")) {
    const style = text.getAttribute("style");
    text.setAttribute("style", `${style ? `${style};` : ""}fill:${palette.textColor ?? "#333333"}`);
  }
  const tree = toTree(root, MAX_ELEMENTS);
  if (!tree) return { error: "its drawing is too large to show here" };
  return { tree, kind };
}
