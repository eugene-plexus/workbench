/**
 * A formula in an answer (workbench-rich-content-roadmap.md RC1): its source
 * while it is laid out, then the formula, with MathML for a screen reader.
 * One that cannot be shown stays its source, saying why, and the answer
 * around it is untouched.
 */

import { Component, memo, type ReactNode } from "react";
import { useFormula } from "../lib/mathClient";

/** A formula that fails while it is drawn is its source, not a blank answer. */
class FormulaBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function Failed({ tex, reason }: { tex: string; reason: string }) {
  return (
    <code className="math-failed" title={`Formula not shown: ${reason}`}>
      {tex}
      <span className="sr-only"> (formula not shown: {reason})</span>
    </code>
  );
}

function Shown({
  tex,
  display,
  failed,
}: {
  tex: string;
  display: boolean;
  failed?: (tex: string, reason: string) => ReactNode;
}) {
  const formula = useFormula(tex, display);
  if (formula.state === "failed") {
    return failed ? failed(tex, formula.reason) : <Failed tex={tex} reason={formula.reason} />;
  }
  const pending = formula.state === "pending";
  const className = `${display ? "math-display" : "math-inline"}${pending ? " math-pending" : ""}`;
  // A wide display formula scrolls in its own box, which a keyboard can reach.
  return (
    <span className={className} tabIndex={display && !pending ? 0 : undefined}>
      {pending ? tex : formula.node}
    </span>
  );
}

/**
 * `failed`, when given, draws a formula that cannot be shown (a block shows
 * its source as a code block); otherwise it is its source as inline code.
 */
export const MathFormula = memo(function MathFormula({
  tex,
  display,
  failed,
}: {
  tex: string;
  display: boolean;
  failed?: (tex: string, reason: string) => ReactNode;
}) {
  const fallback = failed ? (
    failed(tex, "it could not be drawn")
  ) : (
    <Failed tex={tex} reason="it could not be drawn" />
  );
  return (
    <FormulaBoundary fallback={fallback}>
      <Shown tex={tex} display={display} failed={failed} />
    </FormulaBoundary>
  );
});
