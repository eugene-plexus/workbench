/**
 * The token gate (workbench-reading.md 13.2). jsdom lays nothing out and
 * resolves no var(), so this reads the stylesheet as text, takes the colours
 * from it and does the arithmetic of 3.2 (WCAG 2 contrast); it also checks
 * that every name a component can use is a name the stylesheet gives.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";

const here = import.meta.dirname;
const css = readFileSync(join(here, "styles.css"), "utf8");

// --- reading the stylesheet --------------------------------------------------

/** The text between the braces that open at `open`, nested braces counted. */
function braces(text: string, open: number): string {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "{") depth++;
    else if (text[i] === "}" && --depth === 0) return text.slice(open + 1, i);
  }
  throw new Error("styles.css: a block is never closed");
}

function block(header: string): string {
  const at = css.indexOf(header);
  if (at < 0) throw new Error(`styles.css: no "${header}"`);
  return braces(css, css.indexOf("{", at));
}

type Palette = Record<string, string>;

/** `--name: #rrggbb` of a block, then each `--name: var(--other)` resolved against `base`. */
function palette(text: string, base: Palette = {}): Palette {
  const out: Palette = { ...base };
  for (const m of text.matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})\b/gi))
    out[m[1]!] = m[2]!.toLowerCase();
  return out;
}

const DARK_BLOCK = block("\n:root {");
const LIGHT_BLOCK = block("@media (prefers-color-scheme: light) {");
const dark = palette(DARK_BLOCK);
const light = palette(LIGHT_BLOCK, dark);
const darkMore = palette(
  block("@media (prefers-contrast: more) and (prefers-color-scheme: dark) {"),
  dark,
);
const lightMore = palette(
  block("@media (prefers-contrast: more) and (prefers-color-scheme: light) {"),
  light,
);

/** Tokens defined as another token (`--hl-keyword: var(--link)`) follow the scheme they are read in. */
const ALIASES = [...DARK_BLOCK.matchAll(/--([\w-]+):\s*var\(--([\w-]+)\)\s*;/g)].map(
  (m) => [m[1]!, m[2]!] as const,
);
function resolved(scheme: Palette): Palette {
  const out = { ...scheme };
  for (const [name, target] of ALIASES) if (out[target]) out[name] = out[target];
  return out;
}
const SCHEMES = {
  dark: resolved(dark),
  light: resolved(light),
  "dark, increased contrast": resolved(darkMore),
  "light, increased contrast": resolved(lightMore),
};

// --- WCAG 2 -------------------------------------------------------------------

const rgb = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const linear = (c: number) => {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = (c: number[]) =>
  0.2126 * linear(c[0]!) + 0.7152 * linear(c[1]!) + 0.0722 * linear(c[2]!);
function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}
/** `top` over `bottom` at `alpha`: how a tint sits on the page. */
const over = (top: number[], bottom: number[], alpha: number) =>
  top.map((c, i) => c * alpha + bottom[i]! * (1 - alpha));

function ratio(scheme: Palette, fg: string, bg: string): number {
  const [f, b] = [scheme[fg], scheme[bg]];
  if (!f || !b) throw new Error(`no token --${f ? bg : fg}`);
  return contrast(rgb(f), rgb(b));
}

// --- the pairs of 3.2: [text or mark, surface, minimum] -----------------------

const PAIRS: [string, string, number][] = [
  // text, brightest first (4.5; the body and its strong tier are held to 7)
  ["foreground-strong", "background", 7],
  ["foreground", "panel-soft", 7],
  ["muted", "panel-hover", 4.5],
  ["faint", "panel-soft", 4.5],
  ["heading", "panel-soft", 4.5],
  ["link", "panel-soft", 4.5],
  ["link-hover", "panel-soft", 4.5],
  // inline code's own fill: the warm text on it is held to 7 (the mark's colour is not)
  ["accent-right-text", "panel-soft", 7],
  // the person's bubble
  ["foreground", "tint", 4.5],
  ["muted", "tint", 4.5],
  ["link", "tint", 4.5],
  // the text on a primary button, resting, hovered and pressed
  ["on-accent-left", "accent-left", 4.5],
  ["on-accent-left", "accent-hover", 4.5],
  ["on-accent-left", "accent-active", 4.5],
  // code, on the code body
  ["hl-keyword", "panel-soft", 4.5],
  ["hl-string", "panel-soft", 4.5],
  ["hl-number", "panel-soft", 4.5],
  ["hl-title", "panel-soft", 4.5],
  ["hl-attr", "panel-soft", 4.5],
  ["hl-comment", "panel-soft", 4.5],
  ["hl-punct", "panel-soft", 4.5],
  // non-text: what finds a control, the current page, a mark, a scrollbar (3)
  ["border-control", "panel-hover", 3],
  ["accent-left", "panel-hover", 3],
  ["accent-left", "tint", 3],
  ["accent-right", "background", 3],
  ["accent-right", "panel-hover", 3],
  ["danger-edge", "panel-soft", 3],
  ["scrollbar-thumb", "panel-soft", 3],
];

