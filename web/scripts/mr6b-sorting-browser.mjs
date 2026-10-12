/* global process, console, document, getComputedStyle, window, fetch */
// The page, against the real stack (MR6b acceptance): Workbench with a real gateway, a real
// inference driver and a real result sorter on the processor, and Eugene's design documents already
// kept. `scripts/mr6b-sorting-acceptance.py` starts all of it, builds this page from the source
// and runs this script with the signed-in person's cookies; it reads the CHECK lines this prints.
//
// With Sort time at its default (3 s) the screen does not sort on this processor, and a person is
// told so, in the server's own sentence, with the speed the sorter was measured at and the seconds
// a usual sort would add; Test a question says the same of the question it was asked, and no
// passage has a place by a sorter. With Sort time raised the headline says "sorted by" the sorter,
// the notice says where it runs and how fast it reads, and Test a question says how many passages
// it put in order and how long that took, and each passage's place by the sorter beside its places
// by words and by meaning, in the sorter's order. Pictures, dark and light, at 1440 and 390.
import { mkdirSync, readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
mkdirSync(cfg.shots, { recursive: true });
const SORTER = cfg.sorter;
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };
const errors = [];
let failed = 0;

/** One claim, reported as a line the acceptance reads. A claim that fails does not end the run:
 * what comes after it is still looked at. */
function report(id, ok, text) {
  if (!ok) failed += 1;
  console.log(`CHECK ${id} ${ok ? "PASS" : "FAIL"} ${text.replace(/\s+/g, " ")}`);
}

async function claim(id, text, body) {
  try {
    const detail = await body();
    report(id, detail === undefined || detail === true, text);
    if (detail !== undefined && detail !== true) console.log(`  detail: ${String(detail)}`);
  } catch (error) {
    report(id, false, `${text} (${String(error.message ?? error).split("\n")[0]})`);
  }
}

const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });

async function open(scheme, viewport) {
  const context = await browser.newContext({ colorScheme: scheme, viewport });
  context.setDefaultTimeout(90000);
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function setting(values) {
  const response = await fetch(`${cfg.url}/v1/config`, {
    method: "PATCH",
    headers: { "content-type": "application/json", authorization: `Bearer ${cfg.admin}` },
    body: JSON.stringify(values),
  });
  const done = await response.json();
  if (!response.ok || done.rejected.length) throw new Error(`setting ${JSON.stringify(values)}`);
}

/** The server, asked as the signed-in person. */
async function ask(context, path, method = "GET", body = undefined) {
  const headers = {
    Origin: cfg.url,
    "X-Workbench-Secret": cfg.secret,
    ...(body === undefined ? {} : { "content-type": "application/json" }),
  };
  const response = await context.request.fetch(`${cfg.url}${path}`, {
    method,
    headers,
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok()) throw new Error(`${path}: ${response.status()}`);
  return response.json();
}

/** A speed as the screen says it: two figures. */
function rateWords(rate) {
  const whole =
    rate >= 100
      ? Math.round(rate / 10 ** (String(Math.trunc(rate)).length - 2)) *
        10 ** (String(Math.trunc(rate)).length - 2)
      : rate;
  return Math.round(whole).toLocaleString("en-US");
}

const text = async (locator) => ((await locator.textContent()) ?? "").trim();
const wider = (page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

async function tryQuestion(page, words) {
  await page.getByTestId("documents-test-question").fill(words);
  await page.getByTestId("documents-test-ask").click();
  await page
    .getByTestId("documents-test-result")
    .and(page.locator("[data-status=found]"))
    .waitFor();
}

/** What each tried passage says of where each way put it, in the order they are listed. */
const placesOf = (page) =>
  page
    .getByTestId("documents-test-hit")
    .evaluateAll((hits) =>
      hits.map(
        (hit) => hit.querySelector("[data-testid=documents-test-places]")?.textContent ?? "",
      ),
    );

const sorterPlaces = (said) =>
  said
    .map((words) => /the sorter put it (\d+)(?:st|nd|rd|th)/.exec(words)?.[1])
    .filter(Boolean)
    .map(Number);

/** Every scroller to its top, and the window made tall enough for the whole page. */
async function tall(page) {
  const more = await page.evaluate(() => {
    let most = 0;
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) {
        most = Math.max(most, el.scrollHeight - el.clientHeight);
      }
    }
    return most;
  });
  const size = page.viewportSize();
  await page.setViewportSize({
    width: size.width,
    height: Math.min(size.height + more + 40, 7000),
  });
  await page.waitForTimeout(250);
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) el.scrollTop = 0;
    }
  });
}

