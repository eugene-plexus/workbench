/**
 * Where an answer's formulas are (markdownMath.ts): the delimiters a model
 * writes, read by the parser, and the money, code and addresses that are not
 * formulas.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { DOLLAR_REACH, remarkMath } from "./markdownMath";

type Found = { tex: string; display: boolean; open?: boolean; block?: boolean };

function parse(markdown: string): { formulas: Found[]; text: string; html: string } {
  const html = renderToStaticMarkup(
    createElement(
      ReactMarkdown,
      { remarkPlugins: [remarkGfm, remarkMath, remarkBreaks] },
      markdown,
    ),
  );
  const doc = new DOMParser().parseFromString(html, "text/html");
  const formulas: Found[] = [];
  for (const code of doc.querySelectorAll("code.language-math")) {
    const found: Found = {
      tex: code.textContent ?? "",
      display: code.classList.contains("math-display"),
    };
    if (code.parentElement?.tagName === "PRE") found.block = true;
    if (code.getAttribute("data-fence") === "open") found.open = true;
    formulas.push(found);
  }
  return { formulas, text: doc.body.textContent ?? "", html };
}

const formulas = (markdown: string) => parse(markdown).formulas;
const tex = (markdown: string) => formulas(markdown).map((f) => f.tex);

describe("inline formulas", () => {
  it("reads $x$ and \\(x\\) as inline", () => {
    expect(formulas("Energy $E = mc^2$ and \\(a^2 + b^2\\).")).toEqual([
      { tex: "E = mc^2", display: false },
      { tex: "a^2 + b^2", display: false },
    ]);
  });

  it("keeps a backslash's backslash: \\( is not read as an escaped bracket", () => {
    expect(tex("\\(\\frac{1}{2}\\)")).toEqual(["\\frac{1}{2}"]);
  });

  it("does not read Markdown inside a formula", () => {
    expect(tex("$a_1 * b_2 * c_3$ and $x_i$ and $y_i$")).toEqual(["a_1 * b_2 * c_3", "x_i", "y_i"]);
    expect(parse("$a_1 * b_2 * c_3$").html).not.toContain("<em>");
  });

  it("keeps an escaped dollar inside a formula", () => {
    expect(tex("$\\$5 + x$")).toEqual(["\\$5 + x"]);
  });

  it("reads a formula across a line break in its paragraph", () => {
    expect(tex("so $a +\nb$ holds")).toEqual(["a +\nb"]);
  });

  it("reads formulas in lists, tables, headings, emphasis and links", () => {
    const md = [
      "## The $\\alpha$ factor",
      "",
      "- item $x^2$",
      "",
      "| a | b |",
      "| - | - |",
      "| $\\pi$ | 2 |",
      "",
      "**bold $y$** and [link $z$](https://example.com)",
    ].join("\n");
    expect(tex(md)).toEqual(["\\alpha", "x^2", "\\pi", "y", "z"]);
  });

  it("reads Unicode", () => {
    expect(tex("$α + β = γ$ and $\\text{naïve}$")).toEqual(["α + β = γ", "\\text{naïve}"]);
  });
});

describe("money and other dollars stay text", () => {
  it.each([
    ["$5 and $10", "$5 and $10"],
    ["It costs $5.", "It costs $5."],
    ["between $5 and $10, or $20", "between $5 and $10, or $20"],
    ["from $10-$20 a month", "from $10-$20 a month"],
    ["5$/month and 10$/month", "5$/month and 10$/month"],
    ["The variables $x and $y", "The variables $x and $y"],
    ["$HOME and $PATH", "$HOME and $PATH"],
    ["a lone $ sign", "a lone $ sign"],
    ["an escaped \\$x\\$", "an escaped $x$"],
    ["$ x$ is no formula", "$ x$ is no formula"],
    ["$x $ is no formula", "$x $ is no formula"],
    ["$x$5 is no formula", "$x$5 is no formula"],
  ])("%s", (markdown, text) => {
    const found = parse(markdown);
    expect(found.formulas).toEqual([]);
    expect(found.text).toBe(text);
  });

  it("money before a formula stays money, and the formula is read", () => {
    expect(parse("price is $5, then $x$ later").formulas).toEqual([{ tex: "x", display: false }]);
    expect(parse("price is $5, then $x$ later").text).toBe("price is $5, then x later");
    expect(tex("text $5 and $x^2$")).toEqual(["x^2"]);
    // A formula may still begin with a digit.
    expect(tex("so $2x + 3 = 7$ gives $x = 2$")).toEqual(["2x + 3 = 7", "x = 2"]);
  });

  it("an unclosed formula while it is being written is the text it was", () => {
    for (const partial of ["$x + ", "$$x + ", "\\[x + ", "$$\\frac{1}{"]) {
      const found = parse(`Then ${partial}`);
      expect(found.formulas).toEqual([]);
      // The parser drops the backslash of `\[`, as it would without formulas.
      expect(found.text).toBe(`Then ${partial.replace("\\[", "[")}`.trim());
    }
  });

  it.each([
    ["\\(a ", 8_000],
    ["\\[a ", 8_000],
    ["\\(\\a", 8_000],
    ["$$ $ ", 8_000],
  ])("a paragraph of %j that never closes parses quickly", (piece, times) => {
    const started = performance.now();
    const found = parse(`Before $x$. ${piece.repeat(times)}`);
    const ms = performance.now() - started;
    // Each opener looked to its reach before (3 to 6 s here); now the parse
    // stops looking once it has spent its budget. A loaded CI runner took 1.7 s.
    expect(ms).toBeLessThan(2_500);
    expect(found.formulas.map((f) => f.tex)).toEqual(["x"]);
  });

  it("does not look past its reach for a close", () => {
    const far = `$x ${"a".repeat(DOLLAR_REACH)}$`;
    expect(formulas(far)).toEqual([]);
    expect(formulas(`$x ${"a".repeat(DOLLAR_REACH - 10)}$`)).toHaveLength(1);
  });
});

describe("code, addresses and escapes are never formulas", () => {
  it("inline code", () => {
    expect(formulas("Run `echo $HOME$` and `$x$`.")).toEqual([]);
  });

  it("a code fence", () => {
    expect(formulas("```bash\necho $x$ \\(y\\)\n```")).toEqual([]);
  });

  it("an indented code block", () => {
    expect(formulas("    $x$ \\(y\\)")).toEqual([]);
  });

  it("a link's address and an autolink", () => {
    expect(formulas("[a](https://example.com/$x$) <https://example.com/$y$>")).toEqual([]);
  });

  it("a bare address", () => {
    expect(formulas("see https://example.com/?a=$x$ there")).toEqual([]);
  });

  it("an escaped backslash before a bracket", () => {
    expect(formulas("\\\\(x\\\\)")).toEqual([]);
  });

  it("an empty formula", () => {
    expect(formulas("\\(\\) and $$ $$")).toEqual([]);
  });
});

describe("display formulas", () => {
  it("reads $$x$$ and \\[x\\] in a paragraph as display", () => {
    expect(formulas("Then $$x^2$$ and \\[y^2\\] follow.")).toEqual([
      { tex: "x^2", display: true },
      { tex: "y^2", display: true },
    ]);
  });

  it("reads a block of its own lines", () => {
    expect(formulas("Before.\n\n$$\nE = mc^2\n$$\n\nAfter.")).toEqual([
      { tex: "E = mc^2", display: true, block: true },
    ]);
    expect(formulas("\\[\n\\int_0^1 x\\,dx\n\\]")).toEqual([
      { tex: "\\int_0^1 x\\,dx", display: true, block: true },
    ]);
  });

  it("keeps lines a paragraph would end inside a block", () => {
    const md = "$$\n\\begin{aligned}\na &= b\n+ c \\\\\n- d &= e\n# f\n\\end{aligned}\n$$";
    expect(formulas(md)).toEqual([
      {
        tex: "\\begin{aligned}\na &= b\n+ c \\\\\n- d &= e\n# f\n\\end{aligned}",
        display: true,
        block: true,
      },
    ]);
  });

  it("closes a block on a line that ends with its delimiter", () => {
    expect(formulas("$$\n\\begin{aligned} a &= b \\end{aligned}$$\n\nAfter.")).toEqual([
      { tex: "\\begin{aligned} a &= b \\end{aligned}", display: true, block: true },
    ]);
    expect(formulas("\\[\nx = 1 \\]\n\nAfter.")).toEqual([
      { tex: "x = 1", display: true, block: true },
    ]);
  });

  it("a backslash pair before a bracket does not close a block", () => {
    expect(formulas("\\[\na \\\\]\nb\n\\]")).toEqual([
      { tex: "a \\\\]\nb", display: true, block: true },
    ]);
  });

  it("marks a block that has not closed as open", () => {
    expect(formulas("$$\n\\frac{1}{2")).toEqual([
      { tex: "\\frac{1}{2", display: true, block: true, open: true },
    ]);
    expect(formulas("$$")).toEqual([{ tex: "", display: true, block: true, open: true }]);
  });

  it("a paragraph's own $$ pair is the formula, and no block swallows what follows", () => {
    const found = parse("The total is $$\nx + y\n$$\n\nAfter the formula.");
    expect(found.formulas).toEqual([{ tex: "x + y", display: true }]);
    expect(found.text).toContain("After the formula.");
  });

  it("a block in a list item and a quote", () => {
    expect(formulas("1. Step:\n\n   $$\n   x\n   $$\n\n> $$\n> y\n> $$")).toEqual([
      { tex: "x", display: true, block: true },
      { tex: "y", display: true, block: true },
    ]);
  });

  it("a ```math fence is a display formula", () => {
    const html = parse("```math\nx^2\n```").html;
    expect(html).toContain('<code class="language-math">x^2\n</code>');
  });
});

describe("footnotes still parse beside formulas", () => {
  it("a reference and its note", () => {
    const found = parse("A claim $x$.[^1]\n\n[^1]: The note $y$.");
    expect(found.formulas.map((f) => f.tex)).toEqual(["x", "y"]);
    expect(found.html).toContain("data-footnote-ref");
  });
});
