/* global process, console, document, getComputedStyle, window, setTimeout */
// Documents in Chrome (MR2): the whole of it, as a person meets it, against the
// real app and its fake Eugene and gateway. Attaches a three-page PDF and
// watches its chip go from reading to ready, sees why Send waits, reads the text
// Workbench took from it, sends it and finds the file and its pages under the
// answer; attaches a long PDF, is refused by the server when the page's view of
// it is stale, chooses the first part and sends it; changes the model and sees
// every decision asked again; reloads with a file attached and finds it still
// there; then takes pictures of the composer's chips and the Sources under an
// answer, dark and light, at a desk and a phone.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
mkdirSync(cfg.shots, { recursive: true });
const shot = (name) => ({ path: `${cfg.shots}/${name}.png` });
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
const errors = [];
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const WORD = cfg.word;
const SAID = cfg.said;
const log = (line) => console.log(line);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The page last opened, so a failure can say what it was showing.
let latest = null;
async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext({ colorScheme: scheme, viewport, ...extra });
  // A step that never comes fails in seconds, not in half a minute.
  context.setDefaultTimeout(15000);
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  latest = page;
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

/** A new chat for the person this context is signed in as; its address. */
async function newChat(context) {
  const response = await context.request.post(`${cfg.url}/api/chats`, {
    headers: { Origin: cfg.url, "X-Workbench-Secret": cfg.secret },
    data: {},
  });
  assert.ok(response.ok(), `a new chat: ${response.status()}`);
  return `${cfg.url}/chats/${(await response.json()).id}`;
}

const finished = (page) => page.locator('[data-testid=answer]:not([data-status="running"])');

/** Says something in the open chat and waits for its answer. */
async function say(page, words) {
  const before = await finished(page).count();
  await page.getByTestId("composer").fill(words);
  await page.getByTestId("send").click();
  await page.waitForFunction(
    (count) =>
      document.querySelectorAll('[data-testid=answer]:not([data-status="running"])').length > count,
    before,
    { timeout: 20000 },
  );
}

const chips = (page) => page.getByTestId("document-chip");
const chip = (page, name, state) =>
  page
    .locator(`[data-testid=document-chip]${state ? `[data-state="${state}"]` : ""}`)
    .filter({ hasText: name });

/** Attaches a file through the Attach button's own input, and waits for its chip. */
async function attach(page, name) {
  await page.getByTestId("attach-input").setInputFiles(cfg.files[name]);
  await chip(page, name).waitFor();
}

// Every scroller to its top, and the window made tall enough for the whole page.
async function tall(page) {
  const more = await page.evaluate(() => {
    let most = 0;
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) {
        most = Math.max(most, el.scrollHeight - el.clientHeight);
      }
    }
    return most;
  });
  const size = page.viewportSize();
  await page.setViewportSize({
    width: size.width,
    height: Math.min(size.height + more + 40, 7000),
  });
  await settle(page);
}

const wider = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

/** A picture, once the colours and the type have stopped moving. */
async function snap(page, name) {
  await settle(page);
  await page.screenshot(shot(name));
}

/** Files in the composer must not push the message box and Send off the screen: the
 * page does not scroll, and Send is inside the window. */
async function fits(page, what) {
  const found = await page.evaluate(() => ({
    scroll: document.documentElement.scrollHeight - window.innerHeight,
    send: document.querySelector("[data-testid=send]").getBoundingClientRect().bottom,
    height: window.innerHeight,
  }));
  assert.ok(found.scroll <= 1, `${what}: the page scrolls by ${found.scroll}px`);
  assert.ok(
    found.send <= found.height,
    `${what}: Send is ${found.send - found.height}px below the screen`,
  );
}

const listing = /\/api\/chats\/[^/]+\/files(\?.*)?$/;

