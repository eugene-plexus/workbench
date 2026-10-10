import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

async function copy(text: string): Promise<void> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // The selection-based fallback also works on a local network's HTTP address.
  }
  const before = document.activeElement;
  const selection = window.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i))
    : [];
  const field = document.createElement("textarea");
  field.value = text;
  field.className = "clipboard-field";
  document.body.appendChild(field);
  field.select();
  try {
    if (!document.execCommand("copy")) throw new Error("Clipboard refused");
  } finally {
    field.remove();
    if (before instanceof HTMLElement) before.focus({ preventScroll: true });
    selection?.removeAllRanges();
    ranges.forEach((range) => selection?.addRange(range));
  }
}

export function CopyButton({
  text,
  label,
}: {
  text: string;
  /** What it copies, when "Copy" alone repeats on the page: named for
   * screen readers ("Copy this answer"); the visible word stays "Copy". */
  label?: string;
}) {
  const [state, setState] = useState<"ready" | "copied" | "failed">("ready");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <span className="relative inline-flex flex-wrap items-center gap-2">
      <button
        type="button"
        className="flex items-center gap-1 rounded-plexus px-1 py-0.5 hover:text-fg"
        aria-label={label ? `Copy ${label}` : undefined}
        onClick={async () => {
          window.clearTimeout(timer.current);
          try {
            await copy(text);
            setState("copied");
            timer.current = window.setTimeout(() => setState("ready"), 2000);
          } catch {
            setState("failed");
          }
        }}
      >
        {state === "copied" ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
        {state === "copied" ? "Copied" : "Copy"}
      </button>
      <span role="status" className={state === "failed" ? "text-error" : "sr-only"}>
        {state === "failed"
          ? "Copy was blocked. Select the text and copy it manually."
          : state === "copied"
            ? "Copied to clipboard."
            : ""}
      </span>
    </span>
  );
}
