import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, api, post, put } from "../lib/api";
import { type LearningModel, type LearningModels, MemoryModels } from "./MemoryModels";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

function model(over: Partial<LearningModel> = {}): LearningModel {
  return {
    id: "gemma-4-e4b",
    locality: "local",
    provider: null,
    tools: true,
    listed: true,
    mode: "suggest",
    by: "measured",
    chosen: null,
    defaultMode: "suggest",
    recommendation: {
      level: "suggest",
      text: "Recommended for Suggest: measured on 2026-10-19, false memories 3%, recall 75%. Not for Keep automatically: false memories 3% of what it kept.",
      measuredOn: "2026-10-19",
      by: "eugene",
    },
    tagsInRequest: false,
    lastRun: null,
    ...over,
  };
}

function served(over: Partial<LearningModels> = {}): LearningModels {
  return {
    models: [
      model(),
      model({
        id: "cloud",
        locality: "external",
        provider: "OpenAI",
        mode: "off",
        by: "default",
        defaultMode: "off",
        recommendation: {
          level: "unmeasured",
          text: "Not measured yet.",
          measuredOn: null,
          by: null,
        },
      }),
    ],
    listingError: null,
    allowed: true,
    measuring: null,
    runsLeftToday: 3,
    requests: 369,
    ...over,
  };
}

let page: LearningModels;

beforeEach(() => {
  page = served();
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (path === "/api/memory/models") return page;
    throw new Error(`not served: ${path}`);
  }) as never);
});

afterEach(() => vi.clearAllMocks());

test("each model says what it does, whose word that is, and Eugene's recommendation", async () => {
  render(<MemoryModels />);
  const rows = await screen.findAllByTestId("memory-model");
  expect(rows).toHaveLength(2);
  expect(within(rows[0]!).getByTestId("memory-model-effective").textContent).toBe(
    "In effect: Suggest. Eugene's recommendation; you have not chosen.",
  );
  expect(within(rows[0]!).getByTestId("memory-model-recommendation").textContent).toContain(
    "Recommended for Suggest",
  );
  expect(within(rows[1]!).getByTestId("memory-model-recommendation").textContent).toBe(
    "Not measured yet.",
  );
  // Keep automatically, said unavailable while the write policy does not allow it.
  const options = within(rows[0]!).getAllByRole("option");
  expect(options.map((o) => o.textContent)).toEqual([
    "Default (Suggest)",
    "Off",
    "Suggest",
    "Keep automatically (not under your write policy)",
  ]);
  expect((options[3] as HTMLOptionElement).disabled).toBe(true);
});

test("a choice is sent for that model alone", async () => {
  vi.mocked(put).mockImplementation((async (path: string, body: unknown) => {
    expect(path).toBe("/api/memory/models/gemma-4-e4b");
    expect(body).toEqual({ mode: "off" });
    return served({ models: [model({ mode: "off", by: "you", chosen: "off" })] });
  }) as never);
  render(<MemoryModels />);
  const [first] = await screen.findAllByTestId("memory-model-mode");
  fireEvent.change(first!, { target: { value: "off" } });
  await waitFor(() =>
    expect(screen.getByTestId("memory-model-effective").textContent).toBe(
      "In effect: Off. You chose this.",
    ),
  );
});

test("merge proposals show the effective choice and changing it keeps the learning mode", async () => {
  page = served({
    keepAllowed: true,
    models: [model({ mode: "auto", chosen: "auto", proposeMerges: true })],
  });
  vi.mocked(put).mockResolvedValue({
    ...page,
    models: [model({ mode: "auto", chosen: "auto", proposeMerges: false })],
  });
  render(<MemoryModels />);
  const checkbox = await screen.findByRole("checkbox", {
    name: "Let gemma-4-e4b propose merges",
  });
  expect(checkbox).toBeChecked();
  expect(screen.getByRole("combobox", { name: "What gemma-4-e4b may do" })).toHaveValue("auto");
  fireEvent.click(checkbox);
  await waitFor(() => expect(checkbox).not.toBeChecked());
  expect(put).toHaveBeenCalledWith("/api/memory/models/gemma-4-e4b", { proposeMerges: false });
  expect(screen.getByTestId("memory-model-effective")).toHaveTextContent(
    "In effect: Keep automatically.",
  );
});

test("an outside model asks before it is measured, saying what goes and that it is charged", async () => {
  let calls = 0;
  vi.mocked(post).mockImplementation((async (_path: string, body: unknown) => {
    calls += 1;
    if (calls === 1) {
      expect(body).toEqual({ model: "cloud", confirmOutside: false });
      throw new ApiError(
        "About 369 requests and up to 738,000 tokens of made-up messages go to OpenAI, which charges for them. Nothing of yours is sent.",
        409,
        { code: "confirm_outside" },
      );
    }
    expect(body).toEqual({ model: "cloud", confirmOutside: true });
    page = served({
      measuring: { model: "cloud", done: 1, total: 369, startedAt: 1, yours: true, outside: true },
    });
    return {};
  }) as never);
  render(<MemoryModels />);
  const buttons = await screen.findAllByTestId("memory-model-measure");
  fireEvent.click(buttons[1]!);
  expect(await screen.findByText(/which charges for them/)).toBeTruthy();
  fireEvent.click(screen.getByTestId("memory-model-measure-confirm"));
  await waitFor(() =>
    expect(screen.getByTestId("memory-measuring").textContent).toContain("1 of 369 requests"),
  );
});

test("the owner's off is said, and nothing can be chosen", async () => {
  page = served({ allowed: false });
  render(<MemoryModels />);
  expect(await screen.findByTestId("memory-models-off")).toBeTruthy();
  for (const select of screen.getAllByTestId("memory-model-mode")) {
    expect((select as HTMLSelectElement).disabled).toBe(true);
  }
});
