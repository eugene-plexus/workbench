import { render, screen } from "@testing-library/react";

import { useKeepFocus } from "./useKeepFocus";

const handle: { begin: () => void } = { begin: () => undefined };

/** A control that is held while it works, as a browser holds a disabled one. */
function Probe({ busy, gone = false }: { busy: boolean; gone?: boolean }) {
  handle.begin = useKeepFocus(busy);
  return (
    <>
      {!gone && (
        <button data-testid="held" disabled={busy}>
          Work
        </button>
      )}
      <input aria-label="Elsewhere" />
    </>
  );
}

/** What a browser does with the control that has focus when it is disabled: focus
 * is on the page. (jsdom keeps it where it was, so the page says so.) */
function lostFocus() {
  return vi.spyOn(Document.prototype, "activeElement", "get").mockReturnValue(document.body);
}

afterEach(() => vi.restoreAllMocks());

describe("the keyboard's place while a control is held", () => {
  it("is given back to the control that had it when the work is over", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    handle.begin();
    rerender(<Probe busy />);
    lostFocus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy={false} />);
    expect(focus).toHaveBeenCalledOnce();
  });

  it("is not given back while the work goes on", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    handle.begin();
    rerender(<Probe busy />);
    lostFocus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy />);
    expect(focus).not.toHaveBeenCalled();
  });

  it("does not take focus from where the person has gone in the meantime", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    handle.begin();
    rerender(<Probe busy />);
    screen.getByLabelText("Elsewhere").focus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy={false} />);
    expect(focus).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Elsewhere")).toHaveFocus();
  });

  it("lets go of a control that is gone, and says nothing", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    handle.begin();
    rerender(<Probe busy />);
    lostFocus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy={false} gone />);
    expect(focus).not.toHaveBeenCalled();
  });

  it("does nothing for a change that did not begin with it", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    rerender(<Probe busy />);
    lostFocus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy={false} />);
    expect(focus).not.toHaveBeenCalled();
  });

  it("gives focus back once, not every time the work is over", () => {
    const { rerender } = render(<Probe busy={false} />);
    const button = screen.getByTestId("held");
    button.focus();
    handle.begin();
    rerender(<Probe busy />);
    lostFocus();
    const focus = vi.spyOn(button, "focus");
    rerender(<Probe busy={false} />);
    expect(focus).toHaveBeenCalledOnce();
    rerender(<Probe busy />);
    rerender(<Probe busy={false} />);
    expect(focus).toHaveBeenCalledOnce();
  });
});
