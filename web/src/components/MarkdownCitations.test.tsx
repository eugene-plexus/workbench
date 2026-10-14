/**
 * Citations in an answer (rich content RC4): a web source's number after the
 * link the gateway placed it at, moving to the source; a passage's [n] a link
 * only for a passage that was sent, never in code or a link, opening it.
 */

import { createEvent, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";

import { answerStart, citationsFor, type Citations } from "../lib/citations";
import type { Message } from "../lib/types";
import { Markdown } from "./Markdown";

const TEXT = "See [the page](https://example.org/page) and 😀 https://example.org/other for more.";

function at(needle: string): { start: number; end: number } {
  const start = TEXT.indexOf(needle);
  return { start, end: start + needle.length };
}

function inAnswer(ui: ReactElement, extra: ReactElement | null = null) {
  return render(
    <div data-testid="answer">
      {ui}
      {extra}
    </div>,
  );
}

it("puts a web source's number after the link it cites, and moves to the source", () => {
  const citations: Citations = {
    web: [
      { n: 1, ...at("[the page](https://example.org/page)") },
      { n: 2, ...at("https://example.org/other") },
    ],
    passages: [],
  };
  inAnswer(
    <Markdown text={TEXT} citations={citations} />,
    <ol>
      <li data-source-n="1">
        <a href="https://example.org/page">The page</a>
      </li>
      <li data-source-n="2">
        <a href="https://example.org/other">Another</a>
      </li>
    </ol>,
  );
  const one = screen.getByRole("link", { name: "Source 1" });
  expect(one.closest("sup")).not.toBeNull();
  // After the link, not inside it.
  expect(one.previousSibling).toBeNull();
  expect(one.closest("sup")?.previousElementSibling).toHaveAttribute(
    "href",
    "https://example.org/page",
  );
  const two = screen.getByRole("link", { name: "Source 2" });
  const click = createEvent.click(two);
  fireEvent(two, click);
  expect(document.activeElement).toHaveTextContent("Another");
  // The address stays: the browser does not follow the number's own `#` link
  // (this page's jsdom would not show a changed hash, so the click says so).
  expect(click.defaultPrevented).toBe(true);
  expect(window.location.hash).toBe("");
});

it("makes no number where no link covers the place", () => {
  const citations: Citations = { web: [{ n: 1, start: 0, end: 3 }], passages: [] };
  inAnswer(<Markdown text={TEXT} citations={citations} />);
  expect(screen.queryByRole("link", { name: "Source 1" })).toBeNull();
});

it("links a passage's number only for a passage that was sent, and opens it", () => {
  const text = "Both say so [1] and [2], not [7]; `code [1]` and [a [1] link](https://x.example).";
  inAnswer(
    <Markdown text={text} citations={{ web: [], passages: [1, 2] }} />,
    <ol>
      <li data-passage-rank="1">
        <details>
          <summary>The first passage</summary>
          words
        </details>
      </li>
      <li data-passage-rank="2">
        <details>
          <summary>The second passage</summary>
          words
        </details>
      </li>
    </ol>,
  );
  expect(screen.getAllByRole("link", { name: /^Passage / })).toHaveLength(2);
  expect(screen.getByText(/not \[7\]/)).toBeInTheDocument();
  expect(screen.getByText("code [1]").tagName).toBe("CODE");
  fireEvent.click(screen.getByRole("link", { name: "Passage 2" }));
  const second = document.querySelector('[data-passage-rank="2"] details') as HTMLDetailsElement;
  expect(second.open).toBe(true);
  expect(document.activeElement).toHaveTextContent("The second passage");
});

it("takes places from where the answer begins, as the page counts them", () => {
  const message = {
    id: "m",
    role: "assistant",
    status: "done",
    content: "A draft 😀.\n\n  The answer cites https://x.example/a here.",
    answerFrom: 13,
    sources: [{ kind: "web", url: "https://x.example/a", title: "A", marks: [[32, 51]] }],
  } as unknown as Message;
  expect(answerStart(message)).toBe(15);
  expect(citationsFor(message)).toEqual({ web: [{ n: 1, start: 17, end: 36 }], passages: [] });
  const answer = message.content.slice(15);
  expect(answer.slice(17, 36)).toBe("https://x.example/a");
});

it("leaves a footnote's own numbers as they are", () => {
  inAnswer(
    <Markdown
      text={"Said [1].[^n]\n\n[^n]: A note about [1]."}
      citations={{ web: [], passages: [1] }}
    />,
  );
  expect(screen.getAllByRole("link", { name: "Passage 1" })).toHaveLength(1);
});
