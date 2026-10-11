import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { ChatSettings, SHEET_OVERLAY_QUERY } from "./ChatSettings";

vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));

it("inherits protection by default and saves an explicit off override", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(<ChatSettings settings={{}} onSave={save} onClose={() => undefined} />);
  const protection = screen.getByRole("combobox", { name: /Repeated response protection/ });
  expect(protection).toHaveValue("");
  fireEvent.change(protection, { target: { value: "off" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ repetitionMode: "off" })),
  );
});

it("restores a saved stop mode and can clear it back to inheritance", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(
    <ChatSettings settings={{ repetitionMode: "stop" }} onSave={save} onClose={() => undefined} />,
  );
  const protection = screen.getByRole("combobox", { name: /Repeated response protection/ });
  expect(protection).toHaveValue("stop");
  fireEvent.change(protection, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ repetitionMode: null })),
  );
});

it("shows each field's hint and range outside its label, linked by aria-describedby", () => {
  render(<ChatSettings settings={{}} onSave={vi.fn()} onClose={() => undefined} />);
  expect(screen.getByRole("textbox", { name: "Top-p" })).toHaveAccessibleDescription(
    /likeliest tokens.*Between 0\.01 and 1\./,
  );
  expect(screen.queryByText(/likeliest words/)).toBeNull();
  expect(screen.getByRole("textbox", { name: "Temperature" })).toHaveAccessibleDescription(
    /Between 0 and 2\./,
  );
  expect(
    screen.getByRole("textbox", { name: "Longest answer (tokens)" }),
  ).toHaveAccessibleDescription(/Between 1 and 1000000\./);
});

