/* global process, console, URL, document, window, performance, requestAnimationFrame, setTimeout, getComputedStyle */
// Artifacts shown and run in place, in the selected browser (workbench-rich-content-roadmap.md
// RC6), under the headers Workbench really sends: Markdown and SVG drawn where
// the answer puts them; a component started by itself on screen, answering
// clicks and its own form, its frame as tall as its page; a page that tries
// every way out of its frame, started by itself and refused each; nothing run
// before it is on screen; at most four at once; a preview that loops named and
// stopped, the others started again, and not run by itself after a reload; the
// person's switch kept and obeyed; the owner's view of a chat waiting for Run;
// footnotes, in an answer and in a Markdown artifact, that never lengthen the
// page; a phone. Each check is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import {
  contextOptions,
  launchBrowser,
  readOptions,
  watchPolicy,
  watchServiceWorkers,
  canCheckPreviewLoop,
} from "./browser-options.mjs";

const { configPath, browserName } = readOptions();
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
const require = createRequire(import.meta.url);
mkdirSync(cfg.shots, { recursive: true });
const origin = new URL(cfg.url).origin;
const browser = await launchBrowser(require(cfg.playwright), cfg, browserName);
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const FRAME = `${origin}/preview/frame`;
const MAX_RUNNING = 4;

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}

let at = "starting";
let lastPage = null;
function step(name) {
  at = name;
}
setTimeout(() => {
  console.log(`FAIL  the run stopped at: ${at}`);
  process.exit(2);
}, 360_000).unref();

const errors = [];
const policy = [];
const requests = [];
const answered = [];

