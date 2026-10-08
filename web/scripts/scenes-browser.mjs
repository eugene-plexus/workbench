/* global process, document, console, Image, getComputedStyle */
/**
 * The working scenes in Chrome (workbench.md §6.1), served by Workbench
 * itself so its policies apply. Each scene at both themes and both sizes:
 * it draws, its background is transparent, it moves, and reduced motion
 * stops it. Then the real page with a slow model: only the plain line at
 * first, Eugene after the delay, gone at the first words, and the still
 * working pose under reduced motion.
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import assert from "node:assert/strict";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
const passed = [];

/** A context signed in to Workbench, with the console watched for errors and policy refusals. */
async function open(options) {
  const context = await browser.newContext(options);
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (m) => {
    if (m.type() === "error" || /Content Security Policy/i.test(m.text())) errors.push(m.text());
  });
  return { context, page, errors };
}

/** Puts one scene on Workbench's own page as an image and waits for it to load. */
async function place(page, file, size) {
  await page.evaluate(
    async ({ file, size }) => {
      document.getElementById("scene-under-test")?.remove();
      const box = document.createElement("div");
      box.id = "scene-under-test";
      Object.assign(box.style, {
        position: "fixed",
        inset: "0",
        zIndex: "9999",
        display: "grid",
        placeItems: "center",
        background: getComputedStyle(document.body).backgroundColor,
      });
      const img = new Image(size, size);
      img.src = `/scenes/${file}`;
      box.append(img);
      document.body.append(box);
      await img.decode();
    },
    { file, size },
  );
  return page.locator("#scene-under-test img");
}

/** How the scene draws on a canvas: share of painted pixels, and whether its corners are clear. */
async function coverage(page, size) {
  return page.evaluate((size) => {
    const img = document.querySelector("#scene-under-test img");
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, size, size);
    const data = ctx.getImageData(0, 0, size, size).data;
    let painted = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 0) painted++;
    const alpha = (x, y) => data[(y * size + x) * 4 + 3];
    const corners = [
      alpha(0, 0),
      alpha(size - 1, 0),
      alpha(0, size - 1),
      alpha(size - 1, size - 1),
    ];
    return { share: painted / (size * size), corners };
  }, size);
}

try {
  // ---- each scene, each theme, each size ----------------------------------
  // Two looks 700 ms apart: a non-round fraction of any 2-4 s loop.
  const twoLooks = async (page, target) => {
    const first = await target.screenshot();
    await page.waitForTimeout(700);
    return [first, await target.screenshot()];
  };
  for (const scheme of ["dark", "light"]) {
    const { context, page, errors } = await open({
      colorScheme: scheme,
      viewport: { width: 900, height: 700 },
    });
    await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
    await page.getByTestId("new-chat").waitFor();
    for (const file of cfg.scenes) {
      for (const size of [48, 160]) {
        const img = await place(page, file, size);
        const label = `${file} ${scheme} ${size}px`;
        const drawn = await coverage(page, size);
        assert.ok(drawn.share > 0.15, `${label}: drew only ${drawn.share} of its square`);
        assert.deepEqual(drawn.corners, [0, 0, 0, 0], `${label}: background is not transparent`);
        const [first, second] = await twoLooks(page, img);
        assert.ok(!first.equals(second), `${label}: did not move`);
        await img.screenshot({
          path: `${cfg.shots}/${file.replace(".svg", "")}-${scheme}-${size}.png`,
        });
      }
    }
    assert.deepEqual(errors, [], `${scheme}: the console reported problems`);
    await context.close();
  }
  // The file's own still. Chrome does not pass an emulated reduced-motion
  // setting into an image's document (found 2026-10-07), so the rule is
  // proved on the scene opened as a document, under its own policy. In the
  // page, the still working pose stands in, which the second half checks.
  for (const motion of ["no-preference", "reduce"]) {
    const { context, page, errors } = await open({ reducedMotion: motion });
    for (const file of cfg.scenes) {
      await page.goto(`${cfg.url}/scenes/${file}`);
      const [first, second] = await twoLooks(page, page.locator("svg"));
      if (motion === "reduce")
        assert.ok(first.equals(second), `${file}: moved under reduced motion`);
      else assert.ok(!first.equals(second), `${file}: did not move as a document`);
    }
    assert.deepEqual(errors, [], `${motion}: the console reported problems`);
    await context.close();
  }
  passed.push(
    `${cfg.scenes.length} scene(s) x 2 themes x 2 sizes: drawn, transparent, moving; each stops under reduced motion`,
  );

  // ---- the real page, a slow model ----------------------------------------
  for (const [scheme, motion] of [
    ["dark", "no-preference"],
    ["light", "reduce"],
  ]) {
    const { context, page, errors } = await open({
      colorScheme: scheme,
      reducedMotion: motion,
      viewport: { width: 900, height: 700 },
    });
    await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
    await page.getByTestId("new-chat").click();
    await page.getByTestId("composer").fill(`Plan a birdhouse (${scheme})`);
    await page.getByTestId("send").click();
    const sent = Date.now();
    const answer = page.getByTestId("answer").last();
    await answer.getByTestId("progress").waitFor();
    assert.equal(
      await answer.getByTestId("working-scene").count(),
      0,
      "Eugene showed before the delay",
    );
    await answer.getByTestId("working-scene").waitFor({ timeout: 5000 });
    const after = Date.now() - sent;
    assert.ok(after >= cfg.delayMs - 50, `Eugene showed ${after} ms after sending`);
    const phrase = await answer.getByTestId("scene-phrase").textContent();
    assert.ok(cfg.phrases.includes(phrase), `unknown phrase ${phrase}`);
    const art = answer.getByTestId("working-scene").locator("img");
    const src = await art.getAttribute("src");
    if (motion === "reduce") assert.equal(src, "/mascots/eugene-working.svg");
    else assert.match(src, /^\/scenes\/eugene-[a-z]+\.svg$/);
    assert.ok((await art.evaluate((node) => node.naturalWidth)) > 0, `${src} did not load`);
    assert.equal(await answer.getByTestId("working-scene").getAttribute("aria-hidden"), "true");
    assert.equal(await answer.getByTestId("progress").getAttribute("aria-live"), "polite");
    await answer.screenshot({ path: `${cfg.shots}/page-${scheme}-${motion}.png` });
    await answer.locator(".answer").filter({ hasText: "Hello" }).waitFor({ timeout: 15000 });
    assert.equal(
      await answer.getByTestId("working-scene").count(),
      0,
      "Eugene stayed after the first words",
    );
    assert.deepEqual(errors, [], `page ${scheme} ${motion}: the console reported problems`);
    await context.close();
  }
  passed.push(
    "the page: plain line first, Eugene after the delay, gone at the first words, still pose under reduced motion",
  );
  console.log(`PASS: ${passed.join("; ")}`);
} finally {
  await browser.close();
}
