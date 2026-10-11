/* global process, console, document, getComputedStyle, window, NodeFilter */
// Reading Workbench in Chrome (workbench-reading.md): what each kind of text
// looks like, in pictures and in numbers. Screenshots of a rich answer, a
// story, a length stop, the settings and an answer being written, dark and
// light, at a desk and a phone; and the computed type of each kind of text
// (styles-<scheme>.json), with its contrast against what is behind it.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const cfg = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(import.meta.url);
const { chromium } = require(cfg.playwright);
mkdirSync(cfg.shots, { recursive: true });
const shot = (name) => ({ path: `${cfg.shots}/${name}.png` });
const browser = await chromium.launch({ executablePath: cfg.chrome, headless: true });
const errors = [];
const DESK = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

async function open(scheme, viewport, extra = {}) {
  const context = await browser.newContext({ colorScheme: scheme, viewport, ...extra });
  await context.addCookies(cfg.cookies);
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${cfg.url}/#signin=${cfg.secret}`);
  await page.getByTestId("new-chat").waitFor({ state: "attached" });
  return { context, page };
}

async function settle(page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
}

async function chat(page, id) {
  await page.goto(`${cfg.url}/chats/${id}`);
  await page.locator('[data-testid=answer]:not([data-status="running"])').first().waitFor();
  await settle(page);
}

// Every scroller to its top: the chat scrolls inside the page, not the page.
async function toTop(page) {
  await page.evaluate(() => {
    for (const el of document.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) el.scrollTop = 0;
    }
  });
  await page.waitForTimeout(150);
}

// The window made tall enough to show the whole chat at once.
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
  await settle(page);
  await toTop(page);
}

