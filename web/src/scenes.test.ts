/**
 * The working scenes' gate (workbench.md §6.1): every file in
 * `public/scenes/` passes it, every file is a listed scene with its phrase,
 * and each rule is shown failing on a file broken in that one way.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { SCENES } from "./lib/scenes";
import { SCENE_BUDGET_BYTES, sceneProblems } from "./sceneGate";

const DIR = join(import.meta.dirname, "..", "public", "scenes");
const FILES = readdirSync(DIR).sort();

const check = (text: string) => sceneProblems(text, new TextEncoder().encode(text).length);

describe("the scenes on disk", () => {
  it("are exactly the listed scenes, each with its phrase", () => {
    expect(FILES.length).toBeGreaterThan(0);
    expect(FILES).toEqual(SCENES.map((s) => s.file).sort());
    for (const scene of SCENES) expect(scene.phrase).toMatch(/^[A-Z][^…]*…$/);
    expect(new Set(SCENES.map((s) => s.phrase)).size).toBe(SCENES.length);
  });

  it.each(FILES)("%s passes the gate", (file) => {
    const bytes = readFileSync(join(DIR, file));
    expect(sceneProblems(bytes.toString("utf8"), bytes.length)).toEqual([]);
  });
});

const GOOD = `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 320 320">
  <defs><linearGradient id="wood"><stop offset="0" stop-color="#c90"/></linearGradient></defs>
  <style>
    #arm { transform-box: fill-box; transform-origin: 10% 90%; animation: saw 3s ease-in-out infinite; }
    #dust { transform-box: fill-box; transform-origin: 50% 50%; animation: puff 3s linear infinite; }
    @keyframes saw { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(12px, 0); } }
    @keyframes puff {
      from { opacity: 0; transform: scale(0.5); animation-timing-function: ease-out; }
      40% { opacity: 1; }
      to { opacity: 0; transform: scale(0.5); }
    }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
  </style>
  <g transform="translate(4 4)"><g id="arm"><path d="M0 0 L10 10" fill="url(#wood)"/></g></g>
  <g id="dust"><use href="#arm"/><circle r="2"/></g>
</svg>`;

/** Each break: a change to the good file, and what the gate must say about it. */
const BREAKS: [string, (svg: string) => string, string][] = [
  ["a script", (s) => s.replace("</svg>", "<script>alert(1)</script></svg>"), "it has a <script>"],
  [
    "a script hidden from the parser",
    (s) => s.replace("<svg", "<!-- --><svg").replace("</svg>", "<SCRIPT >x</SCRIPT></svg>"),
    "it has a <script>",
  ],
  [
    "an external image",
    (s) => s.replace("</svg>", '<image href="https://example.org/x.png"/></svg>'),
    "it names an external URL",
  ],
  [
    "a link to another file",
    (s) => s.replace('href="#arm"', 'href="other.svg#arm"'),
    "<use> links outside the file",
  ],
  [
    "a data: URL",
    (s) => s.replace("url(#wood)", "url(data:image/png;base64,AAAA)"),
    "it embeds a data: URL",
  ],
  ["a style import", (s) => s.replace("<style>", "<style>@import 'x.css';"), "imports another"],
  [
    "an external style url",
    (s) => s.replace("url(#wood)", "url('wood.png')"),
    "its style points outside the file",
  ],
  [
    "an event handler",
    (s) => s.replace("<circle", '<circle onclick="x()"'),
    "has an event handler (onclick)",
  ],
  [
    "SMIL, which reduced motion does not stop",
    (s) => s.replace('<circle r="2"/>', '<circle r="2"><animate attributeName="r"/></circle>'),
    "it has a forbidden <animate>",
  ],
  [
    "an animation in a style attribute",
    (s) => s.replace("<circle", '<circle style="animation: saw 3s infinite"'),
    "animates in a style attribute",
  ],
  [
    "no reduced-motion rule",
    (s) => s.replace(/@media[^}]*}\s*}/, ""),
    "it has no @media (prefers-reduced-motion: reduce) rule",
  ],
  [
    "a reduced-motion rule an id outranks",
    (s) => s.replace("none !important", "none"),
    "reduced motion does not stop #arm",
  ],
  [
    "a reduced-motion rule that misses a part",
    (s) => s.replace("{ * { animation", "{ #arm { animation"),
    "reduced motion does not stop #dust",
  ],
  [
    "0% and 100% that differ",
    (s) =>
      s.replace(
        "0%, 100% { transform: translate(0, 0); }",
        "0% { transform: translate(0, 0); } 100% { transform: translate(1px, 0); }",
      ),
    "@keyframes saw: 0% and 100% differ",
  ],
  [
    "a 100% keyframe missing",
    (s) => s.replace("to { opacity: 0; transform: scale(0.5); }", "90% { opacity: 0; }"),
    "@keyframes puff needs both a 0% and a 100% keyframe",
  ],
  [
    "a property other than transform and opacity",
    (s) => s.replace("40% { opacity: 1; }", "40% { opacity: 1; fill: red; }"),
    "animates fill",
  ],
  [
    "an animated part that is not a group",
    (s) => s.replace('<circle r="2"/>', '<circle id="dot" r="2"/>').replace("#dust {", "#dot {"),
    "#dot is a <circle>",
  ],
  [
    "a group whose transform attribute the animation replaces",
    (s) => s.replace('<g id="dust">', '<g id="dust" transform="translate(5 5)">'),
    "#dust has a transform attribute",
  ],
  [
    "no transform-box",
    (s) => s.replace("#arm { transform-box: fill-box; ", "#arm { "),
    "#arm needs transform-box: fill-box",
  ],
  [
    "no transform-origin",
    (s) => s.replace("transform-origin: 10% 90%; ", ""),
    "#arm needs a transform-origin",
  ],
  ["a loop too short", (s) => s.replaceAll("3s", "1s"), "loops in 1 s"],
  ["a loop too long", (s) => s.replaceAll("3s", "4500ms"), "loops in 4.5 s"],
  [
    "loops of different lengths",
    (s) => s.replace("puff 3s", "puff 2s"),
    "its loops differ in length",
  ],
  ["a delay", (s) => s.replace("saw 3s ease-in-out", "saw 3s ease-in-out 1s"), "has a delay"],
  [
    "no infinite",
    (s) => s.replace("saw 3s ease-in-out infinite", "saw 3s ease-in-out"),
    "does not loop",
  ],
  [
    "two animations on one part",
    (s) => s.replace("ease-in-out infinite;", "ease-in-out infinite, puff 3s infinite;"),
    "runs two animations",
  ],
  [
    "a longhand",
    (s) => s.replace("#arm {", "#arm { animation-delay: 1s;"),
    "use the animation shorthand",
  ],
  [
    "an unknown keyframes",
    (s) => s.replace("animation: saw", "animation: sew"),
    "names no @keyframes",
  ],
  [
    "a missing id",
    (s) => s.replace('id="dust"', 'id="dirt"'),
    "#dust is animated but nothing has that id",
  ],
  ["no viewBox", (s) => s.replace(' viewBox="0 0 320 320"', ""), "its <svg> has no viewBox"],
  ["broken XML", (s) => s.replace("</svg>", ""), "it is not a well-formed SVG"],
  [
    "over the budget",
    (s) => s.replace("</svg>", `<desc>${"x".repeat(SCENE_BUDGET_BYTES)}</desc></svg>`),
    "over the 32768-byte budget",
  ],
];

describe("the gate", () => {
  it("passes a good scene", () => {
    expect(check(GOOD)).toEqual([]);
  });

  it.each(BREAKS)("fails %s", (_, breakIt, said) => {
    const broken = breakIt(GOOD);
    expect(broken).not.toBe(GOOD);
    const problems = check(broken);
    expect(problems.join("\n")).toContain(said);
  });
});
