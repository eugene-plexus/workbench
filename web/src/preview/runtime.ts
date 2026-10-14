/**
 * An artifact's preview (workbench-rich-content-roadmap.md RC5): the script of
 * the page at `/preview/frame`, which runs one HTML page or React component an
 * answer wrote, after the person pressed Run (components/ArtifactPreview.tsx).
 *
 * That page is held apart by the server, not by this script: its own policy
 * (web.py `PREVIEW_CSP`) gives it no origin of its own (`sandbox`), so it
 * reads no cookie and no storage of Workbench's and cannot reach the page
 * that framed it; it loads nothing from the network and nothing from
 * Workbench but this file (`connect-src 'none'`, no image or font but
 * `data:` and `blob:`); it cannot open a window or move the page it is in
 * (the frame's `sandbox="allow-scripts allow-forms"`). A form's own script runs
 * and the form never sends: this script stops each sending before it starts,
 * and the policy's `form-action 'none'` refuses any it missed. What runs here
 * is untrusted, and so is every message it sends: the page shows them as words.
 *
 * One way out is left: a frame may go to another address itself (by script,
 * or a `<meta>` refresh), and no policy Chrome keeps stops that. This script
 * says `leaving` as it starts to go (`beforeunload`), and the page removes the
 * frame; the request for the new address may have gone by then, carrying
 * whatever the preview put in it. A link the person clicks is not followed.
 *
 * Built on its own as one classic script (vite.preview.config.ts): a module
 * would be fetched as another origin's, which an origin-less page cannot do.
 *
 * The page sends one message, `{type: "run", nonce, kind, source}`, after
 * this script says `ready`; later ones are ignored. This script says
 * `started` or `failed` (a component that cannot run, and why), each with the
 * nonce; `error`, `refused` (what was not loaded or sent) and `leaving` (the
 * preview tried to go to another page).
 */

import { createElement, type ComponentType } from "react";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as JsxRuntime from "react/jsx-runtime";
import { transform } from "sucrase";

/** What a component may import: React, and nothing else. */
const MODULES = new Map<string, unknown>([
  ["react", React],
  ["react-dom", ReactDOM],
  ["react-dom/client", ReactDOMClient],
  ["react/jsx-runtime", JsxRuntime],
]);

/** A message's words are cut to this. */
const MOST = 500;

/**
 * What a form's sending is said as. A form is never sent from a preview, and
 * Chrome is not even asked to: a frame whose form Chrome refused leaves its
 * process unable to end if a preview in it later loops, so that every preview
 * after it never starts, a reload of the page too (measured 2026-10-14, Chrome
 * 155, with a sandbox that refused forms: 85 s and counting, where a preview
 * with no form sent started again 7 s after Stop). So `form.submit()` is
 * refused here, and a `submit` (a button, Enter, `requestSubmit()`) that its
 * page did not handle is stopped, once its own handlers have heard it: the
 * sandbox allows forms only so that a `submit` happens at all (without it
 * Chrome fires none, and a component's form does nothing).
 */
const FORM = "sending a form";

export function requireFor(name: string): unknown {
  const found = MODULES.get(name);
  if (found === undefined) {
    throw new Error(`This preview has React only: it cannot load "${name.slice(0, 80)}"`);
  }
  return found;
}

/** A component's source as script: JSX and TypeScript to JavaScript, imports to `require`. */
export function compile(source: string): string {
  return transform(source, {
    transforms: ["jsx", "typescript", "imports"],
    jsxRuntime: "automatic",
    production: true,
    filePath: "artifact.tsx",
  }).code;
}

/** The component a module exports: its default, else its one function, else `App`. */
export function componentOf(exports: Record<string, unknown>): ComponentType | null {
  const fn = (value: unknown): value is ComponentType => typeof value === "function";
  if (fn(exports.default)) return exports.default;
  const functions = Object.values(exports).filter(fn);
  if (functions.length === 1) return functions[0] ?? null;
  return fn(exports.App) ? exports.App : null;
}

function words(value: unknown): string {
  const text = value instanceof Error ? value.message : String(value);
  return text.length > MOST ? `${text.slice(0, MOST - 1)}…` : text;
}

function post(message: Record<string, unknown>): void {
  try {
    window.parent.postMessage(message, "*");
  } catch {
    // A parent that is gone hears nothing.
  }
}

/**
 * The reporter a page is written with, first thing: what fails, what is not
 * loaded, a link that would leave, and leaving itself, told to the page.
 * Writing a page clears this script's own listeners (document.open), but not
 * what it changed on the window: `listen`'s `form.submit()` stays refused, and
 * the reporter refuses a form sent by its button.
 */