// The computed type of each kind of text, and a census of every text's look.
function probe() {
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const blend = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const behind = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a > 0) {
        layers.push(c);
        if (c.a >= 1) break;
      }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (const layer of layers.reverse()) base = blend(layer, base);
    return base;
  };
  const hex = ({ r, g, b }) =>
    "#" + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  const canvas = document.createElement("canvas").getContext("2d");
  const describe = (el) => {
    const s = getComputedStyle(el);
    const back = behind(el);
    const fore = blend(parse(s.color) || { r: 0, g: 0, b: 0, a: 1 }, back);
    const [hi, lo] = [lum(fore), lum(back)].sort((a, b) => b - a);
    const box = el.getBoundingClientRect();
    const out = {
      text: (el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60),
      font: `${s.fontWeight} ${s.fontSize}/${s.lineHeight} ${s.fontFamily.split(",")[0]}`,
      letterSpacing: s.letterSpacing,
      color: hex(fore),
      background: hex(back),
      contrast: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100,
      margin: `${s.marginTop} ${s.marginRight} ${s.marginBottom} ${s.marginLeft}`,
      padding: `${s.paddingTop} ${s.paddingRight} ${s.paddingBottom} ${s.paddingLeft}`,
      width: Math.round(box.width),
      border: `${s.borderTopWidth} ${s.borderTopStyle} ${s.borderTopColor} / left ${s.borderLeftWidth} ${s.borderLeftColor}`,
    };
    if (/^(P|LI|BLOCKQUOTE)$/.test(el.tagName)) {
      canvas.font = `${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
      const sample = "The quick brown fox jumps over the lazy dog, and then it rests a while.";
      const avg = canvas.measureText(sample).width / sample.length;
      const inner = box.width - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
      out.charsPerLine = Math.round(inner / avg);
    }
    return out;
  };
  const kinds = {
    "answer paragraph": ".answer p",
    "answer strong": ".answer strong",
    "answer em": ".answer em",
    "answer h2": ".answer h2",
    "answer h3": ".answer h3",
    "answer list item": ".answer li",
    "answer inline code": ".answer :not(pre) > code",
    "answer code block": ".answer pre",
    "answer table header": ".answer th",
    "answer table cell": ".answer td",
    "answer quote": ".answer blockquote",
    "answer link": ".answer a",
    "answer rule": ".answer hr",
    "answer task box": ".answer input",
    "user message": "[data-testid=user-message]",
    "chat title": "[data-testid=chat-title]",
    "chat list": "[data-testid=chat-list]",
    composer: "[data-testid=composer]",
    send: "[data-testid=send]",
    "model picker": "[data-testid=model-picker]",
    sources: "[data-testid=sources]",
    "answer status": "[data-testid=answer-status]",
  };
  const found = {};
  for (const [name, selector] of Object.entries(kinds)) {
    const el = document.querySelector(selector);
    found[name] = el ? describe(el) : null;
  }
  const census = {};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim();
    const el = node.parentElement;
    if (!text || !el || !el.getClientRects().length) continue;
    const d = describe(el);
    const key = `${d.color} on ${d.background} · ${d.font}`;
    census[key] ??= { count: 0, contrast: d.contrast, samples: [] };
    census[key].count += 1;
    if (census[key].samples.length < 4) census[key].samples.push(text.slice(0, 40));
  }
  return {
    root: getComputedStyle(document.documentElement).fontSize,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    kinds: found,
    census: Object.fromEntries(Object.entries(census).sort((a, b) => b[1].count - a[1].count)),
  };
}

// What the numbers above cannot say on their own: the rule's line, the code
// block's join, the bullets' colour, how many colours an answer carries, and
// whether anything is wider than the window.
function structure() {
  const parse = (c) => {
    const m = /rgba?\(([^)]+)\)/.exec(c || "");
    if (!m) return null;
    const p = m[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const behind = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c.a >= 1) return c;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
  };
  const answer = document.querySelector(".answer");
  const hr = answer?.querySelector("hr");
  const line = hr ? parse(getComputedStyle(hr).borderTopColor) : null;
  const head = document.querySelector(".code-block .code-head");
  const pre = head?.parentElement?.querySelector("pre");
  const li = answer ? answer.querySelector(":scope > ul > li") : null;
  // The prose's colours only: a code block's syntax colours would let a
  // one-colour answer pass whenever it held code.
  const colours = new Set();
  if (answer) {
    const walker = document.createTreeWalker(answer, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.textContent.trim() && node.parentElement && !node.parentElement.closest("pre")) {
        colours.add(getComputedStyle(node.parentElement).color);
      }
    }
  }
  return {
    ruleContrast: hr && line ? ratio(line, behind(hr)) : null,
    codeGap:
      head && pre ? pre.getBoundingClientRect().top - head.getBoundingClientRect().bottom : null,
    marker: li ? getComputedStyle(li, "::marker").color : null,
    itemColour: li ? getComputedStyle(li).color : null,
    colours: [...colours],
    // The page, and the conversation's own scroller: a wide block inside the
    // chat makes the chat scroll sideways without widening the page.
    overflow: Math.max(
      document.documentElement.scrollWidth - window.innerWidth,
      ...[...document.querySelectorAll("[data-testid=messages]")].map(
        (box) => box.scrollWidth - box.clientWidth,
      ),
    ),
  };
}

// The acceptance: each look the design promises, held to a number.
const failures = [];
function check(name, ok, detail, { always = false } = {}) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok && !always ? "" : `: ${detail}`}`);
  if (!ok) failures.push(name);
}
const px = (value) => parseFloat(value);
function judge(scheme, numbers, shape) {
  const k = numbers.kinds;
  const p = k["answer paragraph"];
  const [size, lead] = (p?.font ?? "").split(" ")[1]?.split("/") ?? [];
  check(`${scheme}: an answer reads at 17 px`, px(size) >= 16.5, `paragraph ${size}`);
  check(
    `${scheme}: lines are 1.5 to 1.8 times the type`,
    px(lead) / px(size) >= 1.5 && px(lead) / px(size) <= 1.8,
    `${lead} on ${size}`,
  );
  check(
    `${scheme}: 60 to 85 characters a line`,
    p?.charsPerLine >= 60 && p?.charsPerLine <= 85,
    `${p?.charsPerLine} characters`,
  );
  const sizeOf = (kind) => px((k[kind]?.font ?? "").split(" ")[1]?.split("/")[0]);
  check(
    `${scheme}: an h2 is at least 1.2 times the text`,
    sizeOf("answer h2") >= 1.2 * px(size),
    `h2 ${sizeOf("answer h2")}px on ${size}`,
  );
  check(
    `${scheme}: an h3 is larger than the text`,
    sizeOf("answer h3") > px(size),
    `h3 ${sizeOf("answer h3")}px on ${size}`,
  );
  check(
    `${scheme}: headings carry their own colour`,
    k["answer h2"]?.color !== p?.color && k["answer h3"]?.color === k["answer h2"]?.color,
    `h2 ${k["answer h2"]?.color}, h3 ${k["answer h3"]?.color}, text ${p?.color}`,
  );
  check(
    `${scheme}: bold is brighter than the text`,
    k["answer strong"]?.color !== p?.color,
    `both ${p?.color}`,
  );
  check(
    `${scheme}: inline code is a warm mark`,
    k["answer inline code"]?.color !== p?.color,
    `both ${p?.color}`,
  );
  check(
    `${scheme}: first-level bullets are marked in colour`,
    shape.marker !== null && shape.marker !== shape.itemColour,
    `marker ${shape.marker}, text ${shape.itemColour}`,
  );
  check(
    `${scheme}: a rule is a hairline, not a bright line`,
    shape.ruleContrast !== null && shape.ruleContrast < 3,
    `rule at ${shape.ruleContrast}:1`,
  );
  check(
    `${scheme}: the Copy bar is joined to its code`,
    shape.codeGap !== null && Math.abs(shape.codeGap) <= 1,
    `gap ${shape.codeGap}px`,
  );
  check(
    `${scheme}: an answer's prose carries at least five colours`,
    shape.colours.length >= 5,
    `${shape.colours.length}: ${shape.colours.join(", ")}`,
    { always: true },
  );
  const faint = Object.entries(numbers.census).filter(([, look]) => look.contrast < 4.5);
  check(
    `${scheme}: every text on the page is at least 4.5:1`,
    faint.length === 0,
    faint.map(([key, look]) => `${key} (${look.contrast}:1, ${look.samples[0]})`).join("; "),
  );
}

