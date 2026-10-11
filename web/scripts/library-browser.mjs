/* global process, console, document, getComputedStyle, window, URL */
// Saved documents in Chrome (MR4): the whole of it, as a person meets it, against the
// real app and its fake Eugene and gateway. Adds three files in Documents (a PDF whose
// code word is only on page 4, a Markdown file and a Word file) and one that is
// refused, watches them become ready, tries a question without a model and finds page
// 4, starts a chat, is told by the hint that Documents are off, turns them on, sees the
// chip say how many, asks, and reads the answer's Sources: the file and its page, the
// words it matched and the mark that the answer cited it. Asks something the files do
// not answer in a chat that answers only from them, and reads Workbench's own reply;
// then removes the PDF and finds it gone from the list and from the search. Pictures of
// the screen, the drawer and the Sources, dark and light, at a desk and a phone.
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

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext({ colorScheme: scheme, viewport, ...extra });
  // A step that never comes fails in seconds, not in half a minute.
  context.setDefaultTimeout(15000);
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

// The server, asked as the signed-in person (the context carries the cookies).
const headers = { Origin: cfg.url, "X-Workbench-Secret": cfg.secret };
async function ask(context, path) {
  const response = await context.request.get(`${cfg.url}${path}`, { headers });
  assert.ok(response.ok(), `${path}: ${response.status()}`);
  return response.json();
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
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) el.scrollTop = 0;
    }
  });
  await page.waitForTimeout(150);
}

const wider = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

/** A picture, once the colours and the type have stopped moving. */
async function snap(page, name) {
  await settle(page);
  await page.screenshot(shot(name));
}

/** What the chip in a chat's header says: its name, which is its words at any width. */
const chipSays = (page, words) =>
  page.waitForFunction(
    (text) =>
      document.querySelector("[data-testid=documents-chip]")?.getAttribute("aria-label") === text,
    words,
    { timeout: 10000 },
  );

const rows = (page, state) =>
  page.locator(`[data-testid=saved-document]${state ? `[data-state="${state}"]` : ""}`);
const row = (page, name) => rows(page).filter({ hasText: name });

