import { describe, expect, it, vi } from "vitest";
import { base64UrlEncode } from "../crypto";
import {
  createAuthorizationRequest,
  exchangeAuthorizationCode,
} from "../oidc";

const ENVIRONMENT: NodeJS.ProcessEnv = {
  NODE_ENV: "production",
  ODP_WEB_BASE_URL: "https://ops.oday.plus",
  ODP_WEB_OIDC_ISSUER: "https://id.example",
  ODP_WEB_OIDC_CLIENT_ID: "oday-web",
  ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT: "https://id.example/authorize",
  ODP_WEB_OIDC_TOKEN_ENDPOINT: "https://id.example/token",
  ODP_WEB_OIDC_JWKS_URI: "https://id.example/jwks",
  ODP_WEB_OIDC_ALLOWED_ALGS: "RS256",
};

function encodeJson(value: unknown): string {
  return base64UrlEncode(
    new TextEncoder().encode(JSON.stringify(value)),
  );
}

describe("OIDC authorization-code + PKCE", () => {
  it("creates a protected authorization request and validates the callback token", async () => {
    const now = 10_000;
    const { url, transaction } = await createAuthorizationRequest({
      requestOrigin: "https://untrusted-host.example",
      returnTo: "/operator?tab=network",
      nowSeconds: now,
      environment: ENVIRONMENT,
    });

    expect(url.origin).toBe("https://id.example");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://ops.oday.plus/auth/callback",
    );
    expect(url.searchParams.get("state")).toBe(transaction.state);
    expect(url.searchParams.get("nonce")).toBe(transaction.nonce);
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).not.toBe(
      transaction.codeVerifier,
    );
    expect(transaction.returnTo).toBe("/operator?tab=network");

    const keys = (await crypto.subtle.generateKey(
      {
        name: "RSASSA-PKCS1-v1_5",
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
      },
      true,
      ["sign", "verify"],
    )) as CryptoKeyPair;
    const publicJwk = await crypto.subtle.exportKey("jwk", keys.publicKey);
    const encodedHeader = encodeJson({ alg: "RS256", kid: "key-1" });
    const encodedClaims = encodeJson({
      iss: "https://id.example",
      sub: "real-user-123",
      aud: "oday-web",
      exp: now + 600,
      iat: now,
      nonce: transaction.nonce,
    });
    const signingInput = `${encodedHeader}.${encodedClaims}`;
    const signature = await crypto.subtle.sign(
      "RSASSA-PKCS1-v1_5",
      keys.privateKey,
      new TextEncoder().encode(signingInput),
    );
    const idToken = `${signingInput}.${base64UrlEncode(
      new Uint8Array(signature),
    )}`;

    const fetchMock = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const target = String(input);
        if (target === "https://id.example/token") {
          const form = new URLSearchParams(String(init?.body));
          expect(form.get("code")).toBe("authorization-code");
          expect(form.get("code_verifier")).toBe(transaction.codeVerifier);
          return Response.json({
            access_token: "real-access-token",
            token_type: "Bearer",
            expires_in: 300,
            id_token: idToken,
            scope: "openid profile",
          });
        }
        if (target === "https://id.example/jwks") {
          return Response.json({
            keys: [{ ...publicJwk, kid: "key-1", use: "sig" }],
          });
        }
        return new Response(null, { status: 404 });
      },
    );

    const session = await exchangeAuthorizationCode({
      code: "authorization-code",
      returnedState: transaction.state,
      transaction,
      nowSeconds: now,
      environment: ENVIRONMENT,
      fetchImpl: fetchMock,
    });

    expect(session.accessToken).toBe("real-access-token");
    expect(session.subject).toBe("real-user-123");
    expect(session.expiresAt).toBe(now + 300);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a state mismatch before token exchange", async () => {
    const { transaction } = await createAuthorizationRequest({
      requestOrigin: "https://ops.oday.plus",
      nowSeconds: 10_000,
      environment: ENVIRONMENT,
    });
    const fetchMock = vi.fn();

    await expect(
      exchangeAuthorizationCode({
        code: "authorization-code",
        returnedState: "attacker-state",
        transaction,
        nowSeconds: 10_000,
        environment: ENVIRONMENT,
        fetchImpl: fetchMock,
      }),
    ).rejects.toThrow("state mismatch");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