describe.each(["dark", "light"] as const)("%s: the colours of 3.2", (name) => {
  it.each(PAIRS)(`--%s on --%s is at least %d:1`, (fg, bg, min) => {
    expect(ratio(SCHEMES[name], fg, bg)).toBeGreaterThanOrEqual(min);
  });
});

describe("increased contrast (9): every text tier to 7:1 on every surface, every edge to 5:1 on the page, a panel and a soft fill", () => {
  const TEXT = [
    "foreground",
    "foreground-strong",
    "muted",
    "faint",
    "heading",
    "link",
    "link-hover",
    "accent-right-text",
    "status-warn-fg",
    "status-error-fg",
    "status-ok-fg",
    "status-info-fg",
  ];
  const SURFACES = ["background", "panel", "panel-soft", "panel-hover"];
  const EDGES = ["border", "border-control", "rule"];
  const EDGE_SURFACES = ["background", "panel", "panel-soft"];
  const cross = (tokens: string[], surfaces: string[]) =>
    tokens.flatMap((token) => surfaces.map((surface) => [token, surface] as const));
  for (const name of ["dark, increased contrast", "light, increased contrast"] as const) {
    it.each(cross(TEXT, SURFACES))(`${name}: --%s on --%s is at least 7:1`, (token, surface) => {
      expect(ratio(SCHEMES[name], token, surface)).toBeGreaterThanOrEqual(7);
    });
    it.each(cross(EDGES, EDGE_SURFACES))(
      `${name}: --%s on --%s is at least 5:1`,
      (token, surface) => {
        expect(ratio(SCHEMES[name], token, surface)).toBeGreaterThanOrEqual(5);
      },
    );
    it.each(cross(["foreground", "muted", "link"], ["tint"]))(
      `${name}: --%s on the person's bubble (--%s) is at least 7:1`,
      (token, surface) => {
        expect(ratio(SCHEMES[name], token, surface)).toBeGreaterThanOrEqual(7);
      },
    );
    it.each(["accent-left", "accent-hover", "accent-active"])(
      `${name}: the primary button's text on --%s is at least 7:1`,
      (fill) => {
        expect(ratio(SCHEMES[name], "on-accent-left", fill)).toBeGreaterThanOrEqual(7);
      },
    );
  }
});

describe("a primary button's states go the same way in every scheme", () => {
  // Dark lightens when hovered and darkens when pressed; light darkens at each step.
  const l = (scheme: Palette, token: string) => luminance(rgb(scheme[token]!));
  it.each([
    ["dark", +1],
    ["dark, increased contrast", +1],
    ["light", -1],
    ["light, increased contrast", -1],
  ] as const)("%s", (name, direction) => {
    const scheme = SCHEMES[name];
    expect(Math.sign(l(scheme, "accent-hover") - l(scheme, "accent-left"))).toBe(direction);
    // Pressed is darker than hovered, in every scheme.
    expect(l(scheme, "accent-active")).toBeLessThan(l(scheme, "accent-hover"));
    if (direction < 0) {
      expect(l(scheme, "accent-active")).toBeLessThan(l(scheme, "accent-left"));
    }
  });
});

