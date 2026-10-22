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
import {
  contextOptions,
  goChat,
  goSignedIn,
  keepScheme,
  ignorablePageError,
  launchBrowser,
  readOptions,
  watchPolicy,
  screenshotConsoleRefusal,
} from "./browser-options.mjs";

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
  await context.exposeBinding("__reportPolicy", ({ frame }, message) => {
    if (!frame.parentFrame()) policy.push(message);
  });
  await context.addInitScript(watchPolicy, browserName);
  const page = await context.newPage();
  page.on("pageerror", (error) => {
    if (!ignorablePageError(browserName, error.message)) errors.push(error.message);
  });
  page.on("console", (message) => {
    if (REFUSED.test(message.text()) && !screenshotConsoleRefusal(browserName, message.text()))
      policy.push(message.text());
  });
  page.on("request", (request) => requests.push(request.url()));
  await goSignedIn(page, `${cfg.url}/#signin=${cfg.secret}`);
  await keepScheme(page, browserName, scheme);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  const close = context.close.bind(context);
  context.close = async () => {
    const snapshot = await page.evaluate(() => ({
      screenshots: window.__screenshotRefusals,
    }));
    if (snapshot.screenshots)
      console.log(
        `SKIP  WebKit: ${snapshot.screenshots} Playwright screenshot body {} stylesheet refusals; app CSP remains checked.`,
      );
    await close();
  };
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

