/**
 * A chart or a table asked for in so many words (workbench-rich-content-
 * roadmap.md RC4), with its numbers a click away and a CSV of them: from a
 * `chart` fence in an answer (with its source too), or from a tool's data.
 */

import { Code, Download, Table2 } from "lucide-react";
import { useMemo, useState } from "react";

import type { Cell, ChartSpec, Spec } from "../lib/chartSpec";
import { Chart, Legend } from "./Chart";
import { csvName, DataTable, saveCsv } from "./DataTable";

/** A chart's numbers as a table: one row a point. */
export function chartRows(spec: ChartSpec): { columns: string[]; rows: Cell[][] } {
  return {
    columns: ["Series", spec.xLabel || "x", spec.yLabel || "y"],
    rows: spec.series.flatMap((s) => s.points.map((p): Cell[] => [s.name, p.x, p.y])),
  };
}

export function Structured({ spec, source }: { spec: Spec; source?: string }) {
  const [showData, setShowData] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const data = useMemo(() => (spec.kind === "chart" ? chartRows(spec) : spec), [spec]);
  const kind = spec.kind === "chart" ? "chart" : "table";
  return (
    <figure className="structured" data-testid={`structured-${kind}`}>
      <div className="structured-head">
        <span className="structured-title">
          {spec.title || (kind === "chart" ? "Chart" : "Table")}
        </span>
        <span className="diagram-tools">
          {spec.kind === "chart" && (
            <button
              type="button"
              className="act"
              aria-pressed={showData}
              onClick={() => setShowData((shown) => !shown)}
            >
              <Table2 size={14} aria-hidden />
              Data
            </button>
          )}
          {source !== undefined && (
            <button
              type="button"
              className="act"
              aria-pressed={showSource}
              onClick={() => setShowSource((shown) => !shown)}
            >
              <Code size={14} aria-hidden />
              Source
            </button>
          )}
          {spec.kind === "chart" && (
            <button
              type="button"
              className="act"
              aria-label={`Download ${spec.title || "the chart's numbers"} as CSV`}
              onClick={() => saveCsv(csvName(spec.title), data.columns, data.rows)}
            >
              <Download size={14} aria-hidden />
              CSV
            </button>
          )}
        </span>
      </div>
      {showSource && source !== undefined ? (
        <pre
          tabIndex={0}
          role="region"
          aria-label={`The ${kind}'s source`}
          className="structured-source"
        >
          <code>{source}</code>
        </pre>
      ) : spec.kind === "chart" ? (
        <>
          <Legend spec={spec} />
          <Chart spec={spec} />
          {showData && (
            <DataTable columns={data.columns} rows={data.rows} title={spec.title} csv={false} />
          )}
        </>
      ) : (
        <DataTable columns={spec.columns} rows={spec.rows} title={spec.title} />
      )}
    </figure>
  );
}
