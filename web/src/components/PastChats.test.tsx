import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { api } from "../lib/api";
import {
  atFromSearch,
  chatAt,
  type PastChatsSearch,
  type PastChatsStatus,
  statusWords,
} from "../lib/pastChats";
import { PastChats } from "./PastChats";
import { PastChatsSection } from "./PastChatsDrawer";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
}));

const IDLE: PastChatsStatus = { backfill: { running: false }, exchanges: 3, owed: 0 };

function found(over: Partial<PastChatsSearch> = {}): PastChatsSearch {
  return {
    results: [
      {
        chatId: "c1",
        messageId: "a1",
        title: "Lisbon trip",
        date: "2026-10-01",
        question: "Where are the best custard tarts?",
        answer: "Try Manteigaria.",
        via: "both",
      },
    ],
    total: 1,
    how: "meaning and words",
    why: null,
    status: IDLE,
    ...over,
  };
}

afterEach(() => vi.clearAllMocks());

test("the address and the status line say what they mean", () => {
  expect(chatAt("c1", "a1")).toBe("/chats/c1?at=a1");
  expect(atFromSearch("?at=a1")).toBe("a1");
  expect(atFromSearch("?at=../x")).toBeNull();
  expect(statusWords(IDLE)).toBeNull();
  expect(
    statusWords({ backfill: { running: true, chats: 1200, done: 340 }, exchanges: 9, owed: 0 }),
  ).toBe("Adding your past chats to search: 340 of 1,200.");
  expect(statusWords({ ...IDLE, owed: 2 })).toBe(
    "2 passages are still being added to search by meaning.",
  );
  expect(
    statusWords({ ...IDLE, owed: 2, meaning: { available: false, why: "No search model." } }),
  ).toBe("No search model.");
});

test("a search lists what matched and a result opens its chat at the exchange", async () => {
  const asked: string[] = [];
  vi.mocked(api).mockImplementation((async (path: string) => {
    asked.push(path);
    if (path === "/api/past-chats/status") return IDLE;
    return found();
  }) as never);
  const onOpen = vi.fn();
  render(<PastChats onOpen={onOpen} onClose={() => undefined} />);
  fireEvent.change(screen.getByTestId("past-chats-query"), { target: { value: "custard" } });
  fireEvent.click(screen.getByRole("button", { name: "Search" }));
  const list = await screen.findByTestId("past-chats-results");
  expect(within(list).getByText("Where are the best custard tarts?")).toBeTruthy();
  expect(asked).toContain("/api/past-chats?q=custard&when=any&order=best&offset=0");
  const link = within(list).getByRole("link", { name: "Lisbon trip" });
  expect(link.getAttribute("href")).toBe("/chats/c1?at=a1");
  fireEvent.click(link);
  expect(onOpen).toHaveBeenCalledWith("c1", "a1");
  fireEvent.change(screen.getByTestId("past-chats-when"), { target: { value: "week" } });
  await waitFor(() =>
    expect(asked).toContain("/api/past-chats?q=custard&when=week&order=best&offset=0"),
  );
});

test("the drawer's switch turns past chats on and names the exchanges a turn was sent", () => {
  const onSwitch = vi.fn();
  const state = {
    on: false,
    allowed: true,
    reason: null,
    reasonCode: null,
    hint: null,
    searchTool: true,
    budget: 1000,
  };
  const { rerender } = render(
    <PastChatsSection state={state} turn={null} busy={false} onSwitch={onSwitch} />,
  );
  expect(screen.getByTestId("past-chats-effective").textContent).toContain("Off");
  fireEvent.click(screen.getByTestId("past-chats-switch"));
  expect(onSwitch).toHaveBeenCalledWith(true);
  rerender(
    <PastChatsSection
      state={{ ...state, on: true }}
      turn={{
        tokens: 120,
        budget: 1000,
        how: "words",
        why: null,
        didNotFit: 0,
        forgotten: 1,
        exchanges: [
          { chatId: "c2", messageId: "a9", title: "Bikes", date: "2026-09-01", via: "words" },
        ],
      }}
      busy={false}
      onSwitch={onSwitch}
    />,
  );
  expect(screen.getByTestId("past-chats-turn").textContent).toContain("1 past exchange sent");
  expect(screen.getByTestId("past-chats-turn").textContent).toContain("1 chat deleted since");
  expect(screen.getByRole("link", { name: "Bikes" }).getAttribute("href")).toBe("/chats/c2?at=a9");
  rerender(
    <PastChatsSection
      state={{
        ...state,
        allowed: false,
        reason: "Your past chats were not sent.",
        hint: "You have set documents never to go to such models.",
      }}
      turn={null}
      busy={false}
      onSwitch={onSwitch}
    />,
  );
  expect(screen.getByTestId("past-chats-effective").textContent).toContain("documents never to go");
});
