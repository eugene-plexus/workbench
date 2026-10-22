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

it("shows the value for the latest hover or focus, restoring focus after hover leaves", () => {
  render(<Chart spec={spec("bar")} />);
  const first = screen.getByLabelText("One · a · 2");
  const second = screen.getByLabelText("Two · b · -2");
  const third = screen.getByLabelText("Two · a · 4");
  fireEvent.focus(first);
  fireEvent.mouseEnter(second);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Two · b · -2");
  expect(second).toHaveAttribute("aria-describedby", screen.getByRole("tooltip").id);
  expect(first).not.toHaveAttribute("aria-describedby");
  fireEvent.mouseLeave(second);
  expect(screen.getByRole("tooltip")).toHaveTextContent("One · a · 2");
  fireEvent.mouseEnter(second);
  fireEvent.blur(first);
  fireEvent.focus(third);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Two · a · 4");
  fireEvent.blur(third);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

it("does not keep a value when the chart changes", () => {
  const original = spec("bar");
  const { rerender } = render(<Chart spec={original} />);
  fireEvent.mouseEnter(screen.getByLabelText("One · a · 2"));
  const changed: ChartSpec = {
    ...original,
    series: [{ name: "New", points: [{ x: "a", y: 20 }] }],
  };
  rerender(<Chart spec={changed} />);
  expect(screen.queryByRole("tooltip")).toBeNull();
  fireEvent.mouseEnter(screen.getByLabelText("New · a · 20"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("New · a · 20");
});

it("keeps every stacked total within the plot", () => {
  const stacked = readSpec({
    workbench: "chart",
    version: 1,
    type: "bar",
    stacked: true,
    series: [
      { name: "One", points: [["a", 20]] },
      { name: "Two", points: [["a", 21]] },
    ],
  }) as ChartSpec;
  const { container } = render(<Chart spec={stacked} />);
  const top = Math.min(
    ...[...container.querySelectorAll(".chart-grid")].map((grid) =>
      Number(grid.getAttribute("y1")),
    ),
  );
  const last = screen.getByLabelText("Two · a · 21").querySelector("rect")!;
  expect(Number(last.getAttribute("y"))).toBeGreaterThanOrEqual(top);
});

it("keeps stacked markers inside their own segments", () => {
  render(<Chart spec={spec("bar", true)} />);
  for (const label of ["One · a · 2", "One · b · -3"]) {
    const mark = screen.getByLabelText(label);
    const bar = mark.querySelector(".chart-bar")!;
    const marker = mark.querySelector(".chart-marker")!;
    const y = Number(bar.getAttribute("y"));
    const bottom = y + Number(bar.getAttribute("height"));
    const markerY = Number(marker.getAttribute("transform")!.split(" ")[1]?.replace(")", ""));
    expect(markerY).toBeGreaterThan(y);
    expect(markerY).toBeLessThan(bottom);
  }
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
