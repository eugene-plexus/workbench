/* global process, console, document, getComputedStyle, window, URL, fetch */
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
//
// Search by meaning (MR5): there is no search model at first, and the screen says words
// only, and where to get one (a link for the owner, and who to ask for anyone else). Then
// the owner starts one, as they would in Eugene's console (the fake gateway's hand), and
// the same screens say meaning and words: each document is read already, and is seen being
// given its meaning (the gateway holds the answers for a while) and then ready; a question
// with none of a passage's words finds it by what it says, in Test a question (and one way
// at a time) and in a chat's Sources; the drawer says how it is searched. Then the search
// model stops, and the open screen names it by itself, and words search goes on. Pictures
// of the screen and the Sources, dark and light, at 1440 and 390.
//
// A result sorter (MR6b): a long file is added so that a question has passages to sort, and
// the owner lists a sorter that reads slowly. The Documents screen says nothing of a sorter
// until there is one, then says it is being measured, and by itself, in seconds, why it does not
// sort: the numbers it was measured at and Sort time, in the server's own sentence, and Test a
// question says the same of the question it was asked, with no sorter's place on any passage and
// the sorter asked nothing. Sort time is raised (the owner's setting, in Eugene's console): the
// headline says "sorted by" the sorter, the notice says where it runs and how fast it reads, and
// Test a question says how many passages were put in order, how long that took, and each
// passage's place by the sorter beside its places by words and by meaning, in the sorter's order;
// a chat's drawer says it, and Sources say what found each passage. Then the sorter stops and
// the screen names it (the owner is linked to Backends, a person is told to ask), a question is
// answered in the order the search found, and the sorter is asked nothing; and when it is turned
// off the screen says nothing of one. Pictures, dark and light, at 1440 and 390.
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
const MODEL = cfg.searchModel;
const SORTER = cfg.sorter;
const log = (line) => console.log(line);

async function open(scheme, viewport, extra = {}, who = cfg) {
  const context = await browser.newContext({ colorScheme: scheme, viewport, ...extra });
  // A step that never comes fails in seconds, not in half a minute.
  context.setDefaultTimeout(15000);
  await context.addCookies(who.cookies);
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${who.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

// The server, asked as the signed-in person (the context carries the cookies).
async function ask(context, path, who = cfg) {
  const headers = { Origin: cfg.url, "X-Workbench-Secret": who.secret };
  const response = await context.request.get(`${cfg.url}${path}`, { headers });
  assert.ok(response.ok(), `${path}: ${response.status()}`);
  return response.json();
}

/** The owner starts, stops or changes the search model in Eugene's console: the gateway lists
 * these from now on. `hold` makes it hold the answers for passages (so meaning can be seen
 * being added), and lets them go when it is false. */
async function listModels(models, hold) {
  const response = await fetch(`${cfg.gateway}/__fake/search-models`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ models, ...(hold === undefined ? {} : { hold }) }),
  });
  assert.ok(response.ok, `the gateway's list of models: ${response.status}`);
}

/** The owner lists, slows, stops or removes a result sorter in Eugene's console: the gateway lists
 * these from now on, and a sorter reads `rate` tokens a second (as Workbench counts them). */
async function listSorters(sorters, rate) {
  const response = await fetch(`${cfg.gateway}/__fake/sorters`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sorters, ...(rate === undefined ? {} : { rate }) }),
  });
  assert.ok(response.ok, `the gateway's list of sorters: ${response.status}`);
}

/** What the gateway's sorters were asked: the sorts of a question, and the small test of how fast
 * one reads. */
async function sorterCalls() {
  const response = await fetch(`${cfg.gateway}/__fake/sorter-calls`);
  assert.ok(response.ok, `the gateway's sorter calls: ${response.status}`);
  return response.json();
}

