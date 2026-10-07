/**
 * Passkeys for job sites (J14a.3, `person-held-keys.md` §4.2, §12.5).
 *
 * The person's passkey lives in their own authenticator; Workbench is the
 * WebAuthn relying party at its HTTPS address. Pairing binds the passkey to a
 * machine with the code shown there: this page computes the MAC from the code
 * in the browser (`SitePasskeyBinding`, site-host.yaml), so the code never
 * leaves it. An approval is an assertion whose challenge is SHA-256 of the
 * envelope the machine wrote; the machine checks it.
 */

/** As the contract says: PBKDF2-HMAC-SHA256, 600000 rounds, 32 bytes. */
export const ITERATIONS = 600_000;
export const BINDING_TYPE = "eugene-plexus/site-passkey";
/** ES256 first: every browser can read its public key back. */
const ALGORITHMS = [-7, -8, -257] as const;

export function b64url(data: ArrayBuffer | Uint8Array): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64(data: ArrayBuffer | Uint8Array): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let text = "";
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text);
}

export function fromB64url(text: string): Uint8Array<ArrayBuffer> {
  const padded =
    text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** As a person types it: any case, a dash or spaces, O for 0, I or L for 1. */
export function normalizeCode(code: string): string {
  return code.toUpperCase().replace(/[-\s]/g, "").replace(/O/g, "0").replace(/[IL]/g, "1");
}

/** A code of ten characters of Crockford's base32, as the machine shows it. */
export function looksLikeCode(code: string): boolean {
  return /^[0-9A-HJKMNP-TV-Z]{10}$/.test(normalizeCode(code));
}

export interface Binding {
  site: string;
  person: string;
  credentialId: string;
  publicKey: string;
  alg: number;
  rpId: string;
}

/** The canonical object the MAC covers: keys sorted, no spaces. */
export function binding(value: Binding): string {
  const object: Record<string, string | number> = {
    typ: BINDING_TYPE,
    v: 1,
    site: value.site,
    person: value.person,
    credentialId: value.credentialId,
    publicKey: value.publicKey,
    alg: value.alg,
    rpId: value.rpId,
  };
  const sorted = Object.keys(object).sort();
  return `{${sorted.map((k) => `${JSON.stringify(k)}:${JSON.stringify(object[k])}`).join(",")}}`;
}

export async function pairingMac(code: string, value: Binding): Promise<string> {
  const encoder = new TextEncoder();
  const secret = await crypto.subtle.importKey(
    "raw",
    encoder.encode(normalizeCode(code)),
    "PBKDF2",
    false,
    ["deriveKey"],
  );
  const key = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt: encoder.encode(`${BINDING_TYPE}:${value.site}:${value.person}`),
      iterations: ITERATIONS,
    },
    secret,
    { name: "HMAC", hash: "SHA-256", length: 256 },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, encoder.encode(binding(value)));
  return b64url(mac);
}

/** Whether this page can make and use a passkey at all. */
export function passkeysHere(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator.credentials?.create === "function"
  );
}

export interface Made {
  credentialId: string;
  publicKey: string;
  alg: number;
}

/** Make a passkey for `person` at this relying party. */
export async function makePasskey(options: {
  rpId: string;
  person: string;
  name: string;
  exclude: string[];
}): Promise<Made> {
  const credential = (await navigator.credentials.create({
    publicKey: {
      rp: { id: options.rpId, name: "Eugene Workbench" },
      user: {
        id: new TextEncoder().encode(options.person),
        name: options.name,
        displayName: options.name,
      },
      challenge: crypto.getRandomValues(new Uint8Array(32)),
      pubKeyCredParams: ALGORITHMS.map((alg) => ({ type: "public-key" as const, alg })),
      authenticatorSelection: { residentKey: "preferred", userVerification: "required" },
      attestation: "none",
      excludeCredentials: options.exclude.map((id) => ({
        type: "public-key" as const,
        id: fromB64url(id),
      })),
      timeout: 120_000,
    },
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("No passkey was made.");
  const response = credential.response as AuthenticatorAttestationResponse;
  const spki = response.getPublicKey();
  const alg = response.getPublicKeyAlgorithm();
  if (!spki || !ALGORITHMS.includes(alg as (typeof ALGORITHMS)[number])) {
    throw new Error(
      "This browser could not read the passkey it made. Try another browser, or a passkey on your phone.",
    );
  }
  return { credentialId: b64url(credential.rawId), publicKey: b64(spki), alg };
}

export interface Signed {
  credentialId: string;
  authenticatorData: string;
  clientDataJSON: string;
  signature: string;
}

/** Approve one envelope with the passkey `credentialId`. */
export async function signEnvelope(
  envelope: string,
  credentialId: string,
  rpId: string,
): Promise<Signed> {
  const challenge = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(envelope));
  const credential = (await navigator.credentials.get({
    publicKey: {
      challenge,
      rpId,
      allowCredentials: [{ type: "public-key", id: fromB64url(credentialId) }],
      userVerification: "required",
      timeout: 120_000,
    },
  })) as PublicKeyCredential | null;
  if (!credential) throw new Error("The passkey did not answer.");
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    credentialId: b64url(credential.rawId),
    authenticatorData: b64url(response.authenticatorData),
    clientDataJSON: b64url(response.clientDataJSON),
    signature: b64url(response.signature),
  };
}

/** Which passkey this browser made for a machine, remembered here. */
export function rememberedPasskey(site: string): string | null {
  try {
    return window.localStorage.getItem(`eugene.passkey.${site}`);
  } catch {
    return null;
  }
}

export function rememberPasskey(site: string, key: string): void {
  try {
    window.localStorage.setItem(`eugene.passkey.${site}`, key);
  } catch {
    // A browser that keeps nothing asks which passkey next time.
  }
}

/** A browser's words for an authenticator's refusal. */
export function passkeyProblem(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError")
      return "The passkey was not used: it was cancelled, or took too long.";
    if (error.name === "InvalidStateError") return "That passkey is already paired here.";
    if (error.name === "SecurityError")
      return "This address cannot hold a passkey. Open Workbench at its https address.";
  }
  return error instanceof Error ? error.message : String(error);
}
