# ODP-DEV-CANDIDATE-REBIND-B175-001 — dev candidate b175b231 evidence

Status: evidence only. This task does not request a lease and does not deploy. The dev rollout itself is `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`, which registers a lease request only after this PR merges.

## Why the candidate changed

Candidate `ee06d1d82944` was deployed once, in Deploy Dev run `36657889962`. The lease was issued and consumed, and the migration succeeded. The run then failed at `gcloud run deploy --no-traffic` because the target service did not exist yet (`--no-traffic not supported when creating a new service`).

The fix, PR #1383 (`ODP-DEPLOY-FIRST-RELEASE-NO-TRAFFIC-FIX-001`), changed `product_ops/deployment/`. That is a non-evidence path, so ee06d1d8 can no longer pass ancestry admission. A fresh candidate was built from dev head `b175b231cea7`.

## Facts and source receipts

| Fact | Value | Source |
|---|---|---|
| Build | Runtime Release run `36724677720`, build job `109918493572`, 13:51:27–13:58:20Z; lease, deploy and watch jobs skipped | `build-reconciliation-36724677720.json` |
| Manifest | logical `sha256:b26b71357b0107a17f115b07f6ff1f71551314197ae3f4f8c4de590d9447924d` (recomputes from canonical JSON), raw sha256 `cff73bb5ae2dc764b68f041be783b4d9ee2e330fe87a694bd3619c65dad2b21b`, byte-identical to `docs/evidence/gates/RELEASE_MANIFEST.json` | `hosted-artifacts/run-36724677720/` |
| Images | four images built fresh; each signature and attestation has its own Rekor tlog entry | `build-reconciliation-36724677720.json#image_signing` |
| Target absence | five dev targets absent, probed 13:54:09–13:54:19Z | hosted `initial-release-absence-readback-*` |
| Exact-candidate CI | `36681540726` (merge_group) and `36683617429` (push), both success. All seven product jobs executed and passed on the exact candidate; no inheritance claimed | `candidate-ci-b175b231.json` |
| Human approval | `HUMANOPS-DEV-MIGRATION-20260930T145659Z`, dev only, expires 2026-10-01T14:56:59Z. It accepts the missing H01 receipt and the dev-tool npm findings as dev-only deviations | `user-deploy-authorization-HUMANOPS-DEV-MIGRATION-20260930T145659Z.json` |

## What is not claimed

- No deployment, lease, or live runtime acceptance.
- No external H01 legal approval. The missing receipt is a disclosed deviation, not a pass.

## Operator note after merge

The lease issuer reads `RELEASE_GATE_REGISTRY.json` and `RELEASE_MANIFEST.json` from the working tree of the canonical checkout `/home/lupin/odayplus`, not from git. After this PR merges, sync those two files there to merged dev before registering a lease request.

## Reproduce

```
python3 delivery_toolchain/e2e/check_release_gate_registry.py        # RELEASE STATE: GO
sha256sum docs/evidence/gates/RELEASE_MANIFEST.json                  # equals the hosted manifest raw sha256
```
