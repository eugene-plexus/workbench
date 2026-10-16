const KEY = "workbench-requested-model";

/** Keep a console's model choice through sign-in; opening a link creates no chat. */
export function requestedModel(): string | null {
  const query = new URLSearchParams(window.location.search).get("model");
  try {
    if (query && query.length <= 256) {
      sessionStorage.setItem(KEY, query);
      return query;
    }
    return sessionStorage.getItem(KEY);
  } catch {
    return query && query.length <= 256 ? query : null;
  }
}

export function clearRequestedModel(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* Storage may be disabled. */
  }
  const url = new URL(window.location.href);
  url.searchParams.delete("model");
  window.history.replaceState(null, "", url.pathname + url.search + url.hash);
}
