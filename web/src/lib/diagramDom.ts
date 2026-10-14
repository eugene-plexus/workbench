/**
 * A page for Mermaid that is not the page (workbench-rich-content-roadmap.md
 * RC2). Mermaid draws into a DOM and measures what it drew; a worker has no
 * DOM, and only a worker can be stopped when a diagram takes too long (one
 * of 1,711 characters, a hundred nested boxes, took 20 s here). So the
 * diagram worker (diagramWorker.ts) draws into linkedom's DOM, and this file
 * gives it what linkedom lacks: the sizes of what was drawn.
 *
 * - Text is measured with the browser's own fonts (`measure`, an
 *   OffscreenCanvas in the worker), in the size, family and weight its
 *   element would have: its attributes, its style, and Mermaid's stylesheet
 *   for the diagram, read here because the worker has no CSS.
 * - Shapes are measured from their geometry, and a group from its children
 *   through their transforms.
 *
 * Then `inlineStyles` writes Mermaid's stylesheet into each element's own
 * style: the page's policy refuses a `<style>` element, and the stylesheet
 * is the diagram's colours and fonts.
 *
 * None of this is trusted by the page: what the worker sends is read again
 * through an allowlist (diagramMarkup.ts).
 */

import { parseHTML } from "linkedom";

/** What `measure` says of one line of text: CSS pixels. */
export type Metrics = { width: number; ascent: number; descent: number };

/** The font a line of text is drawn in. */
export type Font = { size: number; family: string; weight: string; style: string };

export type Measure = (text: string, font: Font) => Metrics;

type Box = { x: number; y: number; width: number; height: number };

// --- Mermaid's stylesheet ------------------------------------------------------------

type Declaration = { prop: string; value: string; important: boolean };
type Rule = { selector: string; declarations: Declaration[]; weight: number; order: number };
export type Sheet = { rules: Rule[]; vars: Map<string, string> };

