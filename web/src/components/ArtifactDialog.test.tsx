import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, post } from "../lib/api";
import { digest, keepDraft, type ArtifactView } from "../lib/artifacts";
import ArtifactDialog from "./ArtifactDialog";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
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
  const diff = await screen.findByRole("tabpanel", { name: "Changes" });
  expect(diff).toHaveTextContent("Revision 2 → Revision 3");
  await waitFor(() => expect(diff.querySelector("del")).toHaveTextContent("older"));
  expect(diff.querySelector("ins")).toHaveTextContent("new");
  expect(
    diff.querySelector("del")?.closest(".artifact-diff-line")?.querySelector("ins"),
  ).toHaveTextContent("new");
  expect(api).toHaveBeenCalledWith("/api/chats/c1/artifacts/a1/revisions/2");
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "1" } });
  await waitFor(() => expect(diff.querySelector("del")).toHaveTextContent("old"));
  fireEvent.change(screen.getByLabelText("Revision"), { target: { value: "2" } });
  expect(screen.queryByRole("tabpanel", { name: "Changes" })).toBeNull();
});

it("selects the Changes tab while comparing and lets the keyboard return to source", async () => {
  show();
  await waitFor(() => expect(screen.getByLabelText("Compare with revision")).not.toBeDisabled());
  fireEvent.change(screen.getByLabelText("Compare with revision"), { target: { value: "1" } });
  const changes = screen.getByRole("tab", { name: "Changes", selected: true });
  expect(screen.getByRole("tabpanel", { name: "Changes" })).toHaveTextContent("old");
  expect(screen.getByRole("tab", { name: "Preview" })).toHaveAttribute("aria-selected", "false");
  fireEvent.keyDown(changes, { key: "ArrowLeft" });
  expect(screen.getByRole("tab", { name: "Source", selected: true })).toHaveFocus();
  expect(screen.getByRole("tabpanel", { name: "Source" })).toBeVisible();
  expect(screen.getByLabelText("Compare with revision")).toHaveValue("");
});

it("saved_tool_artifact_names_the_result_and_keeps_person_edits_separate", async () => {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith("/revisions/3")) return { number: 3, source: "same\nnew\nend" };
    return { ...artifact, origin: "tool", tool: "pages/fetch_page" };
  });
  show();
  await screen.findByText("new", { exact: false });
  const revisions = screen.getByLabelText("Revision");
  expect(revisions.querySelector('option[value="1"]')).toHaveTextContent(
    "from fetch_page's result",
  );
  expect(revisions.querySelector('option[value="2"]')).toHaveTextContent("edited");
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

it("keeps the chosen revision when the chat learns about the artifact just saved", async () => {
  const saved: ArtifactView = {
    ...artifact,
    revisions: 2,
    revisionList: artifact.revisionList.slice(0, 2),
    origin: "tool",
    tool: "pages/fetch_page",
  };
  vi.mocked(post).mockResolvedValue(saved);
  let finishRead!: (value: ArtifactView) => void;
  vi.mocked(api).mockReturnValue(
    new Promise((resolve) => {
      finishRead = resolve;
    }),
  );
  const scope = {
    chatId: "c1",
    messageId: "m1",
    artifacts: [] as ArtifactView[],
    readOnly: false,
    writing: false,
    tool: "pages/fetch_page",
    onKept: vi.fn(),
  };
  const props = {
    scope,
    opened: { kind: "markdown" as const, language: "md", original, title: "Notes" },
    onClose() {},
  };
  const view = render(<ArtifactDialog {...props} />);
  fireEvent.click(screen.getByRole("tab", { name: "Source" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "edited tool result" } });
  fireEvent.click(screen.getByRole("button", { name: "Save as revision 2" }));
  await waitFor(() => expect(scope.onKept).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByLabelText("Revision"), { target: { value: "1" } });
  expect(screen.getByRole("textbox")).toHaveValue(original);

  // The parent refresh arrives after the person has already chosen revision one.
  view.rerender(<ArtifactDialog {...props} scope={{ ...scope, artifacts: [saved] }} />);
  await act(async () => {
    finishRead(saved);
  });
  expect(screen.getByLabelText("Revision")).toHaveValue("1");
  expect(screen.getByRole("textbox")).toHaveValue(original);
  expect(screen.getByLabelText("Revision").querySelector('option[value="1"]')).toHaveTextContent(
    "from fetch_page's result",
  );
});

it("finishes loading when a retained draft saves before the initial revision read", async () => {
  vi.mocked(api).mockReturnValue(new Promise(() => {}));
  const saved = {
    ...artifact,
    revisions: 4,
    revisionList: [
      ...artifact.revisionList,
      { number: 4, author: "person", size: 10, createdAt: 4 },
    ],
  };
  vi.mocked(post).mockResolvedValue(saved);
  keepDraft(`m1:markdown:${digest(original)}`, { text: "retained draft", base: 3 });
  show();
  fireEvent.click(screen.getByRole("tab", { name: "Source" }));
  fireEvent.click(screen.getByRole("button", { name: /Save as revision/ }));
  await waitFor(() => expect(screen.getByLabelText("Revision")).toHaveValue("4"));
  expect(screen.getByLabelText("Revision")).not.toBeDisabled();
  expect(screen.queryByText("Reading the revision…")).not.toBeInTheDocument();
});
