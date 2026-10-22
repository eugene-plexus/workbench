/* global process, console, URL, document, window, getComputedStyle, performance, requestAnimationFrame, setTimeout */
// Artifacts and their previews in the selected browser (workbench-rich-content-roadmap.md
// RC5), under the headers Workbench really sends: the chat page's policy
// unchanged and the preview's page its own; an answer's artifacts as cards
// that run nothing; Markdown read, an SVG drawn and one with a script not; a
// page that tries every way out of its frame and is refused each; a page
// that never stops, stopped; one that tries to leave, stopped; a React
// component run and clicked; one that imports what is not there, saying so;
// an edit kept as a revision across a reload, the first version's own and not
// the second's; revision comparisons, their layout and downloads; the search suggestions' frame as it was; the
// keyboard; both themes and a phone. Each check is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import {
  contextOptions,
  goChat,
  goSignedIn,
  keepScheme,
  ignorablePageError,
  launchBrowser,
  readOptions,
  watchPolicy,
  watchServiceWorkers,
  canCheckPreviewLoop,
} from "./browser-options.mjs";

const { configPath, browserName } = readOptions();
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
const require = createRequire(import.meta.url);
mkdirSync(cfg.shots, { recursive: true });
const origin = new URL(cfg.url).origin;
const browser = await launchBrowser(require(cfg.playwright), cfg, browserName);
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const FRAME = `${origin}/preview/frame`;

const failures = [];

// A step that never ends (a call with no time limit) says which it was, and the run ends.
let at = "starting";
/** The page last opened, to say what it showed when a step failed. */
let lastPage = null;
function step(name) {
  at = name;
}
setTimeout(() => {
  console.log(`FAIL  the run stopped at: ${at}`);
  process.exit(2);
}, 300_000).unref();
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}

