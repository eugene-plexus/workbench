import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, fileOf, post } from "../lib/api";
import type { DataExport } from "../lib/removal";
import { YourData } from "./YourData";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  post: vi.fn(),
  fileOf: vi.fn(),
}));

const NOW = Date.UTC(2026, 9, 21, 12, 0, 0) / 1000;

function made(over: Partial<DataExport> = {}): DataExport {
  return {
    id: "e1",
    subject: "p-ada",
    subjectName: "Ada",
    askerName: "Ada",
    scope: "self",
    state: "ready",
    holds: { chats: 2 },
    size: 2048,
    error: null,
    createdAt: NOW,
    expiresAt: NOW + 86_400,
    ...over,
  };
}

let listed: DataExport[];

beforeEach(() => {
  listed = [];
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/exports") return { exports: listed };
    throw new Error(`not served: ${path}`);
  }) as never);
});

afterEach(() => vi.clearAllMocks());

test("anyone downloads their own data", async () => {
  vi.mocked(post).mockImplementation((async () => {
    listed = [made()];
    return { export: made() };
  }) as never);
  vi.mocked(fileOf).mockResolvedValue({ blob: new Blob(["zip"]), name: "workbench-my-data.zip" });
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  render(<YourData />);
  fireEvent.click(await screen.findByTestId("export-mine"));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/api/me/export"));
  fireEvent.click(await screen.findByTestId("export-download"));
  await waitFor(() => expect(fileOf).toHaveBeenCalledWith("/api/exports/e1"));
});

test("a download that failed says why", async () => {
  listed = [made({ state: "failed", error: "The download could not be made: No space left." })];
  render(<YourData />);
  expect(await screen.findByText("The download could not be made: No space left.")).toBeTruthy();
});
