/**
 * KaTeX's markup made into React elements (workbench-rich-content-roadmap.md
 * RC1), never into `innerHTML`: the page's policy refuses a `style`
 * attribute written into markup (workbench-reading.md 10 counted 33 refusals
 * for two formulas), and React sets a style through the style object, which
 * the policy allows.
 *
 * Reading the markup is not trusting it. The reader accepts only the shape
 * KaTeX writes (tags, double- or single-quoted attributes, text) and gives up
 * on anything else; then every element, attribute, class and style passes an
 * allowlist of what KaTeX makes with `trust` off. An element outside it fails
 * the whole formula, which is then shown as its source; an attribute outside
 * it is dropped. No element that fetches, links or runs anything is on it.
 */

import { createElement, type ReactNode } from "react";

type Node = { tag: string; attrs: Array<[string, string]>; children: Array<Node | string> };

/** The formula's layout holds something this page does not show. */
export class Refused extends Error {}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

function decode(text: string): string {
  return text.replace(/&(#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, (whole, name: string) => {
    if (name[0] !== "#") return ENTITIES[name] ?? whole;
    const point = /^#[xX]/.test(name) ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
    return point >= 0 && point <= 0x10ffff ? String.fromCodePoint(point) : whole;
  });
}

const TAG =
  /<(\/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[a-zA-Z][\w:-]*\s*=\s*(?:"[^"<]*"|'[^'<]*'))*)\s*(\/?)>/y;
const ATTRIBUTE = /\s+([a-zA-Z][\w:-]*)\s*=\s*(?:"([^"<]*)"|'([^'<]*)')/g;

/** The markup as a tree, or null if it is not the shape KaTeX writes. */
export function readMarkup(markup: string): Array<Node | string> | null {
  const root: Node = { tag: "", attrs: [], children: [] };
  const open: Node[] = [root];
  let at = 0;
  while (at < markup.length) {
    const top = open[open.length - 1] ?? root;
    const lt = markup.indexOf("<", at);
    const end = lt === -1 ? markup.length : lt;
    if (end > at) top.children.push(decode(markup.slice(at, end)));
    if (lt === -1) break;
    TAG.lastIndex = lt;
    const match = TAG.exec(markup);
    if (!match) return null;
    const [whole, closing, tag = "", attributes = "", selfClosing] = match;
    if (closing) {
      if (attributes || selfClosing || open.length < 2 || top.tag !== tag) return null;
      open.pop();
    } else {
      const node: Node = { tag, attrs: [], children: [] };
      for (const attribute of attributes.matchAll(ATTRIBUTE)) {
        node.attrs.push([attribute[1] ?? "", decode(attribute[2] ?? attribute[3] ?? "")]);
      }
      top.children.push(node);
      if (!selfClosing) open.push(node);
    }
    at = lt + whole.length;
  }
  return open.length === 1 ? root.children : null;
}

// --- the allowlist -------------------------------------------------------------

const MATHML = new Set([
  "math",
  "semantics",
  "annotation",
  "mrow",
  "mi",
  "mn",
  "mo",
  "ms",
  "mtext",
  "mspace",
  "msup",
  "msub",
  "msubsup",
  "mfrac",
  "msqrt",
  "mroot",
  "mover",
  "munder",
  "munderover",
  "mtable",
  "mtr",
  "mtd",
  "mlabeledtr",
  "menclose",
  "mstyle",
  "mpadded",
  "mphantom",
]);

const MATHML_ATTRIBUTES = new Set([
  "accent",
  "accentunder",
  "columnalign",
  "columnlines",
  "columnspacing",
  "depth",
  "display",
  "displaystyle",
  "encoding",
  "fence",
  "form",
  "height",
  "largeop",
  "linebreak",
  "linethickness",
  "lspace",
  "mathbackground",
  "mathcolor",
  "mathsize",
  "mathvariant",
  "maxsize",
  "minsize",
  "movablelimits",
  "notation",
  "rowlines",
  "rowspacing",
  "rspace",
  "scriptlevel",
  "separator",
  "stretchy",
  "symmetric",
  "valign",
  "voffset",
  "width",
]);

/** Each other element and the attributes it may keep, by their React names. */
const ELEMENTS: Record<string, Record<string, string>> = {
  span: { "aria-hidden": "aria-hidden" },
  svg: {
    width: "width",
    height: "height",
    viewBox: "viewBox",
    preserveAspectRatio: "preserveAspectRatio",
  },
  path: { d: "d" },
  line: { x1: "x1", y1: "y1", x2: "x2", y2: "y2", "stroke-width": "strokeWidth" },
};

