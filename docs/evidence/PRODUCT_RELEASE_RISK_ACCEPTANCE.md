# Product Release Risk Acceptance

Task: ODP-FIN-LIVE-001
Generated: 2026-07-13
Owner: Antigravity6
Reviewer: Antigravity7
Status: pending-human-signoff

This document records the live-readiness wiring state and formal risk
acceptance for the three PV-008 P0 gaps that could not be closed by code
alone. The code paths are wired and fail-closed. Activation requires real
keys, hosts, and explicit human sign-off.

---

## 1. Live-Readiness Summary

| Gap | Code path | Gate | Missing to activate | Risk acceptance required |
|---|---|---|---|---|
| Live external provider (listing / POI / geocoder backend) | `modules/external_data/providers/live.py` + `provider_registry.py` | `ODP_EXTERNAL_PROVIDER_MODE=live` + `validate_external_providers_or_raise()` | Real API keys in named env vars (see §2) | Yes — Human/Ops |
| Live map tile / geocoder (frontend) | `apps/web/features/map/HeatZoneMap.tsx` `readMapBoundaryConfig()` | Missing env vars yield empty string → MapLibre falls back to local style | Provider-issued tile URL + geocoder URL + attribution/terms text (see §3) | Yes — Human/Ops |
| Remote staging | `.github/workflows/deploy-staging.yml` + `scripts/e2e/check_remote_staging_proof.py` | Workflow is fail-closed; missing host/url/secret owner produce a failed check, not a fabricated pass | GitHub `staging` environment variables and secrets (see §4) | Yes — Human/Ops |

No evidence has been fabricated. All three gaps remain **open** until the
human/ops sign-off rows in §5 are completed.

---

## 2. Live External Provider: Wiring and Required Secrets

### Code Path

```
ODP_EXTERNAL_PROVIDER_MODE=live
    └─ external_provider_mode(os.environ)          [provider_registry.py:271]
         └─ validate_external_providers_or_raise()  [provider_registry.py:367]
              └─ per-provider credential check      [provider_registry.py:325-357]
                   ├─ missing/placeholder value → ProviderValidationError (code=missing_credential)
                   └─ invalid status value      → ProviderValidationError (code=credential_<status>)
```

In fixture mode (`ODP_EXTERNAL_PROVIDER_MODE=fixture` or unset) all three
provider adapters run against deterministic replay fixtures. No live network
calls are made. The gate is **fail-closed**: setting `live` without the
required env vars raises `ExternalProviderConfigError` at startup.

### Required Environment Variables (live mode only)

| Provider | Required env var | Auth mode | Status env var | Notes |
|---|---|---|---|---|
| `listing.partner_feed` | `ODP_LISTING_PROVIDER_API_KEY` | `api_key` | `ODP_LISTING_PROVIDER_AUTH_STATUS` | Status must not be `expired / unauthorized / revoked / invalid` |
| `listing.partner_feed` | `ODP_LISTING_PROVIDER_FEED_URL` | (endpoint URL, not a credential) | — | Used by `HttpListingFeedClient`; required when mode=live |
| `poi.commercial_api` | `ODP_POI_PROVIDER_API_KEY` | `api_key` | `ODP_POI_PROVIDER_AUTH_STATUS` | Status must not be invalid |
| `geocode.primary_api` | `ODP_GEOCODE_PROVIDER_API_KEY` | `api_key` | `ODP_GEOCODE_PROVIDER_AUTH_STATUS` | Status must not be invalid |
| `geocode.primary_api` | `ODP_GEOCODE_PROVIDER_URL` | (endpoint URL, not a credential) | — | Used by `HttpGeocodeClient`; required when mode=live |
| `admin_boundary.official_dataset` | `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN` | `bearer_token` | `ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS` | Status must not be invalid |
| `competitor.manual_source` | `ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION` | `manual_attestation` | `ODP_COMPETITOR_MANUAL_SOURCE_STATUS` | `allowed_in_production=False`; blocked by license gate in production |

> **Note:** `competitor.manual_source` is blocked for automated production use
> by its license metadata (`allowed_in_production=False`). Attempting live mode
> in a production-like deploy env raises `license_blocked`.

### Placeholder Values Rejected at Startup

The following values are treated as missing by `_is_missing_or_placeholder()`:

```
"" | "changeme" | "change-me" | "todo" | "placeholder" | "dummy" | "example"
```

### Licensing Constraints

All live providers require license review before production activation.
`competitor.manual_source` is explicitly excluded from production automated use.
See `ProviderLicense.allowed_in_production` and `downstream_use_flags` in
`modules/external_data/connectors/provider_registry.py`.

---

## 3. Live Map Tile / Geocoder: Wiring and Required Config

### Code Path

```
apps/web/features/map/HeatZoneMap.tsx
    └─ readMapBoundaryConfig()                      [line ~350]
         ├─ tileUrl:    process.env.NEXT_PUBLIC_ODP_MAP_TILE_URL    ?? ""
         ├─ geocoderUrl: process.env.NEXT_PUBLIC_ODP_GEOCODER_URL   ?? ""
         ├─ attribution: process.env.NEXT_PUBLIC_ODP_MAP_ATTRIBUTION ?? "ODay Plus local fixture"
         └─ termsUrl:   process.env.NEXT_PUBLIC_ODP_MAP_TERMS_URL   ?? ""
```

When `NEXT_PUBLIC_ODP_MAP_TILE_URL` is empty, the map component falls back to
the local MapLibre style (fixture mode). The status indicator reads
`"local MapLibre style"`. When a URL is provided and the tile layer loads, it
reads `"live tile endpoint configured"`. A `tileFault` flag triggers the
resilience state for E2E testing without requiring a real provider outage.

