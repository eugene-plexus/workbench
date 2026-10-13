/* global process, console, document, getComputedStyle, window, setTimeout, fetch */
// Folders in Chrome (MR7): the whole of it, as a person meets it, against the real app and its
// fake Eugene and gateway. The owner has granted a folder of notes to the person (the test did it
// through the owner's grant API). On the Documents screen the person sees the Folders section with
// the limits said plainly (no file watcher, a quick check that misses an edit keeping a file's size
// and time, no job-site folders, a first read that takes a while) and the folder to add by its name,
// never its path. They add it; a search model starts and holds its answers, so the reading of the
// files can be seen going on (the counts and the bar say how far), then lets them go; the folder is
// synced with when it was checked and when it will be next; its files are listed with their own
// reasons (a picture is skipped, in the server's words), filtered by where they stand and by why,
// and found by a word in their path; what was left out is listed with the rule that left each out
// (always, hidden, temporary, a .gitignore file). A question in a chat is answered from a folder
// file with the Sources naming it by its folder and path. The person edits the file and chooses
// Sync now: the new text answers and the old is gone. They write a pattern in the folder's rules and
// the files it names leave their documents and come back when the pattern goes; a pattern the
// server cannot use is refused in its words under the patterns. They remove the folder (asked
// first, and told its files are never touched), add it again, and the owner takes the grant away:
// the folder says Withdrawn with Dismiss. Pictures of the Folders section, dark and light, at 1440
// and 390.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";
import { join } from "node:path";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
mkdirSync(cfg.shots, { recursive: true });
const shot = (name) => ({ path: `${cfg.shots}/${name}.png` });
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
const errors = [];
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const SAID = cfg.said;
const NAME = cfg.folder.name;
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
 * these from now on. `hold` makes it hold the answers for passages (so meaning can be seen being
 * added), and lets them go when it is false. */
async function listModels(models, hold) {
  const response = await fetch(`${cfg.gateway}/__fake/search-models`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ models, ...(hold === undefined ? {} : { hold }) }),
  });
  assert.ok(response.ok, `the gateway's list of models: ${response.status}`);
}

/** Waits, from here, until something is true (`check` is async). */
async function until(check, what, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await check()) return;
    await new Promise((done) => setTimeout(done, 100));
  }
  assert.fail(`${what} did not happen in time`);
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

/** A picture of the Folders section alone, once the colours and the type have stopped moving. */
async function snapSection(page, name) {
  await tall(page);
  await page.getByTestId("folders-section").screenshot(shot(name));
}

/** What the chip in a chat's header says: its name, which is its words at any width. */
const chipSays = (page, words) =>
  page.waitForFunction(
    (text) =>
      document.querySelector("[data-testid=documents-chip]")?.getAttribute("aria-label") === text,
    words,
    { timeout: 10000 },
  );

const section = (page) => page.getByTestId("folders-section");
const card = (page) => page.getByTestId("folder").first();
const text = async (locator) => ((await locator.textContent()) ?? "").trim();

/** The counts on the open card, by kind: `{files, ready, reading, skipped}`. */
const countsOf = (page) =>
  page
    .getByTestId("folder-count")
    .evaluateAll((els) =>
      Object.fromEntries(els.map((e) => [e.dataset.kind, Number(e.dataset.n)])),
    );

/** Waits until the open card's counts are these. */
const counted = (page, want) =>
  page.waitForFunction(
    (wanted) => {
      const got = Object.fromEntries(
        [...document.querySelectorAll("[data-testid=folder-count]")].map((e) => [
          e.dataset.kind,
          Number(e.dataset.n),
        ]),
      );
      return Object.entries(wanted).every(([kind, n]) => got[kind] === n);
    },
    want,
    { timeout: 40000 },
  );

/** Waits until the card's last check says this: a check that was asked for since. */
const lastSays = (page, pattern) =>
  page.waitForFunction(
    (source) =>
      new RegExp(source).test(
        document.querySelector("[data-testid=folder-last]")?.textContent ?? "",
      ),
    pattern.source,
    { timeout: 40000 },
  );

const rowsOf = (page) => page.getByTestId("folder-file");
/** Waits until the list is that one file and no other: not an earlier list that had one too. */
const onlyPath = (page, path) =>
  page.waitForFunction(
    (wanted) => {
      const rows = [...document.querySelectorAll("[data-testid=folder-file]")];
      return rows.length === 1 && rows[0].getAttribute("data-path") === wanted;
    },
    path,
    { timeout: 15000 },
  );
