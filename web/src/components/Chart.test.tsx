import { fireEvent, render, screen } from "@testing-library/react";
import { readSpec, type ChartSpec } from "../lib/chartSpec";
import { Chart, Legend } from "./Chart";

function spec(type: string, stacked = false): ChartSpec {
  return readSpec({
    workbench: "chart",
    version: 1,
    type,
    stacked,
    series: [
      {
        name: "One",
        points: [
          ["a", 2],
          ["b", -3],
        ],
      },
      {
        name: "Two",
        points: [
          ["b", -2],
          ["a", 4],
        ],
      },
    ],
  }) as ChartSpec;
}

it("shows each mark's value on hover and focus, and dismisses it on Escape", () => {
  render(<Chart spec={spec("bar")} />);
  const mark = screen.getByLabelText("One · a · 2");
  expect(mark).toHaveAttribute("tabindex", "0");
  fireEvent.mouseEnter(mark);
  expect(screen.getByRole("tooltip")).toHaveTextContent("One · a · 2");
  fireEvent.mouseLeave(mark);
  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.focus(mark);
  expect(screen.getByRole("tooltip")).toHaveTextContent("One · a · 2");
  fireEvent.keyDown(mark, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("stacks positives and negatives from zero, matching categories rather than point order", () => {
  render(<Chart spec={spec("bar", true)} />);
  const first = screen.getByLabelText("One · a · 2").querySelector("rect")!;
  const second = screen.getByLabelText("Two · a · 4").querySelector("rect")!;
  expect(second.getAttribute("x")).toBe(first.getAttribute("x"));
  expect(Number(second.getAttribute("y")) + Number(second.getAttribute("height"))).toBeCloseTo(
    Number(first.getAttribute("y")),
  );
  const negFirst = screen.getByLabelText("One · b · -3").querySelector("rect")!;
  const negSecond = screen.getByLabelText("Two · b · -2").querySelector("rect")!;
  expect(Number(negFirst.getAttribute("y")) + Number(negFirst.getAttribute("height"))).toBeCloseTo(
    Number(negSecond.getAttribute("y")),
  );
});

it("draws at most twelve pie slices, with the rest under Other, and keeps full data", () => {
  const pie = readSpec({
    workbench: "chart",
    version: 1,
    type: "pie",
    series: [{ name: "Parts", points: Array.from({ length: 14 }, (_, i) => [`p${i}`, i + 1]) }],
  }) as ChartSpec;
  const { container } = render(
    <>
      <Legend spec={pie} />
      <Chart spec={pie} />
    </>,
  );
  expect(container.querySelectorAll(".chart-pie-slice")).toHaveLength(12);
  fireEvent.focus(screen.getByLabelText("Parts · Other · 39"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("Parts · Other · 39");
  expect(screen.getByText("Other")).toBeInTheDocument();
  expect(pie.series[0]?.points).toHaveLength(14);
});

it("uses marker shapes and dash patterns for series, and handles a full or empty pie", () => {
  const line = spec("line");
  const { container, unmount } = render(
    <>
      <Legend spec={line} />
      <Chart spec={line} />
    </>,
  );
  const markers = [...container.querySelectorAll(".chart-marker")];
  expect(new Set(markers.map((marker) => marker.firstElementChild?.tagName)).size).toBeGreaterThan(
    1,
  );
  expect(
    new Set(
      [...container.querySelectorAll(".chart-line")].map((el) =>
        el.getAttribute("stroke-dasharray"),
      ),
    ).size,
  ).toBeGreaterThan(1);
  unmount();
  for (const y of [5, 0]) {
    const pie = readSpec({
      workbench: "chart",
      version: 1,
      type: "pie",
      series: [{ points: [["a", y]] }],
    }) as ChartSpec;
    const shown = render(<Chart spec={pie} />);
    expect(shown.container.innerHTML).not.toMatch(/NaN|Infinity/);
    if (y > 0)
      expect(
        shown.container.querySelector(".chart-pie-slice")?.getAttribute("d")?.match(/A/g),
      ).toHaveLength(2);
    shown.unmount();
  }
});
