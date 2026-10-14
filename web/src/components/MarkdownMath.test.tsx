/**
 * Formulas in an answer (rich-content RC1): drawn where the model wrote them,
 * laid out once, and a formula that cannot be shown stays its source and says
 * why, without touching the answer around it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { KEPT_BYTES, keptSize, setMathEngine, type Engine } from "../lib/mathClient";
import { typeset } from "../lib/mathTypeset";
import { Markdown } from "./Markdown";

/** The page's engine, counting what it is asked to lay out. */
function counting(): { engine: Engine; asked: string[] } {
  const asked: string[] = [];
  return {
    asked,
    engine: {
      async typeset(tex, display) {
        asked.push(tex);
        return typeset(tex, display);
      },
    },
  };
}

afterEach(() => {
  setMathEngine(counting().engine);
});

it("draws inline and display formulas, with MathML and their source", async () => {
  const { container } = render(
    <Markdown text={"Energy $E = mc^2$, and\n\n$$\n\\int_0^1 x\\,dx = \\tfrac{1}{2}\n$$"} />,
  );
  await waitFor(() => expect(container.querySelectorAll(".katex")).toHaveLength(2));
  const [inline, display] = container.querySelectorAll("annotation");
  expect(inline?.textContent).toBe("E = mc^2");
  expect(display?.textContent).toBe("\\int_0^1 x\\,dx = \\tfrac{1}{2}");
  expect(container.querySelector(".math-inline")).toHaveTextContent(/E/);
  // The display formula is its own block, which a keyboard can scroll.
  const block = container.querySelector(".math-block .math-display");
  expect(block).toHaveAttribute("tabindex", "0");
  expect(block?.querySelector(".katex-display")).not.toBeNull();
});

it("shows a formula's source until it is laid out", async () => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  setMathEngine({
    async typeset(tex, display) {
      await held;
      return typeset(tex, display);
    },
  });
  const { container } = render(<Markdown text="So $x^2$ holds." />);
  const pending = container.querySelector(".math-pending");
  expect(pending).toHaveTextContent("x^2");
  release();
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
  expect(container.querySelector(".math-pending")).toBeNull();
});

it("lays out each formula once while the answer is written", async () => {
  const { engine, asked } = counting();
  setMathEngine(engine);
  const text = "First $a^2$ then $$b^2$$ and";
  const { rerender, container } = render(<Markdown text={text} />);
  for (const more of [" more", " more words", " more words $c", " more words $c$."]) {
    rerender(<Markdown text={text + more} />);
  }
  await waitFor(() => expect(container.querySelectorAll(".katex")).toHaveLength(3));
  rerender(<Markdown text={`${text} more words $c$. And a sentence after.`} />);
  expect(asked).toEqual(["a^2", "b^2", "c"]);
});

it("lays out a formula shown twice, and again in a later answer, once", async () => {
  const { engine, asked } = counting();
  setMathEngine(engine);
  const { container, rerender } = render(<Markdown text="$a$ and $a$" />);
  await waitFor(() => expect(container.querySelectorAll(".katex")).toHaveLength(2));
  rerender(
    <>
      <Markdown text="$a$ and $a$" />
      <Markdown text="Again, $a$." />
    </>,
  );
  await waitFor(() => expect(container.querySelectorAll(".katex")).toHaveLength(3));
  expect(asked).toEqual(["a"]);
});

it("shows a block that has not closed as its source, and draws it once it closes", async () => {
  const { rerender, container } = render(<Markdown text={"$$\n\\frac{1}{2"} />);
  expect(container.querySelector(".katex")).toBeNull();
  expect(container.querySelector("pre code")).toHaveTextContent("\\frac{1}{2");
  rerender(<Markdown text={"$$\n\\frac{1}{2}\n$$"} />);
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
  expect(container.querySelector("pre")).toBeNull();
});

it("draws a ```math fence as a display formula", async () => {
  const { container } = render(<Markdown text={"```math\nx^2 + y^2\n```"} />);
  await waitFor(() => expect(container.querySelector(".katex-display")).not.toBeNull());
  expect(container.querySelector("annotation")?.textContent).toBe("x^2 + y^2");
});

it("keeps a bad inline formula as its source, saying why", async () => {
  render(<Markdown text={"Before $\\foo{x}$ after, and $y$."} />);
  const failed = await screen.findByText("\\foo{x}", { selector: "code.math-failed" });
  expect(failed).toHaveAttribute("title", "Formula not shown: Undefined control sequence: \\foo");
  expect(failed).toHaveTextContent("(formula not shown: Undefined control sequence: \\foo)");
  expect(screen.getByText(/Before/)).toHaveTextContent("after, and");
  await waitFor(() => expect(document.querySelector(".katex")).not.toBeNull());
});

