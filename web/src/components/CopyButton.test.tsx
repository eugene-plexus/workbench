import { act, fireEvent, render, screen } from "@testing-library/react";

import { CopyButton } from "./CopyButton";

const clipboard = (writeText: () => Promise<void>) =>
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

it("is a quiet action, named for what it copies", () => {
  render(<CopyButton text="x" label="this answer" />);
  const button = screen.getByRole("button", { name: "Copy this answer" });
  expect(button).toHaveClass("act");
  expect(button).toHaveTextContent("Copy");
  expect(button).not.toHaveClass("text-ok");
});

it("says Copied in the success colour, with its words, and goes back", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    clipboard(() => Promise.resolve());
    render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    const done = await screen.findByRole("button", { name: "Copied" });
    expect(done).toHaveClass("text-ok");
    expect(screen.getByRole("status")).toHaveTextContent("Copied to clipboard.");
    // Said to a screen reader and not shown twice.
    expect(screen.getByRole("status")).toHaveClass("sr-only");
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(screen.getByRole("button", { name: "Copy" })).not.toHaveClass("text-ok");
  } finally {
    vi.useRealTimers();
  }
});

it("is named Copied, not Copy this answer, while it says Copied", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    clipboard(() => Promise.resolve());
    render(<CopyButton text="x" label="this answer" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy this answer" }));
    // The word on the button is Copied: its name must carry that word, and
    // must not go on saying "Copy this answer" under it.
    const done = await screen.findByRole("button", { name: "Copied" });
    expect(done).toHaveTextContent("Copied");
    expect(done).not.toHaveAttribute("aria-label");
    expect(screen.queryByRole("button", { name: "Copy this answer" })).toBeNull();
    // Then it goes back to naming what it copies.
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(screen.getByRole("button", { name: "Copy this answer" })).toHaveTextContent("Copy");
  } finally {
    vi.useRealTimers();
  }
});

it("says a refusal with a glyph, the error colour and its words, not a colour alone", async () => {
  clipboard(() => Promise.reject(new Error("denied")));
  // The selection fallback is refused as well (jsdom has no execCommand).
  Object.defineProperty(document, "execCommand", { value: () => false, configurable: true });
  const { container } = render(<CopyButton text="x" />);
  fireEvent.click(screen.getByRole("button", { name: "Copy" }));
  const refused = await screen.findByText(
    "Copy was blocked. Select the text and copy it manually.",
  );
  expect(refused).toHaveAttribute("role", "status");
  expect(refused).toHaveClass("text-error");
  expect(refused).not.toHaveClass("sr-only");
  expect(refused.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  expect(container.querySelectorAll("[role=status]")).toHaveLength(1);
});
