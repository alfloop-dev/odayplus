# ODP-DEV-CANDIDATE-REBIND-B175-001 — dev candidate evidence

Status: evidence only. This task does not request a lease and does not deploy. The dev rollout is `ODP-DEV-LIVE-DEPLOY-EXECUTION-001`, which may register a lease request only after this PR merges and an approval for the current candidate exists.

## Next candidate: 48611d26 (no build yet; gates still bound to f5496614, no-go)

`ODP-DEV-PRIVATE-API-TRANSPORT-AUTH-001` (PR #1388) changed `product_ops/deployment/` and `delivery_toolchain/e2e/`. Those are non-evidence paths, so f5496614 can no longer pass ancestry admission. The next candidate is merged dev head `48611d264e9e10b5c74b8ac1cb972bc92e5ef817`. This branch merged it in as a base advance; the merge tree equals `git merge-tree` of both parents.

| Fact | Value | Source |
|---|---|---|
| Exact-candidate CI | `36808278515` (merge_group), success; all ten jobs including the seven product jobs ran on the exact candidate. The push run `36810867790` was still running at read time and is not cited | `candidate-ci-48611d26.json` |
| npm audit inputs | all ten tracked npm inputs are byte-identical to f5496614 and match the original input proof. The 01:13:32Z dev-inclusive audit snapshot is reused by equivalence. This is not a fresh scan | `npm-audit-input-equivalence-48611d26.json` |
| Runtime Release build | none. Deploy Dev has no run for this SHA (latest is `36799446467` on f5496614) | readback of workflow `302984644` runs |

What has not been done, and why:

- No build was dispatched. A build needs an explicit operator permission for this exact SHA. Earlier auto-review refusals (Production Deploy dispatch, Security Weaken gate flip) are not retried and are not routed around with a different tool or actor.
- `RELEASE_MANIFEST.json` and `RELEASE_GATE_REGISTRY.json` are unchanged. They still describe f5496614, and decision stays no-go. They change only after a real build exists for 48611d26.
- The f5496614 approval (`HUMANOPS-DEV-MIGRATION-20261001T012941Z`) is bound to that tuple and does not carry over.

Build parameters for the operator (same shape as run `36799446467`): `gh workflow run deploy-dev.yml --repo alfloop-dev/odayplus --ref dev -f phase=build -f environment=dev -f release_sha=48611d264e9e10b5c74b8ac1cb972bc92e5ef817 -f task_id=ODP-DEV-LIVE-DEPLOY-EXECUTION-001 -f initial_release_recovery=true`, with `release_lease` and the remaining optional inputs left empty. The recovery flag records fresh target-absence evidence for the first release; this command remains a proposal until explicit permission is granted.

## Current candidate: f5496614 (decision: no-go, awaiting user approval)

### Why the candidate changed again

Exact-candidate CI on b175b231 later found a urllib3 vulnerability and a SiteScore receipt test that depended on the wall clock. `ODP-SEC-URLLIB3-20260930-001` (PR #1385) fixed both by changing `uv.lock` and `tests/models/`. Those are non-evidence paths, so b175b231 can no longer pass ancestry admission. The user dispatched one fresh build from dev head `f5496614c01b`.

### Facts and source receipts

| Fact | Value | Source |
|---|---|---|
| Build | Runtime Release run `36799446467`, dispatched by `ajoe734`, build job `110170336414`, 01:06:20–01:14:28Z on 2026-10-01; lease, deploy and watch jobs skipped | `build-reconciliation-36799446467.json` |
| Manifest | logical `sha256:a0fc3aa9647834528a44ac19d1ea9429c4933071248424bfa386591452322e9c` (recomputed with `compute_manifest_digest`), raw sha256 `c79c5283048a497bd0cc33268e5046905b84c35c6be88f497ceac8ea98e3b07f`, byte-identical to `docs/evidence/gates/RELEASE_MANIFEST.json` | `hosted-artifacts/run-36799446467/` |
| Artifacts | all six archives downloaded again; each sha256 equals the GitHub artifact digest, and the expanded files equal the root readback in `/tmp/oday-dev-build-36799446467-root-readback/` | `build-reconciliation-36799446467.json#uploaded_artifacts` |
| Images | four images built fresh; each signature and attestation has its own Rekor tlog entry | `build-reconciliation-36799446467.json#image_signing` |
| Target absence | five dev targets absent, probed 01:09:43–01:09:53Z | hosted `initial-release-absence-readback-*` |
| Exact-candidate CI | `36795834052` (merge_group) and `36797803528` (push), both success; all seven product jobs executed and passed on the exact candidate; no inheritance claimed | `candidate-ci-f5496614.json` |
| Production npm audit | 0 findings (`--omit=dev`) | hosted `release-npm-audit-receipt-dev` |
| Full npm audit | 2 high, 2 moderate, all in dev dependencies. js-yaml and vitest/mocker were disclosed before; brace-expansion GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p (high) and GHSA-q2hr-2g5m-vwhr (moderate) are new | `dev-npm-audit-f5496614.json` |
| Approval | none. `HUMANOPS-DEV-MIGRATION-20260930T145659Z` covers only b175b231 and is not extended. The exact question for the user is recorded, unanswered | `approval-request-f5496614.json` |

### Registry state

- gate-0 and gate-1: passed on exact-candidate CI.
- gate-4: blocked until the user answers the recorded question. It cannot be passed-with-deviation without that answer.
- `release.decision`: `no-go`. `check_release_gate_registry.py` exits 0 and prints `RELEASE STATE: NO-GO`.

### Admission

`admission-dry-run-f5496614.json` records the real `registry_admission_errors` output on a simulated merge of this PR head onto f5496614. It lists the expected refusals (decision is no-go, gate-4 not cleared) and nothing else. The PR head itself includes a base-advance merge of dev, so `check_candidate_ancestry` against the PR head is not meaningful; the final check must run against the actual merged dev SHA, whose first parent is the candidate.

### Next steps (not done by this task)

1. The user answers the question in `approval-request-f5496614.json`. An interactive session transcribes the answer into `user-deploy-authorization-<approval_id>.json`, sets gate-4 and `release.decision`, and reruns admission.
2. After that commit is reviewed and merged, sync the two gate files into the canonical checkout `/home/lupin/odayplus` before any lease request (the lease issuer reads the working tree there).

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