/** `text` split at `separator`, but not inside quotes or parentheses. */
function splitTop(text: string, separator: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let quote = "";
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === quote) quote = "";
    } else if (c === '"' || c === "'") quote = c;
    else if (c === "(") depth++;
    else if (c === ")") depth--;
    else if (c === separator && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/** A selector's specificity, as one number (ids, then classes, then types). */
export function specificity(selector: string): number {
  const ids = selector.match(/#[\w-]+/g)?.length ?? 0;
  const classes = selector.match(/\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+/g)?.length ?? 0;
  const types =
    selector
      .replace(/#[\w-]+|\.[\w-]+|\[[^\]]*\]|:+[\w-]+(\([^)]*\))?/g, " ")
      .match(/[a-zA-Z][\w-]*/g)?.length ?? 0;
  return ids * 10_000 + classes * 100 + types;
}

function declarations(body: string): Declaration[] {
  const out: Declaration[] = [];
  for (const part of splitTop(body, ";")) {
    const colon = part.indexOf(":");
    if (colon < 0) continue;
    const prop = part.slice(0, colon).trim().toLowerCase();
    let value = part.slice(colon + 1).trim();
    const important = /!important\s*$/i.test(value);
    if (important) value = value.replace(/!important\s*$/i, "").trim();
    if (prop && value) out.push({ prop, value, important });
  }
  return out;
}

// States a still picture is never in, and parts it has no box for.
const DYNAMIC = /:(hover|focus|active|visited|before|after|first-line|first-letter|selection)/;

/**
 * Mermaid's stylesheet for a diagram: flat rules, as stylis writes them.
 * At-rules (`@keyframes`, `@media`) are skipped: a picture does not move.
 */
export function parseCss(css: string): Sheet {
  const rules: Rule[] = [];
  const vars = new Map<string, string>();
  let order = 0;
  let at = 0;
  while (at < css.length) {
    const open = css.indexOf("{", at);
    if (open < 0) break;
    const head = css.slice(at, open).trim();
    let depth = 1;
    let end = open + 1;
    while (end < css.length && depth) {
      if (css[end] === "{") depth++;
      else if (css[end] === "}") depth--;
      end++;
    }
    const body = css.slice(open + 1, end - 1);
    at = end;
    if (head.startsWith("@")) continue;
    const found = declarations(body).filter((d) => {
      if (!d.prop.startsWith("--")) return true;
      vars.set(d.prop, d.value);
      return false;
    });
    if (!found.length) continue;
    for (const part of splitTop(head, ",")) {
      const selector = part.trim();
      if (!selector || DYNAMIC.test(selector)) continue;
      rules.push({ selector, declarations: found, weight: specificity(selector), order: order++ });
    }
  }
  rules.sort((a, b) => a.weight - b.weight || a.order - b.order);
  return { rules, vars };
}

/** `var(--x, fallback)` replaced by the stylesheet's value for `--x`. */
export function resolveVars(value: string, vars: Map<string, string>, depth = 0): string {
  if (depth > 5 || !value.includes("var(")) return value;
  const replaced = value.replace(
    /var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)/g,
    (_, name: string, fallback?: string) => vars.get(name) ?? fallback ?? "",
  );
  return resolveVars(replaced, vars, depth + 1);
}

function styleAttribute(text: string | null): Map<string, Declaration> {
  const out = new Map<string, Declaration>();
  for (const d of declarations(text ?? "")) out.set(d.prop, d);
  return out;
}

function matches(el: Element, selector: string): boolean {
  try {
    return el.matches(selector);
  } catch {
    return false;
  }
}

// --- what an element looks like ------------------------------------------------------

const FONT_PROPS = new Set([
  "font-size",
  "font-family",
  "font-weight",
  "font-style",
  "text-anchor",
  "dominant-baseline",
  "alignment-baseline",
]);
const INHERITED = new Set([
  "font-size",
  "font-family",
  "font-weight",
  "font-style",
  "text-anchor",
  "dominant-baseline",
]);

const EMPTY_SHEET: Sheet & { fontRules: Rule[] } = { rules: [], vars: new Map(), fontRules: [] };

function tagOf(node: Node | null | undefined): string {
  return node && node.nodeType === 1 ? (node as Element).tagName.toLowerCase() : "";
}

function children(el: Element): Element[] {
  return [...el.childNodes].filter((c): c is Element => c.nodeType === 1);
}

/** A number from an attribute or style value: px, em (of `em`), pt or rem. */
function length(value: string | null | undefined, em: number): number | null {
  if (value == null) return null;
  const n = parseFloat(value);
  if (!Number.isFinite(n)) return null;
  if (/r?em\s*$/.test(value)) return n * (/rem\s*$/.test(value) ? 16 : em);
  if (/pt\s*$/.test(value)) return (n * 4) / 3;
  return n;
}

// --- shapes --------------------------------------------------------------------------

type Matrix = [number, number, number, number, number, number];
const IDENTITY: Matrix = [1, 0, 0, 1, 0, 0];

function multiply(m: Matrix, n: Matrix): Matrix {
  return [
    m[0] * n[0] + m[2] * n[1],
    m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3],
    m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4],
    m[1] * n[4] + m[3] * n[5] + m[5],
  ];
}

/** An SVG `transform` attribute as one matrix. */
export function parseTransform(text: string | null): Matrix {
  let m = IDENTITY;
  if (!text) return m;
  for (const [, kind, args] of text.matchAll(/(\w+)\s*\(([^)]*)\)/g)) {
    const a = (args ?? "")
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number);
    const [p = 0, q, r, s, t, u] = a;
    let n: Matrix = IDENTITY;
    if (kind === "translate") n = [1, 0, 0, 1, p, q ?? 0];
    else if (kind === "scale") n = [p, 0, 0, q ?? p, 0, 0];
    else if (kind === "rotate") {
      const rad = (p * Math.PI) / 180;
      const [cos, sin] = [Math.cos(rad), Math.sin(rad)];
      n = [cos, sin, -sin, cos, 0, 0];
      if (q !== undefined && r !== undefined) {
        n = multiply(multiply([1, 0, 0, 1, q, r], n), [1, 0, 0, 1, -q, -r]);
      }
    } else if (kind === "matrix" && a.length === 6) n = [p, q ?? 0, r ?? 0, s ?? 1, t ?? 0, u ?? 0];
    else if (kind === "skewX") n = [1, 0, Math.tan((p * Math.PI) / 180), 1, 0, 0];
    else if (kind === "skewY") n = [1, Math.tan((p * Math.PI) / 180), 0, 1, 0, 0];
    if (n.every(Number.isFinite)) m = multiply(m, n);
  }
  return m;
}