// Extend only this fixture's answer at the browser boundary. The real server,
// authentication, page headers and chart renderer stay in use; no Python fixture
// changes are needed for these additional version 1 formats.
async function withExtraCharts(page) {
  const pie = {
    type: "pie",
    title: "Parts of a whole",
    series: [{ name: "Parts", points: Array.from({ length: 14 }, (_, i) => [`p${i}`, i + 1]) }],
  };
  const stacked = {
    type: "bar",
    stacked: true,
    title: "Stacked totals",
    series: [
      {
        name: "One",
        points: [
          ["a", 20],
          ["b", -3],
        ],
      },
      {
        name: "Two",
        points: [
          ["b", -2],
          ["a", 21],
        ],
      },
    ],
  };
  const scatter = {
    type: "scatter",
    title: "Measured points",
    series: [
      {
        name: "One",
        points: [
          [1, 2],
          [2, 4],
        ],
      },
      {
        name: "Two",
        points: [
          [1, 3],
          [2, 5],
        ],
      },
    ],
  };
  const extras = [
    pie,
    stacked,
    scatter,
    { ...pie, series: [{ points: [["a", -1]] }] },
    { ...stacked, series: [stacked.series[0], { points: [["a", 1]] }] },
  ]
    .map((spec) => `\n\n\`\`\`chart\n${JSON.stringify(spec)}\n\`\`\``)
    .join("");
  await page.route(`${origin}/api/chats/${cfg.chats.drawn}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const answer = body.messages.findLast((message) => message.role === "assistant");
    answer.content += extras;
    await route.fulfill({ response, json: body });
  });
}

async function checkValues(page, chart, name) {
  const marks = chart.locator('svg.chart [tabindex="0"]');
  const first = marks.first();
  const second = marks.nth(1);
  const label = await first.getAttribute("aria-label");
  const next = await second.getAttribute("aria-label");
  const tooltip = chart.getByRole("tooltip");
  await first.locator(".chart-marker").hover();
  await tooltip.waitFor();
  check(`${name}: hovering a mark shows its value`, (await tooltip.innerText()) === label);
  await first.focus();
  await second.locator(".chart-marker").hover();
  await tooltip.filter({ hasText: next }).waitFor();
  check(
    `${name}: hovering another mark shows its own value while the first has focus`,
    (await tooltip.innerText()) === next &&
      (await second.getAttribute("aria-describedby")) === (await tooltip.getAttribute("id")),
    await tooltip.innerText(),
  );
  await page.mouse.move(0, 0);
  await page.keyboard.press("Tab");
  await tooltip.filter({ hasText: next }).waitFor();
  check(
    `${name}: Tab gives the next mark focus and shows its value`,
    (await second.evaluate((el) => el === document.activeElement)) &&
      (await tooltip.innerText()) === next,
  );
  await page.keyboard.press("Escape");
  await tooltip.waitFor({ state: "hidden" });
  check(`${name}: Escape dismisses the value`, (await tooltip.count()) === 0);
  await chart.getByRole("button", { name: "Data", exact: true }).focus();
}

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
    await withExtraCharts(page);
    await chat(page, cfg.chats.drawn);
    const drawn = page.locator("[data-testid=answer]").last();
    const chart = drawn.getByTestId("structured-chart").filter({ hasText: "Tokens a second" });
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
    await checkValues(page, chart, `${scheme}: bar`);
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
      (await drawn.getByText('Chart not shown: it has no "series"', { exact: true }).count()) ===
        1 &&
        (await drawn.locator("pre code").allInnerTexts()).some((source) => {
          try {
            const value = JSON.parse(source);
            return value.type === "pie" && Array.isArray(value.series) && value.series.length === 0;
          } catch {
            return false;
          }
        }),
    );
    await chart.screenshot({ path: `${cfg.shots}/chart-${scheme}.png` });

    const pie = drawn.getByTestId("structured-chart").filter({ hasText: "Parts of a whole" });
    check(
      `${scheme}: a pie draws at most twelve slices, combining the rest as Other`,
      (await pie.locator(".chart-pie-slice").count()) === 12 &&
        (await pie.getByRole("img", { name: "Parts · Other · 39", exact: true }).count()) === 1,
    );
    await checkValues(page, pie, `${scheme}: pie`);
    await pie.getByRole("button", { name: "Data", exact: true }).click();
    check(
      `${scheme}: a pie keeps all fourteen original data points`,
      (await pie.getByRole("row").count()) === 15,
    );
    await pie.screenshot({ path: `${cfg.shots}/pie-${scheme}.png` });

    const stacked = drawn.getByTestId("structured-chart").filter({ hasText: "Stacked totals" });
    const stack = await stacked.evaluate((el) => {
      const bar = (label) => {
        const rect = el.querySelector(`[aria-label="${label}"] .chart-bar`);
        return Object.fromEntries(
          ["x", "y", "height"].map((key) => [key, Number(rect.getAttribute(key))]),
        );
      };
      return {
        first: bar("One · a · 20"),
        second: bar("Two · a · 21"),
        negativeFirst: bar("One · b · -3"),
        negativeSecond: bar("Two · b · -2"),
        top: Math.min(
          ...[...el.querySelectorAll(".chart-grid")].map((grid) => Number(grid.getAttribute("y1"))),
        ),
      };
    });
    check(
      `${scheme}: stacks match categories, separate negative values, and fit the totals`,
      stack.first.x === stack.second.x &&
        Math.abs(stack.second.y + stack.second.height - stack.first.y) < 0.01 &&
        stack.negativeFirst.x === stack.negativeSecond.x &&
        Math.abs(stack.negativeFirst.y + stack.negativeFirst.height - stack.negativeSecond.y) <
          0.01 &&
        stack.second.y >= stack.top,
      JSON.stringify(stack),
    );
    await checkValues(page, stacked, `${scheme}: stacked bar`);
    await stacked.screenshot({ path: `${cfg.shots}/stacked-${scheme}.png` });
    const scatter = drawn.getByTestId("structured-chart").filter({ hasText: "Measured points" });
    await checkValues(page, scatter, `${scheme}: scatter`);
    for (const example of [chart, pie, stacked, scatter]) {
      const identities = await example
        .locator(".chart-legend-mark")
        .evaluateAll((keys) =>
          keys.map(
            (key) =>
              `${key.querySelector(".chart-marker")?.innerHTML}|${key.querySelector("line")?.getAttribute("stroke-dasharray")}`,
          ),
        );
      check(
        `${scheme}: ${await example.locator(".structured-title").innerText()} has distinct shapes and dashes`,
        identities.length > 1 && new Set(identities).size === identities.length,
      );
    }
    for (const reason of [
      "A pie cannot show negative values.",
      "A stacked chart needs one value per category in each series.",
    ]) {
      check(
        `${scheme}: refused chart says why: ${reason}`,
        (await drawn.getByText(`Chart not shown: ${reason}`, { exact: true }).count()) === 1,
      );
    }

    // --- a tool's chart -----------------------------------------------------------------------
    await chat(page, cfg.chats.plotted);
    const plotted = page.locator("[data-testid=answer]").last().getByTestId("structured-chart");
    await plotted.waitFor();
    check(
      `${scheme}: a tool's chart is drawn under its call`,
      (await plotted.locator("svg.chart polyline").count()) === 1 &&
        (await plotted.innerText()).includes("Load over a day"),
    );
    await checkValues(page, plotted, `${scheme}: line`);
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
    await withExtraCharts(page);
    await chat(page, cfg.chats.drawn);
    const chart = page.getByTestId("structured-chart").filter({ hasText: "Tokens a second" });
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
    await page
      .getByTestId("structured-chart")
      .filter({ hasText: "Parts of a whole" })
      .screenshot({ path: `${cfg.shots}/pie-light-phone.png` });
    await page
      .getByTestId("structured-chart")
      .filter({ hasText: "Stacked totals" })
      .screenshot({ path: `${cfg.shots}/stacked-light-phone.png` });
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
