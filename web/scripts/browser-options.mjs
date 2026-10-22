/* global process, console, navigator, window, document, MutationObserver */
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

// Workbench answers with Cross-Origin-Opener-Policy: same-origin. On that
// first navigation Playwright's Firefox drops the context's colorScheme (the
// engine's page then reports "light"; reproduced against a bare server that
// sends the same header). A real Firefox honours the person's setting, so the
// check states the scheme again once the page is open; it holds from then on.
export async function keepScheme(page, browserName, scheme) {
  if (browserName === "chromium" || !scheme) return;
  await page.emulateMedia({ colorScheme: scheme });
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

// Observe the real result, including Firefox's asynchronous SecurityError.
// The fixture's synchronous probe still handles browsers that throw on access.
export function watchServiceWorkers() {
  window.__serviceWorkers = [];
  try {
    const service = navigator.serviceWorker;
    if (!service) return;
    const register = service.register.bind(service);
    service.register = (...args) => {
      const result = register(...args);
      window.__serviceWorkers.push(
        result.then(
          () => false,
          () => true,
        ),
      );
      return result;
    };
  } catch {
    // Access itself is refused in some opaque-origin frames.
  }
}

// Playwright 1.63's WebKit screenshot synchronization inserts exactly
// <style>body {}</style> from its utility world (empty source, line 5).
// Count that element and match that location; every other refusal stays.
// https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/server/screenshotter.ts
export function watchPolicy(browserName) {
  window.__policy = [];
  window.__screenshotRefusals = 0;
  let screenshotStyles = 0;
  if (browserName === "webkit") {
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node.nodeName === "STYLE" && node.textContent === "body {}") screenshotStyles++;
        }
      }
    }).observe(document, { childList: true, subtree: true });
  }
  document.addEventListener("securitypolicyviolation", (event) => {
    if (
      browserName === "webkit" &&
      event.violatedDirective === "style-src-elem" &&
      event.blockedURI === "inline" &&
      !event.sourceFile &&
      event.lineNumber === 5 &&
      window.__screenshotRefusals < screenshotStyles
    ) {
      window.__screenshotRefusals++;
      return;
    }
    const message = `${event.violatedDirective} ${event.blockedURI} ${event.sourceFile}:${event.lineNumber}`;
    window.__policy.push(message);
    // Stream to the harness too: navigation must not erase a refusal.
    if (window.__reportPolicy) void window.__reportPolicy(message);
  });
}

// Console text lacks the element and source information. The document's
// policy listener above still checks all app stylesheet refusals.
export function screenshotConsoleRefusal(browserName, text) {
  return (
    browserName === "webkit" &&
    text ===
      "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy."
  );
}

// WebKit reports a request the page itself abandons (a chat's own API calls,
// cut off when the next navigation starts) as "... due to access control
// checks", also for the same origin. Only that text, for the app's own /api,
// is ignored; every other page error still fails the run.
export function ignorablePageError(browserName, message) {
  return (
    browserName === "webkit" &&
    /\/\/?127\.0\.0\.1:\d+\/api\/\S* due to access control checks\.$/.test(message)
  );
}

export function canCheckPreviewLoop(browserName) {
  if (browserName === "chromium") return true;
  console.log(
    `SKIP  ${browserName}: a looping preview can block the chat's event loop; Stop and watchdog recovery cannot run there (docs/browsers.md).`,
  );
  return false;
}
