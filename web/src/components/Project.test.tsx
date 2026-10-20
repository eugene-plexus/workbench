import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, del, patch, post } from "../lib/api";
import type { Chat, MemoryNote, ProjectDetail } from "../lib/types";
import { note } from "../test-memory";
import { ProjectPage } from "./Project";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

const chat = (id: string, title: string): Chat => ({
  id,
  title,
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
  projectId: "p1",
});

let held: ProjectDetail;
let notes: MemoryNote[];

beforeEach(() => {
  held = {
    id: "p1",
    name: "Garden plans",
    generalMemory: true,
    generalDocuments: true,
    createdAt: 1,
    updatedAt: 1,
    chats: 2,
    notes: 1,
    chatList: [chat("c2", "Beds for tomatoes"), chat("c1", "Compost")],
  };
  notes = [note({ id: "n1", text: "The beds face south", projectId: "p1" })];
  for (const mocked of [api, del, patch, post]) vi.mocked(mocked).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/projects/p1") return held;
    if (path.startsWith("/api/memory/notes?where=p1")) {
      return { notes, total: notes.length, offset: 0, search: null };
    }
    throw new Error(`not served: ${path}`);
  }) as never);
});

function open(
  over: { onClose?: () => void; onChanged?: () => void; onOpenChat?: () => void } = {},
) {
  const calls = {
    onClose: over.onClose ?? vi.fn(),
    onChanged: over.onChanged ?? vi.fn(),
    onOpenChat: over.onOpenChat ?? vi.fn(),
    onNewChat: vi.fn(),
  };
  return { ...render(<ProjectPage projectId="p1" {...calls} />), ...calls };
}

const loaded = () => screen.findByTestId("project-title");

it("shows the name, its chats in the server's order, and its notes", async () => {
  const { onOpenChat } = open();
  expect(await loaded()).toHaveTextContent("Garden plans");
  const chats = screen.getAllByTestId("project-chat");
  expect(chats.map((c) => c.textContent)).toEqual(["Beds for tomatoes", "Compost"]);
  fireEvent.click(chats[1]!);
  expect(onOpenChat).toHaveBeenCalledWith("c1");
  expect(within(screen.getByTestId("project-note")).getByText("The beds face south")).toBeVisible();
});

it("starts a chat in the project", async () => {
  const { onNewChat } = open();
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  expect(onNewChat).toHaveBeenCalledOnce();
});

it("says why a project did not load, in the server's words, and tries again", async () => {
  vi.mocked(api).mockRejectedValueOnce(new ApiError("There is no such project.", 404));
  open();
  expect(await screen.findByRole("alert")).toHaveTextContent("There is no such project.");
  vi.mocked(api).mockImplementation((async (path: string) =>
    path === "/api/projects/p1"
      ? held
      : { notes: [], total: 0, offset: 0, search: null }) as never);
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await loaded()).toHaveTextContent("Garden plans");
});

describe("rename", () => {
  it("saves the new name and shows what the server kept", async () => {
    const { onChanged } = open();
    await loaded();
    const box = screen.getByRole("textbox", { name: "Project name" });
    const go = screen.getByRole("button", { name: "Rename" });
    expect(go).toBeDisabled();
    fireEvent.change(box, { target: { value: "  Garden  and beds " } });
    vi.mocked(patch).mockResolvedValue({ ...held, name: "Garden and beds" });
    fireEvent.click(go);
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("/api/projects/p1", { name: "Garden  and beds" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("project-title")).toHaveTextContent("Garden and beds"),
    );
    expect(box).toHaveValue("Garden and beds");
    expect(onChanged).toHaveBeenCalled();
  });

  it("shows the server's refusal and keeps what was typed", async () => {
    open();
    await loaded();
    const box = screen.getByRole("textbox", { name: "Project name" });
    fireEvent.change(box, { target: { value: "Taxes" } });
    vi.mocked(patch).mockRejectedValue(
      new ApiError("You have a project of that name already.", 409),
    );
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(await screen.findByTestId("project-problem")).toHaveTextContent(
      "You have a project of that name already.",
    );
    expect(box).toHaveValue("Taxes");
    expect(screen.getByTestId("project-title")).toHaveTextContent("Garden plans");
  });
});