describe("an alert's label, on the tint of its bar (6.4)", () => {
  // [the bar and tint, the label]: a mark and the text beside it
  const ALERTS: [string, string, string][] = [
    ["note", "status-info-fg", "status-info-fg"],
    ["tip", "status-ok-fg", "status-ok-fg"],
    ["important", "accent-right", "accent-right-text"],
    ["warning", "status-warn-fg", "status-warn-fg"],
    ["caution", "status-error-fg", "status-error-fg"],
  ];
  const cases = [
    ["dark", 4.5],
    ["light", 4.5],
    ["dark, increased contrast", 7],
    ["light, increased contrast", 7],
  ] as const;
  for (const [name, minimum] of cases) {
    it.each(ALERTS)(
      `${name}: %s (bar --%s, label --%s) is at least ${minimum}:1`,
      (_kind, bar, label) => {
        const scheme = SCHEMES[name];
        for (const surface of ["background", "panel"]) {
          const tint = over(rgb(scheme[bar]!), rgb(scheme[surface]!), 0.09);
          expect(contrast(rgb(scheme[label]!), tint)).toBeGreaterThanOrEqual(minimum);
        }
      },
    );
  }
  it.each(["dark", "light"] as const)("%s: a bar is a mark, 3:1 on the page", (name) => {
    for (const [, bar] of ALERTS) {
      expect(ratio(SCHEMES[name], bar, "background")).toBeGreaterThanOrEqual(3);
    }
  });
});

it("the stylesheet's own comparison of two values can fail", () => {
  // A check that cannot fail does not count: the arithmetic must tell black on black from black on white.
  expect(contrast(rgb("#000000"), rgb("#ffffff"))).toBeCloseTo(21, 5);
  expect(contrast(rgb("#3a4048"), rgb("#141619"))).toBeLessThan(3);
});

// --- the names a component can use --------------------------------------------

const ALL_TOKENS = [...DARK_BLOCK.matchAll(/--([\w-]+):/g)].map((m) => m[1]!);
const THEME = block("@theme inline {");
const COLOUR_ALIASES = [...THEME.matchAll(/--color-([\w-]+):\s*var\(--([\w-]+)\)/g)].map(
  (m) => [m[1]!, m[2]!] as const,
);

describe("every alias is a name the tokens give", () => {
  it.each(COLOUR_ALIASES)("--color-%s points at --%s, which exists", (_alias, token) => {
    expect(ALL_TOKENS).toContain(token);
  });
});

describe("no name the first stylesheet gave is lost (components not yet converted use them)", () => {
  const TOKENS = [
    "background",
    "foreground",
    "panel",
    "panel-soft",
    "panel-hover",
    "border",
    "border-hover",
    "accent-left",
    "accent-right",
    "on-accent-left",
    "muted",
    "status-warn-fg",
    "status-warn-bg",
    "status-warn-border",
    "status-error-fg",
    "status-error-bg",
    "status-error-border",
    "radius",
    "font-ui",
    "font-code",
  ];
  const UTILITY_COLOURS = [
    "bg",
    "fg",
    "panel",
    "soft",
    "hover",
    "line",
    "line-strong",
    "muted",
    "accent",
    "apricot",
    "on-accent",
    "warn",
    "warn-bg",
    "warn-line",
    "error",
    "error-bg",
    "error-line",
  ];
  it.each(TOKENS)("--%s", (token) => expect(ALL_TOKENS).toContain(token));
  it.each(UTILITY_COLOURS)("a colour utility named %s", (name) =>
    expect(COLOUR_ALIASES.map(([alias]) => alias)).toContain(name),
  );
  it.each(["font-sans", "font-mono", "radius-plexus"])("--%s", (name) =>
    expect(THEME).toContain(`--${name}:`),
  );
});

