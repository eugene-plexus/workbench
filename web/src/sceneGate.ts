/**
 * The gate a working scene passes before Workbench shows it (workbench.md
 * §6.1). A language model draws the scenes, so each file is read as text and
 * held to the brief: nothing that runs or reaches out, a reduced-motion rule
 * that really stops every loop, loops whose first and last frames match, and
 * a size budget. Only the tests import this; the page never does.
 */

export const SCENE_BUDGET_BYTES = 32 * 1024;
const LOOP_SECONDS = { min: 2, max: 4 };

/** Elements that run, fetch, or animate outside CSS (SMIL ignores the reduced-motion rule). */
const FORBIDDEN = [
  "script",
  "foreignObject",
  "image",
  "feImage",
  "iframe",
  "object",
  "embed",
  "audio",
  "video",
  "link",
  "a",
  "animate",
  "animateTransform",
  "animateMotion",
  "set",
  "discard",
];
const ANIMATED = new Set(["transform", "opacity"]);
const KEYWORDS = new Set([
  "linear",
  "ease",
  "ease-in",
  "ease-out",
  "ease-in-out",
  "step-start",
  "step-end",
  "infinite",
  "normal",
  "reverse",
  "alternate",
  "alternate-reverse",
  "none",
  "forwards",
  "backwards",
  "both",
  "running",
  "paused",
]);

interface Block {
  prelude: string;
  body: string;
}

/** Top-level `prelude { body }` pairs, with nested braces kept in the body. */
function blocks(css: string): Block[] {
  const out: Block[] = [];
  let at = 0;
  while (at < css.length) {
    const open = css.indexOf("{", at);
    if (open < 0) break;
    let depth = 1;
    let end = open + 1;
    for (; end < css.length && depth > 0; end++) {
      if (css[end] === "{") depth++;
      else if (css[end] === "}") depth--;
    }
    if (depth !== 0) throw new Error("its style has unbalanced braces");
    const prelude = css.slice(at, open).split(";").pop() ?? "";
    out.push({ prelude: prelude.trim(), body: css.slice(open + 1, end - 1) });
    at = end;
  }
  return out;
}

const tidy = (value: string) =>
  value
    .replace(/\s+/g, " ")
    .replace(/\s*([(),])\s*/g, "$1")
    .trim();

function declarations(body: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of body.split(";")) {
    const colon = part.indexOf(":");
    if (colon < 0) continue;
    out.set(part.slice(0, colon).trim().toLowerCase(), tidy(part.slice(colon + 1)));
  }
  return out;
}

/** Splits on `sep` outside parentheses, so `cubic-bezier(0, 0, 1, 1)` stays whole. */
function split(value: string, sep: RegExp): string[] {
  const out: string[] = [];
  let depth = 0;
  let word = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (depth === 0 && sep.test(ch)) {
      if (word.trim()) out.push(word.trim());
      word = "";
    } else word += ch;
  }
  if (word.trim()) out.push(word.trim());
  return out;
}

function seconds(token: string): number | null {
  const m = /^(-?\d*\.?\d+)(ms|s)$/.exec(token);
  if (!m) return null;
  return Number(m[1]) / (m[2] === "ms" ? 1000 : 1);
}

