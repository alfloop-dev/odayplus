# Proposed dev admission — awaiting user decision

Status: **NOT APPROVED**. This document is a reviewable request, not a Human/Ops signoff, lease request or legal receipt.

## Exact scope

- Project/environment: `odayplus-runtime-20260825`, **dev only**.
- Candidate: `355a94b52b14badc236be4b3e52eb936a7075549` (merged migration version isolation PR #1372).
- Manifest: `sha256:a1e3fcf6b765861dc269e3fd395b7bec6ac4b2b6f3b3207a3ffb623c6ca6dfaa`.
- Build: https://github.com/alfloop-dev/odayplus/actions/runs/36313147910 (successful; deployment jobs skipped).
- Proposed action after independent evidence review: clear only dev admission with the explicit scoped deviations below, obtain a fresh supervisor-signed release lease and use the existing Runtime Release deploy path for this candidate. Preserve all 16 external sources disabled and default-deny egress, and collect actual migration/runtime/smoke receipts before declaring dev complete.

## Risks to acknowledge

Fresh audit at this candidate reports 1 high (`js-yaml`, GHSA-2883-xcg3-v3hh: possible CPU exhaustion when parsing adversarial YAML) and 2 moderate (`vitest`/`@vitest/mocker`, GHSA-82fw-gwwq-j7x9: path traversal/file read in the test toolchain), 0 critical. These are the same three development-toolchain findings from the earlier signed operator record; dependency manifests/lockfile are unchanged. The build's production-only dependency audit reports zero findings.

The September 18 acceptance explicitly excludes other candidate baselines. The requested decision extends **only those same three findings on this exact candidate** for this dev rollout. It does not cover new advisories, other dependencies, any critical finding or production dependency exposure. Prior conditions remain: address js-yaml with the next ESLint major update, update Vitest in the next existing lockfile update when available, and re-evaluate immediately if a finding reaches production dependency paths.

Four LGPL cases retain their existing conditional operator decision (unmodified upstream binaries, dynamic loading, NOTICE disclosure and source availability). The external authoritative H01 legal receipt is still absent. Requested dev admission records that gap as an explicit dev-only deviation, not an external legal approval or a change to license_policy/license_exemptions. Existing unresolved IAM-condition exceptions and the two runtime-only egress/IAM readbacks remain disclosed as in the prior candidate; live readbacks must be collected during deployment.

## Bounds

No staging/production admission. No enabling external sources. No deletion, reset or stamping of Dagster migration history; the merged fix uses the separate app version table. No reuse of old or consumed approval/lease. If this candidate, manifest or risk scope changes, this request no longer applies. Proposed release-request validity is 6 hours after the user's actual approval, with the standard shorter supervisor lease lifetime and a new unique nonce.

Source requirements: `docs/evidence/runtime/ODP-DEV-MIGRATION-RELEASE-REBUILD-001/gate-4-receipt.md` and `docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md`. Neither the user's GCP login nor this draft is treated as that missing risk decision.
