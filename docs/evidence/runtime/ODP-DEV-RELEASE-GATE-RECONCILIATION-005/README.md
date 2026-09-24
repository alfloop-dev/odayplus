# ODP-DEV-RELEASE-GATE-RECONCILIATION-005 — dev candidate rebind and dev-gate clearance

- **Task ID**: `ODP-DEV-RELEASE-GATE-RECONCILIATION-005`
- **Owner**: Claude · **Reviewer**: Codex
- **Date**: 2026-09-23 UTC
- **Candidate**: `9694320fc8a9922cbfed9b63e647508db32ef26c` (`origin/dev` tip)
- **Previous candidate**: `39ae43f6fe679f03dd7df459a51835cbd2d54f77` (bound by `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`, PR #1352)
- **Manifest digest**: `sha256:2b06be79cd9cd75dba0b785091cd44d22a825b09be159d9d1251978f6ffe3a61`
- **Previous manifest digest**: `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a`
- **Candidate CI run**: [35886760039](https://github.com/alfloop-dev/odayplus/actions/runs/35886760039)
- **Build run**: [Runtime Release 35887115502](https://github.com/alfloop-dev/odayplus/actions/runs/35887115502) (`success`)

## 1. Why the candidate had to move

`ODP-DEV-RELEASE-GATE-RECONCILIATION-004` recorded, on 2026-09-21, that
`39ae43f6..origin/dev` touched only `docs/evidence`, so no rebuild was needed. That is no
longer true. The complete non-evidence delta from `39ae43f6` to this candidate is seven
files:

```
apps/web/src/middleware.ts
apps/web/src/lib/auth/__tests__/middleware.test.ts
shared/domain/events.py
apps/worker/consumers/assisted_listing_intake.py
tests/contract/test_assisted_listing_intake_events.py
delivery_toolchain/security/generate_oss_notice.py
tests/security/test_oss_license_gate.py
```

They come from three merged PRs:

| PR | Task | What it changed |
|---|---|---|
| #1358 | `ODP-WEB-EDGE-RUNTIME-BUILD-WARNINGS-001` | middleware pinned to the Node.js runtime — clears gate-0 C1 |
| #1359 | `ODP-EVENT-CONSUMER-FORWARD-COMPAT-001` | consumer-mode event validation — clears the gate-1 event-schema discrepancy |
| #1363 | `ODP-OSS-RECEIPT-VERIFIER-HARDENING-001` | receipt-bound exemption verifier and its negative tests |

`infra/terraform` is unchanged across the range (0 files), so the IAM and network posture
recorded against `39ae43f6` carries over unaltered.

Because these are product and build-input paths, the images built for `39ae43f6` cannot be
reused: `check_candidate_ancestry` only tolerates evidence-path drift between the
candidate and the deployed head. The artifact handoff was therefore rebuilt.

## 2. The build run

Runtime Release run [35887115502](https://github.com/alfloop-dev/odayplus/actions/runs/35887115502),
`workflow_dispatch`, `phase=build`, `environment=dev`,
`release_sha=9694320fc8a9922cbfed9b63e647508db32ef26c`, `initial_release_recovery=true`.

- `Validate release phase inputs` — success (15s)
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
| api | `oday-api@sha256:b3b9b6bb5707aa1a1fd14e9089d65ac0a70c8e546614a90bad8519dcc1a99fc1` |
| web | `oday-web@sha256:f3ef7971d49d3d57f581897da95e4e157b06a05fed0661a9ace12900d5d87640` |
| worker | `oday-worker@sha256:6429741e09ae88b69fa6dedd5e89d1930c7a8812d2ee3ef6a83dd8f6c28bdab4` |
| scheduler | `oday-scheduler@sha256:40e3183b3ddc2d470dceee3bf6a9601f8f49b16f4cd31624553a53d28c8591d9` |
| migration | shares the `worker` image |

All four carry a Cosign `signature_refs` entry and a CycloneDX `sbom_refs` entry in the
manifest.

### Artifacts committed from the run

| Artifact | Committed path | SHA-256 of the file as committed |
|---|---|---|
| `runtime-release-manifest-9694320f…` | `docs/evidence/gates/RELEASE_MANIFEST.json` | `1e8f40a0b1a7e076ba64e6b9872f4161637872c44c34806c241f8b18559ed140` |
| `runtime-release-images-9694320f…` | `runtime-release-images.json` | `3af41e9b3736b1667690f1d40d27eda8ecb1e226e57413ac5db985f4b62c29fb` |
| `initial-release-absence-readback-9694320f…` | `initial-release-absence-readback.json` | `d34646503bfa93f8b0a4b5ac4e0d9209c9af2932902441d457f72f387c1f5e8a` |
| `release-environment-receipt-dev-build` | `release-environment-receipt.json` | `0cb1ace371a862a5598b00dc1de16e019d610ecad034f7915ad44be66fc975b9` |
| `release-npm-audit-receipt-dev` | `npm-audit-receipt.json` | `930a6a4d49330ad8a898b98eec2cc5f22b3dbda453c3602d48e4d6182e7a8c39` |
| `release-phase-receipt-dev-build` | `release-phase-receipt.json` | `8812f5c4af3eae5fd48bf431c69f23984c21a8784e0f6694906f518015199c63` |

Note that `release.manifest_digest` in the registry is the manifest's own
`manifest_digest` field (`sha256:2b06be79…`), which `release_manifest.compute_manifest_digest`
recomputes from the canonical payload — not the SHA-256 of the file on disk
(`1e8f40a0…`). Both are listed here so neither is mistaken for the other.

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
