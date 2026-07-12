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
