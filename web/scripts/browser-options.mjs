/* global process, console, navigator, window, document, MutationObserver, URL, fetch, AbortSignal */
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

// A new chat opens at its own address and may mount its composer again as the
// page settles (WebKit on Linux lost the text typed into the first one and
// left Send disabled). Fill until the text stays and Send is enabled; an
// enabled Send is what a person waits for too. Say what held it otherwise.
export async function fillComposer(page, text) {
  const composer = page.getByTestId("composer");
  const send = page.getByTestId("send");
  await composer.waitFor();
  for (let attempt = 0; attempt < 20; attempt++) {
    await composer.fill(text).catch(() => {});
    if ((await composer.inputValue().catch(() => "")) === text && (await send.isEnabled())) return;
    await page.waitForTimeout(500);
  }
  const value = await composer.inputValue().catch(() => "");
  throw new Error(
    `the composer kept ${value.length} of ${text.length} characters and Send stayed ${(await send.isEnabled()) ? "enabled" : "disabled"}`,
  );
}

// The sign-in page is only a way in: the next step waits for the app's own
// button. Firefox under load sometimes did not fire "load" for 30 s (once in
// about sixty runs on a busy machine; once on CI), which says nothing about
// what the checks measure. Wait for the document, not for every resource.
//
// On CI, Firefox also let a navigation stand for the full 30 s without the
// page committing (reading and math scripts, four runs of about twenty) while
// the same run's server was fine. Try for 10 s, ask the server whether it
// answers, and try again twice if it does; if it does not, say so. Each retry
// is printed, never silent.
async function visit(page, address, waitUntil, timeout = 10_000) {
  // What the page was still waiting for when it did not open: named in the
  // retry line, so a stall has its observed cause.
  const pending = new Set();
  const started = (request) => pending.add(request.url());
  const ended = (request) => pending.delete(request.url());
  page.on("request", started);
  page.on("requestfinished", ended);
  page.on("requestfailed", ended);
  try {
    return await tryVisit(page, address, waitUntil, timeout, pending);
  } finally {
    page.off("request", started);
    page.off("requestfinished", ended);
    page.off("requestfailed", ended);
  }
}

async function tryVisit(page, address, waitUntil, timeout, pending) {
  for (let attempt = 1; ; attempt++) {
    try {
      await page.goto(address, { waitUntil, timeout });
      return;
    } catch (error) {
      const origin = new URL(address).origin;
      const answered = await fetch(`${origin}/`, { signal: AbortSignal.timeout(5_000) }).then(
        (response) => response.status,
        () => null,
      );
      if (answered === null) {
        throw new Error(`the server at ${origin} did not answer in 5 s: ${error.message}`);
      }
      if (attempt === 3) throw error;
      console.log(
        `RETRY  the page did not open in ${timeout / 1000} s though the server answered ${answered} (try ${attempt} of 3); still waiting for: ${[...pending].map((u) => u.slice(-60)).join(", ") || "nothing"}`,
      );
    }
  }
}

export function goSignedIn(page, address) {
  return visit(page, address, "domcontentloaded");
}

// A chat with pictures, formulas and running previews fires "load" only when
// every one of its frames has loaded; Firefox under load took over 15 s for
// that (a goto timeout on CI) while the page was already usable. Outside
// Chromium, whose "load" has not failed, the checks' own waits (an answer, a
// card, a frame) say when the page is ready.
export function goChat(page, browserName, address) {
  // Chromium keeps its full load and a longer try: its pages have not stalled.
  return browserName === "chromium"
    ? visit(page, address, "load", 30_000)
    : visit(page, address, "domcontentloaded");
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
