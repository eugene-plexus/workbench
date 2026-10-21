/* global process, console, navigator, window */
import { parseArgs } from "node:util";

// Usage: node <check>-browser.mjs config.json [--browser chromium|firefox|webkit].
// The environment option lets the existing pytest fixtures keep starting the
// real servers and writing their configs; a command-line option wins over it.
export function readOptions(args = process.argv.slice(2), env = process.env) {
  const { values, positionals } = parseArgs({
    args,
    allowPositionals: true,
    options: { browser: { type: "string", default: env.WORKBENCH_BROWSER ?? "chromium" } },
  });
  if (!["chromium", "firefox", "webkit"].includes(values.browser)) {
    throw new Error("--browser must be chromium, firefox or webkit");
  }
  if (positionals.length !== 1) throw new Error("Expected one browser config.json path");
  return { configPath: positionals[0], browserName: values.browser };
}

export async function launchBrowser(playwright, cfg, browserName) {
  // An existing Chromium run still uses its configured system Chrome. The
  // other engines must use Playwright's own executable, never cfg.chrome.
  const options = { headless: true };
  if (browserName === "chromium" && cfg.chrome) options.executablePath = cfg.chrome;
  const browser = await playwright[browserName].launch(options);
  console.log(`Browser: ${browserName} ${browser.version()}`);
  return browser;
}

export function contextOptions(browserName, options) {
  const result = { ...options };
  // Firefox supports a phone-sized viewport and touch, but Playwright rejects
  // its isMobile option. Keep every other part of the phone check.
  if (browserName === "firefox") delete result.isMobile;
  return result;
}

// Firefox and WebKit cannot grant Chromium's clipboard-read permission. Observe
// the native write instead: a refusal still rejects and fails the Copy check.
// This function is serialized by Playwright's addInitScript, so is self-contained.
export function watchClipboardWrites() {
  const write = navigator.clipboard.writeText.bind(navigator.clipboard);
  navigator.clipboard.writeText = async (text) => {
    await write(text);
    window.__copied = text;
  };
}
