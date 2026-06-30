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
python3 scripts/e2e/sync_external_proof_fleet_issues.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees
python3 scripts/e2e/check_external_proof_handback_template.py
python3 scripts/e2e/check_external_proof_handback_status_board.py
python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report
python3 scripts/e2e/update_external_proof_handback_status_board.py --help
python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees
python3 scripts/e2e/check_external_proof_fleet_notifications.py
python3 scripts/e2e/check_external_proof_fleet_pickup_board.py
python3 scripts/e2e/check_product_go_no_go.py
```

When PR #82 changes `headRefOid`, Product Validation refreshes the live GitHub
handoff surface before asking fleets to continue:

```bash
python3 scripts/e2e/sync_external_proof_fleet_issues.py --release-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --apply
python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees
python3 scripts/e2e/check_external_proof_fleet_notifications.py
```

## Pickup Table

| Task | Issue | Fleet lane | Required pickup labels | Skeleton command | Acceptance command |
|---|---:|---|---|---|---|
| `ODP-EXT-PROD-001` | #132 | Platform/Ops external provider fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-001 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-EXT-PROD-002` | #133 | Data Partnerships / Legal external provider fleet | `product-e2e`, `external-proof`, `data-partnerships`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-002 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-EXT-PROD-003` | #134 | Platform/Ops external provider fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-EXT-PROD-003 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-MAP-STAGE-001` | #135 | Platform/Ops live map fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-001 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-MAP-STAGE-002` | #136 | Platform/Ops live map fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-MAP-STAGE-002 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-PV-STAGE-001` | #137 | Platform/Ops remote staging fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-PV-STAGE-001 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |
| `ODP-PV-STAGE-002` | #138 | Platform/Ops remote staging fleet | `product-e2e`, `external-proof`, `platform-ops`, `release-blocker` | `python3 scripts/e2e/generate_external_proof_handback_skeleton.py --task ODP-PV-STAGE-002 --release-sha-from-pr82 --output <handback.json>` | `python3 scripts/e2e/check_external_proof_handback_artifact.py <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"` |

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

## Queue-Exact Required Evidence

### `ODP-EXT-PROD-001`

- secret inventory names configured in the deployment environment
- secret owner and rotation policy recorded
- live-mode startup validation passes without printing secret values
- failure cases still fail closed for missing/placeholder/expired/revoked credentials
- Completion rule: Do not close from deterministic fixture or mock-live evidence; close only with environment-specific credential proof and redacted logs.

### `ODP-EXT-PROD-002`

- provider allowed-use/license attestation for production
- redacted production listing raw snapshot id
- canonical snapshot id and lineage
- freshness SLA result
- export restriction/watermark behavior for licensed data
- Completion rule: Do not close until the production provider/license evidence is attached; mock-live provider proof is insufficient.

### `ODP-EXT-PROD-003`

- redacted production geocoder request/response id
- provider observed timestamp
- confidence mapping
- low-confidence flag proof
- timeout/unauthorized/rate-limit fail-closed behavior remains covered
- Completion rule: Do not close from replay fixture alone; close only with redacted production geocoder proof.

### `ODP-MAP-STAGE-001`

- staging map tile URL configured
- provider attribution and terms URL visible
- remote staging smoke proves map list/ranking/detail fallback remains usable during tile outage
- proof report references current PR #82 headRefOid
- Completion rule: Do not close from local MapLibre/deck proof; close only with remote staging endpoint smoke.

### `ODP-MAP-STAGE-002`

- staging geocoder URL configured
- geocoder outage fallback remains usable
- attribution/terms approval is visible
- proof report references current PR #82 headRefOid
- Completion rule: Do not close from local geocoder fallback proof; close only with remote staging geocoder smoke.

### `ODP-PV-STAGE-001`

- ODP_STAGING_DEPLOY_URL configured
- ODP_STAGING_API_URL configured
- ODP_STAGING_SECRET_OWNER configured
- ODAY_RELEASE_SHA injected
- /platform/health reachable
- /platform/version.release_sha equals current PR #82 headRefOid
- redacted remote staging proof report artifact
- Completion rule: Do not close until the checker passes against the configured remote staging target.

