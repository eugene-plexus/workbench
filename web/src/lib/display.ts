import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
export type Display = {
  textSize: 90 | 100 | 110 | 125 | 150;
  width: "narrow" | "comfortable" | "full";
  theme: "system" | Theme;
  speakers: boolean;
};
const KEY = "workbench.display";
const DEFAULTS: Display = { textSize: 100, width: "comfortable", theme: "system", speakers: false };
const listeners = new Set<() => void>();

/** Ignore malformed settings; a blocked read must still leave a usable page. */
export function readDisplay(): Display & { stored: boolean } {
  let saved: string | null;
  try {
    saved = localStorage.getItem(KEY);
  } catch {
    return { ...DEFAULTS, stored: false };
  }
  try {
    const value = JSON.parse(saved ?? "null") ?? {};
    return {
      textSize: [90, 100, 110, 125, 150].includes(value.textSize) ? value.textSize : 100,
      width: ["narrow", "comfortable", "full"].includes(value.width) ? value.width : "comfortable",
      theme: ["system", "dark", "light"].includes(value.theme) ? value.theme : "system",
      speakers: value.speakers === true,
      stored: true,
    };
  } catch {
    return { ...DEFAULTS, stored: true };
  }
}

let current = readDisplay();
function media() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: light)")
    : null;
}
function systemTheme(): Theme {
  return media()?.matches ? "light" : "dark";
}
function effectiveTheme(): Theme {
  return current.theme === "system" ? systemTheme() : current.theme;
}
function apply() {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = effectiveTheme();
  root.style.setProperty("--text-read", `${(1.0625 * current.textSize) / 100}rem`);
  root.style.setProperty(
    "--measure",
    { narrow: "34rem", comfortable: "36rem", full: "100%" }[current.width],
  );
}
apply();

function emit() {
  apply();
  listeners.forEach((listener) => listener());
}
let stopListening = () => {};
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (listeners.size === 1) {
    const query = media();
    const changed = () => emit();
    const storage = (event: StorageEvent) => {
      if (event.key !== KEY && event.key !== null) return;
      current = readDisplay();
      emit();
    };
    query?.addEventListener("change", changed);
    window.addEventListener("storage", storage);
    stopListening = () => {
      query?.removeEventListener("change", changed);
      window.removeEventListener("storage", storage);
    };
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) stopListening();
  };
}

export function setDisplay(change: Partial<Display>): void {
  const next: Display = {
    textSize: current.textSize,
    width: current.width,
    theme: current.theme,
    speakers: current.speakers,
    ...change,
  };
  let stored = true;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    stored = false;
  }
  current = { ...next, stored };
  emit();
}
export function useDisplay() {
  return useSyncExternalStore(
    subscribe,
    () => current,
    () => current,
  );
}
/** All colour readers use the same effective choice, including System. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, effectiveTheme, () => "dark");
}
export function useSystemTheme(): Theme {
  return useSyncExternalStore(subscribe, systemTheme, () => "dark");
}
