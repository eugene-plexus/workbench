/** First-run evidence for engines where a looping preview can freeze the chat.
 * Keep identities only, oldest first, under one key; storage is optional. */
const STORE = "workbench.previews.unfinished";
const LIMIT = 200;
// Only keys begun by this page; sent stays sent until a first run finishes.
// Markers from an earlier page stay held.
const stages = new Map<string, "begun" | "sent">();

export function usesPreviewMarkers(userAgent = navigator.userAgent): boolean {
  // Match the engines as diagramClient's palette does. No feature check tells
  // whether the browser puts a sandboxed preview on the chat's main thread.
  return (
    /Firefox\//.test(userAgent) ||
    (/AppleWebKit\//.test(userAgent) && !/Chrome\/|Chromium\//.test(userAgent))
  );
}

function read(): string[] | null {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORE);
  } catch {
    return null;
  }
  try {
    const keys: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(keys)
      ? [...new Set(keys.filter((key): key is string => typeof key === "string"))].slice(-LIMIT)
      : [];
  } catch {
    return [];
  }
}

function write(keys: string[]): void {
  try {
    window.localStorage.setItem(STORE, JSON.stringify(keys));
  } catch {
    // Storage blocked or full: previews behave as they did without markers.
  }
}

export function previewWasUnfinished(key: string): boolean {
  return usesPreviewMarkers() && (read()?.includes(key) ?? false);
}

export function markPreviewStarted(key: string): void {
  if (!usesPreviewMarkers()) return;
  const keys = read();
  if (keys) {
    const kept = [...keys.filter((other) => other !== key), key].slice(-LIMIT);
    write(kept);
    if (!stages.has(key)) stages.set(key, "begun");
    for (const marked of stages.keys()) if (!kept.includes(marked)) stages.delete(marked);
  }
}

export function markPreviewSent(key: string): void {
  if (usesPreviewMarkers() && stages.has(key)) stages.set(key, "sent");
}

export function markPreviewFinished(key: string): void {
  if (!usesPreviewMarkers()) return;
  stages.delete(key);
  const keys = read();
  if (keys?.includes(key)) write(keys.filter((kept) => kept !== key));
}

/** An unsent run cannot have looped. A sent run needs its frame's reply. */
export function clearUnsentPreview(key: string): void {
  if (stages.get(key) === "begun") markPreviewFinished(key);
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    // A browser may dispatch this after terminating a loop. Only a preview
    // whose run was never sent is safe to forget here.
    for (const key of stages.keys()) clearUnsentPreview(key);
  });
}
