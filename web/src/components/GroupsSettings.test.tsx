import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, put } from "../lib/api";
import type { GroupServersPage, GroupsPage, MemoryServerView } from "../lib/types";
import { GroupServers, YourGroups } from "./GroupsSettings";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  put: vi.fn(),
}));

const NIGHT = { id: "g-night", name: "Night shift" };
const DESK = { id: "g-desk", name: "Desk" };

function server(over: Partial<MemoryServerView> = {}): MemoryServerView {
  return {
    url: "https://memory.example.com/mcp/night/",
    name: "Hindsight",
    hasToken: true,
    verbs: ["forget", "list", "purge", "recall", "retain"],
    missing: [],
    connectedAt: 1_760_000_000,
    lastOkAt: 1_760_000_100,
    lastError: null,
    lastErrorAt: null,
    skipped: 0,
    sent: 0,
    waiting: 0,
    forgetting: 0,
    ...over,
  };
}

function yours(over: Partial<GroupsPage> = {}): GroupsPage {
  return {
    known: true,
    groups: [NIGHT],
    policies: [
      { policy: "queueBand", value: "background", setBy: [NIGHT] },
      { policy: "summaryContextPercent", value: 70, setBy: [NIGHT] },
    ],
    servers: [{ group: NIGHT, server: server() }],
    serversAllowed: true,
    why: null,
    ...over,
  };
}

function manage(over: Partial<GroupServersPage> = {}): GroupServersPage {
  return {
    allowed: true,
    mode: "outside",
    why: null,
    problem: null,
    groups: [
      { ...DESK, server: null },
      { ...NIGHT, server: server() },
    ],
    gone: [],
    ...over,
  };
}

afterEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(put).mockReset();
});

describe("your groups", () => {
  it("shows each value a group sets as set by that group, with no control to change it", async () => {
    vi.mocked(api).mockResolvedValueOnce(yours());
    render(<YourGroups />);
    const box = await screen.findByTestId("your-groups");
    expect(within(box).getByTestId("group-policy-queueBand")).toHaveTextContent(
      "Queue priority: Background. Set by your group: Night shift",
    );
    expect(within(box).getByTestId("group-policy-summaryContextPercent")).toHaveTextContent(
      "Summarise long chats at: 70%. Set by your group: Night shift",
    );
    const shared = within(box).getByTestId("group-server-g-night");
    expect(shared).toHaveTextContent("Hindsight");
    expect(shared).toHaveTextContent("Set by your group: Night shift");
    expect(within(box).queryByRole("button")).not.toBeInTheDocument();
    expect(within(box).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("says nothing for a person in no group, or for an Eugene that sends no groups", async () => {
    vi.mocked(api).mockResolvedValueOnce(yours({ groups: [], policies: [], servers: [] }));
    const { unmount } = render(<YourGroups />);
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/groups"));
    expect(screen.queryByTestId("your-groups")).not.toBeInTheDocument();
    unmount();
    vi.mocked(api).mockResolvedValueOnce(yours({ known: false }));
    render(<YourGroups />);
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("your-groups")).not.toBeInTheDocument();
  });
});

describe("the owner's group servers", () => {
  it("attaches a server to a group after saying whose chats it is for", async () => {
    vi.mocked(api).mockResolvedValueOnce(manage());
    vi.mocked(put).mockResolvedValueOnce(
      manage({
        groups: [
          { ...DESK, server: server({ url: "https://memory.example.com/desk/" }) },
          { ...NIGHT, server: server() },
        ],
      }),
    );
    render(<GroupServers />);
    const desk = await screen.findByTestId("group-servers-g-desk");
    expect(desk).toHaveTextContent("No memory server");
    fireEvent.click(within(desk).getByRole("button", { name: "Add a memory server" }));
    expect(within(desk).getByTestId("group-server-warning")).toHaveTextContent("Everyone in Desk");
    fireEvent.change(within(desk).getByLabelText("Address"), {
      target: { value: " https://memory.example.com/desk/ " },
    });
    fireEvent.click(within(desk).getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/groups/g-desk/memory-server", {
        url: "https://memory.example.com/desk/",
        token: "",
      }),
    );
    expect(await screen.findByTestId("group-servers-g-desk")).toHaveTextContent(
      "memory.example.com/desk/",
    );
  });

  it("removes a group's server after asking, and names a deleted group's server", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(manage({ gone: [{ id: "g-old", server: server() }] }))
      .mockResolvedValueOnce(
        manage({
          groups: [
            { ...DESK, server: null },
            { ...NIGHT, server: null },
          ],
        }),
      );
    render(<GroupServers />);
    expect(await screen.findByTestId("group-servers-gone")).toHaveTextContent(
      "no longer in Eugene",
    );
    const night = screen.getByTestId("group-servers-g-night");
    fireEvent.click(within(night).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(night).getByRole("button", { name: "Remove it" }));
    await waitFor(() =>
      expect(api).toHaveBeenLastCalledWith("/api/groups/g-night/memory-server", {
        method: "DELETE",
      }),
    );
  });

  it("says why when Eugene cannot list its groups", async () => {
    vi.mocked(api).mockResolvedValueOnce(
      manage({ groups: [], problem: "Workbench could not ask Eugene for its groups: 404." }),
    );
    render(<GroupServers />);
    expect(await screen.findByRole("alert")).toHaveTextContent("could not ask Eugene");
  });
});
