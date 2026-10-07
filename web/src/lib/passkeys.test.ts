import { b64url, binding, fromB64url, looksLikeCode, normalizeCode, pairingMac } from "./passkeys";

const VALUE = {
  site: "s-aaaaaaaaaaaaaaaaaaaaaaaaaa",
  person: "person-ada",
  credentialId: "Y3JlZGVudGlhbA",
  publicKey: "cHVibGljIGtleQ==",
  alg: -7,
  rpId: "workbench.example",
};

it("writes the MAC's object exactly as the machine does (keys sorted, no spaces)", () => {
  // The same string the site host's `passkeys.binding` makes.
  expect(binding(VALUE)).toBe(
    '{"alg":-7,"credentialId":"Y3JlZGVudGlhbA","person":"person-ada","publicKey":"cHVibGljIGtleQ==",' +
      '"rpId":"workbench.example","site":"s-aaaaaaaaaaaaaaaaaaaaaaaaaa","typ":"eugene-plexus/site-passkey","v":1}',
  );
});

it("computes the pairing MAC the machine checks, from the code as a person types it", async () => {
  // Computed by the site host (PBKDF2-HMAC-SHA256, 600000 rounds, then HMAC).
  expect(await pairingMac("k7qf3-mzd9t", VALUE)).toBe(
    "MWGQt5Pbv4oHQp-wL92gq1yapyRfVXsLZgnyAXLkaeg",
  );
  // Another code, or another key, is another MAC.
  expect(await pairingMac("k7qf3-mzd9v", VALUE)).not.toBe(
    "MWGQt5Pbv4oHQp-wL92gq1yapyRfVXsLZgnyAXLkaeg",
  );
  expect(await pairingMac("k7qf3-mzd9t", { ...VALUE, publicKey: "b3RoZXI=" })).not.toBe(
    "MWGQt5Pbv4oHQp-wL92gq1yapyRfVXsLZgnyAXLkaeg",
  );
});

it("reads a code as a person types it", () => {
  expect(normalizeCode(" abcde-fghjk ")).toBe("ABCDEFGHJK");
  expect(normalizeCode("o1i1l")).toBe("01111");
  expect(looksLikeCode("K7QF3-MZD9T")).toBe(true);
  expect(looksLikeCode("K7QF3-MZD9")).toBe(false);
  expect(looksLikeCode("UUUUU-UUUUU")).toBe(false);
});

it("round-trips base64url without padding", () => {
  const bytes = new Uint8Array([251, 255, 0, 1, 2]);
  expect(b64url(bytes)).toBe("-_8AAQI");
  expect(Array.from(fromB64url("-_8AAQI"))).toEqual(Array.from(bytes));
});
