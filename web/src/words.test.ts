/**
 * C3 call 1 starts workshop names with the second tool. C5a adds MCP, so
 * Toolbox now carries its plain meaning, Tools. Unbuilt modules keep
 * waiting. Read copy as strings and JSX text, not exported values.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const WORKSHOP = ["Jigs", "Work orders", "Bins", "Crew", "The shop", "Foreman", "Dispatch"];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

it("explains Toolbox and keeps unbuilt workshop names off screen", () => {
  const found: string[] = [];
  for (const path of sources(import.meta.dirname)) {
    const text = readFileSync(path, "utf8");
    if (/\bToolbox\b/.test(text.replaceAll("Toolbox · Tools", ""))) {
      found.push(`${path}: Toolbox without its plain meaning`);
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
