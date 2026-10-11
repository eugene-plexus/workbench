import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** A box of plain text that scrolls, and is a keyboard stop only while it
 * does (a box with nothing to scroll has nothing for the arrow keys to do).
 * It is the dashed box derived text sits in: what a model was given, and what
 * Workbench read from a file. */
export function ScrollText({
  label,
  testId,
  text,
}: {
  label: string;
  testId: string;
  text: string;
}) {
  const box = useRef<HTMLPreElement>(null);
  const [scrolls, setScrolls] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (el) setScrolls(el.scrollHeight > el.clientHeight + 1);
  }, [text]);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const watch = new ResizeObserver(() => setScrolls(el.scrollHeight > el.clientHeight + 1));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  return (
    <pre
      ref={box}
      role="group"
      aria-label={label}
      tabIndex={scrolls ? 0 : undefined}
      data-testid={testId}
      dir="auto"
      className="reasoning-body mt-2 whitespace-pre-wrap font-mono text-[0.8125rem] leading-5 wrap-anywhere"
    >
      {text}
    </pre>
  );
}
