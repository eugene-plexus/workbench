/**
 * A chart Workbench draws itself (workbench-rich-content-roadmap.md RC4): bar,
 * line or scatter, from a spec read by lib/chartSpec.ts. Every mark is an SVG
 * element made here from numbers; every word is text. Its colours are the
 * page's (styles.css `.chart-s0` and on), so it follows the theme, and its
 * numbers are a table a click away, for a screen reader as for anyone.
 */

import { useId } from "react";

import type { ChartSpec, Point } from "../lib/chartSpec";

const WIDTH = 640;
const HEIGHT = 360;
const LEFT = 64;
const RIGHT = 16;
const TOP = 16;
const BOTTOM = 56;

/** Ticks at round numbers across [low, high], about `count` of them. */
export function ticks(low: number, high: number, count = 5): number[] {
  if (low === high) {
    const pad = Math.abs(low) || 1;
    return ticks(low - pad, high + pad, count);
  }
  const raw = (high - low) / count;
  // Too wide a range for a step to count: its ends, as they are.
  if (!Number.isFinite(raw) || raw <= 0) return [low, high];
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const first = Math.floor(low / step) * step;
  const out: number[] = [];
  for (let v = first; v <= high + step * 0.5 && out.length < 50; v += step) {
    out.push(Number(v.toPrecision(12)));
  }
  return out;
}

function words(value: number): string {
  const abs = Math.abs(value);
  if (abs > 0 && abs < 1e-3) return value.toExponential(1);
  // A long number fits its margin as "123K", "4.5M".
  if (abs >= 1e4)
    return value.toLocaleString("en", { notation: "compact", maximumFractionDigits: 1 });
  return value.toLocaleString("en", { maximumFractionDigits: 3 });
}

/** One sentence for a screen reader: what kind, what of, how much. */
export function summary(spec: ChartSpec): string {
  const kind = { bar: "Bar chart", line: "Line chart", scatter: "Scatter chart" }[spec.type];
  const points = spec.series.reduce((n, s) => n + s.points.length, 0);
  const of = spec.title ? `: ${spec.title}` : "";
  const series = spec.series.length === 1 ? "1 series" : `${spec.series.length} series`;
  return `${kind}${of}. ${series}, ${points} points. Its numbers are under Data.`;
}

export function Chart({ spec }: { spec: ChartSpec }) {
  const id = useId();
  const plotW = WIDTH - LEFT - RIGHT;
  const plotH = HEIGHT - TOP - BOTTOM;
  const all = spec.series.flatMap((s) => s.points);
  const ys = all.map((p) => p.y);
  const yTicks = ticks(
    Math.min(...ys, spec.type === "bar" ? 0 : Infinity),
    Math.max(...ys, spec.type === "bar" ? 0 : -Infinity),
  );
  const yLow = yTicks[0] ?? 0;
  const yHigh = yTicks[yTicks.length - 1] ?? 1;
  const yAt = (v: number) => TOP + plotH - ((v - yLow) / (yHigh - yLow || 1)) * plotH;

  const categories = spec.categories;
  const band = categories ? plotW / Math.max(1, categories.length) : 0;
  const xNumbers = categories ? [] : all.map((p) => p.x as number);
  const xTicks = categories ? [] : ticks(Math.min(...xNumbers), Math.max(...xNumbers));
  const xLow = xTicks[0] ?? 0;
  const xHigh = xTicks[xTicks.length - 1] ?? 1;
  const xAt = (p: Point): number =>
    categories
      ? LEFT + band * (categories.indexOf(String(p.x)) + 0.5)
      : LEFT + ((Number(p.x) - xLow) / (xHigh - xLow || 1)) * plotW;
  // Category labels thin out on a crowded axis.
  const every = categories ? Math.max(1, Math.ceil(categories.length / 12)) : 1;
  const barW = categories ? (band * 0.8) / spec.series.length : 0;

  return (
    <svg
      className="chart"
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      role="img"
      aria-labelledby={`${id}-title`}
    >
      <title id={`${id}-title`}>{summary(spec)}</title>
      {yTicks.map((v) => (
        <g key={`y${v}`}>
          <line className="chart-grid" x1={LEFT} x2={LEFT + plotW} y1={yAt(v)} y2={yAt(v)} />
          <text
            className="chart-tick"
            x={LEFT - 6}
            y={yAt(v)}
            textAnchor="end"
            dominantBaseline="middle"
          >
            {words(v)}
          </text>
        </g>
      ))}
      {categories
        ? categories.map((c, i) =>
            i % every ? null : (
              <text
                key={`x${i}`}
                className="chart-tick"
                x={LEFT + band * (i + 0.5)}
                y={TOP + plotH + 16}
                textAnchor="middle"
              >
                {c.length > 14 ? `${c.slice(0, 13)}…` : c}
              </text>
            ),
          )
        : xTicks.map((v) => (
            <text
              key={`x${v}`}
              className="chart-tick"
              x={xAt({ x: v, y: 0 })}
              y={TOP + plotH + 16}
              textAnchor="middle"
            >
              {words(v)}
            </text>
          ))}
      <line
        className="chart-axis"
        x1={LEFT}
        x2={LEFT + plotW}
        y1={yAt(Math.max(yLow, Math.min(0, yHigh)))}
        y2={yAt(Math.max(yLow, Math.min(0, yHigh)))}
      />
      <line className="chart-axis" x1={LEFT} x2={LEFT} y1={TOP} y2={TOP + plotH} />
      {spec.series.map((s, i) => {
        const cls = `chart-s${i % 12}`;
        if (spec.type === "bar") {
          const zero = yAt(Math.max(yLow, Math.min(0, yHigh)));
          return (
            <g key={i} className={cls}>
              {s.points.map((p, j) => {
                const x = xAt(p) - (band * 0.8) / 2 + barW * i;
                const y = yAt(p.y);
                return (
                  <rect
                    key={j}
                    x={x}
                    y={Math.min(y, zero)}
                    width={Math.max(1, barW - 1)}
                    height={Math.max(0.5, Math.abs(zero - y))}
                  />
                );
              })}
            </g>
          );
        }
        const placed = s.points.map((p) => [xAt(p), yAt(p.y)] as const);
        return (
          <g key={i} className={cls}>
            {spec.type === "line" && (
              <polyline
                className="chart-line"
                points={placed.map(([x, y]) => `${x},${y}`).join(" ")}
              />
            )}
            {placed.map(([x, y], j) => (
              <circle key={j} cx={x} cy={y} r={spec.type === "scatter" ? 3.5 : 2.5} />
            ))}
          </g>
        );
      })}
      {spec.xLabel && (
        <text className="chart-label" x={LEFT + plotW / 2} y={HEIGHT - 8} textAnchor="middle">
          {spec.xLabel}
        </text>
      )}
      {spec.yLabel && (
        <text
          className="chart-label"
          transform={`translate(14 ${TOP + plotH / 2}) rotate(-90)`}
          textAnchor="middle"
        >
          {spec.yLabel}
        </text>
      )}
    </svg>
  );
}

/** The series' names beside their colours, when there is more than one. */
export function Legend({ spec }: { spec: ChartSpec }) {
  if (spec.series.length < 2) return null;
  return (
    <ul className="chart-legend">
      {spec.series.map((s, i) => (
        <li key={i} className={`chart-s${i % 12}`}>
          <span className="chart-swatch" aria-hidden="true" />
          {s.name}
        </li>
      ))}
    </ul>
  );
}
