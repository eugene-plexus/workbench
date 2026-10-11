/**
 * What colouring a block may cost, and what a failure of it may not (6.4).
 *
 * An answer is untrusted, the page has no error boundary, and a streamed
 * answer renders on every chunk: a grammar that throws must not blank the
 * chat, and a block that has not changed must not be coloured, turned into
 * elements or compared again. lowlight is wrapped to count what it is asked
 * and to fail on a marker; the colour it makes is real.
 */

import { render, screen } from "@testing-library/react";

import { HIGHLIGHT_MAX_CHARS, HIGHLIGHT_MAX_LINES } from "../lib/highlight";
import { Markdown } from "./Markdown";

/** How many times lowlight was set up: once, when the page loads. */
const built = vi.hoisted(() => vi.fn());
/** The source of each block lowlight was asked to colour. */
const asked = vi.hoisted(() => vi.fn<(source: string) => void>());
/** How many times a code block was rendered (its Copy button is rendered with it). */
const rendered = vi.hoisted(() => vi.fn<(source: string) => void>());
/** How many times the Markdown pipeline was built, which is once per render of a Markdown. */
const parsed = vi.hoisted(() => vi.fn());

vi.mock("lowlight", async (original) => {
  const real = await original<typeof import("lowlight")>();
  return {
    ...real,
    createLowlight: (grammars: Parameters<typeof real.createLowlight>[0]) => {
      built();
      const lowlight = real.createLowlight(grammars);
      return {
        ...lowlight,
        highlight: (language: string, source: string) => {
          asked(source);
          if (source.includes("BOOM")) throw new Error("a grammar failed");
          return lowlight.highlight(language, source);
        },
      };
    },
  };
});

vi.mock("./CopyButton", async () => {
  const { createElement } = await import("react");
  return {
    CopyButton: ({ text }: { text: string }) => {
      rendered(text);
      return createElement("button", { type: "button", "aria-label": "Copy code" }, "Copy");
    },
  };
});

vi.mock("remark-breaks", async (original) => {
  const real = await original<typeof import("remark-breaks")>();
  return {
    default: (...args: Parameters<typeof real.default>) => {
      parsed();
      return real.default(...args);
    },
  };
});

const fence = (language: string, body: string) => `\`\`\`${language}\n${body}\n\`\`\``;
const askedAbout = (needle: string) =>
  asked.mock.calls.filter(([source]) => source.includes(needle));
const renderedAbout = (needle: string) =>
  rendered.mock.calls.filter(([source]) => source.includes(needle));
const isColoured = (code: Element | null | undefined) =>
  Boolean(code?.className.includes("hljs") && code.querySelector("span"));

describe("a grammar that fails", () => {
  const TEXT = [
    "Before.",
    fence("bash", 'echo "BOOM" # fails'),
    fence("bash", 'echo "fine" # works'),
    "After.",
  ].join("\n\n");

  it("leaves the answer showing, the failed block plain and the others coloured", () => {
    const { container } = render(<Markdown text={TEXT} />);
    expect(screen.getByText("Before.")).toBeInTheDocument();
    expect(screen.getByText("After.")).toBeInTheDocument();
    const [failed, fine] = Array.from(container.querySelectorAll("pre code"));
    expect(failed?.textContent).toBe('echo "BOOM" # fails\n');
    expect(isColoured(failed)).toBe(false);
    expect(isColoured(fine)).toBe(true);
    // The label and Copy are still there for both.
    expect(screen.getAllByRole("button", { name: "Copy code" })).toHaveLength(2);
  });

  it("is not tried again on the same text", () => {
    const text = fence("bash", "echo BOOM again");
    const { rerender } = render(<Markdown text={text} />);
    rerender(<Markdown text={`${text}\n\nand more`} />);
    render(<Markdown text={text} />);
    expect(askedAbout("BOOM again")).toHaveLength(1);
  });
});

