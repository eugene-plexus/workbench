/* global process, console, document, getComputedStyle, window, URL */
// Memory in Chrome (MR1, walked again for MV1-MV4): the whole of it, as a person meets it, against the
// real app and its fake Eugene and gateway. Says "Remember that ...", sees the
// toast, sees the unpinned note chosen as relevant memory for that turn, reads the
// note on the Memory page with its source, pins it, starts a new chat
// and sees the chip say a note was sent, reads the exact text in the drawer,
// turns memory off for the chat, keeps and takes back a note from a message,
// meets an outside model, deletes the note; then takes pictures of the page and
// the drawer, dark and light, at a desk and a phone.
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
async function tell(context, path, data) {
  const response = await context.request.post(`${cfg.url}${path}`, { headers, data });
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}

const finished = (page) => page.locator('[data-testid=answer]:not([data-status="running"])');

/** Says something in the open chat and waits for its answer. */
async function say(page, words) {
  const before = await finished(page).count();
  const composer = page.getByTestId("composer");
  await composer.fill(words);
  await page.getByTestId("send").click();
  await page.waitForFunction(
    (count) =>
      document.querySelectorAll('[data-testid=answer]:not([data-status="running"])').length > count,
    before,
    { timeout: 20000 },
  );
}

const chip = (page) => page.getByTestId("memory-chip");
const chipSays = (page, words) => chip(page).filter({ hasText: words }).waitFor({ timeout: 10000 });

/** What the drawer says the latest answer was sent: the page is read again first, since the
 * chip is read when a message is sent and not again when its answer ends. Closes the drawer. */
