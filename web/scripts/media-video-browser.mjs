/* global process, console, document */
// The Video screen in Chrome (workbench-media-screens.md §5): sending asks
// first with the price from the listing; the job runs on the server, saying
// how long so far, with the working scene; the video plays once done, with
// what came back beside what was asked and what the provider billed; a
// first frame brought in is sent; Edit and send fills the form; Back.
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
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("open-media").click();
  await page.getByRole("tab", { name: "Video" }).click();
  await page.waitForURL(/\/media\/video$/);

  // No model is chosen on a first visit; grok is offered at its least.
  const picker = page.getByTestId("video-model");
  assert.equal(await picker.inputValue(), "");
  await picker.selectOption("x-ai/grok-imagine-video");
  assert.equal(await page.getByTestId("video-seconds").inputValue(), "1");
  assert.equal(await page.getByTestId("video-size").inputValue(), "854x480");
  await page.getByTestId("video-prompt").fill("a red ball bouncing");

  // Sending asks first, with the price from the listing.
  await page.getByTestId("make-video").click();
  assert.equal(
    await page.getByTestId("video-quote").textContent(),
    "1 s at 480p. About $0.05, billed to OpenRouter.",
  );
  await page.getByTestId("confirm-video").click();

  // Work orders: the job runs on the server, saying how long so far.
  await page.getByRole("heading", { name: "Work orders · Long jobs" }).waitFor();
  const job = page.locator('[data-testid=media-item][data-status="running"]');
  await job
    .getByTestId("media-status")
    .filter({ hasText: /^Working, \d+ s so far\./ })
    .waitFor();
  await job.getByTestId("working-scene").waitFor();

  // Done: it plays, says what came back beside what was asked, and the cost.
  const done = page.locator('[data-testid=media-item][data-status="done"]');
  await done.getByTestId("bin-video").waitFor({ timeout: 30000 });
  await page.waitForFunction(
    () => document.querySelector("[data-testid=bin-video]")?.readyState >= 1,
  );
  assert.equal(
    await done.getByTestId("video-words").textContent(),
    "Asked 1 s at 854 × 480, got 1.0 s at 160 × 90",
  );
  assert.equal(
    await done.getByTestId("billed-words").textContent(),
    "The provider billed $0.05 for this.",
  );
  assert.equal(await done.getByRole("button", { name: "Again" }).count(), 0);

  // A first frame brought in is sent, and priced as an input image.
  await page.getByTestId("video-bring-in").setInputFiles(cfg.frame);
  await page.getByTestId("video-frame").filter({ hasText: "frame.png" }).waitFor();
  assert.notEqual(await page.getByTestId("video-frame").inputValue(), "");
  await page.getByTestId("video-prompt").fill("the ball from this frame");
  await page.getByTestId("make-video").click();
  assert.equal(
    await page.getByTestId("video-quote").textContent(),
    "1 s at 480p. About $0.05, billed to OpenRouter.",
  );
  await page.getByTestId("confirm-video").click();
  await page.waitForFunction(
    () => document.querySelectorAll("[data-testid=media-item]").length === 2,
  );

  // Edit and send fills the form, and sending still asks first. The first
  // job is the older, so the last in the list.
  await done.last().getByRole("button", { name: "Edit and send" }).click();
  await page.waitForFunction(
    () =>
      document.querySelector("[data-testid=video-prompt]")?.value === "a red ball bouncing" &&
      document.querySelector("[data-testid=video-frame]")?.value === "",
  );
  assert.equal(await page.getByTestId("video-quote").count(), 0);

  // Back returns to the screen before: Images, where the area opened.
  await page.goBack();
  await page.waitForURL(/\/media\/images$/);
  await page.getByRole("tab", { name: "Images", selected: true }).waitFor();

  assert.deepEqual(errors, []);
  console.log("PASS: asked first with the price, ran on the server, played, framed, edited");
} finally {
  await browser.close();
}
