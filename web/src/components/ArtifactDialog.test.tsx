import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../lib/api";
import { digest, type ArtifactView } from "../lib/artifacts";
import ArtifactDialog from "./ArtifactDialog";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
}));

const original = "same\nold\nend";
const artifact: ArtifactView = {
  id: "a1",
  messageId: "m1",
  kind: "markdown",
  title: "Notes",
  digest: digest(original),
  revisions: 3,
  createdAt: 1,
  updatedAt: 2,
  revisionList: [1, 2, 3].map((number) => ({
    number,
    author: number === 1 ? "answer" : "person",
    size: 20,
    createdAt: number,
  })),
};
function show() {
  return render(
    <ArtifactDialog
      scope={{
        chatId: "c1",
        messageId: "m1",
        artifacts: [artifact],
        readOnly: false,
        writing: false,
        onKept() {},
      }}
      opened={{ kind: "markdown", language: "md", original, title: "Notes" }}
      onClose={() => {}}
    />,
  );
}
beforeEach(() => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith("/revisions/3")) return { number: 3, source: "same\nnew\nend" };
    if (path.endsWith("/revisions/2")) return { number: 2, source: "same\nolder\nend" };
    return artifact;
  });
});

it("compares saved revisions and reads the chosen revision through the existing route", async () => {
  show();
  await screen.findByText("new", { exact: false });
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "2" } });
  const diff = await screen.findByRole("region", { name: "Changes from revision 2 to revision 3" });
  await waitFor(() => expect(diff.querySelector("del")).toHaveTextContent("older"));
  expect(diff.querySelector("ins")).toHaveTextContent("new");
  expect(
    diff.querySelector("del")?.closest(".artifact-diff-line")?.querySelector("ins"),
  ).toHaveTextContent("new");
  expect(api).toHaveBeenCalledWith("/api/chats/c1/artifacts/a1/revisions/2");
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "1" } });
  await waitFor(() => expect(diff.querySelector("del")).toHaveTextContent("old"));
  fireEvent.change(screen.getByLabelText("Revision"), { target: { value: "2" } });
  expect(screen.queryByRole("region", { name: /Changes from/ })).toBeNull();
});

it("names a failed revision read and offers downloads when a comparison is too long", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith("/revisions/3")) return { number: 3, source: "x\n".repeat(5001) };
    if (path.endsWith("/revisions/2")) throw new Error("Connection lost");
    return artifact;
  });
  show();
  await waitFor(() => expect(screen.getByLabelText("Compare with revision")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "2" } });
  expect(
    await screen.findByText("Revision 2 could not be read: Connection lost"),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "1" } });
  expect(await screen.findByText("Too long to compare here. Download both.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Download both" })).toBeEnabled();
});