const REPORTER = `<script>(function () {
  var most = ${MOST};
  function cut(text) { text = String(text); return text.length > most ? text.slice(0, most - 1) + "\\u2026" : text; }
  function post(m) { try { parent.postMessage(m, "*"); } catch (e) {} }
  addEventListener("error", function (e) { post({ type: "error", message: cut(e.message || e.error || "an error") }); });
  addEventListener("unhandledrejection", function (e) { var r = e.reason; post({ type: "error", message: cut(r && r.message ? r.message : r) }); });
  document.addEventListener("securitypolicyviolation", function (e) { post({ type: "refused", what: cut(e.blockedURI || e.violatedDirective) }); });
  addEventListener("beforeunload", function () { post({ type: "leaving" }); });
  addEventListener("pagehide", function () { post({ type: "leaving" }); });
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (a && a.getAttribute("href").charAt(0) !== "#") { e.preventDefault(); post({ type: "refused", what: cut(a.href) }); }
  }, true);
  addEventListener("submit", function (e) { if (!e.defaultPrevented) { e.preventDefault(); post({ type: "refused", what: FORM }); } });
})();</script>`.replace(/FORM/g, JSON.stringify(FORM));

/** A page with the reporter first: after its doctype, so the page keeps its mode. */
export function withReporter(html: string): string {
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html);
  return doctype ? doctype[0] + REPORTER + html.slice(doctype[0].length) : REPORTER + html;
}

/** Say what a component's page does wrong, as the reporter does for a written page. */
export function listen(): void {
  window.addEventListener("error", (event) =>
    post({ type: "error", message: words(event.error ?? event.message) }),
  );
  window.addEventListener("unhandledrejection", (event) =>
    post({ type: "error", message: words(event.reason) }),
  );
  document.addEventListener("securitypolicyviolation", (event) =>
    post({ type: "refused", what: words(event.blockedURI || event.violatedDirective) }),
  );
  // As soon as it starts to go, so the page can stop it before it gets there.
  window.addEventListener("beforeunload", () => post({ type: "leaving" }));
  window.addEventListener("pagehide", () => post({ type: "leaving" }));
  document.addEventListener(
    "click",
    (event) => {
      const link = (event.target as Element | null)?.closest?.("a[href]");
      if (link && !link.getAttribute("href")?.startsWith("#")) {
        event.preventDefault();
        post({ type: "refused", what: words((link as HTMLAnchorElement).href) });
      }
    },
    true,
  );
  HTMLFormElement.prototype.submit = () => post({ type: "refused", what: FORM });
  // Last, as it bubbles: a component that handled its form (`preventDefault`, as
  // React's `onSubmit` does) sent nothing and is not told so.
  window.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    event.preventDefault();
    post({ type: "refused", what: FORM });
  });
}

type Run = { type: "run"; nonce: string; kind: "html" | "react"; source: string };

function isRun(data: unknown): data is Run {
  if (typeof data !== "object" || data === null) return false;
  const run = data as Record<string, unknown>;
  return (
    run.type === "run" &&
    typeof run.nonce === "string" &&
    run.nonce.length <= 64 &&
    (run.kind === "html" || run.kind === "react") &&
    typeof run.source === "string" &&
    run.source.length <= 600_000
  );
}

function runHtml(source: string, nonce: string): void {
  post({ type: "started", nonce });
  document.open();
  document.write(withReporter(source));
  document.close();
}

function runReact(source: string, nonce: string): void {
  let code: string;
  try {
    code = compile(source);
  } catch (error) {
    post({ type: "failed", nonce, message: words(`it could not be read: ${words(error)}`) });
    return;
  }
  const box: {
    require: typeof requireFor;
    module: { exports: Record<string, unknown> };
    failed?: unknown;
  } = { require: requireFor, module: { exports: {} } };
  (window as unknown as { __artifact: typeof box }).__artifact = box;
  // A script of its own (this page allows inline script, and no eval). What it throws
  // is caught in it: this file is Workbench's, another origin's to this page, so an
  // error thrown in it (by `require`) reaches an `error` listener as "Script error."
  const script = document.createElement("script");
  script.textContent =
    "try { (function (require, module, exports) {\n" +
    code +
    "\n})(__artifact.require, __artifact.module, __artifact.module.exports); }" +
    " catch (error) { __artifact.failed = error; }";
  document.head.appendChild(script);
  script.remove();
  if ("failed" in box) {
    post({ type: "failed", nonce, message: words(box.failed) });
    return;
  }
  const Component = componentOf(box.module.exports);
  if (!Component) {
    post({
      type: "failed",
      nonce,
      message: "it exports nothing to show: export the function that draws it",
    });
    return;
  }
  const root = document.createElement("div");
  root.id = "root";
  document.body.replaceChildren(root);
  ReactDOMClient.createRoot(root, {
    onUncaughtError: (error) => post({ type: "error", message: words(error) }),
  }).render(createElement(Component));
  post({ type: "started", nonce });
}

/** Wait for the page's one message, and run what it holds. */
export function start(): void {
  let ran = false;
  listen();
  window.addEventListener("message", (event) => {
    if (ran || event.source !== window.parent || !isRun(event.data)) return;
    ran = true;
    if (event.data.kind === "html") runHtml(event.data.source, event.data.nonce);
    else runReact(event.data.source, event.data.nonce);
  });
  post({ type: "ready" });
}

// Only in a frame: a test imports this file at the top of its own window.
if (typeof window !== "undefined" && window.parent !== window) start();
