/** First-run evidence for engines where a looping preview can freeze the chat.
 * Keep identities only, oldest first, under one key; storage is optional. */
const STORE = "workbench.previews.unfinished";
const LIMIT = 200;

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
  if (keys) write([...keys.filter((kept) => kept !== key), key].slice(-LIMIT));
}

export function markPreviewFinished(key: string): void {
  if (!usesPreviewMarkers()) return;
  const keys = read();
  if (keys?.includes(key)) write(keys.filter((kept) => kept !== key));
}
