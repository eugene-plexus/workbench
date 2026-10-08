/* global process, console, document */
// The Images screen in Chrome (workbench-media-screens.md §9): make an image,
// close the tab while it is being made, find it kept in a new tab with what
// was asked beside what came back, send it to a chat, remove it there and
// attach another, go Back, delete it.
// The fake gateway holds each image for 2 s and answers a 3x2 PNG.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
try {
  const context = await browser.newContext();
  await context.addCookies(cfg.cookies);
  const errors = [];
  const open = async () => {
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
    return page;
  };

  let page = await open();
  await page.getByTestId("open-media").click();
  await page.waitForURL(/\/media\/images$/);
  const picker = page.getByTestId("image-model");
  await picker.waitFor();
  assert.equal(await picker.inputValue(), "", "no model is picked on a first visit");
  await picker.selectOption("openrouter/flux");
  assert.match(await page.getByTestId("where-it-runs").textContent(), /^Runs on OpenRouter/);
  await page.getByTestId("image-prompt").fill("a red barn");
  await page.getByTestId("make-image").click();
  await page.locator('[data-testid=media-item][data-status="running"]').waitFor();

  // Close the tab while the gateway is still making it.
  await page.close();
  page = await open();
  await page.goto(`${cfg.url}/media/images`);
  const item = page.locator('[data-testid=media-item][data-status="done"]');
  await item.waitFor({ timeout: 15000 });
  const image = item.getByTestId("bin-image");
  await image.waitFor();
  await page.waitForFunction(
    () => document.querySelector("[data-testid=bin-image]")?.naturalWidth > 0,
  );
  assert.equal(await image.evaluate((img) => img.naturalWidth), 3, "the kept image is shown");
  assert.equal(await item.getByTestId("size-words").textContent(), "Asked 1024 × 1024, got 3 × 2");
  assert.match(await page.getByTestId("bin-total").textContent(), /Your bins hold \d+ bytes\./);
  // The picker remembers the model chosen last time, per person.
  assert.equal(await page.getByTestId("image-model").inputValue(), "openrouter/flux");

  // Send it to a chat: a new chat, the image waiting in its composer.
  await item.getByRole("button", { name: "Send to a chat" }).click();
  await page.waitForURL(/\/chats\/[A-Za-z0-9_-]+$/);
  const remove = page.getByRole("button", { name: "Remove image-1.png" });
  await remove.waitFor();
  // Remove deletes the unsent copy (workbench#3); attach another in its place.
  const removed = page.waitForResponse(
    (r) => r.request().method() === "DELETE" && r.url().includes("/files/"),
  );
  await remove.click();
  assert.equal((await removed).status(), 204, "Remove deleted the copy");
  await remove.waitFor({ state: "detached" });
  await page.getByTestId("attach-input").setInputFiles(cfg.attach);
  await page.getByRole("button", { name: "Remove square.png" }).waitFor();
  await page.getByTestId("composer").fill("What is in this picture?");
  await page.getByTestId("send").click();
  await page.locator('[data-testid=answer][data-status="done"]').waitFor();

  // Back returns to the media area, from the address alone.
  await page.goBack();
  await page.waitForURL(/\/media\/images$/);
  await page.getByTestId("media-bin").waitFor();

  // Delete asks first, then removes it.
  await item.getByRole("button", { name: "Delete" }).click();
  await item.getByRole("button", { name: "Delete" }).click();
  await page.getByText("Nothing here yet. What you make appears here.").waitFor();

  assert.deepEqual(errors, []);
  console.log(
    "PASS: made, kept past a closed tab, sent to a chat, removed, attached, Back, deleted",
  );
} finally {
  await browser.close();
}
