/**
 * Artifacts (workbench-rich-content-roadmap.md RC5; the server's
 * src/eugene_plexus_workbench/artifacts.py): what a block of an answer is as
 * an artifact, the number that finds its saved revisions, and its file.
 *
 * An answer asks for one with the word `artifact` in a fence's info string
 * (```` ```html artifact title="Pricing table" ````); any other closed fence of
 * these languages can be opened as one by the person.
 */

export type ArtifactKind = "markdown" | "svg" | "html" | "react";

/** An artifact's kinds, by the fence languages that name them. */
const KINDS = new Map<string, ArtifactKind>([
  ["markdown", "markdown"],
  ["md", "markdown"],
  ["svg", "svg"],
  ["html", "html"],
  ["jsx", "react"],
  ["tsx", "react"],
  ["react", "react"],
]);

export const KIND_NAMES: Record<ArtifactKind, string> = {
  markdown: "Markdown",
  svg: "SVG",
  html: "HTML",
  react: "React",
};

const DEFAULT_TITLES: Record<ArtifactKind, string> = {
  markdown: "Document",
  svg: "Picture",
  html: "Page",
  react: "Component",
};

/** One revision's source, in UTF-8 bytes, as the server keeps it. */
export const MAX_SOURCE = 512 * 1024;
export const MAX_TITLE = 120;

/**
 * A block's words as the fence holds them: the parser ends a code block's text
 * with the newline before its closing fence, which is not the block's.
 */
export function blockText(source: string): string {
  return source.endsWith("\n") ? source.slice(0, -1) : source;
}

/** The kind a fence's language names, or null. */
export function kindOf(language: string | null): ArtifactKind | null {
  return language ? (KINDS.get(language.toLowerCase()) ?? null) : null;
}

/** Whether a kind runs when previewed (a page, a component). */
export function runs(kind: ArtifactKind): boolean {
  return kind === "html" || kind === "react";
}

const TITLE = /\btitle\s*=\s*(?:"([^"]*)"|'([^']*)')/;

/**
 * What a fence's words after its language ask for: an artifact and its title
 * when they hold the word `artifact`, else null.
 */
export function askedFor(meta: string | null | undefined, kind: ArtifactKind): string | null {
  if (!meta) return null;
  const words = meta.replace(TITLE, " ").trim().split(/\s+/);
  if (!words.includes("artifact")) return null;
  const found = TITLE.exec(meta);
  return titleOf(found?.[1] ?? found?.[2] ?? "", kind);
}

/** A title as the server keeps it: one line, at most MAX_TITLE characters, never empty. */
export function titleOf(title: string, kind: ArtifactKind): string {
  const words = title.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_TITLE);
  return words || DEFAULT_TITLES[kind];
}

/**
 * The number the server keeps for a block's words (artifacts.py `digest`): its
 * length in UTF-16 code units and cyrb53 over them.
 */
export function digest(source: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < source.length; i++) {
    const unit = source.charCodeAt(i);
    h1 = Math.imul(h1 ^ unit, 2654435761);
    h2 = Math.imul(h2 ^ unit, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  const value = 4294967296 * (2097151 & h2) + (h1 >>> 0);
  return `${source.length}-${value.toString(16)}`;
}

/** A source's size in UTF-8 bytes. */
export function byteSize(source: string): number {
  return new TextEncoder().encode(source).length;
}

const EXTENSIONS: Record<ArtifactKind, string> = {
  markdown: "md",
  svg: "svg",
  html: "html",
  react: "jsx",
};

/** The file a revision downloads as: its title in plain letters, and its kind's extension. */
export function fileName(title: string, kind: ArtifactKind, language: string | null): string {
  const words = title
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  const extension =
    kind === "react" && language?.toLowerCase() === "tsx" ? "tsx" : EXTENSIONS[kind];
  return `${words || DEFAULT_TITLES[kind].toLowerCase()}.${extension}`;
}

/** What a message says of an artifact kept for one of its blocks. */
export type ArtifactSummary = {
  id: string;
  kind: ArtifactKind;
  title: string;
  digest: string;
  revisions: number;
  createdAt: number;
  updatedAt: number;
};

export type RevisionView = {
  number: number;
  author: "answer" | "person";
  size: number;
  createdAt: number;
  source?: string;
};

export type ArtifactView = ArtifactSummary & {
  messageId: string;
  revisionList: RevisionView[];
};

/** The artifact kept for a block, by its kind and words. */
export function keptFor(
  artifacts: ArtifactSummary[] | undefined,
  kind: ArtifactKind,
  source: string,
): ArtifactSummary | null {
  if (!artifacts?.length) return null;
  const wanted = digest(source);
  return artifacts.find((a) => a.kind === kind && a.digest === wanted) ?? null;
}

/** An edit not saved yet, and the revision it began from. */
export type Draft = { text: string; base: number };

/**
 * Drafts not yet saved, by block: kept while the page is open, so closing the
 * dialog by mistake loses nothing. Never stored.
 */
const drafts = new Map<string, Draft>();

export function draftOf(key: string): Draft | undefined {
  return drafts.get(key);
}

export function keepDraft(key: string, draft: Draft | null): void {
  if (draft === null) drafts.delete(key);
  else drafts.set(key, draft);
}

/** Every draft forgotten: at sign-out. */
export function forgetDrafts(): void {
  drafts.clear();
}