### `ODP-PV-STAGE-002`

- ODP-PV-STAGE-001 passed against the same target
- product E2E smoke runs against ODP_STAGING_DEPLOY_URL
- API smoke runs against ODP_STAGING_API_URL
- backup artifact id and timestamp
- restore target and timestamp
- rollback command/result
- post-drill health/version proof with correlation id
- Completion rule: Do not close until staging smoke and staging or approved staging-equivalent drill artifacts are attached.

## Queue-Exact Runtime Commands

These are the queue-defined commands each fleet must include in its handback
`commands_run` evidence. Keep this section synchronized with
`PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`; `check_external_proof_fleet_pickup_board.py`
fails if any command fragment is missing.

### `ODP-EXT-PROD-001`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
uv run pytest tests/e2e/test_external_source_product_e2e.py -k "live_provider_mode_product_e2e or auth_quota_and_freshness" -q
```

### `ODP-EXT-PROD-002`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
uv run pytest tests/e2e/test_external_source_product_e2e.py -q
```

### `ODP-EXT-PROD-003`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
uv run pytest tests/data/test_geo_pipeline.py -q
```

### `ODP-MAP-STAGE-001`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1
```

### `ODP-MAP-STAGE-002`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1
```

### `ODP-PV-STAGE-001`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
python3 scripts/e2e/check_remote_staging_proof.py --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --correlation-id "corr-odp-pv-stage-001"
```

### `ODP-PV-STAGE-002`

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" ODP_API_BASE_URL="$ODP_STAGING_API_URL" npx playwright test tests/e2e/product-e2e-env.spec.ts --project=chromium --timeout=90000
python3 scripts/e2e/check_remote_staging_proof.py --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" --correlation-id "corr-odp-pv-stage-002-version"
```

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

The release go/no-go packet must still show these issues as pending external
proof until Product Validation accepts every handback. The guarded packet is
`docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md`. Validate that boundary before
release closeout:

```bash
python3 scripts/e2e/check_product_go_no_go.py
```

Track handback intake status in
`docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json`; it is not runtime
proof, but it is the machine-readable board Product Validation uses to mark
whether each #132-#138 handback is pending, submitted, needs revision, or
accepted. Validate it before release closeout:

```bash
python3 scripts/e2e/check_external_proof_handback_status_board.py
```

Product Validation should also generate the acceptance readiness report before
asking fleets for corrections or before accepting any handback:

```bash
python3 scripts/e2e/check_external_proof_acceptance_readiness.py --report
python3 scripts/e2e/check_external_proof_acceptance_readiness.py --strict-complete
```

The first command is expected to pass while handbacks are pending and lists the
missing evidence per #132-#138 task. The `--strict-complete` command is expected
to fail until every task and the bundle status are accepted.

Product Validation should update the board with the guarded updater instead of
editing JSON by hand:

```bash
python3 scripts/e2e/update_external_proof_handback_status_board.py --task <task-id> --status handback_submitted --handback <handback.json>
python3 scripts/e2e/update_external_proof_handback_status_board.py --task <task-id> --status needs_revision --handback <handback.json> --next-action "<specific correction>"
python3 scripts/e2e/update_external_proof_handback_status_board.py --task <task-id> --status accepted --handback <handback.json> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
```

Before closing any #132-#138 issue, verify that live GitHub blocker state still
matches the handback status board:

```bash
python3 scripts/e2e/check_external_proof_live_blockers.py --require-assignees
```

When PR #82 gets a new `headRefOid`, verify every fleet issue has a pickup
comment for that current release head:

```bash
python3 scripts/e2e/check_external_proof_fleet_notifications.py
```

When all seven handbacks are ready, validate the complete set before release
closeout:

```bash
python3 scripts/e2e/check_external_proof_handback_bundle.py <handback-dir-or-files> --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)"
```