let chatUrl = "";
try {
  const { context, page } = await open("dark", DESK);
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  assert.equal(
    await page
      .getByTestId("attach-input")
      .getAttribute("accept")
      .then((a) => a.includes(".docx")),
    true,
  );
  assert.match(await page.getByTestId("attach-input").getAttribute("accept"), /text\/\*/);
  log("PASS  the Attach button's picker offers Word and text files beside PDFs and images");

  // --- a PDF is read: reading, then ready -------------------------------------------
  // The page asks again each second while a file is being read; slowing those
  // answers keeps "reading" on the screen long enough to be seen and photographed.
  const slow = async (route) => {
    if (route.request().method() === "GET") await sleep(1600);
    await route.continue();
  };
  await page.route(listing, slow);
  await attach(page, "report.pdf");
  const reading = chip(page, "report.pdf", "reading");
  await reading.waitFor();
  assert.match(await reading.textContent(), /Reading…/);
  assert.equal(await page.getByTestId("send").isDisabled(), true);
  assert.match(
    await page.getByTestId("send-held").textContent(),
    /Waiting for Workbench to read report\.pdf\./,
  );
  assert.equal(
    await page.getByTestId("send").getAttribute("aria-describedby"),
    "send-held",
    "the reason is the button's description",
  );
  await snap(page, "documents-reading-dark-desk");
  log("PASS  a file being read has a chip that says so, and Send waits with the reason beside it");

  const ready = chip(page, "report.pdf", "whole");
  await ready.waitFor();
  await page.unroute(listing, slow);
  assert.match(
    await ready.getByTestId("document-meta").textContent(),
    /^PDF · 3 pages · about \d+ tokens$/,
  );
  assert.match(await ready.getByTestId("document-status").textContent(), /Will be read whole\./);
  assert.equal(await page.getByTestId("send-held").count(), 0);
  assert.equal(await page.getByTestId("send").isDisabled(), false, "a file alone can be sent");
  await page.getByTestId("composer").fill(SAID.ask);
  log("PASS  once it is read, the chip says what it holds and that it will be read whole");

  // --- what Workbench read -----------------------------------------------------------
  await ready.getByTestId("document-text-toggle").click();
  const text = ready.getByTestId("document-text");
  await text.waitFor();
  const read = await text.textContent();
  assert.ok(read.includes("[page 3]") && read.includes(WORD), read);
  assert.ok(read.indexOf("[page 1]") < read.indexOf("[page 2]"));
  await snap(page, "documents-text-dark-desk");
  await ready.getByTestId("document-text-toggle").click();
  assert.equal(await ready.getByTestId("document-text").count(), 0);
  log("PASS  Show what Workbench read opens the text with its pages marked, and closes it again");

  // --- send, and the answer names the file and its pages ------------------------------
  await say(page, SAID.ask);
  const mine = page.getByTestId("user-message").first();
  assert.match(await mine.getByTestId("document-file").textContent(), /report\.pdf/);
  assert.match(await mine.getByTestId("document-file-meta").textContent(), /PDF · 3 pages/);
  assert.equal(await chips(page).count(), 0, "the chips go with the message");
  const sources = page.getByTestId("document-sources");
  await sources.waitFor();
  assert.match(await sources.textContent(), /Files sent to the model/);
  assert.match(
    await sources.getByTestId("document-source").textContent(),
    /^report\.pdf, pages 1 to 3 of 3 · about \d+ tokens$/,
  );
  assert.match(await finished(page).first().textContent(), new RegExp(WORD));
  await mine.getByTestId("document-text-toggle").click();
  await mine.getByTestId("document-text").waitFor();
  assert.ok((await mine.getByTestId("document-text").textContent()).includes(WORD));
  await mine.getByTestId("document-text-toggle").click();
  chatUrl = page.url();
  log(
    "PASS  the answer lists the file and its pages (what was sent), and the message shows the text",
  );

  // --- a long file: the page's view is stale, so the server refuses ------------------
  // The first answer about the long file is made to say it is fine; the server
  // knows better, and the chip says why the message was not sent.
  let forged = false;
  const stale = async (route) => {
    if (route.request().method() !== "GET" || forged) return route.continue();
    const response = await route.fetch();
    const body = await response.json();
    for (const file of body.files) {
      if (file.decision?.kind === "choose") {
        forged = true;
        Object.assign(file.decision, { kind: "whole", send: true, offers: [], message: "ok" });
      }
    }
    return route.fulfill({ response, json: body });
  };
  await page.route(listing, stale);
  await attach(page, "long-12-pages.pdf");
  await chip(page, "long-12-pages.pdf", "whole").waitFor();
  await page.unroute(listing, stale);
  await page.getByTestId("composer").fill(SAID.summarize);
  assert.equal(await page.getByTestId("send").isDisabled(), false);
  await page.getByTestId("send").click();
  const refused = chip(page, "long-12-pages.pdf", "choose");
  await refused.waitFor();
  const problem = refused.getByTestId("document-problem");
  await problem.waitFor();
  assert.match(await problem.textContent(), /Your message was not sent/);
  assert.match(await problem.textContent(), /more than Workbench reads at once by default/);
  assert.equal(await problem.getAttribute("role"), "alert");
  assert.equal(await finished(page).count(), 1, "nothing was sent");
  assert.equal(
    await page.getByTestId("composer").inputValue(),
    SAID.summarize,
    "the words are kept",
  );
  await snap(page, "documents-refused-dark-desk");
  log("PASS  a refused send keeps the words and names the file on its own chip");

  // --- choose how much to read -------------------------------------------------------
  assert.match(
    await page.getByTestId("send-held").textContent(),
    /Choose how much of long-12-pages\.pdf to read, or remove it\./,
  );
  const offerAll = refused.getByTestId("document-offer-all");
  const offerFirst = refused.getByTestId("document-offer-first");
  assert.match(await offerAll.textContent(), /^Read it all \(about [\d,]+ tokens\)$/);
  assert.match(
    await offerFirst.textContent(),
    /^Read the first [\d,]+ tokens \(pages 1 to \d+ of 12\)$/,
  );
  await snap(page, "documents-choose-dark-desk");
  const firstWords = await offerFirst.textContent();
  await offerFirst.click();
  const partial = chip(page, "long-12-pages.pdf", "partial");
  await partial.waitFor();
  assert.equal(await partial.getByTestId("document-problem").count(), 0);
  assert.match(
    await partial.getByTestId("document-status").textContent(),
    /Will read pages 1 to \d+ of 12 \(about [\d,]+ tokens\)\. The rest of the file is not sent\./,
  );
  assert.match(
    await partial.getByTestId("document-choice").textContent(),
    /^Your choice: the first [\d,]+ tokens\.$/,
  );
  assert.equal(await page.getByTestId("send-held").count(), 0);
  assert.ok(
    firstWords.includes(
      (await partial.getByTestId("document-choice").textContent()).match(/[\d,]+/)[0],
    ),
  );
  await snap(page, "documents-partial-dark-desk");
  log("PASS  choosing the first part keeps the choice, says what will be read, and lets Send go");
  await say(page, SAID.summarize);
  const second = page.getByTestId("document-sources").last();
  const lines = await second.getByTestId("document-source").allTextContents();
  assert.equal(lines.length, 2, lines.join(" | "));
  assert.match(lines[0], /^report\.pdf, pages 1 to 3 of 3 · attached earlier/);
  assert.match(
    lines[1],
    /^long-12-pages\.pdf, pages 1 to \d+ of 12 · first part · about [\d,]+ tokens$/,
  );
  log("PASS  the answer lists the part that was sent, as the first part, and the file sent again");

  // --- a file that cannot be read -----------------------------------------------------
  await attach(page, "scan.pdf");
  const scan = chip(page, "scan.pdf", "skipped");
  await scan.waitFor();
  assert.match(
    await scan.getByTestId("document-status").textContent(),
    /Skipped: this PDF has no text, only pictures of pages\./,
  );
  await page.getByTestId("composer").fill("Read the scan.");
  assert.equal(await page.getByTestId("send").isDisabled(), true);
  assert.match(
    await page.getByTestId("send-held").textContent(),
    /scan\.pdf cannot be read\. Remove it to send your message\./,
  );
  await snap(page, "documents-skipped-dark-desk");
  await scan.getByRole("button", { name: "Remove scan.pdf" }).click();
  await scan.waitFor({ state: "detached" });
  assert.equal(await page.getByTestId("send").isDisabled(), false);
  log("PASS  a scanned PDF is skipped with the server's words, holds Send, and Remove frees it");

  // --- a file that is too long for the model, and a different model ------------------
  // The first answer's chat already carries a long file's first part, so a model
  // with a small window has no room left for another: the server says so, and the
  // chip is for the model that is picked, in either direction.
  await attach(page, "long-30-pages.pdf");
  const tooLong = chip(page, "long-30-pages.pdf", "too_long");
  await tooLong.waitFor();
  const bigWindow = await tooLong.getByTestId("document-status").textContent();
  assert.match(
    bigWindow,
    /This model's window is 32,768 tokens, so there is room for about [\d,]+ tokens of this file\./,
  );
  assert.equal(await tooLong.getByTestId("document-offer-all").count(), 0, "it does not fit whole");
  assert.match(
    await page.getByTestId("send-held").textContent(),
    /Choose how much of long-30-pages\.pdf to read, or remove it\./,
  );
  await page.getByTestId("model-picker").selectOption(cfg.small);
  const crowded = chip(page, "long-30-pages.pdf", "no_room");
  await crowded.waitFor();
  assert.match(
    await crowded.getByTestId("document-status").textContent(),
    /This chat already fills about [\d,]+ of this model's 4,096 tokens, so there is no room for this document\./,
  );
  assert.match(
    await page.getByTestId("send-held").textContent(),
    /There is no room for long-30-pages\.pdf in this model's window\./,
  );
  await snap(page, "documents-no-room-dark-desk");
  await page.getByTestId("model-picker").selectOption(cfg.model);
  await chip(page, "long-30-pages.pdf", "too_long").waitFor();
  assert.equal(await tooLong.getByTestId("document-status").textContent(), bigWindow);
  log(
    "PASS  a different model asks every file again, in both directions, and the chip is for the model picked",
  );
  await chip(page, "long-30-pages.pdf")
    .getByRole("button", { name: "Remove long-30-pages.pdf" })
    .click();
  await chip(page, "long-30-pages.pdf").waitFor({ state: "detached" });

  // --- a new chat: the same file against a small window -------------------------------
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await page.getByTestId("model-picker").selectOption(cfg.model);
  await attach(page, "long-30-pages.pdf");
  await chip(page, "long-30-pages.pdf", "too_long").waitFor();
  await page.getByTestId("model-picker").selectOption(cfg.small);
  await page.waitForFunction(() =>
    [...document.querySelectorAll("[data-testid=document-chip]")].some((el) =>
      el.textContent.includes("window is 4,096 tokens"),
    ),
  );
  const smallWindow = await tooLong.getByTestId("document-status").textContent();
  assert.notEqual(smallWindow, bigWindow);
  assert.match(
    smallWindow,
    /window is 4,096 tokens, so there is room for about [\d,]+ tokens of this file\./,
  );
  assert.match(
    await tooLong.getByTestId("document-offer-first").textContent(),
    /^Read the first [\d,]+ tokens \(pages 1 to \d+ of 30\)$/,
  );
  await snap(page, "documents-too-long-dark-desk");
  log(
    "PASS  too long for the window: the numbers are the model's, and the offer is the first pages that fit",
  );

  // --- reload with a file still attached ---------------------------------------------
  await page.reload();
  await page.getByTestId("composer").waitFor();
  assert.equal(await chips(page).count(), 0, "a reloaded page has not asked yet");
  await attach(page, "prices.csv");
  await chip(page, "prices.csv", "whole").waitFor();
  await chip(page, "long-30-pages.pdf").waitFor();
  assert.equal(await chips(page).count(), 2);
  log(
    "PASS  a file attached before a reload is found again, so the page never hides a file Send would carry",
  );
  await chip(page, "long-30-pages.pdf")
    .getByRole("button", { name: "Remove long-30-pages.pdf" })
    .click();
  await chip(page, "long-30-pages.pdf").waitFor({ state: "detached" });
  await context.close();

  // --- pictures: the composer's chips, dark and light, at a desk and a phone ---------
  const sizes = [
    ["desk", DESK, {}],
    ["phone", PHONE, { isMobile: true, hasTouch: true }],
  ];
  for (const scheme of ["dark", "light"]) {
    for (const [size, viewport, extra] of sizes) {
      const { context: c, page: p } = await open(scheme, viewport, extra);
      // A phone keeps its chats behind a button: open the new chat by its address.
      await p.goto(await newChat(c));
      await p.getByTestId("composer").waitFor();
      const names = ["field-notes.md", "quarterly-report.docx", "long-12-pages.pdf", "scan.pdf"];
      await p.getByTestId("attach-input").setInputFiles(names.map((name) => cfg.files[name]));
      await chip(p, "field-notes.md", "whole").waitFor();
      await chip(p, "quarterly-report.docx", "whole").waitFor();
      await chip(p, "long-12-pages.pdf", "choose").waitFor();
      await chip(p, "scan.pdf", "skipped").waitFor();
      await fits(p, `${scheme} ${size}: four files`);
      await tall(p);
      await snap(p, `documents-chips-${scheme}-${size}`);
      const over = await wider(p);
      assert.ok(over <= 1, `${scheme} ${size}: the composer with files is ${over}px too wide`);
      // The same files for a model with a small window, in the window it came in.
      await p.setViewportSize(viewport);
      await p.getByTestId("model-picker").selectOption(cfg.small);
      await chip(p, "long-12-pages.pdf", "too_long").waitFor();
      await settle(p);
      await fits(p, `${scheme} ${size}: four files, a small window`);
      await snap(p, `documents-chips-small-${scheme}-${size}`);
      assert.ok((await wider(p)) <= 1, `${scheme} ${size}: too wide with the small model`);
      await c.close();
    }
  }
  log("PASS  the chips, dark and light, at 1440 and 390, nothing wider than the window");

  // --- pictures: the Sources under an answer ----------------------------------------
  for (const scheme of ["dark", "light"]) {
    for (const [size, viewport, extra] of sizes) {
      const { context: c, page: p } = await open(scheme, viewport, extra);
      await p.goto(chatUrl);
      await p.getByTestId("document-sources").last().waitFor();
      await settle(p);
      await tall(p);
      await snap(p, `documents-sources-${scheme}-${size}`);
      const over = await wider(p);
      assert.ok(over <= 1, `${scheme} ${size}: the sources are ${over}px too wide`);
      await c.close();
    }
  }
  log(
    "PASS  the files under an answer, dark and light, at 1440 and 390, nothing wider than the window",
  );
} catch (failure) {
  // What the page showed when a step did not come, for whoever reads the failure.
  if (latest) {
    await latest.screenshot(shot("documents-failure")).catch(() => undefined);
    const shown = await latest
      .evaluate(() =>
        [...document.querySelectorAll("[data-testid=document-chip]")].map(
          (el) => `${el.dataset.state}: ${el.textContent.replace(/\s+/g, " ").slice(0, 300)}`,
        ),
      )
      .catch(() => []);
    console.log("chips at the failure:", shown);
  }
  throw failure;
} finally {
  await browser.close();
}
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
console.log(`PASS: documents in Chrome; pictures in ${cfg.shots}`);
