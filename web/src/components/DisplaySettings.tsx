import { useEffect, useRef, useState } from "react";
import { setDisplay, useDisplay, useSystemTheme, type Display } from "../lib/display";

export function DisplaySettings() {
  const display = useDisplay();
  const system = useSystemTheme();
  const sizeProbe = useRef<HTMLSpanElement>(null);
  const [browserPx, setBrowserPx] = useState(
    () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16,
  );
  useEffect(() => {
    const read = () =>
      setBrowserPx(parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);
    window.addEventListener("resize", read);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(read);
    // The root's viewport box can stay the same when the browser's font size
    // changes. A one-rem box changes with the type even without a resize.
    if (sizeProbe.current) observer?.observe(sizeProbe.current);
    const mutation = new MutationObserver(read);
    mutation.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["style", "class"],
    });
    return () => {
      window.removeEventListener("resize", read);
      observer?.disconnect();
      mutation.disconnect();
    };
  }, []);
  return (
    <details className="border-t border-line px-4 py-2 text-sm">
      <summary className="cursor-pointer py-1 font-medium text-accent">Display</summary>
      <span ref={sizeProbe} aria-hidden="true" className="invisible absolute h-4 w-4" />
      <div className="flex max-h-[45dvh] flex-col gap-3 overflow-y-auto py-2">
        <label className="flex flex-col gap-1">
          Text size
          <select
            className="control"
            value={display.textSize}
            onChange={(event) =>
              setDisplay({ textSize: Number(event.target.value) as Display["textSize"] })
            }
          >
            {([90, 100, 110, 125, 150] as const).map((size) => (
              <option key={size} value={size}>
                {size}% ({Number(((browserPx * 1.0625 * size) / 100).toFixed(2))} px here)
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Reading width
          <select
            className="control"
            value={display.width}
            onChange={(event) => setDisplay({ width: event.target.value as Display["width"] })}
          >
            <option value="narrow">Narrow (34 rem)</option>
            <option value="comfortable">Comfortable (36 rem)</option>
            <option value="full">Full</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Theme
          <select
            className="control"
            value={display.theme}
            onChange={(event) => setDisplay({ theme: event.target.value as Display["theme"] })}
          >
            <option value="system">System ({system} now)</option>
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Speaker labels
          <select
            className="control"
            value={display.speakers ? "on" : "off"}
            onChange={(event) => setDisplay({ speakers: event.target.value === "on" })}
          >
            <option value="off">Off</option>
            <option value="on">On</option>
          </select>
        </label>
        {!display.stored && (
          <p role="status" className="text-warn">
            Not kept: this browser blocks storage.
          </p>
        )}
      </div>
    </details>
  );
}
