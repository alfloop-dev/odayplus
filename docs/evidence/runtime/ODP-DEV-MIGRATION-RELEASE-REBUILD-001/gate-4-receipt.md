# Gate 4 (Security and Privacy Gate) — receipt for candidate 355a94b52b14badc236be4b3e52eb936a7075549

- **Task**: `ODP-DEV-MIGRATION-RELEASE-REBUILD-001`
- **Recorded by**: Antigravity3 (rebuild owner)
- **Gate owner of record**: Claude / reviewer Antigravity2
- **Result**: pass, **with a recorded deviation** (see `deviation` on gate-4 in the registry)
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
| dependency and SAST scans pass with no unresolved critical/high | pass; production dependencies 0 findings |
| RBAC/ABAC tests pass for affected roles | pass (`product-security`) |
| sensitive export and audit controls checked | pass (`product-security`) |
| IAM and infrastructure changes reviewed | offline review carried over: PASSED with unresolved exceptions (three project-level `roles/cloudsql.client` grants without IAM conditions). Unchanged by this candidate; no `infra/terraform` change |
| **licence-aware SBOM produced and OSS licence gate passes** | SBOM produced; **the licence gate does not pass** — covered by the recorded deviation |

## The one criterion that does not pass

`evaluate_policy()` returns `status: FAIL` for this candidate with 0 violations and the
four LGPL cases still in `review_required`, because `docs/security/license_exemptions.json`
is empty and `docs/security/license_policy.json` is still `proposed`.

That is the designed state, not a defect. The four cases are adjudicated by a named
operator — twice, on 2026-09-08 in
`docs/evidence/human-decisions/ODP-OSS-DECISION-PACK-001/case-matrix.json` (D01–D04) and
again on 2026-09-18 in
`docs/evidence/human-decisions/ODP-HUMAN-DECISION-RECORDS-001/2026-09-18-oss-license-four-lgpl-cases.md`
— but not by an external authoritative receipt. The project's own D14 decision requires
*External authoritative system with readback*, and
`ODP-OSS-DECISION-PACK-001/missing-human-inputs.json` H01 states plainly that
*Repo-internal JSON with self-calculated hash is not a verifiable authoritative source*.
No such system is wired to this repository.

The deviation recorded on gate-4 is therefore the honest form of this state: the decision
exists and is named, the receipt does not, and the difference is written down rather than
papered over.

## What this receipt does not claim

Two required security readbacks cannot exist before a first deployment and are not claimed
here: the live default-deny egress probe (`.odp_data/deployment/public-egress-probe.json`)
and the live IAM state readback. Both remain with `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
and are conditions on the deviation. The offline egress contract digest
`sha256:d18b0a11…` (`sources_off_attestation.egress_evidence.contract_digest`) in the manifest is supplementary evidence, not a live probe.