function around(points: Array<[number, number]>): Box | null {
  if (!points.length) return null;
  let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [x, y] of points) {
    x0 = Math.min(x0, x);
    y0 = Math.min(y0, y);
    x1 = Math.max(x1, x);
    y1 = Math.max(y1, y);
  }
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

/** The points of a path, its curves' control points with them: enough for its box. */
export function pathPoints(d: string): Array<[number, number]> {
  const points: Array<[number, number]> = [];
  const tokens = d.match(/[a-zA-Z]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) ?? [];
  let [x, y, sx, sy] = [0, 0, 0, 0];
  let i = 0;
  let command = "M";
  const n = (): number => Number(tokens[i++] ?? 0);
  while (i < tokens.length) {
    if (/[a-zA-Z]/.test(tokens[i] ?? "")) command = tokens[i++] ?? "M";
    const relative = command !== command.toUpperCase();
    const kind = command.toUpperCase();
    const [ox, oy] = relative ? [x, y] : [0, 0];
    if (kind === "Z") {
      [x, y] = [sx, sy];
      // A number after Z is no command's: pass it, or this loops for good.
      if (!/[a-zA-Z]/.test(tokens[i] ?? "Z")) i++;
      continue;
    }
    if (kind === "M" || kind === "L" || kind === "T") {
      [x, y] = [ox + n(), oy + n()];
      if (kind === "M") {
        [sx, sy] = [x, y];
        command = relative ? "l" : "L";
      }
    } else if (kind === "H") x = ox + n();
    else if (kind === "V") y = oy + n();
    else if (kind === "C") {
      points.push([ox + n(), oy + n()], [ox + n(), oy + n()]);
      [x, y] = [ox + n(), oy + n()];
    } else if (kind === "S" || kind === "Q") {
      points.push([ox + n(), oy + n()]);
      [x, y] = [ox + n(), oy + n()];
    } else if (kind === "A") {
      const [rx, ry] = [Math.abs(n()), Math.abs(n())];
      i += 3;
      const [fromX, fromY] = [x, y];
      [x, y] = [ox + n(), oy + n()];
      // The arc stays within its radii of the chord's middle.
      const [mx, my] = [(fromX + x) / 2, (fromY + y) / 2];
      points.push([mx - rx, my - ry], [mx + rx, my + ry]);
    } else {
      i++;
      continue;
    }
    points.push([x, y]);
  }
  return points;
}

// --- the stand-in -----------------------------------------------------------------------

/**
 * Makes linkedom's window and document the globals Mermaid draws into, and
 * gives its elements sizes measured with `measure`. Call once, before
 * Mermaid is loaded (its sanitizer looks for a window when it loads).
 */
