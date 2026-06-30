# External Proof Fleet Pickup Board

Generated: 2026-06-30  
Release target: PR #82 `headRefOid` and attached checks  
Source of truth: `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`

## Purpose

This board is the human-facing pickup surface for the remaining external proof
work. It does not replace the machine-readable closeout queue. Fleets use this
board to pick the right GitHub issue, generate the task-specific handback
skeleton, attach redacted runtime proof, and run the acceptance checker before
Product Validation closes any release blocker.

## Required Preflight

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeStateStatus,statusCheckRollup,url
python3 scripts/e2e/check_external_proof_closeout_queue.py
python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees
python3 scripts/e2e/check_external_proof_handback_template.py
```

## Pickup Table

| Task | Issue | Fleet lane | Required pickup labels | Skeleton command | Acceptance command |
|---|---:|---|---|---|---|
| `ODP-EXT-PROD-001` | #132 | Platform/Ops external provider fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-001 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-EXT-PROD-002` | #133 | Data Partnerships / Legal external provider fleet | `product-e2e`, `external-proof`, `data-partnerships`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-002 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-EXT-PROD-003` | #134 | Platform/Ops external provider fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-003 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-MAP-STAGE-001` | #135 | Platform/Ops live map fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-MAP-STAGE-002` | #136 | Platform/Ops live map fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-002 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-PV-STAGE-001` | #137 | Platform/Ops remote staging fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-PV-STAGE-001 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-PV-STAGE-002` | #138 | Platform/Ops remote staging fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-PV-STAGE-002 --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |

## What Each Lane Must Prove

### External Provider Proof

- `ODP-EXT-PROD-001`: production credential names, owner/rotation policy, startup validation, and fail-closed missing/placeholder/expired/revoked credential behavior.
- `ODP-EXT-PROD-002`: provider-specific allowed-use/license attestation, redacted production listing snapshot id, canonical lineage, freshness SLA, and export restriction/watermark behavior.
- `ODP-EXT-PROD-003`: redacted production geocoder request/response id, observed timestamp, confidence mapping, low-confidence handling, and fail-closed timeout/unauthorized/rate-limit behavior.

### Live Map Proof

- `ODP-MAP-STAGE-001`: remote staging live tile endpoint, provider attribution, terms URL, tile outage fallback, and usable HeatZone list/ranking/detail workflow.
- `ODP-MAP-STAGE-002`: remote staging live geocoder endpoint, attribution/terms approval, geocoder outage fallback, and usable listing workflow.
- Map handbacks must not rely on `mock://`, `localhost`, or `127.0.0.1` endpoint proof.

### Remote Staging Proof

- `ODP-PV-STAGE-001`: `ODP_STAGING_DEPLOY_URL`, `ODP_STAGING_API_URL`, `ODP_STAGING_SECRET_OWNER`, `ODAY_RELEASE_SHA`, `/platform/health`, and `/platform/version.release_sha` matching PR #82 `headRefOid`.
- `ODP-PV-STAGE-002`: product smoke against `ODP_STAGING_DEPLOY_URL`, API smoke against `ODP_STAGING_API_URL`, backup artifact, restore target, rollback result, and post-drill health/version proof.

## Product Validation Close Rule

Do not close #132-#138 from local fixtures, mock-live proof, deterministic CI,
or document-only evidence. A closeout requires:

- the issue still has required labels and assignees;
- the handback cites the current PR #82 `headRefOid`;
- all artifacts are redacted and declare `contains_secret_values: false`;
- every required evidence item maps to an artifact id;
- `completion_attestation.decision` is `accepted`;
- the handback passes:

```bash
python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
```
