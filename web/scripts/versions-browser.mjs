/* global process, console */
// Kept versions of an answer in Chrome (workbench-answer-versions.md §7):
// send, try again on the first answer, edit the first message, go back to
// the first version, then send. Every answer ends " #N", its request's number.
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
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").click();
  const composer = page.getByTestId("composer");
  await composer.waitFor();
  const answers = page.getByTestId("answer");
  const answer = (n) => answers.filter({ hasText: `Hello from the model. #${n}` });
  const finished = (n) =>
    page.locator('[data-testid=answer][data-status="done"]', {
      hasText: `#${n}`,
    });
  const group = (name) => page.getByRole("group", { name, exact: true });

  await composer.fill("First question");
  await page.getByTestId("send").click();
  await finished(1).waitFor();

  // A second tab on the same chat follows every change of path.
  const other = await context.newPage();
  other.on("pageerror", (error) => errors.push(error.message));
  await other.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await other.goto(page.url());
  await other.getByTestId("answer").filter({ hasText: "#1" }).waitFor();

  // Try again on the first answer.
  await answer(1).getByTestId("try-again").click();
  await finished(2).waitFor();
  await group("Answer 2 of 2").waitFor();
  assert.equal(await answer(1).count(), 0, "the old try is not on the path");
  await other.getByRole("group", { name: "Answer 2 of 2", exact: true }).waitFor();

  // Edit the first message: its note says the earlier one is kept.
  await page.getByTestId("user-message").getByRole("button", { name: "Edit" }).click();
  assert.equal(
    await page.getByTestId("edit-note").textContent(),
    "Your earlier version and what followed it are kept. Use the arrows to go back.",
  );
  // The edit box has its own name, apart from the composer's (workbench#2).
  await page
    .getByRole("textbox", { name: "Edit your message", exact: true })
    .fill("Edited question");
  assert.equal(await page.getByRole("textbox", { name: "Your message", exact: true }).count(), 1);
  await page.getByRole("button", { name: "Save and ask again" }).click();
  await finished(3).waitFor();
  await group("Message 2 of 2").waitFor();
  await other.getByText("Edited question").waitFor();

  // Go back to the first version: its branch comes back with it.
  await group("Message 2 of 2").getByRole("button", { name: "Previous version" }).click();
  await group("Message 1 of 2").waitFor();
  await answer(2).waitFor();
  assert.equal(await page.getByText("Edited question").count(), 0);
  await other.getByRole("group", { name: "Message 1 of 2", exact: true }).waitFor();
  await other.getByTestId("answer").filter({ hasText: "#2" }).waitFor();

  // Then send. While it runs, the arrows wait and say why.
  await composer.fill("Final question");
  await page.getByTestId("send").click();
  await page.locator('[data-testid=answer][data-status="running"]').waitFor();
  const back = group("Answer 2 of 2").getByRole("button", { name: "Previous version" });
  assert.equal(await back.isDisabled(), true, "the arrows wait while an answer runs");
  assert.equal(await back.getAttribute("title"), "Wait for the answer, or stop it.");
  await finished(4).waitFor();

  // Every other branch is still there.
  await back.click();
  await group("Answer 1 of 2").waitFor();
  await answer(1).waitFor();
  assert.equal(await page.getByText("Final question").count(), 0);
  await group("Answer 1 of 2").getByRole("button", { name: "Next version" }).click();
  await page.getByText("Final question").waitFor();
  await answer(4).waitFor();
  await group("Message 1 of 2").getByRole("button", { name: "Next version" }).click();
  await page.getByText("Edited question").waitFor();
  await answer(3).waitFor();
  assert.equal(await page.getByText("Final question").count(), 0);
  // And back, for the path the test reads.
  await group("Message 2 of 2").getByRole("button", { name: "Previous version" }).click();
  await page.getByText("Final question").waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "PASS: try again, edit, back to the first version, send; branches kept; arrows wait; tabs follow",
  );
} finally {
  await browser.close();
}
