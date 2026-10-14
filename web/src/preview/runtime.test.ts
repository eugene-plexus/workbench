/**
 * The preview's own script (rich content RC5), its parts that do not need a
 * frame: what a component may import, how it is compiled and found, and the
 * reporter a page is written with. The frame itself is the browser run's
 * (tests/test_rich_artifacts_browser.py).
 */

import * as React from "react";

import { compile, componentOf, listen, requireFor, withReporter } from "./runtime";

describe("a component", () => {
  it("imports React and nothing else, saying so", () => {
    expect(requireFor("react")).toBe(React);
    expect(() => requireFor("lodash")).toThrow(
      'This preview has React only: it cannot load "lodash"',
    );
    expect(() => requireFor("../../api")).toThrow(/cannot load/);
  });

  it("is compiled from JSX and TypeScript to a script with no import of its own", () => {
    const code = compile(
      'import { useState } from "react";\n' +
        "type P = { start?: number };\n" +
        "export default function C({ start = 0 }: P) {\n" +
        "  const [n] = useState(start);\n" +
        "  return <b>{n}</b>;\n" +
        "}\n",
    );
    expect(code).toMatch(/require\(["']react["']\)/);
    expect(code).toMatch(/require\(["']react\/jsx-runtime["']\)/);
    expect(code).not.toMatch(/\bimport\b|: P\b|<b>/);
  });

  it("is its module's default, else its one function, else App", () => {
    const one = () => null;
    const two = () => null;
    expect(componentOf({ default: one })).toBe(one);
    expect(componentOf({ Only: one, value: 3 })).toBe(one);
    expect(componentOf({ App: one, Other: two })).toBe(one);
    expect(componentOf({ First: one, Second: two })).toBeNull();
    expect(componentOf({})).toBeNull();
  });

  it("that cannot be read says where", () => {
    expect(() => compile("export default function () { return <b>; }")).toThrow();
  });
});

describe("a form", () => {
  it("is never sent from a preview, by script or by its button, and that is said", () => {
    const said = vi.spyOn(window, "postMessage").mockImplementation(() => undefined);
    try {
      listen();
      const form = document.createElement("form");
      form.action = "/api/chats";
      document.body.appendChild(form);
      form.submit();
      const event = new Event("submit", { cancelable: true, bubbles: true });
      form.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      // A form its component handled (React's onSubmit prevents it) sent nothing:
      // no word of it.
      const handled = (e: Event) => e.preventDefault();
      form.addEventListener("submit", handled);
      const own = new Event("submit", { cancelable: true, bubbles: true });
      form.dispatchEvent(own);
      expect(own.defaultPrevented).toBe(true);
      expect(said.mock.calls.map(([m]) => m)).toEqual([
        { type: "refused", what: "sending a form" },
        { type: "refused", what: "sending a form" },
      ]);
      form.remove();
    } finally {
      said.mockRestore();
    }
  });
});

describe("a page", () => {
  it("is written with the reporter first, after its doctype so it keeps its mode", () => {
    const page = withReporter("<!DOCTYPE html>\n<p>Hi</p>");
    expect(page.startsWith("<!DOCTYPE html><script>")).toBe(true);
    expect(page.endsWith("\n<p>Hi</p>")).toBe(true);
    expect(withReporter("<p>Hi</p>").startsWith("<script>")).toBe(true);
    for (const said of [
      "error",
      "refused",
      "leaving",
      "pagehide",
      "beforeunload",
      'addEventListener("submit"',
      '"sending a form"',
    ]) {
      expect(page).toContain(said);
    }
  });
});
