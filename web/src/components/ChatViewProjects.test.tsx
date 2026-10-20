import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, patch } from "../lib/api";
import type { Chat, ChatDetail, Me, Project } from "../lib/types";
import { ChatView } from "./ChatView";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

let detail: ChatDetail;
const update = vi.fn();
vi.mock("./Tools", () => ({ ToolSelection: () => null }));
vi.mock("./Folders", () => ({ FolderSelection: () => null }));
vi.mock("./AGUIConversation", () => ({ AGUIConversation: () => null }));
vi.mock("../lib/useChat", () => ({
  useChat: () => ({
    detail,
    update,
    progress: null,
    error: null,
    reload: vi.fn(),
    look: vi.fn(),
  }),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
  projectId: null,
};
const project = (id: string, name: string): Project => ({
  id,
  name,
  generalMemory: true,
  generalDocuments: true,
  documents: 0,
  createdAt: 1,
  updatedAt: 1,
  chats: 0,
  notes: 0,
});
const projects = [project("p1", "Garden plans"), project("p2", "Taxes")];

function view(over: { onChanged?: () => void; onOpenProject?: (id: string) => void } = {}) {
  return render(
    <ChatView
      chatId="one"
      me={me}
      models={null}
      modelsError={null}
      onChanged={over.onChanged ?? vi.fn()}
      onDeleted={vi.fn()}
      onOpenMemory={vi.fn()}
      onOpenDocuments={vi.fn()}
      onOpenProject={over.onOpenProject}
    />,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.mocked(api).mockReset();
  vi.mocked(patch).mockReset();
  update.mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/projects") return { projects };
    return undefined;
  }) as never);
  detail = { chat, messages: [], ownerName: null };
});

it("names the project in the header, as a link to its page", async () => {
  detail = { chat: { ...chat, projectId: "p1" }, messages: [], ownerName: null };
  const open = vi.fn();
  view({ onOpenProject: open });
  const link = await screen.findByRole("link", { name: "Garden plans" });
  expect(link).toHaveAttribute("href", "/projects/p1");
  fireEvent.click(link);
  expect(open).toHaveBeenCalledWith("p1");
});

it("has no project name in the header for a chat in none", () => {
  view();
  expect(screen.queryByTestId("chat-project")).toBeNull();
});

it("moves the chat to a project from its settings, then refreshes the chat and the list", async () => {
  const changed = vi.fn();
  vi.mocked(patch).mockResolvedValue({ ...chat, projectId: "p2" });
  view({ onChanged: changed });
  fireEvent.click(screen.getByRole("button", { name: "This chat's settings" }));
  const pick = await screen.findByRole("combobox", { name: "Move to project" });
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(3));
  expect(within(pick).getByRole("option", { name: "No project" })).toBeInTheDocument();
  expect(pick).toHaveValue("");
  const move = screen.getByRole("button", { name: "Move" });
  expect(move).toBeDisabled();
  fireEvent.change(pick, { target: { value: "p2" } });
  fireEvent.click(move);
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/chats/one", { projectId: "p2" }));
  expect(await screen.findByText("Moved.")).toBeInTheDocument();
  expect(update).toHaveBeenCalled();
  expect(changed).toHaveBeenCalled();
  // The projects were read again, so a count or a name is the server's.
  expect(vi.mocked(api).mock.calls.filter(([path]) => path === "/api/projects").length).toBe(2);
});

it("moves a chat out of its project with No project", async () => {
  detail = { chat: { ...chat, projectId: "p1" }, messages: [], ownerName: null };
  vi.mocked(patch).mockResolvedValue({ ...chat, projectId: null });
  view();
  fireEvent.click(screen.getByRole("button", { name: "This chat's settings" }));
  const pick = await screen.findByRole("combobox", { name: "Move to project" });
  expect(pick).toHaveValue("p1");
  fireEvent.change(pick, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Move" }));
  await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/chats/one", { projectId: null }));
});

it("says the server's reason when the move is refused", async () => {
  vi.mocked(patch).mockRejectedValue(new Error("There is no such project."));
  view();
  fireEvent.click(screen.getByRole("button", { name: "This chat's settings" }));
  const pick = await screen.findByRole("combobox", { name: "Move to project" });
  fireEvent.change(pick, { target: { value: "p1" } });
  fireEvent.click(screen.getByRole("button", { name: "Move" }));
  expect(await screen.findByTestId("chat-project-problem")).toHaveTextContent(
    "There is no such project.",
  );
  expect(screen.queryByText("Moved.")).toBeNull();
});
