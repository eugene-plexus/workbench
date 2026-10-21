/**
 * Charts and tables asked for in so many words (rich content RC4): read into
 * what Workbench draws, or refused saying why; and their CSV, as data.
 */

import { asksToBeDrawn, MAX_POINTS, MAX_SERIES, NotDrawn, readSource, readSpec } from "./chartSpec";
import { csvCell, toCsv } from "./csv";

const BAR = {
  workbench: "chart",
  version: 1,
  type: "bar",
  title: "Tokens a second",
  x: { label: "Model" },
  y: { label: "tok/s" },
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
        ["b", 90],
        ["c", 100],
      ],
    },
  ],
};

it("pie_and_stacked_read", () => {
  expect(readSpec({ ...BAR, type: "pie", series: [BAR.series[0]] })).toMatchObject({
    kind: "chart",
    type: "pie",
  });
  expect(readSpec({ ...BAR, stacked: true, series: [BAR.series[0], BAR.series[0]] })).toMatchObject(
    { type: "bar", stacked: true },
  );
});

it("negative_pie_refused_with_reason", () => {
  expect(() =>
    readSpec({ ...BAR, type: "pie", series: [{ name: "s", points: [["a", -1]] }] }),
  ).toThrow("A pie cannot show negative values.");
});

it("refuses a stack with missing or duplicate categories, and a pie with more than one series", () => {
  expect(() => readSpec({ ...BAR, stacked: true })).toThrow(
    "A stacked chart needs one value per category in each series.",
  );
  expect(() =>
    readSpec({
      ...BAR,
      stacked: true,
      series: [
        {
          points: [
            ["a", 1],
            ["a", 2],
          ],
        },
      ],
    }),
  ).toThrow("A stacked chart needs one value per category in each series.");
  expect(() => readSpec({ ...BAR, type: "pie" })).toThrow("A pie needs one series.");
});

it("reads a chart, its categories in the order first given", () => {
  const spec = readSpec(BAR);
  expect(spec).toMatchObject({
    kind: "chart",
    type: "bar",
    title: "Tokens a second",
    xLabel: "Model",
    yLabel: "tok/s",
    categories: ["a", "b", "c"],
  });
});

it("reads a table, filling short rows and keeping numbers as numbers", () => {
  const spec = readSpec({
    workbench: "table",
    version: 1,
    columns: ["Name", { name: "Count" }],
    rows: [["x", 3], ["y"]],
  });
  expect(spec).toEqual({
    kind: "table",
    title: "",
    columns: ["Name", "Count"],
    rows: [
      ["x", 3],
      ["y", null],
    ],
  });
});

it("reads a ```chart block, which may leave out what it is", () => {
  const spec = readSource(
    JSON.stringify({ type: "line", series: [{ name: "s", points: [[1, 2]] }] }),
  );
  expect(spec.kind).toBe("chart");
  expect(readSource(JSON.stringify({ columns: ["a"], rows: [[1]] })).kind).toBe("table");
});

it("draws only what says it is a chart or table", () => {
  expect(asksToBeDrawn(BAR)).toBe(true);
  expect(asksToBeDrawn({ type: "bar", series: [] })).toBe(false);
  expect(asksToBeDrawn([1, 2])).toBe(false);
  expect(() => readSpec({ type: "bar", series: [] })).toThrow('it does not say "chart" or "table"');
});

it.each([
  [{ ...BAR, version: 2 }, "it is version 2; this page draws version 1"],
  [{ ...BAR, type: "area" }, '"type" must be "bar", "line", "scatter" or "pie", not "area"'],
  [{ ...BAR, series: [] }, 'it has no "series"'],
  [
    { ...BAR, series: [{ name: "s", points: [["a", "b"]] }] },
    "series 1's point 1 has a y that is not a number",
  ],
  [
    {
      ...BAR,
      series: [
        {
          name: "s",
          points: [
            ["a", 1],
            [2, 3],
          ],
        },
      ],
    },
    "its x values mix numbers and words",
  ],
  [{ ...BAR, series: [{ name: "s", points: [[1]] }] }, "series 1's point 1 is not [x, y]"],
  [{ ...BAR, type: "scatter" }, "a scatter chart's x values are numbers"],
  [
    { ...BAR, series: Array.from({ length: MAX_SERIES + 1 }, () => ({ name: "s", points: [] })) },
    `it has ${MAX_SERIES + 1} series; at most ${MAX_SERIES} are drawn`,
  ],
  [
    {
      ...BAR,
      series: [{ name: "s", points: Array.from({ length: MAX_POINTS + 1 }, (_, i) => [i, i]) }],
    },
    `it has more than ${MAX_POINTS} points`,
  ],
  [
    { workbench: "table", version: 1, columns: ["a"], rows: [[{ x: 1 }]] },
    "row 1 has a cell that is neither words nor a number",
  ],
])("refuses, saying why: %#", (data, why) => {
  expect(() => readSpec(data)).toThrow(new NotDrawn(why));
});

it("says a block that is not JSON is not JSON", () => {
  expect(() => readSource("{type: bar")).toThrow(/^it is not JSON/);
});

it("writes CSV as data: quotes where needed, never a formula", () => {
  expect(csvCell("plain")).toBe("plain");
  expect(csvCell('a "b", c')).toBe('"a ""b"", c"');
  expect(csvCell('=HYPERLINK("http://x")')).toBe('"\'=HYPERLINK(""http://x"")"');
  for (const start of ["+1", "-1", "@SUM(A1)", "\tx", "\rx"]) {
    expect(csvCell(start).replace(/^"/, "").startsWith("'")).toBe(true);
  }
  expect(csvCell(-5)).toBe("-5");
  expect(csvCell(null)).toBe("");
  expect(toCsv(["a", "b"], [[1, "x"]])).toBe("a,b\r\n1,x\r\n");
});

it("stands a bar chart's numbers on categories, in their order", () => {
  const spec = readSpec({
    ...BAR,
    series: [
      {
        name: "s",
        points: [
          [2025, 3],
          [2023, 1],
          [2024, 2],
        ],
      },
    ],
  });
  expect(spec.kind === "chart" && spec.categories).toEqual(["2023", "2024", "2025"]);
});

it("refuses a series of no points, and a number too far from zero", () => {
  expect(() => readSpec({ ...BAR, series: [{ name: "s", points: [] }] })).toThrow(
    'series 1 has no "points"',
  );
  expect(() => readSpec({ ...BAR, series: [{ name: "s", points: [["a", 1e300]] }] })).toThrow(
    "series 1's point 1 is too far from zero to draw",
  );
});

it("reads a formula behind spaces as one too", () => {
  expect(csvCell(" =1+1")).toBe("' =1+1");
});
