/**
 * What the control gates share (memoryControls.test.tsx, documentsControls.test.tsx):
 * every control on a screen has a name a screen reader says and a `data-testid`
 * a check can find it by. Each screen is drawn in each state it has, and every
 * control on it is held to both: a control added later with neither fails there,
 * by name, so nobody has to remember to look. Read only by tests.
 */

import { getRoles, within } from "@testing-library/react";

/** What a person can press, type into or choose. */
export const CONTROLS = [
  "button",
  "a[href]",
  'input:not([type="hidden"])',
  "select",
  "textarea",
  "summary",
  '[role="switch"]',
  '[role="button"]',
  '[role="checkbox"]',
  '[role="radio"]',
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/** The controls in `root`, and `root` itself when it is one. */
export const controlsIn = (root: Element): HTMLElement[] => [
  ...(root.matches(CONTROLS) ? [root as HTMLElement] : []),
  ...root.querySelectorAll<HTMLElement>(CONTROLS),
];

function look(el: Element): string {
  const id = el.getAttribute("data-testid");
  const words = (el.getAttribute("aria-label") ?? el.textContent ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, 40);
  return `<${el.tagName.toLowerCase()}${id ? ` data-testid="${id}"` : ""}> "${words}"`;
}

/** The roles in `root`, hidden things included (the typings leave that option out). */
const rolesOf = getRoles as (
  root: HTMLElement,
  options: { hidden: boolean },
) => Record<string, HTMLElement[]>;

/** What each of `controls` lacks: a name (as the browser computes one) and a test id. */
export function lacking(controls: HTMLElement[], scope: HTMLElement): string[] {
  const named = new Set<Element>();
  for (const role of Object.keys(rolesOf(scope, { hidden: true }))) {
    for (const el of within(scope).queryAllByRole(role, { name: /\S/, hidden: true })) {
      named.add(el);
    }
  }
  // The role table the queries use gives a `summary` no role, and a browser names it
  // from what is inside it. A file input has no role at all, and an `aria-label` names
  // any element: the name a browser computes.
  const nameless = (el: HTMLElement) =>
    el.getAttribute("aria-label")?.trim()
      ? false
      : el.tagName === "SUMMARY"
        ? (el.textContent ?? "").trim() === ""
        : !named.has(el);
  return controls.flatMap((el) => {
    const needs = [
      ...(nameless(el) ? ["accessible name"] : []),
      ...(el.hasAttribute("data-testid") ? [] : ["data-testid"]),
    ];
    return needs.length > 0 ? [`${look(el)} has no ${needs.join(" and no ")}`] : [];
  });
}

/** Everything wrong with every control on what is drawn: nothing, when it is right. */
export const wrong = (view: { container: HTMLElement }) =>
  lacking(controlsIn(view.container), view.container);

/** What a screen drawn in some state must be: at least `atLeast` controls on it (a
 * gate that finds none has checked nothing), and every one of them with a name and a
 * test id. A control that is added later with neither fails here, by name. */
export function everyControlIsNamed(view: { container: HTMLElement }, atLeast = 1): void {
  const found = controlsIn(view.container);
  expect(found.length, "controls on the screen").toBeGreaterThanOrEqual(atLeast);
  const problems = lacking(found, view.container);
  // The message names each control, so a failure says which one.
  expect(problems, problems.join("; ")).toEqual([]);
}