export function install(measure: Measure): void {
  const { window, document } = parseHTML(
    "<!doctype html><html><head></head><body></body></html>",
  ) as unknown as { window: Window & Record<string, unknown>; document: Document };
  const scope = globalThis as unknown as Record<string, unknown>;
  scope.window = window;
  scope.document = document;
  for (const name of [
    "Element",
    "HTMLElement",
    "SVGElement",
    "Node",
    "DocumentFragment",
    "Text",
    "DOMParser",
    "NodeFilter",
    "HTMLTemplateElement",
  ]) {
    if (window[name] && !scope[name]) scope[name] = window[name];
  }
  // Mermaid checks its rules with a stylesheet; it only reads them back.
  scope.CSSStyleSheet = class {
    cssRules: Array<{ cssText: string }> = [];
    insertRule(rule: string, index = 0): number {
      this.cssRules.splice(index, 0, { cssText: rule });
      return index;
    }
  };
  // A shape drawn as a picture (`img:`) loads it to learn its size. Nothing
  // is fetched here: the picture has no size of its own, the shape takes the
  // one the diagram gave it, and the page drops the picture (diagramMarkup.ts).
  scope.Image = class {
    src = "";
    naturalWidth = 0;
    naturalHeight = 0;
    decode(): Promise<void> {
      return Promise.resolve();
    }
  };
  const computed = (el: Element) => (el as unknown as { style: unknown }).style;
  window.getComputedStyle ??= computed as unknown as typeof window.getComputedStyle;
  scope.getComputedStyle = window.getComputedStyle;

  // The stylesheet of the drawing an element is in: its svg's own <style>.
  let lastCss = "";
  let lastSheet = EMPTY_SHEET;
  function sheetOf(el: Element): typeof EMPTY_SHEET {
    let svg: Node | null = el;
    while (svg && tagOf(svg) !== "svg") svg = svg.parentNode;
    const style = svg ? children(svg as Element).find((c) => tagOf(c) === "style") : undefined;
    const css = style?.textContent ?? "";
    if (!css) return EMPTY_SHEET;
    if (css !== lastCss) {
      const sheet = parseCss(css);
      lastCss = css;
      lastSheet = {
        ...sheet,
        fontRules: sheet.rules.filter((r) => r.declarations.some((d) => FONT_PROPS.has(d.prop))),
      };
    }
    return lastSheet;
  }

  /** A property as set on the element itself: stylesheet, style, attribute. */
  function own(el: Element, prop: string, sheet: typeof EMPTY_SHEET): string | null {
    let important: string | null = null;
    let ruled: string | null = null;
    for (const rule of sheet.fontRules) {
      for (const d of rule.declarations) {
        if (d.prop !== prop || !matches(el, rule.selector)) continue;
        if (d.important) important = d.value;
        else ruled = d.value;
      }
    }
    // CSS's order: an important own style, an important rule, the own style,
    // a rule, then the attribute.
    const inline = styleAttribute(el.getAttribute("style")).get(prop);
    const value = inline?.important
      ? inline.value
      : (important ?? inline?.value ?? ruled ?? el.getAttribute(prop));
    return value == null ? null : resolveVars(value, sheet.vars);
  }

  function property(el: Element, prop: string, sheet: typeof EMPTY_SHEET): string | null {
    for (let n: Node | null = el; n && n.nodeType === 1; n = n.parentNode) {
      const value = own(n as Element, prop, sheet);
      if (value && value !== "inherit") return value;
      if (!INHERITED.has(prop)) return null;
    }
    return null;
  }

  /** The font size in pixels; a size in em is of the parent's size. */
  function fontSize(el: Element, sheet: typeof EMPTY_SHEET): number {
    const chain: Element[] = [];
    for (let n: Node | null = el; n && n.nodeType === 1; n = n.parentNode) chain.push(n as Element);
    let size = 16;
    for (const n of chain.reverse()) {
      const px = length(own(n, "font-size", sheet), size);
      if (px !== null && px > 0) size = px;
    }
    return size;
  }

  type Style = Font & { anchor: string; baseline: string };
  function styleOf(el: Element): Style {
    const sheet = sheetOf(el);
    return {
      size: fontSize(el, sheet),
      family: property(el, "font-family", sheet) || "sans-serif",
      weight: property(el, "font-weight", sheet) || "normal",
      style: property(el, "font-style", sheet) || "normal",
      anchor: property(el, "text-anchor", sheet) || "start",
      baseline:
        property(el, "dominant-baseline", sheet) || own(el, "alignment-baseline", sheet) || "",
    };
  }

  function number(el: Element, name: string, fallback = 0, em = 16): number {
    return length(el.getAttribute(name), em) ?? fallback;
  }

  type Line = { text: string; x: number; y: number; style: Style };

  /** The lines of a <text>: each <tspan> child is one, at its own x and y. */
  function linesOf(el: Element): Line[] {
    const style = styleOf(el);
    const x = number(el, "x", 0, style.size) + number(el, "dx", 0, style.size);
    const y = number(el, "y", 0, style.size) + number(el, "dy", 0, style.size);
    const spans = children(el).filter((c) => tagOf(c) === "tspan");
    if (tagOf(el) !== "text" || !spans.length) return [{ text: el.textContent ?? "", x, y, style }];
    const lines: Line[] = [];
    const loose = [...el.childNodes]
      .filter((c) => c.nodeType === 3)
      .map((c) => c.textContent)
      .join("");
    if (loose.trim()) lines.push({ text: loose, x, y, style });
    let [atX, atY] = [x, y];
    for (const span of spans) {
      const s = styleOf(span);
      if (span.hasAttribute("x")) atX = number(span, "x", atX, s.size);
      atX += number(span, "dx", 0, s.size);
      if (span.hasAttribute("y")) atY = number(span, "y", atY, s.size);
      atY += number(span, "dy", 0, s.size);
      lines.push({ text: span.textContent ?? "", x: atX, y: atY, style: s });
    }
    return lines;
  }

  function textBox(el: Element): Box {
    const points: Array<[number, number]> = [];
    for (const line of linesOf(el)) {
      if (!line.text) continue;
      const m = measure(line.text, line.style);
      let left = line.x;
      if (line.style.anchor === "middle") left -= m.width / 2;
      else if (line.style.anchor === "end") left -= m.width;
      const high = m.ascent + m.descent;
      let top = line.y - m.ascent;
      const base = line.style.baseline;
      if (base === "middle" || base === "central") top = line.y - high / 2;
      else if (base === "hanging" || base === "text-before-edge") top = line.y;
      else if (base === "text-after-edge" || base === "ideographic") top = line.y - high;
      points.push([left, top], [left + m.width, top + high]);
    }
    return around(points) ?? { x: number(el, "x"), y: number(el, "y"), width: 0, height: 0 };
  }

  const NO_BOX = new Set([
    "style",
    "title",
    "desc",
    "defs",
    "marker",
    "clippath",
    "mask",
    "lineargradient",
    "radialgradient",
    "filter",
    "pattern",
    "symbol",
    "metadata",
  ]);

  function box(el: Element): Box | null {
    const tag = tagOf(el);
    if (NO_BOX.has(tag)) return null;
    switch (tag) {
      case "rect":
      case "image":
      case "use":
      case "foreignobject":
        return {
          x: number(el, "x"),
          y: number(el, "y"),
          width: number(el, "width"),
          height: number(el, "height"),
        };
      case "circle": {
        const r = number(el, "r");
        return { x: number(el, "cx") - r, y: number(el, "cy") - r, width: 2 * r, height: 2 * r };
      }
      case "ellipse": {
        const [rx, ry] = [number(el, "rx"), number(el, "ry")];
        return {
          x: number(el, "cx") - rx,
          y: number(el, "cy") - ry,
          width: 2 * rx,
          height: 2 * ry,
        };
      }
      case "line":
        return around([
          [number(el, "x1"), number(el, "y1")],
          [number(el, "x2"), number(el, "y2")],
        ]);
      case "polygon":
      case "polyline": {
        const v = (el.getAttribute("points") ?? "")
          .trim()
          .split(/[\s,]+/)
          .map(Number);
        const points: Array<[number, number]> = [];
        for (let i = 0; i + 1 < v.length; i += 2) points.push([v[i] ?? 0, v[i + 1] ?? 0]);
        return around(points.filter((p) => p.every(Number.isFinite)));
      }
      case "path":
        return around(pathPoints(el.getAttribute("d") ?? ""));
      case "text":
      case "tspan":
        return textBox(el);
      default: {
        const points: Array<[number, number]> = [];
        for (const child of children(el)) {
          const b = box(child);
          if (!b) continue;
          const m = parseTransform(child.getAttribute("transform"));
          for (const [x, y] of [
            [b.x, b.y],
            [b.x + b.width, b.y],
            [b.x, b.y + b.height],
            [b.x + b.width, b.y + b.height],
          ] as Array<[number, number]>) {
            points.push([m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]]);
          }
        }
        return around(points);
      }
    }
  }

  const empty = (): Box => ({ x: 0, y: 0, width: 0, height: 0 });
  const proto = (window.Element as unknown as { prototype: Record<string, unknown> }).prototype;
  proto.getBBox = function (this: Element) {
    return box(this) ?? empty();
  };
  proto.getComputedTextLength = function (this: Element) {
    return measure(this.textContent ?? "", styleOf(this)).width;
  };
  proto.getBoundingClientRect = function (this: Element) {
    const b = box(this) ?? empty();
    return { ...b, left: b.x, top: b.y, right: b.x + b.width, bottom: b.y + b.height };
  };
  proto.getTotalLength = function (this: Element) {
    const p = pathPoints(this.getAttribute("d") ?? "");
    let total = 0;
    for (let i = 1; i < p.length; i++) {
      const [ax, ay] = p[i - 1] ?? [0, 0];
      const [bx, by] = p[i] ?? [0, 0];
      total += Math.hypot(bx - ax, by - ay);
    }
    return total;
  };
  proto.getPointAtLength = () => ({ x: 0, y: 0 });
  proto.getScreenCTM = () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
}

