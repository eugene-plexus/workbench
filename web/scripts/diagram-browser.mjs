/* global process, console, URL, document, getComputedStyle, window, Worker, requestAnimationFrame, performance, navigator */
// Mermaid diagrams in the selected browser (workbench-rich-content-roadmap.md RC2), under
// the headers Workbench really sends: what a chat with no diagram loads,
// every diagram drawn or kept as its source with its reason, one that takes
// 20 s stopped while the page keeps drawing, a diagram that tries to run,
// fetch or reconfigure itself, the same diagram twice drawn once, a branch,
// a change of theme, the tools (source, copy, a larger view, downloads) by
// keyboard, a phone, reduced motion, and an answer being written. Each check
// is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import {
  contextOptions,
  launchBrowser,
  readOptions,
  watchClipboardWrites,
} from "./browser-options.mjs";

const { configPath, browserName } = readOptions();
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
const require = createRequire(import.meta.url);
mkdirSync(cfg.shots, { recursive: true });
const shot = (name) => ({ path: `${cfg.shots}/${name}.png`, fullPage: false });
const origin = new URL(cfg.url).origin;
const browser = await launchBrowser(require(cfg.playwright), cfg, browserName);
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
const dialogs = [];
const DIAGRAM_CODE = /diagramWorker|diagramMarkup|diagramDom|mermaid/i;
const REFUSED = /Content Security Policy|Refused to/i;

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext(
    contextOptions(browserName, { colorScheme: scheme, viewport, ...extra }),
  );
  if (browserName !== "chromium") await context.addInitScript(watchClipboardWrites);
  await context.addCookies(cfg.cookies);
  // What the page itself sees: refusals by its policy, the diagrams sent to
  // the worker, and how long the page went between two frames.
  await context.addInitScript(() => {
    window.__policy = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__policy.push(
        `${e.violatedDirective} ${e.blockedURI} ${e.sourceFile}:${e.lineNumber}`,
      ),
    );
    window.__posts = [];
    const post = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message, ...rest) {
      if (message && typeof message.source === "string") window.__posts.push(message.source);
      return post.call(this, message, ...rest);
    };
    window.__gap = 0;
    let last = performance.now();
    const frame = (now) => {
      window.__gap = Math.max(window.__gap, now - last);
      last = now;
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (REFUSED.test(message.text())) policy.push(message.text());
  });
  // The worker's own console: a refusal inside it is reported there.
  page.on("worker", (worker) =>
    worker.on("console", (message) => {
      if (REFUSED.test(message.text())) policy.push(`worker: ${message.text()}`);
    }),
  );
  page.on("dialog", async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  });
  page.on("request", (request) => requests.push(request.url()));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function chat(page, id) {
  await page.goto(`${cfg.url}/chats/${id}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
}

/**
 * Every diagram brought near, so each is drawn: a diagram far from the
 * screen waits until it comes near (MermaidDiagram.tsx).
 */
async function drawAll(page) {
  const slots = page.locator(".diagram-slot");
  for (let i = 0; i < (await slots.count()); i++) {
    await slots.nth(i).scrollIntoViewIfNeeded();
    await page.waitForTimeout(40);
  }
}

/** Every diagram on the page settled: drawn, or kept as its source. */
async function settled(page, timeout = 60000) {
  await drawAll(page);
  await page.waitForFunction(
    () =>
      ![...document.querySelectorAll(".code-note")].some(
        (n) => n.textContent === "Drawing the diagram…",
      ),
    null,
    { timeout },
  );
  await page.waitForTimeout(150);
}

/** The SVG behind each picture in `region`, with what the page names it. */
async function pictures(region) {
  return region.locator(".diagram-view img").evaluateAll((imgs) =>
    imgs.map((img) => {
      const src = img.getAttribute("src") ?? "";
      return {
        alt: img.getAttribute("alt") ?? "",
        src,
        svg: decodeURIComponent(src.slice(src.indexOf(",") + 1)),
        width: img.getBoundingClientRect().width,
      };
    }),
  );
}

/**
 * What a drawing holds that it must not: elements, handlers, addresses. Read
 * here, not in the page: a document the page parses is held to the page's
 * policy, and its style attributes would be counted as refusals.
 */
function hostility(svg) {
  const found = [];
  for (const [, tag, attributes] of svg.matchAll(/<([a-zA-Z][\w:-]*)([^>]*)>/g)) {
    const name = tag.toLowerCase();
    if (["script", "foreignobject", "a", "image", "iframe", "style", "img"].includes(name)) {
      found.push(`<${name}>`);
    }
    for (const [, attribute, value] of attributes.matchAll(/\s([\w:-]+)="([^"]*)"/g)) {
      if (/^on/i.test(attribute)) found.push(`${name} ${attribute}`);
      if (/javascript:|evil\.example|url\((?!\s*#)/i.test(value)) {
        found.push(`${name} ${attribute}=${value.slice(0, 80)}`);
      }
      if (/href$/i.test(attribute) && !value.startsWith("#")) found.push(`${name} ${attribute}`);
    }
    // Every attribute is name="value": nothing else hides in a tag.
    if (
      attributes
        .replace(/\s[\w:-]+="[^"]*"/g, "")
        .replace(/\s*\/?$/, "")
        .trim()
    ) {
      found.push(`${name} has more than attributes`);
    }
  }
  return found;
}

async function pagePolicy(page) {
  return page.evaluate(() => window.__policy);
}

/** Ids used more than once on the page. */
async function duplicateIds(page) {
  return page.evaluate(() => {
    const seen = new Map();
    for (const el of document.querySelectorAll("[id]")) seen.set(el.id, (seen.get(el.id) ?? 0) + 1);
    return [...seen].filter(([, n]) => n > 1).map(([id]) => id);
  });
}

function contrast(fore, back) {
  const rgb = (hex) => {
    const h = hex.replace("#", "");
    const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h.slice(0, 6);
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  };
  const lum = (hex) => {
    const [r, g, b] = rgb(hex).map((v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [lum(fore), lum(back)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/** The page's colours in this theme. */
async function colours(page, names) {
  return page.evaluate((names) => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(names.map((n) => [n, style.getPropertyValue(n).trim()]));
  }, names);
}

/** A PNG's size, from its header. */
function pngSize(buffer) {
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

/** Nothing on the page is wider than the window. */
async function fits(page) {
  return page.evaluate(() => {
    const width = window.innerWidth;
    const wide = [];
    for (const el of document.querySelectorAll(
      ".answer, .answer > *, .diagram-block, .diagram-view, .diagram-view img, .diagram-tools",
    )) {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.right > width + 1) wide.push(el.className || el.tagName);
    }
    return { page: document.documentElement.scrollWidth <= width + 1, wide };
  });
}

const drawnSvg = {};

try {
  // --- a chat with no diagram loads nothing for diagrams ----------------------------
  {
    const { context, page } = await open("dark", DESK);
    await chat(page, cfg.chats.plain);
    await page.waitForTimeout(1000);
    const loaded = requests.filter((url) => DIAGRAM_CODE.test(url));
    check("a chat with no diagram loads no diagram code", loaded.length === 0, loaded.join(", "));
    const response = await page.request.get(`${cfg.url}/chats/${cfg.chats.plain}`);
    check(
      "the page is sent with its policy unchanged",
      response.headers()["content-security-policy"] === cfg.csp,
      response.headers()["content-security-policy"],
    );
    await context.close();
  }

  for (const scheme of ["dark", "light"]) {
    const { context, page } = await open(scheme, DESK, { acceptDownloads: true });
    if (browserName === "chromium") {
      await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
    }
    await chat(page, cfg.chats.diagrams);
    await page.evaluate(() => (window.__gap = 0));
    // While the diagrams are drawn (one of them for 4 s, until it is stopped),
    // the page's own controls answer at once.
    const nav = performance.now();
    await page.getByTestId("new-chat").click();
    await page.getByTestId("composer").waitFor();
    const navMs = performance.now() - nav;
    check(
      `${scheme}: New chat opens at once while diagrams are drawn`,
      navMs < 1000,
      `${Math.round(navMs)} ms`,
    );
    await page.evaluate(() => {
      window.__gap = 0;
      window.__posts = [];
    });
    await chat(page, cfg.chats.diagrams);
    const answers = page.locator("[data-testid=answer]");
    const first = answers.nth(0).locator("[data-md-scope]").last();
    const second = answers.nth(1).locator("[data-md-scope]").last();
    // Opened at its latest answer: the first answer's opening diagram is
    // screens away, and waits until it comes near.
    await page.waitForTimeout(1500);
    const far = await page.evaluate((flowchart) => {
      const slot = document.querySelector(".diagram-slot");
      const scroller = slot?.closest("[data-testid=answer]")?.parentElement;
      return {
        away: slot ? Math.round(slot.getBoundingClientRect().bottom) : null,
        posted: window.__posts.some((p) => p.trim() === flowchart),
        scrolled: scroller ? scroller.scrollTop : null,
      };
    }, cfg.diagrams.flowchart);
    check(
      `${scheme}: a diagram screens away is not drawn until it comes near`,
      far.away !== null && far.away < -DESK.height && !far.posted,
      JSON.stringify(far),
    );
    await settled(page);
    const gap = await page.evaluate(() => window.__gap);
    const posts = await page.evaluate(() => window.__posts);

    // --- every diagram drawn, or its source with the reason ---------------------------
    const drawn = await pictures(first);
    check(
      `${scheme}: every diagram in the first answer is drawn, named by its kind`,
      drawn.length === cfg.diagrams.kinds.length &&
        drawn.every((d, i) => d.alt.startsWith(cfg.diagrams.kinds[i])),
      JSON.stringify(drawn.map((d) => d.alt)),
    );
    drawnSvg[scheme] = drawn[0]?.src;
    const notes = await first
      .locator(".code-block .code-note")
      .evaluateAll((els) => els.map((el) => el.textContent ?? ""));
    check(
      `${scheme}: a diagram that cannot be drawn is its source, saying why`,
      notes.length === cfg.diagrams.failed.length &&
        notes.every((note, i) => note.startsWith(cfg.diagrams.failed[i])),
      JSON.stringify(notes),
    );
    const brokenSource = await first.locator(".code-block:has(.code-note) pre").first().innerText();
    check(
      `${scheme}: its source is shown as written`,
      brokenSource.includes("A -->"),
      brokenSource,
    );
    check(
      `${scheme}: plain code stays code`,
      (await first.locator("pre code.language-python").count()) === 1,
    );
    const flowPosts = posts.filter((p) => p.trim() === cfg.diagrams.flowchart).length;
    check(
      `${scheme}: the same diagram twice, and in another answer, is drawn once`,
      flowPosts === 1,
      `${flowPosts} times of ${posts.length}: ${JSON.stringify(posts.map((p) => p.slice(0, 30)))}`,
    );

    // --- a diagram that tries too much ------------------------------------------------
    const hostile = drawn[6];
    const found = hostile ? hostility(hostile.svg) : ["not drawn"];
    check(
      `${scheme}: a diagram's links, handlers, HTML and outside addresses are gone`,
      found.length === 0,
      found.join(", "),
    );
    check(
      `${scheme}: what the label said is its text`,
      !!hostile && hostile.svg.includes("onclick=") && hostile.svg.includes("&lt;img src="),
    );
    for (const d of drawn) {
      const bad = hostility(d.svg);
      if (bad.length) check(`${scheme}: ${d.alt} holds nothing it must not`, false, bad.join(", "));
    }

    // --- one that takes too long, stopped ------------------------------------------------
    const secondNotes = await second
      .locator(".code-note")
      .evaluateAll((els) => els.map((el) => el.textContent ?? ""));
    check(
      `${scheme}: a diagram that takes too long is stopped and says so`,
      secondNotes.includes("Diagram not shown: it took longer than 4 s to draw"),
      JSON.stringify(secondNotes),
    );
    check(
      `${scheme}: the diagram after a stopped one is drawn`,
      (await pictures(second)).some((d) => d.alt.startsWith("Entity relationship diagram")),
    );
    check(
      `${scheme}: the page kept drawing while a diagram was stopped`,
      gap < 250,
      `${Math.round(gap)} ms between frames`,
    );
    check(`${scheme}: no id is used twice on the page`, (await duplicateIds(page)).length === 0);

    // --- the other branch -----------------------------------------------------------------
    await answers.nth(1).getByRole("button", { name: "Previous version of this answer" }).click();
    await page.getByText("The first try, with a pie chart").waitFor();
    await settled(page);
    const firstTry = answers.nth(1).locator("[data-md-scope]").last();
    const branch = await pictures(firstTry);
    check(
      `${scheme}: the other branch draws its diagrams`,
      branch.length === 2 &&
        branch[0].alt.startsWith("Pie chart") &&
        branch[1].alt.startsWith("Flowchart"),
      JSON.stringify(branch.map((d) => d.alt)),
    );
    const flowAgain = (await page.evaluate(() => window.__posts)).filter(
      (p) => p.trim() === cfg.diagrams.flowchart,
    ).length;
    check(`${scheme}: switching branches draws nothing twice`, flowAgain === 1, `${flowAgain}`);
    check(
      `${scheme}: no id is used twice with the other branch shown`,
      (await duplicateIds(page)).length === 0,
    );
    await answers.nth(1).getByRole("button", { name: "Next version of this answer" }).click();
    await page.getByText("The second try").waitFor();
    await settled(page);

    // --- the tools, by keyboard ------------------------------------------------------------
    const block = first.locator(".diagram-block").first();
    const sourceButton = block.getByRole("button", { name: "Source", exact: true });
    await sourceButton.focus();
    await page.keyboard.press("Enter");
    const sourceShown = await block.locator("pre code").innerText();
    check(
      `${scheme}: Source shows the diagram's source, by keyboard`,
      sourceShown.trim() === cfg.diagrams.flowchart &&
        (await sourceButton.getAttribute("aria-pressed")) === "true",
      sourceShown.slice(0, 80),
    );
    await page.keyboard.press("Enter");
    check(
      `${scheme}: and the diagram again`,
      (await block.locator(".diagram-view img").count()) === 1,
    );
    await block.getByRole("button", { name: "Copy the diagram's source" }).click();
    if (browserName !== "chromium") {
      await page.waitForFunction(() => typeof window.__copied === "string");
    }
    const copied = await page.evaluate(
      (name) => (name === "chromium" ? navigator.clipboard.readText() : window.__copied),
      browserName,
    );
    // Windows' clipboard ends its lines in \r\n.
    check(
      `${scheme}: Copy copies the source`,
      copied.replace(/\r\n/g, "\n").trim() === cfg.diagrams.flowchart,
      JSON.stringify(copied),
    );

    const expand = block.getByRole("button", { name: /^Expand the / });
    await expand.focus();
    await page.keyboard.press("Enter");
    const dialog = page.locator("dialog.diagram-dialog");
    await dialog.waitFor();
    const opened = await page.evaluate(() => ({
      modal: document.querySelector("dialog.diagram-dialog")?.matches(":modal") ?? false,
      focus: document.activeElement?.textContent ?? "",
      animations: document.getAnimations().length,
    }));
    check(
      `${scheme}: Expand opens the diagram on its own, by keyboard, with Close first`,
      opened.modal && opened.focus.includes("Close"),
      JSON.stringify(opened),
    );
    const before = await dialog.locator("img").evaluate((img) => img.width);
    await dialog.getByRole("button", { name: "Zoom in" }).click();
    const after = await dialog.locator("img").evaluate((img) => img.width);
    check(`${scheme}: Zoom in makes it larger`, after > before, `${before} -> ${after}`);
    await page.screenshot(shot(`diagram-${scheme}-dialog`));
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    const back = await page.evaluate(
      () => document.activeElement?.getAttribute("aria-label") ?? "",
    );
    check(`${scheme}: Escape closes it and returns to Expand`, /^Expand the /.test(back), back);

    const [svgDownload] = await Promise.all([
      page.waitForEvent("download"),
      block.getByRole("button", { name: "Download the diagram as SVG" }).click(),
    ]);
    const svgFile = readFileSync(await svgDownload.path(), "utf8");
    const svgFound = hostility(svgFile);
    check(
      `${scheme}: SVG downloads the drawing, and nothing it must not hold`,
      svgDownload.suggestedFilename().endsWith(".svg") &&
        svgFile.startsWith('<svg xmlns="http://www.w3.org/2000/svg"') &&
        svgFound.length === 0,
      `${svgDownload.suggestedFilename()} ${svgFound.join(", ")}`,
    );
    const [pngDownload] = await Promise.all([
      page.waitForEvent("download"),
      block.getByRole("button", { name: "Download the diagram as PNG" }).click(),
    ]);
    const png = pngSize(readFileSync(await pngDownload.path()));
    const natural = await block.locator(".diagram-view img").evaluate((img) => ({
      width: Number(img.getAttribute("width")),
      height: Number(img.getAttribute("height")),
    }));
    check(
      `${scheme}: PNG downloads at twice the diagram's size`,
      Math.abs(png.width - Math.floor(natural.width * 2)) <= 1 &&
        Math.abs(png.height - Math.floor(natural.height * 2)) <= 1,
      `${png.width} x ${png.height} for ${natural.width} x ${natural.height}`,
    );
    const wideBlock = first.locator(".diagram-block").nth(5);
    const [widePng] = await Promise.all([
      page.waitForEvent("download"),
      wideBlock.getByRole("button", { name: "Download the diagram as PNG" }).click(),
    ]);
    const wide = pngSize(readFileSync(await widePng.path()));
    check(
      `${scheme}: a PNG is at most 4,096 pixels a side`,
      Math.max(wide.width, wide.height) === 4096,
      `${wide.width} x ${wide.height}`,
    );

    // --- legible in this theme ----------------------------------------------------------------
    // The pairs diagramClient.ts's palette puts text on (lib/diagramClient.ts).
    const sections = [
      "--status-info-border",
      "--status-ok-border",
      "--status-warn-border",
      "--status-error-border",
      "--tint-edge",
    ];
    const slices = [
      "--accent-left",
      "--accent-right",
      "--status-ok-fg",
      "--status-warn-fg",
      "--status-error-fg",
      "--heading",
      "--accent-active",
      "--muted",
    ];
    const c = await colours(page, [
      "--tint",
      "--foreground-strong",
      "--panel",
      "--foreground",
      "--on-accent-left",
      "--panel-soft",
      ...sections,
      ...slices,
    ]);
    const pairs = [
      ["a box's text", c["--foreground-strong"], c["--tint"]],
      ["a line's label", c["--foreground"], c["--panel"]],
      ["a note's text", c["--foreground-strong"], c["--panel-soft"]],
      ...sections.map((s) => [`a section's text on ${s}`, c["--foreground-strong"], c[s]]),
      ...slices.map((s) => [`a pie slice's text on ${s}`, c["--on-accent-left"], c[s]]),
    ];
    const low = pairs
      .map(([name, fore, back]) => [name, contrast(fore, back)])
      .filter(([, ratio]) => ratio < 4.5);
    check(
      `${scheme}: a diagram's text reads at least 4.5:1`,
      low.length === 0,
      JSON.stringify(low),
    );
    const inPicture = drawn[0]?.svg ?? "";
    check(
      `${scheme}: the picture is in this theme's colours`,
      inPicture.toLowerCase().includes(c["--tint"].toLowerCase()) &&
        inPicture.toLowerCase().includes(c["--panel"].toLowerCase()),
    );
    await first.locator(".diagram-block").first().scrollIntoViewIfNeeded();
    await page.screenshot(shot(`diagram-${scheme}-desk`));
    for (const [i, name] of [
      [1, "sequence"],
      [2, "class"],
      [3, "state"],
      [5, "wide"],
      [6, "hostile"],
    ]) {
      await first
        .locator(".diagram-block")
        .nth(i)
        .screenshot({ path: `${cfg.shots}/diagram-${scheme}-${name}.png` });
    }
    await second.screenshot({ path: `${cfg.shots}/diagram-${scheme}-second.png` });
    check(
      `${scheme}: the page's policy refused nothing`,
      (await pagePolicy(page)).length === 0,
      JSON.stringify(await pagePolicy(page)),
    );
    // --- every other kind drawn here ----------------------------------------------------------
    await chat(page, cfg.chats.gallery);
    await settled(page);
    const galleryAnswer = answers.nth(0).locator("[data-md-scope]").last();
    const gallery = await pictures(galleryAnswer);
    check(
      `${scheme}: every other kind Workbench draws is drawn, named by its kind`,
      gallery.length === cfg.diagrams.gallery.length &&
        gallery.every((d, i) => d.alt.startsWith(cfg.diagrams.gallery[i])),
      JSON.stringify(gallery.map((d) => d.alt)),
    );
    const galleryFound = gallery.flatMap((d) => hostility(d.svg));
    check(
      `${scheme}: and holds nothing it must not`,
      galleryFound.length === 0,
      galleryFound.join(", "),
    );
    const blocks = galleryAnswer.locator(".diagram-block");
    for (let i = 0; i < (await blocks.count()); i++) {
      await blocks.nth(i).screenshot({ path: `${cfg.shots}/diagram-${scheme}-kind-${i}.png` });
    }
    check(
      `${scheme}: the page's policy refused nothing for them either`,
      (await pagePolicy(page)).length === 0,
      JSON.stringify(await pagePolicy(page)),
    );
    await context.close();

    // --- a phone, with reduced motion ------------------------------------------------------------
    const small = await open(scheme, PHONE, {
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 3,
      reducedMotion: "reduce",
    });
    await chat(small.page, cfg.chats.diagrams);
    await settled(small.page);
    const fit = await fits(small.page);
    check(
      `${scheme} phone: nothing is wider than the screen`,
      fit.page && fit.wide.length === 0,
      JSON.stringify(fit.wide),
    );
    const phoneFirst = small.page
      .locator("[data-testid=answer]")
      .nth(0)
      .locator("[data-md-scope]")
      .last();
    const wideOnPhone = phoneFirst.locator(".diagram-view img").nth(5);
    const shrunk = await wideOnPhone.evaluate((img) => ({
      shown: img.getBoundingClientRect().width,
      natural: img.naturalWidth,
    }));
    check(
      `${scheme} phone: a wide diagram fits the column, and Expand shows it at size`,
      shrunk.shown < shrunk.natural && shrunk.shown <= PHONE.width,
      JSON.stringify(shrunk),
    );
    // One row: the name and every tool share a middle line.
    const rows = await phoneFirst.locator(".diagram-block .code-head").evaluateAll((heads) =>
      heads.map((head) => {
        const parts = [head.querySelector(".code-lang"), ...head.querySelectorAll("button")];
        return new Set(
          parts.map((p) => {
            const box = p.getBoundingClientRect();
            return Math.round((box.top + box.bottom) / 8);
          }),
        ).size;
      }),
    );
    check(
      `${scheme} phone: a diagram's name and tools fit one row`,
      rows.length > 0 && rows.every((n) => n === 1),
      JSON.stringify(rows),
    );
    await phoneFirst.locator(".diagram-block").nth(5).scrollIntoViewIfNeeded();
    await small.page.screenshot(shot(`diagram-${scheme}-phone`));
    await phoneFirst
      .locator(".diagram-block")
      .nth(5)
      .getByRole("button", { name: /^Expand the / })
      .click();
    const phoneDialog = small.page.locator("dialog.diagram-dialog");
    await phoneDialog.waitFor();
    const filled = await phoneDialog.evaluate((d) => {
      const box = d.getBoundingClientRect();
      return { width: box.width, height: box.height, animations: document.getAnimations().length };
    });
    check(
      `${scheme} phone: the larger view fills the screen, and nothing moves`,
      Math.abs(filled.width - PHONE.width) <= 1 && filled.animations === 0,
      JSON.stringify(filled),
    );
    await small.page.screenshot(shot(`diagram-${scheme}-phone-dialog`));
    await small.context.close();
  }

  check(
    "a theme's pictures are its own",
    !!drawnSvg.dark && !!drawnSvg.light && drawnSvg.dark !== drawnSvg.light,
  );

  // --- a change of theme draws again --------------------------------------------------------
  {
    const { context, page } = await open("dark", DESK);
    await chat(page, cfg.chats.diagrams);
    await settled(page);
    const darkSrc = await page.locator(".diagram-view img").first().getAttribute("src");
    await page.emulateMedia({ colorScheme: "light" });
    await page.waitForFunction(
      (old) => {
        const img = document.querySelector(".diagram-view img");
        return img && img.getAttribute("src") !== old;
      },
      darkSrc,
      { timeout: 30000 },
    );
    const lightSrc = await page.locator(".diagram-view img").first().getAttribute("src");
    check("a change of theme draws the diagram in the new colours", lightSrc === drawnSvg.light);
    await context.close();
  }

  // --- an answer being written ---------------------------------------------------------------
  {
    const { context, page } = await open("dark", DESK);
    await page.getByTestId("new-chat").click();
    const composer = page.getByTestId("composer");
    await composer.waitFor();
    await composer.fill(cfg.live.ask);
    await page.evaluate(() => (window.__posts = []));
    await page.getByTestId("send").click();
    const running = page.locator('[data-testid=answer][data-status="running"]');
    await running.waitFor();
    const seen = { open: false, drawnWhileOpen: false };
    for (let i = 0; i < 400; i++) {
      const look = await page.evaluate(() => {
        const answer = document.querySelector('[data-testid=answer][data-status="running"]');
        if (!answer) return null;
        const text = answer.textContent ?? "";
        const fenceOpen = text.includes("a[Write]") && !text.includes("Done.");
        return {
          open:
            fenceOpen &&
            !answer.querySelector(".diagram-view img") &&
            !!answer.querySelector("pre code"),
          drawnWhileOpen:
            fenceOpen && !text.includes("c[Show]") && !!answer.querySelector(".diagram-view img"),
        };
      });
      if (!look) break;
      for (const key of Object.keys(seen)) seen[key] ||= look[key];
      await page.waitForTimeout(40);
    }
    await page
      .locator('[data-testid=answer]:not([data-status="running"])')
      .last()
      .waitFor({ timeout: 30000 });
    await settled(page);
    const live = page.locator("[data-testid=answer]").last();
    check("live: a diagram not yet closed shows as its source", seen.open);
    check("live: a diagram is not drawn before its block closes", !seen.drawnWhileOpen);
    check(
      "live: the diagram is drawn once written",
      (await live.locator(".diagram-view img").count()) === 1,
    );
    const livePosts = await page.evaluate(() => window.__posts);
    check(
      "live: the diagram is drawn once, not on every chunk",
      JSON.stringify(livePosts.map((p) => p.trim())) === JSON.stringify([cfg.live.source]),
      JSON.stringify(livePosts),
    );
    check(
      "live: the page's policy refused nothing",
      (await pagePolicy(page)).length === 0,
      JSON.stringify(await pagePolicy(page)),
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
check("no diagram opened a dialog of its own", dialogs.length === 0, dialogs.join(" | "));
check(
  "diagram code was loaded once diagrams were shown",
  requests.some((url) => /diagramWorker/.test(url)),
);
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
