import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, post } from "../lib/api";
import { CompareVersions } from "./CompareVersions";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn() }));

it("reads both paths, shows shared messages once, and chooses only on request", async () => {
  const shared = { id: "u", role: "user", content: "Shared question" };
  vi.mocked(api).mockImplementation(async (path) => ({
    chat: {},
    ownerName: null,
    messages: [
      shared,
      {
        id: path.endsWith("a1") ? "a1" : "a2",
        role: "assistant",
        model: "model",
        content: path.endsWith("a1") ? "First answer" : "Second answer",
        versions: { index: path.endsWith("a1") ? 1 : 2, names: [null, "Shorter tone"] },
      },
    ],
  }));
  const changed = vi.fn();
  render(
    <CompareVersions
      chatId="c"
      ids={["a1", "a2"]}
      readOnly={false}
      busy={false}
      onChanged={changed}
      onClose={vi.fn()}
    />,
  );
  expect(await screen.findByText("First answer")).toBeInTheDocument();
  expect(screen.getAllByText("Shared question")).toHaveLength(1);
  expect(screen.getByText("Answer 2 · Shorter tone · model")).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole("button", { name: "Choose this one" })[1]!);
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/chats/c/messages/a2/choose"));
  await waitFor(() => expect(changed).toHaveBeenCalled());
});