/** Everything wrong with one scene file; empty when it may ship. */
export function sceneProblems(text: string, bytes: number): string[] {
  const problems: string[] = [];
  if (bytes > SCENE_BUDGET_BYTES) {
    problems.push(`it is ${bytes} bytes, over the ${SCENE_BUDGET_BYTES}-byte budget`);
  }

  // Nothing that runs or reaches out, read from the raw text first, so a
  // file the parser would reject is still checked.
  if (/<\s*script\b/i.test(text)) problems.push("it has a <script>");
  const withoutNamespaces = text.replace(/\sxmlns(:\w+)?\s*=\s*("[^"]*"|'[^']*')/g, "");
  if (/[a-z][a-z0-9+.-]*:\/\//i.test(withoutNamespaces)) problems.push("it names an external URL");
  if (/\bdata:/i.test(text)) problems.push("it embeds a data: URL");
  if (/javascript:/i.test(text)) problems.push("it has a javascript: URL");
  if (/@import\b/i.test(text)) problems.push("its style imports another file");
  if (/url\(\s*(?!['"]?#)/i.test(text)) problems.push("its style points outside the file");

  const doc = new DOMParser().parseFromString(text, "image/svg+xml");
  const root = doc.documentElement;
  if (doc.getElementsByTagName("parsererror").length > 0 || root.localName !== "svg") {
    problems.push("it is not a well-formed SVG");
    return problems;
  }
  if (!root.hasAttribute("viewBox")) problems.push("its <svg> has no viewBox");
  for (const name of FORBIDDEN) {
    if (doc.getElementsByTagName(name).length > 0) problems.push(`it has a forbidden <${name}>`);
  }
  for (const element of Array.from(doc.getElementsByTagName("*"))) {
    for (const attr of Array.from(element.attributes)) {
      if (attr.localName === "href" && !attr.value.startsWith("#")) {
        problems.push(`<${element.localName}> links outside the file`);
      }
      if (attr.localName === "src") problems.push(`<${element.localName}> has a src`);
      if (/^on/i.test(attr.localName)) {
        problems.push(`<${element.localName}> has an event handler (${attr.localName})`);
      }
      if (attr.localName === "style" && /animation/i.test(attr.value)) {
        problems.push(`<${element.localName}> animates in a style attribute`);
      }
    }
  }

  const css = Array.from(doc.getElementsByTagName("style"))
    .map((s) => s.textContent ?? "")
    .join("\n")
    .replace(/\/\*[\s\S]*?\*\//g, "");
  let top: Block[];
  try {
    top = blocks(css);
  } catch (error) {
    problems.push((error as Error).message);
    return problems;
  }

  const keyframes = new Map<string, Block[]>();
  const rules: Block[] = [];
  const stillRules: Block[] = [];
  let reducedRule = false;
  for (const block of top) {
    if (/^@keyframes\s/i.test(block.prelude)) {
      keyframes.set(block.prelude.replace(/^@keyframes\s+/i, "").trim(), blocks(block.body));
    } else if (/^@media\b/i.test(block.prelude)) {
      const reduced = /prefers-reduced-motion\s*:\s*reduce/i.test(block.prelude);
      reducedRule ||= reduced;
      (reduced ? stillRules : rules).push(...blocks(block.body));
    } else if (block.prelude.startsWith("@")) {
      problems.push(`its style has an unexpected ${block.prelude.split(/\s/)[0]} rule`);
    } else rules.push(block);
  }

  if (keyframes.size === 0) problems.push("it has no @keyframes, so it is not an animation");
  for (const [name, stops] of keyframes) {
    const at = new Map<string, Map<string, string>>();
    for (const stop of stops) {
      const decls = declarations(stop.body);
      for (const prop of decls.keys()) {
        if (!ANIMATED.has(prop) && prop !== "animation-timing-function") {
          problems.push(`@keyframes ${name} animates ${prop}; only transform and opacity may move`);
        }
      }
      for (const raw of stop.prelude.split(",")) {
        const key = { from: "0%", to: "100%" }[raw.trim()] ?? raw.trim();
        const merged = at.get(key) ?? new Map<string, string>();
        for (const [prop, value] of decls) if (ANIMATED.has(prop)) merged.set(prop, value);
        at.set(key, merged);
      }
    }
    const first = at.get("0%");
    const last = at.get("100%");
    if (!first || !last) {
      problems.push(`@keyframes ${name} needs both a 0% and a 100% keyframe`);
    } else {
      const show = (m: Map<string, string>) =>
        JSON.stringify(Array.from(m).sort(([a], [b]) => a.localeCompare(b)));
      if (show(first) !== show(last)) {
        problems.push(`@keyframes ${name}: 0% and 100% differ, so the loop jumps`);
      }
    }
  }

  // Each animated part: a named group of its own, turned about its own box.
  const animated: string[] = [];
  const durations = new Set<number>();
  for (const rule of rules) {
    const decls = declarations(rule.body);
    for (const prop of decls.keys()) {
      if (prop.startsWith("animation-")) {
        problems.push(`${rule.prelude} sets ${prop}; use the animation shorthand`);
      }
    }
    const animation = decls.get("animation");
    if (!animation || animation === "none") continue;
    const selectors = rule.prelude.split(",").map((s) => s.trim());
    for (const selector of selectors) {
      animated.push(selector);
      if (!/^#[A-Za-z][\w-]*$/.test(selector)) {
        problems.push(`${selector} is animated but is not one named group (#id)`);
        continue;
      }
      const element = doc.getElementById(selector.slice(1));
      if (!element) problems.push(`${selector} is animated but nothing has that id`);
      else if (element.localName !== "g") {
        problems.push(`${selector} is a <${element.localName}>; animate a named <g>`);
      } else if (element.hasAttribute("transform")) {
        problems.push(`${selector} has a transform attribute, which the animation would replace`);
      }
    }
    if (decls.get("transform-box") !== "fill-box") {
      problems.push(`${rule.prelude} needs transform-box: fill-box`);
    }
    if (!decls.has("transform-origin")) problems.push(`${rule.prelude} needs a transform-origin`);
    if (split(animation, /,/).length > 1) {
      problems.push(`${rule.prelude} runs two animations; nest a group for the second`);
      continue;
    }
    const tokens = split(animation, /\s/);
    const times = tokens.map(seconds).filter((t): t is number => t !== null);
    const duration = times[0];
    if (duration === undefined || duration < LOOP_SECONDS.min || duration > LOOP_SECONDS.max) {
      problems.push(
        `${rule.prelude} loops in ${duration ?? "no"} s; a loop is ${LOOP_SECONDS.min}-${LOOP_SECONDS.max} s`,
      );
    } else durations.add(duration);
    if (times.length > 1 && times[1] !== 0) {
      problems.push(`${rule.prelude} has a delay; put the offset in its keyframes`);
    }
    if (!tokens.includes("infinite")) problems.push(`${rule.prelude} does not loop (infinite)`);
    const names = tokens.filter(
      (t) => seconds(t) === null && !KEYWORDS.has(t) && !t.includes("(") && !/^\d/.test(t),
    );
    if (names.length !== 1 || !keyframes.has(names[0] ?? "")) {
      problems.push(`${rule.prelude} names no @keyframes this file defines`);
    }
  }
  if (animated.length === 0) problems.push("nothing in it is animated");
  if (durations.size > 1) {
    problems.push(`its loops differ in length (${[...durations].join(", ")} s), so they drift`);
  }

  // The still: every loop stopped. `!important`, because an id rule outranks
  // a `*` rule in the reduced-motion block and would keep running.
  if (!reducedRule) problems.push("it has no @media (prefers-reduced-motion: reduce) rule");
  else {
    const stopped = new Set<string>();
    for (const rule of stillRules) {
      if (/^none\s*!important$/.test(declarations(rule.body).get("animation") ?? "")) {
        for (const s of rule.prelude.split(",")) stopped.add(s.trim());
      }
    }
    for (const selector of animated) {
      if (!stopped.has("*") && !stopped.has(selector)) {
        problems.push(`reduced motion does not stop ${selector} (animation: none !important)`);
      }
    }
  }
  return problems;
}
