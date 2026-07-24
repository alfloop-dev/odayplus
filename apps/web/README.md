# Web App

OpsBoard frontend shell. The workspace is reserved for a Next.js application
and shared UI integration.

## Production authentication

Production (`NODE_ENV=production`) uses OIDC authorization-code + PKCE. The
browser receives only an encrypted `HttpOnly`, `Secure`, `SameSite=Lax` session
cookie. Calls to `/api/v1/**` and `/avm/**` go through the same-origin Next BFF,
which reads that session and injects the access-token bearer. Browser-supplied
`Authorization`, `X-Subject-Id`, `X-Tenant-Id`, and `X-Roles` headers are not
forwarded.

Required environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_SESSION_SECRET` | Server-only session encryption secret, at least 32 bytes |
| `ODP_WEB_BASE_URL` | Canonical HTTPS web origin |
| `ODP_WEB_OIDC_ISSUER` | Exact OIDC issuer |
| `ODP_WEB_OIDC_CLIENT_ID` | Registered web client ID |
| `ODP_API_BASE_URL` | Server-side API origin used by the BFF and server components |

Optional environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_OIDC_CLIENT_SECRET` | Confidential-client secret; omitted for a public PKCE client |
| `ODP_WEB_OIDC_REDIRECT_URI` | Override callback URI; defaults to `<ODP_WEB_BASE_URL>/auth/callback` |
| `ODP_WEB_OIDC_SCOPES` | Defaults to `openid profile email` |
| `ODP_WEB_OIDC_ALLOWED_ALGS` | Comma-separated ID-token algorithms; defaults to `RS256` |
| `ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_TOKEN_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_JWKS_URI` | Explicit JWKS endpoint when discovery is not used |
| `ODP_WEB_OIDC_END_SESSION_ENDPOINT` | Optional provider logout endpoint |
| `ODP_WEB_SESSION_TTL_SECONDS` | Session cap, no more than eight hours |
| `ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS` | Local/test compatibility only; ignored in production |

The provider must register the callback URI and, when supported, the
post-logout URI `<ODP_WEB_BASE_URL>/login`.
