import { fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { SIGNED_OUT_BY_YOU, SignIn } from "./SignIn";

it("offers Check again when sign-in is unavailable, and it reloads", () => {
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload });
  render(<SignIn message={null} unavailable="Eugene cannot be reached." />);
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  expect(reload).toHaveBeenCalled();
  vi.unstubAllGlobals();
});

it("offers no Check again when sign-in is available", () => {
  render(<SignIn message={null} unavailable={null} />);
  expect(screen.queryByRole("button", { name: "Check again" })).toBeNull();
  expect(screen.getByTestId("sign-in")).toBeInTheDocument();
});

it("acknowledges a sign-out the person chose as a plain notice, not a warning", () => {
  render(<SignIn message={SIGNED_OUT_BY_YOU} unavailable={null} />);
  const note = screen.getByRole("status");
  expect(note).toHaveTextContent("You signed out.");
  expect(note).toHaveClass("notice");
  expect(note).not.toHaveClass("notice--warn");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("warns when Eugene ended the sign-in, in Eugene's own words", () => {
  render(
    <SignIn message="Your Workbench sign-in is a month old. Sign in again." unavailable={null} />,
  );
  const warning = screen.getByRole("alert");
  expect(warning).toHaveTextContent("Your Workbench sign-in is a month old. Sign in again.");
  expect(warning).toHaveClass("notice", "notice--warn");
  expect(screen.queryByRole("status")).toBeNull();
});

it("says in an error, with Check again, when sign-in cannot be offered", () => {
  render(<SignIn message={null} unavailable="Eugene's sign-in answered 502." />);
  const error = screen.getByRole("alert");
  expect(error).toHaveTextContent("Eugene's sign-in answered 502.");
  expect(error).toHaveClass("notice--error");
  expect(screen.getByRole("button", { name: "Check again" })).toBeInTheDocument();
  expect(screen.queryByTestId("sign-in")).toBeNull();
});

it("says the same words App.tsx says when a person signs out, or the notice would turn into a warning", () => {
  const app = readFileSync(join(import.meta.dirname, "..", "App.tsx"), "utf8");
  expect(app).toContain(`"${SIGNED_OUT_BY_YOU}"`);
});

it("offers one primary action, a link to sign-in, and says what the page is for in two plain sentences", () => {
  render(<SignIn message={null} unavailable={null} />);
  const link = screen.getByTestId("sign-in");
  expect(link).toHaveAttribute("href", "/signin");
  expect(link).toHaveClass("btn", "btn-primary");
  expect(screen.getByRole("heading", { level: 1, name: "Workbench" })).toBeInTheDocument();
  expect(screen.getByText("Chat with the models your Eugene install serves.")).toBeInTheDocument();
  expect(
    screen.getByText(
      "Sign in with your Eugene account, or with Eugene's passphrase if you are its owner.",
    ),
  ).toHaveClass("text-muted");
});
