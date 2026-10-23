/**
 * Charts and tables a model or a tool asked for in so many words
 * (workbench-rich-content-roadmap.md RC4): a ```chart block in an answer, or a
 * tool's structured data that says `"workbench": "chart"` or `"table"`. Read
 * here into a shape Workbench draws itself (components/Chart.tsx,
 * DataTable.tsx); nothing in it runs, and JSON that is not one of these is
 * left as the data it is.
 *
 * Version 1:
 *
 *   { "workbench": "chart", "version": 1, "type": "bar" | "line" | "scatter" | "pie",
 *     "title"?: string, "x"?: { "label"?: string }, "y"?: { "label"?: string },
 *     "stacked"?: boolean,
 *     "series": [ { "name": string, "points": [ [x, y], ... ] } ] }
 *
 *   { "workbench": "table", "version": 1, "title"?: string,
 *     "columns": [ string | { "name": string } ], "rows": [ [cell, ...], ... ] }
 *
 * x is a number, or a category (a string) for bar and line; y is a number.
 * A pie has one series of non-negative values. With more than twelve points,
 * keep the first eleven slices and combine the rest as Other; keep all data.
 * A stacked bar has one point per category in each series.
 * In a ```chart block, "workbench" and "version" may be left out.
 */

export const MAX_SERIES = 12;
export const MAX_POINTS = 5_000;
export const MAX_COLUMNS = 50;
export const MAX_ROWS = 10_000;
export const MAX_LABEL = 200;
/** A number further from zero is not drawn. */
export const MAX_MAGNITUDE = 1e15;
/** A table's words, in all. */
export const MAX_TABLE_CHARACTERS = 2_000_000;

export type Point = { x: number | string; y: number };
export type Series = { name: string; points: Point[] };
export type ChartSpec = {
  kind: "chart";
  type: "bar" | "line" | "scatter" | "pie";
  stacked?: boolean;
  title: string;
  xLabel: string;
  yLabel: string;
  series: Series[];
  /** Whether x is a category (strings, in the order first given). */
  categories: string[] | null;
};
export type Cell = string | number | boolean | null;
export type TableSpec = { kind: "table"; title: string; columns: string[]; rows: Cell[][] };
export type Spec = ChartSpec | TableSpec;

/** Why what was asked for cannot be drawn, in words for the person. */
export class NotDrawn extends Error {}

