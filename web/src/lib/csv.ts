/**
 * A table or a chart's data as CSV (RFC 4180), as data and never as a
 * spreadsheet's formulas: a cell a spreadsheet would read as one (it starts
 * with `=`, `+`, `-`, `@`, a tab or a return) is written after an apostrophe,
 * as OWASP's guidance for CSV injection has it. A number is written as the
 * number it is.
 */

import type { Cell } from "./chartSpec";

// Leading spaces first: some spreadsheets read ` =1` as a formula too.
const FORMULA = /^\s*[=+\-@]|^[\t\r]/;

export function csvCell(cell: Cell): string {
  if (cell === null) return "";
  if (typeof cell === "number" || typeof cell === "boolean") return String(cell);
  const text = FORMULA.test(cell) ? `'${cell}` : cell;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  return [columns, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
