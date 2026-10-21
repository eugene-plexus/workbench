/**
 * Footnotes (rich-content RC1): a reference moves focus to its note and the
 * note's way back returns it, inside the same answer, without touching the
 * address; two answers with a `[^1]` keep their own.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Markdown } from "./Markdown";

const NOTED = "A claim.[^1] Another.[^2]\n\n[^1]: The first note.\n[^2]: The second note.";

function regions(container: HTMLElement): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>("[data-md-scope]")];
}

it("gives each answer its own ids, and every reference a target in its own answer", () => {
  const { container } = render(
    <>
      <Markdown text={NOTED} />
      <Markdown text={NOTED} quiet />
      <Markdown text={"A third.[^1]\n\n[^1]: Its note."} />
    </>,
  );
  const ids = [...container.querySelectorAll("[id]")].map((el) => el.id);
  expect(ids.length).toBeGreaterThan(9);
  expect(new Set(ids).size).toBe(ids.length);
  for (const region of regions(container)) {
    const scope = region.dataset.mdScope ?? "";
    for (const link of region.querySelectorAll("a[href^='#']")) {
      const target = link.getAttribute("href")?.slice(1) ?? "";
      expect(target.startsWith(scope)).toBe(true);
      expect(region.querySelector(`[id="${target}"]`)).not.toBeNull();
    }
    // The heading that names the notes, and what points at it, are the answer's own.
    const label = region.querySelector('[data-footnotes] > h4[data-md="h2"]');
    expect(label?.id).toBe(`${scope}footnote-label`);
    expect(label).toHaveClass("sr-only");
    for (const ref of region.querySelectorAll("[data-footnote-ref]")) {
      expect(ref).toHaveAttribute("aria-describedby", `${scope}footnote-label`);
    }
  }
});

it("moves focus to the note and back, and leaves the address alone", () => {
  const before = window.location.href;
  const { container } = render(
    <>
      <Markdown text={NOTED} />
      <Markdown text={NOTED} />
    </>,
  );
  const second = regions(container)[1] as HTMLElement;
  const [ref] = within(second).getAllByRole("link", { name: "1" });
  fireEvent.click(ref as HTMLElement);
  const note = second.querySelector<HTMLElement>("[data-footnotes] li");
  expect(document.activeElement).toBe(note);
  expect(note).toHaveAttribute("tabindex", "-1");
  expect(note).toHaveTextContent("The first note.");
  const back = within(note as HTMLElement).getByRole("link", { name: "Back to reference 1" });
  fireEvent.click(back);
  expect(document.activeElement).toBe(ref);
  expect(window.location.href).toBe(before);
});

it("works from the keyboard", async () => {
  const user = userEvent.setup();
  render(<Markdown text={NOTED} />);
  const ref = screen.getByRole("link", { name: "2" });
  ref.focus();
  await user.keyboard("{Enter}");
  expect(document.activeElement).toHaveTextContent("The second note.");
  await user.tab();
  expect(document.activeElement).toHaveAccessibleName("Back to reference 2");
  await user.keyboard("{Enter}");
  expect(document.activeElement).toBe(ref);
});

it("finds the note inside its own answer, whatever else on the page has its id", () => {
  const decoy = document.createElement("button");
  document.body.prepend(decoy);
  try {
    const { container } = render(<Markdown text={NOTED} />);
    const scope = regions(container)[0]?.dataset.mdScope ?? "";
    decoy.id = `${scope}fn-1`;
    fireEvent.click(screen.getByRole("link", { name: "1" }));
    expect(document.activeElement).not.toBe(decoy);
    expect(document.activeElement).toHaveTextContent("The first note.");
  } finally {
    decoy.remove();
  }
});

it("keeps a model's own link to an id inert, as before", () => {
  const { container } = render(<Markdown text={NOTED} />);
  const scope = regions(container)[0]?.dataset.mdScope ?? "";
  const { container: forged } = render(
    <Markdown text={`[jump](#${scope}fn-1) and [top](#main) and [js](javascript:alert(1))`} />,
  );
  expect(forged.querySelector("a")).toBeNull();
  expect(forged).toHaveTextContent("jump and top and js");
});