const errors = [];
/** The page's own refusals, apart from a preview's (which are the point). */
const policy = [];
const requests = [];
const answered = [];
const failed = [];

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext(
    contextOptions(browserName, {
      colorScheme: scheme,
      viewport,
      acceptDownloads: true,
      ...extra,
    }),
  );
  await context.addCookies(cfg.cookies);
  await context.addInitScript(watchServiceWorkers);
  // A step that cannot happen fails in seconds, not in the run's whole time.
  context.setDefaultTimeout(15_000);
  await context.addInitScript(watchPolicy, browserName);
  const page = await context.newPage();
  lastPage = page;
  // A preview's own errors are its own (and some are the point): only the page's count.
  page.on("pageerror", (error) => {
    if (
      !(error.stack ?? "").includes("/preview/") &&
      !ignorablePageError(browserName, error.message)
    )
      errors.push(error.message);
  });
  // The chat page's own refusals: a preview's (in its own window) are the point.
  page.on("request", (request) => {
    const frame = request.frame();
    requests.push({
      url: request.url(),
      method: request.method(),
      main: frame === page.mainFrame(),
    });
  });
  // Chrome reports a request its policy refused as a request that failed: only a
  // response says something reached a server.
  page.on("response", (response) => {
    const request = response.request();
    answered.push({
      url: request.url(),
      method: request.method(),
      main: request.frame() === page.mainFrame(),
    });
  });
  page.on("requestfailed", (request) => {
    failed.push(`${request.url()} ${request.failure()?.errorText ?? ""}`);
  });
  await goSignedIn(page, `${cfg.url}/#signin=${cfg.secret}`);
  await keepScheme(page, browserName, scheme);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function chat(page, id) {
  await goChat(page, browserName, `${cfg.url}/chats/${id}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
}

function contrast(fore, back) {
  const lum = ([r, g, b]) => {
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const [hi, lo] = [lum(fore), lum(back)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

const rgb = (c) =>
  (/rgba?\(([^)]+)\)/.exec(c ?? "")?.[1] ?? "0,0,0")
    .split(/[\s,/]+/)
    .filter(Boolean)
    .slice(0, 3)
    .map(Number);

/** The answer's card for `title`, and its dialog once opened. */
function card(page, title) {
  return page
    .locator("[data-testid=answer]")
    .last()
    .getByTestId("artifact-card")
    .filter({ has: page.getByText(title, { exact: true }) });
}

async function openArtifact(page, title) {
  step(`opening ${title}`);
  await card(page, title)
    .getByRole("button", { name: `Open ${title}` })
    .click();
  const dialog = page.getByTestId("artifact-dialog");
  await dialog.waitFor();
  return dialog;
}

async function closeDialog(page) {
  step("closing a dialog by Escape");
  await page.keyboard.press("Escape");
  await page.getByTestId("artifact-dialog").waitFor({ state: "detached" });
}

/** The preview frame inside the dialog, once its page has said it started. */
async function runIt(page, dialog) {
  step("running a preview");
  await dialog.getByRole("button", { name: "Run" }).click();
  await dialog.getByTestId("artifact-frame").waitFor();
  const frame = dialog.getByTestId("artifact-frame");
  check(
    "a running preview keeps its sandbox and separate page",
    (await frame.getAttribute("sandbox")) === "allow-scripts allow-forms" &&
      (await frame.getAttribute("src")) === "/preview/frame" &&
      (await frame.getAttribute("srcdoc")) === null,
  );
  const handle = await dialog.getByTestId("artifact-frame").elementHandle();
  return handle.contentFrame();
}

async function compareRevisions(page, dialog, scheme) {
  step(`${scheme}: comparing revisions`);
  await dialog.getByRole("combobox", { name: "Compare with revision" }).selectOption("1");
  const changes = dialog.getByRole("tabpanel", { name: "Changes" });
  await changes.locator("del").waitFor();
  check(
    `${scheme}: comparing selects Changes and marks removed and added lines`,
    (await dialog.getByRole("tab", { name: "Changes" }).getAttribute("aria-selected")) === "true" &&
      (await dialog.getByRole("tab", { name: "Preview" }).getAttribute("aria-selected")) ===
        "false" &&
      (await changes.locator("del").innerText()).includes("- bread") &&
      (await changes.locator("ins").allTextContents()).some((line) => line.includes("- oats")) &&
      (await changes.locator("ins").allTextContents()).some((line) => line.includes("- eggs")),
  );
  // WebKit on Windows sizes in display pixels (819 CSS px for 1024 at 125%).
  // Exercise the design's actual CSS breakpoints in every engine.
  const scale = page.viewportSize().width / (await page.evaluate(() => window.innerWidth));
  // Its media queries round fractional CSS widths up; use a whole CSS width below the boundary.
  const belowBoundary = Number.isInteger(1023 * scale) ? 1023 : 1020;
  for (const width of [1024, belowBoundary, PHONE.width]) {
    await page.setViewportSize({ width: Math.ceil(width * scale), height: DESK.height });
    await page.evaluate(
      () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))),
    );
    const layout = await changes.evaluate((element) => {
      const removed = element.querySelector("del");
      const added = removed.closest(".artifact-diff-line").querySelector("ins");
      const left = removed.parentElement;
      const right = added.parentElement;
      const leftBox = left.getBoundingClientRect();
      const rightBox = right.getBoundingClientRect();
      return {
        width: window.innerWidth,
        sideBySide: Math.abs(leftBox.top - rightBox.top) < 1 && leftBox.right <= rightBox.left + 1,
        oneColumn: leftBox.bottom <= rightBox.top + 1 && Math.abs(leftBox.left - rightBox.left) < 1,
        fits: element.scrollWidth <= element.clientWidth + 1,
        removedColor: getComputedStyle(left).backgroundColor,
        addedColor: getComputedStyle(right).backgroundColor,
        struck: getComputedStyle(removed).textDecorationLine.includes("line-through"),
        unchangedCopies: [...element.querySelectorAll('[data-change="same"]')].filter(
          (line) => line.getBoundingClientRect().height > 0,
        ).length,
      };
    });
    const red = rgb(layout.removedColor);
    const green = rgb(layout.addedColor);
    check(
      `${scheme}: comparison at ${width}px fits, uses ${width >= 1024 ? "two columns" : "one column"}, red removals and green additions`,
      layout.width === width &&
        (width >= 1024 ? layout.sideBySide : layout.oneColumn) &&
        layout.fits &&
        layout.struck &&
        red[0] > red[1] &&
        green[1] > green[0],
      JSON.stringify(layout),
    );
    const sameCount = await changes.locator('.diff-before[data-change="same"]').count();
    check(
      `${scheme}: comparison at ${width}px shows unchanged lines once per column`,
      layout.unchangedCopies === sameCount * (width >= 1024 ? 2 : 1),
      JSON.stringify(layout),
    );
  }
  await page.screenshot({ path: `${cfg.shots}/comparison-phone-${scheme}.png` });
  await page.setViewportSize(DESK);
  await page.screenshot({ path: `${cfg.shots}/comparison-${scheme}.png` });
  await dialog.getByRole("tab", { name: "Changes" }).focus();
  await page.keyboard.press("ArrowLeft");
  check(
    `${scheme}: the keyboard returns from Changes to Source`,
    (await dialog.getByRole("tab", { name: "Source" }).getAttribute("aria-selected")) === "true" &&
      (await dialog.getByRole("combobox", { name: "Compare with revision" }).inputValue()) === "" &&
      (await dialog.getByRole("textbox").inputValue()).includes("- oats"),
  );
}

async function downloadComparison(page, dialog, before, after, label) {
  const downloads = await Promise.all([
    page.waitForEvent("download", {
      predicate: (item) => item.suggestedFilename() === "notes-revision-1.md",
    }),
    page.waitForEvent("download", {
      predicate: (item) => item.suggestedFilename() === "notes-revision-2.md",
    }),
    dialog.getByRole("button", { name: "Download both", exact: true }).click(),
  ]);
  check(
    label,
    readFileSync(await downloads[0].path(), "utf8") === before &&
      readFileSync(await downloads[1].path(), "utf8") === after,
  );
}

try {
  // --- the two policies, as the built app sends them -----------------------------------
  step("the two policies, as the built app sends them");
  {
    const { context, page } = await open("dark", DESK);
    const main = await page.request.get(`${cfg.url}/chats/${cfg.chats.made}`);
    const frame = await page.request.get(FRAME);
    const runtime = await page.request.get(`${origin}/preview/runtime.js`);
    check(
      "the chat's page keeps its own policy, and no page may frame it",
      main.headers()["content-security-policy"] === cfg.csp &&
        main.headers()["x-frame-options"] === "DENY",
      JSON.stringify(main.headers()),
    );
    check(
      "the preview's page has its own policy: no origin, no network, framed by Workbench alone",
      frame.headers()["content-security-policy"] === cfg.previewCsp &&
        frame.headers()["x-frame-options"] === "SAMEORIGIN" &&
        (await frame.text()).includes('<script src="/preview/runtime.js"></script>'),
      JSON.stringify(frame.headers()),
    );
    check(
      "the preview's script is Workbench's own file",
      runtime.status() === 200 && (await runtime.text()).startsWith("var WorkbenchPreview="),
    );
    await context.close();
  }

  for (const scheme of ["dark", "light"]) {
    const since = requests.length;
    const { context, page } = await open(scheme, DESK);
    await chat(page, cfg.chats.made);
    const answer = page.locator("[data-testid=answer]").last();
    await answer.getByTestId("artifact-card").first().waitFor();
    // The second pass finds the first version chosen again, with the edit the first kept.
    if (scheme === "light") await card(page, "Notes").getByText("Markdown · 2 revisions").waitFor();
    const cards = await answer.getByTestId("artifact-card").count();
    check(
      `${scheme}: an answer's artifacts are cards, and nothing runs`,
      cards === 8 &&
        (await page.locator("iframe").count()) === 0 &&
        !requests.slice(since).some((r) => r.url.startsWith(FRAME)),
      `${cards} cards`,
    );
    check(
      `${scheme}: a plain block of HTML can be opened as an artifact`,
      (await answer.getByRole("button", { name: "Open this HTML as an artifact" }).count()) === 1,
    );
    const words = await card(page, "Notes").evaluate((el) => {
      const title = el.querySelector(".artifact-card-title");
      return [getComputedStyle(title).color, getComputedStyle(el).backgroundColor];
    });
    const ratio = contrast(rgb(words[0]), rgb(words[1]));
    check(`${scheme}: a card's title reads at least 4.5:1`, ratio >= 4.5, ratio.toFixed(2));

    // --- Markdown, by keyboard ------------------------------------------------------------
    step("Markdown, by keyboard");
    const opener = card(page, "Notes").getByRole("button", { name: "Open Notes" });
    await opener.focus();
    await page.keyboard.press("Enter");
    let dialog = page.getByTestId("artifact-dialog");
    await dialog.waitFor();
    // Opened at its last revision, which is read first.
    await dialog.getByRole("heading", { name: "Shopping" }).waitFor();
    check(
      `${scheme}: Markdown reads as an answer does, opened by keyboard`,
      (await dialog.getByRole("heading", { name: "Shopping" }).count()) === 1 &&
        (await dialog.locator("li").count()) >= 2,
      `${await dialog.getByRole("heading", { name: "Shopping" }).count()} headings, ${await dialog.locator("li").count()} items`,
    );
    await page.screenshot({ path: `${cfg.shots}/notes-${scheme}.png` });
    if (scheme === "light") await compareRevisions(page, dialog, scheme);
    await closeDialog(page);
    check(
      `${scheme}: Escape closes it and gives focus back to Open`,
      await opener.evaluate((el) => el === document.activeElement),
    );

    // --- SVG ------------------------------------------------------------------------------
    step("SVG");
    dialog = await openArtifact(page, "Logo");
    const logo = dialog.getByRole("img", { name: "Logo" });
    await logo.waitFor();
    check(
      `${scheme}: an SVG is a picture written anew`,
      (await logo.getAttribute("src")).startsWith("data:image/svg+xml") &&
        (await logo.evaluate((img) => img.complete && img.naturalWidth > 0)),
    );
    await closeDialog(page);
    const title = await page.title();
    dialog = await openArtifact(page, "Scripted picture");
    check(
      `${scheme}: an SVG with a script is not shown, saying why, and its source is there`,
      (await dialog.getByText(/^Picture not shown: its drawing has a <script> element/).count()) ===
        1 &&
        (await dialog.locator("pre code").innerText()).includes("<script>") &&
        (await page.title()) === title,
    );
    await closeDialog(page);

    if (scheme === "dark") {
      // --- a page that tries every way out ---------------------------------------------------
      step("a page that tries every way out");
      const before = answered.length;
      const asked = requests.length;
      dialog = await openArtifact(page, "Escapes");
      check(
        "dark: a page runs nothing until Run",
        (await dialog.locator("iframe").count()) === 0 &&
          (await dialog.getByRole("button", { name: "Run" }).count()) === 1,
      );
      const frame = await runIt(page, dialog);
      await frame.locator('li[data-name="network"]').waitFor({ timeout: 10_000 });
      await frame.locator('li[data-name="api"]').waitFor({ timeout: 10_000 });
      await frame.locator('li[data-name="worker"]').waitFor({ timeout: 10_000 });
      await page.waitForTimeout(1_000);
      const said = Object.fromEntries(
        await frame
          .locator("#out li")
          .evaluateAll((items) => items.map((li) => [li.dataset.name, li.textContent])),
      );
      const serviceWorkers = await frame.evaluate(() => Promise.all(window.__serviceWorkers));
      for (const name of [
        "parent-dom",
        "top-dom",
        "cookie",
        "storage",
        "popup",
        "worker",
        "service-worker",
        "form-script",
        "form-button",
        "api",
        "network",
      ]) {
        check(
          `dark: the preview's ${name} is refused`,
          (said[name] ?? "").startsWith(`${name}: blocked`) ||
            (name === "service-worker" &&
              serviceWorkers.length === 1 &&
              serviceWorkers[0] === true),
          said[name],
        );
      }
      const reached = answered
        .slice(before)
        .filter(
          (r) => !r.main && !r.url.startsWith(FRAME) && r.url !== `${origin}/preview/runtime.js`,
        );
      const posted = requests.slice(asked).filter((r) => !r.main && r.method !== "GET");
      check(
        "dark: the preview reached nothing but its own page and script (no form, no picture, no API)",
        reached.length === 0 && posted.length === 0,
        [...reached, ...posted].map((r) => `${r.method} ${r.url}`).join(", "),
      );
      console.log(`  measured: refused before reaching anything: ${failed.join(" | ")}`);
      check(
        "dark: it did not move the page, or change its title",
        page.url() === `${cfg.url}/chats/${cfg.chats.made}` && (await page.title()) === title,
        page.url(),
      );
      check(
        "dark: what it could not load is said in words, and the source stays",
        (await dialog.getByTestId("artifact-notes").innerText()).includes(
          "Refused: https://cdn.example.com/lib.js",
        ),
      );
      check(
        "dark: its form is never sent, and that is said",
        (await dialog.getByTestId("artifact-notes").innerText()).includes(
          "Refused: sending a form",
        ),
      );
      await page.screenshot({ path: `${cfg.shots}/escapes-dark.png` });
      await dialog.getByRole("button", { name: "Stop the page" }).click();
      check("dark: Stop takes the frame away", (await dialog.locator("iframe").count()) === 0);
      await closeDialog(page);

      const loops = canCheckPreviewLoop(browserName);
      if (loops) {
        // --- a page that never stops -----------------------------------------------------------
        step("a page that never stops");
        dialog = await openArtifact(page, "Busy");
        await runIt(page, dialog);
        await page.waitForTimeout(1_500);
        const gap = await page.evaluate(
          () =>
            new Promise((done) => {
              const start = performance.now();
              requestAnimationFrame(() => done(performance.now() - start));
            }),
        );
        const stopping = performance.now();
        await dialog.getByRole("button", { name: "Stop the page" }).click();
        await dialog.locator("iframe").waitFor({ state: "detached" });
        const stopped = performance.now() - stopping;
        check(
          "dark: a page that never stops leaves this page free, and Stop ends it",
          gap < 250 && stopped < 2_000,
          `frame gap ${gap.toFixed(0)} ms, stop ${stopped.toFixed(0)} ms`,
        );
        console.log(
          `  measured: busy preview, page frame gap ${gap.toFixed(1)} ms; Stop ${stopped.toFixed(0)} ms`,
        );
        await closeDialog(page);
      }

      // --- a page that tries to leave ----------------------------------------------------------
      step("a page that tries to leave");
      dialog = await openArtifact(page, "Leaves");
      const running = performance.now();
      await runIt(page, dialog);
      // Run at once after a page stuck in a loop was stopped: its first frames are put in
      // that page's process until Chrome ends it, and the preview says it waits.
      const waited =
        loops &&
        (await dialog
          .getByRole("status")
          .getByText(/waiting for the browser to end a preview stopped before/)
          .waitFor({ timeout: 5_000 })
          .then(
            () => true,
            () => false,
          ));
      // WebKit enforces the chat page's frame-src on the frame's own move: the
      // move is refused before the page can say it is leaving, so the frame
      // stays where it was and no message follows (docs/browsers.md).
      const refusedEarly = browserName === "webkit";
      if (refusedEarly) await page.waitForTimeout(2_000);
      else
        await dialog
          .getByText("The page tried to go to another page, and was stopped.")
          .waitFor({ timeout: 25_000 });
      const after = performance.now() - running;
      if (loops) {
        check(
          "dark: run at once after a stuck page was stopped, a page waits, saying why, and then runs",
          waited,
          `started ${after.toFixed(0)} ms after Run`,
        );
        console.log(
          `  measured: run at once after Stop of a stuck page, it ran in ${after.toFixed(0)} ms`,
        );
      }
      const left = requests.some((r) => r.url.startsWith("https://example.com/away"))
        ? answered.some((r) => r.url.startsWith("https://example.com/away"))
          ? "went out and was answered"
          : "was asked for, with no answer before the frame went"
        : "was never asked for";
      if (refusedEarly) {
        console.log(
          "SKIP  webkit: a page that tries to leave is refused before it can say so; checking that it stayed (docs/browsers.md).",
        );
        check(
          "dark: a page that tries to leave stays in its frame, and is never asked for",
          left === "was never asked for" &&
            page.frames().some((f) => f.url().endsWith("/preview/frame")),
          left,
        );
      } else
        check(
          "dark: a page that tries to leave is stopped, and says so",
          (await dialog.locator("iframe").count()) === 0,
        );
      console.log(`  measured: the leaving page's address ${left}`);
      await closeDialog(page);

      // --- a React component -------------------------------------------------------------------
      step("a React component");
      // Exercise the 3 s ready rule without a CPU loop: hold one bootstrap
      // silent, then let the replacement run. This is portable even where
      // the browser cannot isolate a looping frame from its parent.
      let bootstraps = 0;
      let resume = () => {};
      const replacement = new Promise((resolve) => {
        resume = resolve;
      });
      const runtimeUrl = `${origin}/preview/runtime.js`;
      if (!loops) {
        await context.route(runtimeUrl, async (route) => {
          bootstraps++;
          if (bootstraps === 1) {
            await route.fulfill({
              contentType: "text/javascript",
              body: "// silent test bootstrap",
            });
          } else {
            await replacement;
            await route.continue();
          }
        });
      }
      dialog = await openArtifact(page, "Counter");
      const starting = performance.now();
      let counter = await runIt(page, dialog);
      if (!loops) {
        await dialog
          .getByRole("status")
          .getByText(/waiting for the browser/)
          .waitFor({ timeout: 5_000 });
        const delay = performance.now() - starting;
        check(
          "a silent frame is replaced after 3 seconds, saying why",
          delay >= 2_900 && delay < 5_500,
          `${delay.toFixed(0)} ms`,
        );
        resume();
        counter = await (await dialog.getByTestId("artifact-frame").elementHandle()).contentFrame();
      }
      const button = counter.getByRole("button", { name: "Clicked 0" });
      await button.waitFor({ timeout: 10_000 });
      if (!loops) {
        check("the replacement frame starts", bootstraps === 2, `${bootstraps} bootstraps`);
        await context.unroute(runtimeUrl);
      }
      await button.click();
      check(
        "dark: a React component runs, and answers a click",
        (await counter.getByRole("button", { name: "Clicked 1" }).count()) === 1,
      );
      // Its form: its own handler runs, by Enter and by its button, and nothing is sent.
      await counter.getByRole("textbox", { name: "Name" }).fill("Ada");
      await counter.getByRole("textbox", { name: "Name" }).press("Enter");
      await counter.getByRole("textbox", { name: "Name" }).fill("Bo");
      await counter.getByRole("button", { name: "Add" }).click();
      await counter.getByText("Bo", { exact: true }).waitFor({ timeout: 5_000 });
      const notes = (await dialog.getByTestId("artifact-notes").count())
        ? await dialog.getByTestId("artifact-notes").innerText()
        : "";
      check(
        "dark: a component's form runs its own handler, and sends nothing",
        (await counter.getByRole("listitem").allInnerTexts()).join(",") === "Ada,Bo" &&
          !notes.includes("sending a form") &&
          (await dialog.getByTestId("artifact-frame").count()) === 1,
        notes,
      );
      await dialog.getByRole("button", { name: "Reload the component" }).click();
      const again = await (
        await dialog.getByTestId("artifact-frame").elementHandle()
      ).contentFrame();
      await again.getByRole("button", { name: "Clicked 0" }).waitFor({ timeout: 10_000 });
      check("dark: Reload starts it afresh", true);
      await page.screenshot({ path: `${cfg.shots}/counter-dark.png` });
      await closeDialog(page);
      dialog = await openArtifact(page, "Needs lodash");
      // This frame is taken away as soon as the page says it could not run, which
      // WebKit does before a check could look at it: wait for the words instead.
      step("running a preview that cannot start");
      await dialog.getByRole("button", { name: "Run" }).click();
      await dialog
        .getByText('This preview has React only: it cannot load "lodash"', { exact: false })
        .waitFor({ timeout: 10_000 });
      await dialog.getByRole("tab", { name: "Source" }).click();
      check(
        "dark: a component that imports what is not there says so, and its source stays",
        (await dialog.getByRole("textbox").inputValue()).includes('import _ from "lodash"'),
      );
      await closeDialog(page);

      // --- an edit, kept as a revision ---------------------------------------------------------
      step("an edit, kept as a revision");
      dialog = await openArtifact(page, "Notes");
      await dialog.getByRole("tab", { name: "Source" }).click();
      const editor = dialog.getByRole("textbox");
      const edited = `${cfg.sources.notes.replace("- bread", "- oats")}\n- eggs`;
      await editor.fill(edited);
      await dialog.getByRole("button", { name: "Save as revision 2" }).click();
      await dialog
        .getByRole("combobox", { name: "Revision", exact: true })
        .getByText(/^Revision 2 of 2, edited/)
        .waitFor({ state: "attached" });
      const [download] = await Promise.all([
        page.waitForEvent("download"),
        dialog.getByRole("button", { name: "Download revision 2" }).click(),
      ]);
      const saved = readFileSync(await download.path(), "utf8");
      check(
        "dark: an edit is saved as revision 2 and downloads under the title",
        download.suggestedFilename() === "notes.md" && saved === edited,
        `${download.suggestedFilename()} ${JSON.stringify(saved.slice(-20))}`,
      );
      await closeDialog(page);
      await page.reload();
      await card(page, "Notes").getByText("Markdown · 2 revisions").waitFor();
      dialog = await openArtifact(page, "Notes");
      await dialog.getByRole("tab", { name: "Source" }).click();
      await compareRevisions(page, dialog, scheme);
      const latest = await dialog.getByRole("textbox").inputValue();
      await dialog.getByRole("combobox", { name: "Compare with revision" }).selectOption("1");
      await downloadComparison(
        page,
        dialog,
        cfg.sources.notes,
        edited,
        "dark: Download both keeps each revision's source and number",
      );
      await dialog.getByRole("tab", { name: "Source" }).click();
      await dialog.getByRole("combobox", { name: "Revision", exact: true }).selectOption("1");
      await page.waitForTimeout(300);
      const first = await dialog.getByRole("textbox").inputValue();
      check(
        "dark: after a reload it opens at revision 2, and revision 1 is the answer's",
        latest.endsWith("- eggs") && first === cfg.sources.notes,
        JSON.stringify([latest.slice(-10), first.slice(-10)]),
      );
      await closeDialog(page);

      // A long response uses the same revision route, without changing the saved artifact.
      step("a comparison over 5000 lines");
      const longSource = Array(5001).fill("long").join("\n");
      const revisionRoute = "**/artifacts/*/revisions/2";
      await page.route(revisionRoute, async (route) => {
        const response = await route.fetch();
        await route.fulfill({ response, json: { ...(await response.json()), source: longSource } });
      });
      dialog = await openArtifact(page, "Notes");
      await dialog.getByRole("combobox", { name: "Compare with revision" }).selectOption("1");
      await dialog.getByText("Too long to compare here. Download both.", { exact: true }).waitFor();
      check(
        "dark: over 5000 lines says why and draws no comparison",
        (await dialog.locator(".artifact-diff-line").count()) === 0,
      );
      await downloadComparison(
        page,
        dialog,
        cfg.sources.notes,
        longSource,
        "dark: a comparison over 5000 lines still downloads both complete revisions",
      );
      await closeDialog(page);
      await page.unroute(revisionRoute);

      // --- another version of the answer has its own -------------------------------------------
      step("another version of the answer has its own");
      await answer
        .getByRole("button", { name: "Try again: write a new version of this answer" })
        .click();
      await page
        .locator('[data-testid=answer][data-status="running"]')
        .waitFor({ state: "detached", timeout: 15_000 });
      await card(page, "Notes").getByText("Markdown · From the answer").waitFor();
      await answer.getByRole("button", { name: "Previous version of this answer" }).click();
      await card(page, "Notes").getByText("Markdown · 2 revisions").waitFor();
      check("dark: each version of the answer keeps its own revisions", true);
    }
    const refused = await page.evaluate(() => window.__policy);
    policy.push(...refused);
    check(
      `${scheme}: the page's own policy refused nothing`,
      refused.length === 0,
      refused.join(" | "),
    );
    await context.close();
  }

  // --- the search suggestions' frame, as it was ------------------------------------------------
  step("the search suggestions' frame, as it was");
  {
    const { context, page } = await open("light", DESK);
    await chat(page, cfg.chats.searched);
    const frame = page.getByTestId("search-suggestions");
    await frame.waitFor();
    const inner = await (await frame.elementHandle()).contentFrame();
    check(
      "the search suggestions' frame is as it was: inert, its chips links",
      (await frame.getAttribute("sandbox")) === "allow-popups allow-popups-to-escape-sandbox" &&
        (await inner.getByRole("link", { name: "barns & silos" }).count()) === 1,
    );
    await context.close();
  }

  // --- a phone ---------------------------------------------------------------------------------
  step("a phone");
  {
    const { context, page } = await open("light", PHONE, {
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 3,
    });
    await chat(page, cfg.chats.made);
    await card(page, "Counter").scrollIntoViewIfNeeded();
    const dialog = await openArtifact(page, "Counter");
    const frame = await runIt(page, dialog);
    await frame.getByRole("button", { name: "Clicked 0" }).waitFor({ timeout: 10_000 });
    const fit = await page.evaluate(() => {
      const dialogBox = document
        .querySelector("[data-testid=artifact-dialog]")
        .getBoundingClientRect();
      const frameBox = document
        .querySelector("[data-testid=artifact-frame]")
        .getBoundingClientRect();
      const stop = [...document.querySelectorAll("[data-testid=artifact-dialog] button")].find(
        (b) => b.getAttribute("aria-label") === "Stop the component",
      );
      const stopBox = stop?.getBoundingClientRect();
      return {
        dialog: dialogBox.right <= window.innerWidth + 1,
        frame: frameBox.right <= window.innerWidth + 1 && frameBox.width > 200,
        stop: Boolean(stopBox && stopBox.bottom <= window.innerHeight && stopBox.height >= 32),
      };
    });
    check(
      "phone: the dialog, its frame and Stop fit the screen",
      fit.dialog && fit.frame && fit.stop,
      JSON.stringify(fit),
    );
    await page.screenshot({ path: `${cfg.shots}/counter-phone.png` });
    await context.close();
  }
} catch (error) {
  check(
    `the run went on past: ${at}`,
    false,
    String(error?.message ?? error)
      .split("\n")
      .slice(0, 12)
      .join(" | "),
  );
  const shown = await lastPage
    ?.getByTestId("artifact-dialog")
    .innerText({ timeout: 2_000 })
    .catch(() => "");
  console.log(`  the dialog showed: ${JSON.stringify(shown ?? "").slice(0, 600)}`);
  await lastPage
    ?.screenshot({ path: `${cfg.shots}/failed-at.png`, timeout: 5_000 })
    .catch(() => {});
} finally {
  step("closing the browser");
  // A renderer still stuck in a stopped preview can hold the browser's close.
  await Promise.race([browser.close(), new Promise((done) => setTimeout(done, 10_000))]);
}

const elsewhere = requests.filter(
  (r) =>
    r.main && !r.url.startsWith(origin) && !r.url.startsWith("data:") && !r.url.startsWith("blob:"),
);
check(
  "every request of the page went to Workbench itself",
  elsewhere.length === 0,
  elsewhere.map((r) => r.url).join(", "),
);
check(
  "the browser reported no refusal by the page's policy",
  policy.length === 0,
  policy.join(" | "),
);
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
// The browser may still be closing a stuck renderer: the run's verdict is in.
process.exit(0);