/** The styles KaTeX sets (its `CssStyle`), as React names. */
const STYLES = new Set([
  "backgroundColor",
  "border",
  "borderBottomWidth",
  "borderColor",
  "borderRightStyle",
  "borderRightWidth",
  "borderStyle",
  "borderTopWidth",
  "borderWidth",
  "bottom",
  "color",
  "height",
  "left",
  "margin",
  "marginLeft",
  "marginRight",
  "marginTop",
  "minWidth",
  "paddingLeft",
  "position",
  "textShadow",
  "top",
  "verticalAlign",
  "width",
]);

/**
 * How far a formula may pull what is beside it. KaTeX caps a size the formula
 * names (`\rule`, `\kern`) at 20 em, but not below zero: `\kern{-500em}`
 * would pull the rest of the line across the page, and 300 of `\kern{-20em}`
 * the same. So one negative margin may be at most 25 em, and a formula's
 * negative margins together at most 40 em (the most measured in real
 * formulas was 19, for a line of 60 `\!`). Offsets up and down are KaTeX's own
 * layout (a 40-row `aligned` uses `top: 70em`) and are only held to the size
 * limit, as widths and heights are.
 */
const PULLS = new Set(["margin", "marginLeft", "marginRight", "left"]);
const PULL_LIMIT_EM = 25;
const PULLS_LIMIT_EM = 40;
const SIZE_LIMIT_EM = 2_000;

// Lengths, numbers, colour names and hex, and calc() and rgb(): no url(),
// image(), var() or anything else a style could fetch or reach with.
const STYLE_VALUE = /^(?:[\w.#%+\-*/\s,]|calc\(|rgba?\(|\))+$/i;
const ATTRIBUTE_VALUE_REFUSED = /url\s*\(|javascript:|expression\s*\(|[<>]/i;
const CLASS_NAME = /^[\w-]+$/;

function camel(property: string): string {
  return property.trim().replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

/** What one formula has pulled back so far, in em. */
type Pulled = { em: number };

function styleOf(text: string, pulled: Pulled): Record<string, string> {
  const style: Record<string, string> = {};
  for (const declaration of text.split(";")) {
    const colon = declaration.indexOf(":");
    if (colon === -1) continue;
    const name = camel(declaration.slice(0, colon));
    const value = declaration.slice(colon + 1).trim();
    if (!STYLES.has(name) || !value || !STYLE_VALUE.test(value)) continue;
    if (name === "position" && value !== "relative" && value !== "absolute") continue;
    for (const length of value.matchAll(/(-?\d*\.?\d+)em/g)) {
      const em = Number(length[1]);
      if (Math.abs(em) > (PULLS.has(name) ? PULL_LIMIT_EM : SIZE_LIMIT_EM)) {
        throw new Refused(`it moves or sizes text by ${length[0]}, more than this page allows`);
      }
      if (PULLS.has(name) && em < 0) pulled.em -= em;
    }
    if (pulled.em > PULLS_LIMIT_EM) {
      throw new Refused(
        `it pulls text back by ${Math.round(pulled.em)}em in all, more than this page allows`,
      );
    }
    style[name] = value;
  }
  return style;
}

function element(node: Node, key: number, pulled: Pulled): ReactNode {
  const mathml = MATHML.has(node.tag);
  const allowed = ELEMENTS[node.tag];
  if (!mathml && !allowed) throw new Refused(`its layout has a <${node.tag}> element`);
  const props: Record<string, unknown> = { key };
  for (const [name, value] of node.attrs) {
    if (name === "class") {
      const classes = value.split(/\s+/).filter((c) => CLASS_NAME.test(c));
      if (classes.length) props.className = classes.join(" ");
    } else if (name === "style") {
      // The MathML copy is for a screen reader and is not seen; its styles
      // (a box's border, a bold's shadow) have nothing to draw.
      if (mathml) continue;
      const style = styleOf(value, pulled);
      if (Object.keys(style).length) props.style = style;
    } else if (!ATTRIBUTE_VALUE_REFUSED.test(value)) {
      const reactName = mathml ? (MATHML_ATTRIBUTES.has(name) ? name : null) : allowed?.[name];
      if (reactName) props[reactName] = value;
    }
  }
  return createElement(
    node.tag,
    props,
    ...node.children.map((child, index) => draw(child, index, pulled)),
  );
}

function draw(node: Node | string, index: number, pulled: Pulled): ReactNode {
  return typeof node === "string" ? node : element(node, index, pulled);
}

/**
 * KaTeX's markup as React elements. Throws `Refused`, saying why, when the
 * markup is not what KaTeX writes or holds an element off the allowlist.
 */
export function toReact(markup: string): ReactNode {
  const tree = readMarkup(markup);
  if (!tree) throw new Refused("its layout could not be read");
  const pulled: Pulled = { em: 0 };
  return tree.map((node, index) => draw(node, index, pulled));
}
