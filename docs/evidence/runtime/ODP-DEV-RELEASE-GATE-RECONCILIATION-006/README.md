# ODP-DEV-RELEASE-GATE-RECONCILIATION-006 — dev candidate rebind and dev-gate clearance

- **Task ID**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-006`
- **Owner**: Antigravity7 (reassigned from Claude) · **Reviewer**: Codex
- **Date**: 2026-09-24 UTC
- **Candidate**: `419e6bf4958269c5b9e94efcb80770e28cd54dda` (`origin/dev` tip)
- **Previous candidate**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77` (bound by `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`, PR #1352)
- **Manifest digest**: `sha256:134cc712132155b0003d68063298d3044d5400d91244b8024b4448268c4fc678`
- **Previous manifest digest**: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a`
- **Candidate CI run**: [36034118291](https://github.com/alfloop-dev/odayplus/actions/runs/36034118291)
- **Build run**: [Runtime Release 36080312679](https://github.com/alfloop-dev/odayplus/actions/runs/36080312679) (`success`)

## 1. Why the candidate had to move

`ODP-DEV-RELEASE-GATE-RECONCILIATION-004` recorded, on 2026-09-21, that
`39ae43f6..origin/dev` touched only `docs/evidence`, so no rebuild was needed. That is no
longer true. The complete non-evidence delta from `39ae43f6` to this candidate is ten
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
.github/workflows/deploy-dev.yml
tests/ops/test_deploy_workflow_contract.py
```

They come from five merged PRs:

| PR | Task | What it changed |
|---|---|---|
| #1358 | `ODP-WEB-EDGE-RUNTIME-BUILD-WARNINGS-001` | middleware pinned to the Node.js runtime — clears gate-0 C1 |
| #1359 | `ODP-EVENT-CONSUMER-FORWARD-COMPAT-001` | consumer-mode event validation — clears the gate-1 event-schema discrepancy |
| #1363 | `ODP-OSS-RECEIPT-VERIFIER-HARDENING-001` | receipt-bound exemption verifier and its negative tests |
| #1366 | `ODP-RELEASE-MANIFEST-LEGACY-FIXTURE-001` | the legacy-migration fixture repair described in section 6 |
| #1369 | `ODP-RELEASE-ADMISSION-JOB-DEPS-001` | the admission job provisions the locked Python environment it always needed |

### Two intermediate candidates that were superseded

This is the third rebind in the same chain, and each earlier one was invalidated the
same way: the repair that had to reach the runtime could not be carried as evidence, so
it had to land in `dev` on its own and move the tip past the candidate that was already
built.

| Candidate | Built by | Why it was superseded |
|---|---|---|
| `9694320fc8a9922cbfed9b63e647508db32ef26c` | run 35887115502 (success) | the legacy-migration fixture repair had to land as its own change, and `tests/release/test_release_manifest.py` is not an evidence path (PR #1366) |
| `1364363402900c800ec3ed033d38fd1d757c1f10` | run 35944616693 (success) | the admission job could not run the gs://-backed lease check at all, and the repair changes `.github/workflows/deploy-dev.yml` (PR #1369) |

Neither reached `dev` as a bound candidate. **Their images and manifests must not be
deployed**; the digest bound here, `sha256:134cc712…`, is the one from run 36080312679.

The second of those is the only one that ever produced a deploy attempt. Runtime Release
run 36027737089 (2026-09-24T16:29:34Z) carried a validly signed Supervisor lease and
failed at `Verify the Supervisor lease authorises this deploy` with

```
runtime admission blocked:
- google-cloud-storage is required for gs:// lease state
```

That was the first dispatch in this repository's history to reach that step, and it is how
the defect PR #1369 repairs became visible: the admission job had never provisioned a
Python environment, so it called the runner's bare `python3` for a check that needs the
project dependencies. `Deploy the admitted artifact by immutable digest` was skipped.
**Nothing has been deployed to dev at any point in this chain.**

`infra/terraform` is unchanged across the range (0 files), so the IAM and network posture
recorded against `39ae43f6` carries over unaltered.

Because these are product and build-input paths, the images built for `39ae43f6` cannot be
reused: `check_candidate_ancestry` only tolerates evidence-path drift between the
candidate and the deployed head. The artifact handoff was therefore rebuilt.

## 2. The build run

Runtime Release run [36080312679](https://github.com/alfloop-dev/odayplus/actions/runs/36080312679),
`workflow_dispatch`, `phase=build`, `environment=dev`,
`release_sha=419e6bf4958269c5b9e94efcb80770e28cd54dda`, `initial_release_recovery=true`.

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
| api | `oday-api@sha256:b5b6e14bdd7c9c1a6002f354eb23a9b7d568e4155494d9358c407ef51805aa03` |
| web | `oday-web@sha256:d8f42092518a8dd059a0f1ef88b4e86180deba220d1c1814cde97202eba2fb72` |
| worker | `oday-worker@sha256:cb8cd21a4e7df4030fa1f3fe18080ccd0b85dbef152b6fb505775e319840e749` |
| scheduler | `oday-scheduler@sha256:43244a46ea4ba06f283ac57937a24483c261705ecda2d47698b052822651d75d` |
| migration | shares the `worker` image |

All four carry a Cosign `signature_refs` entry and a CycloneDX `sbom_refs` entry in the
manifest.

### Artifacts committed from the run

| Artifact | Committed path | SHA-256 of the file as committed |
|---|---|---|
| `runtime-release-manifest-419e6bf4…` | `docs/evidence/gates/RELEASE_MANIFEST.json` | `487184ed9332431a9ae8abac550fdee4ea3bd99431e21481c0a3244fa285210c` |
| `runtime-release-images-419e6bf4…` | `runtime-release-images.json` | `c0f51747865ab4c52049702e104cf8ba055b2a219481becf5e218c66ab00c104` |
| `initial-release-absence-readback-419e6bf4…` | `initial-release-absence-readback.json` | `4d8383fbaee70efe9287f665b7259a9cf18338e1ab3567578455721e37a0dee3` |
| `release-environment-receipt-dev-build` | `release-environment-receipt.json` | `133ff648c54a1f4d640e3f64da8f3274e10c1516f1bbbb2d24d55b98dd6bbf89` |
| `release-npm-audit-receipt-dev` | `npm-audit-receipt.json` | `3279014ee871d875159ded1adad14369b1e46c5a74b7431d67aefec52c85217a` |
| `release-phase-receipt-dev-build` | `release-phase-receipt.json` | `1b8b1b162d528f799eb5d0d7a64087c2b429488c7c7cccc984f7cfe6447552fe` |

Note that `release.manifest_digest` in the registry is the manifest's own
`manifest_digest` field (`sha256:134cc712…`), which `release_manifest.compute_manifest_digest`
recomputes from the canonical payload — not the SHA-256 of the file on disk
(`487184ed…`). Both are listed here so neither is mistaken for the other.

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
