/** The code block an answer shows (Markdown.tsx), also a diagram's source (MermaidDiagram.tsx). */

import { memo } from "react";
import type { ArtifactKind } from "../lib/artifacts";
import { colourCode } from "../lib/highlight";
import { OpenAsArtifact } from "./Artifact";
import { CopyButton } from "./CopyButton";

/**
 * A code block: its language and Copy joined to the code in one box, and the
 * code coloured once its fence has closed. Memoised on what it is shown from,
 * strings and a flag: a streamed answer renders on every token, and a block
 * above the text being written has long stopped changing, so React skips it
 * (turning 300 lines of coloured tokens into elements and comparing them took
 * 70 ms a token here).
 *
 * `openAs`, the artifact a block of its language can be (rich content RC5),
 * puts Open as artifact in its head once its fence has closed, in an answer.
 */
export const Block = memo(function Block({
  language,
  source,
  open,
  note,
  openAs = null,
}: {
  language: string | null;
  source: string;
  open: boolean;
  note?: string;
  openAs?: ArtifactKind | null;
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
        {openAs && !open ? (
          <span className="diagram-tools">
            <OpenAsArtifact kind={openAs} language={language} source={source} />
            <CopyButton text={source} label="code" />
          </span>
        ) : (
          <CopyButton text={source} label="code" />
        )}
      </div>
      {/* A focusable scroller, so a keyboard can reach code wider than its box. */}
      <pre tabIndex={0} aria-label={`${language ?? "Code"} block`}>
        <code className={coloured ? `hljs ${named}` : named}>{coloured ?? source}</code>
      </pre>
      {note && <p className="code-note">{note}</p>}
    </div>
  );
});