try {
  for (const scheme of ["dark", "light"]) {
    {
      const { context, page } = await open(scheme, DESK);
      await page.goto(`${cfg.url}/`);
      await page.waitForTimeout(600);
      await settle(page);
      await page.screenshot(shot(`home-${scheme}-desk`));

      await chat(page, cfg.chats.rich);
      await toTop(page);
      await page.screenshot(shot(`rich-${scheme}-desk`));
      const numbers = await page.evaluate(probe);
      writeFileSync(`${cfg.shots}/styles-${scheme}.json`, JSON.stringify(numbers, null, 2));
      judge(scheme, numbers, await page.evaluate(structure));
      await page.getByText("Reasoning", { exact: true }).first().click();
      await settle(page);
      await page.screenshot(shot(`rich-reasoning-${scheme}-desk`));
      await tall(page);
      await page.screenshot(shot(`rich-all-${scheme}-desk`));

      await page.setViewportSize(DESK);
      await chat(page, cfg.chats.story);
      await tall(page);
      await page.screenshot(shot(`story-all-${scheme}-desk`));

      await page.setViewportSize(DESK);
      await chat(page, cfg.chats.cut);
      await page.screenshot(shot(`cut-${scheme}-desk`));
      await page.getByRole("button", { name: "This chat's settings" }).first().click();
      await settle(page);
      await page.screenshot(shot(`settings-${scheme}-desk`));

      // The other screens a person reads: the top bar's pages and the keys.
      for (const [name, file] of [
        [/Tools/, "tools"],
        [/Media/, "media"],
        [/Job sites/, "jobsites"],
        [/Keyboard shortcuts/, "shortcuts"],
      ]) {
        await chat(page, cfg.chats.rich);
        await page
          .getByRole("link", { name })
          .or(page.getByRole("button", { name }))
          .first()
          .click();
        await settle(page);
        await page.screenshot(shot(`${file}-${scheme}-desk`));
        await page.keyboard.press("Escape");
      }
      await context.close();

      const away = await browser.newContext({ colorScheme: scheme, viewport: DESK });
      const signedOut = await away.newPage();
      await signedOut.goto(cfg.url);
      await settle(signedOut);
      await signedOut.screenshot(shot(`signin-${scheme}-desk`));
      await away.close();
    }
    {
      const { context, page } = await open(scheme, PHONE, { isMobile: true, hasTouch: true });
      await chat(page, cfg.chats.rich);
      await toTop(page);
      await page.screenshot(shot(`rich-${scheme}-phone`));
      const wide = (await page.evaluate(structure)).overflow;
      check(`${scheme} phone: nothing is wider than the screen`, wide <= 1, `${wide}px too wide`);
      await chat(page, cfg.chats.story);
      await toTop(page);
      await page.screenshot(shot(`story-${scheme}-phone`));
      await context.close();
    }
  }
  // An answer being written: the working line while it thinks, then writes.
  for (const scheme of ["dark", "light"]) {
    const { context, page } = await open(scheme, DESK);
    await page.getByTestId("new-chat").click();
    const composer = page.getByTestId("composer");
    await composer.waitFor();
    await composer.fill(cfg.live);
    await page.getByTestId("send").click();
    await page.locator('[data-testid=answer][data-status="running"]').waitFor();
    await page.waitForTimeout(1200);
    await page.screenshot(shot(`running-thinking-${scheme}-desk`));
    await page.waitForTimeout(3500);
    await page.screenshot(shot(`running-writing-${scheme}-desk`));
    await page
      .locator('[data-testid=answer]:not([data-status="running"])')
      .last()
      .waitFor({ timeout: 60000 });
    await context.close();
  }
} finally {
  await browser.close();
}
if (errors.length) {
  console.log("page errors:", errors);
  process.exit(1);
}
if (failures.length) {
  console.log(`${failures.length} reading checks failed; pictures in ${cfg.shots}`);
  process.exit(1);
}
console.log(`PASS: pictures and numbers in ${cfg.shots}`);
