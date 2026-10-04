/* global process, document, innerWidth, innerHeight, console, sessionStorage, getComputedStyle */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
try {
  const context = await browser.newContext({ permissions: ["clipboard-read", "clipboard-write"] });
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").click();
  const composer = page.getByTestId("composer");
  await composer.fill("Unfinished birdhouse plans");
  const first = page.url();
  await page.reload();
  await composer.waitFor();
  assert.equal(await composer.inputValue(), "Unfinished birdhouse plans");
  await page.getByTestId("new-chat").click();
  await page.waitForURL((url) => url.href !== first);
  await composer.waitFor();
  assert.equal(await composer.inputValue(), "");
  await page.goto(first);
  await composer.waitFor();
  assert.equal(await composer.inputValue(), "Unfinished birdhouse plans");
  await page.getByRole("button", { name: "Rename this chat" }).click();
  await page.getByLabel("Chat name", { exact: true }).fill("Birdhouse plans");
  await page.getByLabel("Save the name").click();
  await page.getByTestId("chat-title").filter({ hasText: "Birdhouse plans" }).waitFor();
  await page.keyboard.press("Control+Alt+f");
  await page.getByRole("searchbox").fill("BIRDHOUSE");
  assert.equal(await page.getByTestId("chat-list").getByRole("button").count(), 1);
  await page.getByLabel("Clear chat search").click();
  await page.keyboard.press("Control+Alt+m");
  assert.equal(await composer.evaluate((node) => node === document.activeElement), true);
  const small = await composer.evaluate((node) => node.clientHeight);
  await composer.fill(
    Array.from({ length: 55 }, (_, i) => `Line ${i}: Help me plan the weekend birdhouse.`).join(
      "\n",
    ),
  );
  assert.ok((await composer.evaluate((node) => node.clientHeight)) > small);
  await page.getByTestId("send").click();
  await page
    .getByTestId("answer")
    .locator(".answer")
    .filter({ hasText: "Hello from the model." })
    .waitFor();
  await page.getByTestId("answer").getByRole("button", { name: "Copy", exact: true }).click();
  await page.getByRole("button", { name: "Copied", exact: true }).waitFor();
  assert.ok((await page.locator("time[datetime]").count()) >= 2);
  const messages = page.getByTestId("messages");
  await messages.evaluate((node) => {
    node.scrollTop = 0;
  });
  await page.getByRole("button", { name: "Jump to latest" }).click();
  await page.waitForFunction(() => {
    const box = document.querySelector('[data-testid="messages"]');
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  });
  const download = page.waitForEvent("download");
  await page.getByLabel("Export chat as Markdown").click();
  const saved = await download;
  assert.equal(saved.suggestedFilename(), "Workbench-Birdhouse plans.md");
  assert.match(readFileSync(await saved.path(), "utf8"), /Hello from the model\./);
  await page.setViewportSize({ width: 320, height: 740 });
  await page.getByRole("button", { name: "Delete this chat", exact: true }).click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.getByRole("button", { name: "Keep it" }).click();
  const layout = await page.evaluate(() => ({
    height: innerHeight,
    page: document.documentElement.scrollHeight,
    boxes: [
      ...document.querySelectorAll(
        "body, #root, main, [data-testid=messages], [data-testid=user-message], [data-testid=composer-area]",
      ),
    ].map((node) => ({
      tag: node.tagName,
      test: node.getAttribute("data-testid"),
      height: node.clientHeight,
      scroll: node.scrollHeight,
      bottom: node.getBoundingClientRect().bottom,
      overflow: getComputedStyle(node).overflow,
    })),
  }));
  assert.ok(layout.page <= layout.height + 1, JSON.stringify(layout));
  await page.screenshot({ path: cfg.screenshot, fullPage: true });
  await composer.fill("A private draft");
  await page.getByRole("button", { name: "Chats", exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.getByRole("link", { name: "Sign in with Eugene", exact: true }).waitFor();
  assert.equal(
    await page.evaluate(() =>
      Object.keys(sessionStorage).some((key) => key.startsWith("workbench-draft:")),
    ),
    false,
  );
  assert.deepEqual(errors, []);
  console.log(
    "PASS: drafts, chat search, shortcuts, composer growth, send, copy, timestamps, scroll, export, 320px layout, sign-out cleanup",
  );
} finally {
  await browser.close();
}
