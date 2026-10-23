import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api, post } from "../lib/api";
import { MemoryBackfill } from "./MemoryBackfill";

vi.mock("../lib/api", () => ({ api: vi.fn(), post: vi.fn() }));

it("names a memory-off pause while keeping Stop available", async () => {
  vi.mocked(api).mockResolvedValue({
    exchanges: 3,
    chats: 1,
    tokens: 40,
    estimateSeconds: null,
    backfill: {
      state: "running",
      sent: 1,
      total: 3,
      skipped: 0,
      pauseReason: "Paused: memory is off",
    },
  });
  render(<MemoryBackfill />);
  expect(await screen.findByRole("status")).toHaveTextContent("Paused: memory is off");
  expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
});

it("shows the count and time before Send, and can stop sending", async () => {
  const preview = {
    exchanges: 1240,
    chats: 312,
    tokens: 380000,
    estimateSeconds: null,
    backfill: null,
  };
  vi.mocked(api).mockResolvedValue(preview);
  vi.mocked(post).mockResolvedValue({
    ...preview,
    backfill: { state: "running", sent: 0, total: 1240, skipped: 0 },
  });
  render(<MemoryBackfill />);
  fireEvent.click(await screen.findByRole("button", { name: "Send my past chats" }));
  expect(await screen.findByText(/1,240 exchanges from 312 chats/)).toBeInTheDocument();
  expect(screen.getByText(/Time: shown after the first 10/)).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/memory/server/backfill", {}));
  fireEvent.click(await screen.findByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("/api/memory/server/backfill", { method: "DELETE" }),
  );
});
