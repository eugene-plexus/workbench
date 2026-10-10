import { fireEvent, render, screen } from "@testing-library/react";

import { SignIn } from "./SignIn";

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
