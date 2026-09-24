# ODP-DEV-RELEASE-GATE-RECONCILIATION-005 — dev candidate rebind and dev-gate clearance

- **Task ID**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-005`
- **Owner**: Claude · **Reviewer**: Codex
- **Date**: 2026-09-23 UTC
- **Candidate**: `1364363402900c800ec3ed033d38fd1d757c1f10` (`origin/dev` tip)
- **Previous candidate**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77` (bound by `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`, PR #1352)
- **Manifest digest**: `sha256:6fb8f9e2e6af8dcef9cbe2fd76f2c3d319d95a40fe64c77e3ffdb435f3dc246d`
- **Previous manifest digest**: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a`
- **Candidate CI run**: [35943771029](https://github.com/alfloop-dev/odayplus/actions/runs/35943771029)
- **Build run**: [Runtime Release 35944616693](https://github.com/alfloop-dev/odayplus/actions/runs/35944616693) (`success`)

## 1. Why the candidate had to move

`ODP-DEV-RELEASE-GATE-RECONCILIATION-004` recorded, on 2026-09-21, that
`39ae43f6..origin/dev` touched only `docs/evidence`, so no rebuild was needed. That is no
longer true. The complete non-evidence delta from `39ae43f6` to this candidate is eight
files:

```
apps/web/src/middleware.ts
apps/web/src/lib/auth/__tests__/middleware.test.ts
shared/domain/events.py
apps/worker/consumers/assisted_listing_intake.py
tests/contract/test_assisted_listing_intake_events.py
delivery_toolchain/security/generate_oss_notice.py
tests/security/test_oss_license_gate.py
tests/release/test_release_manifest.py
```

They come from four merged PRs:

| PR | Task | What it changed |
|---|---|---|
| #1358 | `ODP-WEB-EDGE-RUNTIME-BUILD-WARNINGS-001` | middleware pinned to the Node.js runtime — clears gate-0 C1 |
| #1359 | `ODP-EVENT-CONSUMER-FORWARD-COMPAT-001` | consumer-mode event validation — clears the gate-1 event-schema discrepancy |
| #1363 | `ODP-OSS-RECEIPT-VERIFIER-HARDENING-001` | receipt-bound exemption verifier and its negative tests |
| #1366 | `ODP-RELEASE-MANIFEST-LEGACY-FIXTURE-001` | the legacy-migration fixture repair described in section 6 |

### An intermediate candidate that was superseded

A first rebind targeted `9694320fc8a9922cbfed9b63e647508db32ef26c` and was built by
Runtime Release run 35887115502 (success, 2026-09-23). It never reached `dev`: the fixture
repair in section 6 had to land as its own change, and `tests/release/test_release_manifest.py`
is not an evidence path, so merging it would have broken candidate ancestry for
`9694320f`. #1366 merged that repair into `dev` instead, which moved the tip to
`13643634` and made a second build necessary. The images and manifest from run
35887115502 are therefore superseded and must not be deployed; the manifest digest bound
here, `sha256:6fb8f9e2…`, is the one from run 35944616693.

`infra/terraform` is unchanged across the range (0 files), so the IAM and network posture
recorded against `39ae43f6` carries over unaltered.

Because these are product and build-input paths, the images built for `39ae43f6` cannot be
reused: `check_candidate_ancestry` only tolerates evidence-path drift between the
candidate and the deployed head. The artifact handoff was therefore rebuilt.

## 2. The build run

Runtime Release run [35944616693](https://github.com/alfloop-dev/odayplus/actions/runs/35944616693),
`workflow_dispatch`, `phase=build`, `environment=dev`,
`release_sha=1364363402900c800ec3ed033d38fd1d757c1f10`, `initial_release_recovery=true`.

- `Validate release phase inputs` — success (10s)
- `Build once and publish the immutable artifact handoff` — success (8m22s)
- `Verify the Supervisor lease…`, `Deploy the admitted artifact…`, `Verify production watch…` — **skipped**

The three deploy jobs were skipped because no Supervisor lease was supplied. **Nothing was
deployed by this run.** `initial_release_recovery=true` was required because dev holds no
approved release; the build reads the target back and refuses unless every Cloud Run
service and job is absent. It was, for all five targets — see
`initial-release-absence-readback.json`.

Unlike the `39ae43f6` rebind, which needed two runs (35492613570 built the images but
failed the handoff step, 35493018607 reused and published them), this candidate built,
signed, attested and published in a single successful run.

### Component images

| Component | Image |
|---|---|
| api | `oday-api@sha256:e43d0038d0d9cec3d1460f6992f86e2550fa4c778258d77070f0f7a55444ec97` |
| web | `oday-web@sha256:7792bc544a1f09f13023d2394ec22c18324b69907fda046323f66bda0d0d0997` |
| worker | `oday-worker@sha256:67ae8111446fd12413e5fab720bd861e6d5660f4f466eb7e5b0f4ca6203dddfd` |
| scheduler | `oday-scheduler@sha256:050133c6cc5094783a828d02701f5a78c011b76d0d8773c95ff137ef61505e1f` |
| migration | shares the `worker` image |

All four carry a Cosign `signature_refs` entry and a CycloneDX `sbom_refs` entry in the
manifest.

### Artifacts committed from the run

| Artifact | Committed path | SHA-256 of the file as committed |
|---|---|---|
| `runtime-release-manifest-13643634…` | `docs/evidence/gates/RELEASE_MANIFEST.json` | `5e70973d094474967e313ad5b148649ec5f24b322c1cbc053b5c4af4bbd2c6c3` |
| `runtime-release-images-13643634…` | `runtime-release-images.json` | `03f1388792f5482c4d0f8b84f434468ed8cb57d770d173f85ace824ab2acb66f` |
| `initial-release-absence-readback-13643634…` | `initial-release-absence-readback.json` | `1fc0216709cd466450acb6b82bab9a8f032ac3919d8e49633a6607046344a6d9` |
| `release-environment-receipt-dev-build` | `release-environment-receipt.json` | `33121487c5e74fac311177b45f637460b1e9bffd38af90135aeedfe2612db066` |
| `release-npm-audit-receipt-dev` | `npm-audit-receipt.json` | `9559797b5f7a87ae12a4333efb102d10d11cab6e5403bfecfdf84078d6dbb7ed` |
| `release-phase-receipt-dev-build` | `release-phase-receipt.json` | `b65db13d7ffb4bd38dfc868b43e63816aa71b072ee8f8e2047ad3d4afad05aea` |

Note that `release.manifest_digest` in the registry is the manifest's own
`manifest_digest` field (`sha256:6fb8f9e2…`), which `release_manifest.compute_manifest_digest`
recomputes from the canonical payload — not the SHA-256 of the file on disk
(`5e70973d…`). Both are listed here so neither is mistaken for the other.

## 3. Gate disposition

Only gates bound to `admission_target: dev` are touched. Gates 2, 3, 5 and 6 are bound to
staging and production, keep their blockers, and stay `blocked`; their blocker prose was
rewritten only to name the new candidate SHA, because a blocker that still names a
superseded candidate reads as current fact to the next worker.

| Gate | Status | Receipt |
|---|---|---|
| gate-0 Code | `passed` | [`gate-0-receipt.md`](gate-0-receipt.md) |
| gate-1 Contract | `passed` | [`gate-1-receipt.md`](gate-1-receipt.md) |
| gate-4 Security and Privacy | `passed-with-deviation` | [`gate-4-receipt.md`](gate-4-receipt.md) |
| gate-2 Data | `blocked` (staging) | — |
| gate-3 Model and Solver | `blocked` (production) | — |
| gate-5 E2E/Performance/UAT | `blocked` (production) | — |
| gate-6 Ops/Release/Audit | `blocked` (production) | — |

`registry_admission_errors` only requires gates whose `admission_target` equals the
environment being deployed, so clearing gate-0, gate-1 and gate-4 admits dev and nothing
else. This is the documented break in the old Gate-0..6 circularity, not a shortcut.

## 4. The gate-4 deviation, stated plainly

The OSS licence gate's automated verdict is still `FAIL` on this candidate and will stay
that way. The four LGPL cases are adjudicated by a named operator but not by the external
authoritative receipt that H01 and the project's own D14 decision require, and no external
identity or receipt system is wired to this repository. Rather than edit the three tests
that assert the un-adjudicated state — which is the only way to turn that gate green, and
exactly the tripwire that should not be moved by an AI worker — the decision is recorded
as a deviation on gate-4, with conditions and a review date. `license_policy.json` stays
`proposed` and `license_exemptions.json` stays empty.

The full reasoning, including the 2026-09-22 measurement where those tests correctly
refused an attempt to register the exemptions (PR #1357, closed unmerged), is in
[`gate-4-receipt.md`](gate-4-receipt.md).

## 5. What this task did not do

- No deployment. No Supervisor lease was requested, issued or supplied.
- No edit to `docs/security/`.
- No change to any gate bound to staging or production.
- The `human_signoff` and `deviation.approver` fields record a named operator decision
  taken in an interactive session. They are release-admission decisions recorded in this
  repository, not external authoritative receipts.

## 6. CI repair on the first submission

The first submitted head `a9acabd8` failed one required check. CI run 35933577494
(created 2026-09-23T23:26:06Z, `product-lint-unit` completed 2026-09-23T23:49:31Z) reported:

```
FAILED tests/release/test_release_manifest.py::test_legacy_migration_adds_identity_and_requires_re_attestation
  release.decision is 'go' but these gates are not cleared: ['gate-2', 'gate-3', 'gate-5', 'gate-6']
1 failed, 6191 passed, 23 skipped
```

The `product` lane failed only as the aggregate of that lane. Every other product lane,
including `product-security`, `product-api-contract` and `product-e2e-gate`, passed on the
same head.

**Cause.** That test builds its v1 "legacy" fixture by loading the live registry and
stripping `admission_target` from the release block and every gate, then migrates it.
`migrate_registry` rebinds all seven gates to the `dev` boundary. A v1 registry had no
admission target, so `decision: go` there meant all seven gates were cleared. This task is
the first time the live registry records `go` scoped to one target while staging- and
production-bound gates stay blocked; v1 cannot express that state, so the fixture carried a
scoped `go` into a full-scope registry and the validator refused it. The validator and the
migration module are behaving correctly; the fixture was calibrated on a registry that had
only ever been `no-go`.

**Fix.** `tests/release/test_release_manifest.py` now builds the fixture through one
`legacy_registry()` helper that projects the live registry onto the v1 shape and sets the
legacy decision to `no-go` whenever any gate is still open, which is what a real v1
registry in that state would have recorded. Both legacy-migration tests use it. No change
to `delivery_toolchain/e2e/check_release_gate_registry.py`, to
`delivery_toolchain/release/migrate_gate_registry.py`, or to any registry or manifest field.

**Measured locally (Python 3.12, `uv run --frozen`):** `ruff check` on the file passed;
`pytest tests/release/test_release_manifest.py` reported 87 passed, 0 failed. The declared
verification command `check_release_gate_registry.py` is re-run against the new head and its
receipt is recorded through `task_verification.py` before resubmission.
