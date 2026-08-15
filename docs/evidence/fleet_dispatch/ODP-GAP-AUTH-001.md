# ODP-GAP-AUTH-001 — Auth / identity backend

- **Owner:** Claude2
- **Reviewer:** Antigravity
- **Phase:** Product Platform Gap Closure
- **Status source of truth:** `ai-status.json`

## Problem / gap

R0-007 delivered the *authorization* half of the platform — `shared.auth`
(RBAC/ABAC engine, `Principal`, `Scope`, feature flags) and `shared.audit`. But
the *authentication boundary* was a stub: `apps/api/oday_api/security/
dependencies.py::principal_from_headers` builds a `Principal` **directly from
request headers** (`x-subject-id`, `x-roles`, `x-tenant-id`) with no
cryptographic verification. Any caller can assert any subject and any role.

This task closes that gap with a real, fail-closed server-side verification
boundary that produces a *trusted* `Principal` for the existing engine to
authorize.

## Delivered scope

New package `modules/opsboard/auth/`:

| File | Responsibility |
| --- | --- |
| `jwt.py` | Dependency-free JOSE compact JWS verify/encode (HS256/384/512 via stdlib `hmac`). Rejects `alg: none` and algorithm-confusion. Constant-time signature compare. Asymmetric (RS256/JWKS) is a documented plug-in seam (`register_verifier`). |
| `config.py` | `AuthBoundaryConfig` — trusted issuer, audiences, signing keys, leeway. `is_configured` is the **fail-closed gate**: false unless issuer + audience + keys are all present. `config_from_env` reads env, defaults to fail-closed. |
| `claims.py` | Maps verified OIDC claims → canonical `shared.auth.Principal` (roles/scope/tenant/clearance); drops unknown role ids. |
| `service_identity.py` | `ServiceIdentityVerifier` — service-to-service auth against an explicit registry; constant-time secret compare; empty registry authenticates nothing. |
| `boundary.py` | `AuthenticationBoundary.authenticate(Credentials)` — orchestrates OIDC + service paths, emits a canonical `security.authentication` audit event on **every** decision, and optional structured-log / metric signals via `shared.observability`. |

Composition (unchanged, consumed not modified): `shared.auth`, `shared.audit`,
`shared.observability`. The insecure `principal_from_headers` stub is left in
place for local/dev but is superseded by this boundary as the production path.

## Acceptance mapping

1. **Meets scope in this doc** — table above.
2. **Fail-closed when external live inputs are absent** —
   - unconfigured `AuthBoundaryConfig` ⇒ every bearer token denied
     (`BOUNDARY_NOT_CONFIGURED`); never falls back to header trust.
   - empty service registry ⇒ every service credential denied.
   - token without a bounded `exp`, `alg: none`, unknown `kid`, bad signature,
     issuer/audience mismatch, expired/not-yet-valid ⇒ all denied.
   - Tests: `tests/security/test_opsboard_auth_boundary.py` (fail-closed,
     expired, invalid, alg-confusion, service identity, audit hook);
     `tests/integration/test_auth_boundary_authz.py` (boundary → RBAC/ABAC
     end-to-end, shared audit trail).
   - Maps to security control **SEC-AUTH-001** ("Unauthenticated, expired, and
     invalid token requests are denied").
3. **Scoped task-branch PR with green required checks** — branch
   `task/ODP-GAP-AUTH-001`, PR into `dev`.

## Verification

```
python3 -m pytest tests/security/test_opsboard_auth_boundary.py \
                  tests/integration/test_auth_boundary_authz.py -q      # 30 passed
python3 -m pytest tests/security -q                                     # full security suite green
python3 -m ruff check modules/opsboard/auth tests/security tests/integration  # clean
```

## Non-goals / follow-ups

- Live RS256/JWKS fetch + rotation against a real IdP: seam is present
  (`register_verifier`, key-by-`kid`); wiring a production JWKS client and
  caching is a runtime/infra follow-up (composes with ODP-GAP-RUNTIME-001).
- Migrating `apps/api` routes off `principal_from_headers` onto this boundary:
  follow-up so existing R0-007 header-based tests are not disturbed in this PR.

## Finalization / closeout

- **Deliverable PR:** #215 (`task/ODP-GAP-AUTH-001` → `dev`), merged
  2026-07-11; auth boundary durable in `dev` at commit `a14b1d6`.
- **Review:** Reviewer **Antigravity** approved 2026-07-12 — verified the
  boundary meets all **SEC-AUTH-001** fail-closed and audit specifications
  (30 security/integration tests pass). Task moved to `review_approved`.
- **Owner re-verification (closeout):** re-ran
  `pytest tests/security/test_opsboard_auth_boundary.py tests/integration/test_auth_boundary_authz.py`
  = **30 passed**; `ruff check modules/opsboard/auth tests/security/test_opsboard_auth_boundary.py tests/integration/test_auth_boundary_authz.py`
  = clean.
- **Reviewer reconciliation:** the delivery commit `a14b1d6` carries the
  original `Reviewer: Codex` trailer (assigned before reviewer rotation);
  this closeout records the final reviewer of record as **Antigravity**, who
  performed the approving review.
