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
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ repetitionMode: null })),
  );
});
