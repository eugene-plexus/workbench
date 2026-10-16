import { act, renderHook, waitFor } from "@testing-library/react";
import { api } from "./api";
import { useAGUI, type Run } from "./agui";
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  api: vi.fn(),
}));
const saved: Run = {
  threadId: "c",
  runId: "new",
  direction: "connect",
  agentId: "agent",
  status: "done",
  cursor: 3,
  messages: [],
  state: {},
  answerId: "a",
  outcome: { type: "success" },
};
beforeEach(() => {
  vi.mocked(api).mockReset();
  sessionStorage.clear();
});
afterEach(() => vi.unstubAllGlobals());
it("ignores a stale snapshot arriving after a newer read", async () => {
  let first: (value: Run) => void = () => {},
    second: (value: Run) => void = () => {};
  vi.mocked(api)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          first = resolve as typeof first;
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          second = resolve as typeof second;
        }),
    );
  const { result } = renderHook(() => useAGUI("c", true, "ada"));
  let reload: Promise<void>;
  act(() => {
    reload = result.current.reload();
  });
  await act(async () => {
    second(saved);
    await reload;
  });
  expect(result.current.run?.runId).toBe("new");
  await act(async () => first({ ...saved, runId: "old" }));
  expect(result.current.run?.runId).toBe("new");
});
it("persists an ambiguous start and recovers by reading without resubmitting", async () => {
  vi.mocked(api).mockResolvedValue(null);
  const network = vi.fn().mockRejectedValue(new Error("Connection lost"));
  vi.stubGlobal("fetch", network);
  const { result } = renderHook(() => useAGUI("c", true, "ada"));
  await waitFor(() => expect(result.current.loaded).toBe(true));
  await act(async () => {
    await expect(result.current.start("agent", "hello")).rejects.toThrow("Connection lost");
  });
  const pending = sessionStorage.getItem("agui-start:ada:c");
  expect(pending).toBeTruthy();
  expect(result.current.unclear).toBe(true);
  await expect(result.current.start("agent", "again")).rejects.toThrow("saved run");
  vi.mocked(api).mockResolvedValue({ ...saved, runId: pending });
  await act(() => result.current.reload());
  expect(result.current.unclear).toBe(false);
  expect(network).toHaveBeenCalledOnce();
  expect(sessionStorage.getItem("agui-start:ada:c")).toBeNull();
});