async function snap(page, name) {
  const size = page.viewportSize();
  await tall(page);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${cfg.shots}/${name}.png` });
  await page.setViewportSize(size);
}

try {
  // --- Sort time as it is shipped: 3 s, which this processor is too slow for -----------------
  await setting({ documentsSortSeconds: null, documentsSortModel: null });
  const { context, page } = await open("dark", DESK);
  await page.goto(`${cfg.url}/documents`);
  const box = page.getByTestId("documents-sort");
  let slow = {};
  await claim(
    "default-notice",
    "at the default Sort time the screen says by itself, with the numbers, why the passages are not sorted",
    async () => {
      await box.and(page.locator("[data-code=over_budget]")).waitFor();
      slow = (await ask(context, "/api/documents")).search.sort;
      const said = await text(page.getByTestId("documents-sort-reason"));
      const m = new RegExp(
        `^Not sorted: ${SORTER} sorts about ([\\d,]+) tokens a second here, so sorting the 18 passages a question usually finds \\(about 300 tokens each\\) would add about ([\\d.]+) s to each answer, and Sort time is 3 s\\. Raise Sort time in Workbench's settings \\(in Eugene's console\\) to sort anyway\\.$`,
      ).exec(said);
      if (!m) return `the sentence was: ${said}`;
      if (m[1] !== rateWords(slow.ratePerSecond)) {
        return `the page says ${m[1]} tokens a second, the server measured ${slow.ratePerSecond}`;
      }
      if (Math.abs(Number(m[2]) - slow.typical.seconds) > 0.06 * slow.typical.seconds + 0.011) {
        return `the page says ${m[2]} s, the server ${slow.typical.seconds}`;
      }
      if ((await box.evaluate((el) => el.classList.contains("notice--warn"))) !== false) {
        return "a sorter that is only too slow for Sort time is not a warning";
      }
      return true;
    },
  );
  await claim(
    "default-headline",
    "and the headline does not say it sorts, and the sorter is not said to be in use",
    async () => {
      const headline = await text(page.getByTestId("documents-search-headline"));
      if (!/^Searching by meaning and words\.$/.test(headline)) return headline;
      if ((await page.getByTestId("documents-sort-model").count()) !== 0) {
        return "the slow sorter is called in use";
      }
      return true;
    },
  );
  await claim(
    "default-test",
    "Test a question says the same of its own passages with their numbers, and no passage has a place by a sorter",
    async () => {
      await tryQuestion(page, cfg.question);
      const reason = await text(page.getByTestId("documents-test-sort"));
      const m = new RegExp(
        `^Not sorted: ${SORTER} sorts about ([\\d,]+) tokens a second here, so sorting these (\\d+) passages \\(([\\d,]+) tokens\\) would take about ([\\d.]+) s, and Sort time is 3 s\\. Raise Sort time in Workbench's settings \\(in Eugene's console\\) to sort anyway\\.$`,
      ).exec(reason);
      if (!m) return `the sentence was: ${reason}`;
      const api = (
        await ask(context, "/api/documents/test", "POST", { question: cfg.question, passages: 5 })
      ).sort;
      if (Number(m[2]) !== api.passages || Number(m[3].replace(/,/g, "")) !== api.tokens) {
        return `the page says ${m[2]} passages and ${m[3]} tokens, the server ${api.passages} and ${api.tokens}`;
      }
      if ((await page.getByTestId("documents-test-sorted").count()) !== 0)
        return "it says it sorted";
      for (const words of await placesOf(page)) if (/sorter/.test(words)) return words;
      return true;
    },
  );
  await snap(page, "sort-default-dark-desk");
  await context.close();

  // --- Sort time raised: sorted --------------------------------------------------------------
  await setting({ documentsSortSeconds: cfg.raised });
  const raised = await open("dark", DESK);
  const view = raised.page;
  await view.goto(`${cfg.url}/documents`);
  await claim(
    "raised-headline",
    `with Sort time raised the headline says the passages are sorted by ${SORTER}`,
    async () => {
      await view
        .getByTestId("documents-search-headline")
        .filter({ hasText: `Searching by meaning and words, sorted by ${SORTER}.` })
        .waitFor();
      const mode = await view.getByTestId("documents-search").getAttribute("data-mode");
      return mode === `meaning and words, sorted by ${SORTER}` ? true : mode;
    },
  );
  await claim(
    "raised-notice",
    "and the notice says the sorter in use, that it runs on the person's machines, and how fast it reads, as the server measured it",
    async () => {
      const used = await text(view.getByTestId("documents-sort-model"));
      if (used !== `In use: ${SORTER} (automatic)`) return used;
      const where = await text(view.getByTestId("documents-sort-where"));
      if (!/^It runs on your own machines/.test(where)) return where;
      const api = (await ask(raised.context, "/api/documents")).search.sort;
      const speed = await text(view.getByTestId("documents-sort-speed"));
      const m = new RegExp(
        `^${SORTER} sorts about ([\\d,]+) tokens a second here, so sorting the 18 passages a question usually finds \\(about 300 tokens each\\) adds about ([\\d.]+) s to each answer, within Sort time \\(${cfg.raised} s\\)\\.$`,
      ).exec(speed);
      if (!m) return speed;
      return m[1] === rateWords(api.ratePerSecond) ? true : `${m[1]} against ${api.ratePerSecond}`;
    },
  );
  let places = [];
  await claim(
    "raised-test-mode",
    "Test a question says it searched by meaning and words, sorted by the sorter, and how many passages it put in order and how long that took",
    async () => {
      await tryQuestion(view, cfg.question);
      const mode = await text(view.getByTestId("documents-test-mode"));
      if (mode !== `Searched by meaning and words, sorted by ${SORTER}.`) return mode;
      const line = await text(view.getByTestId("documents-test-sorted"));
      const m = new RegExp(
        `^The sorter put (\\d+) passages in order in ([\\d.]+) s \\(Sort time is ${cfg.raised} s\\)\\.$`,
      ).exec(line);
      if (!m) return line;
      const api = (
        await ask(raised.context, "/api/documents/test", "POST", {
          question: cfg.question,
          passages: 5,
        })
      ).sort;
      if (Number(m[1]) !== api.passages)
        return `the page says ${m[1]} passages, the server ${api.passages}`;
      return (await view.getByTestId("documents-test-sort").count()) === 0
        ? true
        : "a reason is shown";
    },
  );
  await claim(
    "raised-places",
    "each passage says where the sorter put it, a place and never a score, beside where words and meaning put it, and the first listed is the sorter's first",
    async () => {
      places = await placesOf(view);
      const byTheSorter = sorterPlaces(places);
      if (byTheSorter.length < 3) return JSON.stringify(places);
      if (byTheSorter[0] !== 1) return `the first place is ${byTheSorter[0]}`;
      if (JSON.stringify([...byTheSorter].sort((a, b) => a - b)) !== JSON.stringify(byTheSorter)) {
        return `not in the sorter's order: ${byTheSorter}`;
      }
      for (const words of places) {
        if (
          words.includes("the sorter put it") &&
          !/the sorter put it \d+(st|nd|rd|th)\.$/.test(words)
        ) {
          return words;
        }
        if (/\d\.\d/.test(words.replace(/closeness \d\.\d+/, ""))) return `a score: ${words}`;
      }
      return places.some((words) =>
        /(Words|words) put it .*(meaning put it .*the sorter put it)/.test(words),
      )
        ? true
        : "no passage says where words, meaning and the sorter put it";
    },
  );
  await claim(
    "raised-first-file",
    "and the file listed first is the file the server's own answer puts first",
    async () => {
      const api = await ask(raised.context, "/api/documents/test", "POST", {
        question: cfg.question,
        passages: 5,
      });
      const first = await text(view.getByTestId("documents-test-hit").first());
      return first.includes(api.passages[0].name)
        ? true
        : `${first.slice(0, 80)} / ${api.passages[0].name}`;
    },
  );
  await snap(view, "sort-sorted-dark-desk");
  await raised.context.close();

  // --- pictures, dark and light, at a desk and a phone ---------------------------------------
  await claim(
    "pictures",
    "pictures of the screen with a sorter, dark and light, at 1440 and 390, none wider than the window",
    async () => {
      for (const scheme of ["dark", "light"]) {
        for (const [where, size] of [
          ["desk", DESK],
          ["phone", PHONE],
        ]) {
          const { context: c, page: p } = await open(scheme, size);
          await p.goto(`${cfg.url}/documents`);
          await p.getByTestId("documents-sort-model").waitFor();
          await tryQuestion(p, cfg.question);
          await snap(p, `sort-${scheme}-${where}`);
          if ((await wider(p)) > 1) return `${scheme} ${where}: wider than the window`;
          await c.close();
        }
      }
      return existsSync(`${cfg.shots}/sort-light-phone.png`) ? true : "no picture";
    },
  );
  await claim("no-page-errors", "the page raised no error", () =>
    errors.length === 0 ? true : errors.slice(0, 2).join(" | "),
  );
} catch (error) {
  report("ran", false, `the run stopped: ${String(error.message ?? error).split("\n")[0]}`);
} finally {
  await browser.close();
}
console.log(failed === 0 ? "DONE" : `DONE with ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
