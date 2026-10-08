/* global process, console, document */
// The Speech and Transcription screens in Chrome (workbench-media-screens.md
// §4): speak a clip and play it; transcribe an uploaded recording and see
// what the model heard against the clip; record one with Chrome's fake
// microphone and transcribe that; send a transcript to a chat; go Back.
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
const browser = await chromium.launch({
  executablePath: cfg.chrome,
  headless: true,
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
});
try {
  const context = await browser.newContext();
  await context.addCookies(cfg.cookies);
  const errors = [];
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("open-media").click();
  await page.getByRole("tab", { name: "Speech" }).click();
  await page.waitForURL(/\/media\/speech$/);

  // Speak a clip, as WAV, and play it from the bin.
  await page.getByTestId("speech-model").selectOption("openrouter/kokoro");
  await page.getByTestId("speech-input").fill("The bench is ready.");
  await page.getByTestId("speech-voice").selectOption("af_bella");
  await page.getByTestId("speech-format").selectOption("wav");
  await page.getByTestId("make-speech").click();
  const spoken = page.locator('[data-testid=media-item][data-status="done"]');
  await spoken.waitFor();
  const audio = spoken.getByTestId("bin-audio");
  await audio.waitFor();
  await page.waitForFunction(
    () => document.querySelector("[data-testid=bin-audio]")?.readyState >= 1,
  );
  const seconds = await audio.evaluate((el) => el.duration);
  assert.ok(Math.abs(seconds - 0.5) < 0.05, `the clip plays, ${seconds} s`);

  // Transcribe an uploaded recording: its length is measured here.
  await page.getByRole("tab", { name: "Transcription" }).click();
  await page.waitForURL(/\/media\/transcription$/);
  await page.getByTestId("transcription-model").selectOption("openrouter/whisper-turbo");
  await page.getByTestId("choose-recording").setInputFiles(cfg.recording);
  await page.getByTestId("chosen-recording").filter({ hasText: "note.wav, 3.0 s" }).waitFor();
  await page.getByTestId("make-transcript").click();
  const heard = page.locator('[data-testid=media-item][data-status="done"]').first();
  await heard.getByTestId("transcript").filter({ hasText: "The bench is ready." }).waitFor();
  assert.equal(
    await heard.getByTestId("heard-words").textContent(),
    "The model heard 1.5 s of this 3.0 s clip. Words after that may be missing.",
  );

  // Record with the browser's (fake) microphone: this page is on localhost.
  await page.getByTestId("record").click();
  await page.getByTestId("stop-recording").waitFor();
  await page.waitForTimeout(1500);
  await page.getByTestId("stop-recording").click();
  await page
    .getByTestId("chosen-recording")
    .filter({ hasText: /^recording\.webm, 1\.\d s$/ })
    .waitFor();
  await page.getByTestId("make-transcript").click();
  await page.locator('[data-testid=media-item][data-status="done"]').nth(1).waitFor();
  await page.waitForFunction(
    () => document.querySelectorAll('[data-testid=media-item][data-status="done"]').length === 2,
  );

  // Send the first transcript to a chat: it waits as the message to send.
  await heard.getByRole("button", { name: "Send to a chat" }).click();
  await page.waitForURL(/\/chats\/[A-Za-z0-9_-]+$/);
  const composer = page.getByTestId("composer");
  await composer.waitFor();
  assert.equal(await composer.inputValue(), "The bench is ready.");

  // Back returns to the Transcription screen.
  await page.goBack();
  await page.waitForURL(/\/media\/transcription$/);
  await page.getByTestId("transcription-model").waitFor();

  assert.deepEqual(errors, []);
  console.log("PASS: spoke and played, transcribed an upload and a recording, sent text, Back");
} finally {
  await browser.close();
}
