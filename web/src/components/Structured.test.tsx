/**
 * Charts and tables (rich content RC4): drawn from numbers with the page's
 * colours, named for a screen reader, their numbers a click away, a table
 * sorted at a click and bounded on the page, and every row in the CSV.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";

import { readSpec, type ChartSpec, type TableSpec } from "../lib/chartSpec";
import { summary, ticks } from "./Chart";
import { SHOWN } from "./DataTable";
import { Markdown } from "./Markdown";
import { Structured } from "./Structured";
import { ToolContent } from "./ToolContent";

const BAR = readSpec({
  workbench: "chart",
  version: 1,
  type: "bar",
  title: "Tokens a second",
  series: [
    {
      name: "Before",
      points: [
        ["a", 40],
        ["b", 80],
      ],
    },
    {
      name: "After",
      points: [
        ["a", 50],
        ["b", 90],
      ],
    },
  ],
}) as ChartSpec;

let saved: Blob[];
let names: string[];

beforeEach(() => {
  saved = [];
  names = [];
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    saved.push(blob as Blob);
    return "blob:csv";
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    names.push(this.download);
  });
});

afterEach(() => vi.restoreAllMocks());

it("draws a bar for each point, named for a screen reader, in the series' colours", () => {
  const { container } = render(<Structured spec={BAR} />);
  const chart = screen.getByRole("group", { name: summary(BAR) });
  expect(summary(BAR)).toBe(
    "Bar chart: Tokens a second. 2 series, 4 points. Its numbers are under Data.",
  );
  expect(chart.querySelectorAll(".chart-bar")).toHaveLength(4);
  expect(container.querySelectorAll(".chart-s0 .chart-bar, .chart-s1 .chart-bar")).toHaveLength(4);
  expect(screen.getByText("Before").closest("li")).toHaveClass("chart-s0");
  // No style attribute anywhere: the page's policy refuses them.
  expect(container.querySelector("[style]")).toBeNull();
});

it("shows its numbers on request, and downloads them as CSV", async () => {
  render(<Structured spec={BAR} />);
  fireEvent.click(screen.getByRole("button", { name: "Data" }));
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(5);
  fireEvent.click(screen.getByRole("button", { name: "Download Tokens a second as CSV" }));
  expect(names).toEqual(["tokens-a-second.csv"]);
  expect(await saved[0]?.text()).toBe(
    "Series,x,y\r\nBefore,a,40\r\nBefore,b,80\r\nAfter,a,50\r\nAfter,b,90\r\n",
  );
});

it("sorts a table by a column at a click, and shows so many rows of many", async () => {
  const rows = Array.from({ length: SHOWN + 50 }, (_, i) => [`row ${i}`, i]);
  const spec = readSpec({
    workbench: "table",
    version: 1,
    title: "Runs",
    columns: ["Name", "N"],
    rows,
  }) as TableSpec;
  render(<Structured spec={spec} />);
  expect(
    screen.getByText(`Showing ${SHOWN} of ${SHOWN + 50} rows; the CSV has them all.`),
  ).toBeVisible();
  expect(screen.getAllByRole("row")).toHaveLength(SHOWN + 1);
  const n = screen.getByRole("button", { name: "N" });
  fireEvent.click(n);
  fireEvent.click(n);
  expect(n.closest("th")).toHaveAttribute("aria-sort", "descending");
  expect(screen.getAllByRole("row")[1]).toHaveTextContent(`row ${SHOWN + 49}`);
  fireEvent.click(screen.getByRole("button", { name: "Download Runs as CSV" }));
  expect((await saved[0]?.text())?.split("\r\n")).toHaveLength(SHOWN + 50 + 2);
});

it("draws a ```chart block, and keeps one it cannot draw as its source, saying why", () => {
  const good = JSON.stringify({
    type: "line",
    series: [
      {
        name: "s",
        points: [
          [1, 2],
          [2, 4],
        ],
      },
    ],
  });
  const { container, unmount } = render(<Markdown text={"```chart\n" + good + "\n```"} />);
  expect(container.querySelector("svg.chart polyline")).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Source" }));
  expect(screen.getByLabelText("The chart's source")).toHaveTextContent('"type":"line"');
  unmount();
  render(<Markdown text={'```chart\n{"type": "area", "series": []}\n```'} />);
  expect(screen.getByText(/^Chart not shown: "type" must be/)).toBeInTheDocument();
});

it("draws a tool's chart, and leaves other data as the data it is", () => {
  render(
    <ToolContent
      blocks={[
        {
          id: "a",
          type: "data",
          data: {
            workbench: "chart",
            version: 1,
            type: "scatter",
            series: [{ name: "s", points: [[1, 1]] }],
          },
        },
        { id: "b", type: "data", data: { workbench: "chart", version: 9 } },
        { id: "c", type: "data", data: { rows: 3 } },
      ]}
    />,
  );
  expect(screen.getByTestId("structured-chart")).toBeInTheDocument();
  expect(
    screen.getByText("Not drawn: it is version 9; this page draws version 1."),
  ).toBeInTheDocument();
  expect(screen.getAllByText("Data", { selector: "summary" })).toHaveLength(2);
  // Data that does not ask to be drawn is not tried, and says nothing of it.
  expect(screen.getAllByTestId("tool-not-drawn")).toHaveLength(1);
});

it("puts ticks on round numbers", () => {
  expect(ticks(0, 97)).toEqual([0, 20, 40, 60, 80, 100]);
  expect(ticks(-3, 3)).toEqual([-4, -2, 0, 2, 4]);
  expect(ticks(5, 5).length).toBeGreaterThan(1);
});

it("gives a range too wide to step its two ends, and fits long numbers in their margin", () => {
  expect(ticks(-1e308, 1e308)).toEqual([-1e308, 1e308]);
  const spec = readSpec({
    workbench: "chart",
    version: 1,
    type: "line",
    series: [
      {
        name: "s",
        points: [
          [1, 123456],
          [2, 4500000],
        ],
      },
    ],
  }) as ChartSpec;
  render(<Structured spec={spec} />);
  expect(screen.getByText("5M")).toBeInTheDocument();
});
