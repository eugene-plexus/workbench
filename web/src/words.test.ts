/**
 * Plain words in version 1 (Troy, C3 call 1): the workshop names in
 * workbench.md §6 start when there is more than one tool, so none of them
 * is on screen yet. Read as text, because copy is strings and JSX text,
 * not values a module exports.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const WORKSHOP = [
  "Toolbox",
  "Jigs",
  "Work orders",
  "Bins",
  "Crew",
  "The shop",
  "Foreman",
  "Dispatch",
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

it("puts no workshop name on screen in version 1", () => {
  const found: string[] = [];
  for (const path of sources(import.meta.dirname)) {
    const text = readFileSync(path, "utf8");
    for (const word of WORKSHOP) {
      if (new RegExp(`\\b${word}\\b`).test(text)) found.push(`${path}: ${word}`);
    }
  }
  expect(found).toEqual([]);
});

it("reads the files it is about", () => {
  expect(sources(import.meta.dirname).some((p) => p.endsWith("Composer.tsx"))).toBe(true);
});
