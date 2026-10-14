/**
 * KaTeX's faces ship as woff2 only (katexFonts.ts, applied by vite.config.ts):
 * read against the stylesheet the build really uses.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { isKatexCss, katexWoff2Only } from "./katexFonts";

const require = createRequire(import.meta.url);
const path = require.resolve("katex/dist/katex.min.css");
const css = readFileSync(path, "utf8");

it("names each face once, as woff2", () => {
  const out = katexWoff2Only(css);
  const faces = out.match(/@font-face\s*{[^}]*}/g) ?? [];
  expect(faces).toHaveLength(20);
  for (const face of faces) {
    const urls = face.match(/url\([^)]*\)/g) ?? [];
    expect(urls).toHaveLength(1);
    expect(urls[0]).toMatch(/\.woff2\)$/);
  }
  expect(out).not.toMatch(/\.(woff|ttf)\)/);
});

it("changes nothing but the font sources", () => {
  const strip = (s: string) => s.replace(/@font-face\s*{[^}]*}/g, "");
  expect(strip(katexWoff2Only(css))).toBe(strip(css));
});

it("knows KaTeX's stylesheet, and only it", () => {
  expect(isKatexCss(path)).toBe(true);
  expect(isKatexCss("/x/node_modules/katex/dist/katex.css?direct")).toBe(true);
  expect(isKatexCss("C:\\x\\node_modules\\katex\\dist\\katex.min.css")).toBe(true);
  expect(isKatexCss("/x/src/styles.css")).toBe(false);
  expect(isKatexCss("/x/node_modules/katex/dist/katex-swap.min.css")).toBe(false);
});
