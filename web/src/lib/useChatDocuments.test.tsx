import { act, renderHook } from "@testing-library/react";

import { chatDocuments, held, off, owners } from "../test-saved";
import { api } from "./api";
import type { ChatDocumentsView } from "./types";
import { useChatDocuments, WAIT_MS } from "./useChatDocuments";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
});
afterEach(() => vi.useRealTimers());

const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const open = (stamp = "a") =>
  renderHook(({ at }) => useChatDocuments("c1", at), { initialProps: { at: stamp } });

/** A chat whose documents are still being read, so nothing can be searched yet. */
const reading = (waiting: number): ChatDocumentsView =>
  chatDocuments({
    counts: {
      library: { documents: 0, passages: 0, waiting },
      chat: { documents: 0, passages: 0, waiting: 0 },
    },
  });

describe("a chat's documents", () => {
  it("are read when the chat opens, for that chat", async () => {
    vi.mocked(api).mockResolvedValue(chatDocuments());
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledExactlyOnceWith("/api/chats/c1/documents");
    expect(view.result.current.view).toEqual(chatDocuments());
    expect(view.result.current.error).toBeNull();
  });

  it("are read again when the stamp changes (the model, or a setting that decides what is searched)", async () => {
    vi.mocked(api).mockResolvedValueOnce(chatDocuments()).mockResolvedValueOnce(off());
    const view = open("model-a");
    await wait(0);
    view.rerender({ at: "model-a" });
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
    view.rerender({ at: "model-b|on" });
    await wait(0);
    expect(api).toHaveBeenCalledTimes(2);
    expect(view.result.current.view).toEqual(off());
  });

  it("are read again when the chat says so", async () => {
    vi.mocked(api).mockResolvedValueOnce(chatDocuments()).mockResolvedValueOnce(off());
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.view).toEqual(off());
  });

  it("show the owner reading someone's chat only whether they were on", async () => {
    vi.mocked(api).mockResolvedValue(owners());
    const view = open();
    await wait(0);
    expect(view.result.current.view).toEqual(owners());
    // Nothing of the person's documents to wait for.
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("are not a view when the server answered with something else", async () => {
    vi.mocked(api).mockResolvedValue({ chatSwitch: "on", counts: { notes: 3 } } as never);
    const view = open();
    await wait(0);
    expect(view.result.current.view).toBeNull();
    expect(view.result.current.error).toBeNull();
    vi.mocked(api).mockResolvedValue(undefined as never);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.view).toBeNull();
  });

  it("take a view the server just answered with as the one shown", async () => {
    vi.mocked(api).mockResolvedValue(chatDocuments());
    const view = open();
    await wait(0);
    act(() => view.result.current.adopt(off()));
    expect(view.result.current.view).toEqual(off());
  });

  it("take a view the server just answered with over a read that is still on its way", async () => {
    let slow: (value: ChatDocumentsView) => void = () => undefined;
    vi.mocked(api)
      .mockResolvedValueOnce(chatDocuments())
      .mockReturnValueOnce(new Promise<ChatDocumentsView>((resolve) => (slow = resolve)) as never);
    const view = open();
    await wait(0);
    // A read begins, and before it answers a setting is made and the view that came with it taken.
    let again: Promise<void> = Promise.resolve();
    act(() => {
      again = view.result.current.reload();
    });
    act(() => view.result.current.adopt(off()));
    await act(async () => {
      slow(chatDocuments());
      await again;
    });
    expect(view.result.current.view).toEqual(off());
  });

  it("take a view the server just answered with as the end of an error", async () => {
    vi.mocked(api).mockResolvedValueOnce(chatDocuments()).mockRejectedValueOnce(new Error("Down."));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.error).toBe("Down.");
    act(() => view.result.current.adopt(off()));
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.view).toEqual(off());
  });

  it("are asked for at the chat's own address, whatever its id has in it", async () => {
    vi.mocked(api).mockResolvedValue(chatDocuments());
    renderHook(() => useChatDocuments("a/b c", "a"));
    await wait(0);
    expect(api).toHaveBeenCalledExactlyOnceWith("/api/chats/a%2Fb%20c/documents");
  });
});

describe("documents still being read", () => {
  it("are asked about again every two seconds until none is, and not again", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(reading(2))
      .mockResolvedValueOnce(reading(1))
      .mockResolvedValue(chatDocuments());
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(WAIT_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(WAIT_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.view).toEqual(chatDocuments());
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("count a chat's own files too", async () => {
    vi.mocked(api).mockResolvedValue(
      chatDocuments({
        counts: {
          library: { documents: 3, passages: 9, waiting: 0 },
          chat: { documents: 0, passages: 0, waiting: 1 },
        },
      }),
    );
    open();
    await wait(0);
    await wait(WAIT_MS);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("are not waited for when the chat sends nothing of them", async () => {
    vi.mocked(api).mockResolvedValue(held("install_off", "The owner turned them off."));
    open();
    await wait(0);
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("keep being asked about after a read that failed", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(reading(2))
      .mockRejectedValueOnce(new Error("Workbench answered 502."))
      .mockResolvedValue(chatDocuments());
    const view = open();
    await wait(0);
    await wait(WAIT_MS);
    // The view already shown stays, and why it could not be updated is said beside it.
    expect(view.result.current.error).toBe("Workbench answered 502.");
    expect(view.result.current.view).toEqual(reading(2));
    await wait(WAIT_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.error).toBeNull();
    expect(view.result.current.view).toEqual(chatDocuments());
  });

  it("stop when the chat is left", async () => {
    vi.mocked(api).mockResolvedValue(reading(2));
    const view = open();
    await wait(0);
    view.unmount();
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });
});

describe("answers that arrive out of order", () => {
  it("never put back what a newer read replaced", async () => {
    let slow: (value: ChatDocumentsView) => void = () => undefined;
    vi.mocked(api)
      .mockReturnValueOnce(new Promise<ChatDocumentsView>((resolve) => (slow = resolve)) as never)
      .mockResolvedValueOnce(off());
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.view).toEqual(off());
    await act(async () => slow(chatDocuments()));
    expect(view.result.current.view).toEqual(off());
  });
});
