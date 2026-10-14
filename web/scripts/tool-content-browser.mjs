/* global process, console, URL, document, window, getComputedStyle, Image */
// A tool's pictures, sounds and files in Chrome (workbench-rich-content-
// roadmap.md RC3), under the headers Workbench really sends: a mixed result
// shown after a fresh load, a sound loaded only when asked and never played
// by itself, a page that said it was a picture offered only as a download, an
// address named but never fetched, both themes and a phone, and every file
// address let go when the person signs out. Each check is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
mkdirSync(cfg.shots, { recursive: true });
const origin = new URL(cfg.url).origin;
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}

const errors = [];
const policy = [];
const requests = [];
const REFUSED = /Content Security Policy|Refused to/i;

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext({
    colorScheme: scheme,
    viewport,
    acceptDownloads: true,
    ...extra,
  });
  await context.addCookies(cfg.cookies);
  await context.addInitScript(() => {
    window.__policy = [];
    // Every blob: address the page makes, in order.
    window.__made = [];
    const make = URL.createObjectURL;
    URL.createObjectURL = function (...args) {
      const url = make.apply(this, args);
      window.__made.push(url);
      return url;
    };
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__policy.push(`${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (REFUSED.test(message.text())) policy.push(message.text());
  });
  page.on("request", (request) => requests.push(request.url()));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  await page.goto(`${cfg.url}/chats/${cfg.chat}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
  return { context, page };
}

function contrast(fore, back) {
  const lum = ([r, g, b]) => {
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const [hi, lo] = [lum(fore), lum(back)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/** The colour of an element's text and of the first solid colour behind it. */
async function colours(locator) {
  return locator.evaluate((el) => {
    const parse = (c) => {
      const m = /rgba?\(([^)]+)\)/.exec(c || "");
      if (!m) return null;
      const p = m[1]
        .split(/[\s,/]+/)
        .filter(Boolean)
        .map(Number);
      return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
    };
    let back = [255, 255, 255];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a >= 1) {
        back = c.rgb;
        break;
      }
    }
    return { fore: parse(getComputedStyle(el).color).rgb, back };
  });
}

/** Whether a blob: address still shows: loaded as an image (the policy lets
 * an image come from blob:, and refuses a fetch of one). */
async function shows(page, src) {
  return page.evaluate(
    (src) =>
      new Promise((done) => {
        const img = new Image();
        img.onload = () => done("shows");
        img.onerror = () => done("gone");
        img.src = src;
      }),
    src,
  );
}

try {
  for (const scheme of ["dark", "light"]) {
    const mark = requests.length;
    const { context, page } = await open(scheme, DESK);
    const content = page.getByTestId("tool-content");
    await content.waitFor();
    const picture = page.getByRole("img", { name: cfg.picture.name });
    await picture.waitFor();
    const drawn = await picture.evaluate((img) => ({
      loaded: img.complete && img.naturalWidth > 0,
      src: img.getAttribute("src"),
    }));
    check(
      `${scheme}: the tool's picture is shown after a fresh load, from Workbench`,
      drawn.loaded && drawn.src.startsWith("blob:"),
      JSON.stringify(drawn),
    );
    const soundUrl = `/api/files/${encodeURIComponent(cfg.sound.id)}`;
    check(
      `${scheme}: a sound is not fetched until asked`,
      !requests.slice(mark).some((url) => url.includes(soundUrl)),
    );
    await page.getByRole("button", { name: `Load the sound ${cfg.sound.name}` }).click();
    const audio = page.locator("audio");
    await audio.waitFor();
    await page.waitForTimeout(300);
    const playing = await audio.evaluate((a) => ({
      controls: a.controls,
      paused: a.paused,
      autoplay: a.autoplay,
    }));
    check(
      `${scheme}: asked for, it has its controls and does not play by itself`,
      playing.controls && playing.paused && !playing.autoplay,
      JSON.stringify(playing),
    );
    const card = page.getByTestId("tool-file");
    const words = await card.innerText();
    check(
      `${scheme}: a page that said it was a picture is a file, saying what it claimed`,
      words.includes(cfg.page.name) &&
        words.includes("said to be text/html") &&
        (await card.locator("img, iframe, object, embed").count()) === 0,
      words,
    );
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: `Download ${cfg.page.name}` }).click(),
    ]);
    const saved = readFileSync(await download.path(), "utf8");
    check(
      `${scheme}: it downloads under its own name, as the bytes it is`,
      download.suggestedFilename() === cfg.page.name && saved === cfg.page.bytes,
      `${download.suggestedFilename()} ${saved.slice(0, 40)}`,
    );
    const link = page.getByRole("link", { name: "elsewhere" });
    check(
      `${scheme}: an address the tool named is a link that goes nowhere by itself`,
      (await link.getAttribute("rel")) === "noopener noreferrer" &&
        !requests.some((url) => url.includes("example.com")),
    );
    const name = card.locator(".text-muted").first();
    const c = await colours(name);
    const ratio = contrast(c.fore, c.back);
    check(`${scheme}: a file's words read at least 4.5:1`, ratio >= 4.5, ratio.toFixed(2));
    await content.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${cfg.shots}/tool-content-${scheme}-desk.png` });
    const refused = await page.evaluate(() => window.__policy);
    check(`${scheme}: the page's policy refused nothing`, refused.length === 0, refused.join(", "));

    await context.close();
  }

  // --- a phone ---------------------------------------------------------------------------------
  {
    const { context, page } = await open("light", PHONE, {
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 3,
    });
    const content = page.getByTestId("tool-content");
    await content.waitFor();
    await page.getByRole("img", { name: cfg.picture.name }).waitFor();
    await content.scrollIntoViewIfNeeded();
    const fit = await page.evaluate(() => {
      const wide = [];
      for (const el of document.querySelectorAll(
        "[data-testid=tool-content], [data-testid=tool-content] *",
      )) {
        const box = el.getBoundingClientRect();
        if (box.width > 0 && box.right > window.innerWidth + 1) {
          wide.push(el.className || el.tagName);
        }
      }
      return { page: document.documentElement.scrollWidth <= window.innerWidth + 1, wide };
    });
    check(
      "phone: nothing the tool returned is wider than the screen",
      fit.page && fit.wide.length === 0,
      JSON.stringify(fit.wide),
    );
    await page.screenshot({ path: `${cfg.shots}/tool-content-light-phone.png` });
    await context.close();
  }
  // --- signing out lets every file go (last: it ends the session) -------------------------
  {
    const { context, page } = await open("dark", DESK);
    const picture = page.getByRole("img", { name: cfg.picture.name });
    await picture.waitFor();
    const src = await picture.getAttribute("src");
    await page.waitForFunction((src) => document.querySelector(`img[src="${src}"]`)?.complete, src);
    const before = await shows(page, src);
    // A download's address is kept 10 s for the download, by no view: only
    // signing out itself can let it go sooner (App.tsx).
    await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: `Download ${cfg.picture.name}` }).click(),
    ]);
    const saved = await page.evaluate(() => window.__made.at(-1));
    const savedBefore = await shows(page, saved);
    await page.getByRole("button", { name: /Sign out/ }).click();
    await page.getByText("You signed out.").waitFor();
    const after = await shows(page, src);
    const savedAfter = await shows(page, saved);
    check(
      "signing out lets go of the picture's address, so it cannot be shown again",
      before === "shows" && after === "gone",
      `${before} -> ${after}`,
    );
    check(
      "and of a download's address, which no view holds",
      saved !== src && savedBefore === "shows" && savedAfter === "gone",
      `${savedBefore} -> ${savedAfter}`,
    );
    await context.close();
  }
} finally {
  await browser.close();
}

const elsewhere = requests.filter(
  (url) => !url.startsWith(origin) && !url.startsWith("data:") && !url.startsWith("blob:"),
);
check("every request went to Workbench itself", elsewhere.length === 0, elsewhere.join(", "));
check("the browser reported no refusal by the policy", policy.length === 0, policy.join(" | "));
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