async function open(scheme, viewport, who = cfg, extra = {}) {
  const context = await browser.newContext(
    contextOptions(browserName, { colorScheme: scheme, viewport, ...extra }),
  );
  await context.addCookies(who.cookies);
  await context.addInitScript(watchServiceWorkers);
  context.setDefaultTimeout(15_000);
  await context.addInitScript(watchPolicy, browserName);
  const page = await context.newPage();
  lastPage = page;
  page.on("pageerror", (error) => {
    if (!(error.stack ?? "").includes("/preview/")) errors.push(error.message);
  });
  page.on("request", (request) =>
    requests.push({ url: request.url(), main: request.frame() === page.mainFrame() }),
  );
  page.on("response", (response) =>
    answered.push({
      url: response.request().url(),
      main: response.request().frame() === page.mainFrame(),
    }),
  );
  await page.goto(`${cfg.url}/#signin=${who.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function chat(page, id) {
  await page.goto(`${cfg.url}/chats/${id}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
}

function card(page, title) {
  return page
    .getByTestId("artifact-card")
    .filter({ has: page.locator(".artifact-card-title", { hasText: new RegExp(`^${title}$`) }) });
}

/** The card's own frame, once it runs by itself. */
async function runsBy(page, title, timeout = 15_000) {
  const frame = card(page, title).getByTestId("artifact-frame");
  await frame.waitFor({ timeout });
  return (await frame.elementHandle()).contentFrame();
}

/**
 * The card brought on screen until its preview runs by itself: the chat keeps to
 * its end while what it holds grows, which can take the card away again.
 */
async function shownRuns(page, title) {
  for (let i = 0; i < 10; i++) {
    await card(page, title).scrollIntoViewIfNeeded();
    if (await card(page, title).getByTestId("artifact-frame").count()) return runsBy(page, title);
    await page.waitForTimeout(1_500);
  }
  return runsBy(page, title, 1_000);
}

const frames = (page) => page.getByTestId("artifact-frame").count();
const notes = async (page, title) =>
  (await card(page, title).getByTestId("artifact-notes").count())
    ? card(page, title).getByTestId("artifact-notes").innerText()
    : "";

try {
  // --- an answer's artifacts, where they stand --------------------------------------------
  step("an answer's artifacts");
  let { context, page } = await open("dark", DESK);
  await chat(page, cfg.chats.shown);
  await card(page, "Notes").getByRole("heading", { name: "Shopping" }).waitFor();
  check(
    "Markdown is drawn where the answer puts it",
    (await card(page, "Notes").locator("li").count()) >= 2,
  );
  const logo = card(page, "Logo").getByRole("img", { name: "Logo" });
  await logo.waitFor();
  check(
    "an SVG is drawn where the answer puts it, as a picture",
    (await logo.getAttribute("src")).startsWith("data:image/svg+xml") &&
      (await logo.evaluate((img) => img.complete && img.naturalWidth > 0)),
  );

  step("a component that runs by itself");
  await card(page, "Counter").scrollIntoViewIfNeeded();
  const counter = await runsBy(page, "Counter");
  await counter.getByRole("button", { name: "Clicked 0" }).click();
  check(
    "a component runs by itself once on screen, and answers a click",
    (await counter.getByRole("button", { name: "Clicked 1" }).count()) === 1 &&
      (await card(page, "Counter").getByRole("button", { name: "Run" }).count()) === 0,
  );
  const counterFrame = card(page, "Counter").getByTestId("artifact-frame");
  const before = (await counterFrame.boundingBox()).height;
  for (const name of ["Ada", "Bo", "Cy", "Di", "Ed", "Flo"]) {
    await counter.getByRole("textbox", { name: "Name" }).fill(name);
    await counter.getByRole("textbox", { name: "Name" }).press("Enter");
  }
  await counter.getByText("Flo", { exact: true }).waitFor();
  await page.waitForTimeout(500);
  const after = (await counterFrame.boundingBox()).height;
  check(
    "its frame grows with its page, and its form runs its own handler",
    after > before + 40 && !(await notes(page, "Counter")).includes("sending a form"),
    `${before} -> ${after}`,
  );

  step("heights");
  await card(page, "Short").scrollIntoViewIfNeeded();
  await runsBy(page, "Short");
  await page.waitForTimeout(800);
  const short = (await card(page, "Short").getByTestId("artifact-frame").boundingBox()).height;
  await card(page, "Tall").scrollIntoViewIfNeeded();
  await runsBy(page, "Tall");
  await page.waitForTimeout(800);
  const tall = (await card(page, "Tall").getByTestId("artifact-frame").boundingBox()).height;
  const most = Math.round(DESK.height * 0.7);
  check(
    "a short page's frame is short, a tall one's stops at most of the window",
    short >= 118 && short < 220 && Math.abs(tall - most) <= 3,
    `short ${short}, tall ${tall}, most ${most}`,
  );

  step("a page that tries every way out, run by itself");
  const sinceEscapes = answered.length;
  await card(page, "Escapes").scrollIntoViewIfNeeded();
  const escapes = await runsBy(page, "Escapes");
  for (const name of ["network", "api", "worker"]) {
    await escapes.locator(`li[data-name="${name}"]`).waitFor({ timeout: 10_000 });
  }
  const said = Object.fromEntries(
    await escapes
      .locator("#out li")
      .evaluateAll((items) => items.map((li) => [li.dataset.name, li.textContent])),
  );
  const serviceWorkers = await escapes.evaluate(() => Promise.all(window.__serviceWorkers));
  const refused = [
    "parent-dom",
    "top-dom",
    "cookie",
    "storage",
    "popup",
    "worker",
    "service-worker",
    "form-script",
    "form-button",
    "api",
    "network",
  ].filter(
    (name) =>
      !(
        (said[name] ?? "").startsWith(`${name}: blocked`) ||
        (name === "service-worker" && serviceWorkers.length === 1 && serviceWorkers[0] === true)
      ),
  );
  const reached = answered
    .slice(sinceEscapes)
    .filter((r) => !r.main && !r.url.startsWith(FRAME) && r.url !== `${origin}/preview/runtime.js`);
  check(
    "a page run by itself is refused every way out, and reaches nothing",
    refused.length === 0 && reached.length === 0 && page.url().endsWith(cfg.chats.shown),
    `${refused.join(", ")} | ${reached.map((r) => r.url).join(", ")}`,
  );

  step("at most four at once");
  let most4 = await frames(page);
  for (const title of ["Fifth", "Notes", "Counter", "Short", "Tall", "Escapes", "Fifth"]) {
    await card(page, title).scrollIntoViewIfNeeded();
    await page.waitForTimeout(2_500);
    most4 = Math.max(most4, await frames(page));
  }
  await runsBy(page, "Fifth");
  const roomed = [];
  for (const title of ["Counter", "Short", "Tall", "Escapes"]) {
    if ((await notes(page, title)).includes("make room")) roomed.push(title);
  }
  check(
    `at most ${MAX_RUNNING} run at once, the one longest out of view making room`,
    most4 <= MAX_RUNNING && roomed.length >= 1,
    `most ${most4}, made room: ${roomed.join(", ")}`,
  );
  const gap = await page.evaluate(
    () =>
      new Promise((done) => {
        let last = performance.now();
        let worst = 0;
        const end = last + 1_000;
        const tick = (t) => {
          worst = Math.max(worst, t - last);
          last = t;
          if (t < end) requestAnimationFrame(tick);
          else done(worst);
        };
        requestAnimationFrame(tick);
      }),
  );
  check("the chat stays smooth while they run", gap < 100, `${gap.toFixed(0)} ms`);
  console.log(
    `  measured: longest frame of the chat with ${await frames(page)} running, ${gap.toFixed(1)} ms`,
  );
  await page.screenshot({ path: `${cfg.shots}/shown-dark.png` });
  policy.push(...(await page.evaluate(() => window.__policy)));
  await context.close();

  // --- nothing runs before it is on screen -------------------------------------------------
  step("nothing before it is on screen");
  ({ context, page } = await open("dark", DESK));
  await chat(page, cfg.chats.far);
  await card(page, "Far").waitFor({ state: "attached" });
  // The last, on screen as the chat opens at its end, runs; then its page grows.
  await shownRuns(page, "Grows");
  await card(page, "Grows").getByRole("status").getByText("Running").waitFor();
  const grows = card(page, "Grows").getByTestId("artifact-frame");
  const small = (await grows.boundingBox()).height;
  // It grows 4 s after it starts: measured before, and well after.
  await page.waitForTimeout(6_000);
  const grown = (await grows.boundingBox()).height;
  check(
    "a written page that grows after it loads: its frame follows",
    grown > small + 100,
    `${small} -> ${grown}`,
  );
  const early = await card(page, "Far").getByTestId("artifact-frame").count();
  await card(page, "Far").scrollIntoViewIfNeeded();
  await runsBy(page, "Far");
  check("a preview out of view does not run until it is on screen", early === 0, `${early}`);
  // Footnotes far down the chat, and in a long Markdown artifact: each list's heading
  // is hidden for a screen reader (positioned out of sight). The thread holds the
  // answer's, the artifact's box holds its own, and the page itself never scrolls.
  const hidden = await page.evaluate(() => {
    const thread = document.getElementById("conversation");
    const positioned = (box) =>
      [...box.querySelectorAll("*")].filter((el) => getComputedStyle(el).position === "absolute");
    const loose = (box) => positioned(box).filter((el) => !box.contains(el.offsetParent)).length;
    const docs = [...thread.querySelectorAll(".artifact-doc")];
    const page = () =>
      document.scrollingElement.scrollHeight - document.scrollingElement.clientHeight;
    const atEnd = page();
    thread.scrollTop = 0;
    return {
      thread: positioned(thread).length,
      threadLoose: loose(thread),
      docs: docs.map((doc) => positioned(doc).length),
      docsLoose: docs.reduce((n, doc) => n + loose(doc), 0),
      page: Math.max(atEnd, page()),
    };
  });
  check(
    "footnotes far down a chat do not lengthen the page",
    hidden.page === 0 && hidden.thread > 1 && hidden.threadLoose === 0,
    JSON.stringify(hidden),
  );
  check(
    "a Markdown artifact's box holds what its Markdown hides",
    hidden.docs.length === 1 && hidden.docs[0] > 0 && hidden.docsLoose === 0,
    JSON.stringify(hidden),
  );
  policy.push(...(await page.evaluate(() => window.__policy)));
  await context.close();

  if (canCheckPreviewLoop(browserName)) {
    // --- one that loops ----------------------------------------------------------------------
    step("a preview that loops");
    ({ context, page } = await open("dark", DESK));
    await chat(page, cfg.chats.stuck);
    const named = card(page, "Busy").getByTestId("artifact-notes");
    await named.getByText(/stopped answering/).waitFor({ timeout: 30_000 });
    const stoppedAt = performance.now();
    await card(page, "Calm").getByRole("status").getByText("Running").waitFor({ timeout: 30_000 });
    await card(page, "Calm too")
      .getByRole("status")
      .getByText("Running")
      .waitFor({ timeout: 30_000 });
    const back = performance.now() - stoppedAt;
    check(
      "a preview that loops is named and stopped, and the others run again",
      (await card(page, "Busy").getByTestId("artifact-frame").count()) === 0 &&
        (await card(page, "Busy").getByRole("button", { name: "Run" }).count()) === 1 &&
        (await card(page, "Calm").getByTestId("artifact-frame").count()) === 1 &&
        (await card(page, "Calm too").getByTestId("artifact-frame").count()) === 1,
      await notes(page, "Busy"),
    );
    console.log(
      `  measured: the others running again ${back.toFixed(0)} ms after the loop was stopped`,
    );
    await page.screenshot({ path: `${cfg.shots}/stuck-dark.png` });
    await page.reload();
    await card(page, "Calm").getByRole("status").getByText("Running").waitFor({ timeout: 30_000 });
    await page.waitForTimeout(4_000);
    check(
      "after a reload, the one that looped waits for Run",
      (await card(page, "Busy").getByTestId("artifact-frame").count()) === 0 &&
        (await card(page, "Busy").getByRole("button", { name: "Run" }).count()) === 1,
    );
    policy.push(...(await page.evaluate(() => window.__policy)));
    await context.close();
  }

  // --- the person's switch --------------------------------------------------------------
  step("the switch");
  ({ context, page } = await open("light", DESK));
  const box = page.getByRole("checkbox", { name: "Run previews when shown" });
  check("the switch is on until the person turns it off", await box.isChecked());
  // The box shows what Workbench keeps: it changes once that is kept.
  await box.click();
  await page.waitForFunction(() => {
    const label = [...document.querySelectorAll("label")].find((l) =>
      l.textContent.includes("Run previews when shown"),
    );
    const input = label?.querySelector("input");
    return input && !input.checked && !input.disabled;
  });
  await page.reload();
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  await chat(page, cfg.chats.shown);
  await card(page, "Counter").scrollIntoViewIfNeeded();
  await card(page, "Counter").getByRole("button", { name: "Run" }).waitFor();
  await page.waitForTimeout(3_000);
  check(
    "turned off, it is kept, and previews wait for Run",
    !(await page.getByRole("checkbox", { name: "Run previews when shown" }).isChecked()) &&
      (await frames(page)) === 0,
  );
  await page.getByRole("checkbox", { name: "Run previews when shown" }).click();
  await shownRuns(page, "Counter");
  check("turned on, the preview on screen runs", true);
  policy.push(...(await page.evaluate(() => window.__policy)));
  await context.close();

  // --- the owner's view --------------------------------------------------------------------
  step("the owner's view");
  ({ context, page } = await open("light", DESK, cfg.owner));
  await chat(page, cfg.chats.shown);
  await card(page, "Counter").scrollIntoViewIfNeeded();
  await card(page, "Counter").getByRole("button", { name: "Run" }).waitFor();
  await page.waitForTimeout(3_000);
  check("the owner's view of someone's chat runs nothing by itself", (await frames(page)) === 0);
  await context.close();

  // --- a phone ----------------------------------------------------------------------------
  step("a phone");
  ({ context, page } = await open("light", PHONE, cfg, {
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 3,
  }));
  await chat(page, cfg.chats.shown);
  await shownRuns(page, "Counter");
  const fit = await page.evaluate(() => {
    const wide = [];
    for (const el of document.querySelectorAll(
      "[data-testid=artifact-card], [data-testid=artifact-card] *",
    )) {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.right > window.innerWidth + 1 && !el.closest(".artifact-doc, pre")) {
        wide.push(el.className?.baseVal ?? el.className ?? el.tagName);
      }
    }
    return { page: document.documentElement.scrollWidth <= window.innerWidth + 1, wide };
  });
  check(
    "phone: the artifacts and their frames fit the screen",
    fit.page && fit.wide.length === 0,
    JSON.stringify(fit.wide.slice(0, 5)),
  );
  await page.screenshot({ path: `${cfg.shots}/shown-phone.png` });
  policy.push(...(await page.evaluate(() => window.__policy)));
  await context.close();
} catch (error) {
  check(`the run went on past: ${at}`, false, String(error?.message ?? error).split("\n")[0]);
  const shown = await lastPage
    ?.locator("[data-testid=artifact-card]")
    .allInnerTexts()
    .catch(() => []);
  console.log(`  the cards showed: ${JSON.stringify(shown ?? []).slice(0, 800)}`);
} finally {
  step("closing the browser");
  await Promise.race([browser.close(), new Promise((done) => setTimeout(done, 10_000))]);
}

const elsewhere = requests.filter(
  (r) =>
    r.main && !r.url.startsWith(origin) && !r.url.startsWith("data:") && !r.url.startsWith("blob:"),
);
check(
  "every request of the page went to Workbench itself",
  elsewhere.length === 0,
  elsewhere.map((r) => r.url).join(", "),
);
check("the page's own policy refused nothing", policy.length === 0, policy.join(" | "));
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
process.exit(0);
