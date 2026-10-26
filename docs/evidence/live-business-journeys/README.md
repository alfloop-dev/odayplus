# Six business journey gate — review recovery

Task: `ODP-BUSINESS-LIVE-E2E-COVERAGE-001`; original PR: #1431.
Base contract: `edf7fdd67298ac4d0124813e6bab9a8cb7c245dd`.

This is **engineering delivery, not live acceptance**. Pi recovered the original
owner's quota-interrupted diff; Codex2 remains the independent reviewer. No live
business write, source activation/acquisition, training/promotion, IAM, network,
deployment, lease or human disposition is performed by this change.

## R1–R8 changes

- **R1:** served record identity and available tenant fields must match the
  authorized scope before writes are armed. Every actor resolves its actual
  account UUID, tenant and roles through `GET /api/v1/auth/principal`. This
  additive, read-only endpoint uses the existing authentication boundary and
  exposes only the caller's context, never another account, token, session or
  principal attributes. Local-token/browser role claims cannot override the
  identity store. A missing endpoint/grant/role blocks before business writes.
  Served RBAC audit decisions must additionally bind the actor UUID and tenant.
- **R2:** the gate CLI's admitted manifest digest reaches `GateConfig` and full
  receipt verification; CLI regression includes `--require-full-acceptance`.
- **R3:** governance uses `approve/return/reject` and the actual reason policy;
  durable `decisions` and new `auditRows` are read back. Governance records
  `corr-<approvalId>` in its domain trail; that is not misrepresented as the
  request correlation. Each write also requires its own served RBAC audit.
- **R4:** franchise identity uses `store.id` and `meta.scope.storeId`; reports
  use the actual `message` field and shell audit response correlation.
- **R5:** only the authorized, non-empty campaign can be submitted to AdLift;
  source snapshot provenance is required. A null/empty initial report blocks.
  `succeeded` without a report does not pass. The result must bind the campaign
  and job, and a fresh durable report ID must match the completed job's report
  and carry model/feature/source provenance.
- **R6:** expansion runs an explicit live Playwright entry after solve and
  before submit/approval. It logs in as the same authorized actor and verifies
  the real rebalance scenario's modelled/unmodelled disclosure selectors. No
  request interception, fixtures, local web server, tracing or screenshots.
  Missing browser tooling blocks before writes; failed UI prevents approval.
- **R7:** receipt verification requires non-empty scoped before/after identity,
  distinct non-empty write correlations, corresponding business/RBAC audit
  references, actual roles/grants, fresh job result and exact UI assertions.
- **R8:** intake uses `create/revise/duplicate/quarantine/reject`, authored
  reason and acknowledged risk. Both runner regressions and actual offline
  application-service calls exercise all five actions and durable audit shape.

## Scope and invocation

Scope and receipt schema are now **version 2**. Old version-1 receipts cannot
be reused as proof. The authorization document contains:

- `kind: odp.live-business-journey-scope`, `schema_version: 2`;
- exact `release_sha`, `manifest_digest`, named `authorized_by` and
  `authorization_ref`;
- `journeys.<id>`: tenant UUID, distinct foreign tenant UUID, actor usernames
  and `account_ids` (authoritative UUID for **each** actor slot), authorized
  `records`, `foreign_records`, actions and authored request `writes.*.body`;
- distinct approver and named `approval_ref` where required;
- expansion `records`: `scenario_id`, `rebalance_store_id`, `modelled_class`
  and `unmodelled_class` (the actual displayed disclosure labels);
- growth `writes.adlift_job.body.campaigns`: one complete authorized campaign,
  matching `records.campaign_id`, with real `source_snapshot_ids`;
- intake decision `action`, `reason`, `riskSummary`, `riskAcknowledged: true`,
  and a separately authorized HTTPS source-policy probe URL and canonical
  `source_ref`. A generic validation 422 is not a source-policy rejection.
  `revise/duplicate` also require `records.target_listing_id`, matching
  `body.targetListingId` and a live target read in the same tenant; duplicate
  must bind the intake's existing match target too.

Secrets remain in the existing per-journey credential environment variables,
not scope files, command arguments or receipts. No test credential or sample
campaign in the offline tests is an approved live input.

Only after `ODP-BUSINESS-LIVE-E2E-ACCEPTANCE-001` has actual full-profile admission,
real records/models/sources/roles and named action authorization may it run:

```sh
python3 delivery_toolchain/e2e/live_business_journeys.py \
  --api-url "$ODP_LIVE_E2E_API_URL" --web-url "$ODP_LIVE_E2E_WEB_URL" \
  --release-manifest "$ODP_RELEASE_MANIFEST_PATH" \
  --expected-sha "$ODAY_RELEASE_SHA" \
  --expected-manifest-digest "$ODP_RELEASE_MANIFEST_DIGEST" \
  --scope "$ODP_LIVE_JOURNEY_SCOPE_PATH"
```

The Python runner invokes
`npx --no-install playwright test --config tests/e2e/live/playwright.live.config.ts --reporter=line`
only after successful preflight. Install the project's pinned Node dependencies
and Chromium beforehand. The browser child receives only its authorized
credential and an allowlisted tool environment, not other journey/cloud secrets.
The normal offline Playwright suite does not discover
`business-ui.live.ts`. Neither `ODP_RELEASE_PROFILE=full` nor this code change
upgrades a sealed dev-admin manifest.

## Offline verification

Focused selection: `tests/e2e/test_live_e2e_gate.py` and
`tests/e2e/test_live_business_journeys.py`; also scoped Ruff and boundary checks.
The interactive host exports `NODE_ENV=production`, so test processes explicitly
use `NODE_ENV=test` to test in-memory doubles. No deployed environment is changed.
An initial unadjusted run failed an existing memory-persistence test with 503;
that failed run is not counted as successful proof. Final declared-command
receipts are recorded by `delivery_toolchain/git/task_verification.py` against
the committed head, with exit codes, duration and selection.

Code merge does not close live business acceptance, original brand/format/lease
requirements, structural holds, masked snapshot/model/history dependencies,
staging or production admission.