it("announces the save as a status and names the buttons", async () => {
  const save = vi.fn().mockResolvedValue(undefined);
  render(<ChatSettings settings={{}} onSave={save} onClose={() => undefined} />);
  expect(screen.getByRole("button", { name: "Close settings" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Saved. The next answer uses these.");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A window that says whether it is narrow, and can be resized. */
function window_(narrow: boolean) {
  const listeners = new Set<() => void>();
  const list = {
    matches: narrow,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => list),
  );
  return {
    resize(next: boolean) {
      list.matches = next;
      listeners.forEach((fn) => fn());
    },
  };
}

describe("the sheet over the whole window", () => {
  const sheet = () => <ChatSettings settings={{}} onSave={vi.fn()} onClose={() => undefined} />;

  it("is a panel above the chat where nothing says the window is narrow", () => {
    render(sheet());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("region", { name: "This chat's settings" })).toBe(
      screen.getByTestId("chat-settings"),
    );
    // A panel does not take focus: a person who opened it with a pointer keeps reading the chat.
    expect(document.body).toHaveFocus();
  });

  it("is a named, modal dialog when it covers the window, and focus goes to its first field", () => {
    window_(true);
    render(sheet());
    const dialog = screen.getByRole("dialog", { name: "This chat's settings" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveFocus();
  });

  it("says what it is at the top when it covers the window, and not when it is a panel under the chat's own title", () => {
    window_(true);
    const covering = render(sheet());
    const title = screen.getByRole("heading", { name: "This chat's settings" });
    expect(screen.getByRole("dialog")).toContainElement(title);
    // It comes before the first field, so the first thing read is where the person is.
    expect(
      title.compareDocumentPosition(screen.getByRole("textbox", { name: "Instructions" })) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    covering.unmount();
    window_(false);
    render(sheet());
    expect(screen.queryByRole("heading", { name: "This chat's settings" })).toBeNull();
  });

  it("asks the window the same question the stylesheet does", () => {
    const css = readFileSync(join(import.meta.dirname, "..", "styles.css"), "utf8");
    expect(css).toContain(`@media ${SHEET_OVERLAY_QUERY} {\n  .sheet {\n    position: fixed;`);
    window_(false);
    render(sheet());
    expect(window.matchMedia).toHaveBeenCalledWith(SHEET_OVERLAY_QUERY);
  });

  it("becomes a dialog, and a panel again, as the window is resized", () => {
    const resizing = window_(false);
    render(sheet());
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => resizing.resize(true));
    expect(screen.getByRole("dialog", { name: "This chat's settings" })).toBeInTheDocument();
    act(() => resizing.resize(false));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps Tab inside it: past the last control to the first, and back", () => {
    window_(true);
    render(sheet());
    const first = screen.getByRole("textbox", { name: "Instructions" });
    const last = screen.getByRole("button", { name: "Save settings" });
    last.focus();
    // Prevented: the browser would have left the dialog.
    expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(false);
    expect(first).toHaveFocus();
    expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
    expect(last).toHaveFocus();
    // Between the two it leaves Tab to the browser.
    const middle = screen.getByRole("button", { name: "Close settings" });
    middle.focus();
    expect(fireEvent.keyDown(middle, { key: "Tab" })).toBe(true);
  });

  it("does not keep Tab inside the panel, which is part of the page", () => {
    window_(false);
    render(sheet());
    const last = screen.getByRole("button", { name: "Save settings" });
    last.focus();
    expect(fireEvent.keyDown(last, { key: "Tab" })).toBe(true);
    expect(last).toHaveFocus();
  });
});

describe("closing", () => {
  it("closes on Escape from anywhere inside, but not while a composition is open", () => {
    const close = vi.fn();
    render(<ChatSettings settings={{}} onSave={vi.fn()} onClose={close} />);
    const field = screen.getByRole("textbox", { name: "Top-p" });
    fireEvent.keyDown(field, { key: "Escape", isComposing: true });
    expect(close).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["a dialog", true],
    ["a panel", false],
  ])("as %s gives focus back to what opened it", (_kind, narrow) => {
    window_(narrow);
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    const { unmount } = render(
      <ChatSettings settings={{}} onSave={vi.fn()} onClose={() => undefined} />,
    );
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });
});

describe("a number out of range", () => {
  it("names the field, marks it, and says so in the footer beside Save", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<ChatSettings settings={{}} onSave={save} onClose={() => undefined} />);
    const field = screen.getByRole("textbox", { name: "Temperature" });
    fireEvent.change(field, { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Temperature must be between 0 and 2, or empty for the model's default.",
    );
    expect(save).not.toHaveBeenCalled();
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveClass("border-error-line");
    // The hint stays in the description, and the alert joins it.
    expect(field).toHaveAccessibleDescription(
      /Higher is more varied.*Between 0 and 2\..*Temperature must be between/s,
    );
    expect(screen.getByRole("textbox", { name: "Top-p" })).not.toHaveAttribute("aria-invalid");
    // The alert is in the footer, which does not scroll away from a field far above it.
    expect(alert.closest(".sheet-foot")).not.toBeNull();
    expect(field.closest(".sheet-body")).not.toBeNull();
  });

  it("takes the mark away when the number is mended and saved", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(<ChatSettings settings={{}} onSave={save} onClose={() => undefined} />);
    const field = screen.getByRole("textbox", { name: "Temperature" });
    fireEvent.change(field, { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByRole("alert");
    fireEvent.change(field, { target: { value: "1.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(field).not.toHaveAttribute("aria-invalid");
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ temperature: 1.5 }));
  });

  it("marks no field when the save itself is refused, and says what Workbench said", async () => {
    const save = vi.fn().mockRejectedValue(new Error("This chat is read only."));
    render(<ChatSettings settings={{}} onSave={save} onClose={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This chat is read only.");
    expect(screen.queryAllByRole("textbox").filter((f) => f.hasAttribute("aria-invalid"))).toEqual(
      [],
    );
  });
});

describe("the layout", () => {
  it("scrolls the fields and not the buttons, and groups the answers under their own label", () => {
    render(<ChatSettings settings={{}} onSave={vi.fn()} onClose={() => undefined} />);
    const body = screen.getByRole("textbox", { name: "Instructions" }).closest(".sheet-body");
    const foot = screen.getByRole("button", { name: "Save settings" }).closest(".sheet-foot");
    expect(body).not.toBeNull();
    expect(foot).not.toBeNull();
    expect(body).not.toContainElement(foot as HTMLElement);
    expect(screen.getByRole("group", { name: "Answers" })).toContainElement(
      screen.getByRole("combobox", { name: /Repeated response protection/ }),
    );
  });

  it("says the save worked in the tint of success, with words", async () => {
    render(
      <ChatSettings
        settings={{}}
        onSave={vi.fn().mockResolvedValue(undefined)}
        onClose={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("status")).toHaveClass("text-ok");
  });

  it("describes the instructions and the protection by the help under each", () => {
    render(<ChatSettings settings={{}} onSave={vi.fn()} onClose={() => undefined} />);
    expect(screen.getByRole("textbox", { name: "Instructions" })).toHaveAccessibleDescription(
      "Told to the model before every message in this chat.",
    );
    expect(
      screen.getByRole("combobox", { name: /Repeated response protection/ }),
    ).toHaveAccessibleDescription(/Reasoning and tool arguments are never stopped/);
  });
});