async function turnSays(page, words) {
  await page.reload();
  await chip(page).waitFor();
  await chip(page).click();
  const turn = page.getByTestId("memory-drawer").getByTestId("memory-this-turn");
  await turn.getByTestId("memory-turn-summary").filter({ hasText: "1 note chosen" }).waitFor();
  assert.match(await turn.getByTestId("memory-turn-notes").textContent(), new RegExp(words));
  await page.getByTestId("memory-close").click();
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

let drawerText = "";
try {
  // --- the first words: "Remember that ..." -------------------------------------
  const { context, page } = await open("dark", DESK);
  await page.getByTestId("new-chat").click();
  await page.getByTestId("composer").waitFor();
  await chipSays(page, "Memory");
  await say(page, SAID.remember);
  const toast = page.getByTestId("memory-toast");
  await toast.waitFor();
  assert.match(await toast.textContent(), new RegExp(`Remembered:.*${WORD}`));
  await toast.getByRole("button", { name: "Undo" }).waitFor();
  await toast.getByRole("button", { name: "Manage" }).waitFor();
  assert.equal(await toast.getAttribute("role"), "status");
  await page.screenshot(shot("memory-toast-dark-desk"));
  log("PASS  a message that begins 'Remember that' keeps the note, and the toast says so");
  const first = page.url();
  await turnSays(page, WORD);
  log("PASS  the drawer says the unpinned note was chosen for the message, as relevant memory");

  // --- words that are not kept, and a note forgotten on purpose --------------------
  await say(page, SAID.card);
  const refused = page.getByTestId("memory-toast").filter({ hasText: "Not remembered" });
  await refused.waitFor();
  assert.equal(await refused.getAttribute("role"), "alert");
  const refusal = await refused.textContent();
  assert.match(
    refusal,
    /That looks like a password or key, so Workbench will not keep it\. It looks like a card number\./,
  );
  assert.match(refusal, /Your message was still sent\./);
  await page.screenshot(shot("memory-refused-dark-desk"));
  assert.equal((await ask(context, "/api/memory")).usage.notes, 1);
  log("PASS  a card number is not kept, and the toast says why in plain words");

  await say(page, SAID.cat);
  await page.getByTestId("memory-toast").filter({ hasText: "Biscuit" }).waitFor();
  await say(page, SAID.cat2);
  await page.getByTestId("memory-toast").filter({ hasText: "Marmalade" }).waitFor();
  const texts = async () => {
    const { sections } = await ask(context, "/api/memory");
    return [...sections.always, ...sections.other].map((n) => n.text);
  };
  assert.equal((await texts()).length, 3);
  // "Forget my cat" finds both cats and deletes neither: the person picks one.
  await say(page, SAID.forget);
  const asking = page.getByTestId("memory-toast").filter({ hasText: "Delete one of these notes?" });
  await asking.waitFor();
  assert.equal((await texts()).length, 3, "forget deleted on a guess");
  assert.ok(!(await asking.textContent()).includes(WORD), "forget offered a note it did not match");
  await page.screenshot(shot("memory-forget-dark-desk"));
  const buttons = asking.getByRole("button", { name: /^Delete note: my .*cat/ });
  assert.equal(await buttons.count(), 2);
  // The second one listed, not the first: only the one that was picked goes.
  const picked = await buttons.last().getAttribute("aria-label");
  await buttons.last().click();
  await page.getByTestId("memory-toast").filter({ hasText: "Deleted:" }).waitFor();
  const left = await texts();
  assert.equal(left.length, 2);
  assert.ok(left.includes(`my code word is ${WORD}`));
  assert.ok(
    !left.some((text) => picked.endsWith(text)) && left.some((text) => /cat/.test(text)),
    `the note that was picked is still there, or the other one went too: ${picked} / ${left}`,
  );
  await say(page, SAID.forget2);
  const again = page.getByTestId("memory-toast").filter({ hasText: "Delete one of these notes?" });
  await again.waitFor();
  await again.getByRole("button", { name: /^Delete note: my .*cat/ }).click();
  await page.getByTestId("memory-toast").filter({ hasText: "Deleted:" }).waitFor();
  assert.deepEqual(await texts(), [`my code word is ${WORD}`]);
  log("PASS  forget lists the notes it might mean, and deletes only the one that was picked");

  // --- the Memory page ------------------------------------------------------------
  await page.getByTestId("open-memory").click();
  await page.getByRole("heading", { name: "Memory", level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/memory");
  assert.equal(await page.title(), "Memory · Workbench");
  const note = page.getByTestId("memory-note");
  await note.waitFor();
  assert.match(await note.getByTestId("memory-note-text").textContent(), new RegExp(WORD));
  const from = note.getByTestId("memory-note-from");
  assert.match(await from.textContent(), /From /);
  assert.match(await from.getByRole("link").textContent(), /Remember that my code word/);
  assert.match(await page.getByTestId("memory-usage-notes").textContent(), /1 of 20,000 notes/);
  assert.match(
    await page.getByTestId("memory-usage-tokens").textContent(),
    /Pinned: \d+ of [\d,]+ tokens, in every chat\. Relevant: up to [\d,]+ tokens a turn/,
  );
  // The settings are not on this page (MV4): a button goes to them, on the Settings page.
  assert.equal(await page.getByTestId("memory-use-switch").count(), 0);
  assert.equal(await page.getByTestId("memory-external-ask").count(), 0);
  assert.equal(await page.getByTestId("memory-person-off").count(), 0);
  await page.getByTestId("memory-open-settings").click();
  await page.getByRole("heading", { name: "Settings", level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/settings");
  assert.equal(new URL(page.url()).hash, "#memory");
  assert.equal(await page.title(), "Settings · Workbench");
  await page.getByTestId("settings-memory").waitFor();
  assert.equal(await page.getByTestId("memory-use-switch").isChecked(), true);
  assert.equal(await page.getByTestId("memory-external-ask").isChecked(), true);
  const sections = await page
    .getByRole("heading", { level: 2 })
    .evaluateAll((all) => all.map((h) => h.textContent.trim()));
  assert.deepEqual(
    sections.filter((name) => /^(Memory|Documents|Connections)/.test(name)),
    ["Memory", "Documents", "Connections to other apps"],
  );
  await page.getByTestId("settings-open-memory").click();
  await page.getByRole("heading", { name: "Memory", level: 1 }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/memory");
  await note.waitFor();
  log(
    "PASS  Settings holds Memory's, Documents' and Connections' settings; the Memory page holds none",
  );
  log("PASS  the Memory page lists the note with its kind, date and the chat it came from");
  // Pinned, the note is in the chat's own copy from the start, in every chat (the unpinned
  // way, chosen again for each message, was the first chat's).
  await note.getByTestId("memory-note-pin").click();
  await page.getByTestId("memory-section-always").getByTestId("memory-note").waitFor();
  assert.equal((await ask(context, "/api/memory")).sections.always.length, 1);
  log("PASS  Pin makes it a note that is always used");
  await from.getByRole("link").click();
  await page.waitForURL(first);
  log("PASS  the 'from' link goes back to the chat");

  // --- a new chat: the chip, the drawer, the exact text ---------------------------
  await page.getByTestId("new-chat").click();
  await chipSays(page, "1 note ready");
  await say(page, SAID.ask);
  await chipSays(page, "1 note sent");
  await chip(page).click();
  const drawer = page.getByTestId("memory-drawer");
  await drawer.waitFor();
  assert.match(await drawer.getByTestId("memory-sent").textContent(), new RegExp(WORD));
  await drawer.getByTestId("memory-exact-details").locator("summary").click();
  drawerText = await drawer.getByTestId("memory-exact").textContent();
  assert.ok(drawerText.includes(WORD), drawerText);
  assert.ok(drawerText.startsWith("What you know about this person"), drawerText);
  await page.screenshot(shot("memory-drawer-exact-dark-desk"));
  log("PASS  the drawer shows the note that was sent, and the exact text the model was given");

  // --- the chat's own switch -------------------------------------------------------
  await drawer.getByTestId("memory-switch").uncheck();
  await chipSays(page, "Memory off in this chat");
  await drawer.getByTestId("memory-status").getByText("Memory is off for this chat.").waitFor();
  // By keyboard: the switch is held while the server answers, which takes the
  // keyboard from it in a real browser, and it is still on the switch after.
  const onSwitch = () =>
    page.waitForFunction(
      () => document.activeElement?.getAttribute("data-testid") === "memory-switch",
    );
  await drawer.getByTestId("memory-switch").focus();
  await page.keyboard.press("Space");
  await chipSays(page, "1 note sent");
  await onSwitch();
  await page.keyboard.press("Space");
  await chipSays(page, "Memory off in this chat");
  await onSwitch();
  await page.getByTestId("memory-close").click();
  await say(page, SAID.french);
  log("PASS  turning memory off for the chat takes effect, and the chip says so");

  // --- Remember this, and Undo -----------------------------------------------------
  const mine = page.getByTestId("user-message").last();
  await mine.hover();
  await mine.getByTestId("remember-this").click();
  const kept = page.getByTestId("memory-toast");
  await kept.waitFor();
  assert.match(await kept.textContent(), /Remembered:.*And in French\?/);
  assert.equal((await ask(context, "/api/memory")).usage.notes, 2);
  await kept.getByRole("button", { name: "Undo" }).click();
  await kept.getByText("Undone.").waitFor();
  assert.equal((await ask(context, "/api/memory")).usage.notes, 1);
  log("PASS  Remember this keeps a message's words as a note, and Undo takes them back");

  // --- the note from a message that is too long ------------------------------------
  await say(page, SAID.long);
  const longOne = page.getByTestId("user-message").last();
  await longOne.hover();
  await longOne.getByTestId("remember-this").click();
  const editor = page.getByTestId("remember-editor");
  await editor.waitFor();
  assert.match(await editor.getByTestId("remember-help").textContent(), /at most 240 characters/);
  assert.match(await editor.getByTestId("remember-count").textContent(), /\d+ of 240/);
  await page.screenshot(shot("memory-remember-editor-dark-desk"));
  await editor.getByRole("button", { name: "Cancel" }).click();
  assert.equal((await ask(context, "/api/memory")).usage.notes, 1);
  log(
    "PASS  a message over 240 characters is not cut quietly: the person is told, and may shorten it",
  );

  // --- a model that runs outside the machines --------------------------------------
  await page.getByTestId("new-chat").click();
  await chipSays(page, "1 note ready");
  await page.getByTestId("model-picker").selectOption(cfg.outside);
  await chipSays(page, "Memory not sent: outside model");
  await chip(page).click();
  const outside = page.getByTestId("memory-drawer");
  await outside.getByTestId("memory-status").getByText("Your notes were not sent").waitFor();
  await page.screenshot(shot("memory-outside-dark-desk"));
  await outside.getByTestId("memory-send-here").click();
  await outside.getByTestId("memory-stop-sending").waitFor();
  await page.getByTestId("memory-close").click();
  await say(page, SAID.outside);
  await chipSays(page, "1 note sent");
  log("PASS  notes are held back from an outside model until the person says Send for this chat");

  // --- delete the note -------------------------------------------------------------
  await page.getByTestId("open-memory").click();
  await page.getByTestId("memory-note").waitFor();
  await page.getByTestId("memory-note-delete").click();
  await page.getByTestId("memory-empty").waitFor();
  await page.getByTestId("memory-deleted").waitFor();
  await page.getByTestId("memory-undo").click();
  await page.getByTestId("memory-note").waitFor();
  await page.getByTestId("memory-note-delete").click();
  await page.getByTestId("memory-empty").waitFor();
  assert.equal((await ask(context, "/api/memory")).usage.notes, 0);
  log(
    "PASS  deleting a note takes it from the page for good, and Undo brings it back for a moment",
  );
  await page.getByRole("button", { name: "Back to chat" }).click();
  await chipSays(page, "no notes yet");
  log("PASS  a chat with nothing to send says so");

  // --- the person's own copy: export, and delete all --------------------------------
  const kept2 = cfg.notes.slice(0, 2);
  for (const body of kept2) {
    await tell(context, "/api/memory/notes", {
      text: body.text,
      kind: body.kind,
      pinned: body.pinned,
    });
  }
  await page.goto(`${cfg.url}/memory`);
  await page.getByTestId("memory-note").first().waitFor();
  const markdown = page.waitForEvent("download");
  await page.getByTestId("memory-export-md").click();
  const mdFile = await markdown;
  assert.equal(mdFile.suggestedFilename(), "workbench-memory.md");
  const mdText = readFileSync(await mdFile.path(), "utf8");
  assert.match(mdText, /^# Memory/);
  for (const body of kept2) assert.ok(mdText.includes(body.text), mdText);
  const asJson = page.waitForEvent("download");
  await page.getByTestId("memory-export-json").click();
  const jsonFile = await asJson;
  assert.equal(jsonFile.suggestedFilename(), "workbench-memory.json");
  const exported = JSON.parse(readFileSync(await jsonFile.path(), "utf8"));
  assert.deepEqual(exported.notes.map((n) => n.text).sort(), kept2.map((n) => n.text).sort());
  assert.ok(exported.notes.every((n) => n.source === "typed" && "createdAt" in n));
  log("PASS  the notes are exported as Markdown and as JSON, fetched with the secret");

  await page.getByTestId("memory-delete-all").click();
  await page.getByRole("group", { name: "Delete all memory?" }).waitFor();
  assert.equal(await page.getByTestId("memory-delete-all-go").isDisabled(), true);
  await page.getByTestId("memory-delete-all-typed").fill("DELETE");
  await page.getByTestId("memory-delete-all-go").click();
  await page.getByTestId("memory-said").filter({ hasText: "Deleted 2 notes" }).waitFor();
  await page.getByTestId("memory-empty").waitFor();
  assert.equal((await ask(context, "/api/memory")).usage.notes, 0);
  log("PASS  delete all memory asks for the word, and then leaves nothing");

  // --- pictures: the empty page ----------------------------------------------------
  await context.close();
  const schemes = ["dark", "light"];
  const sizes = [
    ["desk", DESK, {}],
    ["phone", PHONE, { isMobile: true, hasTouch: true }],
  ];
  for (const scheme of schemes) {
    for (const [size, viewport, extra] of sizes) {
      const { context: c, page: p } = await open(scheme, viewport, extra);
      await p.goto(`${cfg.url}/memory`);
      await p.getByTestId("memory-empty").waitFor();
      await tall(p);
      await p.screenshot(shot(`memory-empty-${scheme}-${size}`));
      const over = await wider(p);
      assert.ok(over <= 1, `${scheme} ${size}: the empty Memory page is ${over}px too wide`);
      await c.close();
    }
  }

  // --- pictures: a page with notes in it, and a chat that was sent them -------------
  const seed = await open("dark", DESK);
  const own = (await ask(seed.context, "/api/chats")).chats;
  assert.ok(own.length >= 3);
  const [older, newer] = [own[own.length - 1].id, own[0].id];
  for (const body of cfg.notes) {
    const chatId = body.chat === "older" ? older : body.chat === "newer" ? newer : undefined;
    await tell(seed.context, "/api/memory/notes", {
      text: body.text,
      kind: body.kind,
      pinned: body.pinned,
      ...(chatId ? { source: "button", chatId } : {}),
    });
  }
  await seed.page.getByTestId("new-chat").click();
  await chipSays(seed.page, "notes ready");
  await say(seed.page, SAID.pictures);
  await chipSays(seed.page, "notes sent");
  const pictured = seed.page.url();
  await seed.context.close();

  for (const scheme of schemes) {
    for (const [size, viewport, extra] of sizes) {
      const { context: c, page: p } = await open(scheme, viewport, extra);
      await p.goto(`${cfg.url}/memory`);
      await p.getByTestId("memory-note").first().waitFor();
      await tall(p);
      await p.screenshot(shot(`memory-page-${scheme}-${size}`));
      const over = await wider(p);
      assert.ok(over <= 1, `${scheme} ${size}: the Memory page is ${over}px too wide`);

      await p.setViewportSize(viewport);
      await p.goto(pictured);
      await chipSays(p, "notes sent");
      await chip(p).click();
      const d = p.getByTestId("memory-drawer");
      await d.getByTestId("memory-sent").waitFor();
      await settle(p);
      await p.screenshot(shot(`memory-drawer-${scheme}-${size}`));
      const around = await wider(p);
      assert.ok(around <= 1, `${scheme} ${size}: the drawer is ${around}px too wide`);
      await d.getByTestId("memory-exact-details").locator("summary").click();
      await settle(p);
      await p.screenshot(shot(`memory-drawer-exact-${scheme}-${size}`));
      await c.close();
    }
  }
  log(
    "PASS  the page and the drawer, dark and light, at 1440 and 390, nothing wider than the window",
  );
  console.log(`DRAWER_TEXT:${JSON.stringify(drawerText)}`);
} finally {
  await browser.close();
}
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
console.log(`PASS: memory in Chrome; pictures in ${cfg.shots}`);
