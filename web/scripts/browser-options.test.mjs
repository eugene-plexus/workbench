/* global process, navigator, window, document, console */
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, afterEach, expect, it, vi } from "vitest";
import {
  contextOptions,
  readOptions,
  watchClipboardWrites,
  watchServiceWorkers,
  watchPolicy,
  screenshotConsoleRefusal,
  canCheckPreviewLoop,
  ignorablePageError,
  keepScheme,
  newChatLimit,
  goChat,
  goSignedIn,
} from "./browser-options.mjs";

const temp = mkdtempSync(join(tmpdir(), "workbench-browsers-"));
const playwright = join(temp, "playwright.cjs");
const config = join(temp, "browser.json");
writeFileSync(
  playwright,
  `for (const name of ["chromium", "firefox", "webkit"]) {
    exports[name] = { launch: async (options) => {
      console.log(JSON.stringify({ name, options }));
      process.exit(0);
    } };
  }`,
);
writeFileSync(
  config,
  JSON.stringify({
    playwright,
    chrome: "system-chrome",
    shots: temp,
    url: "http://127.0.0.1:8190",
  }),
);
afterAll(() => rmSync(temp, { recursive: true, force: true }));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const scripts = ["reading", "math", "diagram", "artifacts", "artifacts-inline", "structured"];
for (const script of scripts) {
  for (const name of ["chromium", "firefox", "webkit"]) {
    it(`${script} launches ${name} with --browser`, () => {
      const result = spawnSync(
        process.execPath,
        [join(process.cwd(), "scripts", `${script}-browser.mjs`), config, "--browser", name],
        { encoding: "utf8", env: { ...process.env, WORKBENCH_BROWSER: "chromium" } },
      );
      expect(result.status, result.stderr).toBe(0);
      expect(JSON.parse(result.stdout)).toEqual({
        name,
        options:
          name === "chromium"
            ? { headless: true, executablePath: "system-chrome" }
            : { headless: true },
      });
    });
  }
}

it("keeps Chromium as the default and accepts the browser before the config", () => {
  expect(readOptions([config], {})).toEqual({ configPath: config, browserName: "chromium" });
  expect(readOptions(["--browser=webkit", config], {})).toEqual({
    configPath: config,
    browserName: "webkit",
  });
});

it("lets the existing pytest server fixtures select the CI browser through the environment", () => {
  expect(readOptions([config], { WORKBENCH_BROWSER: "firefox" })).toEqual({
    configPath: config,
    browserName: "firefox",
  });
});

it("refuses an unknown browser or a missing config", () => {
  expect(() => readOptions([config, "--browser", "safari"], {})).toThrow(
    /chromium.*firefox.*webkit/,
  );
  expect(() => readOptions([], {})).toThrow(/config/);
});

