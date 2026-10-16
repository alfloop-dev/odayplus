import { beforeAll, describe, expect, it } from "vitest";
import { exchangeAuthorizationCode } from "../oidc";
import type { OidcTransaction } from "../session";

const now = 1800000000;
const environment: NodeJS.ProcessEnv = {
  NODE_ENV: "test", ODP_WEB_OIDC_ISSUER: "https://offline.invalid",
  ODP_WEB_OIDC_CLIENT_ID: "offline-client", ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT: "https://offline.invalid/authorize",
  ODP_WEB_OIDC_TOKEN_ENDPOINT: "https://offline.invalid/token", ODP_WEB_OIDC_JWKS_URI: "https://offline.invalid/jwks",
};
const transaction: OidcTransaction = { kind: "oidc-transaction", state: "offline-state", nonce: "offline-nonce",
  codeVerifier: "offline-pkce", issuedAt: now, expiresAt: now + 300, redirectUri: "https://web.invalid/auth/callback", returnTo: "/" };
let pair: CryptoKeyPair;
let jwk: JsonWebKey;
beforeAll(async () => {
  pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
});
async function exchange(claim?: string, value?: unknown) {
  const claims: Record<string, unknown> = { iss: environment.ODP_WEB_OIDC_ISSUER, sub: "linked-subject", aud: "offline-client",
    nonce: transaction.nonce, iat: now, exp: now + 300 };
  if (claim) claims[claim] = value;
  // 1e400 is valid JSON, parsed as Infinity; sign the actual bytes, not a mock
  // signature or JSON.stringify(Infinity), which would silently become null.
  const raw = JSON.stringify(claims).replace('"RAW-EXPONENT"', '1e400');
  const header = Buffer.from(JSON.stringify({ alg: "RS256", kid: "offline-key" })).toString("base64url");
  const payload = Buffer.from(raw).toString("base64url");
  const signature = await crypto.subtle.sign({ name: "RSASSA-PKCS1-v1_5" }, pair.privateKey, new TextEncoder().encode(`${header}.${payload}`));
  const idToken = `${header}.${payload}.${Buffer.from(signature).toString("base64url")}`;
  const fetchImpl: typeof fetch = async (url) => String(url).endsWith("/jwks")
    ? new Response(JSON.stringify({ keys: [{ ...jwk, kid: "offline-key", use: "sig" }] }))
    // Opaque access token is generated per run (never a fixed credential-shaped
    // literal); only the real signed id_token is under test here.
    : new Response(JSON.stringify({ access_token: crypto.randomUUID(), token_type: "Bearer", expires_in: 300, id_token: idToken }));
  return exchangeAuthorizationCode({ code: "offline-code", returnedState: transaction.state, transaction, environment, fetchImpl, nowSeconds: now });
}
describe("real signed Web ID-token temporal bounds (no network/store/session effects)", () => {
  it.each(["exp", "iat", "nbf"])("%s rejects every present malformed runtime type/overflow", async (claim) => {
    for (const value of ["not-a-number", String(now), true, null, [now], { value: now }, "RAW-EXPONENT"]) {
      await expect(exchange(claim, value)).rejects.toThrow("OIDC id_token claims are invalid");
    }
  });
  it.each([
    ["sub", ["linked-subject"]], ["sub", { value: "linked-subject" }],
    ["nonce", [transaction.nonce]], ["nonce", { value: transaction.nonce }],
    ["aud", ["offline-client", 7]],
  ])("rejects non-string identity/nonce/audience members: %s", async (claim, value) => {
    await expect(exchange(claim as string, value)).rejects.toThrow("OIDC id_token claims are invalid");
  });
  it("preserves healthy finite claims and rejects future nbf/iat and expired exp", async () => {
    const healthy = await exchange();
    expect(healthy.subject).toBe("linked-subject");
    expect(healthy.expiresAt).toBe(now + 300);
    expect((await exchange("nbf", now)).expiresAt).toBe(now + 300);
    await expect(exchange("nbf", now + 61)).rejects.toThrow();
    await expect(exchange("iat", now + 61)).rejects.toThrow();
    await expect(exchange("exp", now)).rejects.toThrow();
  });
});
