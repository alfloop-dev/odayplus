# ODP-DEV-CANDIDATE-REBIND-B175-001 — dev candidate evidence

Status: evidence only. This task does not request a lease and does not deploy. The dev rollout is `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`; it may register a lease request only after the candidate PR is reviewed and merged, an exact-tuple user authorization exists, and actual merged-dev admission passes.

## Current candidate: 48611d26 (build succeeded; GO for dev only, post-merge admission pending)

Candidate `48611d264e9e10b5c74b8ac1cb972bc92e5ef817` is the actual merged dev head after PR #1388 (`ODP-DEV-PRIVATE-API-TRANSPORT-AUTH-001`). The candidate is built once under the user-authorized Runtime Release build phase. This evidence continues the original PR #1384; it does not change product code or workflow files.

| Fact | Value | Source |
|---|---|---|
| Exact-candidate CI | `36808278515` (`merge_group`), success; all ten jobs including all seven product child jobs ran on exact C | `candidate-ci-48611d26.json` |
| Build | Runtime Release run `36823039168`, exact head C, completed successfully; build job `110242751752` succeeded | `build-reconciliation-36823039168.json` |
| Manifest | logical digest `sha256:eb97c149496cfda57fc353e121be0a01a1c75282c9e23a9f32052913745a99c7`; raw SHA-256 `1075e139de973fe55e9d8c74b93cf2c920168803eace03ab4d067119637f0b0c`; canonical copy is byte-identical | `build-reconciliation-36823039168.json` |
| Hosted artifacts | all six ZIPs were downloaded from GitHub; each downloaded archive SHA-256 matches its GitHub `artifact.digest`; expanded files are stored under `hosted-artifacts/run-36823039168/` | `build-reconciliation-36823039168.json` |
| Build checks | secret scan, Python SAST, production npm audit (0 findings), E2E health/backup/restore/rollback proof, GCP WIF, and four cosign signature verifications passed | `build-reconciliation-36823039168.json` |
| First-release target readback | five dev Cloud Run targets were absent; this is recorded in the hosted absence-readback artifact | `hosted-artifacts/run-36823039168/initial-release-absence-readback-48611d264e9e10b5c74b8ac1cb972bc92e5ef817/initial-release-absence-readback.json` |
| Lease / deployment | no lease was supplied; lease, deploy, and watch jobs were skipped; nothing was deployed | `build-reconciliation-36823039168.json` |
| Full npm audit | reused only because all ten tracked npm inputs are byte-identical to the earlier snapshot; it has four dev-only finding records (2 high, 2 moderate), and is not described as a fresh scan | `npm-audit-input-equivalence-48611d26.json`, `dev-npm-audit-f5496614.json` |
| Exact dev authorization | `HUMANOPS-DEV-MIGRATION-20261001T141113Z`; exact C / manifest / build; H01 and npm findings remain disclosed dev-only deviations; 24-hour expiry | `user-deploy-authorization-HUMANOPS-DEV-MIGRATION-20261001T141113Z.json` |

The earlier authorized dispatch `36813751985` did not produce a usable build artifact. The corrected, separately authorized run `36823039168` is the successful build recorded here.

## Registry and admission

- Gate 0 and gate 1 pass on exact-C CI `36808278515`; historical receipts for prior candidates remain recorded as history.
- In this PR branch, Gate 4 records `passed-with-deviation` for dev only under `HUMANOPS-DEV-MIGRATION-20261001T141113Z`. The external H01 receipt remains missing and is not claimed as passed; the four dev-only npm findings remain disclosed.
- Data, model/solver, production E2E/UAT, and operations gates remain blocked for their staging/production boundaries.
- In this PR branch, `release.decision` is `go` for dev only. This is not yet actual merged-dev admission: rerun registry and Runtime Release admission after merge, against the then-current `origin/dev`, before requesting a lease.
- Previous approvals for b175b231 and f5496614 do not carry over.

## Remaining steps

1. PR #1384 already merged at `405fc892151860c19cf261c520792ea25baba5a7`; candidate ancestry is evidence-only.
2. The exact user authorization is recorded in `user-deploy-authorization-HUMANOPS-DEV-MIGRATION-20261001T141113Z.json`. This follow-up branch updates gate 4 and the dev-only release decision; submit it for review and merge.
3. The branch-local registry and runtime admission projection against current `origin/dev` is recorded in `admission-projection-48611d26.json`. It passes but is explicitly not post-merge proof.
4. After this branch merges, rerun registry and Runtime Release admission against the actual current `origin/dev` SHA. Only if both return zero errors may the existing deployment task register one fresh release request and let Supervisor issue one short-lived lease. This task itself does not request a lease or deploy.

## History: candidate b175b231 (superseded, not deployed)

Status: evidence only. This task does not request a lease and does not deploy. The dev rollout itself is `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`, which registers a lease request only after this PR merges.

### Why the candidate changed

Candidate `ee06d1d82944` was deployed once, in Deploy Dev run `36657889962`. The lease was issued and consumed, and the migration succeeded. The run then failed at `gcloud run deploy --no-traffic` because the target service did not exist yet (`--no-traffic not supported when creating a new service`).

The fix, PR #1383 (`ODP-DEPLOY-FIRST-RELEASE-NO-TRAFFIC-FIX-001`), changed `product_ops/deployment/`. That is a non-evidence path, so ee06d1d8 can no longer pass ancestry admission. A fresh candidate was built from dev head `b175b231cea7`.

### Facts and source receipts

| Fact | Value | Source |
|---|---|---|
| Build | Runtime Release run `36724677720`, build job `109918493572`, 13:51:27–13:58:20Z; lease, deploy and watch jobs skipped | `build-reconciliation-36724677720.json` |
| Manifest | logical `sha256:b26b71357b0107a17f115b07f6ff1f71551314197ae3f4f8c4de590d9447924d` (recomputes from canonical JSON), raw sha256 `cff73bb5ae2dc764b68f041be783b4d9ee2e330fe87a694bd3619c65dad2b21b`, byte-identical to `docs/evidence/gates/RELEASE_MANIFEST.json` | `hosted-artifacts/run-36724677720/` |
| Images | four images built fresh; each signature and attestation has its own Rekor tlog entry | `build-reconciliation-36724677720.json#image_signing` |
| Target absence | five dev targets absent, probed 13:54:09–13:54:19Z | hosted `initial-release-absence-readback-*` |
| Exact-candidate CI | `36681540726` (merge_group) and `36683617429` (push), both success. All seven product jobs executed and passed on the exact candidate; no inheritance claimed | `candidate-ci-b175b231.json` |
| Human approval | `HUMANOPS-DEV-MIGRATION-20260930T145659Z`, dev only, expires 2026-10-01T14:56:59Z. It accepts the missing H01 receipt and the dev-tool npm findings as dev-only deviations | `user-deploy-authorization-HUMANOPS-DEV-MIGRATION-20260930T145659Z.json` |

### What is not claimed

- No deployment, lease, or live runtime acceptance.
- No external H01 legal approval. The missing receipt is a disclosed deviation, not a pass.

### Operator note after merge

The lease issuer reads `RELEASE_GATE_REGISTRY.json` and `RELEASE_MANIFEST.json` from the working tree of the canonical checkout `/home/lupin/odayplus`, not from git. After this PR merges, sync those two files there to merged dev before registering a lease request.

### Reproduce

```
python3 delivery_toolchain/e2e/check_release_gate_registry.py        # printed RELEASE STATE: GO at 8ed4c41d (b175 binding)
sha256sum docs/evidence/gates/RELEASE_MANIFEST.json                  # equals the hosted manifest raw sha256
```
