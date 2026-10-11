/**
 * The font gate (workbench-reading.md 13.2): a page may not ask for a face it
 * does not load. Read only by fonts.test.ts; the page never imports it.
 *
 * The page loads IBM Plex Sans as one variable font, normal and italic, so
 * any weight from 100 to 700 and a real italic are on hand; IBM Plex Mono has
 * no variable build and is loaded at 400, upright. Before this gate, 500 drew
 * as 400, bold as 600, and every italic was the roman slanted by the browser.
 */

const WEIGHT_CLASSES: Record<string, number> = {
  "font-thin": 100,
  "font-extralight": 200,
  "font-light": 300,
  "font-normal": 400,
  "font-medium": 500,
  "font-semibold": 600,
  "font-bold": 700,
  "font-extrabold": 800,
  "font-black": 900,
};

/** What `main.tsx` loads: the weight range of Sans, and whether its italic is there. */
export function loadedFaces(main: string): {
  range: readonly [number, number] | null;
  italic: boolean;
} {
  const normal = /@fontsource-variable\/ibm-plex-sans\/wght\.css/.test(main);
  const italic = /@fontsource-variable\/ibm-plex-sans\/wght-italic\.css/.test(main);
  return { range: normal ? [100, 700] : null, italic };
}

/** Every string literal in some source: where a class list lives. */
function literals(source: string): string[] {
  return source.match(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g) ?? [];
}

/**
 * What is wrong with the fonts, one sentence each: `main` is main.tsx, `markup`
 * the source of every component, `css` the stylesheet.
 */
export function fontProblems(main: string, markup: string, css: string): string[] {
  const { range, italic } = loadedFaces(main);
  const asked = new Set<number>([400]);
  for (const [name, weight] of Object.entries(WEIGHT_CLASSES)) {
    if (new RegExp(`(?<![\\w-])${name}(?![\\w-])`).test(markup)) asked.add(weight);
  }
  for (const m of markup.matchAll(/(?<![\w-])font-\[(\d{3})\]/g)) asked.add(Number(m[1]));
  for (const m of css.matchAll(/font-weight:\s*(\d{3})\b/g)) asked.add(Number(m[1]));
  const problems: string[] = [];
  for (const weight of [...asked].sort((a, b) => a - b)) {
    if (!range || weight < range[0] || weight > range[1]) {
      problems.push(`weight ${weight} is asked for and not loaded`);
    }
  }
  if (!italic) problems.push("italic is needed (em, and bold inside it) and not loaded");
  if (!/font-synthesis:\s*none/.test(css)) {
    problems.push("font-synthesis is not off, so a missing face would be faked");
  }
  // Mono is one face: a class list that makes code bold would ask for 600 of it.
  for (const text of literals(markup)) {
    if (!/(?<![\w-])font-mono(?![\w-])/.test(text)) continue;
    for (const [name, weight] of Object.entries(WEIGHT_CLASSES)) {
      if (weight !== 400 && new RegExp(`(?<![\\w-])${name}(?![\\w-])`).test(text)) {
        problems.push(
          `${name} with font-mono asks for a weight of Plex Mono, which is loaded at 400 only`,
        );
      }
    }
  }
  return problems;
}