let chatUrl = "";
try {
  // --- the screen, before anything is kept -----------------------------------------
  const { context, page } = await open("dark", DESK);
  await page.getByTestId("open-documents").click();
  await page.getByTestId("documents-page").waitFor();
  assert.equal(new URL(page.url()).pathname, "/documents");
  assert.equal(await page.title(), "Documents · Workbench");
  assert.equal(await page.evaluate(() => document.activeElement?.id), "view-heading");
  assert.match(
    await page.getByTestId("documents-intro").textContent(),
    /Your own files, searched for the words in your question\. Only you can see them\./,
  );
  const headline = await page.getByTestId("documents-search").textContent();
  assert.match(headline, /Searching by words only\./);
  assert.doesNotMatch(headline, /meaning and words|by meaning/i, "words only, and no promise");
  await page.getByTestId("documents-empty").waitFor();
  assert.equal(await page.getByTestId("documents-test").count(), 0);
  assert.equal(await page.getByTestId("documents-ask").count(), 0);
  // What a person reads here is plain: nothing of how it works underneath.
  const everything = await page.locator("main").innerText();
  assert.doesNotMatch(everything, /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*)\b/i);
  await snap(page, "library-empty-dark-desk");
  log("PASS  the screen says what it is and that search is by words only, and has no list yet");

  // --- three files, watched until they are read ------------------------------------
  await page
    .getByTestId("documents-input")
    .setInputFiles([
      cfg.files["harbour-survey.pdf"],
      cfg.files["field-notes.md"],
      cfg.files["quarterly-report.docx"],
    ]);
  await rows(page).nth(2).waitFor();
  await page
    .getByTestId("documents-upload-summary")
    .filter({ hasText: "Added 3 files." })
    .waitFor();
  await rows(page, "ready").nth(2).waitFor({ timeout: 30000 });
  assert.equal(await rows(page, "ready").count(), 3);
  const pdf = row(page, "harbour-survey.pdf");
  assert.match(
    await pdf.getByTestId("saved-document-meta").textContent(),
    /^PDF · [\d.]+ [KM]iB · 5 pages · \d+ passages · Added /,
  );
  assert.match(await pdf.getByTestId("saved-document-status").textContent(), /Ready/);
  assert.match(
    await row(page, "quarterly-report.docx").getByTestId("saved-document-meta").textContent(),
    /^Word · /,
  );
  assert.match(
    await page.getByTestId("documents-totals").textContent(),
    /^3 documents · \d+ passages · /,
  );
  // The caps are the server's own numbers, stated.
  const { limits } = await ask(context, "/api/documents");
  const caps = await page.getByTestId("documents-caps").textContent();
  assert.ok(caps.includes(`A file may be up to ${limits.maxFileBytes / 1048576} MiB.`), caps);
  assert.ok(caps.includes(`up to ${limits.maxPassages.toLocaleString("en-US")} passages`), caps);
  await page.getByTestId("documents-ask").waitFor();
  log(
    "PASS  three files are kept, read in the background, and each ends Ready with its pages and passages",
  );

  // --- a file Workbench does not read, refused in its own words ---------------------
  await page.getByTestId("documents-uploads-clear").click();
  await page.getByTestId("documents-input").setInputFiles(cfg.files["picture.png"]);
  const refused = page.locator("[data-testid=documents-upload][data-state=refused]");
  await refused.waitFor();
  assert.match(
    await refused.getByTestId("documents-upload-words").textContent(),
    /is an image, not a document\. My documents keeps PDF, Word, Markdown, text, CSV, HTML and code files\./,
  );
  assert.equal(await rows(page).count(), 3, "a refused file is not kept");
  await snap(page, "library-refused-dark-desk");
  await page.getByTestId("documents-uploads-clear").click();
  log("PASS  a picture is refused in the server's own words, and the others are untouched");

  // --- Test a question: the passages, with no model --------------------------------
  await page.getByTestId("documents-test-question").fill(SAID.ask);
  await page.getByTestId("documents-test-ask").click();
  const result = page.getByTestId("documents-test-result");
  await result.waitFor();
  assert.equal(await result.getAttribute("data-status"), "found");
  assert.match(
    await page.getByTestId("documents-test-words").textContent(),
    /No model was asked\./,
  );
  const best = page.getByTestId("documents-test-hit").first();
  assert.match(
    await best.getByTestId("documents-test-title").textContent(),
    /^harbour-survey\.pdf, page 4/,
  );
  assert.match(await best.getByTestId("documents-test-matched").textContent(), /Matched: .*code/);
  assert.ok((await best.locator("mark").count()) >= 1, "the words it was found by are marked");
  assert.ok(
    (await best.getByTestId("documents-test-snippet").textContent()).includes(WORD),
    "the snippet is the passage with the code word",
  );
  await best.getByTestId("documents-test-whole").click();
  assert.ok((await best.getByTestId("documents-test-text").textContent()).includes(WORD));
  await best.getByTestId("documents-test-whole").click();
  log(
    "PASS  Test a question finds page 4 of the survey, with the words it matched, and asks no model",
  );
  await page.getByTestId("documents-test-question").fill(SAID.nothing);
  await page.getByTestId("documents-test-ask").click();
  await page
    .getByTestId("documents-test-result")
    .and(page.locator("[data-status=nothing_matched]"))
    .waitFor();
  assert.match(
    await page.getByTestId("documents-test-words").textContent(),
    /Nothing in your documents matched/,
  );
  await page.getByTestId("documents-test-question").fill(SAID.ask);
  await page.getByTestId("documents-test-ask").click();
  await page
    .getByTestId("documents-test-result")
    .and(page.locator("[data-status=found]"))
    .waitFor();
  await tall(page);
  await snap(page, "library-screen-dark-desk");
  await page.setViewportSize(DESK);
  log("PASS  nothing matched is said as a normal result");

  // --- a chat: the hint, the chip, the drawer ----------------------------------------
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Documents off");
  const hint = page.getByTestId("documents-hint");
  await hint.waitFor();
  assert.match(await hint.textContent(), /Your 3 documents are off here\./);
  // One short line, so on a phone it stays beside its two buttons.
  assert.ok((await hint.boundingBox()).height < 40, "the hint is one line at a desk");
  await page.getByTestId("documents-hint-on").click();
  await chipSays(page, "Documents (3)");
  assert.equal(await page.getByTestId("documents-hint").count(), 0, "the hint goes once it is on");
  chatUrl = page.url();
  await page.getByTestId("documents-chip").click();
  const drawer = page.getByTestId("documents-drawer");
  await drawer.waitFor();
  assert.equal(await drawer.getByTestId("documents-switch").isChecked(), true);
  assert.equal(await drawer.getByTestId("documents-only-switch").isChecked(), false);
  assert.match(
    await drawer.getByTestId("documents-counts").textContent(),
    /Your documents: 3 documents ready to search \(\d+ passages\)/,
  );
  await snap(page, "library-drawer-dark-desk");
  await page.getByTestId("documents-close").click();
  log(
    "PASS  Documents are off in a new chat, the hint says so and turns them on, and the chip counts",
  );

  // --- ask, and read the Sources -------------------------------------------------------
  await say(page, SAID.ask);
  const answer = finished(page).last();
  const first = answer.getByTestId("passage-source").first();
  await first.waitFor();
  assert.match(
    await first.getByTestId("passage-title").textContent(),
    /^harbour-survey\.pdf, page 4/,
  );
  assert.equal(await first.getAttribute("data-cited"), "true");
  assert.match(await first.getByTestId("passage-cited").textContent(), /Cited/);
  assert.match(
    await answer.getByTestId("passage-sent").textContent(),
    /^Sent to the model: \d passages? from \d documents?/,
  );
  await first.getByTestId("passage-toggle").click();
  assert.ok((await first.getByTestId("passage-snippet").textContent()).includes(WORD));
  assert.ok(
    (await first.getByTestId("passage-snippet").locator("mark").count()) >= 1,
    "the words the passage was found by are marked where they stand",
  );
  assert.match(await first.getByTestId("passage-matched").textContent(), /Matched: .*code/);
  // It lists what was sent, and never says the model used it.
  assert.doesNotMatch(await answer.getByTestId("sources").textContent(), /\bused\b/i);
  await page.evaluate(() => document.querySelector("[data-testid=sources]")?.scrollIntoView());
  await snap(page, "library-sources-dark-desk");
  log(
    "PASS  Sources names the file and page 4, the words matched, and marks the passage the answer cited",
  );

  // --- a chat that answers only from the documents, and nothing matches ---------------
  // A new chat: a question borrows the words of the one before it in the same chat, so
  // that its own words alone are what is searched for.
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Documents off");
  await page.getByTestId("documents-chip").click();
  await page.getByTestId("documents-switch").check();
  await chipSays(page, "Documents (3)");
  await page.getByTestId("documents-only-switch").check();
  await page.getByTestId("documents-only-switch").and(page.locator(":enabled")).waitFor();
  assert.equal(await page.getByTestId("documents-only-switch").isChecked(), true);
  await page.getByTestId("documents-close").click();
  const strictId = new URL(page.url()).pathname.split("/").pop();
  await say(page, SAID.nothing);
  // What the server kept: Workbench's own words, and no model.
  const kept = (await ask(context, `/api/chats/${strictId}`)).messages;
  const own = kept[kept.length - 1];
  assert.equal(own.finish, "documents_only", JSON.stringify(own).slice(0, 600));
  assert.equal(own.model, null);
  assert.equal(own.retrieval.status, "nothing_matched");
  const reply = page.getByTestId("workbench-reply").last();
  await reply.waitFor();
  assert.match(
    await reply.getByTestId("workbench-reply-text").textContent(),
    /I could not find that in your documents\./,
  );
  assert.match(await reply.textContent(), /did not ask the model/);
  assert.match(
    await page.getByTestId("search-nothing").last().textContent(),
    /Nothing in your documents matched\./,
  );
  assert.equal(await reply.getByTestId("ask-anyway").isVisible(), true);
  await snap(page, "library-reply-dark-desk");
  await reply.getByTestId("ask-anyway").click();
  // Another version of that answer is the model's own, and the path shows it.
  await page.waitForFunction(
    () =>
      !document.querySelector("[data-testid=workbench-reply]") &&
      !document.querySelector('[data-testid=answer][data-status="running"]'),
  );
  assert.equal(
    await page.getByTestId("workbench-reply").count(),
    0,
    "the model's own version is shown now",
  );
  log(
    "PASS  with Documents only, nothing matched is Workbench's own reply, and Ask the model anyway asks",
  );

  // --- the other looks: dark and light, a desk and a phone ---------------------------------
  const looks = [
    ["dark", PHONE, { isMobile: true, hasTouch: true }],
    ["light", DESK, {}],
    ["light", PHONE, { isMobile: true, hasTouch: true }],
  ];
  for (const [scheme, size, extra] of looks) {
    const where = `${scheme}-${size === DESK ? "desk" : "phone"}`;
    const other = await open(scheme, size, extra);
    const view = other.page;
    await view.goto(`${cfg.url}/documents`);
    await rows(view, "ready").nth(2).waitFor();
    await view.getByTestId("documents-test-question").fill(SAID.ask);
    await view.getByTestId("documents-test-ask").click();
    await view
      .getByTestId("documents-test-result")
      .and(view.locator("[data-status=found]"))
      .waitFor();
    await tall(view);
    await snap(view, `library-screen-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the screen is wider than the window`);
    await view.setViewportSize(size);
    await view.goto(chatUrl);
    await finished(view).first().waitFor();
    await chipSays(view, "Documents (3)");
    await view.getByTestId("documents-chip").click();
    await view.getByTestId("documents-drawer").waitFor();
    await snap(view, `library-drawer-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the drawer is wider than the window`);
    await view.getByTestId("documents-close").click();
    const mine = finished(view).first();
    await mine.getByTestId("passage-toggle").first().click();
    await view.evaluate(() => document.querySelector("[data-testid=sources]")?.scrollIntoView());
    await snap(view, `library-sources-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the Sources are wider than the window`);
    await other.context.close();
  }
  log(
    "PASS  pictures of the screen, the drawer and the Sources, dark and light, at a desk and a phone",
  );

  // --- removing is real ------------------------------------------------------------------
  await page.goto(`${cfg.url}/documents`);
  await rows(page, "ready").nth(2).waitFor();
  const survey = row(page, "harbour-survey.pdf");
  await survey.getByTestId("saved-document-remove").click();
  const confirm = survey.getByTestId("saved-document-remove-confirm");
  assert.match(await confirm.textContent(), /This cannot be undone\./);
  await confirm.getByTestId("saved-document-remove-keep").click();
  assert.equal(await rows(page).count(), 3, "Keep it keeps it");
  await survey.getByTestId("saved-document-remove").click();
  await survey.getByTestId("saved-document-remove-go").click();
  await page.waitForFunction(
    () => document.querySelectorAll("[data-testid=saved-document]").length === 2,
  );
  assert.equal(await row(page, "harbour-survey.pdf").count(), 0);
  const left = (await ask(context, "/api/documents")).documents.map((d) => d.name).sort();
  assert.deepEqual(left, ["field-notes.md", "quarterly-report.docx"]);
  // The same question again: the Word file also says "code word", so it may be found, and
  // the survey, with its page 4, may not.
  await page.getByTestId("documents-test-question").fill(SAID.ask);
  await page.getByTestId("documents-test-ask").click();
  await page.getByTestId("documents-test-result").waitFor();
  await page.waitForFunction(
    (word) =>
      !document.querySelector("[data-testid=documents-test-result]")?.textContent.includes(word),
    WORD,
  );
  const after = await page.getByTestId("documents-test-result").textContent();
  assert.ok(!after.includes("harbour-survey") && !after.includes(WORD), after);
  log("PASS  Remove asks first, and a removed file is gone from the list and from the search");
  await context.close();
} finally {
  await browser.close();
}
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
