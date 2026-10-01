import { render, screen } from "@testing-library/react";

import type { Message } from "../lib/types";
import { MessageView } from "./MessageView";

const answer = (over: Partial<Message>): Message => ({
  id: "a",
  seq: 2,
  role: "assistant",
  content: "An answer.",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

function show(message: Message) {
  return render(
    <MessageView
      chatId="c"
      message={message}
      progress={null}
      last
      busy={false}
      readOnly={false}
      onChanged={() => undefined}
    />,
  );
}

describe("a searched answer (W6)", () => {
  it("lists what it cites as links", () => {
    show(answer({ searches: 1, sources: [{ url: "https://example.org/a", title: "A page" }] }));
    expect(screen.getByRole("link", { name: "A page" })).toHaveAttribute(
      "href",
      "https://example.org/a",
    );
    expect(screen.getByTestId("sources")).toHaveTextContent("Sources (1 search)");
  });

  it("says it searched even when it cites nothing", () => {
    show(answer({ searches: 2 }));
    expect(screen.getByTestId("sources")).toHaveTextContent("Sources (2 searches)");
    expect(screen.getByTestId("sources")).toHaveTextContent("does not link to any page");
  });

  it("says nothing of a search that did not run", () => {
    show(answer({}));
    expect(screen.queryByTestId("sources")).toBeNull();
  });

  it("shows the reasoning folded away, and a failure's own words", () => {
    show(answer({ reasoning: "Thinking…", status: "failed", error: "the key was refused" }));
    expect(screen.getByText("Reasoning").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("alert")).toHaveTextContent("the key was refused");
  });
});
