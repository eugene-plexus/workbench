/**
 * A person's own usage on the Settings page (row 40). Eugene's root counts the tokens and the
 * spend of every request made as them, on every gateway, and the allowances their groups set.
 * This shows its answer now, read only: the owner sets allowances and prices in Eugene.
 */

import { CircleAlert, Gauge } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { api } from "../lib/api";
import { failureWords } from "../lib/memory";
import type { UsageAllowance, UsagePage, UsageTotals } from "../lib/types";

/** The root's exact dollar string as a person reads it: "$0.0105", "$4.00". Never a float. */
export function usd(exact: string): string {
  const match = /^(\d+)\.(\d+)$/.exec(exact.trim());
  if (!match) return `$${exact}`;
  const fraction = (match[2] ?? "").replace(/0+$/, "").padEnd(2, "0");
  return `$${Number(match[1] ?? "0").toLocaleString("en-US")}.${fraction}`;
}

function totals(t: UsageTotals): string {
  if (t.requests === 0) return "nothing yet";
  const unknown = t.unpricedRequests > 0 ? `, and ${t.unpricedRequests} with no price` : "";
  return `${t.tokens.toLocaleString("en-US")} tokens, ${usd(t.costUsd)}${unknown}`;
}

export function allowanceWords(a: UsageAllowance, group: string): string {
  const money = a.kind === "costPerMonth";
  const used = money ? usd(a.used) : Number(a.used).toLocaleString("en-US");
  const limit = money ? usd(a.limit) : `${Number(a.limit).toLocaleString("en-US")} tokens`;
  const span = a.kind === "tokensPerDay" ? "today" : "this month";
  const reset = new Date(a.resetsAt).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  });
  const state = a.spent ? `Used up: new requests wait until ${reset}.` : `Resets on ${reset}.`;
  return `${group}: ${used} of ${limit} ${span}. ${state}`;
}

export function YourUsage() {
  const uid = useId();
  const [page, setPage] = useState<UsagePage | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api<UsagePage>("/api/usage")
      .then((answer) => {
        setPage(answer);
        setProblem(null);
      })
      .catch((failure) => setProblem(failureWords(failure)));
  }, []);

  if (problem) {
    return (
      <p role="alert" className="notice notice--error">
        <CircleAlert size={16} aria-hidden />
        {problem}
      </p>
    );
  }
  if (!page) return null;
  const me = page.known ? page.report.subjects.find((s) => s.kind === "person") : undefined;
  const groups = page.known ? page.report.subjects.filter((s) => s.kind === "group") : [];
  return (
    <section aria-labelledby={`${uid}-title`} data-testid="your-usage" className="section">
      <h2 id={`${uid}-title`} className="title-section">
        <Gauge size={18} aria-hidden />
        Your usage
      </h2>
      {!page.known ? (
        <p className="help">{page.why}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          <li data-testid="usage-today">Today: {me ? totals(me.day) : "nothing yet"}.</li>
          <li data-testid="usage-month">This month: {me ? totals(me.month) : "nothing yet"}.</li>
          {groups.flatMap((g) =>
            g.allowances.map((a) => (
              <li
                key={`${g.id}-${a.kind}`}
                data-testid={`usage-allowance-${g.id}-${a.kind}`}
                className={a.spent ? "notice notice--error" : undefined}
              >
                {allowanceWords(a, `Your group ${g.name}`)}
              </li>
            )),
          )}
        </ul>
      )}
      <p className="help">
        Counted by Eugene on every machine, for everything you send from your apps. Days are UTC.
      </p>
    </section>
  );
}
