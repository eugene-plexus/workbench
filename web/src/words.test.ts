/**
 * C3 call 1 starts workshop names with the second tool. C5a adds MCP, so
 * Toolbox now carries its plain meaning, Tools; the media screens add Bins,
 * which carries Media, and Work orders, which carries Long jobs (video). The
 * Foreman that polls them is the server's, and stays off screen. Unbuilt
 * modules keep waiting. Read copy as strings
 * and JSX text, not exported values.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const WORKSHOP = ["Jigs", "Crew", "The shop", "Foreman", "Dispatch"];
/** A built workshop name, and the plain meaning it never appears without. */
const BUILT: [string, string][] = [
  ["Toolbox", "Toolbox · Tools"],
  ["Bins", "Bins · Media"],
  ["Work orders", "Work orders · Long jobs"],
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
  });
}

it("explains Toolbox, Bins and Work orders and keeps unbuilt workshop names off screen", () => {
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

/**
 * Memory (MR1) says "Memory" and "notes". How it works underneath (RAG, a
 * vector store, embeddings, retrieval) is never on the way a person reads it
 * (workbench-memory: words), so none of those words is in the files that write
 * what a person reads about it, comments included, so a string that is added
 * later cannot slip one in unseen.
 */
const MEMORY_FILES = [
  "components/Memory.tsx",
  "components/MemoryChip.tsx",
  "components/MemoryToast.tsx",
  "components/RememberThis.tsx",
  "lib/memory.ts",
  "lib/useChatMemory.ts",
];
const NOT_ON_THE_PATH = /\b(RAG|vectors?|embeddings?|retrieval|retrieve[ds]?)\b/i;

it("keeps how memory works underneath off every screen about it", () => {
  const found: string[] = [];
  for (const file of MEMORY_FILES) {
    const text = readFileSync(join(import.meta.dirname, file), "utf8");
    const word = NOT_ON_THE_PATH.exec(text);
    if (word) found.push(`${file}: ${word[0]}`);
  }
  expect(found).toEqual([]);
});

it("can fail: it names each of those words, in any case, and lets the plain ones through", () => {
  for (const sample of ["a vector store", "RAG", "Embeddings", "retrieval", "it was retrieved"]) {
    expect(NOT_ON_THE_PATH.test(sample), sample).toBe(true);
  }
  for (const sample of ["Memory", "notes", "Delete note", "storage", "Refresh memory"]) {
    expect(NOT_ON_THE_PATH.test(sample), sample).toBe(false);
  }
});

it("reads the memory files it is about", () => {
  for (const file of MEMORY_FILES) {
    expect(() => readFileSync(join(import.meta.dirname, file), "utf8"), file).not.toThrow();
  }
});

/**
 * Documents (MR2) say "files", "pages" and "tokens". How they are read underneath
 * (chunks, embeddings, a vector store, retrieval, RAG) is not on the way a person
 * reads it, so none of those words is in the files that write what a person reads
 * about documents, comments included.
 */
const DOCUMENT_FILES = [
  "components/Attachments.tsx",
  "components/DocumentText.tsx",
  "lib/documents.ts",
  "lib/useChatFiles.ts",
];
const NOT_ON_THE_DOCUMENT_PATH = /\b(RAG|vectors?|embeddings?|retrieval|retrieve[ds]?|chunks?)\b/i;

it("keeps how documents are read underneath off every screen about them", () => {
  const found: string[] = [];
  for (const file of DOCUMENT_FILES) {
    const text = readFileSync(join(import.meta.dirname, file), "utf8");
    const word = NOT_ON_THE_DOCUMENT_PATH.exec(text);
    if (word) found.push(`${file}: ${word[0]}`);
  }
  expect(found).toEqual([]);
});

it("can fail: it names each of those words, and lets the plain ones through", () => {
  for (const sample of [
    "RAG",
    "a text chunk",
    "Chunks",
    "an embedding",
    "vector search",
    "retrieval",
  ]) {
    expect(NOT_ON_THE_DOCUMENT_PATH.test(sample), sample).toBe(true);
  }
  for (const sample of [
    "Documents",
    "files",
    "pages",
    "tokens",
    "window",
    "Show what Workbench read",
  ]) {
    expect(NOT_ON_THE_DOCUMENT_PATH.test(sample), sample).toBe(false);
  }
});

it("reads the document files it is about", () => {
  for (const file of DOCUMENT_FILES) {
    expect(() => readFileSync(join(import.meta.dirname, file), "utf8"), file).not.toThrow();
  }
});
