import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { HIGHLIGHT_MAX_CHARS, HIGHLIGHT_MAX_LINES } from "../lib/highlight";
import { fenceIsOpen } from "../lib/markdownPlugins";
import { Markdown, safeHref } from "./Markdown";

it("answer_headings_nest_under_the_message", () => {
  render(
    <Markdown
      text={"# First\n\n## Second\n\n### Third\n\n#### Fourth\n\n##### Fifth\n\n###### Sixth"}
    />,
  );
  const headings = screen.getAllByRole("heading");
  expect(headings.map((heading) => heading.tagName)).toEqual(["H3", "H4", "H5", "H6", "H6", "H6"]);
  expect(headings.map((heading) => heading.getAttribute("data-md"))).toEqual([
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
  ]);
});

it("kana block gets lang ja", () => {
  const { container } = render(
    <Markdown
      text={
        "# 日本の見出し\n\n**かな**と漢字\n\n- カナ\n\n> 日本語です\n\n| 名前 |\n| --- |\n| ひらがな |"
      }
    />,
  );
  for (const selector of ["h3", "p", "li", "ul", "blockquote", "table", "td"]) {
    expect(container.querySelector(selector), selector).toHaveAttribute("lang", "ja");
  }
});

it("chooses Korean and the viewer's Han font, leaving Latin text alone", () => {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US", "ja-JP", "zh-TW"]);
  const { container } = render(<Markdown text={"한국어\n\n漢字\n\nEnglish"} />);
  expect(Array.from(container.querySelectorAll("p")).map((p) => p.lang)).toEqual([
    "ko",
    "ja-JP",
    "",
  ]);
  vi.restoreAllMocks();
});

