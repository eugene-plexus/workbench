/**
 * An SVG artifact as a picture (workbench-rich-content-roadmap.md RC5), by
 * RC2's rules for a diagram (lib/diagramMarkup.ts): read as XML into a tree,
 * every element, attribute and style through the same allowlist, written out
 * again and shown as an `<img>`, which runs nothing and fetches nothing.
 *
 * The XML is read by the browser's own parser into a document that is never
 * shown: nothing in it runs or loads. A drawing with what the allowlist does
 * not hold (a script, a stylesheet, an outside picture's address) is not
 * shown, and the reason is said; its source stays a click away.
 */

import { MAX_SVG, Refused, toPicture, type Picture } from "./diagramMarkup";

export { Refused };

const SVG_NS = "http://www.w3.org/2000/svg";

type Tree = [string, Record<string, string>, Array<Tree | string>];

function treeOf(element: Element, depth: number): Tree {
  if (depth > 200) throw new Refused("its drawing is nested too deeply to show here");
  const attributes: Record<string, string> = {};
  for (const attribute of element.attributes) attributes[attribute.name] = attribute.value;
  const children: Array<Tree | string> = [];
  for (const child of element.childNodes) {
    if (child.nodeType === 1) children.push(treeOf(child as Element, depth + 1));
    // Text and CDATA are words; a comment or an instruction is nothing.
    else if (child.nodeType === 3 || child.nodeType === 4) children.push(child.nodeValue ?? "");
  }
  return [element.localName, attributes, children];
}

/** A length in plain numbers or pixels, else NaN. */
function pixels(value: string | undefined): number {
  const found = /^\s*([0-9]*\.?[0-9]+)\s*(px)?\s*$/.exec(value ?? "");
  return found ? Number(found[1]) : NaN;
}

/** The SVG an artifact holds as a picture on `background`; `Refused`, saying why, when it is not one. */
export function readSvg(source: string, background: string): Picture {
  if (source.length > MAX_SVG) throw new Refused("it is too large to show here");
  const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
  if (parsed.getElementsByTagName("parsererror").length) {
    throw new Refused("it is not well-formed SVG");
  }
  const root = parsed.documentElement;
  if (root.localName !== "svg" || root.namespaceURI !== SVG_NS) {
    throw new Refused("it is not an SVG drawing");
  }
  const tree = treeOf(root, 0);
  // A drawing sized by its width and height alone is the box they name.
  if (!tree[1].viewBox) {
    const width = pixels(tree[1].width);
    const height = pixels(tree[1].height);
    if (width > 0 && height > 0) tree[1].viewBox = `0 0 ${width} ${height}`;
  }
  return toPicture(tree, "Picture", background);
}
