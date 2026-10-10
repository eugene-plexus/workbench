import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ChatSettings } from "./ChatSettings";

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
