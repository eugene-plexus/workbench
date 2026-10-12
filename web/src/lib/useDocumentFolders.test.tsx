import { act, renderHook } from "@testing-library/react";

import { ApiError, api } from "./api";
import { IDLE_POLL_MS, POLL_MS, RETRY_MS } from "./savedDocuments";
import type { DocumentFolders } from "./types";
import { useDocumentFolders } from "./useDocumentFolders";
import {
  folder,
  foldersPage,
  NOW,
  readingFolder,
  syncingFolder,
  waitingFolder,
} from "../test-folders";

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
}));

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  vi.mocked(api).mockReset();
});
afterEach(() => vi.useRealTimers());

/** Lets time pass, and every answer that was waiting arrive. */
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const open = () => renderHook(() => useDocumentFolders());

describe("reading the folders", () => {
  it("reads them when the screen opens, and says what it read", async () => {
    vi.mocked(api).mockResolvedValue(foldersPage([folder()]));
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledExactlyOnceWith("/api/documents/folders");
    expect(view.result.current.data?.folders).toHaveLength(1);
    expect(view.result.current.problem).toBeNull();
  });

  it("reads them again when asked", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(foldersPage([folder()]))
      .mockResolvedValueOnce(foldersPage([folder(), folder({ id: "g2", name: "More" })]));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data?.folders).toHaveLength(2);
  });

  it("is not a list of folders when the answer is not one: it says so, and shows none", async () => {
    vi.mocked(api).mockResolvedValue({ documents: [], totals: {} } as never);
    const view = open();
    await wait(0);
    expect(view.result.current.data).toBeNull();
    expect(view.result.current.problem).toBe(
      "Workbench's answer about your folders was not one this page understands.",
    );
  });

  it("says why when the first read failed, and does not ask again by itself: Try again does", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("Workbench answered 502."));
    const view = open();
    await wait(0);
    expect(view.result.current.data).toBeNull();
    expect(view.result.current.problem).toBe("Workbench answered 502.");
    await wait(IDLE_POLL_MS * 2);
    expect(api).toHaveBeenCalledTimes(1);
    vi.mocked(api).mockResolvedValue(foldersPage([folder()]));
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data?.folders).toHaveLength(1);
    expect(view.result.current.problem).toBeNull();
  });

  it("says a refusal in the server's own sentence", async () => {
    vi.mocked(api).mockRejectedValueOnce(
      new ApiError("The owner of this Workbench has turned off documents.", 403, {
        message: "The owner of this Workbench has turned off documents.",
      }),
    );
    const view = open();
    await wait(0);
    expect(view.result.current.problem).toBe(
      "The owner of this Workbench has turned off documents.",
    );
  });
});

describe("watching folders change", () => {
  it("asks each second while a check is under way, and each half minute once nothing is going on", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(foldersPage([syncingFolder()]))
      .mockResolvedValueOnce(foldersPage([syncingFolder({ done: 3_000 })]))
      .mockResolvedValue(foldersPage([folder({ nextAt: NOW + 3_600 })]));
    const view = open();
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    expect(view.result.current.data?.folders[0]?.syncing?.done).toBe(3_000);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.data?.folders[0]?.state).toBe("synced");
    // Nothing is going on: it is quiet, and looks again only each half minute.
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(4);
  });

  it("keeps asking each second while files are still being read, so the counts are seen to move", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(foldersPage([readingFolder()]))
      .mockResolvedValueOnce(foldersPage([readingFolder()]))
      .mockResolvedValue(foldersPage([folder({ nextAt: NOW + 3_600 })]));
    open();
    await wait(0);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("asks each second while a folder waits for its first look", async () => {
    vi.mocked(api).mockResolvedValue(foldersPage([waitingFolder()]));
    open();
    await wait(0);
    // One second at a time: the screen draws between two reads, and that is what asks the next.
    for (let n = 2; n <= 4; n++) {
      await wait(POLL_MS);
      expect(api).toHaveBeenCalledTimes(n);
    }
  });

  it("asks soon after a check is due, so the screen sees it begin, and not before", async () => {
    vi.mocked(api).mockResolvedValue(foldersPage([folder({ nextAt: NOW + 8 })]));
    open();
    await wait(0);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(9_499);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("is quiet for a folder whose next check is far off: the half-minute look only", async () => {
    vi.mocked(api).mockResolvedValue(foldersPage([folder({ nextAt: NOW + 600 })]));
    open();
    await wait(0);
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("keeps what it showed when a read fails while something is going on, says why, and asks again after a pause", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(foldersPage([syncingFolder()]))
      .mockRejectedValueOnce(new Error("Workbench answered 502."))
      .mockResolvedValue(foldersPage([folder({ nextAt: NOW + 3_600 })]));
    const view = open();
    await wait(0);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    expect(view.result.current.data?.folders[0]?.state).toBe("syncing");
    expect(view.result.current.problem).toBe("Workbench answered 502.");
    await wait(RETRY_MS - 1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.data?.folders[0]?.state).toBe("synced");
    expect(view.result.current.problem).toBeNull();
  });

  it("stops asking when the screen is closed", async () => {
    vi.mocked(api).mockResolvedValue(foldersPage([syncingFolder()]));
    const view = open();
    await wait(0);
    view.unmount();
    await wait(POLL_MS * 5);
    expect(api).toHaveBeenCalledTimes(1);
  });
});

describe("a read that is overtaken", () => {
  it("never puts back what a newer read replaced", async () => {
    let slow: (page: DocumentFolders) => void = () => undefined;
    vi.mocked(api)
      .mockImplementationOnce(() => new Promise<DocumentFolders>((done) => (slow = done)) as never)
      .mockResolvedValueOnce(foldersPage([folder({ name: "Newer" })]));
    const view = open();
    await wait(0);
    await act(async () => {
      await view.result.current.reload();
    });
    expect(view.result.current.data?.folders[0]?.name).toBe("Newer");
    await act(async () => {
      slow(foldersPage([folder({ name: "Older" })]));
      await Promise.resolve();
    });
    expect(view.result.current.data?.folders[0]?.name).toBe("Newer");
  });
});
