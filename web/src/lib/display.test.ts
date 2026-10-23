import { createElement } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { DisplaySettings } from "../components/DisplaySettings";
import { Chart } from "../components/Chart";
import { Markdown } from "../components/Markdown";
import { setDiagramEngine } from "./diagramClient";
import { readSource } from "./chartSpec";
import type { DiagramEngine } from "./diagramClient";
import { readDisplay, setDisplay, useTheme } from "./display";

beforeEach(() => {
  localStorage.clear();
  setDisplay({ textSize: 100, width: "comfortable", theme: "system", speakers: false });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.documentElement.style.cssText = "";
  setDiagramEngine(null);
});

it("theme_override_reaches_diagram_and_chart", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const paletteStyle = document.createElement("style");
  paletteStyle.textContent =
    'html[data-theme="light"] { --panel: #ffffff; } html[data-theme="dark"] { --panel: #141619; }';
  document.head.append(paletteStyle);
  const draw = vi.fn<DiagramEngine["draw"]>(async () => ({ error: "test drawing" }));
  setDiagramEngine({ draw });
  const { result } = renderHook(() => useTheme());
  act(() => setDisplay({ theme: "light" }));
  expect(result.current).toBe("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  render(createElement(Markdown, { text: "```mermaid\nflowchart TD\n A --> B\n```" }));
  await waitFor(() => expect(draw).toHaveBeenCalled());
  expect(draw.mock.calls[0]?.[2]).toBe(false);
  expect(draw.mock.calls[0]?.[1].background).toBe("#ffffff");
  paletteStyle.remove();
  const { container } = render(
    createElement(Chart, {
      spec: readSource(
        '{"type":"bar","series":[{"name":"One","points":[["A",2]]}]}',
      ) as import("./chartSpec").ChartSpec,
    }),
  );
  expect(container.querySelector("svg")).toHaveAttribute("data-theme", "light");
});

it("text size label shows effective px", () => {
  document.documentElement.style.fontSize = "20px";
  render(createElement(DisplaySettings));
  expect(screen.getByRole("option", { name: "100% (21.25 px here)" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Text size"), { target: { value: "125" } });
  expect(document.documentElement.style.getPropertyValue("--text-read")).toBe("1.328125rem");
  expect(screen.getByLabelText("Text size")).toHaveValue("125");
});

it("storage blocked keeps the setting for the page", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  expect(readDisplay().stored).toBe(false);
  const first = render(createElement(DisplaySettings));
  fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
  expect(screen.getByText("Not kept: this browser blocks storage.")).toBeInTheDocument();
  first.unmount();
  render(createElement(DisplaySettings));
  expect(screen.getByLabelText("Theme")).toHaveValue("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
});

it("a blocked storage event keeps this page's effective settings", () => {
  render(createElement(DisplaySettings));
  fireEvent.change(screen.getByLabelText("Theme"), { target: { value: "light" } });
  fireEvent.change(screen.getByLabelText("Reading width"), { target: { value: "narrow" } });
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  act(() => window.dispatchEvent(new StorageEvent("storage", { key: "workbench.display" })));
  expect(screen.getByLabelText("Theme")).toHaveValue("light");
  expect(screen.getByLabelText("Reading width")).toHaveValue("narrow");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  expect(document.documentElement.style.getPropertyValue("--measure")).toBe("34rem");
  expect(screen.getByText("Not kept: this browser blocks storage.")).toBeInTheDocument();
});

it("text size label follows the browser size without a window resize", async () => {
  document.documentElement.style.fontSize = "16px";
  render(createElement(DisplaySettings));
  expect(screen.getByRole("option", { name: "100% (17 px here)" })).toBeInTheDocument();
  document.documentElement.style.fontSize = "20px";
  await waitFor(() =>
    expect(screen.getByRole("option", { name: "100% (21.25 px here)" })).toBeInTheDocument(),
  );
});

it("ignores malformed settings without claiming storage is blocked", () => {
  localStorage.setItem("workbench.display", "not JSON");
  expect(readDisplay()).toEqual({
    textSize: 100,
    width: "comfortable",
    theme: "system",
    speakers: false,
    stored: true,
  });
});

it("follows system changes only while System is chosen", () => {
  let light = false;
  let changed = () => {};
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return light;
    },
    addEventListener(_event: string, listener: () => void) {
      changed = listener;
    },
    removeEventListener() {},
  }));
  const { result } = renderHook(() => useTheme());
  expect(result.current).toBe("dark");
  act(() => {
    light = true;
    changed();
  });
  expect(result.current).toBe("light");
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  act(() => setDisplay({ theme: "dark" }));
  act(() => {
    light = false;
    changed();
    light = true;
    changed();
  });
  expect(result.current).toBe("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
});

it("reconciles a system theme that changed before the first subscription", () => {
  let light = true;
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return light;
    },
    addEventListener() {},
    removeEventListener() {},
  }));
  setDisplay({ theme: "system" });
  expect(document.documentElement).toHaveAttribute("data-theme", "light");
  // A browser can finish applying its preference between module evaluation
  // and React mounting. No subscriber was present to hear that change.
  light = false;
  const { result } = renderHook(() => useTheme());
  expect(result.current).toBe("dark");
  expect(document.documentElement).toHaveAttribute("data-theme", "dark");
});
