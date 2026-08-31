# Web App

ODay Plus frontend application built with Next.js and the shared design system.

## Authentication modes

The Web app supports two authentication modes controlled by `ODP_AUTH_MODE`:

- **`local`** (default): Password-only authentication with no OIDC dependency.
  The deployment does not require any OIDC provider, client ID, or client secret.
- **`oidc`**: OIDC authorization-code + PKCE. The browser receives only an
  encrypted `HttpOnly`, `Secure`, `SameSite=Lax` session cookie. Calls to
  `/api/v1/**` and `/avm/**` go through the same-origin Next BFF. The BFF
  forwards the end-user access token in `Authorization` and obtains its Cloud
  Run service identity from the Google metadata server for
  `X-Serverless-Authorization`. Browser-supplied identity, service-identity,
  `X-Subject-Id`, `X-Tenant-Id`, and `X-Roles` headers are not forwarded.

Required environment (all modes):

| Variable | Purpose |
|---|---|
| `ODP_WEB_SESSION_SECRET` | Server-only session encryption secret, at least 32 bytes |
| `ODP_WEB_BASE_URL` | Canonical HTTPS web origin |
| `ODP_API_BASE_URL` | Server-side API origin used by the BFF and server components |
| `ODP_API_SERVICE_AUDIENCE` | Cloud Run API audience used to mint the BFF service identity token |
| `ODP_AUTH_MODE` | `local` (default) or `oidc` |
| `ODP_AUTH_LOCAL_ISSUER` | Issuer for Web-minted local access tokens; defaults to `urn:odp:identity:local` |
| `ODP_AUTH_AUDIENCES` | API audience for Web-minted local access tokens |

In production, `ODP_IDENTITY_TOKEN_SIGNING_KEY` is injected from Secret
Manager. The API and Web services must use the same pinned secret version; the
API trust resolver registers its plain value as the `local-default` HS256 key.

### OIDC mode (`ODP_AUTH_MODE=oidc`)

When OIDC is enabled, the following additional variables are required:

| Variable | Purpose |
|---|---|
| `ODP_WEB_OIDC_ISSUER` | Exact OIDC issuer |
| `ODP_WEB_OIDC_CLIENT_ID` | Registered web client ID |
| `ODP_WEB_OIDC_CLIENT_SECRET` | Web OIDC client secret (bound via Secret Manager `ODP_WEB_OIDC_CLIENT_SECRET_SECRET` in deployment) |

Optional OIDC environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_OIDC_REDIRECT_URI` | Override callback URI; defaults to `<ODP_WEB_BASE_URL>/auth/callback` |
| `ODP_WEB_OIDC_SCOPES` | Defaults to `openid profile email` |
| `ODP_WEB_OIDC_ALLOWED_ALGS` | Comma-separated ID-token algorithms; defaults to `RS256` |
| `ODP_WEB_OIDC_AUTHORIZATION_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_TOKEN_ENDPOINT` | Explicit endpoint when discovery is not used |
| `ODP_WEB_OIDC_JWKS_URI` | Explicit JWKS endpoint when discovery is not used |
| `ODP_WEB_OIDC_END_SESSION_ENDPOINT` | Optional provider logout endpoint |

The provider must register the callback URI and, when supported, the
post-logout URI `<ODP_WEB_BASE_URL>/login`.

General optional environment:

| Variable | Purpose |
|---|---|
| `ODP_WEB_SESSION_TTL_SECONDS` | Session cap, no more than eight hours |
| `ODP_WEB_ALLOW_LEGACY_TRUSTED_HEADERS` | Local/test compatibility only; ignored in production |
| `ODP_WEB_LOGIN_THROTTLE_PEPPER` | Overrides the login throttle digest pepper; defaults to `ODP_WEB_SESSION_SECRET` |
| `ODP_WEB_TRUSTED_PROXY_HOPS` | Trusted proxies in front of the service; defaults to `1` |

## Login throttle

`POST /login` is throttled before any credential is verified. The counters
live in `identity.login_attempts`, so every Cloud Run instance shares one
view of them:

- five failures per account within fifteen minutes lock that account for
  fifteen minutes, doubling on each further lockout round up to sixty minutes;
- fifty failures per source IP within fifteen minutes reject further attempts
  from it;
- a successful login clears the account counter and returns the attempt it
  charged to the source IP.

The attempt is counted before verification and only given back on success, so
a request that dies before reaching a verdict still counts. The gate refuses
with `AUTH_ACCOUNT_LOCKED` (423) or `AUTH_RATE_LIMITED` (429); because the
account key is derived from the submitted username rather than a resolved
account, an unknown username throttles exactly like a real one and the
response is not an account-existence oracle.

`attempt_key` stores an HMAC-SHA256 digest, never a plaintext client IP or
username. The pepper defaults to `ODP_WEB_SESSION_SECRET`; rotating that
secret re-keys the table and clears in-flight lockouts.

The client address is taken from the last `X-Forwarded-For` entry, which the
platform appends and a client cannot forge. Deployments with additional
trusted proxies in front set `ODP_WEB_TRUSTED_PROXY_HOPS` to the number of
hops to skip.

In production the throttle fails closed: without a database URL `/login`
returns `503 WEB_AUTH_NOT_CONFIGURED` rather than serving an unthrottled
login form, and an unreachable store returns `503 WEB_AUTH_UNAVAILABLE`.

The Web runtime service account must have permission to invoke the API Cloud
Run service. Production requests fail closed before contacting the API when
the service audience is missing or the metadata server cannot issue an
identity token. The service token remains server-side and is never copied to a
browser response.
