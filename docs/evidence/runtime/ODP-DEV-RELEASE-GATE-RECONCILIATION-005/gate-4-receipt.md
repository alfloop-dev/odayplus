# Gate 4 (Security and Privacy Gate) — receipt for candidate 9694320fc8a9922cbfed9b63e647508db32ef26c

- **Task**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-005`
- **Recorded by**: Claude (owner of this reconciliation task)
- **Gate owner of record**: Claude / reviewer Antigravity2
- **Result**: pass, **with a recorded deviation** (see `deviation` on gate-4 in the registry)
- **Candidate**: `9694320fc8a9922cbfed9b63e647508db32ef26c`
- **CI run**: [35886760039](https://github.com/alfloop-dev/odayplus/actions/runs/35886760039) on that exact head

## Measurement on this candidate

`product-security` job [107268835108](https://github.com/alfloop-dev/odayplus/actions/runs/35886760039/job/107268835108):
**464 passed** in 178.02s, job conclusion `success`. That suite carries secret scanning,
SAST, the RBAC/ABAC matrix, audit-retention and privacy tests, the SBOM/NOTICE tests, and
the OSS licence gate tests.

Production dependency audit, from the release build's own receipt
(`npm-audit-receipt.json`, produced by Runtime Release run 35887115502 at
2026-09-23T16:13:07Z with `omit_dev: true`):

```
critical 0, high 0, moderate 0, low 0, info 0
PASS: no production vulnerabilities at or above 'high'
```

Container supply chain, from `RELEASE_MANIFEST.json` for this candidate: four images
built, four `signature_refs` (Cosign) and four `sbom_refs` (CycloneDX attestation).

Egress posture, from the manifest's `sources_off_attestation`: all 16 external sources
`disabled`, `zero_credentials_present: true`, `egress_posture: default-deny`,
`firewall_egress: default-deny`.

## Required checks

| Criterion | Status on this candidate |
|---|---|
| secret scan passes | pass (`product-security`) |
| dependency and SAST scans pass with no unresolved critical/high | pass; production dependencies 0 findings |
| RBAC/ABAC tests pass for affected roles | pass (`product-security`) |
| sensitive export and audit controls checked | pass (`product-security`) |
| IAM and infrastructure changes reviewed | offline review carried over from the `39ae43f6` gate record: PASSED with unresolved exceptions (three project-level `roles/cloudsql.client` grants without IAM conditions). Unchanged by this candidate; no `infra/terraform` change between `39ae43f6` and `9694320f` |
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

Three tests in `tests/security/test_oss_license_gate.py` assert this state as an
invariant: `test_policy_and_exemptions_remain_proposed` (`exemptions == []`),
`test_license_policy_evaluation_fails_on_unadjudicated_cases` (`status == FAIL`) and
`test_attestation_contract_valid_and_integrity_readback` (readback must not verify).
They were measured on 2026-09-22 against an attempt to register the exemptions (PR #1357,
`product-security` 3 failed / 350 passed) and they refused it. PR #1357 was closed without
merging; the exemption register is untouched. The verifier that would one day validate a
real external receipt was built separately under `ODP-OSS-RECEIPT-VERIFIER-HARDENING-001`
(PR #1363, merged), with the external readback explicitly mocked and no live approval
claimed.

The deviation recorded on gate-4 is therefore the honest form of this state: the decision
exists and is named, the receipt does not, and the difference is written down rather than
papered over.

## What this receipt does not claim

Two required security readbacks cannot exist before a first deployment and are not claimed
here: the live default-deny egress probe (`.odp_data/deployment/public-egress-probe.json`)
and the live IAM state readback. Both remain with `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
and are conditions on the deviation. The offline egress contract digest
`sha256:8492ae19…` in the manifest is supplementary evidence, not a live probe.
