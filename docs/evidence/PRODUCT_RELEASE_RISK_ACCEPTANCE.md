# Product Release Risk Acceptance

Task: ODP-PV-008  
Decision: **GO — with explicit residual-risk acceptance**  
Decision scope: internal / POC / deterministic product-E2E milestone only  
Decision owner: Human/Ops  
Decision recorded: 2026-07-12  
Prepared by: Claude (owner), reviewed by Claude2 (reviewer)

This document is the durable, auditable record of the Human/Ops release
decision for the product-grade E2E validation wave. It sits on top of the
conditional go/no-go packet in
`docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` and the traceability packet in
`docs/evidence/PRODUCT_E2E_READINESS_REPORT.md`, and formally states which
residual risks Human/Ops has accepted and which claims remain prohibited.

## Decision Statement

Human/Ops accepts the current release candidate **for the deterministic
product-E2E / internal / proof-of-concept milestone only**. Development for
every P0 product flow is complete at the fixture / deterministic-environment
level and is merged into `dev`. The remaining P0 gaps are **live-evidence
gaps, not missing code**: they require running against real external
providers, a live map endpoint, and a configured remote staging target.

This decision **does not** authorize any external, customer-facing, or
"production-ready" claim. Live remote rollout stays blocked until the live
proof below is captured and accepted.

## What Is Accepted

| Area | Accepted basis | Evidence |
|---|---|---|
| Product E2E readiness | Deterministic Docker product stack, seeded API/source data, Playwright P0 specs (PV-005/006/007), map canvas/a11y specs | `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md`, `scripts/e2e/run_product_e2e.sh` |
| Go/No-Go boundary | Conditional-go packet keeps live provider, live map, and remote staging explicitly conditional | `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`, `scripts/e2e/check_product_go_no_go.py` |
| Audit evidence | Retained audit bundle checksums and correlation IDs in product specs | `corr-pv006-ops-intervention-price-ad`, `corr-pv007-avm-netplan-learning-audit`, `corr-product-e2e-seed-001` |
| Deployment/backup/rollback | Deterministic E2E backup/restore/rollback proof | `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md` |

## Residual Risks Explicitly Accepted (Deferred, Not Waived)

The following three P0 live-evidence gaps remain open. They are accepted as
**deferred to their tracked external-proof tasks** and must be closed with
environment-specific live evidence before any production claim. They are
**not** waived and must **not** be closed from deterministic fixtures or
mock-live evidence.

| Residual risk | Tracked closeout | Blocking type | Close only with |
|---|---|---|---|
| Live external provider proof (credentials / license / geocoder) | `ODP-EXT-PROD-001/002/003` — issues #132, #133, #134 | `external_blocked` | Redacted production credential/license/geocoder runtime proof |
| Live map endpoint proof (remote tile + geocoder smoke) | `ODP-MAP-STAGE-001/002` — issues #135, #136 | `external_blocked` | Remote staging map endpoint + geocoder smoke |
| Remote staging rollout proof | `ODP-PV-STAGE-001/002` — issues #137, #138 | `external_blocked` | Configured remote staging target passing `scripts/e2e/check_remote_staging_proof.py` + staging drill |

Live closeout state for all of the above is tracked in
`docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`, and each redacted
handback must pass
`scripts/e2e/check_external_proof_handback_bundle.py` against the release
target PR #82 `headRefOid` before its issue may be closed.

## Automated Gate Posture (Deliberately Fail-Closed)

The automated static release gate stays **fail-closed** on purpose. This risk
acceptance is a **human** decision layered on top of the machine gate; it does
**not** flip the machine gate to pass, and no code change may be made to force
the gate green for the deferred live items.

- `make product-release-gate` (`scripts/e2e/check_product_release_gate.py` +
  `scripts/e2e/run_product_e2e.sh`) remains the release-blocking command and
  continues to fail-closed until the deferred live proof and closeout-queue
  reconciliation are satisfied. It is intentionally not overridden for this
  internal milestone.
- `scripts/e2e/check_product_go_no_go.py` verifies the go/no-go packet still
  keeps live provider, live map, and remote staging **conditional** until
  issues #132–#138 are accepted. This guard passing is the required proof that
  this risk acceptance did not silently promote a live claim.

## Prohibited Claims Under This Decision

- No statement that the platform is "production-ready" or generally available.
- No closing of `ODP-EXT-PROD-*`, `ODP-MAP-STAGE-*`, or `ODP-PV-STAGE-*` from
  deterministic or mock-live evidence.
