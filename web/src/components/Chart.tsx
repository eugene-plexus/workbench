/**
 * A chart Workbench draws itself (workbench-rich-content-roadmap.md RC4): bar,
 * line, scatter or pie, from a spec read by lib/chartSpec.ts. Every mark is an SVG
 * element made here from numbers; every word is text. Its colours are the
 * page's (styles.css `.chart-s0` and on), so it follows the theme, and its
 * numbers are a table a click away, for a screen reader as for anyone.
 */

import { useId, useState, type ReactNode, type SVGProps } from "react";

import { useTheme } from "../lib/display";
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
  const last = Math.ceil(high / step) * step;
  const out: number[] = [];
  for (let v = first; v <= last + step * 0.5 && out.length < 50; v += step) {
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
  const kind = {
    bar: spec.stacked ? "Stacked bar chart" : "Bar chart",
    line: "Line chart",
    scatter: "Scatter chart",
    pie: "Pie chart",
  }[spec.type];
  const points = spec.series.reduce((n, s) => n + s.points.length, 0);
  const series = spec.series.length === 1 ? "1 series" : `${spec.series.length} series`;
  return `${kind}${spec.title ? `: ${spec.title}` : ""}. ${series}, ${points} points. Its numbers are under Data.`;
}

const DASHES = ["none", "7 3", "2 3"];
const dash = (index: number) => DASHES[index % DASHES.length];

function Marker({
  index,
  x = 0,
  y = 0,
  size = 4,
}: {
  index: number;
  x?: number;
  y?: number;
  size?: number;
}) {
  let shape: ReactNode;
  switch (index % 4) {
    case 1:
      shape = <rect x={-size} y={-size} width={size * 2} height={size * 2} />;
      break;
    case 2:
      shape = <path d={`M 0 ${-size - 1} L ${size + 1} 0 L 0 ${size + 1} L ${-size - 1} 0 Z`} />;
      break;
    case 3:
      shape = <polygon points={`0,${-size - 1} ${size + 1},${size} ${-size - 1},${size}`} />;
      break;
    default:
      shape = <circle r={size} />;
  }
  return (
    <g className="chart-marker" transform={`translate(${x} ${y})`}>
      {shape}
    </g>
  );
}

function piePoints(spec: ChartSpec): Point[] {
  const points = spec.series[0]?.points ?? [];
  return points.length <= 12
    ? points
    : [
        ...points.slice(0, 11),
        { x: "Other", y: points.slice(11).reduce((sum, p) => sum + p.y, 0) },
      ];
}

type MarkProps = (series: string, point: Point) => SVGProps<SVGGElement>;
type ActiveMark = { spec: ChartSpec; label: string };

/** Each mark has the same value for hover, focus and its accessible name. */
export function Chart({ spec }: { spec: ChartSpec }) {
  const id = useId();
  const theme = useTheme();
  const [hovered, setHovered] = useState<ActiveMark | null>(null);
  const [focused, setFocused] = useState<ActiveMark | null>(null);
  const active =
    (hovered?.spec === spec ? hovered.label : null) ??
    (focused?.spec === spec ? focused.label : null);
  const mark: MarkProps = (series, p) => {
    const label = `${series} · ${p.x} · ${p.y}`;
    return {
      tabIndex: 0,
      role: "img",
      "aria-label": label,
      "aria-describedby": active === label ? `${id}-value` : undefined,
      onMouseEnter: () => setHovered({ spec, label }),
      onMouseLeave: () => setHovered(null),
      onFocus: () => {
        setHovered(null);
        setFocused({ spec, label });
      },
      onBlur: () => setFocused(null),
      onKeyDown: (event) => {
        if (event.key === "Escape") {
          setFocused(null);
          setHovered(null);
        }
      },
    };
  };
  return (
    <div className="chart-wrap">
      <svg
        className="chart"
        data-theme={theme}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-labelledby={`${id}-title`}
      >
        <title id={`${id}-title`}>{summary(spec)}</title>
        <defs>
          {Array.from({ length: 12 }, (_, i) => (
            <pattern
              key={i}
              id={`${id}-pattern-${i}`}
              className={`chart-s${i}`}
              patternUnits="userSpaceOnUse"
              width={6 + (i % 3) * 2}
              height={6 + (i % 3) * 2}
              patternTransform={`rotate(${i * 15})`}
            >
              <rect className="chart-pattern-base" width="12" height="12" />
              {i > 0 && (
                <path className="chart-pattern-line" d="M 0 0 L 0 12" strokeDasharray={dash(i)} />
              )}
            </pattern>
          ))}
        </defs>
        {spec.type === "pie" ? (
          <Pie spec={spec} mark={mark} id={id} />
        ) : (
          <Cartesian spec={spec} mark={mark} id={id} />
        )}
      </svg>
      {active && (
        <div id={`${id}-value`} role="tooltip" className="chart-tooltip">
          {active}
        </div>
      )}
    </div>
  );
}

