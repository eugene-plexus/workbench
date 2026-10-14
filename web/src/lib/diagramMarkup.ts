/**
 * A diagram the worker drew, made into a picture the page can show
 * (workbench-rich-content-roadmap.md RC2). What the worker sends is read as
 * untrusted: Mermaid ran on what a model wrote.
 *
 * The tree must be the shape the worker sends ([tag, attributes, children]);
 * then every element, attribute and style passes an allowlist of what
 * Mermaid draws with, and the result is written out again as a new SVG
 * document, every name from this file's tables and every value escaped.
 *
 * - No element that runs, fetches, links or holds HTML: a link (`<a>`) becomes
 *   a group, keeping its drawing and losing its address; a `<foreignObject>` (a user journey's
 *   HTML label, beside the text it falls back to) and an `<image>` (a shape
 *   drawn as a picture from an address) are dropped; any other element
 *   outside the list fails the diagram, which then shows its source.
 * - A value may point only inside the drawing (`url(#id)`, `href="#id"`); no
 *   other `url()`, function, escape or markup passes.
 *
 * The page shows the result as an image (`<img>`), a second lock on the same
 * door: an image runs no script and fetches nothing. So its ids cannot meet
 * the page's, or another diagram's.
 */

/** The diagram's drawing holds something this page does not show. */
export class Refused extends Error {}

type Tree = [string, Record<string, string>, Array<Tree | string>];

/** What the page shows, copies and downloads. */
export type Picture = {
  /** The drawing as an SVG document. */
  svg: string;
  /** The same, for an `<img>`. */
  src: string;
  width: number;
  height: number;
  kind: string;
  /** The diagram's own title and description, if it gave them (accTitle, accDescr). */
  title: string;
  description: string;
};

/** At most this many elements, and this much SVG. */
export const MAX_ELEMENTS = 20_000;
export const MAX_SVG = 6_000_000;
/** A side longer than this is not drawn (in CSS pixels). */
export const MAX_SIDE = 50_000;

/**
 * The elements kept, by lower-case name, written with SVG's own case. Maps,
 * not objects: a name like `constructor` must find nothing.
 */
const ELEMENTS = new Map<string, string>(
  [
    "svg",
    "g",
    "path",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "text",
    "tspan",
    "defs",
    "marker",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "filter",
    "feDropShadow",
    "feGaussianBlur",
    "feOffset",
    "feFlood",
    "feComposite",
    "feMerge",
    "feMergeNode",
    "feBlend",
    "feColorMatrix",
    "title",
    "desc",
    "symbol",
    "use",
    "switch",
  ].map((name): [string, string] => [name.toLowerCase(), name]),
);

/**
 * A link keeps what it draws and loses where it goes: it becomes a group,
 * with its place (Mermaid puts a linked node's transform on the link).
 */
const RENAMED = new Map([["a", "g"]]);
/**
 * HTML inside the drawing, and a picture from elsewhere (a shape with
 * `img:`): dropped with all they hold, as an answer's own images are never
 * fetched (Markdown.tsx).
 */
const DROPPED = new Set(["foreignobject", "image"]);

/** The attributes kept, by lower-case name, written with SVG's own case. */
const ATTRIBUTES = new Map<string, string>(
  [
    "id",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "fx",
    "fy",
    "dx",
    "dy",
    "width",
    "height",
    "d",
    "points",
    "transform",
    "transform-origin",
    "viewBox",
    "preserveAspectRatio",
    "fill",
    "fill-opacity",
    "fill-rule",
    "clip-rule",
    "stroke",
    "stroke-width",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "opacity",
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "text-anchor",
    "dominant-baseline",
    "alignment-baseline",
    "text-decoration",
    "letter-spacing",
    "marker-start",
    "marker-mid",
    "marker-end",
    "markerWidth",
    "markerHeight",
    "markerUnits",
    "refX",
    "refY",
    "orient",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "gradientTransform",
    "clip-path",
    "clipPathUnits",
    "filter",
    "filterUnits",
    "flood-color",
    "flood-opacity",
    "stdDeviation",
    "in",
    "in2",
    "result",
    "operator",
    "mode",
    "values",
    "type",
    "visibility",
    "display",
    "href",
    "style",
  ].map((name): [string, string] => [name.toLowerCase(), name]),
);
// SVG 2's `href`: an `xlink:` prefix needs a namespace the drawing does not declare.
ATTRIBUTES.set("xlink:href", "href");

