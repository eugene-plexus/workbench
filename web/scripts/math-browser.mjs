/* global process, console, URL, document, getComputedStyle, window, Worker, requestAnimationFrame, performance, location */
// Formulas and footnotes in the selected browser (workbench-rich-content-roadmap.md RC1),
// under the headers Workbench really sends: what a chat with no formula
// loads, every formula drawn or kept as its source with its reason, a
// formula that takes a minute stopped while the page keeps drawing, the
// policy unbroken and every request to Workbench itself, footnotes by
// keyboard in two answers, a branch and a draft, an answer being written,
// both themes, a phone and 200% zoom. Each check is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import {
  contextOptions,
  fillComposer,
  goChat,
  goSignedIn,
  keepScheme,
  ignorablePageError,
  launchBrowser,
  readOptions,
  watchPolicy,
  screenshotConsoleRefusal,
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
// 200% zoom of a 1440 x 900 window: half the CSS pixels, each drawn twice.
const ZOOM = { width: 720, height: 450 };

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}

const errors = [];
const policy = [];
const requests = [];
const FORMULA_CODE = /mathView|mathWorker|KaTeX_/;

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext(
    contextOptions(browserName, { colorScheme: scheme, viewport, ...extra }),
  );
  await context.addCookies(cfg.cookies);
  // What the page itself sees: refusals by its policy, the formulas sent to
  // the worker, and how long the page went between two frames.
  await context.addInitScript(() => {
    window.__posts = [];
    const post = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message, ...rest) {
      window.__posts.push(message && message.tex);
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
  await context.exposeBinding("__reportPolicy", ({ frame }, message) => {
    if (!frame.parentFrame()) policy.push(message);
  });
  await context.addInitScript(watchPolicy, browserName);
  const page = await context.newPage();
  page.on("pageerror", (error) => {
    if (!ignorablePageError(browserName, error.message)) errors.push(error.message);
  });
  page.on("console", (message) => {
    if (
      /Content Security Policy|Refused to/i.test(message.text()) &&
      !screenshotConsoleRefusal(browserName, message.text())
    )
      policy.push(message.text());
  });
  page.on("request", (request) => requests.push(request.url()));
  await goSignedIn(page, `${cfg.url}/#signin=${cfg.secret}`);
  await keepScheme(page, browserName, scheme);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  const close = context.close.bind(context);
  context.close = async () => {
    const snapshot = await page.evaluate(() => ({
      screenshots: window.__screenshotRefusals,
    }));
    if (snapshot.screenshots)
      console.log(
        `SKIP  WebKit: ${snapshot.screenshots} Playwright screenshot body {} stylesheet refusals; app CSP remains checked.`,
      );
    await close();
  };
  return { context, page };
}

async function chat(page, id) {
  await goChat(page, browserName, `${cfg.url}/chats/${id}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
}

/** Every formula on the page settled: drawn, or kept as its source. */
async function settled(page, timeout = 20000) {
  await page.waitForFunction(() => document.querySelectorAll(".math-pending").length === 0, null, {
    timeout,
  });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
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

/**
 * Footnote `n` of the answer at `index`: focus its reference, Enter, then the
 * way back and Enter. Where focus went each time, and whether the address
 * stayed.
 */
async function footnoteTrip(page, region, n) {
  const before = await page.evaluate(() => location.href);
  const ref = region.locator(`a[data-footnote-ref]`).nth(n - 1);
  await ref.focus();
  await page.keyboard.press("Enter");
  const there = await page.evaluate(() => {
    const el = document.activeElement;
    return {
      tag: el?.tagName,
      text: el?.textContent ?? "",
      scope: el?.closest("[data-md-scope]")?.getAttribute("data-md-scope") ?? null,
    };
  });
  await page.keyboard.press("Tab");
  const back = await page.evaluate(() => document.activeElement?.getAttribute("aria-label"));
  await page.keyboard.press("Enter");
  const home = await page.evaluate(() => ({
    ref: document.activeElement?.hasAttribute("data-footnote-ref") ?? false,
    text: document.activeElement?.textContent ?? "",
  }));
  const after = await page.evaluate(() => location.href);
  const scope = await region.getAttribute("data-md-scope");
  return { there, back, home, sameScope: there.scope === scope, addressKept: before === after };
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

/** The colour of a drawn formula and of what is behind it. */
async function formulaColours(page, selector) {
  return page.evaluate((selector) => {
    const parse = (c) => {
      const m = /rgba?\(([^)]+)\)/.exec(c || "");
      if (!m) return null;
      const p = m[1]
        .split(/[\s,/]+/)
        .filter(Boolean)
        .map(Number);
      return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
    };
    const el = document.querySelector(selector);
    if (!el) return null;
    let back = [255, 255, 255];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a >= 1) {
        back = c.rgb;
        break;
      }
    }
    return { fore: parse(getComputedStyle(el).color).rgb, back };
  }, selector);
}

/** Nothing on the page is wider than the window. */
async function fits(page) {
  return page.evaluate(() => {
    const width = window.innerWidth;
    const wide = [];
    for (const el of document.querySelectorAll(".answer, .answer > *, .math-display")) {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.right > width + 1) wide.push(el.className || el.tagName);
    }
    // The scrollers that hold the answers (the chat's own) never scroll
    // sideways; only a box inside an answer (code, a table, a formula) may.
    // (The top bar's links scroll sideways on a phone by design.)
    for (const el of document.querySelectorAll("*")) {
      if (el.closest(".answer") || !el.querySelector(".answer")) continue;
      const style = getComputedStyle(el);
      if (/(auto|scroll)/.test(style.overflowX) && el.scrollWidth > el.clientWidth + 1) {
        wide.push(`${el.tagName.toLowerCase()} scrolls sideways`);
      }
    }
    return { page: document.documentElement.scrollWidth <= width + 1, wide };
  });
}

try {
  // --- a chat with no formula loads nothing for formulas --------------------
  {
    const { context, page } = await open("dark", DESK);
    await chat(page, cfg.chats.plain);
    await page.waitForTimeout(1000);
    const loaded = requests.filter((url) => FORMULA_CODE.test(url));
    check(
      "a chat with no formula loads no formula code, style or font",
      loaded.length === 0,
      loaded.join(", "),
    );
    const response = await page.request.get(`${cfg.url}/chats/${cfg.chats.plain}`);
    check(
      "the page is sent with its policy unchanged",
      response.headers()["content-security-policy"] === cfg.csp,
      response.headers()["content-security-policy"],
    );
    await context.close();
  }

  for (const scheme of ["dark", "light"]) {
    const { context, page } = await open(scheme, DESK);
    // A real macro's cost varies by engine: WebKit can reach MAX_MARKUP
    // before 1.5 s. Stall only that fixture job in the actual module worker,
    // so the deadline, termination and following jobs are proved everywhere.
    await context.route("**/mathWorker-*.js", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        body:
          'self.addEventListener("message", (event) => { if (event.data.tex?.startsWith("\\\\def\\\\a{")) { for (;;) {} } });\n' +
          (await response.text()),
      });
    });
    await chat(page, cfg.chats.math);
    await page.evaluate(() => (window.__gap = 0));
    // While the formulas are laid out (one of them for 1.5 s, until it is
    // stopped), the page's own controls answer at once.
    const nav = performance.now();
    await page.getByTestId("new-chat").click();
    await page.getByTestId("composer").waitFor();
    const navMs = performance.now() - nav;
    check(
      `${scheme}: New chat opens at once while formulas are laid out`,
      navMs < 1000,
      `${Math.round(navMs)} ms`,
    );
    await chat(page, cfg.chats.math);
    const answers = page.locator("[data-testid=answer]");
    const first = answers.nth(0).locator("[data-md-scope]").last();
    const second = answers.nth(1).locator("[data-md-scope]").last();
    await settled(page, 30000);
    const gap = await page.evaluate(() => window.__gap);
    const faces = await page.evaluate(() =>
      [...document.fonts]
        .filter((face) => face.family.replace(/["']/g, "").startsWith("KaTeX_"))
        .map((face) => ({ family: face.family, display: face.display, status: face.status })),
    );
    check(
      `${scheme}: KaTeX blocks fallback text while its own fonts load`,
      faces.length > 0 &&
        faces.every((face) => face.display === "block") &&
        faces.some((face) => face.status === "loaded"),
      JSON.stringify(faces),
    );

    // --- every formula drawn, or its source with the reason --------------------
    const drawn = await first.locator(".katex").count();
    check(
      `${scheme}: every formula in the first answer is drawn`,
      drawn === cfg.math.drawn,
      `${drawn} of ${cfg.math.drawn}`,
    );
    const mathml = await first.locator(".katex math annotation").count();
    check(
      `${scheme}: each drawn formula carries MathML and its source`,
      mathml === cfg.math.drawn,
      `${mathml}`,
    );
    const failed = await first.locator("code.math-failed").evaluateAll((els) =>
      els.map((el) => ({
        text: el.firstChild?.textContent ?? "",
        title: el.getAttribute("title") ?? "",
      })),
    );
    check(
      `${scheme}: a formula that cannot be shown is its source, saying why`,
      JSON.stringify(failed.map((f) => f.text)) === JSON.stringify(cfg.math.failed) &&
        failed.every((f) => f.title.startsWith("Formula not shown: ")) &&
        failed.some((f) => f.title.includes("Undefined control sequence: \\nope")),
      JSON.stringify(failed),
    );
    const prose = await first.innerText();
    check(
      `${scheme}: money and an escaped dollar stay as written`,
      prose.includes("It costs $5 and $10, or $20 with tax."),
    );
    const code = await first.locator("code:not(.math-failed)").allInnerTexts();
    check(
      `${scheme}: code with dollars stays code`,
      code.some((c) => c.includes("price = $5")) &&
        code.some((c) => c.includes('total = "$5" + "$10"')),
      JSON.stringify(code),
    );
    check(
      `${scheme}: a display formula is a block of its own`,
      (await first.locator(".math-block .katex-display").count()) === 3 &&
        (await first.locator(":not(.math-block) > .math-display .katex-display").count()) === 2,
    );

    // --- a formula that takes a minute, stopped ---------------------------------
    const heavy = second.locator("code.math-failed").first();
    check(
      `${scheme}: a formula that takes too long is stopped and says so`,
      (await heavy.getAttribute("title")) ===
        "Formula not shown: it took longer than 1.5 s to lay out",
      await heavy.getAttribute("title"),
    );
    check(
      `${scheme}: a formula that would pull its line across the page is its source`,
      /-500em/.test((await second.locator("code.math-failed").nth(1).getAttribute("title")) ?? ""),
    );
    check(
      `${scheme}: the formulas after a stopped one are drawn`,
      (await second.locator(".katex").count()) === 2,
    );
    check(
      `${scheme}: the page kept drawing while a formula was stopped`,
      gap < 250,
      `${Math.round(gap)} ms between frames`,
    );

    // --- footnotes, by keyboard, in each answer ----------------------------------
    const duplicates = await duplicateIds(page);
    check(
      `${scheme}: no id is used twice on the page`,
      duplicates.length === 0,
      duplicates.join(", "),
    );
    for (const [name, region, n, note] of [
      ["the first answer", first, 1, "Fourier's law"],
      ["the first answer's second note", first, 2, "Prices before tax"],
      ["the second answer", second, 1, "The second try's own note"],
    ]) {
      const trip = await footnoteTrip(page, region, n);
      check(
        `${scheme}: a reference in ${name} moves to its own note and back by keyboard`,
        trip.there.tag === "LI" &&
          trip.there.text.includes(note) &&
          trip.sameScope &&
          trip.back === `Back to reference ${n}` &&
          trip.home.ref &&
          trip.addressKept,
        JSON.stringify(trip),
      );
    }

    // --- the other branch ----------------------------------------------------
    await answers.nth(1).getByRole("button", { name: "Previous version of this answer" }).click();
    await page.getByText("The first try at it").waitFor();
    await settled(page);
    const firstTry = answers.nth(1).locator("[data-md-scope]").last();
    check(
      `${scheme}: the other branch draws its formula`,
      (await firstTry.locator(".katex").count()) === 1,
    );
    const branchTrip = await footnoteTrip(page, firstTry, 1);
    check(
      `${scheme}: the other branch's reference moves to its own note and back`,
      branchTrip.there.text.includes("The first try's own note") &&
        branchTrip.sameScope &&
        branchTrip.home.ref,
      JSON.stringify(branchTrip),
    );
    check(
      `${scheme}: no id is used twice with the other branch shown`,
      (await duplicateIds(page)).length === 0,
    );
    await answers.nth(1).getByRole("button", { name: "Next version of this answer" }).click();
    await page.getByText("Two more, and two that cannot be shown").waitFor();
    await settled(page);

    // --- legible in this theme, and the pictures -----------------------------------
    const colours = await formulaColours(page, ".answer .katex");
    const ratio = colours ? contrast(colours.fore, colours.back) : 0;
    check(`${scheme}: a formula reads at least 4.5:1`, ratio >= 4.5, ratio.toFixed(2));
    const failedColours = await formulaColours(page, ".answer code.math-failed");
    const failedRatio = failedColours ? contrast(failedColours.fore, failedColours.back) : 0;
    check(
      `${scheme}: a formula's source reads at least 4.5:1`,
      failedRatio >= 4.5,
      failedRatio.toFixed(2),
    );
    await first.scrollIntoViewIfNeeded();
    await page.screenshot(shot(`math-${scheme}-desk`));
    await second.scrollIntoViewIfNeeded();
    await page.screenshot(shot(`math-second-${scheme}-desk`));
    check(
      `${scheme}: the page's policy refused nothing`,
      (await pagePolicy(page)).length === 0,
      JSON.stringify(await pagePolicy(page)),
    );
    await context.close();

    // --- a phone, and 200% zoom ------------------------------------------------------
    for (const [label, viewport, extra] of [
      ["phone", PHONE, { isMobile: true, hasTouch: true, deviceScaleFactor: 3 }],
      ["200% zoom", ZOOM, { deviceScaleFactor: 2 }],
    ]) {
      const small = await open(scheme, viewport, extra);
      await chat(small.page, cfg.chats.math);
      await settled(small.page, 30000);
      const fit = await fits(small.page);
      check(
        `${scheme} ${label}: nothing is wider than the screen`,
        fit.page && fit.wide.length === 0,
        JSON.stringify(fit.wide),
      );
      // The polynomial, the third block: wider than a phone.
      const wide = small.page.locator(".math-block .math-display").nth(2);
      // Wider than its box, and the box itself really scrolls (a box that
      // lets the formula spill, or clips it, is wider and does not).
      const scrolls = await wide.evaluate((el) => {
        const wider = el.scrollWidth > el.clientWidth + 1;
        el.scrollLeft = 40;
        const moved = el.scrollLeft > 0;
        el.scrollLeft = 0;
        const fits = el.getBoundingClientRect().right <= window.innerWidth + 1;
        return { wider, moved, fits, focusable: el.tabIndex === 0 };
      });
      check(
        `${scheme} ${label}: a display formula wider than the column scrolls in its own box, by keyboard too`,
        scrolls.focusable &&
          scrolls.fits &&
          (label === "phone" ? scrolls.wider && scrolls.moved : true),
        JSON.stringify(scrolls),
      );
      await wide.scrollIntoViewIfNeeded();
      await small.page.screenshot(
        shot(`math-${scheme}-${label === "phone" ? "phone" : "zoom200"}`),
      );
      await small.context.close();
    }
  }

  // --- a draft and its answer each keep their own footnote ------------------------
  {
    const { context, page } = await open("dark", DESK);
    await chat(page, cfg.chats.draft);
    await page.getByTestId("draft").locator("summary").click();
    await settled(page);
    // Reasoning now contains Markdown too; it is a third, unrelated scope.
    const draft = page.getByTestId("draft").locator("[data-md-scope]");
    const answer = page.locator("[data-testid=answer] > [data-md-scope]");
    const regions = draft.or(answer);
    check("draft: the draft and its answer are each their own", (await regions.count()) === 2);
    check(
      "draft: no id is used twice",
      (await duplicateIds(page)).length === 0,
      (await duplicateIds(page)).join(", "),
    );
    const draftTrip = await footnoteTrip(page, draft, 1);
    check(
      "draft: the draft's reference moves to the draft's note and back",
      draftTrip.there.text.includes("Written before the search") &&
        draftTrip.sameScope &&
        draftTrip.home.ref,
      JSON.stringify(draftTrip),
    );
    const answerTrip = await footnoteTrip(page, answer, 1);
    check(
      "draft: the answer's reference moves to the answer's note and back",
      answerTrip.there.text.includes("Found by the search") &&
        answerTrip.sameScope &&
        answerTrip.home.ref,
      JSON.stringify(answerTrip),
    );
    await page.screenshot(shot("math-draft-dark-desk"));
    await context.close();
  }

  // --- an answer being written ---------------------------------------------------------
  {
    const { context, page } = await open("dark", DESK);
    await page.getByTestId("new-chat").click();
    await fillComposer(page, cfg.live);
    await page.evaluate(() => (window.__posts = []));
    await page.getByTestId("send").click();
    const running = page.locator('[data-testid=answer][data-status="running"]');
    await running.waitFor();
    const seen = { partial: false, openBlock: false, drawnWhileOpen: false };
    for (let i = 0; i < 400; i++) {
      const look = await page.evaluate(() => {
        const answer = document.querySelector('[data-testid=answer][data-status="running"]');
        if (!answer) return null;
        const text = answer.textContent ?? "";
        const block = [...answer.querySelectorAll("pre code")].some((c) =>
          c.textContent?.includes("\\frac{1}"),
        );
        return {
          partial: text.includes("$x +") && !answer.querySelector(".math-inline"),
          openBlock: block && !answer.querySelector(".katex-display"),
          drawnWhileOpen: block && !!answer.querySelector(".katex-display"),
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
    check("live: half a formula shows as the text it is", seen.partial);
    check("live: a block not yet closed shows as its source", seen.openBlock);
    check("live: a block is not drawn before it closes", !seen.drawnWhileOpen);
    check(
      "live: both formulas are drawn once written",
      (await live.locator(".katex").count()) === 2,
    );
    const posts = await page.evaluate(() => window.__posts);
    check(
      "live: each formula is laid out once, not on every chunk",
      JSON.stringify(posts) === JSON.stringify(["x + y", "\\frac{1}{2}"]),
      JSON.stringify(posts),
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
check(
  "formula code was loaded once formulas were shown",
  requests.some((url) => /mathWorker/.test(url)),
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
