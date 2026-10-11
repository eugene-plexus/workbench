import { act, renderHook } from "@testing-library/react";

import { ApiError, api } from "./api";
import { broken, markdown, page, queued, reading, saved } from "../test-saved";
import { FIRST_POLL_MS, POLL_MS, RETRY_MS } from "./savedDocuments";
import type { DocumentsPage } from "./types";
import { useSavedDocuments } from "./useSavedDocuments";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(api).mockReset();
});
afterEach(() => vi.useRealTimers());

/** Lets time pass, and every answer that was waiting arrive. */
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const open = () => renderHook(() => useSavedDocuments());

describe("reading the documents", () => {
  it("reads them when the screen opens, and says what it read", async () => {
    vi.mocked(api).mockResolvedValue(page([saved()]));
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledExactlyOnceWith("/api/documents");
    expect(view.result.current.data?.documents).toHaveLength(1);
    expect(view.result.current.blocked).toBeNull();
    expect(view.result.current.problem).toBeNull();
  });

  it("reads them again when asked", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([saved()]))
      .mockResolvedValueOnce(page([saved(), markdown()]));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data?.documents).toHaveLength(2);
  });
});

describe("watching documents being read", () => {
  it("asks nothing more while every document is read", async () => {
    vi.mocked(api).mockResolvedValue(page([saved(), broken()]));
    open();
    await wait(0);
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("asks again soon after a document turns up unread, then each second, and stops once none is", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([queued()]))
      .mockResolvedValueOnce(page([reading()]))
      .mockResolvedValue(page([saved({ id: "d2", name: "notes.md" })]));
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(FIRST_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.data?.documents[0]?.state).toBe("ready");
    // Nothing is being read: it is quiet, however long it waits.
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("keeps asking while a document can be found by words and is not ready yet", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([saved({ state: "searchable" })]))
      .mockResolvedValueOnce(page([saved({ state: "searchable" })]))
      .mockResolvedValue(page([saved()]));
    open();
    await wait(0);
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(60_000);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("waits longer after a read that failed, and carries on when it works again", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([queued()]))
      .mockRejectedValueOnce(new Error("Workbench answered 502."))
      .mockResolvedValue(page([saved()]));
    const view = open();
    await wait(0);
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    // What was shown stays, with why it could not be updated beside it.
    expect(view.result.current.problem).toBe("Workbench answered 502.");
    expect(view.result.current.data?.documents[0]?.state).toBe("queued");
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(RETRY_MS - POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.problem).toBeNull();
    expect(view.result.current.data?.documents[0]?.state).toBe("ready");
  });

  it("stops when the screen goes", async () => {
    vi.mocked(api).mockResolvedValue(page([queued()]));
    const view = open();
    await wait(0);
    view.unmount();
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(1);
  });

  it("does not ask again when the screen goes while a read is on its way", async () => {
    let arrive: (value: DocumentsPage) => void = () => undefined;
    vi.mocked(api)
      .mockResolvedValueOnce(page([queued()]))
      .mockReturnValueOnce(new Promise<DocumentsPage>((resolve) => (arrive = resolve)) as never)
      .mockResolvedValue(page([queued()]));
    const view = open();
    await wait(0);
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    view.unmount();
    await act(async () => arrive(page([queued()])));
    await wait(30_000);
    expect(api).toHaveBeenCalledTimes(2);
  });
});

describe("a search that cannot be used", () => {
  const damaged = () =>
    new ApiError("The search index for your documents is damaged. Your files are safe.", 503, {
      message: "The search index for your documents is damaged. Your files are safe.",
      code: "index_damaged",
      canRebuild: true,
    });

  it("shows the server's words and what can be done, and nothing that may be out of date", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([saved()]))
      .mockRejectedValueOnce(damaged());
    const view = open();
    await wait(0);
    expect(view.result.current.data).not.toBeNull();
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data).toBeNull();
    expect(view.result.current.blocked).toEqual({
      message: "The search index for your documents is damaged. Your files are safe.",
      code: "index_damaged",
      canRebuild: true,
    });
    expect(view.result.current.problem).toBeNull();
  });

  it("is let go of when the documents can be read again", async () => {
    vi.mocked(api)
      .mockRejectedValueOnce(damaged())
      .mockResolvedValue(page([queued()]));
    const view = open();
    await wait(0);
    expect(view.result.current.blocked?.canRebuild).toBe(true);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.blocked).toBeNull();
    expect(view.result.current.data?.documents).toHaveLength(1);
  });

  it("is not a failure to read: any other error keeps what is shown", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([saved()]))
      .mockRejectedValueOnce(new ApiError("Down.", 500));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.blocked).toBeNull();
    expect(view.result.current.problem).toBe("Down.");
    expect(view.result.current.data).not.toBeNull();
  });
});

describe("answers that arrive out of order", () => {
  it("drops one that a later read overtook", async () => {
    let slow: (value: DocumentsPage) => void = () => undefined;
    vi.mocked(api)
      .mockReturnValueOnce(new Promise<DocumentsPage>((resolve) => (slow = resolve)) as never)
      .mockResolvedValueOnce(page([saved(), markdown()]));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data?.documents).toHaveLength(2);
    await act(async () => slow(page([])));
    expect(view.result.current.data?.documents).toHaveLength(2);
  });
});