const pathsOf = (page) =>
  rowsOf(page).evaluateAll((els) => els.map((el) => el.getAttribute("data-path")));

/** Turns Documents on in a new chat and asks, and gives the answer. */
async function askInNewChat(page, words) {
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Documents off");
  await page.getByTestId("documents-hint-on").click();
  await chipSays(page, "Documents (4)");
  await say(page, words);
  return finished(page).last();
}

let seen = null;
try {
  // --- the section, before anything is added ---------------------------------------------------
  const { context, page } = await open("dark", DESK);
  seen = { context, page };
  await page.getByTestId("open-documents").click();
  await page.getByTestId("documents-page").waitFor();
  await section(page).waitFor();
  assert.equal(await text(section(page).getByRole("heading", { level: 2 })), "Folders");
  assert.match(
    await text(page.getByTestId("folders-intro")),
    /^Add a folder the owner gave you\. .*It never changes, adds or deletes anything in the folder\.$/,
  );
  // The limits, said plainly, from the numbers in effect.
  const limits = await text(page.getByTestId("folders-limits"));
  assert.match(
    limits,
    /Folders are not watched: each is checked every \d+ minutes? and on Sync now, so a change can take that long to show\./,
  );
  assert.match(limits, /not job site folders yet\./);
  assert.match(limits, /The first read of a big folder takes a while\./);
  assert.match(limits, /takes up to 10,000 files and 2,048 MB, 12 folders deep;/);
  assert.match(limits, /The owner of this Workbench sets these limits\./);
  // The folder is offered by its name, and its place on the disk is not on the page at all.
  const options = await page.getByTestId("folders-add-select").locator("option").allTextContents();
  assert.deepEqual(options, [NAME]);
  assert.equal(await card(page).count(), 0);
  const everything = await page.locator("main").innerText();
  assert.ok(!everything.includes(cfg.folder.path), "a person is never shown where a folder is");
  assert.doesNotMatch(everything, /\b(RAG|vectors?|embeddings?|chunks?|retriev\w*)\b/i);
  // What to leave out can be written first: a disclosure that starts closed.
  assert.equal(await page.getByTestId("folders-add-rules").getAttribute("open"), null);
  await page.getByTestId("folders-add-rules-toggle").click();
  assert.match(
    await text(page.getByTestId("folders-add-always")),
    /Folders: \.git, \.hg, \.svn, node_modules/,
  );
  await page.getByTestId("folders-add-always-toggle").click();
  assert.match(await text(page.getByTestId("folders-add-count")), /^0 of 100 patterns$/);
  await snapSection(page, "folders-add-dark-desk");
  await page.setViewportSize(DESK);
  await page.getByTestId("folders-add-rules-toggle").click();
  log("PASS  the section says what it is, its limits plainly, and offers the folder by name only");

  // --- add it: a search model holds its answers, so the reading can be seen -------------------
  await listModels([{ id: cfg.searchModel, ready: true }], true);
  await until(
    async () => (await ask(context, "/api/documents")).search.code === "ok",
    "the search model being in use",
  );
  await page.getByTestId("folders-add-go").click();
  await card(page).waitFor();
  // The keyboard goes to the new card, so a screen reader says where it is.
  assert.equal(
    await page.evaluate(
      () => document.activeElement?.tagName + ":" + document.activeElement?.textContent?.trim(),
    ),
    `H3:${NAME}`,
  );
  assert.equal(
    await text(page.getByTestId("folders-add-none")),
    "Every folder you were given is added.",
  );
  assert.equal(await card(page).getAttribute("data-folder-id"), cfg.folder.grantId);
  // It is checked at once: waiting or syncing, then synced.
  await page.locator("[data-testid=folder][data-state=synced]").waitFor({ timeout: 30000 });
  assert.equal(await text(page.getByTestId("folder-badge")), "Synced");
  await counted(page, { files: 5, skipped: 1 });
  const reading = page.getByTestId("folder-reading");
  await page
    .getByTestId("folder-reading")
    .filter({ hasText: "2 files can be searched by words already" })
    .waitFor({ timeout: 30000 });
  assert.equal(
    await text(reading),
    "Reading the files: 3 of 5 are done. 2 files can be searched by words already, and are having their meaning added.",
  );
  const bar = page.getByRole("progressbar", { name: `Files of ${NAME} that are done` });
  assert.equal(await bar.getAttribute("max"), "5");
  assert.equal(await bar.getAttribute("value"), "3");
  assert.deepEqual(await countsOf(page), { files: 5, ready: 2, reading: 2, skipped: 1 });
  await snapSection(page, "folders-reading-dark-desk");
  await page.setViewportSize(DESK);
  log("PASS  the folder is added, checked at once, and says how far its files have been read");

  // The answers are let go: the counts move by themselves, and every file ends ready.
  await listModels([{ id: cfg.searchModel, ready: true }], false);
  await counted(page, { files: 5, ready: 4, reading: 0, skipped: 1 });
  assert.equal(await page.getByTestId("folder-reading").count(), 0);
  assert.match(
    await text(page.getByTestId("folder-status")),
    /^Checked (just now|a minute ago)\.Next check in about (9|10) minutes\.$/,
  );
  assert.match(
    await text(page.getByTestId("folder-last")),
    /^Last check, a scheduled one: 4 new, 1 skipped\. It looked at 5 files in \d+ folders?, and took (under a second|\d+ seconds?)\.$/,
  );
  assert.equal(
    await text(page.getByTestId("folder-left-summary")),
    "Left out: 4 files and 3 folders ignored.",
  );
  assert.match(
    await text(page.getByTestId("folder-holds")),
    /^\d+ passages? · [\d.]+ [KM]iB · Added /,
  );
  assert.match(
    await text(page.getByTestId("folders-totals")),
    /^1 folder · 5 files · \d+ passages · [\d.]+ [KM]iB$/,
  );
  // The folder's files are not in the list of the person's own files, and are counted apart.
  assert.equal(await page.getByTestId("saved-document").count(), 0);
  assert.equal(await page.getByTestId("documents-totals").count(), 0);
  await snapSection(page, "folders-synced-dark-desk");
  await page.setViewportSize(DESK);
  log(
    "PASS  the files end ready by themselves, and the card says when it was checked and when it will be next",
  );

  // --- the folder's files, each with its own reason ----------------------------------------------
  await card(page).getByTestId("folder-files-toggle").click();
  await rowsOf(page).nth(4).waitFor();
  assert.deepEqual(await pathsOf(page), [
    "docs/field-notes.md",
    "docs/field-survey.pdf",
    "docs/quarterly-report.docx",
    "harbour-notes.md",
    "picture.png",
  ]);
  assert.equal(await text(page.getByTestId("folder-files-range")), "1 to 5 of 5");
  const picture = page.locator('[data-testid=folder-file][data-path="picture.png"]');
  assert.equal(await picture.getAttribute("data-state"), "skipped");
  assert.equal(await picture.getAttribute("data-code"), "not_document");
  assert.equal(
    await text(picture.getByTestId("folder-file-status")),
    "Skipped: this file is a picture, not a document. Workbench reads PDF, Word (.docx), Markdown, text, CSV, HTML and code files.",
  );
  const notes = page.locator('[data-testid=folder-file][data-path="harbour-notes.md"]');
  assert.match(
    await text(notes.getByTestId("folder-file-meta")),
    /^Markdown · .* · \d+ passages? · Added /,
  );
  assert.equal(await text(notes.getByTestId("folder-file-status")), "Ready");
  // A file that was skipped has no copy to open.
  assert.equal(await picture.getByTestId("folder-file-open").count(), 0);
  assert.equal(await notes.getByTestId("folder-file-open").count(), 1);
  // A filter for where files stand, one for why, and a word in the path.
  const states = await page.getByTestId("folder-files-state").locator("option").allTextContents();
  assert.deepEqual(states, ["All files (5)", "Ready (4)", "Skipped (1)"]);
  await page.getByTestId("folder-files-state").selectOption("skipped");
  await onlyPath(page, "picture.png");
  assert.deepEqual(await pathsOf(page), ["picture.png"]);
  const reasons = await page.getByTestId("folder-files-reason").locator("option").allTextContents();
  assert.deepEqual(reasons, ["Any reason", "A picture or a sound recording (1)"]);
  await page.getByTestId("folder-files-state").selectOption("all");
  await page.getByTestId("folder-files-find").fill("QUARTERLY");
  await onlyPath(page, "docs/quarterly-report.docx");
  assert.deepEqual(await pathsOf(page), ["docs/quarterly-report.docx"]);
  await page.getByTestId("folder-files-find").fill("");
  await rowsOf(page).nth(4).waitFor();
  // What Workbench read from a file can be looked at.
  await notes.getByTestId("folder-file-text-toggle").click();
  assert.ok((await text(notes.getByTestId("folder-file-text"))).includes(cfg.word));
  await notes.getByTestId("folder-file-text-toggle").click();
  log(
    "PASS  the files are listed with their own reasons, filtered by state and by reason, and found by a word in the path",
  );

  // --- what was left out, and why ---------------------------------------------------------------------
  await card(page).getByTestId("folder-left-toggle").click();
  await page.getByTestId("folder-left-group").nth(3).waitFor();
  const groups = await page
    .getByTestId("folder-left-group")
    .evaluateAll((els) => els.map((el) => el.getAttribute("data-code")));
  assert.deepEqual(groups, [
    "ignored_default",
    "ignored_hidden",
    "ignored_temporary",
    "ignored_gitignore",
  ]);
  const leftBy = async (code) =>
    page
      .locator(`[data-testid=folder-left-group][data-code=${code}] [data-testid=folder-left-item]`)
      .evaluateAll((els) =>
        els.map((el) => el.querySelector("[data-testid=folder-left-path]").textContent),
      );
  assert.deepEqual(await leftBy("ignored_default"), [".git/", "node_modules/", "Thumbs.db"]);
  assert.deepEqual(await leftBy("ignored_hidden"), [".env", ".gitignore"]);
  assert.deepEqual(await leftBy("ignored_temporary"), ["scratch.tmp"]);
  assert.deepEqual(await leftBy("ignored_gitignore"), ["build/"]);
  for (const reason of await page.getByTestId("folder-left-reason").allTextContents()) {
    assert.match(reason, /^Ignored: /);
  }
  await card(page).getByTestId("folder-left-toggle").click();
  await card(page).getByTestId("folder-files-toggle").click();
  log("PASS  what was left out is listed with the rule that left each out, in the server's words");

  // --- a question answered from a file of the folder -----------------------------------------------------
  let answer = await askInNewChat(page, SAID.ask);
  const source = answer.getByTestId("passage-source").first();
  await source.waitFor();
  assert.match(
    await text(source.getByTestId("passage-title")),
    new RegExp(`^${NAME}/harbour-notes\\.md`),
  );
  assert.equal(await source.getAttribute("data-cited"), "true");
  await source.getByTestId("passage-toggle").click();
  assert.ok((await text(source.getByTestId("passage-snippet"))).includes(cfg.word));
  log(
    "PASS  a question is answered from the folder's file, and the Sources name it by its folder and path",
  );

  // --- edit the file and Sync now: the new text answers, the old is gone ----------------------------------
  await page.goto(`${cfg.url}/documents`);
  await card(page).waitFor();
  await counted(page, { files: 5, ready: 4 });
  writeFileSync(join(cfg.folder.path, "harbour-notes.md"), cfg.edited.text);
  await card(page).getByTestId("folder-sync").click();
  await lastSays(page, /^Last check, one you asked for: 1 changed\. /);
  await counted(page, { files: 5, ready: 4, reading: 0 });
  assert.equal(await text(page.getByTestId("folder-badge")), "Synced");
  answer = await askInNewChat(page, SAID.again);
  const again = answer.getByTestId("passage-source").first();
  await again.waitFor();
  assert.match(
    await text(again.getByTestId("passage-title")),
    new RegExp(`^${NAME}/harbour-notes\\.md`),
  );
  await again.getByTestId("passage-toggle").click();
  const snippet = await text(again.getByTestId("passage-snippet"));
  assert.ok(snippet.includes(cfg.edited.word), snippet);
  assert.ok(!snippet.includes(cfg.word), "the old text is never found again");
  log("PASS  Sync now reads an edited file again, and the old text is gone from the answers");

  // --- the rules: a pattern leaves files out, and a pattern the server refuses says why --------------
  await page.goto(`${cfg.url}/documents`);
  await card(page).waitFor();
  await counted(page, { files: 5, ready: 4 });
  await card(page).getByTestId("folder-rules-toggle").click();
  assert.equal(await page.getByTestId("folder-rules-ignore").inputValue(), "");
  assert.equal(await page.getByTestId("folder-rules-hidden").isChecked(), false);
  assert.equal(await page.getByTestId("folder-rules-gitignore").isChecked(), true);
  assert.equal(await page.getByTestId("folder-rules-save").isDisabled(), true);
  await page.getByTestId("folder-rules-ignore").fill("!docs");
  await page.getByTestId("folder-rules-save").click();
  const refusal = page.getByTestId("folder-rules-error");
  await refusal.waitFor();
  assert.equal(
    await text(refusal),
    "Start a pattern with a name, not with !. This list only ignores.",
  );
  assert.equal(await page.getByTestId("folder-rules-ignore").getAttribute("aria-invalid"), "true");
  assert.equal(
    await page.getByTestId("folder-rules-ignore").inputValue(),
    "!docs",
    "what was typed stays",
  );
  await page.getByTestId("folder-rules-ignore").fill("docs/");
  await page.getByTestId("folder-rules-save").click();
  await page.getByTestId("folder-rules-saved").waitFor();
  assert.match(
    await text(page.getByTestId("folder-rules-saved")),
    /Saved\. Workbench is checking the folder again\./,
  );
  // The folder is checked again at once: the files of docs/ leave the documents.
  await counted(page, { files: 2, ready: 1, skipped: 1 });
  const left = (await ask(context, `/api/documents/folders/${cfg.folder.grantId}`)).left;
  assert.ok(
    left.some((item) => item.code === "ignored_list" && item.path === "docs"),
    JSON.stringify(left),
  );
  await card(page).getByTestId("folder-left-toggle").click();
  await page.locator("[data-testid=folder-left-group][data-code=ignored_list]").waitFor();
  assert.match(
    await text(page.locator("[data-testid=folder-left-group][data-code=ignored_list]")),
    /^Your ignore list \(1\)docs\//,
  );
  await card(page).getByTestId("folder-left-toggle").click();
  // The pattern goes, and the files come back.
  await page.getByTestId("folder-rules-ignore").fill("");
  await page.getByTestId("folder-rules-save").click();
  await counted(page, { files: 5, ready: 4, reading: 0, skipped: 1 });
  assert.equal(await page.getByTestId("folder-rules-ignore").inputValue(), "");
  log(
    "PASS  a pattern leaves files out and brings them back, and a pattern the server refuses says why",
  );

  // --- pictures of the section, dark and light, at a desk and a phone -------------------------------------
  const looks = [
    ["light", DESK, {}],
    ["dark", PHONE, { isMobile: true, hasTouch: true }],
    ["light", PHONE, { isMobile: true, hasTouch: true }],
  ];
  for (const [scheme, size, extra] of looks) {
    const where = `${scheme}-${size === DESK ? "desk" : "phone"}`;
    const other = await open(scheme, size, extra);
    const view = other.page;
    await view.goto(`${cfg.url}/documents`);
    await card(view).waitFor();
    await counted(view, { files: 5, ready: 4, reading: 0, skipped: 1 });
    await snapSection(view, `folders-synced-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the section is wider than the window`);
    await view.setViewportSize(size);
    await card(view).getByTestId("folder-files-toggle").click();
    await rowsOf(view).nth(4).waitFor();
    await card(view).getByTestId("folder-left-toggle").click();
    await view.getByTestId("folder-left-group").nth(3).waitFor();
    await card(view).getByTestId("folder-rules-toggle").click();
    await snapSection(view, `folders-open-${where}`);
    assert.ok((await wider(view)) <= 1, `${where}: the open section is wider than the window`);
    await other.context.close();
  }
  await page.setViewportSize(DESK);
  await page.goto(`${cfg.url}/documents`);
  await card(page).waitFor();
  await card(page).getByTestId("folder-files-toggle").click();
  await rowsOf(page).nth(4).waitFor();
  await card(page).getByTestId("folder-left-toggle").click();
  await page.getByTestId("folder-left-group").nth(3).waitFor();
  await card(page).getByTestId("folder-rules-toggle").click();
  await snapSection(page, "folders-open-dark-desk");
  await page.setViewportSize(DESK);
  log("PASS  pictures of the Folders section, dark and light, at a desk and a phone");

  // --- remove the folder: asked first, and the folder is never touched -----------------------------------
  await page.goto(`${cfg.url}/documents`);
  await card(page).waitFor();
  await counted(page, { files: 5, ready: 4 });
  await card(page).getByTestId("folder-remove").click();
  const confirm = page.getByTestId("folder-remove-confirm");
  assert.match(
    await text(confirm),
    /Workbench stops checking it and takes its 5 files out of your documents, so no chat can find them\. The folder and your access to it are not touched; you can add it again\./,
  );
  await confirm.getByTestId("folder-remove-keep").click();
  assert.equal(await card(page).count(), 1, "Keep it keeps it");
  await card(page).getByTestId("folder-remove").click();
  await confirm.getByTestId("folder-remove-go").click();
  await page.waitForFunction(() => document.querySelectorAll("[data-testid=folder]").length === 0);
  assert.equal(
    await page.evaluate(
      () => document.activeElement?.tagName + ":" + document.activeElement?.textContent?.trim(),
    ),
    "H2:Folders",
  );
  assert.deepEqual((await ask(context, "/api/documents/folders")).folders, []);
  // Nothing of it is left in the person's documents, and the folder can be added again.
  assert.equal((await ask(context, "/api/documents")).totals.passagesKept, 0);
  assert.deepEqual(
    await page.getByTestId("folders-add-select").locator("option").allTextContents(),
    [NAME],
  );
  log(
    "PASS  Remove folder asks first, takes its files out of the documents, and leaves the folder as it was",
  );

  // --- the owner takes the grant away: Withdrawn, and Dismiss -------------------------------------------------
  await page.getByTestId("folders-add-go").click();
  await card(page).waitFor();
  await counted(page, { files: 5, ready: 4, reading: 0, skipped: 1 });
  const boss = await open("dark", DESK, {}, cfg.owner);
  const withdrawn = await boss.context.request.delete(
    `${cfg.url}/api/folders/${cfg.folder.grantId}`,
    {
      headers: { Origin: cfg.url, "X-Workbench-Secret": cfg.owner.secret },
    },
  );
  assert.equal(withdrawn.status(), 204);
  await boss.context.close();
  await page.goto(`${cfg.url}/documents`);
  await page.locator("[data-testid=folder][data-state=withdrawn]").waitFor();
  assert.equal(await text(page.getByTestId("folder-badge")), "Withdrawn");
  assert.match(
    await text(page.getByTestId("folder-status")),
    /^The owner took away your access to this folder\. 5 documents from it were taken out of your search\.Taken away (just now|a minute ago)\.$/,
  );
  assert.equal(await page.getByTestId("folder-counts").count(), 0);
  assert.equal(await page.getByTestId("folder-sync").count(), 0);
  assert.equal(await page.getByTestId("folder-remove").count(), 0);
  assert.equal(await page.getByTestId("folder-dismiss").count(), 1);
  assert.equal(
    (await ask(context, "/api/documents")).totals.passagesKept,
    0,
    "its documents are out of the search",
  );
  await snapSection(page, "folders-withdrawn-dark-desk");
  await page.setViewportSize(DESK);
  await page.getByTestId("folder-dismiss").click();
  await page.waitForFunction(() => document.querySelectorAll("[data-testid=folder]").length === 0);
  assert.equal(
    await text(page.getByTestId("folders-add-none")),
    "No folder has been given to you yet. Ask the owner of this Workbench to give you one.",
  );
  assert.equal(
    await page.getByTestId("folders-open-tools").count(),
    0,
    "the person has no Tools to open",
  );
  log(
    "PASS  the owner's withdrawal says Withdrawn with Dismiss, and its documents are out of the search",
  );

  await context.close();
} catch (error) {
  // What the screen and the server said when a step did not come, so a failure can be read.
  if (seen) {
    const said = await seen.page
      .getByTestId("folders-section")
      .innerText({ timeout: 2000 })
      .catch(() => "(no section on the screen)");
    console.log(`THE SECTION SAID:\n${said.slice(0, 3000)}`);
    const kept = await ask(seen.context, "/api/documents/folders").catch(() => null);
    console.log(`THE SERVER SAID: ${JSON.stringify(kept).slice(0, 3000)}`);
    await seen.page.screenshot(shot("failure")).catch(() => undefined);
  }
  throw error;
} finally {
  await browser.close();
}
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
