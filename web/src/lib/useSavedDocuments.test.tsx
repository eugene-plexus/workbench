import { act, renderHook } from "@testing-library/react";

import { ApiError, api } from "./api";
import {
  adding,
  broken,
  byMeaning,
  addingView,
  limited,
  markdown,
  page,
  queued,
  reading,
  saved,
  waiting,
  wordsOnlyDoc,
} from "../test-saved";
import { FIRST_POLL_MS, IDLE_POLL_MS, POLL_MS, RETRY_MS, WAITING_POLL_MS } from "./savedDocuments";
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
  it("asks only for the half-minute look while every document is read", async () => {
    vi.mocked(api).mockResolvedValue(page([saved(), broken()]));
    open();
    await wait(0);
    // Nothing is being read, so nothing is asked quickly: the header is a live look at the search
    // model, and is read again each half minute for as long as the screen is open.
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(IDLE_POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
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
    // Nothing is being read: it is quiet, and looks again only each half minute.
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(4);
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
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(4);
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

describe("watching meaning being added", () => {
  const working = (embedded: number) =>
    byMeaning({
      adding: addingView({
        state: "working",
        message: `Adding meaning: ${embedded} of 5 passages, about 14 minutes left.`,
        embedded,
        owed: 5 - embedded,
      }),
    });

  it("asks soon, then each second, while meaning is being added, and quietly once it is added", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([adding()], { search: working(2) }))
      .mockResolvedValueOnce(page([adding()], { search: working(4) }))
      .mockResolvedValue(page([saved({ meaning: { ...adding().meaning!, state: "ready" } })]));
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
    expect(view.result.current.data?.documents[0]?.meaning?.state).toBe("ready");
    // Added: it is quiet again, but for the half-minute look.
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(4);
  });

  it("asks every few seconds, not each second, while meaning waits for the search model", async () => {
    vi.mocked(api).mockResolvedValue(
      page([waiting()], {
        search: byMeaning({
          code: "not_running",
          meaning: false,
          adding: addingView({ state: "waiting", message: "Meaning is waiting.", owed: 5 }),
        }),
      }),
    );
    open();
    await wait(0);
    await wait(WAITING_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(WAITING_POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("asks every few seconds while the key's limit holds meaning back, and each second once it eases", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(
        page([limited()], {
          search: byMeaning({
            adding: addingView({ state: "limited", message: "Slow.", owed: 3 }),
          }),
        }),
      )
      .mockResolvedValue(page([adding()], { search: working(3) }));
    open();
    await wait(0);
    await wait(WAITING_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
    // Working again: soon, as for any change, and then each second.
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(4);
  });

  it("asks soon for a document that was read before any search model ran, once one is running, which takes it up", async () => {
    vi.mocked(api).mockResolvedValue(page([wordsOnlyDoc()], { search: byMeaning() }));
    open();
    await wait(0);
    await wait(FIRST_POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    await wait(POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("does not ask each second for such a document while no search model runs: it waits for the half-minute look", async () => {
    vi.mocked(api).mockResolvedValue(page([wordsOnlyDoc()]));
    open();
    await wait(0);
    await wait(IDLE_POLL_MS - 1);
    expect(api).toHaveBeenCalledTimes(1);
    await wait(1);
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("shows a search model that was stopped, at the next look, with nothing else changed", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([saved()], { search: byMeaning() }))
      .mockResolvedValue(
        page([saved()], {
          search: byMeaning({
            code: "not_running",
            meaning: false,
            mode: "words",
            headline: "Searching by words only. No search model is running.",
          }),
        }),
      );
    const view = open();
    await wait(0);
    expect(view.result.current.data?.search.meaning).toBe(true);
    await wait(IDLE_POLL_MS);
    expect(view.result.current.data?.search.meaning).toBe(false);
    expect(view.result.current.data?.search.code).toBe("not_running");
  });

  it("goes on asking at its own pace after a read that failed while meaning waits", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce(page([waiting()], { search: byMeaning({ code: "not_running" }) }))
      .mockRejectedValueOnce(new Error("Workbench answered 502."))
      .mockResolvedValue(page([saved()]));
    const view = open();
    await wait(0);
    await wait(WAITING_POLL_MS);
    expect(api).toHaveBeenCalledTimes(2);
    expect(view.result.current.problem).toBe("Workbench answered 502.");
    await wait(WAITING_POLL_MS);
    expect(api).toHaveBeenCalledTimes(3);
    expect(view.result.current.problem).toBeNull();
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