// --- after drawing ------------------------------------------------------------------------

/**
 * Mermaid's `<style>` written into each element's own style, then removed:
 * a declaration from the stylesheet gives way to the element's own style
 * unless it is `!important` and that is not.
 */
export function inlineStyles(svg: Element): void {
  const style = children(svg).find((c) => tagOf(c) === "style");
  if (!style) return;
  const { rules, vars } = parseCss(style.textContent ?? "");
  style.remove();
  const root = svg.parentNode as unknown as ParentNode | null;
  if (!root) return;
  const applied = new Map<Element, Map<string, Declaration>>();
  for (const rule of rules) {
    let found: Iterable<Element>;
    try {
      found = root.querySelectorAll(rule.selector);
    } catch {
      continue;
    }
    for (const el of found) {
      let set = applied.get(el);
      if (!set) applied.set(el, (set = new Map()));
      for (const d of rule.declarations) {
        if (set.get(d.prop)?.important && !d.important) continue;
        set.set(d.prop, { ...d, value: resolveVars(d.value, vars) });
      }
    }
  }
  for (const [el, set] of applied) {
    for (const [prop, d] of styleAttribute(el.getAttribute("style"))) {
      const ruled = set.get(prop);
      if (!ruled?.important || d.important)
        set.set(prop, { ...d, value: resolveVars(d.value, vars) });
    }
    el.setAttribute("style", [...set.values()].map((d) => `${d.prop}:${d.value}`).join(";"));
  }
}

/** A drawing as plain data for the page: [tag, attributes, children]. */
export type Tree = [string, Record<string, string>, Array<Tree | string>];

/**
 * The drawing as a tree the page reads, at most `limit` elements (null past
 * it). Comments go; text stays text.
 */
export function toTree(el: Element, limit: number): Tree | null {
  let count = 0;
  const walk = (node: Element): Tree | null => {
    if (++count > limit) return null;
    const attrs: Record<string, string> = {};
    for (const a of [...node.attributes]) attrs[a.name] = a.value;
    const kids: Array<Tree | string> = [];
    for (const child of [...node.childNodes]) {
      if (child.nodeType === 3) kids.push(child.textContent ?? "");
      else if (child.nodeType === 1) {
        const t = walk(child as Element);
        if (!t) return null;
        kids.push(t);
      }
    }
    // SVG's names keep their case (linearGradient, viewBox).
    return [node.tagName, attrs, kids];
  };
  return walk(el);
}