### Required Build-Time Environment Variables (live map activation)

| Variable | Type | Purpose | Required at build |
|---|---|---|---|
| `NEXT_PUBLIC_ODP_MAP_TILE_URL` | URL (public, baked at build) | MapLibre/deck.gl tile provider base URL | Yes |
| `NEXT_PUBLIC_ODP_GEOCODER_URL` | URL (public, baked at build) | Geocoder API URL used by address search | Yes |
| `NEXT_PUBLIC_ODP_MAP_ATTRIBUTION` | string (public) | Provider attribution text displayed in map footer | Yes (must match provider license terms) |
| `NEXT_PUBLIC_ODP_MAP_TERMS_URL` | URL (public) | Provider terms-of-service URL linked from attribution | Yes (if provider requires it) |

> **Note:** `NEXT_PUBLIC_*` variables are baked into the Next.js production
> build. They are **not secrets** and appear in the browser bundle. The tile
> URL and geocoder URL may contain provider-issued keys embedded in the URL
> path (e.g., MapTiler style URLs). If the provider embeds a key in the URL,
> treat the full URL as sensitive and do not commit it.

### Fail-Closed Behaviour

An empty `tileUrl` is not an error state; the component degrades gracefully to
the local MapLibre style. The E2E tests
`tests/e2e/e2e-map-live-boundary.spec.ts` and
`tests/e2e/e2e-map-resilience.spec.ts` verify both the live-configured and the
fault/fallback states. Live tile proof requires a real provider URL.

---

## 4. Remote Staging: Wiring and Required Config

### Code Path

```
.github/workflows/deploy-staging.yml
    └─ scripts/e2e/check_remote_staging_proof.py
         ├─ checks env: ODP_STAGING_DEPLOY_URL, ODP_STAGING_API_URL, ODP_STAGING_SECRET_OWNER
         ├─ GET /platform/health → status=ok
         └─ GET /platform/version → release_sha matches expected SHA
```

The workflow is fail-closed: missing variables produce a failed check, not a
fabricated pass. See `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md` for the
full execution sequence.

### Required GitHub Environment Configuration

| Name | Type | Owner | Purpose |
|---|---|---|---|
| `ODP_STAGING_DEPLOY_URL` | variable | Platform/Ops | Public staging web URL |
| `ODP_STAGING_API_URL` | variable | Platform/Ops | Staging API base URL for smoke checks |
| `ODP_STAGING_HOST` | variable | Platform/Ops | Remote host or orchestrator target |
| `ODP_STAGING_SECRET_OWNER` | variable | Platform/Ops | Human/team accountable for secret rotation |
| `ODP_STAGING_DEPLOY_USER` | secret | Platform/Ops | SSH/remote deploy user (if SSH-based) |
| `ODP_STAGING_SSH_PRIVATE_KEY` | secret | Platform/Ops | SSH private key (if SSH-based) |
| `ODP_STAGING_DATABASE_URL` | secret | Platform/Ops | Staging database connection string |

The deployed API container must set `ODAY_RELEASE_SHA=<release commit SHA>` so
that `GET /platform/version` returns the correct `release_sha`.

---

## 5. Risk Acceptance Table

The three P0 gaps are **not blocking the deterministic product-E2E gate**.
They are blocking live-provider activation and remote staging.

| Risk | Owner | Accepted? | Due date | Notes |
|---|---|---|---|---|
| Live external provider not activated | Human/Ops | ☐ PENDING | — | Set `ODP_EXTERNAL_PROVIDER_MODE=live` + all §2 env vars; run `validate_external_providers_or_raise()` smoke before go-live |
| Live map tile / geocoder not configured | Human/Ops | ☐ PENDING | — | Set all §3 `NEXT_PUBLIC_*` vars at Next.js build time; confirm attribution matches provider license |
| Remote staging not configured | Human/Ops | ☐ PENDING | — | Configure GitHub `staging` environment per §4; run `check_remote_staging_proof.py` and attach report |

Until all three rows are `✅ ACCEPTED`, the product is approved only for
the **deterministic product-E2E environment** (fixture mode, local Docker
stack). Production or remote-staging rollout is explicitly blocked.

---

## 6. Feature Flag Gate

High-risk production features (SiteScore approval, PriceOps execution,
model publish, etc.) are governed by `shared/auth/feature_flags.py`.
`default_registry()` seeds all high-risk flags as **disabled**. Enabling
them requires dual approval (`DUAL_APPROVAL_MINIMUM = 2`) and is an
independent admin action from live provider activation.

Live external provider activation (`ODP_EXTERNAL_PROVIDER_MODE=live`) and
live map tile configuration are **not** feature flags — they are runtime
environment configuration. The feature flag system governs high-risk product
decisions, not infrastructure credential injection.

---

## 7. Traceability

| Artifact | Reference |
|---|---|
| Provider registry + credential definitions | `modules/external_data/connectors/provider_registry.py` |
| Live listing adapter | `modules/external_data/providers/live.py` |
| Geo pipeline + geocoder wiring | `modules/external_data/geo/pipeline.py` |
| Feature flags | `shared/auth/feature_flags.py` |
| Map tile config wiring | `apps/web/features/map/HeatZoneMap.tsx` (lines 350–375) |
| Remote staging runbook | `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md` |
| E2E readiness report | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md` |
| Production readiness package | `docs/evidence/PRODUCTION_READINESS_PACKAGE.md` |
| Go/no-go decision | `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` |
| External provider proof queue | `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json` |

LLM-Agent: Antigravity6
Task-ID: ODP-FIN-LIVE-001
Reviewer: Antigravity7
