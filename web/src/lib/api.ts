/**
 * Every call to Workbench's own API, with the request secret (session.ts).
 * A 401 is a sign-out and carries the reason, which the sign-in screen
 * shows; every other refusal carries the sentence the server wrote.
 */

import { forget, SECRET_HEADER, secret } from "./session";

export class SignedOut extends Error {
  constructor(
    message: string,
    readonly reason: string,
  ) {
    super(message);
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** The refusal's own fields when the server sent some beside its sentence
     * (a note refused says `reason`, and for a secret `looksLike`). */
    readonly detail?: Record<string, unknown>,
  ) {
    super(message);
  }
}

type Listener = (error: SignedOut) => void;
const listeners = new Set<Listener>();

/** Called whenever any call finds the person signed out. */
export function onSignedOut(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Signs the page out everywhere it is shown, saying why. */
export function announce(error: SignedOut): void {
  forget();
  for (const listener of listeners) listener(error);
}

export function headers(extra?: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = { ...extra };
  const given = secret();
  if (given) out[SECRET_HEADER] = given;
  return out;
}

/** What a refusal says: the sentence, and the `detail` object it came in. */
async function refusalOf(
  response: Response,
): Promise<{ message: string; detail?: Record<string, unknown> }> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail?.message === "string") return { message: detail.message, detail };
    if (typeof detail === "string") return { message: detail };
  } catch {
    // Not JSON: fall back to the status.
  }
  return { message: `Workbench answered ${response.status}.` };
}

/** The sentence in a refusal: `{detail: {message}}`, or the status. */
export async function problemOf(response: Response): Promise<string> {
  return (await refusalOf(response)).message;
}

export async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  if (response.status === 401) {
    let reason = "none";
    let message = "Sign in with Eugene to use Workbench.";
    try {
      const detail = (await response.json())?.detail;
      if (typeof detail?.reason === "string") reason = detail.reason;
      if (typeof detail?.message === "string") message = detail.message;
    } catch {
      // A bare 401 is still a sign-out.
    }
    const error = new SignedOut(message, reason);
    announce(error);
    throw error;
  }
  const { message, detail } = await refusalOf(response);
  throw new ApiError(message, response.status, detail);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const extra: Record<string, string> = {};
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    extra["Content-Type"] = "application/json";
  }
  const response = await checked(
    await fetch(path, {
      ...init,
      credentials: "same-origin",
      headers: headers({ ...extra, ...(init.headers as Record<string, string> | undefined) }),
    }),
  );
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const del = (path: string) => api<void>(path, { method: "DELETE" });

/** A file the server keeps, fetched with the secret and shown from a blob:
 * address, since an `<img src>` cannot carry a header. */
export async function fileUrl(id: string): Promise<string> {
  const response = await checked(
    await fetch(`/api/files/${encodeURIComponent(id)}`, {
      credentials: "same-origin",
      headers: headers(),
    }),
  );
  return URL.createObjectURL(await response.blob());
}

/** What Workbench read from a document, as text (`GET /api/files/{id}/text`),
 * fetched with the secret since a plain link cannot carry the header. A PDF's
 * pages are marked `[page N]`. */
export async function fileText(id: string): Promise<string> {
  const response = await checked(
    await fetch(`/api/files/${encodeURIComponent(id)}/text`, {
      credentials: "same-origin",
      headers: headers(),
    }),
  );
  return response.text();
}

/** A file the server makes on request (the memory export), fetched with the
 * secret, since a plain link cannot carry the header. `name` is the one the
 * server gave it, when it gave one. */
export async function fileOf(path: string): Promise<{ blob: Blob; name: string | null }> {
  const response = await checked(
    await fetch(path, { credentials: "same-origin", headers: headers() }),
  );
  const named = /filename="?([^";]+)"?/i.exec(response.headers.get("Content-Disposition") ?? "");
  return { blob: await response.blob(), name: named?.[1] ?? null };
}
