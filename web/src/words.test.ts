/**
 * C3 call 1 starts workshop names with the second tool. C5a adds MCP, so
 * Toolbox now carries its plain meaning, Tools; the media screens add Bins,
 * which carries Media. Unbuilt modules keep waiting. Read copy as strings
 * and JSX text, not exported values.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const WORKSHOP = ["Jigs", "Work orders", "Crew", "The shop", "Foreman", "Dispatch"];
/** A built workshop name, and the plain meaning it never appears without. */
const BUILT: [string, string][] = [
  ["Toolbox", "Toolbox · Tools"],
  ["Bins", "Bins · Media"],
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

it("explains Toolbox and Bins and keeps unbuilt workshop names off screen", () => {
  const found: string[] = [];
  for (const path of sources(import.meta.dirname)) {
    const text = readFileSync(path, "utf8");
    for (const [name, plain] of BUILT) {
      if (new RegExp(`\\b${name}\\b`).test(text.replaceAll(plain, ""))) {
        found.push(`${path}: ${name} without its plain meaning`);
      }
    }
    for (const word of WORKSHOP) {
      if (new RegExp(`\\b${word}\\b`).test(text)) found.push(`${path}: ${word}`);
    }
  }
  expect(found).toEqual([]);
});

it("reads the files it is about", () => {
  expect(sources(import.meta.dirname).some((p) => p.endsWith("Composer.tsx"))).toBe(true);
});
