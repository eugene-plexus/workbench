import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, post, put } from "../lib/api";
import type { Message } from "../lib/types";
import { VersionArrows } from "./Versions";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn(), put: vi.fn() }));

const message: Message = {
  id: "a2",
  seq: 2,
  role: "assistant",
  content: "Answer",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "model",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  versions: { index: 2, count: 2, ids: ["a1", "a2"], names: [null, "Shorter tone"] },
};

it("shows a name, saves it while busy, and holds deletion while busy", async () => {
  const changed = vi.fn();
  render(
    <VersionArrows
      chatId="c"
      message={message}
      busy
      readOnly={false}
      onChanged={changed}
      onProblem={vi.fn()}
    />,
  );
  expect(screen.getByTestId("versions")).toHaveTextContent("2 of 2 · Shorter tone");
  fireEvent.click(screen.getByRole("button", { name: "Version actions" }));
  expect(screen.getByRole("button", { name: "Delete this version" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Name this version" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Version name" }), {
    target: { value: "Brief" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save name" }));
  await waitFor(() =>
    expect(put).toHaveBeenCalledWith("/api/chats/c/messages/a2/name", { name: "Brief" }),
  );
});

it("counts the branch before confirmation and never focuses delete by default", async () => {
  vi.mocked(api).mockResolvedValue({
    messages: 7,
    answers: 3,
    files: 2,
    artifacts: 1,
    pendingNotes: 1,
    keptNotes: 1,
    toolCalls: 0,
    chosen: true,
  });
  render(
    <VersionArrows
      chatId="c"
      message={message}
      busy={false}
      readOnly={false}
      onChanged={vi.fn()}
      onProblem={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Version actions" }));
  fireEvent.click(screen.getByRole("button", { name: "Delete this version" }));
  const confirm = await screen.findByRole("button", { name: "Delete version" });
  expect(screen.getByRole("dialog")).toHaveTextContent(
    "Delete answer 2 of 2 and the 6 messages after it? 2 files and 1 artifact go with them. 1 pending note goes too. Notes kept from them stay.",
  );
  expect(screen.getByRole("dialog")).toHaveTextContent("30 days");
  expect(confirm).not.toHaveFocus();
  expect(post).not.toHaveBeenCalled();
});

it("lets the owner compare but has no write actions", () => {
  render(
    <VersionArrows
      chatId="c"
      message={message}
      busy={false}
      readOnly
      onChanged={vi.fn()}
      onProblem={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Version actions" }));
  expect(screen.getByRole("button", { name: "Compare with version 1" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Name this version" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete this version" })).toBeNull();
});
