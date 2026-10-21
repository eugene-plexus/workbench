import { useEffect, useState } from "react";
import { setDisplay, useDisplay, useSystemTheme, type Display } from "../lib/display";

export function DisplaySettings() {
  const display = useDisplay();
  const system = useSystemTheme();
  const [browserPx, setBrowserPx] = useState(
    () => parseFloat(getComputedStyle(document.documentElement).fontSize) || 16,
  );
  useEffect(() => {
    const read = () =>
      setBrowserPx(parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);
    window.addEventListener("resize", read);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(read);
    observer?.observe(document.documentElement);
    return () => {
      window.removeEventListener("resize", read);
      observer?.disconnect();
    };
  }, []);
  return (
    <details className="border-t border-line px-4 py-2 text-sm">
      <summary className="cursor-pointer py-1 font-medium text-accent">Display</summary>
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
