import { act, renderHook, waitFor } from "@testing-library/react";

import { api } from "./api";
import { watch } from "./events";
import type { ChatDetail, ChatEvent, Message } from "./types";
import { applyEvent, useChat } from "./useChat";

vi.mock("./api", () => ({ api: vi.fn() }));
vi.mock("./events", () => ({ watch: vi.fn() }));

const detail: ChatDetail = {
  chat: {
    id: "c",
    title: "t",
    model: "m",
    search: false,
    settings: {},
    createdAt: 0,
    updatedAt: 0,
    running: false,
    readOnly: false,
  },
  messages: [],
  ownerName: null,
};

function watched(): (event: ChatEvent) => void {
  const [, onEvent] = vi.mocked(watch).mock.calls.at(-1)!;
  return onEvent;
}

beforeEach(() => {
  vi.mocked(api).mockReset().mockResolvedValue(detail);
  vi.mocked(watch)
    .mockReset()
    .mockReturnValue({ close: () => undefined });
});

it("reloads every tab when another version is shown", async () => {
  renderHook(() => useChat("c"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  act(() => watched()({ type: "path" }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
  expect(api).toHaveBeenLastCalledWith("/api/chats/c");
});

it("looks through another branch, and keeps looking there on a reload", async () => {
  const { result } = renderHook(() => useChat("c"));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
  act(() => result.current.look("a 1"));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith("/api/chats/c?via=a%201"));
  act(() => watched()({ type: "reload" }));
  await waitFor(() => expect(api).toHaveBeenCalledTimes(3));
  expect(api).toHaveBeenLastCalledWith("/api/chats/c?via=a%201");
});

it("keeps a message's place among its versions when its answer finishes", () => {
  const running = {
    id: "a",
    role: "assistant",
    status: "running",
    versions: { index: 2, count: 2, ids: ["x", "a"] },
  } as Message;
  const done = { ...running, status: "done", versions: undefined } as Message;
  const next = applyEvent({ ...detail, messages: [running] }, { type: "done", message: done });
  expect(next.detail.messages[0]!.versions).toEqual(running.versions);
});
