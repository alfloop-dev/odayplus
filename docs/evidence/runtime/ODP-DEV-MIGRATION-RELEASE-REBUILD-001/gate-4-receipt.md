# Gate 4 (Security and Privacy Gate) — receipt for candidate 355a94b52b14badc236be4b3e52eb936a7075549

- **Task**: `ODP-DEV-MIGRATION-RELEASE-REBUILD-001`
- **Recorded by**: Antigravity3 (rebuild owner)
- **Gate owner of record**: Claude / reviewer Antigravity2
- **Result**: blocked (pending candidate-applicable human risk acceptance and release authorization)
- **Candidate**: `355a94b52b14badc236be4b3e52eb936a7075549`
- **CI run**: [36311413006](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006) on that exact head
- **Build run**: [36313147910](https://github.com/alfloop-dev/odayplus/actions/runs/36313147910)

## Measurement on this candidate

`product-security` job [108597973707](https://github.com/alfloop-dev/odayplus/actions/runs/36311413006/job/108597973707):
job conclusion `success`. That suite carries secret scanning, SAST, the RBAC/ABAC matrix,
audit-retention and privacy tests, the SBOM/NOTICE tests, and the OSS licence gate tests.

Production dependency audit, from the release build's own receipt
(`npm-audit-receipt.json`, produced by Runtime Release run 36313147910 at
2026-09-27T10:38:32Z with `omit_dev: true`):

```
critical 0, high 0, moderate 0, low 0, info 0
PASS: no production vulnerabilities at or above 'high'
```

Container supply chain, from `RELEASE_MANIFEST.json` for candidate `355a94b52b14badc236be4b3e52eb936a7075549`:
four images built, four `signature_refs` (Cosign) and four `sbom_refs` (CycloneDX attestation).

Egress posture, from the manifest's `sources_off_attestation`: all 16 external sources
`disabled`, `zero_credentials_present: true`, `egress_posture: default-deny`,
`firewall_egress: default-deny`, `contract_digest: sha256:d18b0a1178ed69442a92a9abe76e2c22f049d17ee0c86e34d39233cff244cd72`.

## Required checks

| Criterion | Status on this candidate |
|---|---|
| secret scan passes | pass (`product-security`) |
| dependency and SAST scans pass with no unresolved critical/high | pass for production dependencies (0 findings); dev-toolchain vulnerability waiver pending candidate-specific human signoff |
| RBAC/ABAC tests pass for affected roles | pass (`product-security`) |
| sensitive export and audit controls checked | pass (`product-security`) |
| IAM and infrastructure changes reviewed | offline review carried over: PASSED with unresolved exceptions (three project-level `roles/cloudsql.client` grants without IAM conditions). Unchanged by this candidate; no `infra/terraform` change |
| **licence-aware SBOM produced and OSS licence gate passes** | SBOM produced; automated licence gate verdict is FAIL (0 violations, 4 LGPL cases in review_required; H01 external receipt pending) |

## Gate blockers and missing human inputs

1. **Dev-toolchain vulnerability risk acceptance scope**:
   The dev-toolchain risk acceptance in `docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-dev-toolchain-vulnerability-risk-acceptance.md` was expressly limited to baseline `dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc` and excludes other baselines. Candidate `355a94b52b14badc236be4b3e52eb936a7075549` requires a candidate-applicable human risk acceptance / waiver before gate-4 can be cleared.

2. **Candidate-specific release authorization**:
   Human/Ops release authorization for candidate `355a94b52b14badc236be4b3e52eb936a7075549` dev admission is pending.

3. **OSS license external receipt**:
   The four LGPL cases (LGPL-SHARP-LIBVIPS, LGPL-PSYCOPG2, LGPL-PSYCOPG3, LGPL-MOOCORE) carry conditional operator approval in `docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-oss-license-four-lgpl-cases.md`, but external authoritative receipt (H01) is pending. `license_policy.json` remains `proposed` and `license_exemptions.json` remains empty.

## What this receipt does not claim

Two required security readbacks cannot exist before a first deployment and are not claimed
here: the live default-deny egress probe (`.odp_data/deployment/public-egress-probe.json`)
and the live IAM state readback. Both remain with `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`. The offline egress contract digest
`sha256:d18b0a11…` (`sources_off_attestation.egress_evidence.contract_digest`) in the manifest is supplementary evidence, not a live probe.
