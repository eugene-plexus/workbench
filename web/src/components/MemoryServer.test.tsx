import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api, put } from "../lib/api";
import type { ChatMemory, MemoryServerPage, MemoryTurn } from "../lib/types";
import { MemoryServer } from "./MemoryServer";
import { ThisTurn } from "./MemoryTurn";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  put: vi.fn(),
}));

type Connected = NonNullable<MemoryServerPage["server"]>;

function page(server: Partial<Connected> | null = null, over: Partial<MemoryServerPage> = {}) {
  return {
    allowed: true,
    mode: "outside",
    why: null,
    server:
      server === null
        ? null
        : {
            url: "https://memory.example.com/mcp/ada/",
            name: "Hindsight",
            hasToken: true,
            verbs: ["forget", "list", "purge", "recall", "retain"],
            missing: [],
            connectedAt: 1_760_000_000,
            lastOkAt: 1_760_000_100,
            lastError: null,
            lastErrorAt: null,
            skipped: 0,
            sent: 3,
            waiting: 0,
            forgetting: 0,
            ...server,
          },
    ...over,
  } satisfies MemoryServerPage;
}

afterEach(() => vi.mocked(api).mockReset());

it("connects a server with an address and a token, and never shows the token", async () => {
  vi.mocked(api).mockResolvedValueOnce(page());
  vi.mocked(put).mockResolvedValueOnce(page({}));
  render(<MemoryServer />);
  fireEvent.change(await screen.findByLabelText("Address"), {
    target: { value: " https://memory.example.com/mcp/ada/ " },
  });
  fireEvent.change(screen.getByLabelText("Token (optional)"), { target: { value: "s3cret" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() =>
    expect(put).toHaveBeenCalledWith("/api/memory/server", {
      url: "https://memory.example.com/mcp/ada/",
      token: "s3cret",
    }),
  );
  const box = await screen.findByTestId("memory-server-connected");
  expect(box).toHaveTextContent("Hindsight");
  expect(box).toHaveTextContent("3 exchanges sent.");
  expect(box).not.toHaveTextContent("s3cret");
  expect(screen.queryByTestId("memory-server-form")).not.toBeInTheDocument();
});

it("keeps the saved token when the same address is connected again with the box empty", async () => {
  vi.mocked(api).mockResolvedValueOnce(page({}));
  vi.mocked(put).mockResolvedValueOnce(page({}));
  render(<MemoryServer />);
  fireEvent.click(await screen.findByRole("button", { name: "Change" }));
  expect(screen.getByLabelText("Token (optional)")).toHaveAttribute(
    "placeholder",
    "A token is saved",
  );
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() =>
    expect(put).toHaveBeenCalledWith("/api/memory/server", {
      url: "https://memory.example.com/mcp/ada/",
      token: null,
    }),
  );
});

it("says what the server cannot do, when it was last not reached, and what was not sent", async () => {
  vi.mocked(api).mockResolvedValueOnce(
    page({
      missing: ["It cannot forget a chat you delete: what it kept from that chat stays there."],
      lastError: "The memory server did not answer in time.",
      lastErrorAt: 1_760_000_200,
      skipped: 1,
      forgetting: 2,
    }),
  );
  render(<MemoryServer />);
  expect(await screen.findByTestId("memory-server-missing")).toHaveTextContent(
    "cannot forget a chat you delete",
  );
  expect(screen.getByTestId("memory-server-error")).toHaveTextContent(
    "The memory server did not answer in time.",
  );
  const counts = screen.getByTestId("memory-server-counts");
  expect(counts).toHaveTextContent("2 exchanges of deleted chats waiting to be forgotten.");
  expect(counts).toHaveTextContent("Not sent for looking like a password or key: 1 turn.");
});

it("asks before disconnecting, and says nothing on the server is deleted", async () => {
  vi.mocked(api)
    .mockResolvedValueOnce(page({ forgetting: 1 }))
    .mockResolvedValueOnce(page());
  render(<MemoryServer />);
  fireEvent.click(await screen.findByTestId("memory-server-disconnect"));
  const ask = screen.getByRole("alertdialog");
  expect(ask).toHaveTextContent("Nothing on the server is deleted.");
  expect(ask).toHaveTextContent("1 exchange of deleted chats still waiting");
  expect(within(ask).getByRole("button", { name: "Keep it" })).toHaveFocus();
  fireEvent.click(screen.getByTestId("memory-server-disconnect-yes"));
  await waitFor(() =>
    expect(api).toHaveBeenLastCalledWith("/api/memory/server", { method: "DELETE" }),
  );
  expect(await screen.findByTestId("memory-server-form")).toBeInTheDocument();
});

it("holds no control where the owner does not allow servers, and says why", async () => {
  vi.mocked(api).mockResolvedValueOnce(
    page(null, { allowed: false, mode: "off", why: "Your install does not allow memory servers." }),
  );
  render(<MemoryServer />);
  expect(await screen.findByTestId("memory-server-off")).toHaveTextContent(
    "Your install does not allow memory servers.",
  );
  expect(screen.queryByTestId("memory-server-form")).not.toBeInTheDocument();
});

it("shows in a chat's drawer what the server gave the turn, and what was left out", () => {
  const turn = {
    messageId: "m1",
    tokens: 0,
    budget: 100,
    didNotFit: 0,
    notes: [],
    how: "words",
    why: null,
    from: 0,
    window: null,
    room: null,
    memoryServer: {
      name: "Hindsight",
      why: null,
      dropped: 1,
      memories: [{ text: "Ada's sister is Grace.", date: "2026-10-01" }],
    },
  } as unknown as MemoryTurn;
  const view = { turn, turns: ["m1"], relevantBudget: 100 } as unknown as ChatMemory;
  render(<ThisTurn chatId="c1" view={view} />);
  const box = screen.getByTestId("memory-turn-server");
  expect(box).toHaveTextContent("From Hindsight");
  expect(box).toHaveTextContent("1 memory sent.");
  expect(box).toHaveTextContent("Left out for looking like a password or key: 1 result.");
  expect(box).toHaveTextContent("Ada's sister is Grace. (2026-10-01)");
});

it("says why a turn was given nothing from the server", () => {
  const turn = {
    messageId: "m1",
    memoryServer: { name: "", why: "The memory server did not answer in time.", memories: [] },
  } as unknown as MemoryTurn;
  render(<ThisTurn chatId="c1" view={{ turn, turns: ["m1"] } as unknown as ChatMemory} />);
  const box = screen.getByTestId("memory-turn-server");
  expect(box).toHaveTextContent("From Your memory server");
  expect(box).toHaveTextContent("The memory server did not answer in time.");
});
