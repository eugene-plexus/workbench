import { render, screen } from "@testing-library/react";

import { api } from "../lib/api";
import type { UsagePage, UsageTotals } from "../lib/types";
import { YourUsage, usd } from "./YourUsage";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
}));

const NONE: UsageTotals = {
  requests: 0,
  promptTokens: 0,
  completionTokens: 0,
  tokens: 0,
  costUsd: "0.000000000000",
  unpricedRequests: 0,
};

function answer(page: UsagePage) {
  vi.mocked(api).mockResolvedValue(page);
}

describe("Your usage (row 40)", () => {
  it("says today, this month and a group's spent allowance, money exactly", async () => {
    answer({
      known: true,
      report: {
        day: "2026-11-16",
        month: "2026-11",
        subjects: [
          {
            kind: "person",
            id: "p-ada",
            name: "Ada",
            day: { ...NONE, requests: 2, tokens: 1500, costUsd: "0.010500000000" },
            month: {
              ...NONE,
              requests: 5,
              tokens: 12040,
              costUsd: "4.010000000000",
              unpricedRequests: 1,
            },
            allowances: [],
          },
          {
            kind: "group",
            id: "g-sales",
            name: "Sales",
            day: NONE,
            month: NONE,
            allowances: [
              {
                kind: "costPerMonth",
                limit: "4.000000000000",
                used: "4.010000000000",
                left: "0.000000000000",
                spent: true,
                resetsAt: "2026-12-01T00:00:00Z",
              },
            ],
          },
        ],
      },
    });
    render(<YourUsage />);
    expect(await screen.findByTestId("usage-today")).toHaveTextContent(
      "Today: 1,500 tokens, $0.0105.",
    );
    expect(screen.getByTestId("usage-month")).toHaveTextContent(
      "This month: 12,040 tokens, $4.01, and 1 with no price.",
    );
    expect(screen.getByTestId("usage-allowance-g-sales-costPerMonth")).toHaveTextContent(
      "Your group Sales: $4.01 of $4.00 this month. Used up: new requests wait until 1 December.",
    );
  });

  it("says why there is nothing to show, never a zero", async () => {
    answer({
      known: false,
      why: "This Eugene does not count usage yet. Update Eugene to see yours here.",
    });
    render(<YourUsage />);
    expect(await screen.findByTestId("your-usage")).toHaveTextContent(
      "This Eugene does not count usage yet.",
    );
    expect(screen.queryByTestId("usage-today")).toBeNull();
  });

  it("reads money on the digits", () => {
    expect(usd("0.000300000000")).toBe("$0.0003");
    expect(usd("1234.500000000000")).toBe("$1,234.50");
  });
});
