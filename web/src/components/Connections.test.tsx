import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, del, post } from "../lib/api";
import { type Connection, type ConnectionsPage, Connections } from "./Connections";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  post: vi.fn(),
}));

const NOW = Date.UTC(2026, 9, 18, 15, 0, 0) / 1000;

function connection(over: Partial<Connection> = {}): Connection {
  return {
    id: "c1",
    name: "Claude Code on my laptop",
    rights: { notes: true, documents: false, suggest: false },
    createdAt: NOW,
    expiresAt: NOW + 90 * 86400,
    expired: false,
    lastUsedAt: null,
    callsToday: 0,
    ...over,
  };
}

function served(over: Partial<ConnectionsPage> = {}): ConnectionsPage {
  return {
    enabled: true,
    notesAllowed: true,
    documentsAllowed: true,
    suggestionsAllowed: true,
    address: "http://192.168.1.5:8190/mcp",
    limit: 10,
    days: [90, 30, 365],
    connections: [],
    ...over,
  };
}

let page: ConnectionsPage;

beforeEach(() => {
  page = served();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/connections") return page;
    throw new Error(`not served: ${path}`);
  }) as never);
});

afterEach(() => vi.clearAllMocks());

test("making one shows the token once, with the address, and says where the text may go", async () => {
  const made = connection({ rights: { notes: true, documents: true, suggest: false } });
  vi.mocked(post).mockImplementation((async (path: string, body: unknown) => {
    expect(path).toBe("/api/connections");
    expect(body).toEqual({
      name: "Claude Code on my laptop",
      rights: { notes: true, documents: true, suggest: false, chats: false },
      days: 90,
    });
    page = served({ connections: [made] });
    return {
      connection: made,
      token: "wbc_secret",
      address: page.address,
      setups: {
        claudeCode: "claude mcp add ... wbc_secret",
        codex: "[mcp_servers.workbench]",
        json: "{}",
      },
    };
  }) as never);
  render(<Connections />);
  fireEvent.click(await screen.findByTestId("memory-connection-new"));
  const form = screen.getByTestId("memory-connection-form");
  expect(within(form).getByText(/can run outside your machines/)).toBeInTheDocument();
  // Suggesting is off until the person chooses it.
  expect(screen.getByTestId("memory-connection-right-suggest")).not.toBeChecked();
  fireEvent.change(screen.getByTestId("memory-connection-name"), {
    target: { value: "Claude Code on my laptop" },
  });
  fireEvent.click(screen.getByTestId("memory-connection-make"));
  const token = await screen.findByTestId("memory-connection-token");
  expect(screen.getByTestId("memory-connection-token-value")).toHaveValue("wbc_secret");
  expect(within(token).getByText(page.address)).toBeInTheDocument();
  fireEvent.click(screen.getByTestId("memory-connection-done"));
  await waitFor(() => expect(screen.queryByTestId("memory-connection-token")).toBeNull());
  expect(screen.queryByDisplayValue("wbc_secret")).toBeNull();
  expect(screen.getByText("Reads your notes · Searches your documents")).toBeInTheDocument();
});

test("revoking asks first, then removes it", async () => {
  page = served({ connections: [connection()] });
  vi.mocked(del).mockImplementation((async () => {
    page = served();
  }) as never);
  render(<Connections />);
  fireEvent.click(await screen.findByTestId("memory-connection-revoke"));
  expect(del).not.toHaveBeenCalled();
  fireEvent.click(screen.getByTestId("memory-connection-revoke-go"));
  await waitFor(() => expect(del).toHaveBeenCalledWith("/api/connections/c1"));
  await waitFor(() => expect(screen.queryByTestId("memory-connection")).toBeNull());
});

test("what the owner turned off is said, not offered", async () => {
  page = served({ enabled: false, connections: [connection({ expired: true })] });
  render(<Connections />);
  expect(await screen.findByTestId("memory-connections-off")).toBeInTheDocument();
  expect(screen.queryByTestId("memory-connection-new")).toBeNull();
  expect(screen.getByTestId("memory-connection-expired")).toBeInTheDocument();
});

test("a right the install does not allow cannot be chosen, and says why", async () => {
  page = served({ suggestionsAllowed: false });
  render(<Connections />);
  fireEvent.click(await screen.findByTestId("memory-connection-new"));
  expect(screen.getByTestId("memory-connection-right-suggest")).toBeDisabled();
  expect(screen.getByText(/turned suggested notes off/)).toBeInTheDocument();
});

test("at the limit, no new connection is offered and the reason is said", async () => {
  page = served({ limit: 1, connections: [connection()] });
  render(<Connections />);
  expect(await screen.findByTestId("memory-connection-new")).toBeDisabled();
  expect(screen.getByText(/Revoke one to make another/)).toBeInTheDocument();
});