/** The styles kept: how a shape and its text look, nothing that moves or reaches out. */
const STYLES = new Set([
  "fill",
  "fill-opacity",
  "fill-rule",
  "clip-rule",
  "stroke",
  "stroke-width",
  "stroke-dasharray",
  "stroke-dashoffset",
  "stroke-linecap",
  "stroke-linejoin",
  "stroke-miterlimit",
  "stroke-opacity",
  "opacity",
  "color",
  "display",
  "visibility",
  "filter",
  "clip-path",
  "marker",
  "marker-start",
  "marker-mid",
  "marker-end",
  "font-family",
  "font-size",
  "font-weight",
  "font-style",
  "font-variant",
  "text-anchor",
  "dominant-baseline",
  "alignment-baseline",
  "text-decoration",
  "letter-spacing",
  "word-spacing",
  "shape-rendering",
  "rx",
  "ry",
]);

const ID = /^[\w-]{1,200}$/;
const LOCAL_URL = /url\(\s*#([\w-]{1,200})\s*\)/g;

/**
 * A value that names nothing outside the drawing: letters, numbers, units,
 * colours (hex, rgb(), hsl()), calc(), a transform's functions, quoted font
 * names and `url(#id)`. A path's data is letters and numbers.
 */
export function safeValue(value: string): boolean {
  if (value.length > 200_000) return false;
  const rest = value
    .replace(LOCAL_URL, "")
    .replace(/\b(?:rgba?|hsla?|calc|translate|rotate|scale|matrix|skewX|skewY)\(/gi, "");
  return /^[\w.#%+\-*/\s,'")]*$/.test(rest);
}

function escapeText(text: string): string {
  return (
    text
      // What XML may not hold: control characters, and half a surrogate pair.
      // eslint-disable-next-line no-control-regex -- matching them is the point
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f￾￿]/g, "")
      .replace(/[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]/g, "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
  );
}

function escapeAttribute(value: string): string {
  return escapeText(value)
    .replace(/"/g, "&quot;")
    .replace(/[\n\r\t]/g, " ");
}

function isTree(node: unknown): node is Tree {
  return (
    Array.isArray(node) &&
    node.length === 3 &&
    typeof node[0] === "string" &&
    typeof node[1] === "object" &&
    node[1] !== null &&
    !Array.isArray(node[1]) &&
    Array.isArray(node[2])
  );
}

/** A style attribute's declarations that pass, written out again. */
function cleanStyle(text: string): string {
  const kept: string[] = [];
  for (const part of text.split(";")) {
    const colon = part.indexOf(":");
    if (colon < 0) continue;
    const name = part.slice(0, colon).trim().toLowerCase();
    let value = part.slice(colon + 1).trim();
    if (!STYLES.has(name) || !value || /!important/i.test(value) || !safeValue(value)) continue;
    // A quote the split at `;` left open would swallow what follows it.
    if ((value.match(/'/g)?.length ?? 0) % 2 || (value.match(/"/g)?.length ?? 0) % 2) continue;
    // A bare number is no font size in CSS (a user journey writes one).
    if (name === "font-size" && /^\d+(\.\d+)?$/.test(value)) value = `${value}px`;
    kept.push(`${name}:${value}`);
  }
  return kept.join(";");
}

type Reading = { count: number; title: string; description: string };

/** What an element holds, written out. */
function writeChildren(node: Tree, reading: Reading, depth: number): string {
  return node[2]
    .map((child) => {
      if (typeof child === "string") return escapeText(child);
      if (!isTree(child)) throw new Refused("its drawing could not be read");
      return write(child, reading, depth + 1);
    })
    .join("");
}

/** One element written out, or "" when it is dropped. */
function write(node: Tree, reading: Reading, depth: number): string {
  if (++reading.count > MAX_ELEMENTS) throw new Refused("its drawing is too large to show here");
  if (depth > 200) throw new Refused("its drawing is nested too deeply to show here");
  const lower = node[0].toLowerCase();
  if (DROPPED.has(lower)) return "";
  const inner = (): string => writeChildren(node, reading, depth);
  const name = ELEMENTS.get(RENAMED.get(lower) ?? lower);
  if (!name) throw new Refused(`its drawing has a <${node[0].slice(0, 40)}> element`);
  const attributes: string[] = [];
  const written = new Set<string>();
  for (const [rawName, rawValue] of Object.entries(node[1])) {
    if (typeof rawValue !== "string") continue;
    const attribute = ATTRIBUTES.get(rawName.toLowerCase());
    // Two spellings of one name would be two of one attribute: the first counts.
    if (!attribute || written.has(attribute)) continue;
    let value = rawValue;
    if (attribute === "href") {
      // Only a <use> of a shape in this drawing.
      if (name !== "use" || !/^#[\w-]{1,200}$/.test(value)) continue;
    } else if (attribute === "id") {
      if (!ID.test(value)) continue;
    } else if (attribute === "style") {
      value = cleanStyle(value);
      if (!value) continue;
    } else if (!safeValue(value)) continue;
    written.add(attribute);
    attributes.push(` ${attribute}="${escapeAttribute(value)}"`);
  }
  const body = inner();
  if (lower === "title" && depth === 1 && !reading.title) reading.title = textOf(node);
  if (lower === "desc" && depth === 1 && !reading.description) reading.description = textOf(node);
  return `<${name}${attributes.join("")}>${body}</${name}>`;
}

function textOf(node: Tree): string {
  return node[2]
    .map((child) => (typeof child === "string" ? child : isTree(child) ? textOf(child) : ""))
    .join("")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 500);
}

/** The drawing's size, from its viewBox: [x, y, width, height]. */
function viewBoxOf(root: Tree): [number, number, number, number] {
  const parts = (root[1].viewBox ?? "")
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  if (parts.length !== 4 || !parts.every(Number.isFinite)) {
    throw new Refused("its drawing has no size");
  }
  const [x = 0, y = 0, width = 0, height = 0] = parts;
  if (width <= 0 || height <= 0) throw new Refused("its drawing has no size");
  if (width > MAX_SIDE || height > MAX_SIDE) {
    throw new Refused("its drawing is too large to show here");
  }
  return [x, y, width, height];
}

/** A colour the page may paint behind the drawing (hex only). */
const HEX = /^#[0-9a-f]{3,8}$/i;

/**
 * The worker's drawing as a picture, behind it `background` (so a download
 * reads as it did on the page). Throws `Refused`, saying why, when the
 * drawing is not the shape the worker sends or holds what is not allowed.
 */
export function toPicture(drawing: unknown, kind: string, background: string): Picture {
  if (!isTree(drawing) || drawing[0].toLowerCase() !== "svg") {
    throw new Refused("its drawing could not be read");
  }
  const [x, y, width, height] = viewBoxOf(drawing);
  // The root is written here, with its size; what it holds goes through `write`.
  const reading: Reading = { count: 1, title: "", description: "" };
  const inner = writeChildren(drawing, reading, 0);
  const style = cleanStyle(drawing[1].style ?? "");
  const fill = HEX.test(background) ? background : "#ffffff";
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"` +
    ` viewBox="${x} ${y} ${width} ${height}"${style ? ` style="${escapeAttribute(style)}"` : ""}>` +
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="${fill}"/>` +
    `${inner}</svg>`;
  if (svg.length > MAX_SVG) throw new Refused("its drawing is too large to show here");
  return {
    svg,
    src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`,
    width,
    height,
    kind,
    title: reading.title,
    description: reading.description,
  };
}
