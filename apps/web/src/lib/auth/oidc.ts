import {
  base64UrlDecode,
  constantTimeEqual,
  randomBase64Url,
  sha256Base64Url,
} from "./crypto";
import type { OidcTransaction, WebSession } from "./session";
import {
  OIDC_TRANSACTION_MAX_AGE_SECONDS,
  SESSION_COOKIE_MAX_AGE_SECONDS,
  isProductionWebRuntime,
  resolveWebBaseUrl,
  safeReturnTo,
} from "./runtime";

type OidcMetadata = {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  jwks_uri: string;
  end_session_endpoint?: string;
};

type TokenResponse = {
  access_token?: string;
  token_type?: string;
  expires_in?: number;
  id_token?: string;
  scope?: string;
  error?: string;
};

type IdTokenClaims = {
  iss?: string;
  sub?: string;
  aud?: string | string[];
  azp?: string;
  exp?: number;
  iat?: number;
  nonce?: string;
};

type JwtHeader = {
  alg?: string;
  kid?: string;
};

type OidcJwk = JsonWebKey & {
  kid?: string;
  use?: string;
};

function requiredEnvironment(
  name: string,
  environment: NodeJS.ProcessEnv,
): string {
  const value = environment[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function validatedProviderUrl(
  value: string,
  name: string,
  environment: NodeJS.ProcessEnv,
): string {
  const parsed = new URL(value);
  if (isProductionWebRuntime(environment) && parsed.protocol !== "https:") {
    throw new Error(`${name} must use https in production`);
  }
  return value;
}

function parseJsonSegment<T>(value: string): T {
  return JSON.parse(new TextDecoder().decode(base64UrlDecode(value))) as T;
}

function allowedAlgorithms(environment: NodeJS.ProcessEnv): Set<string> {
  const configured = environment.ODP_WEB_OIDC_ALLOWED_ALGS || "RS256";
  return new Set(
    configured
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
}

function verifyAlgorithm(algorithm: string): {
  importAlgorithm: RsaHashedImportParams | EcKeyImportParams;
  verifyAlgorithm: AlgorithmIdentifier | RsaPssParams | EcdsaParams;
} {
  if (algorithm === "RS256") {
    return {
      importAlgorithm: { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      verifyAlgorithm: { name: "RSASSA-PKCS1-v1_5" },
    };
  }
  if (algorithm === "PS256") {
    return {
      importAlgorithm: { name: "RSA-PSS", hash: "SHA-256" },
      verifyAlgorithm: { name: "RSA-PSS", saltLength: 32 },
    };
  }
  if (algorithm === "ES256") {
    return {
      importAlgorithm: { name: "ECDSA", namedCurve: "P-256" },
      verifyAlgorithm: { name: "ECDSA", hash: "SHA-256" },
    };
  }
  throw new Error(`Unsupported OIDC signing algorithm: ${algorithm}`);
}

export async function resolveOidcMetadata(
  environment: NodeJS.ProcessEnv = process.env,
  fetchImpl: typeof fetch = fetch,
): Promise<OidcMetadata> {
  const issuer = requiredEnvironment("ODP_WEB_OIDC_ISSUER", environment).replace(
    /\/+$/,
    "",
  );
  validatedProviderUrl(issuer, "ODP_WEB_OIDC_ISSUER", environment);
  const configuredAuthorization =
    environment.ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT?.trim();
  const configuredToken = environment.ODP_WEB_OIDC_TOKEN_ENDPOINT?.trim();
  const configuredJwks = environment.ODP_WEB_OIDC_JWKS_URI?.trim();

  if (configuredAuthorization && configuredToken && configuredJwks) {
    return {
      issuer,
      authorization_endpoint: validatedProviderUrl(
        configuredAuthorization,
        "ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT",
        environment,
      ),
      token_endpoint: validatedProviderUrl(
        configuredToken,
        "ODP_WEB_OIDC_TOKEN_ENDPOINT",
        environment,
      ),
      jwks_uri: validatedProviderUrl(
        configuredJwks,
        "ODP_WEB_OIDC_JWKS_URI",
        environment,
      ),
      end_session_endpoint:
        environment.ODP_WEB_OIDC_END_SESSION_ENDPOINT?.trim()
          ? validatedProviderUrl(
              environment.ODP_WEB_OIDC_END_SESSION_ENDPOINT.trim(),
              "ODP_WEB_OIDC_END_SESSION_ENDPOINT",
              environment,
            )
          : undefined,
    };
  }

  const response = await fetchImpl(
    `${issuer}/.well-known/openid-configuration`,
    {
      headers: { accept: "application/json" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error("OIDC discovery failed");
  }
  const metadata = (await response.json()) as Partial<OidcMetadata>;
  if (
    metadata.issuer !== issuer ||
    !metadata.authorization_endpoint ||
    !metadata.token_endpoint ||
    !metadata.jwks_uri
  ) {
    throw new Error("OIDC discovery document is incomplete or has wrong issuer");
  }
  validatedProviderUrl(
    metadata.authorization_endpoint,
    "OIDC authorization_endpoint",
    environment,
  );
  validatedProviderUrl(
    metadata.token_endpoint,
    "OIDC token_endpoint",
    environment,
  );
  validatedProviderUrl(metadata.jwks_uri, "OIDC jwks_uri", environment);
  if (metadata.end_session_endpoint) {
    validatedProviderUrl(
      metadata.end_session_endpoint,
      "OIDC end_session_endpoint",
      environment,
    );
  }
  return metadata as OidcMetadata;
}

export async function createAuthorizationRequest(
  options: {
    requestOrigin: string;
    returnTo?: string | null;
    nowSeconds?: number;
    environment?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
  },
): Promise<{ url: URL; transaction: OidcTransaction }> {
  const environment = options.environment ?? process.env;
  const metadata = await resolveOidcMetadata(
    environment,
    options.fetchImpl ?? fetch,
  );
  const clientId = requiredEnvironment("ODP_WEB_OIDC_CLIENT_ID", environment);
  const redirectUri =
    environment.ODP_WEB_OIDC_REDIRECT_URI?.trim()
      ? validatedProviderUrl(
          environment.ODP_WEB_OIDC_REDIRECT_URI.trim(),
          "ODP_WEB_OIDC_REDIRECT_URI",
          environment,
        )
      : `${resolveWebBaseUrl(options.requestOrigin, environment)}/auth/callback`;
  const issuedAt = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const state = randomBase64Url();
  const nonce = randomBase64Url();
  const codeVerifier = randomBase64Url(48);
  const codeChallenge = await sha256Base64Url(codeVerifier);
  const transaction: OidcTransaction = {
    kind: "oidc-transaction",
    issuedAt,
    expiresAt: issuedAt + OIDC_TRANSACTION_MAX_AGE_SECONDS,
    state,
    nonce,
    codeVerifier,
    redirectUri,
    returnTo: safeReturnTo(options.returnTo),
  };

  const authorizationUrl = new URL(metadata.authorization_endpoint);
  authorizationUrl.searchParams.set("response_type", "code");
  authorizationUrl.searchParams.set("client_id", clientId);
  authorizationUrl.searchParams.set("redirect_uri", redirectUri);
  authorizationUrl.searchParams.set(
    "scope",
    environment.ODP_WEB_OIDC_SCOPES?.trim() || "openid profile email",
  );
  authorizationUrl.searchParams.set("state", state);
  authorizationUrl.searchParams.set("nonce", nonce);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");

  return { url: authorizationUrl, transaction };
}

async function verifyIdToken(
  idToken: string,
  options: {
    clientId: string;
    expectedNonce: string;
    metadata: OidcMetadata;
    environment: NodeJS.ProcessEnv;
    fetchImpl: typeof fetch;
    nowSeconds: number;
  },
): Promise<IdTokenClaims> {
  const segments = idToken.split(".");
  if (segments.length !== 3) throw new Error("OIDC id_token is malformed");
  const [encodedHeader, encodedClaims, encodedSignature] = segments;
  const header = parseJsonSegment<JwtHeader>(encodedHeader);
  const claims = parseJsonSegment<IdTokenClaims>(encodedClaims);
  if (
    !header.alg ||
    !header.kid ||
    !allowedAlgorithms(options.environment).has(header.alg)
  ) {
    throw new Error("OIDC id_token signing algorithm is not allowed");
  }

  const jwksResponse = await options.fetchImpl(options.metadata.jwks_uri, {
    headers: { accept: "application/json" },
    cache: "no-store",
  });
  if (!jwksResponse.ok) throw new Error("OIDC JWKS retrieval failed");
  const jwks = (await jwksResponse.json()) as { keys?: OidcJwk[] };
  const jwk = jwks.keys?.find(
    (candidate) =>
      candidate.kid === header.kid &&
      (!candidate.use || candidate.use === "sig"),
  );
  if (!jwk) throw new Error("OIDC signing key was not found");

  const algorithms = verifyAlgorithm(header.alg);
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    algorithms.importAlgorithm,
    false,
    ["verify"],
  );
  const signingInput = new TextEncoder().encode(
    `${encodedHeader}.${encodedClaims}`,
  );
  const signatureValid = await crypto.subtle.verify(
    algorithms.verifyAlgorithm,
    key,
    base64UrlDecode(encodedSignature),
    signingInput,
  );
  if (!signatureValid) throw new Error("OIDC id_token signature is invalid");

  const audiences = Array.isArray(claims.aud)
    ? claims.aud
    : claims.aud
      ? [claims.aud]
      : [];
  if (
    claims.iss !== options.metadata.issuer ||
    !audiences.includes(options.clientId) ||
    (audiences.length > 1 && claims.azp !== options.clientId) ||
    !claims.sub ||
    !claims.exp ||
    claims.exp <= options.nowSeconds ||
    !claims.iat ||
    claims.iat > options.nowSeconds + 60 ||
    !claims.nonce ||
    !constantTimeEqual(claims.nonce, options.expectedNonce)
  ) {
    throw new Error("OIDC id_token claims are invalid");
  }

  return claims;
}

export async function exchangeAuthorizationCode(
  options: {
    code: string;
    returnedState: string;
    transaction: OidcTransaction;
    nowSeconds?: number;
    environment?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
  },
): Promise<WebSession> {
  const environment = options.environment ?? process.env;
  const fetchImpl = options.fetchImpl ?? fetch;
  if (!constantTimeEqual(options.returnedState, options.transaction.state)) {
    throw new Error("OIDC state mismatch");
  }

  const metadata = await resolveOidcMetadata(environment, fetchImpl);
  const clientId = requiredEnvironment("ODP_WEB_OIDC_CLIENT_ID", environment);
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    code: options.code,
    redirect_uri: options.transaction.redirectUri,
    client_id: clientId,
    code_verifier: options.transaction.codeVerifier,
  });
  const headers: Record<string, string> = {
    accept: "application/json",
    "content-type": "application/x-www-form-urlencoded",
  };
  const clientSecret = environment.ODP_WEB_OIDC_CLIENT_SECRET?.trim();
  if (clientSecret) {
    headers.authorization = `Basic ${btoa(`${clientId}:${clientSecret}`)}`;
    body.delete("client_id");
  }

  const tokenResponse = await fetchImpl(metadata.token_endpoint, {
    method: "POST",
    headers,
    body,
    cache: "no-store",
  });
  const token = (await tokenResponse.json().catch(() => ({}))) as TokenResponse;
  if (
    !tokenResponse.ok ||
    token.error ||
    !token.access_token ||
    token.token_type?.toLowerCase() !== "bearer" ||
    !token.id_token ||
    !Number.isFinite(token.expires_in) ||
    Number(token.expires_in) <= 0
  ) {
    throw new Error("OIDC token exchange failed");
  }

  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const claims = await verifyIdToken(token.id_token, {
    clientId,
    expectedNonce: options.transaction.nonce,
    metadata,
    environment,
    fetchImpl,
    nowSeconds,
  });
  const configuredTtl = Number(
    environment.ODP_WEB_SESSION_TTL_SECONDS ||
      SESSION_COOKIE_MAX_AGE_SECONDS,
  );
  const sessionTtl =
    Number.isFinite(configuredTtl) && configuredTtl > 0
      ? Math.min(configuredTtl, SESSION_COOKIE_MAX_AGE_SECONDS)
      : SESSION_COOKIE_MAX_AGE_SECONDS;

  return {
    kind: "web-session",
    issuedAt: nowSeconds,
    expiresAt: Math.min(
      nowSeconds + Number(token.expires_in),
      nowSeconds + sessionTtl,
      claims.exp as number,
    ),
    accessToken: token.access_token,
    tokenType: "Bearer",
    subject: claims.sub as string,
  };
}

export async function resolveEndSessionEndpoint(
  environment: NodeJS.ProcessEnv = process.env,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  const configured =
    environment.ODP_WEB_OIDC_END_SESSION_ENDPOINT?.trim();
  if (configured) {
    return validatedProviderUrl(
      configured,
      "ODP_WEB_OIDC_END_SESSION_ENDPOINT",
      environment,
    );
  }
  try {
    return (await resolveOidcMetadata(environment, fetchImpl))
      .end_session_endpoint ?? null;
  } catch {
    return null;
  }
}