function label(value: unknown, what: string): string {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string") throw new NotDrawn(`its ${what} is not words`);
  return value.length > MAX_LABEL ? `${value.slice(0, MAX_LABEL - 1)}…` : value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Whether a tool's data asks to be a chart or a table. */
export function asksToBeDrawn(data: unknown): boolean {
  return isRecord(data) && (data.workbench === "chart" || data.workbench === "table");
}

function chart(data: Record<string, unknown>): ChartSpec {
  const type = data.type;
  if (type !== "bar" && type !== "line" && type !== "scatter" && type !== "pie") {
    throw new NotDrawn(
      `"type" must be "bar", "line", "scatter" or "pie", not ${JSON.stringify(type)?.slice(0, 40)}`,
    );
  }
  if (!Array.isArray(data.series) || data.series.length === 0) {
    throw new NotDrawn('it has no "series"');
  }
  if (data.series.length > MAX_SERIES) {
    throw new NotDrawn(`it has ${data.series.length} series; at most ${MAX_SERIES} are drawn`);
  }
  if (type === "pie" && data.series.length !== 1) throw new NotDrawn("A pie needs one series.");
  if (data.stacked !== undefined && typeof data.stacked !== "boolean")
    throw new NotDrawn('"stacked" must be true or false.');
  if (data.stacked === true && type !== "bar")
    throw new NotDrawn("Only a bar chart can be stacked.");
  const x = isRecord(data.x) ? data.x : {};
  const y = isRecord(data.y) ? data.y : {};
  let total = 0;
  let strings = 0;
  let numbers = 0;
  const series = data.series.map((raw, s): Series => {
    if (!isRecord(raw)) throw new NotDrawn(`series ${s + 1} is not an object`);
    if (!Array.isArray(raw.points) || raw.points.length === 0) {
      throw new NotDrawn(`series ${s + 1} has no "points"`);
    }
    total += raw.points.length;
    if (total > MAX_POINTS) throw new NotDrawn(`it has more than ${MAX_POINTS} points`);
    const points = raw.points.map((p, i): Point => {
      const where = `series ${s + 1}'s point ${i + 1}`;
      if (!Array.isArray(p) || p.length !== 2) throw new NotDrawn(`${where} is not [x, y]`);
      const [px, py] = p as [unknown, unknown];
      if (typeof py !== "number" || !Number.isFinite(py)) {
        throw new NotDrawn(`${where} has a y that is not a number`);
      }
      if (type === "pie" && py < 0) throw new NotDrawn("A pie cannot show negative values.");
      if (
        Math.abs(py) > MAX_MAGNITUDE ||
        (typeof px === "number" && Math.abs(px) > MAX_MAGNITUDE)
      ) {
        throw new NotDrawn(`${where} is too far from zero to draw`);
      }
      if (typeof px === "number" && Number.isFinite(px)) {
        numbers++;
        return { x: px, y: py };
      }
      if (typeof px === "string") {
        strings++;
        return { x: label(px, `${where}'s x`), y: py };
      }
      throw new NotDrawn(`${where} has an x that is neither a number nor words`);
    });
    return { name: label(raw.name, `series ${s + 1}'s name`) || `Series ${s + 1}`, points };
  });
  if (strings && numbers) throw new NotDrawn("its x values mix numbers and words");
  if (strings && type === "scatter") throw new NotDrawn("a scatter chart's x values are numbers");
  let categories: string[] | null = null;
  if (strings) {
    const seen = new Set<string>();
    for (const s of series) for (const p of s.points) seen.add(String(p.x));
    categories = [...seen];
  } else if (type === "bar" || type === "pie") {
    // A bar stands on a category: numbers (years, say) are categories, in order.
    const seen = new Set<number>();
    for (const s of series) for (const p of s.points) seen.add(Number(p.x));
    categories = [...seen].sort((a, b) => a - b).map(String);
  }
  if (
    data.stacked === true &&
    series.some(
      (s) =>
        s.points.length !== categories?.length ||
        new Set(s.points.map((p) => String(p.x))).size !== categories.length,
    )
  ) {
    throw new NotDrawn("A stacked chart needs one value per category in each series.");
  }
  return {
    kind: "chart",
    type,
    title: label(data.title, "title"),
    xLabel: label(x.label, "x label"),
    yLabel: label(y.label, "y label"),
    series,
    categories,
    ...(data.stacked === true ? { stacked: true } : {}),
  };
}

function table(data: Record<string, unknown>): TableSpec {
  if (!Array.isArray(data.columns) || data.columns.length === 0) {
    throw new NotDrawn('it has no "columns"');
  }
  if (data.columns.length > MAX_COLUMNS) {
    throw new NotDrawn(`it has ${data.columns.length} columns; at most ${MAX_COLUMNS} are shown`);
  }
  const columns = data.columns.map(
    (c, i) => label(isRecord(c) ? c.name : c, `column ${i + 1}'s name`) || `Column ${i + 1}`,
  );
  if (!Array.isArray(data.rows)) throw new NotDrawn('it has no "rows"');
  if (data.rows.length > MAX_ROWS) {
    throw new NotDrawn(`it has ${data.rows.length} rows; at most ${MAX_ROWS} are read`);
  }
  let characters = 0;
  const rows = data.rows.map((row, r): Cell[] => {
    if (!Array.isArray(row)) throw new NotDrawn(`row ${r + 1} is not a list`);
    return columns.map((_, c) => {
      const cell: unknown = row[c];
      if (cell === undefined || cell === null) return null;
      if (typeof cell === "number") {
        if (!Number.isFinite(cell)) throw new NotDrawn(`row ${r + 1} has a number that is not one`);
        return cell;
      }
      if (typeof cell === "boolean") return cell;
      if (typeof cell === "string") {
        characters += Math.min(cell.length, 2_000);
        if (characters > MAX_TABLE_CHARACTERS) {
          throw new NotDrawn(
            `it holds more than ${MAX_TABLE_CHARACTERS.toLocaleString("en")} characters`,
          );
        }
        return cell.length > 2_000 ? `${cell.slice(0, 1_999)}…` : cell;
      }
      throw new NotDrawn(`row ${r + 1} has a cell that is neither words nor a number`);
    });
  });
  return { kind: "table", title: label(data.title, "title"), columns, rows };
}

/**
 * The chart or table `data` asks for; `NotDrawn`, saying why, when it is not
 * one. `loose` (a ```chart block) lets "workbench" and "version" be left out.
 */
export function readSpec(data: unknown, loose = false): Spec {
  if (!isRecord(data)) throw new NotDrawn("it is not a JSON object");
  const kind = data.workbench ?? (loose ? (Array.isArray(data.rows) ? "table" : "chart") : null);
  if (kind !== "chart" && kind !== "table")
    throw new NotDrawn('it does not say "chart" or "table"');
  const version = data.version ?? (loose ? 1 : null);
  if (version !== 1) {
    throw new NotDrawn(`it is version ${JSON.stringify(version)}; this page draws version 1`);
  }
  return kind === "chart" ? chart(data) : table(data);
}

/** A ```chart block's source read as JSON, then as a chart or table. */
export function readSource(source: string): Spec {
  if (source.length > 200_000) throw new NotDrawn("it is longer than 200,000 characters");
  let data: unknown;
  try {
    data = JSON.parse(source);
  } catch (error) {
    throw new NotDrawn(
      `it is not JSON (${error instanceof Error ? error.message : String(error)})`.slice(0, 200),
    );
  }
  return readSpec(data, true);
}
