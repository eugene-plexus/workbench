/**
 * The request secret (workbench-v1.md W3).
 *
 * A browser sends Workbench's cookie to every port on its host, so a cookie
 * alone opens nothing: every call also carries this secret, which lives in
 * this origin's own storage -- kept per origin, port included -- and so never
 * reaches another service on the machine. It arrives once, in the address
 * fragment of the redirect that finishes signing in, and the fragment is
 * cleared from the address bar at once.
 */

import { clearDrafts } from "./conveniences";

export const SECRET_KEY = "workbench-secret";
export const SECRET_HEADER = "X-Workbench-Secret";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function secret(): string | null {
  try {
    return storage()?.getItem(SECRET_KEY) ?? null;
  } catch {
    return null;
  }
}

export function forget(): void {
  clearDrafts();
  try {
    storage()?.removeItem(SECRET_KEY);
  } catch {
    // Nothing stored is nothing to forget.
  }
}

/**
 * Reads what the sign-in redirect left in the fragment: the secret, or the
 * reason signing in failed. Clears the fragment either way.
 */
export function takeFragment(): { signedIn: boolean; error: string | null } {
  const hash = window.location.hash.replace(/^#/, "");
  if (!hash) return { signedIn: false, error: null };
  const params = new URLSearchParams(hash);
  const given = params.get("signin");
  const error = params.get("signin-error");
  if (given === null && error === null) return { signedIn: false, error: null };
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  if (given) {
    clearDrafts();
    try {
      storage()?.setItem(SECRET_KEY, given);
    } catch {
      return {
        signedIn: false,
        error:
          "This browser will not let Workbench keep its sign-in. Allow site storage, then sign in again.",
      };
    }
    return { signedIn: true, error: null };
  }
  return { signedIn: false, error };
}
