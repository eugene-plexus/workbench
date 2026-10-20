import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { MemoryNote, MemoryPrefs, ToolCall } from "../lib/types";
import { LearnedReceipts, RecentlyLearned, RememberReceipt, WritePolicy } from "./MemoryLearned";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
}));

afterEach(() => vi.clearAllMocks());

function learned(over: Partial<MemoryNote> = {}): MemoryNote {
  return {
    id: "n1",
    text: "Works as a nurse.",
    kind: "fact",
    pinned: false,
    always: false,
    source: "suggested",
    sourceChatId: "c1",
    sourceMessageId: "m1",
    createdAt: Date.UTC(2026, 9, 19) / 1000,
    updatedAt: Date.UTC(2026, 9, 19) / 1000,
    lastUsedAt: null,
    useCount: 0,
    status: "active",
    tags: [],
    bodyChars: 0,
    validFrom: null,
    supersededAt: null,
    supersededBy: null,
    model: "gemma-4-e4b",
    policy: "standard",
    auto: true,
    seen: false,
    ...over,
  };
}

test("Recently learned marks what it shows as seen, and Undo says what came back", async () => {
  vi.mocked(post).mockImplementation((async (path: string) => {
    if (path === "/api/memory/learned/seen") return { seen: 1 };
    return { removed: ["n1"], restored: ["old"] };
  }) as never);
  const changed = vi.fn();
  render(<RecentlyLearned notes={[learned()]} onChanged={changed} />);
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/memory/learned/seen", { ids: ["n1"] }),
  );
  expect(screen.getByTestId("memory-learned-note").textContent).toContain("Kept by gemma-4-e4b");
  fireEvent.click(screen.getByTestId("memory-learned-undo"));
  expect(await screen.findByText(/The note it replaced is back/)).toBeTruthy();
  expect(changed).toHaveBeenCalled();
});

test("removing what a model kept names how many first", async () => {
  vi.mocked(post).mockResolvedValue({ seen: 0 } as never);
  vi.mocked(api).mockResolvedValue({ count: 4 } as never);
  render(<RecentlyLearned notes={[learned({ seen: true })]} onChanged={() => undefined} />);
  fireEvent.click(screen.getByText("Remove…"));
  expect((await screen.findByTestId("memory-learned-removal-count")).textContent).toContain(
    "This removes 4 notes",
  );
});

test("the receipt of a kept note offers Undo; an offered one says where it waits", () => {
  const call = {
    id: "c",
    serverId: "memory-write",
    serverName: "Memory",
    tool: "remember",
    arguments: {},
    status: "done",
    result: "Kept.",
    note: "n1",
    noteText: "Works as a nurse.",
    kept: true,
  } as ToolCall;
  const { rerender } = render(<RememberReceipt call={call} readOnly={false} />);
  expect(screen.getByTestId("memory-receipt").textContent).toContain(
    "Remembered: Works as a nurse.",
  );
  expect(screen.getByTestId("memory-receipt-undo")).toBeTruthy();
  rerender(<RememberReceipt call={{ ...call, kept: false }} readOnly={false} />);
  expect(screen.getByTestId("memory-receipt").textContent).toContain("Suggested for your memory");
  rerender(<RememberReceipt call={{ ...call, removed: true }} readOnly={false} />);
  expect(screen.getByTestId("memory-receipt").textContent).toBe("You removed this note.");
});

test("a note the pass kept shows in the chat with Undo", async () => {
  vi.mocked(post).mockResolvedValue({ removed: ["n1"], restored: [] } as never);
  render(<LearnedReceipts notes={[{ id: "n1", text: "Works as a nurse.", model: "m" }]} />);
  fireEvent.click(screen.getByText("Undo"));
  await waitFor(() => expect(screen.queryByTestId("memory-receipt")).toBeNull());
});

test("the write policy in effect, whose it is, and what it holds back", () => {
  const prefs = {
    writePolicy: null,
    policyChoices: ["strict", "standard"],
    policyPeople: "stricter",
    effective: {
      policy: {
        name: "strict",
        rows: {},
        allows: [],
        holdsBack: ["No model keeps notes by itself."],
        setBy: "owner",
        owners: "strict",
      },
    },
  } as unknown as MemoryPrefs;
  const chose = vi.fn();
  render(<WritePolicy prefs={prefs} busy={false} onChoose={chose} />);
  expect(screen.getByTestId("memory-policy-effective").textContent).toBe(
    "In effect: Strict. The owner of this Workbench set this.",
  );
  expect(screen.getByTestId("memory-policy-differs").textContent).toContain("No model keeps");
  fireEvent.change(screen.getByTestId("memory-policy-choice"), { target: { value: "strict" } });
  expect(chose).toHaveBeenCalledWith("strict");
});
