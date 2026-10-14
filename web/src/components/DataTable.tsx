/**
 * A table of data (workbench-rich-content-roadmap.md RC4): a tool's table, or
 * the numbers behind a chart. Sorted by a column at a click; at most SHOWN
 * rows are on the page, saying how many there are, and every row is in the
 * CSV, written as data and never as a spreadsheet's formulas (lib/csv.ts).
 */

import { ArrowDown, ArrowUp, Download } from "lucide-react";
import { useMemo, useState } from "react";

import type { Cell } from "../lib/chartSpec";
import { toCsv } from "../lib/csv";

export const SHOWN = 200;

// One collator, numbers in words in their order ("row 2" before "row 10").
const COLLATOR = new Intl.Collator(undefined, { numeric: true });

function compare(a: Cell, b: Cell): number {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return COLLATOR.compare(String(a), String(b));
}

/** A file name from a title. */
export function csvName(title: string): string {
  const words = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `${words || "data"}.csv`;
}

export function saveCsv(name: string, columns: string[], rows: Cell[][]): void {
  const url = URL.createObjectURL(new Blob([toCsv(columns, rows)], { type: "text/csv" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function DataTable({
  columns,
  rows,
  title,
  csv = true,
}: {
  columns: string[];
  rows: Cell[][];
  title: string;
  /** Whether it offers its own CSV (a chart's numbers are downloaded from its head). */
  csv?: boolean;
}) {
  const [sort, setSort] = useState<{ column: number; up: boolean } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const out = [...rows].sort((a, b) => compare(a[sort.column] ?? null, b[sort.column] ?? null));
    return sort.up ? out : out.reverse();
  }, [rows, sort]);
  const shown = sorted.slice(0, SHOWN);
  return (
    <div className="data-table" data-testid="data-table">
      <div className="data-table-head">
        <span className="text-muted">
          {rows.length > SHOWN
            ? `Showing ${SHOWN.toLocaleString("en")} of ${rows.length.toLocaleString("en")} rows; the CSV has them all.`
            : `${rows.length.toLocaleString("en")} ${rows.length === 1 ? "row" : "rows"}`}
        </span>
        {csv && (
          <button
            type="button"
            className="act"
            aria-label={`Download ${title || "the data"} as CSV`}
            onClick={() => saveCsv(csvName(title), columns, sorted)}
          >
            <Download size={14} aria-hidden />
            CSV
          </button>
        )}
      </div>
      {/* A focusable scroller, so a keyboard can reach a table wider than its box. */}
      <div
        className="data-table-scroll"
        tabIndex={0}
        role="region"
        aria-label={`${title || "Data"}, scrolls`}
      >
        <table>
          {title && <caption className="sr-only">{title}</caption>}
          <thead>
            <tr>
              {columns.map((name, c) => {
                const active = sort?.column === c;
                return (
                  <th
                    key={c}
                    scope="col"
                    aria-sort={active ? (sort.up ? "ascending" : "descending") : "none"}
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setSort(active ? { column: c, up: !sort.up } : { column: c, up: true })
                      }
                    >
                      {name}
                      {active &&
                        (sort.up ? (
                          <ArrowUp size={12} aria-hidden />
                        ) : (
                          <ArrowDown size={12} aria-hidden />
                        ))}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {shown.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className={typeof cell === "number" ? "num" : undefined}>
                    {cell === null ? "" : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
