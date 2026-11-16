import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { CollectionDetail } from "../lib/team";
import type { ProjectDetail } from "../lib/types";
import { SharedProject } from "./SharedProject";
import { TeamCollections } from "./TeamCollections";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

function handbook(over: Partial<CollectionDetail> = {}): CollectionDetail {
  return {
    id: "hand",
    name: "Handbook",
    kind: "install",
    projectId: null,
    role: "contribute",
    documents: 1,
    notes: 0,
    proposed: 1,
    outsideModels: false,
    documentList: [
      {
        id: "d1",
        kind: "document",
        state: "active",
        title: "rules.md",
        addedBy: "Ada",
        addedAt: 1,
        formerMember: false,
        document: { state: "ready", type: "markdown", bytes: 10, passages: 1, message: null },
      },
      {
        id: "d2",
        kind: "document",
        state: "active",
        title: "old.md",
        addedBy: "a former member",
        addedAt: 1,
        formerMember: true,
      },
    ],
    noteList: [],
    inbox: [
      {
        id: "n1",
        kind: "note",
        state: "proposed",
        title: "Fire drills are on Fridays.",
        addedBy: "Bo",
        addedAt: 2,
        formerMember: false,
        model: "local-model",
      },
    ],
    grants: null,
    ...over,
  };
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
});

test("a collection shows what is in it and who added it, and a proposed note waits for a person", async () => {
  let detail = handbook();
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/collections")
      return { collections: [{ ...detail, documents: 2 }], canCreate: false };
    if (path === "/api/collections/hand") return detail;
    throw new Error(`not served: ${path}`);
  });
  vi.mocked(post).mockImplementation(async () => {
    detail = handbook({ inbox: [], proposed: 0 });
    return {};
  });
  render(<TeamCollections />);
  const row = await screen.findByTestId("team-collection");
  expect(row).toHaveTextContent("Handbook");
  expect(row).toHaveTextContent("You can add to it");
  expect(within(row).getByTestId("team-waiting")).toHaveTextContent("1 waiting");
  fireEvent.click(within(row).getByRole("button", { name: /Handbook/ }));
  const items = await screen.findAllByTestId("team-item");
  expect(items[0]).toHaveTextContent("Added by Ada");
  expect(items[1]).toHaveTextContent("Added by a former member");
  const inbox = screen.getByTestId("team-inbox");
  expect(inbox).toHaveTextContent("Fire drills are on Fridays.");
  expect(inbox).toHaveTextContent("Proposed in Bo's chat by local-model");
  fireEvent.click(within(inbox).getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(screen.queryByTestId("team-inbox")).toBeNull());
  expect(post).toHaveBeenCalledWith("/api/collections/hand/inbox/n1/accept", {});
});

test("a reader is offered nothing to add, and nothing waiting", async () => {
  const detail = handbook({ role: "read", inbox: [], proposed: 0 });
  vi.mocked(api).mockImplementation(async (path: string) => {
    if (path === "/api/collections") return { collections: [detail], canCreate: false };
    if (path === "/api/collections/hand") return detail;
    throw new Error(`not served: ${path}`);
  });
  render(<TeamCollections />);
  const row = await screen.findByTestId("team-collection");
  expect(row).toHaveTextContent("You can read it");
  fireEvent.click(within(row).getByRole("button", { name: /Handbook/ }));
  await screen.findAllByTestId("team-item");
  expect(screen.queryByTestId("team-note")).toBeNull();
  expect(screen.queryByRole("button", { name: /Take .* out/ })).toBeNull();
});

test("a project shared with the person says whose it is, and shows only their chats", () => {
  const project = {
    id: "p",
    name: "Harbour",
    generalMemory: true,
    generalDocuments: true,
    createdAt: 0,
    updatedAt: 0,
    chats: 1,
    notes: 0,
    documents: 1,
    shared: { owner: "Ada", role: "read" },
    instructions: [{ id: "i", text: "Answer as a lighthouse keeper.", kind: "instruction" }],
    chatList: [{ id: "c", title: "My question" }],
    collectionId: null,
  } as unknown as ProjectDetail;
  render(
    <SharedProject project={project} header={null} onOpenChat={() => {}} onChanged={() => {}} />,
  );
  expect(screen.getByTestId("project-shared-by")).toHaveTextContent(
    "Shared by Ada. You can read it. Your chats in it are yours alone.",
  );
  expect(screen.getByTestId("project-instructions")).toHaveTextContent(
    "Answer as a lighthouse keeper.",
  );
  expect(screen.getByTestId("project-chats")).toHaveTextContent("My question");
});

test("the chat's settings say which chosen collection its next answer is sent, and why not", async () => {
  const { ChatTeam } = await import("./ChatTeam");
  vi.mocked(api).mockImplementation(async () => ({
    chosen: [
      { id: "hand", name: "Handbook" },
      { id: "price", name: "Prices" },
    ],
    available: [
      { id: "hand", name: "Handbook", role: "read" },
      { id: "price", name: "Prices", role: "read" },
    ],
    searched: [{ id: "price", name: "Prices" }],
    held: [{ id: "hand", name: "Handbook", why: "Handbook was not sent: outside model." }],
  }));
  render(<ChatTeam chatId="c" />);
  const team = await screen.findByTestId("chat-team");
  expect(team).toHaveTextContent("Handbook: Handbook was not sent: outside model.");
  expect(team).toHaveTextContent("Prices: Searched");
});

test("the documents chip names the team collections sent with the chat's own documents off", async () => {
  const { chatChipWords } = await import("../lib/savedDocuments");
  const words = chatChipWords({
    chatId: "c",
    readOnly: false,
    switch: "off",
    documentsOnly: false,
    sending: true,
    searching: { library: false, chat: false, team: true },
    team: { sent: [{ id: "hand", name: "Handbook" }], held: [] },
    reason: null,
    reasonCode: null,
    hint: null,
    model: null,
    outside: null,
    counts: {
      library: { documents: 3, passages: 9, waiting: 0 },
      chat: { documents: 0, passages: 0, waiting: 0 },
    },
    chatDocuments: [],
    budget: { limit: 1000, window: null },
  } as never);
  expect(words.label).toBe("Documents: Handbook");
  expect(words.tone).toBe("on");
});
