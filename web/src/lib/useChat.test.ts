import { act, renderHook, waitFor } from "@testing-library/react";

import { api, ApiError } from "./api";
import { watch } from "./events";
import type { ChatDetail, ChatEvent, Message } from "./types";
import { record } from "../test-saved";
import { applyEvent, useChat } from "./useChat";

vi.mock("./api", async (actual) => ({ ...(await actual<typeof import("./api")>()), api: vi.fn() }));
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

it("returns an owner's view to the current path when the viewed branch is deleted", async () => {
  const { result } = renderHook(() => useChat("c"));
  await waitFor(() => expect(result.current.detail).not.toBeNull());
  vi.mocked(api)
    .mockRejectedValueOnce(new ApiError("Gone", 404, {}))
    .mockResolvedValue(detail);
  act(() => result.current.look("gone"));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith("/api/chats/c"));
  expect(result.current.error).toBeNull();
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

describe("what searching the person's documents came to", () => {
  const found = record({
    query: "code word",
    terms: ["code", "word"],
    tokens: 290,
    passages: 1,
    documents: 1,
    found: 1,
    cited: [],
  });
  const running = (over: Partial<Message> = {}) =>
    ({
      id: "a",
      role: "assistant",
      status: "running",
      content: "",
      reasoning: "",
      sources: [],
      ...over,
    }) as Message;

  it("arrives with the passages, before the model's first word, and is kept", () => {
    const passage = { kind: "document", passage: true, name: "report.pdf", rank: 1 } as never;
    const next = applyEvent(
      { ...detail, messages: [running()] },
      { type: "delta", id: "a", sources: [passage], retrieval: found },
    );
    expect(next.detail.messages[0]!.retrieval).toEqual(found);
    expect(next.detail.messages[0]!.sources).toEqual([passage]);
    // The words that follow do not take it away.
    const words = applyEvent(next.detail, { type: "delta", id: "a", content: "The", contentAt: 0 });
    expect(words.detail.messages[0]!.retrieval).toEqual(found);
    expect(words.detail.messages[0]!.content).toBe("The");
  });

  it("keeps reasoning measurements from deltas, including explicit unavailable counts", () => {
    const first = applyEvent(
      { ...detail, messages: [running()] },
      { type: "delta", id: "a", reasoning: "Think", reasoningTokens: 12, reasoningSeconds: 0.5 },
    );
    expect(first.detail.messages[0]).toMatchObject({ reasoningTokens: 12, reasoningSeconds: 0.5 });
    const next = applyEvent(first.detail, {
      type: "delta",
      id: "a",
      reasoningTokens: null,
      reasoningSeconds: 2,
    });
    expect(next.detail.messages[0]).toMatchObject({ reasoningTokens: null, reasoningSeconds: 2 });
    const text = applyEvent(next.detail, { type: "delta", id: "a", content: "Answer" });
    expect(text.detail.messages[0]).toMatchObject({ reasoningTokens: null, reasoningSeconds: 2 });
  });

  it("can be cleared by an event that says it is none, and is left alone by one that says nothing of it", () => {
    const shown = { ...detail, messages: [running({ retrieval: found })] };
    expect(
      applyEvent(shown, { type: "delta", id: "a", retrieval: null }).detail.messages[0]!.retrieval,
    ).toBeNull();
    expect(
      applyEvent(shown, { type: "delta", id: "a", content: "x" }).detail.messages[0]!.retrieval,
    ).toEqual(found);
  });

  it("is on the answer when it is done, with which passages the answer cited", () => {
    const done = running({ status: "done", retrieval: { ...found, cited: [1] } });
    const next = applyEvent(
      { ...detail, messages: [running({ retrieval: found })] },
      { type: "done", message: done },
    );
    expect(next.detail.messages[0]!.retrieval?.cited).toEqual([1]);
  });
});
