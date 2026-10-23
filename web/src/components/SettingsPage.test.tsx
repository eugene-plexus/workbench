import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "../lib/api";
import { page as memoryPage } from "../test-memory";
import { page as documentsPage } from "../test-saved";
import { SettingsPage } from "./SettingsPage";

// Connections and the models that learn have their own tests; here they only hold a place.
vi.mock("./Connections", () => ({
  Connections: () => (
    <section data-testid="memory-connections">
      <h2>Connections to other apps</h2>
    </section>
  ),
}));
vi.mock("./YourData", () => ({
  YourData: () => (
    <section>
      <h2>Your data</h2>
    </section>
  ),
}));
vi.mock("./RemovedPeople", () => ({
  RemovedPeople: () => (
    <section>
      <h2>Removed people</h2>
    </section>
  ),
}));
vi.mock("./GroupsSettings", () => ({
  YourGroups: () => (
    <section>
      <h2>Your groups</h2>
    </section>
  ),
  GroupServers: () => (
    <section>
      <h2>Group memory servers</h2>
    </section>
  ),
}));
vi.mock("./MemoryModels", () => ({ MemoryModels: () => null }));
vi.mock("./MemoryServer", () => ({ MemoryServer: () => null }));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  put: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/memory") return memoryPage();
    if (path === "/api/documents") return documentsPage([]);
    throw new Error(`not served: ${path}`);
  }) as never);
});

async function opened() {
  const calls = { onClose: vi.fn(), onOpenMemory: vi.fn(), onOpenDocuments: vi.fn() };
  const view = render(<SettingsPage {...calls} />);
  await waitFor(() => {
    expect(screen.queryByText("Loading your memory settings…")).toBeNull();
    expect(screen.queryByText("Loading your documents settings…")).toBeNull();
  });
  return { ...view, ...calls };
}

describe("the Settings page", () => {
  it("has the page heading, and Back to chat", async () => {
    const { onClose } = await opened();
    const heading = screen.getByRole("heading", { level: 1, name: "Settings" });
    expect(heading).toHaveAttribute("id", "view-heading");
    fireEvent.click(screen.getByTestId("settings-back"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows its sections in order: Memory, Documents, groups, Connections, Your data", async () => {
    await opened();
    const names = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent?.trim());
    expect(names).toEqual([
      "Memory",
      "Documents",
      "Your groups",
      "Connections to other apps",
      "Your data",
    ]);
    const memory = screen.getByTestId("settings-memory");
    const documents = screen.getByTestId("settings-documents");
    const connections = document.getElementById("connections")!;
    expect(memory).toHaveAttribute("id", "memory");
    expect(documents).toHaveAttribute("id", "documents");
    expect(
      memory.compareDocumentPosition(documents) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      documents.compareDocumentPosition(connections) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("shows the owner the people removed from Eugene, last", async () => {
    render(
      <SettingsPage owner onClose={vi.fn()} onOpenMemory={vi.fn()} onOpenDocuments={vi.fn()} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading your memory settings…")).toBeNull());
    const names = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent?.trim());
    expect(names.slice(-2)).toEqual(["Your data", "Removed people"]);
    expect(names).toContain("Group memory servers");
  });

  it("shows everyone their groups, and only the owner each group's memory server", async () => {
    await opened();
    const names = () =>
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent?.trim());
    expect(names()).toContain("Your groups");
    expect(names()).not.toContain("Group memory servers");
  });

  it("puts the owner's group memory servers where the console links to (#groups)", async () => {
    render(
      <SettingsPage owner onClose={vi.fn()} onOpenMemory={vi.fn()} onOpenDocuments={vi.fn()} />,
    );
    await waitFor(() => expect(screen.queryByText("Loading your memory settings…")).toBeNull());
    const target = document.getElementById("groups");
    expect(target).not.toBeNull();
    expect(target).toHaveTextContent("Group memory servers");
  });

  it("holds the controls of both settings", async () => {
    await opened();
    expect(screen.getByTestId("memory-use-switch")).toBeInTheDocument();
    expect(screen.getByTestId("documents-new-chats-switch")).toBeInTheDocument();
  });

  it("opens the Memory page and the Documents page from their sections", async () => {
    const { onOpenMemory, onOpenDocuments } = await opened();
    fireEvent.click(screen.getByTestId("settings-open-memory"));
    expect(onOpenMemory).toHaveBeenCalledOnce();
    expect(onOpenDocuments).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("settings-open-documents"));
    expect(onOpenDocuments).toHaveBeenCalledOnce();
  });

  it("scrolls to the section the address names", async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    window.history.replaceState(null, "", "/settings#documents");
    try {
      await opened();
      expect(scroll).toHaveBeenCalledOnce();
      expect(scroll.mock.contexts[0]).toBe(screen.getByTestId("settings-documents"));
    } finally {
      window.history.replaceState(null, "", "/");
      // @ts-expect-error jsdom has none, and a test that adds one takes it away again
      delete Element.prototype.scrollIntoView;
    }
  });
});