describe("an answer is untrusted (W5)", () => {
  it("shows an image an answer names as a link, never fetching it", () => {
    const { container } = render(
      <Markdown text="Look: ![the chat](https://attacker.example/x.png?q=secret)" />,
    );
    expect(container.querySelector("img")).toBeNull();
    const link = container.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("https://attacker.example/x.png?q=secret");
    expect(link.textContent).toBe("[Image: the chat]");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("renders raw HTML in an answer as nothing that runs", () => {
    const { container } = render(
      <Markdown text={'Hi <img src=x onerror="alert(1)"> <script>alert(2)</script> <b>bold</b>'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.innerHTML).not.toContain("onerror");
  });

  it("makes only web and mail addresses into links", () => {
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref(" JavaScript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,<b>")).toBeNull();
    expect(safeHref("https://example.org")).toBe("https://example.org");
    expect(safeHref("mailto:a@example.org")).toBe("mailto:a@example.org");
    const { container } = render(<Markdown text="[click](javascript:alert(1))" />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("click");
  });

  it("opens every link in a new tab with no opener", () => {
    const { container } = render(<Markdown text="See [the docs](https://example.org/docs)." />);
    const link = container.querySelector("a")!;
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("gives a code block a copy button", () => {
    const { getByRole } = render(<Markdown text={"```py\nprint(1)\n```"} />);
    expect(getByRole("button", { name: "Copy code" })).toBeInTheDocument();
  });
});

describe("a code block", () => {
  it("says which language the fence names, above its code, beside one Copy button", () => {
    const { container } = render(<Markdown text={"```py\nprint(1)\n```"} />);
    const block = container.querySelector(".code-block")!;
    expect(block.querySelector(".code-head .code-lang")?.textContent).toBe("py");
    expect(screen.getAllByRole("button", { name: "Copy code" })).toHaveLength(1);
    // One box: the header and the code are siblings in it, the header first.
    expect(Array.from(block.children).map((el) => el.className || el.tagName)).toEqual([
      "code-head",
      "PRE",
    ]);
  });

  it("is a named scroller a keyboard can reach, so code wider than a phone can be read", () => {
    const { container } = render(<Markdown text={"```py\nprint(1)\n```\n\n```\nplain\n```"} />);
    const blocks = Array.from(container.querySelectorAll("pre"));
    expect(blocks.map((pre) => pre.getAttribute("tabindex"))).toEqual(["0", "0"]);
    expect(blocks.map((pre) => pre.getAttribute("aria-label"))).toEqual(["py block", "Code block"]);
  });

  it("says the language as it was written", () => {
    const { container } = render(<Markdown text={"```c++\nint main() {}\n```"} />);
    expect(container.querySelector(".code-lang")?.textContent).toBe("c++");
  });

  it("says code when the fence names no language", () => {
    const { container } = render(<Markdown text={"```\nplain\n```"} />);
    expect(container.querySelector(".code-lang")?.textContent).toBe("code");
  });
});

describe("a table", () => {
  const TABLE = "| Setting | Value |\n| --- | ---: |\n| Layers | 62 |";

  it("sits in its own scrolling box and is still a table", () => {
    const { container } = render(<Markdown text={TABLE} />);
    const table = screen.getByRole("table");
    expect(table.parentElement).toBe(container.querySelector(".table-wrap"));
  });

  it("keeps a column's alignment, which react-markdown writes as an inline style", () => {
    render(<Markdown text={TABLE} />);
    expect(screen.getByRole("columnheader", { name: "Value" }).style.textAlign).toBe("right");
    expect(screen.getByRole("cell", { name: "62" }).style.textAlign).toBe("right");
    // The stylesheet aligns the header nobody aligned; no style is written for it.
    expect(screen.getByRole("columnheader", { name: "Setting" }).getAttribute("style")).toBeNull();
  });
});

describe("a bold lead-in", () => {
  it("is a short bold label that ends in a colon", () => {
    const { container } = render(
      <Markdown text="**Why:** x. **Note：** y. **plain** z. **Why**: w." />,
    );
    const marked = Array.from(container.querySelectorAll("strong")).map((el) => [
      el.textContent,
      el.className,
    ]);
    expect(marked).toEqual([
      ["Why:", "lead-in"],
      ["Note：", "lead-in"],
      ["plain", ""],
      ["Why", ""],
    ]);
  });

  it("is not a sentence that happens to end in a colon", () => {
    const long = "A bold sentence that is much too long to be a label for anything:";
    const { container } = render(<Markdown text={`**${long}** x`} />);
    expect(container.querySelector("strong")?.className).toBe("");
  });
});

describe("a task list", () => {
  const TASKS = "- [x] Weights at Q4_K_M\n- [ ] Try Q5_K_M";

  it("draws its boxes and says done or not done in words", () => {
    const { container } = render(<Markdown text={TASKS} />);
    expect(container.querySelector("input")).toBeNull();
    const items = Array.from(container.querySelectorAll("li")).map((li) =>
      li.textContent?.replace(/\s+/g, " ").trim(),
    );
    expect(items).toEqual(["Done: Weights at Q4_K_M", "Not done: Try Q5_K_M"]);
    expect(container.querySelectorAll('.task-box[data-checked="true"]')).toHaveLength(1);
    expect(container.querySelectorAll('.task-box[data-checked="false"]')).toHaveLength(1);
    // The drawn box says nothing: its words are the screen reader's.
    for (const box of container.querySelectorAll(".task-box")) {
      expect(box.getAttribute("aria-hidden")).toBe("true");
    }
  });
});

describe("a draft", () => {
  it("is quiet, and an answer is not", () => {
    const { container, rerender } = render(<Markdown text="x" />);
    expect(container.firstElementChild?.className).toBe("answer");
    rerender(<Markdown text="x" quiet />);
    expect(container.firstElementChild?.className).toBe("answer answer-quiet");
  });
});

describe("the HTML a model really writes (6.4)", () => {
  it("reads <br> as a line break, in a table cell too", () => {
    const { container } = render(
      <Markdown text={"| a | b |\n| - | - |\n| one<br>two | x<br/>y <br /> z |"} />,
    );
    const cells = Array.from(container.querySelectorAll("td"));
    expect(cells.map((td) => td.querySelectorAll("br").length)).toEqual([1, 2]);
    expect(cells[0]?.textContent?.replace(/\s+/g, "")).toBe("onetwo");
  });

  it("reads sub, sup and kbd, and nothing about how they look comes from the answer", () => {
    const { container } = render(
      <Markdown text="H<sub>2</sub>O, E=mc<sup>2</sup>, press <kbd>Ctrl</kbd>+<kbd>C</kbd>." />,
    );
    expect(container.querySelector("sub")?.textContent).toBe("2");
    expect(container.querySelector("sup")?.textContent).toBe("2");
    expect(Array.from(container.querySelectorAll("kbd")).map((k) => k.textContent)).toEqual([
      "Ctrl",
      "C",
    ]);
    expect(container.querySelector("p")?.textContent).toBe("H2O, E=mc2, press Ctrl+C.");
  });

  it("copies no attribute, and every other tag still renders as nothing that runs", () => {
    const { container } = render(
      <Markdown
        text={[
          '<br class="x"> <sub onclick="alert(1)">2</sub> <kbd style="color:red">K</kbd>',
          '<img src=x onerror="alert(2)"> <script>alert(3)</script> <b>bold</b> <br>',
        ].join("\n\n")}
      />,
    );
    for (const tag of ["sub", "kbd", "img", "script", "b"]) {
      expect(container.querySelector(tag), tag).toBeNull();
    }
    // Only the bare <br> at the end of the second paragraph is a break.
    expect(container.querySelectorAll("br")).toHaveLength(1);
    // Inside the answer's own box, which has a class of its own.
    const answer = container.querySelector(".answer")!;
    for (const attribute of ["class=", "onclick", "onerror", "style="]) {
      expect(answer.innerHTML).not.toContain(attribute);
    }
    // What was between the tags was written by the model, and is still read.
    expect(container.textContent).toContain("2");
    expect(container.textContent).toContain("K");
    expect(container.textContent).toContain("bold");
  });

  it("shows what an opening tag with no end holds, as plain text", () => {
    const { container } = render(<Markdown text="H<sub>2 and then more, and x</kbd>y" />);
    expect(container.querySelector("sub")).toBeNull();
    expect(container.querySelector("kbd")).toBeNull();
    expect(container.querySelector("p")?.textContent).toBe("H2 and then more, and xy");
  });

  it("does not make a second break of the line end after a <br>", () => {
    const { container } = render(<Markdown text={"one<br>\ntwo"} />);
    expect(container.querySelectorAll("br")).toHaveLength(1);
  });
});

describe("a single newline (6.4)", () => {
  it("is a line break, as the person's own messages show it", () => {
    const { container } = render(<Markdown text={"Name: Alice\nAge: 30\nCity: Paris"} />);
    const paragraph = container.querySelector("p")!;
    expect(paragraph.querySelectorAll("br")).toHaveLength(2);
    expect(paragraph.textContent?.replace(/\s+/g, " ")).toBe("Name: Alice Age: 30 City: Paris");
  });

  it("leaves a blank line a paragraph break", () => {
    const { container } = render(<Markdown text={"one\n\ntwo"} />);
    expect(container.querySelectorAll("p")).toHaveLength(2);
    expect(container.querySelectorAll("br")).toHaveLength(0);
  });

  it("does not touch code", () => {
    const { container } = render(<Markdown text={"```\na\nb\n```\n\n`x`"} />);
    expect(container.querySelectorAll("br")).toHaveLength(0);
    expect(container.querySelector("pre code")?.textContent).toBe("a\nb\n");
  });
});

describe("GitHub alerts (6.4)", () => {
  it.each([
    ["NOTE", "note", "Note"],
    ["TIP", "tip", "Tip"],
    ["IMPORTANT", "important", "Important"],
    ["WARNING", "warning", "Warning"],
    ["CAUTION", "caution", "Caution"],
  ])("[!%s] is a %s alert, and the marker is not in the words", (marker, kind, label) => {
    const { container } = render(<Markdown text={`> [!${marker}]\n> Mind the \`cache\`.`} />);
    const quote = container.querySelector("blockquote")!;
    expect(quote.className).toBe(`alert alert-${kind}`);
    expect(quote.getAttribute("data-alert")).toBe(label);
    expect(quote.textContent?.trim()).toBe("Mind the cache.");
  });

  it("reads the marker in any case, and drops a paragraph that held only the marker", () => {
    const { container } = render(<Markdown text={"> [!warning]\n>\n> Second paragraph."} />);
    const quote = container.querySelector("blockquote")!;
    expect(quote.getAttribute("data-alert")).toBe("Warning");
    expect(Array.from(quote.querySelectorAll("p")).map((p) => p.textContent)).toEqual([
      "Second paragraph.",
    ]);
  });

  it("leaves alone a quote that only looks like one", () => {
    for (const text of [
      "> [!TIP] on the same line",
      "> [!UNKNOWN]\n> body",
      "> body [!NOTE]\n> more",
      "> a plain quote",
    ]) {
      const { container, unmount } = render(<Markdown text={text} />);
      const quote = container.querySelector("blockquote")!;
      expect(quote.className, text).toBe("");
      expect(quote.hasAttribute("data-alert"), text).toBe(false);
      unmount();
    }
  });
});

describe("syntax colour (6.4)", () => {
  const spans = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("pre code span")).map((el) => el.className);

  it("gives a fence that names a language classed spans, and only classes", () => {
    const { container } = render(
      <Markdown text={'```python\ndef f():\n    return "x"  # note\n```'} />,
    );
    const code = container.querySelector("pre code")!;
    expect(code.className).toContain("language-python");
    expect(spans(container)).toEqual(
      expect.arrayContaining(["hljs-keyword", "hljs-string", "hljs-comment"]),
    );
    for (const span of code.querySelectorAll("span")) {
      // No style attribute: the page's policy would refuse it, and it would not follow the theme.
      expect(span.getAttributeNames()).toEqual(["class"]);
    }
  });

  it("never guesses the language of a fence that names none", () => {
    const { container } = render(<Markdown text={"```\ndef f():\n    return 1\n```"} />);
    expect(spans(container)).toEqual([]);
    expect(container.querySelector("pre code")?.className).toBe("");
    expect(container.querySelector(".code-lang")?.textContent).toBe("code");
  });

  it("waits for a fence to close: a block being written is plain, then coloured", () => {
    const open = '```python\ndef f():\n    return "x"  # note';
    const { container, rerender } = render(<Markdown text={`Here:\n\n${open}`} />);
    expect(spans(container)).toEqual([]);
    expect(container.querySelector("pre code")?.textContent).toBe(
      'def f():\n    return "x"  # note\n',
    );
    // Half a closing fence is not one.
    rerender(<Markdown text={`Here:\n\n${open}\n\`\``} />);
    expect(spans(container)).toEqual([]);
    rerender(<Markdown text={`Here:\n\n${open}\n\`\`\``} />);
    expect(spans(container)).toEqual(expect.arrayContaining(["hljs-keyword", "hljs-comment"]));
  });

  it("waits for a fence in a quote or a list to close as well", () => {
    for (const [open, close] of [
      ['> ```bash\n> echo "x" # c', "\n> ```"],
      ['- item\n\n  ```bash\n  echo "x" # c', "\n  ```"],
    ] as const) {
      const writing = render(<Markdown text={open} />);
      expect(spans(writing.container), open).toEqual([]);
      writing.unmount();
      const done = render(<Markdown text={open + close} />);
      expect(spans(done.container), open + close).toContain("hljs-comment");
      done.unmount();
    }
  });

  it("shows a block at the line cap coloured and one line past it plain", () => {
    const block = (lines: number) => `\`\`\`python\n${"x = 1\n".repeat(lines)}\`\`\``;
    const atCap = render(<Markdown text={block(HIGHLIGHT_MAX_LINES)} />);
    expect(spans(atCap.container).length).toBe(HIGHLIGHT_MAX_LINES);
    atCap.unmount();
    const past = render(<Markdown text={block(HIGHLIGHT_MAX_LINES + 1)} />);
    expect(spans(past.container)).toEqual([]);
    expect(past.container.querySelector(".code-lang")?.textContent).toBe("python");
  });

  it("shows a block at the size cap coloured and one past it plain, as written", () => {
    const line = (chars: number) => `s = "${"a".repeat(chars)}"`;
    // The code's text ends with a line end, which counts; "s = " and the quotes are six more.
    const fits = line(HIGHLIGHT_MAX_CHARS - 7);
    const small = render(<Markdown text={`\`\`\`python\n${fits}\n\`\`\``} />);
    expect(spans(small.container)).toContain("hljs-string");
    small.unmount();
    const source = line(HIGHLIGHT_MAX_CHARS);
    const { container } = render(<Markdown text={`\`\`\`python\n${source}\n\`\`\``} />);
    expect(spans(container)).toEqual([]);
    expect(container.querySelector("pre code")?.textContent).toBe(`${source}\n`);
  });

  it.each(["text", "txt", "log", "output"])("leaves a %s fence as it is", (name) => {
    const { container } = render(<Markdown text={`\`\`\`${name}\ndef f(): return "x"\n\`\`\``} />);
    expect(spans(container)).toEqual([]);
    // Not even tried: the highlighter marks a block it has looked at.
    expect(container.querySelector("pre code")?.className).not.toContain("hljs");
    expect(container.querySelector(".code-lang")?.textContent).toBe(name);
  });

  it("knows PowerShell and Dockerfile, which the common set leaves out", () => {
    const power = render(
      <Markdown text={"```powershell\nGet-ChildItem -Recurse | Select Name\n```"} />,
    );
    expect(spans(power.container).length).toBeGreaterThan(0);
    power.unmount();
    const docker = render(
      <Markdown text={"```dockerfile\nFROM python:3.12\nRUN pip install x\n```"} />,
    );
    expect(spans(docker.container)).toContain("hljs-keyword");
  });

  it("shows a language it does not know as written, with its label", () => {
    const { container } = render(<Markdown text={"```frobnicate\nx := 1\n```"} />);
    expect(spans(container)).toEqual([]);
    expect(container.querySelector(".code-lang")?.textContent).toBe("frobnicate");
    expect(container.querySelector("pre code")?.textContent).toBe("x := 1\n");
  });

  it("turns code that looks like markup into text, never into markup", () => {
    const source = '<script>alert(1)</script><img src=x onerror="alert(2)">';
    const { container } = render(<Markdown text={`\`\`\`html\n${source}\n\`\`\``} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("pre code")?.textContent).toBe(`${source}\n`);
    expect(spans(container).length).toBeGreaterThan(0);
  });

  describe("Copy", () => {
    const writeText = vi.fn(async () => undefined);
    beforeEach(() => {
      writeText.mockClear();
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    });

    it("copies the source of a coloured fence, exactly as it does an uncoloured one", async () => {
      const code = 'echo "done" # a comment\nls -la';
      for (const language of ["bash", "text"]) {
        const { unmount } = render(<Markdown text={`\`\`\`${language}\n${code}\n\`\`\``} />);
        fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
        await waitFor(() => expect(writeText).toHaveBeenCalled());
        unmount();
      }
      expect(writeText.mock.calls).toEqual([[`${code}\n`], [`${code}\n`]]);
    });
  });
});

describe("a fence that is still open (6.4)", () => {
  it.each([
    ["only its opening line", "```bash", true],
    ["an opening line and some code", "```bash\necho", true],
    ["half a closing fence", "```bash\necho\n``", true],
    ["a closing fence of the other kind", "~~~\ncode\n```", true],
    ["a closing fence shorter than the opening one", "````\ncode\n```", true],
    ["a closing line that has words after it", "```\ncode\n``` more", true],
    ["a closing fence", "```bash\necho\n```", false],
    ["a closing fence with spaces after it", "~~~\ncode\n~~~  ", false],
    ["a longer closing fence", "```\ncode\n`````", false],
    ["Windows line ends", "```bash\r\necho\r\n```", false],
    ["a closing fence in a quote", "```bash\n> echo\n> ```", false],
    ["an empty block", "```\n```", false],
    ["an indented block, which has no fence", "    code\n    more", false],
  ])("%s: open is %s", (_what, raw, open) => {
    expect(fenceIsOpen(raw)).toBe(open);
  });
});

describe("the page never sets inner HTML (W5)", () => {
  // Colour is made of React elements, and nothing else is made from a string a model wrote.
  it("in any file it ships", () => {
    const src = join(import.meta.dirname, "..");
    const shipped = readdirSync(src, { recursive: true, encoding: "utf8" }).filter(
      (file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file),
    );
    expect(shipped.length).toBeGreaterThan(20);
    const setting =
      /dangerouslySetInnerHTML|\.innerHTML\s*[+]?=|\.outerHTML\s*=|insertAdjacentHTML/;
    expect(shipped.filter((file) => setting.test(readFileSync(join(src, file), "utf8")))).toEqual(
      [],
    );
  });
});
