import { Fragment } from "react";

import { marked } from "../lib/savedDocuments";

/** Words with the ones a search was made with marked where they are. A mark is a
 * tint and a heavier word, so it shows without colour alone. */
export function Marked({ text, words }: { text: string; words: string[] }) {
  return (
    <>
      {marked(text, words).map((part, at) =>
        part.hit ? (
          <mark key={at} className="rounded-sm bg-info-bg px-0.5 font-medium text-strong">
            {part.text}
          </mark>
        ) : (
          <Fragment key={at}>{part.text}</Fragment>
        ),
      )}
    </>
  );
}