it("keeps Firefox's phone viewport and touch while removing unsupported isMobile", () => {
  const phone = {
    viewport: { width: 390, height: 844 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 3,
  };
  const options = contextOptions("firefox", phone);
  expect(options).not.toHaveProperty("isMobile");
  expect(options).toMatchObject({ viewport: phone.viewport, hasTouch: true, deviceScaleFactor: 3 });
  expect(contextOptions("chromium", phone)).toEqual(phone);
  expect(contextOptions("webkit", phone)).toEqual(phone);
  expect(phone.isMobile).toBe(true);
});

it("observes a clipboard write only after the browser accepts it", async () => {
  const writeText = vi
    .fn()
    .mockResolvedValueOnce(undefined)
    .mockRejectedValueOnce(new Error("blocked"));
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  window.__copied = undefined;
  watchClipboardWrites();
  await navigator.clipboard.writeText("flowchart LR\n  A --> B");
  expect(writeText).toHaveBeenCalledWith("flowchart LR\n  A --> B");
  expect(window.__copied).toBe("flowchart LR\n  A --> B");
  await expect(navigator.clipboard.writeText("refused")).rejects.toThrow("blocked");
  expect(window.__copied).toBe("flowchart LR\n  A --> B");
});

it("observes registration rejection, resolution and synchronous refusal without replacing the result", async () => {
  const refused = Promise.reject(new Error("opaque origin"));
  const allowed = Promise.resolve({ scope: "/" });
  const register = vi
    .fn()
    .mockReturnValueOnce(refused)
    .mockReturnValueOnce(allowed)
    .mockImplementation(() => {
      throw new Error("blocked");
    });
  vi.stubGlobal("navigator", { serviceWorker: { register } });
  watchServiceWorkers();
  expect(navigator.serviceWorker.register("/sw.js")).toBe(refused);
  expect(navigator.serviceWorker.register("/allowed.js")).toBe(allowed);
  expect(() => navigator.serviceWorker.register("/throws.js")).toThrow("blocked");
  expect(await Promise.all(window.__serviceWorkers)).toEqual([true, false]);
});

it("exempts only counted WebKit screenshot styles at Playwright's exact source location", () => {
  let observe;
  let report;
  vi.stubGlobal(
    "MutationObserver",
    class {
      constructor(callback) {
        observe = callback;
      }
      observe() {}
    },
  );
  vi.spyOn(document, "addEventListener").mockImplementation((_type, callback) => {
    report = callback;
  });
  watchPolicy("webkit");
  const event = {
    violatedDirective: "style-src-elem",
    blockedURI: "inline",
    sourceFile: "",
    lineNumber: 5,
  };
  report(event); // No screenshot: this must still fail.
  expect(window.__policy).toHaveLength(1);
  observe([{ addedNodes: [{ nodeName: "STYLE", textContent: "body {}" }] }]);
  report({ ...event, sourceFile: "http://localhost/assets/app.js" });
  report({ ...event, violatedDirective: "script-src-elem" });
  report(event);
  expect(window.__screenshotRefusals).toBe(1);
  expect(window.__policy).toHaveLength(3);
  report(event); // One style cannot excuse two violations.
  expect(window.__policy).toHaveLength(4);
  watchPolicy("firefox");
  report(event);
  expect(window.__policy).toHaveLength(1);
  expect(screenshotConsoleRefusal("webkit", "Refused to load a script")).toBe(false);
});

it("skips only the loop scenarios in engines that share the chat's event loop", () => {
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  expect(canCheckPreviewLoop("chromium")).toBe(true);
  expect(log).not.toHaveBeenCalled();
  expect(canCheckPreviewLoop("firefox")).toBe(false);
  expect(canCheckPreviewLoop("webkit")).toBe(false);
  expect(
    log.mock.calls.every(([line]) => line.includes("SKIP") && line.includes("docs/browsers.md")),
  ).toBe(true);
});

it("states the colour scheme again where Firefox drops it on a COOP page", async () => {
  const emulateMedia = vi.fn(async () => {});
  await keepScheme({ emulateMedia }, "firefox", "dark");
  expect(emulateMedia).toHaveBeenCalledWith({ colorScheme: "dark" });
  await keepScheme({ emulateMedia }, "chromium", "dark");
  expect(emulateMedia).toHaveBeenCalledTimes(1);
});

it("ignores only WebKit's report of an abandoned request to the app's own API", () => {
  const abandoned = "http://127.0.0.1:41913/api/chats due to access control checks.";
  expect(ignorablePageError("webkit", abandoned)).toBe(true);
  expect(
    ignorablePageError(
      "webkit",
      "/127.0.0.1:41913/api/chats/x/events due to access control checks.",
    ),
  ).toBe(true);
  expect(ignorablePageError("firefox", abandoned)).toBe(false);
  expect(
    ignorablePageError("webkit", "https://example.com/api/x due to access control checks."),
  ).toBe(false);
  expect(ignorablePageError("webkit", "TypeError: undefined is not an object")).toBe(false);
});

it("limits New chat by its quiet time, so a slow runner passes and a stall does not", () => {
  expect(newChatLimit(150)).toBe(1000);
  // 1311 ms on a runner where New chat takes 700 ms quiet: no stall in that.
  expect(1311 < newChatLimit(700)).toBe(true);
  // A page held for 1.5 s adds that to its quiet time.
  expect(700 + 1500 < newChatLimit(700)).toBe(false);
  expect(150 + 1500 < newChatLimit(150)).toBe(false);
});

it("opens the sign-in page without waiting for every resource to load", async () => {
  const goto = vi.fn(async () => {});
  await goSignedIn({ goto, on() {}, off() {} }, "http://127.0.0.1:1/#signin=x");
  expect(goto).toHaveBeenCalledWith("http://127.0.0.1:1/#signin=x", {
    waitUntil: "domcontentloaded",
    timeout: 10_000,
  });
});

it("tries a navigation that does not commit again, saying so, while the server answers", async () => {
  const log = vi.spyOn(console, "log").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ status: 200 })),
  );
  const goto = vi
    .fn()
    .mockRejectedValueOnce(new Error("Timeout 10000ms exceeded"))
    .mockResolvedValueOnce(undefined);
  await goSignedIn({ goto, on() {}, off() {} }, "http://127.0.0.1:1/#signin=x");
  expect(goto).toHaveBeenCalledTimes(2);
  expect(log.mock.calls.some(([line]) => String(line).startsWith("RETRY"))).toBe(true);
  // Three failures in a row end the run with the engine's own error.
  goto.mockReset().mockRejectedValue(new Error("Timeout 10000ms exceeded"));
  await expect(
    goSignedIn({ goto, on() {}, off() {} }, "http://127.0.0.1:1/#signin=x"),
  ).rejects.toThrow("Timeout");
  expect(goto).toHaveBeenCalledTimes(3);
  // A server that does not answer is the cause, named at once.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new Error("refused");
    }),
  );
  goto.mockReset().mockRejectedValue(new Error("Timeout 10000ms exceeded"));
  await expect(
    goSignedIn({ goto, on() {}, off() {} }, "http://127.0.0.1:1/#signin=x"),
  ).rejects.toThrow("did not answer in 5 s");
  expect(goto).toHaveBeenCalledTimes(1);
});

it("opens a chat for the document outside Chromium, and as before in Chromium", async () => {
  const goto = vi.fn(async () => {});
  await goChat({ goto, on() {}, off() {} }, "chromium", "http://127.0.0.1:1/chats/a");
  expect(goto).toHaveBeenLastCalledWith("http://127.0.0.1:1/chats/a", {
    waitUntil: "load",
    timeout: 30_000,
  });
  await goChat({ goto, on() {}, off() {} }, "firefox", "http://127.0.0.1:1/chats/a");
  expect(goto).toHaveBeenLastCalledWith("http://127.0.0.1:1/chats/a", {
    waitUntil: "domcontentloaded",
    timeout: 10_000,
  });
});
