/**
 * The font gate (workbench-reading.md 13.2): the page asks for no weight or
 * italic it does not load, and a missing face is never faked. The gate's own
 * rules are in fontGate.ts; this reads the real files and then shows each rule
 * can fail, because a check that cannot fail does not count.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { fontProblems } from "./fontGate";

const here = import.meta.dirname;
const main = readFileSync(join(here, "main.tsx"), "utf8");
const css = readFileSync(join(here, "styles.css"), "utf8");
const markup = readdirSync(here, { recursive: true, encoding: "utf8" })
  .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))
  .map((f) => readFileSync(join(here, f), "utf8"))
  .join("\n");

describe("the page's fonts", () => {
  it("ask for no face that is not loaded", () => {
    expect(fontProblems(main, markup, css)).toEqual([]);
  });

  it("load Sans as one variable font, upright and italic, and Mono upright", () => {
    expect(main).toContain('import "@fontsource-variable/ibm-plex-sans/wght.css";');
    expect(main).toContain('import "@fontsource-variable/ibm-plex-sans/wght-italic.css";');
    expect(main).toContain('import "@fontsource/ibm-plex-mono/400.css";');
    // The first family named is the one that is loaded.
    expect(css).toMatch(/--font-ui:\s*"IBM Plex Sans Variable"/);
  });
});

describe("the gate can fail", () => {
  it("names the italic when its file is not imported", () => {
    const without = main.replace(
      /^import "@fontsource-variable\/ibm-plex-sans\/wght-italic.css";\n/m,
      "",
    );
    expect(without).not.toBe(main);
    expect(fontProblems(without, markup, css)).toEqual([
      "italic is needed (em, and bold inside it) and not loaded",
    ]);
  });

  it("names every weight when the variable file is not imported (today's static faces)", () => {
    const staticFaces = [
      'import "@fontsource/ibm-plex-sans/400.css";',
      'import "@fontsource/ibm-plex-sans/600.css";',
      'import "@fontsource/ibm-plex-mono/400.css";',
    ].join("\n");
    const problems = fontProblems(staticFaces, "font-medium font-semibold", css);
    expect(problems).toContain("weight 400 is asked for and not loaded");
    expect(problems).toContain("weight 500 is asked for and not loaded");
    expect(problems).toContain("weight 600 is asked for and not loaded");
    expect(problems).toContain("italic is needed (em, and bold inside it) and not loaded");
  });

  it("names a weight the variable font does not have, from a class or the stylesheet", () => {
    expect(fontProblems(main, 'className="font-black"', css)).toEqual([
      "weight 900 is asked for and not loaded",
    ]);
    expect(fontProblems(main, 'className="font-[800]"', css)).toEqual([
      "weight 800 is asked for and not loaded",
    ]);
    expect(fontProblems(main, "", `${css}\nstrong { font-weight: 800; }`)).toEqual([
      "weight 800 is asked for and not loaded",
    ]);
    // Anything from 100 to 700 is one file.
    expect(fontProblems(main, 'className="font-[650] font-thin font-bold"', css)).toEqual([]);
  });

  it("names font-synthesis left on", () => {
    expect(fontProblems(main, markup, css.replace(/font-synthesis:\s*none;/, ""))).toEqual([
      "font-synthesis is not off, so a missing face would be faked",
    ]);
  });

  it("names a weight asked of Mono, which is one face", () => {
    expect(fontProblems(main, '<code className="font-mono text-sm font-semibold">', css)).toEqual([
      "font-semibold with font-mono asks for a weight of Plex Mono, which is loaded at 400 only",
    ]);
    expect(fontProblems(main, '<code className="font-mono font-normal">', css)).toEqual([]);
    // A bold word and a mono word in different class lists are fine.
    expect(
      fontProblems(main, '<b className="font-semibold"/><code className="font-mono">', css),
    ).toEqual([]);
  });
});
