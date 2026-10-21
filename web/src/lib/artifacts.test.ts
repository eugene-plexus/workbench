/**
 * What a block is as an artifact (rich content RC5): its kind, whether the
 * answer asked for one, the number the server keeps for it, and its file.
 */

import {
  askedFor,
  digest,
  draftOf,
  fileName,
  forgetDrafts,
  keepDraft,
  keptFor,
  kindOf,
  runs,
  titleOf,
  toolKind,
  type ArtifactSummary,
} from "./artifacts";
import { readSvg, Refused } from "./svgArtifact";

describe("a block as an artifact", () => {
  it("tool_artifact_kind_uses_mime_resources_and_sniffing", () => {
    expect(toolKind("plain words", "text/markdown; charset=utf-8")).toBe("markdown");
    expect(toolKind("plain words", undefined, "file:///notes.md")).toBe("markdown");
    expect(toolKind('<svg viewBox="0 0 1 1"/>')).toBe("svg");
    expect(toolKind("<!doctype html><h1>Page</h1>")).toBe("html");
    expect(toolKind("<div>Page</div>", "text/plain")).toBe("html");
    expect(toolKind("# Notes\n\n- one")).toBe("markdown");
    expect(toolKind("ordinary words")).toBeNull();
    expect(toolKind("", "text/html")).toBeNull();
  });

  it("is of a kind its language names, and only those", () => {
    expect(["markdown", "md", "svg", "html", "jsx", "tsx", "react", "HTML"].map(kindOf)).toEqual([
      "markdown",
      "markdown",
      "svg",
      "html",
      "react",
      "react",
      "react",
      "html",
    ]);
    expect([null, "python", "mermaid", "chart", "xml", "constructor"].map(kindOf)).toEqual([
      null,
      null,
      null,
      null,
      null,
      null,
    ]);
    expect(runs("html") && runs("react") && !runs("svg") && !runs("markdown")).toBe(true);
  });

  it("is asked for by the word artifact after the language, with a title or its kind's", () => {
    expect(askedFor('artifact title="Pricing table"', "html")).toBe("Pricing table");
    expect(askedFor("title='Logo' artifact", "svg")).toBe("Logo");
    expect(askedFor("artifact", "react")).toBe("Component");
    expect(askedFor('title="An artifact of sorts"', "html")).toBeNull();
    expect(askedFor("artifacts", "html")).toBeNull();
    expect(askedFor("", "html")).toBeNull();
    expect(askedFor(undefined, "html")).toBeNull();
  });

  it("keeps a title to one short line", () => {
    expect(titleOf("  Two\n lines ", "html")).toBe("Two lines");
    expect(titleOf("x".repeat(500), "markdown")).toHaveLength(120);
    expect(titleOf("", "markdown")).toBe("Document");
  });

  it("is found by the number the server computes (tests/test_artifacts.py has the same)", () => {
    expect(digest("")).toBe("0-bdcb81aee8d83");
    expect(digest("<svg/>")).toBe("6-1afcc7b26c5050");
    expect(digest("é 😀\n")).toBe("5-13aaf5a05f19cb");
    expect(digest("a")).not.toBe(digest("b"));
  });

  it("finds what is kept for a block by its kind and words", () => {
    const kept: ArtifactSummary = {
      id: "a1",
      kind: "html",
      title: "Prices",
      digest: digest("<p>x</p>"),
      revisions: 3,
      createdAt: 1,
      updatedAt: 2,
    };
    expect(keptFor([kept], "html", "<p>x</p>")).toBe(kept);
    expect(keptFor([kept], "markdown", "<p>x</p>")).toBeNull();
    expect(keptFor([kept], "html", "<p>y</p>")).toBeNull();
    expect(keptFor(undefined, "html", "<p>x</p>")).toBeNull();
  });

  it("downloads under its title, with its kind's extension", () => {
    expect(fileName("Pricing table!", "html", "html")).toBe("pricing-table.html");
    expect(fileName("Zähler", "react", "tsx")).toBe("zähler.tsx");
    expect(fileName("", "react", "jsx")).toBe("component.jsx");
    expect(fileName("../../etc", "markdown", "md")).toBe("etc.md");
  });

  it("keeps a draft while the page is open, and forgets all at sign-out", () => {
    keepDraft("m:html:1", { text: "mine", base: 2 });
    expect(draftOf("m:html:1")).toEqual({ text: "mine", base: 2 });
    keepDraft("m:html:1", null);
    expect(draftOf("m:html:1")).toBeUndefined();
    keepDraft("m:html:2", { text: "other", base: 1 });
    forgetDrafts();
    expect(draftOf("m:html:2")).toBeUndefined();
  });
});

describe("an SVG artifact", () => {
  it("is a picture written anew, sized by its width and height when it has no box", () => {
    const picture = readSvg(
      '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><title>Dot</title>' +
        '<circle cx="10" cy="10" r="5" fill="#f00" onclick="alert(1)" class="x"/></svg>',
      "#ffffff",
    );
    expect(picture.width).toBe(40);
    expect(picture.height).toBe(20);
    expect(picture.title).toBe("Dot");
    expect(picture.svg).toContain('<circle cx="10" cy="10" r="5" fill="#f00">');
    expect(picture.svg).not.toMatch(/onclick|class=/);
    expect(picture.src.startsWith("data:image/svg+xml")).toBe(true);
  });

  it.each([
    [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><script>x()</script></svg>',
      /<script>/,
    ],
    [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><style>rect{}</style></svg>',
      /<style>/,
    ],
    ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9 9"><rect', /not well-formed/],
    ["<html><body/></html>", /not an SVG/],
    ['<svg viewBox="0 0 9 9"/>', /not an SVG/],
    ['<svg xmlns="http://www.w3.org/2000/svg" width="10em" height="2em"/>', /no size/],
  ])("is not shown, saying why, when it is %j", (source, why) => {
    expect(() => readSvg(source, "#ffffff")).toThrow(Refused);
    expect(() => readSvg(source, "#ffffff")).toThrow(why);
  });

  it("names nothing outside itself: an outside picture is dropped, an outside fill too", () => {
    const picture = readSvg(
      '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
        'viewBox="0 0 9 9"><image href="https://example.com/x.png"/>' +
        '<rect width="9" height="9" fill="url(https://example.com/#p)"/>' +
        '<use xlink:href="https://example.com/s.svg#a"/></svg>',
      "#ffffff",
    );
    expect(picture.svg).not.toContain("example.com");
  });
});