describe("the cost of colouring", () => {
  it("is set up once, not on every render of a streamed answer", () => {
    const before = built.mock.calls.length;
    const { rerender } = render(<Markdown text={fence("bash", "a")} />);
    for (const more of ["ab", "abc", "abcd"]) rerender(<Markdown text={fence("bash", more)} />);
    // The grammars are registered once, when the page loads.
    expect(built.mock.calls.length).toBe(before);
  });

  it("is paid once for a block that stays the same while the answer grows", () => {
    const block = fence("bash", 'echo "cached" # once');
    const { container, rerender } = render(<Markdown text={block} />);
    for (const more of ["a", "ab", "abc", "abcd"]) {
      rerender(<Markdown text={`${block}\n\nThen ${more}`} />);
    }
    expect(askedAbout("cached")).toHaveLength(1);
    // What was remembered is what is shown: the colour is still there.
    expect(isColoured(container.querySelector("pre code"))).toBe(true);
  });

  it("is remembered across answers: the same text elsewhere is not coloured again", () => {
    const block = fence("bash", 'echo "elsewhere" # same');
    render(<Markdown text={block} />);
    const second = render(<Markdown text={`Another answer.\n\n${block}`} />);
    expect(askedAbout("elsewhere")).toHaveLength(1);
    expect(isColoured(second.container.querySelector("pre code"))).toBe(true);
  });

  it("is paid again for another language or other text", () => {
    const same = 'echo "x" # 1';
    render(<Markdown text={[fence("bash", same), fence("shell", same)].join("\n\n")} />);
    render(<Markdown text={fence("bash", 'echo "x" # 2')} />);
    expect(askedAbout("# 1")).toHaveLength(2);
    expect(askedAbout("# 2")).toHaveLength(1);
  });

  it("waits for a fence to close: nothing is asked while it is being written", () => {
    const open = '```bash\necho "still writing" # open';
    const { container, rerender } = render(<Markdown text={`Here:\n\n${open}`} />);
    expect(askedAbout("still writing")).toHaveLength(0);
    expect(isColoured(container.querySelector("pre code"))).toBe(false);
    // Half a closing fence is not one.
    rerender(<Markdown text={`Here:\n\n${open}\n\`\``} />);
    expect(askedAbout("still writing")).toHaveLength(0);
    // Closed: asked once, and the colour appears.
    rerender(<Markdown text={`Here:\n\n${open}\n\`\`\`\n`} />);
    expect(askedAbout("still writing")).toHaveLength(1);
    expect(isColoured(container.querySelector("pre code"))).toBe(true);
  });

  it("shows a block over the size cap plain, and one at the cap coloured", () => {
    const atCap = Array.from({ length: HIGHLIGHT_MAX_LINES }, () => "x").join("\n");
    const pastLines = `${atCap}\nx`;
    const pastChars = "y".repeat(HIGHLIGHT_MAX_CHARS + 1);
    render(<Markdown text={[fence("bash", atCap), fence("bash", pastLines)].join("\n\n")} />);
    render(<Markdown text={fence("bash", pastChars)} />);
    // The parser ends a code block's text with a line end.
    expect(asked.mock.calls.filter(([source]) => source === `${atCap}\n`)).toHaveLength(1);
    expect(asked.mock.calls.filter(([source]) => source === `${pastLines}\n`)).toHaveLength(0);
    expect(asked.mock.calls.filter(([source]) => source === `${pastChars}\n`)).toHaveLength(0);
  });

  it("forgets the longest unused block when the memory is full, and keeps one used again", () => {
    // Blocks just under both caps (330 lines, 7,600 characters): 26 fill the memory
    // (200,000 characters), and 8 more push the oldest unused out.
    const big = (name: string) => `# ${name}\n${"echo hello hello hello\n".repeat(330)}`;
    const names = Array.from({ length: 34 }, (_, i) => `block ${i}`);
    const show = (name: string) => render(<Markdown text={fence("bash", big(name))} />);
    const count = (name: string) => askedAbout(`# ${name}\n`).length;
    for (const name of names.slice(0, 26)) show(name);
    show(names[0]!); // used again: now the most recent, not the oldest
    for (const name of names.slice(26)) show(name);
    names.forEach((name) => expect(count(name), name).toBe(1));
    // Kept, though it was the first: not asked again. Forgotten: asked again.
    show(names[0]!);
    show(names[names.length - 1]!);
    expect(count(names[0]!)).toBe(1);
    expect(count(names[names.length - 1]!)).toBe(1);
    show(names[1]!);
    expect(count(names[1]!)).toBe(2);
  });
});

describe("a block that has stopped changing", () => {
  it("is not rendered again while the answer under it grows", () => {
    const block = fence("bash", 'echo "settled" # render');
    const { rerender } = render(<Markdown text={`${block}\n\nThen`} />);
    expect(renderedAbout("settled")).toHaveLength(1);
    for (const more of ["Then a", "Then a b", "Then a b c"]) {
      rerender(<Markdown text={`${block}\n\n${more}`} />);
    }
    // React skipped it: no new element tree, no comparison of its tokens.
    expect(renderedAbout("settled")).toHaveLength(1);
  });

  it("is rendered again when its own text changes, as one being written does", () => {
    const { rerender } = render(<Markdown text={'```bash\necho "growing" # a'} />);
    rerender(<Markdown text={'```bash\necho "growing" # a b'} />);
    expect(renderedAbout("growing")).toHaveLength(2);
  });
});

describe("a finished answer", () => {
  it("is not parsed again when only what is around it changes", () => {
    const { rerender } = render(<Markdown text="Finished **answer**." />);
    const after = parsed.mock.calls.length;
    // The same props, as MessageView passes them on every token of an answer below.
    rerender(<Markdown text="Finished **answer**." />);
    rerender(<Markdown text="Finished **answer**." />);
    expect(parsed.mock.calls.length).toBe(after);
    // A different text is parsed.
    rerender(<Markdown text="Finished **answer**. And more." />);
    expect(parsed.mock.calls.length).toBe(after + 1);
  });
});