describe("Also use my general memory", () => {
  it("shows the value in effect, says what it means, and saves with PATCH", async () => {
    open();
    await loaded();
    const box = screen.getByRole("checkbox", { name: "Also use my general memory" });
    expect(box).toBeChecked();
    expect(box).toHaveAccessibleDescription(/also given your general notes/);
    vi.mocked(patch).mockResolvedValue({ ...held, generalMemory: false });
    fireEvent.click(box);
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("/api/projects/p1", { generalMemory: false }),
    );
    await waitFor(() => expect(box).not.toBeChecked());
  });

  it("stays as it was when the change is refused, and says why", async () => {
    open();
    await loaded();
    const box = screen.getByRole("checkbox", { name: "Also use my general memory" });
    vi.mocked(patch).mockRejectedValue(new ApiError("Workbench could not save that.", 500));
    fireEvent.click(box);
    expect(await screen.findByTestId("project-switch-problem")).toHaveTextContent(
      "Not changed: Workbench could not save that.",
    );
    expect(box).toBeChecked();
  });

  it("does not show the documents switch yet", async () => {
    open();
    await loaded();
    expect(screen.queryByText(/My documents/)).toBeNull();
  });
});

describe("Add a note", () => {
  it("keeps the note to the project and reads the list again", async () => {
    open();
    await loaded();
    fireEvent.change(screen.getByTestId("project-add-text"), {
      target: { value: "Water on Tuesdays" },
    });
    vi.mocked(post).mockResolvedValue({ note: note({ id: "n2" }), created: true });
    notes = [note({ id: "n2", text: "Water on Tuesdays", projectId: "p1" }), ...notes];
    fireEvent.click(screen.getByTestId("project-add"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/memory/notes", {
        text: "Water on Tuesdays",
        kind: "fact",
        projectId: "p1",
      }),
    );
    await waitFor(() => expect(screen.getAllByTestId("project-note")).toHaveLength(2));
  });

  it("shows the server's refusal", async () => {
    open();
    await loaded();
    fireEvent.change(screen.getByTestId("project-add-text"), { target: { value: "x" } });
    vi.mocked(post).mockRejectedValue(new ApiError("There is no such project.", 404));
    fireEvent.click(screen.getByTestId("project-add"));
    expect(await screen.findByTestId("project-add-problem")).toHaveTextContent(
      "There is no such project.",
    );
  });
});

describe("Delete project", () => {
  async function asked() {
    const view = open();
    await loaded();
    fireEvent.click(screen.getByTestId("project-delete"));
    return view;
  }

  it("asks one question with the counts, and the safe answer is focused", async () => {
    await asked();
    const group = screen.getByRole("group", { name: "Delete this project?" });
    expect(group).toHaveTextContent("This project has 2 chats and 1 note.");
    expect(group).toHaveTextContent("Deletes 2 chats and 1 note for good.");
    expect(group).toHaveTextContent("2 chats and 1 note stay and become general.");
    expect(screen.getByTestId("project-delete-cancel")).toHaveFocus();
    expect(del).not.toHaveBeenCalled();
  });

  it("deletes the project and its chats and notes, then goes back to the chats", async () => {
    const { onClose, onChanged } = await asked();
    vi.mocked(del).mockResolvedValue(undefined as never);
    fireEvent.click(
      screen.getByRole("button", { name: "Delete the project and its chats and notes" }),
    );
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/projects/p1?keep=false"));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onChanged).toHaveBeenCalled();
  });

  it("deletes the project and keeps its chats and notes, which become general", async () => {
    const { onClose } = await asked();
    vi.mocked(del).mockResolvedValue(undefined as never);
    fireEvent.click(
      screen.getByRole("button", { name: "Delete the project, keep its chats and notes" }),
    );
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/projects/p1?keep=true"));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });

  it("stays on the page and says the server's reason when it fails", async () => {
    const { onClose } = await asked();
    vi.mocked(del).mockRejectedValue(
      new ApiError("Workbench deleted 1 chats of this project.", 503),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Delete the project and its chats and notes" }),
    );
    expect(await screen.findByTestId("project-problem")).toHaveTextContent(
      "Workbench deleted 1 chats of this project.",
    );
    expect(onClose).not.toHaveBeenCalled();
  });

  it("backs out with Keep the project", async () => {
    await asked();
    fireEvent.click(screen.getByTestId("project-delete-cancel"));
    expect(screen.queryByRole("group", { name: "Delete this project?" })).toBeNull();
    expect(del).not.toHaveBeenCalled();
  });
});
