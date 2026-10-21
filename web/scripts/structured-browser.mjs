/* global process, console, URL, document, window, getComputedStyle, location */
// Citations, charts and tables in the selected browser (workbench-rich-content-roadmap.md
// RC4), under the headers Workbench really sends: a web source's number after
// the link it cites, moving to the source by mouse and keyboard, in each
// branch; a source with no place only in the list; a passage's [n] opening
// that passage and a number no passage has staying words; charts and a table
// an answer asked for, their numbers, a CSV written as data, one that cannot
// be drawn as its source; a tool's chart; both themes and a phone. Each check
// is one PASS or FAIL line.
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { contextOptions, launchBrowser, readOptions } from "./browser-options.mjs";

const { configPath, browserName } = readOptions();
const cfg = JSON.parse(readFileSync(configPath, "utf8"));
const require = createRequire(import.meta.url);
mkdirSync(cfg.shots, { recursive: true });
const origin = new URL(cfg.url).origin;
const browser = await launchBrowser(require(cfg.playwright), cfg, browserName);
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}

const errors = [];
const policy = [];
const requests = [];
const REFUSED = /Content Security Policy|Refused to/i;

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
  await context.addInitScript(() => {
    window.__policy = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      window.__policy.push(`${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (REFUSED.test(message.text())) policy.push(message.text());
  });
  page.on("request", (request) => requests.push(request.url()));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function chat(page, id) {
  await page.goto(`${cfg.url}/chats/${id}`);
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

try {
  for (const scheme of ["dark", "light"]) {
    const { context, page } = await open(scheme, DESK);

    // --- a web source's number, where the answer cites it ---------------------------
    await chat(page, cfg.chats.cited);
    const answer = page.locator("[data-testid=answer]").last();
    await answer.getByText("A third page").waitFor();
    // The version shown is the one tried again: back to the first.
    await answer.getByRole("button", { name: "Previous version of this answer" }).click();
    await answer.getByText("Another page").waitFor();
    const one = answer.getByRole("link", { name: "Source 1" });
    const two = answer.getByRole("link", { name: "Source 2" });
    const after = await one.evaluate((a) => {
      const sup = a.closest("sup");
      return sup?.previousElementSibling?.getAttribute("href") ?? null;
    });
    check(
      `${scheme}: a web source's number follows the link it cites`,
      after === "https://example.org/page" && (await two.count()) === 1,
      String(after),
    );
    check(
      `${scheme}: a source the gateway gave no place has no number, only its line in Sources`,
      (await answer.getByRole("link", { name: "Source 3" }).count()) === 0 &&
        (await answer.locator("[data-source-n='3']").innerText()).includes("A page with no place"),
    );
    const before = await page.evaluate(() => location.href);
    await two.click();
    const clicked = await page.evaluate(() => document.activeElement?.textContent ?? "");
    await one.focus();
    await page.keyboard.press("Enter");
    const keyed = await page.evaluate(() => document.activeElement?.textContent ?? "");
    check(
      `${scheme}: a number moves to its source, by mouse and by keyboard, and the address stays`,
      clicked.includes("Another page") &&
        keyed.includes("The page") &&
        (await page.evaluate(() => location.href)) === before,
      `${clicked} | ${keyed}`,
    );
    await answer.getByRole("button", { name: "Next version of this answer" }).click();
    await answer.getByText("A third page").waitFor();
    await answer.getByRole("link", { name: "Source 1" }).click();
    const branch = await page.evaluate(() => document.activeElement?.textContent ?? "");
    check(
      `${scheme}: in the other version, its own number moves to its own source`,
      branch.includes("A third page"),
      branch,
    );
    await answer.screenshot({ path: `${cfg.shots}/cited-${scheme}.png` });

    // --- a passage's number ---------------------------------------------------------------
    await chat(page, cfg.chats.passages);
    const passaged = page.locator("[data-testid=answer]").last();
    const passage = passaged.getByRole("link", { name: "Passage 1" });
    await passage.waitFor();
    check(
      `${scheme}: a number no passage has stays words`,
      (await passaged.getByRole("link", { name: "Passage 9" }).count()) === 0 &&
        (await passaged.innerText()).includes("[9]"),
    );
    await passage.focus();
    await page.keyboard.press("Enter");
    const opened = await page.evaluate(() => {
      const item = document.querySelector('[data-passage-rank="1"]');
      return {
        open: item?.querySelector("details")?.open ?? false,
        focused: item?.contains(document.activeElement) ?? false,
      };
    });
    check(
      `${scheme}: a passage's number opens that passage, by keyboard`,
      opened.open && opened.focused,
      JSON.stringify(opened),
    );

    // --- charts and tables an answer asked for --------------------------------------------
    await chat(page, cfg.chats.drawn);
    const drawn = page.locator("[data-testid=answer]").last();
    const chart = drawn.getByTestId("structured-chart");
    await chart.waitFor();
    const bars = await chart.locator("svg.chart .chart-bar").count();
    const fills = await chart
      .locator("svg.chart .chart-bar")
      .evaluateAll((els) => [...new Set(els.map((el) => getComputedStyle(el).fill))]);
    check(
      `${scheme}: a bar chart draws a bar a point, each series in its own colour`,
      bars === 6 && fills.length === 2,
      `${bars} bars, ${fills.join(" ")}`,
    );
    const named = await chart.locator("svg.chart").getAttribute("aria-labelledby");
    check(`${scheme}: the chart is named for a screen reader`, Boolean(named));
    const tick = chart.locator("svg.chart .chart-tick").first();
    const panel = await chart.evaluate((el) => getComputedStyle(el).backgroundColor);
    const tickColour = await tick.evaluate((el) => getComputedStyle(el).fill);
    const ratio = contrast(rgb(tickColour), rgb(panel));
    check(`${scheme}: a chart's words read at least 4.5:1`, ratio >= 4.5, ratio.toFixed(2));
    await chart.getByRole("button", { name: "Data" }).click();
    check(
      `${scheme}: its numbers are a table a click away`,
      (await chart.getByRole("row").count()) === 7,
    );
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      chart.getByRole("button", { name: "Download Tokens a second as CSV" }).click(),
    ]);
    const csv = readFileSync(await download.path(), "utf8");
    check(
      `${scheme}: its CSV is data: a cell a spreadsheet would run is written as words`,
      download.suggestedFilename() === "tokens-a-second.csv" &&
        csv.startsWith("Series,Model,tok/s\r\n") &&
        csv.includes("Before,'=cmd(),40") &&
        !/(^|,)=/m.test(csv),
      csv.slice(0, 120),
    );
    const table = drawn.getByTestId("structured-table");
    const seconds = table.getByRole("button", { name: "Seconds" });
    await seconds.click();
    const sorted = await table.locator("tbody tr td:first-child").allInnerTexts();
    check(
      `${scheme}: a table sorts by a column at a click`,
      JSON.stringify(sorted) === JSON.stringify(["c", "b", "a"]) &&
        (await seconds.locator("xpath=..").getAttribute("aria-sort")) === "ascending",
      JSON.stringify(sorted),
    );
    check(
      `${scheme}: a chart that cannot be drawn is its source, saying why`,
      (await drawn.getByText(/^Chart not shown: "type" must be/).count()) === 1,
    );
    await chart.screenshot({ path: `${cfg.shots}/chart-${scheme}.png` });

    // --- a tool's chart -----------------------------------------------------------------------
    await chat(page, cfg.chats.plotted);
    const plotted = page.locator("[data-testid=answer]").last().getByTestId("structured-chart");
    await plotted.waitFor();
    check(
      `${scheme}: a tool's chart is drawn under its call`,
      (await plotted.locator("svg.chart polyline").count()) === 1 &&
        (await plotted.innerText()).includes("Load over a day"),
    );
    const refused = await page.evaluate(() => window.__policy);
    check(`${scheme}: the page's policy refused nothing`, refused.length === 0, refused.join(", "));
    await context.close();
  }

  // --- a phone -----------------------------------------------------------------------------------
  {
    const { context, page } = await open("light", PHONE, {
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 3,
    });
    await chat(page, cfg.chats.drawn);
    const chart = page.getByTestId("structured-chart");
    await chart.waitFor();
    await chart.scrollIntoViewIfNeeded();
    const fit = await page.evaluate(() => {
      const wide = [];
      for (const el of document.querySelectorAll(".structured, .structured *")) {
        const box = el.getBoundingClientRect();
        if (
          box.width > 0 &&
          box.right > window.innerWidth + 1 &&
          !el.closest(".data-table-scroll")
        ) {
          wide.push(el.className?.baseVal ?? el.className ?? el.tagName);
        }
      }
      return { page: document.documentElement.scrollWidth <= window.innerWidth + 1, wide };
    });
    check(
      "phone: a chart and a table fit the screen (a wide table scrolls in its own box)",
      fit.page && fit.wide.length === 0,
      JSON.stringify(fit.wide),
    );
    await page.screenshot({ path: `${cfg.shots}/chart-light-phone.png` });
    await context.close();
  }
} finally {
  await browser.close();
}

const elsewhere = requests.filter(
  (url) => !url.startsWith(origin) && !url.startsWith("data:") && !url.startsWith("blob:"),
);
check("every request went to Workbench itself", elsewhere.length === 0, elsewhere.join(", "));
check("the browser reported no refusal by the policy", policy.length === 0, policy.join(" | "));
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures in ${cfg.shots}`);