function Pie({ spec, mark, id }: { spec: ChartSpec; mark: MarkProps; id: string }) {
  const points = piePoints(spec);
  const total = points.reduce((sum, p) => sum + p.y, 0);
  let angle = -Math.PI / 2;
  const cx = WIDTH / 2,
    cy = HEIGHT / 2,
    radius = 140;
  if (!total)
    return (
      <text className="chart-label" x={cx} y={cy} textAnchor="middle">
        No positive values.
      </text>
    );
  const at = (a: number) => [cx + radius * Math.cos(a), cy + radius * Math.sin(a)];
  return (
    <>
      {points.map((p, i) => {
        const start = angle;
        angle += (p.y / total) * Math.PI * 2;
        const [sx, sy] = at(start),
          [ex, ey] = at(angle);
        const d =
          p.y === total
            ? `M ${cx - radius} ${cy} A ${radius} ${radius} 0 1 0 ${cx + radius} ${cy} A ${radius} ${radius} 0 1 0 ${cx - radius} ${cy} Z`
            : `M ${cx} ${cy} L ${sx} ${sy} A ${radius} ${radius} 0 ${angle - start > Math.PI ? 1 : 0} 1 ${ex} ${ey} Z`;
        const mid = (start + angle) / 2;
        return (
          <g key={i} className={`chart-s${i}`} {...mark(spec.series[0]?.name ?? "Series 1", p)}>
            <path className="chart-pie-slice" d={d} fill={`url(#${id}-pattern-${i})`} />
            {p.y > 0 && (
              <g className="chart-pie-marker">
                <Marker
                  index={i}
                  x={cx + radius * 0.65 * Math.cos(mid)}
                  y={cy + radius * 0.65 * Math.sin(mid)}
                />
              </g>
            )}
          </g>
        );
      })}
    </>
  );
}

function Cartesian({ spec, mark, id }: { spec: ChartSpec; mark: MarkProps; id: string }) {
  const plotW = WIDTH - LEFT - RIGHT,
    plotH = HEIGHT - TOP - BOTTOM;
  const all = spec.series.flatMap((s) => s.points);
  const categories = spec.categories;
  const positive = new Map<string, number>(),
    negative = new Map<string, number>();
  const positions = spec.series.map((s) =>
    s.points.map((p) => {
      const sums = p.y >= 0 ? positive : negative;
      const start = spec.stacked ? (sums.get(String(p.x)) ?? 0) : 0;
      const end = start + p.y;
      sums.set(String(p.x), end);
      return { start, end };
    }),
  );
  const ys = spec.stacked ? positions.flat().flatMap((p) => [p.start, p.end]) : all.map((p) => p.y);
  const yTicks = ticks(
    Math.min(...ys, spec.type === "bar" ? 0 : Infinity),
    Math.max(...ys, spec.type === "bar" ? 0 : -Infinity),
  );
  const yLow = yTicks[0] ?? 0,
    yHigh = yTicks[yTicks.length - 1] ?? 1;
  const yAt = (v: number) => TOP + plotH - ((v - yLow) / (yHigh - yLow || 1)) * plotH;
  const band = categories ? plotW / Math.max(1, categories.length) : 0;
  const xNumbers = categories ? [] : all.map((p) => p.x as number);
  const xTicks = categories ? [] : ticks(Math.min(...xNumbers), Math.max(...xNumbers));
  const xLow = xTicks[0] ?? 0,
    xHigh = xTicks[xTicks.length - 1] ?? 1;
  const xAt = (p: Point) =>
    categories
      ? LEFT + band * (categories.indexOf(String(p.x)) + 0.5)
      : LEFT + ((Number(p.x) - xLow) / (xHigh - xLow || 1)) * plotW;
  const every = categories ? Math.max(1, Math.ceil(categories.length / 12)) : 1;
  const barW = (band * 0.8) / (spec.stacked ? 1 : spec.series.length);
  return (
    <>
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
      {spec.series.map((s, i) => (
        <g key={i} className={`chart-s${i % 12}`}>
          {spec.type === "line" && (
            <polyline
              className="chart-line"
              strokeDasharray={dash(i)}
              points={s.points.map((p) => `${xAt(p)},${yAt(p.y)}`).join(" ")}
            />
          )}
          {s.points.map((p, j) => {
            if (spec.type === "bar") {
              const { start, end } = positions[i]![j]!;
              const x = xAt(p) - (band * 0.8) / 2 + (spec.stacked ? 0 : barW * i);
              const y = yAt(end),
                zero = yAt(start);
              return (
                <g key={j} {...mark(s.name, p)}>
                  <rect
                    className="chart-bar"
                    x={x}
                    y={Math.min(y, zero)}
                    width={Math.max(1, barW - 1)}
                    height={Math.max(0.5, Math.abs(zero - y))}
                    fill={`url(#${id}-pattern-${i})`}
                  />
                  <g className="chart-bar-marker">
                    <Marker
                      index={i}
                      x={x + Math.max(1, barW - 1) / 2}
                      y={spec.stacked ? (y + zero) / 2 : y}
                      size={3}
                    />
                  </g>
                </g>
              );
            }
            return (
              <g key={j} {...mark(s.name, p)}>
                <Marker index={i} x={xAt(p)} y={yAt(p.y)} size={spec.type === "scatter" ? 4 : 3} />
              </g>
            );
          })}
        </g>
      ))}
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
    </>
  );
}

/** Shapes and dashes match the marks, including each pie slice. */
export function Legend({ spec }: { spec: ChartSpec }) {
  const entries =
    spec.type === "pie" ? piePoints(spec).map((p) => String(p.x)) : spec.series.map((s) => s.name);
  if (entries.length < 2 && spec.type !== "pie") return null;
  return (
    <ul className="chart-legend">
      {entries.map((name, i) => (
        <li key={i} className={`chart-s${i % 12}`}>
          <svg className="chart-legend-mark" viewBox="0 0 32 16" aria-hidden="true">
            <line x1="0" x2="32" y1="8" y2="8" strokeDasharray={dash(i)} />
            <Marker index={i} x={16} y={8} />
          </svg>
          {name}
        </li>
      ))}
    </ul>
  );
}