it("keeps a bad block formula as a block of its source, with Copy and the reason", async () => {
  const { container } = render(<Markdown text={"$$\n\\frac{1}{\n$$\n\nAfter."} />);
  await waitFor(() => expect(container.querySelector(".code-note")).not.toBeNull());
  expect(container.querySelector(".code-lang")).toHaveTextContent("math");
  expect(container.querySelector("pre code")).toHaveTextContent("\\frac{1}{");
  expect(container.querySelector(".code-note")).toHaveTextContent(/^Formula not shown: /);
  expect(screen.getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  expect(screen.getByText("After.")).toBeInTheDocument();
});

it("says why a formula too long to lay out is not shown", async () => {
  const long = "x".repeat(8_001);
  render(<Markdown text={`$$${long}$$`} />);
  const failed = await screen.findByText(long, { selector: "code.math-failed" });
  expect(failed).toHaveAttribute("title", "Formula not shown: it is longer than 8,000 characters");
});

it("keeps one formula's macro out of the next answer", async () => {
  const { container } = render(
    <>
      <Markdown text={"$\\gdef\\shared{X}\\shared$"} />
      <Markdown text={"$\\shared$"} />
    </>,
  );
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
  expect(await screen.findByText("\\shared", { selector: "code.math-failed" })).toHaveAttribute(
    "title",
    "Formula not shown: Undefined control sequence: \\shared",
  );
});

it("tries a formula again when it is shown again after the worker failed, and not after its own error", async () => {
  const asked: string[] = [];
  let worker = "broken";
  setMathEngine({
    async typeset(tex, display) {
      asked.push(tex);
      if (worker === "broken") return { error: "the formula worker stopped: gone", again: true };
      return typeset(tex, display);
    },
  });
  const first = render(<Markdown text={"So $x$ and $\\foo$."} />);
  expect(await screen.findByText("x", { selector: "code.math-failed" })).toHaveAttribute(
    "title",
    "Formula not shown: the formula worker stopped: gone",
  );
  first.unmount();
  worker = "fine";
  const { container } = render(<Markdown text={"So $x$ and $\\foo$."} />);
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
  await screen.findByText("\\foo", { selector: "code.math-failed" });
  expect(asked).toEqual(["x", "\\foo", "x", "\\foo"]);
  // Its own error is final: shown a third time, it is not asked for again.
  render(<Markdown text={"Again $\\foo$."} />);
  expect(asked).toHaveLength(4);
});

it("keeps no more laid-out markup than its budget", async () => {
  const big = `<span>${"x".repeat(KEPT_BYTES / 2)}</span>`;
  setMathEngine({
    async typeset() {
      return { markup: big };
    },
  });
  for (const tex of ["a", "b", "c"]) {
    const shown = render(<Markdown text={`$${tex}$`} />);
    await waitFor(() => expect(shown.container.querySelector(".math-pending")).toBeNull());
    shown.unmount();
  }
  expect(keptSize().bytes).toBeLessThanOrEqual(KEPT_BYTES);
  expect(keptSize().count).toBe(1);
});

it("leaves formulas as their source when none can be laid out", async () => {
  setMathEngine(null);
  render(<Markdown text="So $x$." />);
  expect(await screen.findByText("x", { selector: "code.math-failed" })).toHaveAttribute(
    "title",
    "Formula not shown: this browser cannot lay out formulas",
  );
});

it("keeps money, code and an escaped dollar as they were written", () => {
  const { container } = render(
    <Markdown text={"It costs $5 and $10, or \\$20.\n\n`echo $x$`\n\n```bash\necho $HOME$\n```"} />,
  );
  expect(container.querySelector(".math-inline, .math-display, .math-failed")).toBeNull();
  expect(container).toHaveTextContent("It costs $5 and $10, or $20.");
  expect(container.querySelector(":not(pre) > code")).toHaveTextContent("echo $x$");
});

it("still renders raw HTML in a formula's neighbourhood as nothing that runs", async () => {
  const { container } = render(
    <Markdown text={'$x$ <img src=x onerror="alert(1)"> <script>alert(1)</script>'} />,
  );
  await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
  expect(container.querySelector("img, script")).toBeNull();
});