// What is not a colour, among the utilities that share a prefix with colours.
const NOT_COLOUR: Record<string, RegExp> = {
  text: /^(xs|sm|base|lg|xl|[2-9]xl|read|left|center|right|justify|start|end|wrap|nowrap|balance|pretty|ellipsis|clip)$/,
  border:
    /^(([trblxyse](-(0|2|4|8))?)|0|2|4|8|solid|dashed|dotted|double|hidden|none|collapse|separate|spacing-.+)$/,
  bg: /^(none|auto|cover|contain|center|top|bottom|left|right|(left|right)-(top|bottom)|(top|bottom)-(left|right)|no-repeat|repeat(-.+)?|fixed|local|scroll|(clip|origin|blend|linear|gradient|radial|conic|size|position)-.+)$/,
  ring: /^(0|1|2|4|8|inset|offset-.+)$/,
  divide: /^([xy](-(0|2|4|8|reverse))?|solid|dashed|dotted|double|none)$/,
  outline: /^(none|hidden|solid|dashed|dotted|double|0|1|2|4|8|offset-.+)$/,
  decoration: /^(solid|double|dotted|dashed|wavy|auto|from-font|0|1|2|4|8)$/,
  fill: /^none$/,
  stroke: /^(none|0|1|2)$/,
  placeholder: /^$/,
  caret: /^$/,
};
// Values every colour utility takes beside the palette's own.
const SPECIAL = new Set(["transparent", "current", "inherit"]);
// A word in a string that only looks like a utility (prose, an id); add it here, with its file.
const PLAIN_WORDS = new Set<string>([]);

/** The utility of a class token: no variants, no `!`, no opacity; null when it is arbitrary. */
function utilityOf(token: string): string | null {
  let depth = 0;
  let start = 0;
  for (let i = 0; i < token.length; i++) {
    const c = token[i];
    if (c === "[" || c === "(") depth++;
    else if (c === "]" || c === ")") depth--;
    else if (c === ":" && depth === 0) start = i + 1;
  }
  const utility = token.slice(start).replace(/^!|!$/g, "");
  return utility.includes("[") ? null : utility.replace(/\/\d+$/, "");
}

function sources(dir: string): string[] {
  return readdirSync(dir, { recursive: true, encoding: "utf8" })
    .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))
    .map((f) => join(dir, f));
}

/** Every string a component writes: class lists live in them (className, a conditional, a constant). */
function stringsIn(file: string): string[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, "utf8"),
    ts.ScriptTarget.ES2022,
    true,
    ts.ScriptKind.TSX,
  );
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      found.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

/** The colour utilities in some strings that no alias answers. */
function unresolvedColours(strings: string[], aliases: string[]): string[] {
  const bad = new Set<string>();
  for (const text of strings) {
    for (const token of text.split(/\s+/)) {
      const utility = utilityOf(token);
      const m =
        utility &&
        /^(bg|text|border|ring|divide|outline|fill|stroke|decoration|placeholder|caret)-(.*)$/.exec(
          utility,
        );
      if (!m || PLAIN_WORDS.has(token)) continue;
      const [, prefix, name] = m;
      if (aliases.includes(name!) || SPECIAL.has(name!) || NOT_COLOUR[prefix!]!.test(name!))
        continue;
      bad.add(token);
    }
  }
  return [...bad];
}

describe("every colour utility a component writes is a name the stylesheet gives", () => {
  const aliases = COLOUR_ALIASES.map(([alias]) => alias);

  it("holds for every component", () => {
    const problems: string[] = [];
    for (const file of sources(here)) {
      for (const token of unresolvedColours(stringsIn(file), aliases)) {
        problems.push(
          `${file.slice(here.length + 1)}: "${token}" is not a colour the stylesheet names (a utility like bg-surface with no alias draws nothing); name it in @theme inline, or list it in PLAIN_WORDS if it is not a class`,
        );
      }
    }
    expect(problems).toEqual([]);
  });

  it("can fail: it names bg-surface when the alias is gone, and an off-palette colour", () => {
    const without = aliases.filter((alias) => alias !== "surface");
    expect(unresolvedColours(["rounded-plexus bg-surface p-2"], without)).toEqual(["bg-surface"]);
    expect(unresolvedColours(["rounded-plexus bg-surface p-2"], aliases)).toEqual([]);
    expect(unresolvedColours(["hover:bg-red-500 md:text-white"], aliases)).toEqual([
      "hover:bg-red-500",
      "md:text-white",
    ]);
  });

  it("knows a variant, an opacity and an arbitrary value from a name", () => {
    expect(
      unresolvedColours(
        [
          "hover:bg-hover text-fg/70 border-[#fff] text-[0.8125rem] max-sm:text-sm [&:not(:first-child)]:border-t group-open/r:text-muted text-balance border-2 ring-inset",
        ],
        aliases,
      ),
    ).toEqual([]);
  });
});