/** The owner changes a setting of Workbench in Eugene's console (a null puts the default back). */
async function setting(values) {
  const response = await fetch(`${cfg.url}/v1/config`, {
    method: "PATCH",
    headers: { "content-type": "application/json", authorization: `Bearer ${cfg.admin}` },
    body: JSON.stringify(values),
  });
  assert.ok(response.ok, `the setting ${JSON.stringify(values)}: ${response.status}`);
  const done = await response.json();
  assert.deepEqual(done.rejected, [], `a setting was refused: ${JSON.stringify(done)}`);
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

/** Every document says exactly this: they are all read and have all their meaning. */
const allSay = (page, pattern) =>
  page.waitForFunction(
    (source) =>
      [...document.querySelectorAll("[data-testid=saved-document-status]")].every((s) =>
        new RegExp(source).test((s.textContent ?? "").trim()),
      ),
    pattern.source,
    { timeout: 30000 },
  );

/** Test a question in the open Documents screen, and wait for the result. */
async function tryQuestion(page, words, status = "found") {
  await page.getByTestId("documents-test-question").fill(words);
  await page.getByTestId("documents-test-ask").click();
  await page
    .getByTestId("documents-test-result")
    .and(page.locator(`[data-status=${status}]`))
    .waitFor();
}

const hitOf = (page, name) =>
  page.getByTestId("documents-test-hit").filter({ hasText: name }).first();

/** What each tried passage says of where each way put it, in the order they are listed. */
const placesOf = (page) =>
  page
    .getByTestId("documents-test-hit")
    .evaluateAll((hits) =>
      hits.map(
        (hit) => hit.querySelector("[data-testid=documents-test-places]")?.textContent ?? "",
      ),
    );

/** Where the sorter put each passage that has a place by it, in the order they are listed. */
const sorterPlaces = (said) =>
  said
    .map((words) => /the sorter put it (\d+)(?:st|nd|rd|th)/.exec(words)?.[1])
    .filter(Boolean)
    .map(Number);

let chatUrl = "";
let meaningUrl = "";
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
  assert.match(headline, /Searching by words only\. No search model is running\./);
  assert.match(headline, /No search model is set up\./);
  assert.doesNotMatch(headline, /meaning and words|by meaning/i, "words only, and no promise");
  // What words only finds, said so it promises no more.
  assert.match(
    await page.getByTestId("documents-search-help").textContent(),
    /“costs” does not find “cost”, and it does not look for meaning\./,
  );
  // A person who is not the owner has no console to go to: they are told to ask the owner.
  assert.match(
    await page.getByTestId("documents-ask-owner").textContent(),
    /Ask the owner of this Workbench to set one up in Eugene's console\./,
  );
  assert.equal(await page.getByTestId("documents-get-search-model").count(), 0);
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
  // Read while no search model ran: found by words only, and the row says why.
  assert.match(
    await pdf.getByTestId("saved-document-status").textContent(),
    /^\s*Ready for words only: no search model was running when it was read\.\s*$/,
  );
  assert.match(
    await row(page, "quarterly-report.docx").getByTestId("saved-document-meta").textContent(),
    /^Word · /,
  );
  assert.match(
    await page.getByTestId("documents-totals").textContent(),
    /^3 documents · \d+ passages · /,
  );
  assert.equal(
    await page.getByTestId("documents-meaning-totals").count(),
    0,
    "no passage has meaning, and none is owed it: nothing is said of it",
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
  // With no search model set up there is no second way to try: one way at a time is not offered.
  assert.equal(await page.getByTestId("documents-test-ways").count(), 0);
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
  assert.match(await best.getByTestId("documents-test-via").textContent(), /^Found by words$/);
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by words only\.$/,
  );
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
  // The drawer says how they are searched: by words only, and why.
  const mode = drawer.getByTestId("documents-search-mode");
  assert.match(await mode.textContent(), /Searching by words only\. No search model is running\./);
  assert.equal(await mode.getAttribute("data-mode"), "words");
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
  assert.equal(await first.getAttribute("data-via"), "words");
  assert.match(await first.getByTestId("passage-via").textContent(), /found by words/);
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
  assert.match(await answer.getByTestId("passage-facts").textContent(), /Found by words only\./);
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
  // The drawer shows what the server now says once it has read it again: wait for that, then
  // see that it stays (a switch that came back unchecked would be a refusal said nowhere).
  await page.waitForFunction(
    () => document.querySelector("[data-testid=documents-only-switch]")?.checked === true,
  );
  await page.waitForTimeout(300);
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
  assert.match(
    await page.getByTestId("search-nothing-mode").last().textContent(),
    /Searched by words only\./,
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

  // --- search by meaning: the owner is linked to where to get a search model -----------------
  {
    const boss = await open("dark", DESK, {}, cfg.owner);
    await boss.page.goto(`${cfg.url}/documents`);
    await boss.page.getByTestId("documents-search").waitFor();
    const { consoleUrl } = await ask(boss.context, "/api/me", cfg.owner);
    assert.ok(consoleUrl, "the owner has Eugene's console to go to");
    const link = boss.page.getByTestId("documents-get-search-model");
    await link.waitFor();
    assert.equal(await link.getAttribute("href"), `${consoleUrl.replace(/\/$/, "")}/library/`);
    assert.equal(await link.getAttribute("target"), "_blank");
    assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
    assert.match(await link.textContent(), /^\s*Get a search model\s*$/);
    assert.match(
      await boss.page.getByTestId("documents-search").textContent(),
      /Eugene's console downloads models; Workbench cannot\./,
    );
    assert.equal(
      await boss.page.getByTestId("documents-ask-owner").count(),
      0,
      "the owner is not told to ask the owner",
    );
    await tall(boss.page);
    await snap(boss.page, "meaning-none-owner-dark-desk");
    await boss.context.close();
  }
  log("PASS  with no search model set up, the owner is linked to Eugene's console to get one");

  // --- the owner starts a search model, and the answers for passages are held for a while ---
  await listModels([{ id: MODEL, ready: true }], true);
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: "Searching by meaning and words." })
    .waitFor();
  assert.match(
    await page.getByTestId("documents-search-model").textContent(),
    new RegExp(`^In use: ${MODEL} \\(automatic\\)$`),
  );
  assert.match(
    await page.getByTestId("documents-search-where").textContent(),
    /It runs on your own machines, so what is in your documents stays on them\./,
  );
  assert.equal(
    await page.getByTestId("documents-search").getAttribute("data-mode"),
    "meaning and words",
  );
  assert.equal(
    await page.getByTestId("documents-search-help").count(),
    0,
    "what words only finds is not said once meaning is in use",
  );
  assert.match(await page.getByTestId("documents-intro").textContent(), /and for what it means\./);
  // Each document was read before there was a search model: the screen takes them up by itself,
  // and says how far each has got. The gateway holds the answers, so it can be seen.
  const adding = page
    .getByTestId("saved-document-status")
    .filter({ hasText: /^Ready for words\. Adding meaning: 0 of \d+ passages\.$/ });
  await adding.first().waitFor({ timeout: 20000 });
  assert.equal(await adding.count(), 3, "every document is having its meaning added");
  assert.match(
    await page.getByTestId("documents-search-adding").textContent(),
    /^Adding meaning: 0 of \d+ passages\.$/,
  );
  assert.match(
    await page.getByTestId("documents-meaning-totals").textContent(),
    /^Meaning is added to 0 of \d+ passages; \d+ are still to do\.$/,
  );
  assert.equal(
    (await rows(page, "searchable").count()) + (await rows(page, "embedding").count()),
    3,
  );
  await tall(page);
  await snap(page, "meaning-adding-dark-desk");
  await page.setViewportSize(DESK);
  log("PASS  documents read before any search model ran are taken up, and say how far meaning is");

  // The answers are let go: the screen asks again by itself, and every document ends Ready.
  await listModels([{ id: MODEL, ready: true }], false);
  await allSay(page, /^Ready$/);
  assert.equal(await rows(page, "ready").count(), 3);
  assert.equal(await page.getByTestId("documents-search-adding").count(), 0);
  assert.match(
    await page.getByTestId("documents-meaning-totals").textContent(),
    /^Meaning is added to every passage\.$/,
  );
  log("PASS  every document ends Ready with its meaning, without the page being reloaded");

  // --- a file whose passage says what a question asks, in other words ------------------------
  await page.getByTestId("documents-input").setInputFiles(cfg.files["harbour-office.md"]);
  await rows(page).nth(3).waitFor();
  await allSay(page, /^Ready$/);
  await page.getByTestId("documents-uploads-clear").click();
  assert.equal(await rows(page, "ready").count(), 4);

  // --- Test a question: a passage found by meaning alone -------------------------------------
  await tryQuestion(page, SAID.meaning);
  const office = hitOf(page, "harbour-office.md");
  assert.match(
    await office.getByTestId("documents-test-via").textContent(),
    /^Found by meaning: none of your words are in it$/,
  );
  assert.equal(await office.getByTestId("documents-test-via").getAttribute("data-via"), "meaning");
  assert.equal(
    await office.getByTestId("documents-test-matched").count(),
    0,
    "no word of the question is in it, so none is said to be matched",
  );
  assert.equal(await office.locator("mark").count(), 0);
  assert.match(
    await office.getByTestId("documents-test-places").textContent(),
    /^Meaning put it \d+(st|nd|rd|th) \(closeness 0\.\d\d\)\.$/,
  );
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning and words\.$/,
  );
  assert.match(
    await page.getByTestId("documents-test-words").textContent(),
    /No model was asked\./,
    "the question is embedded by the search model, which is not a model that answers",
  );
  assert.ok(
    (await office.getByTestId("documents-test-snippet").textContent()).includes("vessel registry"),
  );
  log("PASS  Test a question finds a passage with none of the question's words, by meaning");

  // One way at a time, as an expert: words find nothing for it, and meaning alone finds it.
  await page.getByTestId("documents-test-ways-toggle").click();
  await page.getByTestId("documents-test-way-words").check();
  assert.match(
    await page.getByTestId("documents-test-ways-toggle").textContent(),
    /Try one way at a time: words only/,
  );
  await tryQuestion(page, SAID.meaning, "nothing_matched");
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by words only\. You chose this\.$/,
  );
  await page.getByTestId("documents-test-way-meaning").check();
  await tryQuestion(page, SAID.meaning);
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning only\. You chose this\.$/,
  );
  assert.ok((await page.getByTestId("documents-test-hit").count()) >= 1);
  for (const via of await page
    .getByTestId("documents-test-via")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-via")))) {
    assert.equal(via, "meaning", "meaning alone finds each by meaning");
  }
  await page.getByTestId("documents-test-way-both").check();
  // A passage with the question's words, and near it by meaning, is found by both.
  await tryQuestion(page, SAID.ask);
  const survey = hitOf(page, "harbour-survey.pdf");
  assert.match(
    await survey.getByTestId("documents-test-via").textContent(),
    /^Found by both words and meaning$/,
  );
  assert.match(await survey.getByTestId("documents-test-matched").textContent(), /Matched: .*code/);
  assert.match(
    await survey.getByTestId("documents-test-places").textContent(),
    /^Words put it 1st, meaning put it \d+(st|nd|rd|th) \(closeness 0\.\d\d\)\.$/,
  );
  log("PASS  one way at a time shows what each finds, and a passage with both says both");

  // --- a chat: the drawer says how documents are searched, and the answer says how each was found ---
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Documents off");
  await page.getByTestId("documents-hint-on").click();
  await chipSays(page, "Documents (4)");
  meaningUrl = page.url();
  await page.getByTestId("documents-chip").click();
  const drawer2 = page.getByTestId("documents-drawer");
  await drawer2.waitFor();
  const how = drawer2.getByTestId("documents-search-mode");
  assert.match(await how.textContent(), /^Searching by meaning and words\.$/);
  assert.equal(await how.getAttribute("data-mode"), "meaning and words");
  await snap(page, "meaning-drawer-dark-desk");
  await page.getByTestId("documents-close").click();
  await say(page, SAID.meaning);
  const found = finished(page).last();
  const byMeaning = found.locator("[data-testid=passage-source][data-via=meaning]").first();
  await byMeaning.waitFor();
  assert.match(
    await byMeaning.getByTestId("passage-title").textContent(),
    /^harbour-office\.md/,
    "the passage that says what the question asks is the first the answer lists",
  );
  assert.match(await byMeaning.getByTestId("passage-via").textContent(), /found by meaning/);
  await byMeaning.getByTestId("passage-toggle").click();
  assert.match(
    await byMeaning.getByTestId("passage-found").textContent(),
    /^Found by meaning: none of your words are in it$/,
  );
  assert.equal(await byMeaning.getByTestId("passage-matched").count(), 0);
  assert.ok(
    (await byMeaning.getByTestId("passage-snippet").textContent()).includes("vessel registry"),
  );
  assert.match(
    await found.getByTestId("passage-facts").textContent(),
    new RegExp(`Found by meaning and words, with the search model ${MODEL}\\.`),
  );
  assert.match(
    await byMeaning.getByTestId("passage-toggle").getAttribute("aria-label"),
    /^\[1\] harbour-office\.md.*, found by meaning/,
  );
  await page.evaluate(() => document.querySelector("[data-testid=sources]")?.scrollIntoView());
  await snap(page, "meaning-sources-dark-desk");
  log(
    "PASS  a chat asked what the passage says is sent it, and Sources says it was found by meaning",
  );

  // --- pictures: the screen and the Sources, dark and light, at 1440 and 390 -----------------
  for (const [scheme, size, extra] of [
    ["dark", DESK, {}],
    ["dark", PHONE, { isMobile: true, hasTouch: true }],
    ["light", DESK, {}],
    ["light", PHONE, { isMobile: true, hasTouch: true }],
  ]) {
    const where = `${scheme}-${size === DESK ? "desk" : "phone"}`;
    const other = await open(scheme, size, extra);
    const view = other.page;
    await view.goto(`${cfg.url}/documents`);
    await rows(view, "ready").nth(3).waitFor();
    await tryQuestion(view, SAID.meaning);
    await tall(view);
    await snap(view, `meaning-screen-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the screen is wider than the window`);
    await view.setViewportSize(size);
    await view.goto(meaningUrl);
    await finished(view).first().waitFor();
    await chipSays(view, "Documents (4)");
    const mine = finished(view).first();
    await mine.getByTestId("passage-toggle").first().click();
    await view.evaluate(() => document.querySelector("[data-testid=sources]")?.scrollIntoView());
    await snap(view, `meaning-sources-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the Sources are wider than the window`);
    await other.context.close();
  }
  log("PASS  pictures of the screen and the Sources with meaning, dark and light, at 1440 and 390");

  // --- the search model stops: the open screen names it, and words search goes on --------------
  await page.goto(`${cfg.url}/documents`);
  await rows(page, "ready").nth(3).waitFor();
  await listModels([{ id: MODEL, ready: false }]);
  // Nobody pressed anything: the screen looks again by itself, each half minute.
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: "Searching by words only. No search model is running." })
    .waitFor({ timeout: 45000 });
  assert.equal(
    (await page.getByTestId("documents-search-detail").textContent()).trim(),
    `The search model ${MODEL} is not running. Start it in Eugene's console under Backends.`,
  );
  assert.equal(await page.getByTestId("documents-search-model").count(), 0);
  assert.match(
    await page.getByTestId("documents-search-help").textContent(),
    /it does not look for meaning/,
  );
  // What they have of meaning stays, and words are not in doubt.
  assert.equal(await rows(page, "ready").count(), 4);
  await tryQuestion(page, SAID.ask);
  assert.match(
    await page.getByTestId("documents-test-meaning").textContent(),
    new RegExp(`the search model ${MODEL} is not running`),
  );
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by words only\.$/,
  );
  assert.match(
    await hitOf(page, "harbour-survey.pdf").getByTestId("documents-test-via").textContent(),
    /^Found by words$/,
  );
  await tall(page);
  await snap(page, "meaning-stopped-dark-desk");
  await page.setViewportSize(DESK);
  // The owner is linked to Backends, where it is started.
  const boss = await open("dark", DESK, {}, cfg.owner);
  await boss.page.goto(`${cfg.url}/documents`);
  const backends = boss.page.getByTestId("documents-open-backends");
  await backends.waitFor();
  const { consoleUrl } = await ask(boss.context, "/api/me", cfg.owner);
  // The console calls this page Backends and keeps its old route, /inference.
  assert.equal(await backends.getAttribute("href"), `${consoleUrl.replace(/\/$/, "")}/inference/`);
  await boss.context.close();
  // And it is back: the next look says so.
  await listModels([{ id: MODEL, ready: true }]);
  log("PASS  a search model that stopped is named by the open screen, and words search goes on");

  // --- a result sorter (MR6b): a long file, so that a question has passages enough to sort -----
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: "Searching by meaning and words." })
    .waitFor();
  await page.getByTestId("documents-input").setInputFiles(cfg.files["handbook.md"]);
  await rows(page).nth(4).waitFor();
  await allSay(page, /^Ready$/);
  await page.getByTestId("documents-uploads-clear").click();
  assert.equal(await rows(page, "ready").count(), 5);
  // No sorter is set up: the screen says nothing of one, and a question is searched as it was.
  assert.equal(
    await page.getByTestId("documents-sort").count(),
    0,
    "no sorter, nothing said of one",
  );
  assert.match(
    await page.getByTestId("documents-search-headline").textContent(),
    /^Searching by meaning and words\.$/,
  );
  await tryQuestion(page, SAID.handbook);
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning and words\.$/,
  );
  assert.equal(await page.getByTestId("documents-test-sort").count(), 0);
  assert.equal(await page.getByTestId("documents-test-sorted").count(), 0);
  for (const words of await placesOf(page)) assert.doesNotMatch(words, /sorter/);
  log("PASS  with no sorter set up, the screen and Test a question say nothing of one");

  // The owner lists a sorter that reads about 1,500 tokens a second, and Sort time is a second:
  // the screen is told it is being measured, and looks again by itself in seconds.
  await setting({ documentsSortSeconds: 1 });
  await listSorters([{ id: SORTER, ready: true }], 1500);
  const listed = Date.now();
  await page.goto(`${cfg.url}/documents`);
  const sortBox = page.getByTestId("documents-sort");
  await sortBox.and(page.locator("[data-code=measuring]")).waitFor();
  assert.equal(
    (await page.getByTestId("documents-sort-reason").textContent()).trim(),
    `Measuring how fast ${SORTER} sorts, to tell whether it is quick enough.`,
  );
  assert.equal(
    await page.getByTestId("documents-sort-model").count(),
    0,
    "a sorter that is being measured is not said to be in use",
  );
  await sortBox.and(page.locator("[data-code=over_budget]")).waitFor();
  assert.ok(
    Date.now() - listed < 20000,
    "the screen looked again by itself in seconds, and not each half minute",
  );
  // Too slow for Sort time: the server's own sentence with the numbers it was decided by, and the
  // speed on the screen is the speed it measured (to two figures).
  const slowSaid = (await page.getByTestId("documents-sort-reason").textContent()).trim();
  const slow = (await ask(context, "/api/documents")).search.sort;
  assert.equal(slow.code, "over_budget");
  assert.equal(slow.budgetSeconds, 1);
  assert.ok(
    slow.ratePerSecond > 500 && slow.ratePerSecond < 1600,
    `measured at ${slow.ratePerSecond}`,
  );
  const slowWords = new RegExp(
    `^Not sorted: ${SORTER} sorts about ([\\d,]+) tokens a second here, so sorting the 18 passages a question usually finds \\(about 300 tokens each\\) would add about ([\\d.]+) s to each answer, and Sort time is 1 s\\. Raise Sort time in Workbench's settings \\(in Eugene's console\\) to sort anyway\\.$`,
  ).exec(slowSaid);
  assert.ok(slowWords, slowSaid);
  const shown = Number(slowWords[1].replaceAll(",", ""));
  assert.ok(
    Math.abs(shown - slow.ratePerSecond) <= slow.ratePerSecond * 0.06,
    `the screen says ${shown} tokens a second and the server measured ${slow.ratePerSecond}`,
  );
  assert.match(
    await page.getByTestId("documents-sort-speed-from").textContent(),
    /^How fast it reads was measured by a quick test before any question was sorted\.$/,
  );
  // Not in use, not sorted, and not a fault: the headline and the notice say so.
  assert.equal(await page.getByTestId("documents-sort-model").count(), 0);
  assert.equal(
    await page.getByTestId("documents-search").getAttribute("data-mode"),
    "meaning and words",
    "not sorted, so not said to be",
  );
  assert.match(
    await page.getByTestId("documents-search-headline").textContent(),
    /^Searching by meaning and words\.$/,
  );
  assert.equal(await sortBox.evaluate((el) => el.classList.contains("notice--warn")), false);
  // A question: the same words with the numbers of its passages, the passages as the search found
  // them, and the sorter asked nothing.
  await tryQuestion(page, SAID.handbook);
  const notSorted = page.getByTestId("documents-test-sort");
  await notSorted.waitFor();
  assert.match(
    await notSorted.textContent(),
    new RegExp(
      `^Not sorted: ${SORTER} sorts about [\\d,]+ tokens a second here, so sorting these \\d+ passages \\([\\d,]+ tokens\\) would take about [\\d.]+ s, and Sort time is 1 s\\. Raise Sort time in Workbench's settings \\(in Eugene's console\\) to sort anyway\\.$`,
    ),
  );
  assert.equal(await notSorted.getAttribute("data-code"), "over_budget");
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning and words\.$/,
  );
  assert.equal(await page.getByTestId("documents-test-sorted").count(), 0);
  assert.ok((await page.getByTestId("documents-test-hit").count()) >= 3);
  for (const words of await placesOf(page)) assert.doesNotMatch(words, /sorter/);
  const slowCalls = await sorterCalls();
  assert.equal(slowCalls.sorts, 0, "the sorter was asked nothing while it was too slow");
  assert.ok(slowCalls.probes >= 1, "how fast it reads was measured by a quick test");
  await tall(page);
  await snap(page, "sort-over-budget-dark-desk");
  await page.setViewportSize(DESK);
  {
    const phone = await open("dark", PHONE, { isMobile: true, hasTouch: true });
    const view = phone.page;
    await view.goto(`${cfg.url}/documents`);
    await view
      .getByTestId("documents-sort")
      .and(view.locator("[data-code=over_budget]"))
      .waitFor({ timeout: 30000 });
    await tryQuestion(view, SAID.handbook);
    await view.getByTestId("documents-test-sort").waitFor();
    await tall(view);
    await snap(view, "sort-over-budget-dark-phone");
    assert.ok((await wider(view)) <= 1, "the screen with a sorter too slow is wider than a phone");
    await phone.context.close();
  }
  log(
    "PASS  a sorter too slow for Sort time is said so by itself, with the numbers, on the screen and for a question, and is asked nothing",
  );

  // Sort time is raised: the headline says the sorter, and the notice where it runs and how fast.
  await setting({ documentsSortSeconds: 30 });
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: `Searching by meaning and words, sorted by ${SORTER}.` })
    .waitFor();
  assert.equal(
    await page.getByTestId("documents-search").getAttribute("data-mode"),
    `meaning and words, sorted by ${SORTER}`,
  );
  assert.equal(
    (await page.getByTestId("documents-sort-model").textContent()).trim(),
    `In use: ${SORTER} (automatic)`,
  );
  assert.equal(
    (await page.getByTestId("documents-sort-where").textContent()).trim(),
    "It runs on your own machines, so what is in your documents stays on them. Automatic means the first result sorter that is running on your own machines.",
  );
  assert.match(
    await page.getByTestId("documents-sort-speed").textContent(),
    new RegExp(
      `^${SORTER} sorts about [\\d,]+ tokens a second here, so sorting the 18 passages a question usually finds \\(about 300 tokens each\\) adds about [\\d.]+ s to each answer, within Sort time \\(30 s\\)\\.$`,
    ),
  );
  assert.match(
    await page.getByTestId("documents-sort-speed-from").textContent(),
    /^How fast it reads (was measured by a quick test before any question was sorted|is measured from the sorts Workbench has made)\.$/,
  );
  assert.equal(await page.getByTestId("documents-sort-reason").count(), 0);
  assert.equal(await sortBox.evaluate((el) => el.classList.contains("notice--warn")), false);
  // Test a question: how it was searched with the sorter, how many passages it put in order and how
  // long that took, and each passage's place by it beside its places by words and by meaning, in its
  // order.
  await tryQuestion(page, SAID.handbook);
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    new RegExp(`^Searched by meaning and words, sorted by ${SORTER}\\.$`),
  );
  assert.equal(
    await page.getByTestId("documents-test-result").getAttribute("data-mode"),
    `meaning and words, sorted by ${SORTER}`,
  );
  assert.match(
    await page.getByTestId("documents-test-sorted").textContent(),
    /^The sorter put \d+ passages in order in [\d.]+ s \(Sort time is 30 s\)\.$/,
  );
  assert.equal(await page.getByTestId("documents-test-sort").count(), 0);
  const placed = await placesOf(page);
  const byTheSorter = sorterPlaces(placed);
  assert.ok(byTheSorter.length >= 3, `places by the sorter: ${JSON.stringify(placed)}`);
  assert.equal(byTheSorter[0], 1, "the passage the sorter put first is the first listed");
  assert.deepEqual(
    [...byTheSorter].sort((a, b) => a - b),
    byTheSorter,
    "the passages are listed in the sorter's order",
  );
  for (const words of placed) {
    assert.match(words, /\b(Words|Meaning|words|meaning) put it \d+(st|nd|rd|th)\b/);
    // Where the sorter put it is a place, and never a score.
    if (words.includes("the sorter put it")) {
      assert.match(words, /the sorter put it \d+(st|nd|rd|th)\.$/, words);
    }
  }
  assert.ok(
    placed.some((words) =>
      /(Words|words) put it .*(meaning put it .*the sorter put it)/.test(words),
    ),
    "a passage says where words, meaning and the sorter put it",
  );
  assert.equal((await sorterCalls()).sorts, 1, "the question was sorted once");
  await tall(page);
  await snap(page, "sort-sorted-dark-desk");
  await page.setViewportSize(DESK);
  log(
    "PASS  with Sort time raised the screen says the sorter, where it runs and how fast it reads, and Test a question says each passage's place by it",
  );

  // Pictures, dark and light, at 1440 and 390.
  for (const [scheme, size, extra] of [
    ["dark", DESK, {}],
    ["dark", PHONE, { isMobile: true, hasTouch: true }],
    ["light", DESK, {}],
    ["light", PHONE, { isMobile: true, hasTouch: true }],
  ]) {
    const where = `${scheme}-${size === DESK ? "desk" : "phone"}`;
    const other = await open(scheme, size, extra);
    const view = other.page;
    await view.goto(`${cfg.url}/documents`);
    await view.getByTestId("documents-sort-model").waitFor();
    await tryQuestion(view, SAID.handbook);
    await view.getByTestId("documents-test-sorted").waitFor();
    await tall(view);
    await snap(view, `sort-screen-${where}`);
    assert.ok(
      (await wider(view)) <= 1,
      `${where}: the screen with a sorter is wider than the window`,
    );
    await other.context.close();
  }
  log("PASS  pictures of the screen with a sorter, dark and light, at 1440 and 390");

  // A chat: the drawer says the sorter, and the answer says how its passages were found.
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Documents off");
  await page.getByTestId("documents-hint-on").click();
  await chipSays(page, "Documents (5)");
  await page.getByTestId("documents-chip").click();
  const drawer3 = page.getByTestId("documents-drawer");
  await drawer3.waitFor();
  const sortedHow = drawer3.getByTestId("documents-search-mode");
  assert.equal(
    (await sortedHow.textContent()).trim(),
    `Searching by meaning and words, sorted by ${SORTER}.`,
  );
  assert.equal(await sortedHow.getAttribute("data-mode"), `meaning and words, sorted by ${SORTER}`);
  await page.getByTestId("documents-close").click();
  await say(page, SAID.handbook);
  const sortedAnswer = finished(page).last();
  await sortedAnswer.getByTestId("passage-source").first().waitFor();
  const sortedFacts = await sortedAnswer.getByTestId("passage-facts").textContent();
  assert.ok(
    sortedFacts.includes(
      `Found by meaning and words, sorted by ${SORTER}, with the search model ${MODEL}.`,
    ),
    sortedFacts,
  );
  assert.doesNotMatch(
    await sortedAnswer.getByTestId("sources").textContent(),
    /Not sorted|The sorter put|the sorter put it/,
    "under an answer, Sources say what was sent and how it was found, and nothing more of the sorter",
  );
  log("PASS  a chat's drawer and the answer's Sources say the passages were sorted, and by what");

  // The sorter stops (the owner stops it in Eugene's console): the open screen names it by itself,
  // the owner is linked to where it is started and a person is told to ask, and a question is
  // answered in the order the search found, with the sorter asked nothing.
  const before = (await sorterCalls()).sorts;
  await listSorters([{ id: SORTER, ready: false }]);
  await page.goto(`${cfg.url}/documents`);
  await sortBox.and(page.locator("[data-code=not_running]")).waitFor();
  assert.equal(
    (await page.getByTestId("documents-sort-reason").textContent()).trim(),
    `Not sorted: the sorter ${SORTER} is not running. Start it in Eugene's console under Backends.`,
  );
  assert.equal(await sortBox.evaluate((el) => el.classList.contains("notice--warn")), true);
  assert.match(
    await page.getByTestId("documents-search-headline").textContent(),
    /^Searching by meaning and words\.$/,
  );
  assert.equal(await page.getByTestId("documents-sort-model").count(), 0);
  assert.equal(await page.getByTestId("documents-sort-open-backends").count(), 0);
  assert.match(
    await page.getByTestId("documents-sort-ask-owner").textContent(),
    /^Ask the owner of this Workbench to fix this in Eugene's console\.$/,
  );
  await tryQuestion(page, SAID.handbook);
  assert.equal(
    (await page.getByTestId("documents-test-sort").textContent()).trim(),
    `Not sorted: the sorter ${SORTER} is not running. Start it in Eugene's console under Backends.`,
  );
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning and words\.$/,
  );
  assert.ok((await page.getByTestId("documents-test-hit").count()) >= 3, "the passages are listed");
  for (const words of await placesOf(page)) assert.doesNotMatch(words, /sorter/);
  assert.equal((await sorterCalls()).sorts, before, "a sorter that is stopped is asked nothing");
  await tall(page);
  await snap(page, "sort-stopped-dark-desk");
  await page.setViewportSize(DESK);
  {
    const boss = await open("dark", DESK, {}, cfg.owner);
    await boss.page.goto(`${cfg.url}/documents`);
    const backends = boss.page.getByTestId("documents-sort-open-backends");
    await backends.waitFor();
    const { consoleUrl } = await ask(boss.context, "/api/me", cfg.owner);
    assert.equal(
      await backends.getAttribute("href"),
      `${consoleUrl.replace(/\/$/, "")}/inference/`,
    );
    assert.equal(await backends.getAttribute("target"), "_blank");
    assert.equal(await backends.getAttribute("rel"), "noopener noreferrer");
    assert.equal(await boss.page.getByTestId("documents-sort-ask-owner").count(), 0);
    await boss.context.close();
  }
  log(
    "PASS  a sorter that stopped is named by the open screen, the owner is linked to Backends, and the passages are listed as the search found them",
  );

  // It is back, and then turned off: the screen says it sorts again, and then says nothing of one.
  await listSorters([{ id: SORTER, ready: true }]);
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: `sorted by ${SORTER}.` })
    .waitFor();
  await setting({ documentsSortModel: "off" });
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: /^Searching by meaning and words\.$/ })
    .waitFor();
  assert.equal(await page.getByTestId("documents-sort").count(), 0, "turned off, and nothing said");
  await tryQuestion(page, SAID.handbook);
  assert.match(
    await page.getByTestId("documents-test-mode").textContent(),
    /^Searched by meaning and words\.$/,
  );
  assert.equal(await page.getByTestId("documents-test-sort").count(), 0);
  assert.equal(await page.getByTestId("documents-test-sorted").count(), 0);
  assert.equal((await sorterCalls()).sorts, before, "a sorter that is off is asked nothing");
  // The defaults are put back, and the sorter is gone, before the rest of the run.
  await setting({ documentsSortModel: null, documentsSortSeconds: null });
  await listSorters([]);
  const handbookRow = row(page, "handbook.md");
  await handbookRow.getByTestId("saved-document-remove").click();
  await handbookRow.getByTestId("saved-document-remove-go").click();
  await page.waitForFunction(
    () => document.querySelectorAll("[data-testid=saved-document]").length === 4,
  );
  log(
    "PASS  a sorter that is back is said to sort again, and one that is turned off is not said at all",
  );

  // --- the harbour office's file is removed, with its meaning ------------------------------------
  await page.goto(`${cfg.url}/documents`);
  await page
    .getByTestId("documents-search-headline")
    .filter({ hasText: "Searching by meaning and words." })
    .waitFor();
  const officeRow = row(page, "harbour-office.md");
  await officeRow.getByTestId("saved-document-remove").click();
  await officeRow.getByTestId("saved-document-remove-go").click();
  await page.waitForFunction(
    () => document.querySelectorAll("[data-testid=saved-document]").length === 3,
  );
  await tryQuestion(page, SAID.meaning, "found");
  assert.equal(
    await page.getByTestId("documents-test-hit").filter({ hasText: "harbour-office.md" }).count(),
    0,
    "a removed file is not found by meaning either",
  );

  // --- removing is real ------------------------------------------------------------------
  await page.goto(`${cfg.url}/documents`);
  await rows(page, "ready").nth(2).waitFor();
  const survey2 = row(page, "harbour-survey.pdf");
  await survey2.getByTestId("saved-document-remove").click();
  const confirm = survey2.getByTestId("saved-document-remove-confirm");
  assert.match(await confirm.textContent(), /This cannot be undone\./);
  await confirm.getByTestId("saved-document-remove-keep").click();
  assert.equal(await rows(page).count(), 3, "Keep it keeps it");
  await survey2.getByTestId("saved-document-remove").click();
  await survey2.getByTestId("saved-document-remove-go").click();
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
