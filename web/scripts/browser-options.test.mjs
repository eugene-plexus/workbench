/* global process, navigator, window */
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, afterEach, expect, it, vi } from "vitest";
import { contextOptions, readOptions, watchClipboardWrites } from "./browser-options.mjs";

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
afterEach(() => vi.unstubAllGlobals());

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