- No promotion of the draft release (PR #82) as a live rollout without the
  live proof above and a fresh Human/Ops sign-off against the target release
  commit's GitHub checks.

## Required Follow-Up Before Any Production Claim

1. Configure a real staging target and deploy with `ODAY_RELEASE_SHA`.
2. Capture and accept the #132–#138 redacted live-proof handbacks.
3. Re-run `make product-release-gate` and confirm it passes on the target
   release commit (not a stale `dev` hash).
4. Record a new Human/Ops go/no-go against that commit's attached checks.

## References

- `docs/evidence/PRODUCT_E2E_READINESS_REPORT.md`
- `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`
- `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`
- `docs/evidence/DEPLOYMENT_HEALTH_BACKUP_ROLLBACK_EVIDENCE.md`
- `scripts/e2e/check_product_release_gate.py`
- `scripts/e2e/check_product_go_no_go.py`

---

## Live-Readiness Wiring Addendum (ODP-FIN-LIVE-001)

Task: ODP-FIN-LIVE-001  
Recorded: 2026-07-13  
Prepared by: Antigravity6 (owner), reviewed by Antigravity7 (reviewer)

This addendum documents the code-layer wiring for the three deferred P0
live-evidence gaps above and precisely lists the env vars / secrets needed
to activate each live path. No fabricated live evidence is added. The
gates remain fail-closed.

### Live External Provider: Code Path and Required Secrets

The live mode switch is `ODP_EXTERNAL_PROVIDER_MODE=live`. In fixture mode
(default or `ODP_EXTERNAL_PROVIDER_MODE=fixture`) all adapters use
deterministic replay. Setting `live` without the required env vars raises
`ExternalProviderConfigError` at startup (`validate_external_providers_or_raise()`
in `modules/external_data/connectors/provider_registry.py`).

**Required env vars for live external provider activation:**

| Provider | Required env var | Auth mode | Status env var |
|---|---|---|---|
| `listing.partner_feed` | `ODP_LISTING_PROVIDER_API_KEY` | `api_key` | `ODP_LISTING_PROVIDER_AUTH_STATUS` |
| `listing.partner_feed` | `ODP_LISTING_PROVIDER_FEED_URL` | (endpoint URL) | — |
| `poi.commercial_api` | `ODP_POI_PROVIDER_API_KEY` | `api_key` | `ODP_POI_PROVIDER_AUTH_STATUS` |
| `geocode.primary_api` | `ODP_GEOCODE_PROVIDER_API_KEY` | `api_key` | `ODP_GEOCODE_PROVIDER_AUTH_STATUS` |
| `geocode.primary_api` | `ODP_GEOCODE_PROVIDER_URL` | (endpoint URL) | — |
| `admin_boundary.official_dataset` | `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN` | `bearer_token` | `ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS` |
| `competitor.manual_source` | `ODP_COMPETITOR_MANUAL_SOURCE_ATTESTATION` | `manual_attestation` | `ODP_COMPETITOR_MANUAL_SOURCE_STATUS` |

Status env vars must not be `expired / unauthorized / revoked / invalid`.
Placeholder values (`""`, `changeme`, `todo`, etc.) are rejected.
`competitor.manual_source` is `allowed_in_production=False` — blocked by
license gate in a `production`-like deploy env.

### Live Map Tile / Geocoder: Code Path and Required Config

`apps/web/features/map/HeatZoneMap.tsx` — `readMapBoundaryConfig()` reads
four `NEXT_PUBLIC_*` build-time env vars. An empty `NEXT_PUBLIC_ODP_MAP_TILE_URL`
is a safe no-op: the map falls back to local MapLibre style with status
`"local MapLibre style"`.

**Required build-time env vars for live map activation:**

| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_ODP_MAP_TILE_URL` | MapLibre/deck.gl tile provider base URL |
| `NEXT_PUBLIC_ODP_GEOCODER_URL` | Geocoder API URL for address search |
| `NEXT_PUBLIC_ODP_MAP_ATTRIBUTION` | Provider attribution text (must match license terms) |
| `NEXT_PUBLIC_ODP_MAP_TERMS_URL` | Provider terms-of-service URL |

`NEXT_PUBLIC_*` vars are baked into the Next.js production build. If the
provider embeds a key in the URL (e.g., MapTiler style URLs), treat the
full URL as sensitive and do not commit it.

### Remote Staging: Code Path and Required Config

`.github/workflows/deploy-staging.yml` runs
`scripts/e2e/check_remote_staging_proof.py`. Missing host/url/secret owner
produce a failed check, not a fabricated pass.

**Required GitHub `staging` environment configuration:**

| Name | Type |
|---|---|
| `ODP_STAGING_DEPLOY_URL` | variable |
| `ODP_STAGING_API_URL` | variable |
| `ODP_STAGING_HOST` | variable |
| `ODP_STAGING_SECRET_OWNER` | variable |
| `ODP_STAGING_DEPLOY_USER` | secret |
| `ODP_STAGING_SSH_PRIVATE_KEY` | secret |
| `ODP_STAGING_DATABASE_URL` | secret |

See `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md` for the full
execution sequence and closeout criteria.

### Risk Acceptance Table (Pending Human/Ops Sign-Off)

| Risk | Current state | Accepted? |
|---|---|---|
| Live external provider not activated | Wired, fail-closed. Needs real API keys. | ☐ PENDING |
| Live map tile / geocoder not configured | Wired, safe fallback. Needs NEXT_PUBLIC_* vars at build. | ☐ PENDING |
| Remote staging not configured | Wired, fail-closed. Needs GitHub staging environment config. | ☐ PENDING |

LLM-Agent: Antigravity6
Task-ID: ODP-FIN-LIVE-001
Reviewer: Antigravity7
