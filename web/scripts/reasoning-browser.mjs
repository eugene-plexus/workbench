/* global process, console, document, window */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";

const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(config.playwright);
const browser = await chromium.launch({ executablePath: config.chrome, headless: true });
mkdirSync(config.shots, { recursive: true });

try {
  for (const width of [1440, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    await context.addCookies(config.cookies);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${config.url}/#signin=${config.secret}`);
    await page.getByTestId("new-chat").waitFor({ state: "attached" });
    if (!(await page.getByTestId("new-chat").isVisible())) {
      await page.locator('button[aria-controls="chat-navigation"]').click();
    }
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/chats") && response.request().method() === "POST",
      ),
      page.getByTestId("new-chat").click(),
    ]);
    await page.getByTestId("model-picker").selectOption("local-model");
    await page.getByTestId("composer").fill("Think about this carefully.");
    await page.getByTestId("send").click();
    const answer = page.getByTestId("answer").last();
    const summary = answer.locator("summary").filter({ hasText: "Reasoning" });
    await summary.waitFor();
    assert.match(await summary.textContent(), /~[\d,]+ tokens\s*\(estimated\)/);
    await answer.locator(".answer").filter({ hasText: "Final answer." }).waitFor();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="answer"]:last-of-type')
          ?.getAttribute("data-status") === "done",
    );
    const final = await summary.textContent();
    assert.match(final, /[0-9]+\.[0-9]s/);
    assert.ok(Number(final.match(/([0-9]+\.[0-9])s/)[1]) > 0);
    assert.equal(await summary.evaluate((node) => node.parentElement.open), false);
    assert.equal(await page.getByRole("group", { name: "Reasoning text" }).isVisible(), false);
    const fits = await summary.evaluate((node) => {
      const box = node.getBoundingClientRect();
      return (
        box.left >= 0 &&
        box.right <= window.innerWidth &&
        document.documentElement.scrollWidth <= window.innerWidth
      );
    });
    assert.ok(fits, `Reasoning header overflowed at ${width}px`);
    await page.screenshot({ path: `${config.shots}/reasoning-${width}.png` });
    await summary.click();
    assert.equal(await page.getByRole("group", { name: "Reasoning text" }).isVisible(), true);
    await summary.click();
    await page.reload();
    await summary.waitFor();
    assert.equal(await summary.textContent(), final);
    assert.equal(await summary.evaluate((node) => node.parentElement.open), false);
    assert.deepEqual(errors, []);
    await context.close();
    console.log(
      `PASS: ${width}px reasoning tokens, observed duration, collapsed toggle, reload, no overflow`,
    );
  }
} finally {
  await browser.close();
}
