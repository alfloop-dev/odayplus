# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001

## 結論

本輪依正常 task workflow 合入 `origin/dev`，並對含有 product input 變更的最新
base `d858e1c3a75489b5ecae5f67920fb314289a93d9` 重新執行唯一 `Runtime Release`
build phase。Hosted run [33642907363](https://github.com/alfloop-dev/odayplus/actions/runs/33642907363)
成功完成 environment binding、security/SBOM/dependency gates、GCP WIF、target
absence readback、四個 image 的 build/push/Cosign/SBOM attest，以及 build-once
manifest handoff。

部署仍維持 **fail-closed / NO-GO**：canonical release gate registry 尚綁定舊候選
`ebc4fca5c2dd5871275aee39a18406dd67464f04` 且為 `no-go`，Supervisor 尚未對本次
manifest 發出 signed lease。故本輪沒有執行 Cloud Run deploy、migration/worker/
scheduler execution、traffic mutation 或 authenticated smoke，也沒有產生成功部署
收據。這是授權與 admission 缺口，不把 build 成功誤標成 live rollout 成功。

## 1. Exact candidate 與 build-once handoff

| 項目 | 真實值 |
|---|---|
| current `origin/dev` / release SHA | `d858e1c3a75489b5ecae5f67920fb314289a93d9` |
| workflow | `Runtime Release` / `.github/workflows/deploy-dev.yml` |
| hosted build run | [33642907363](https://github.com/alfloop-dev/odayplus/actions/runs/33642907363) |
| build environment | `dev-build` |
| release ID | `odp-d858e1c3a754` |
| manifest schema / status | `2` / `ready` |
| manifest digest | `sha256:3fc63dbb7578e425797b4d8f26f9e944202ac6e1cf84957cc5a0572e85bbc2cc` |
| uploaded manifest artifact | [9851903053](https://github.com/alfloop-dev/odayplus/actions/runs/33642907363/artifacts/9851903053) |
| uploaded image handoff artifact | [9851902442](https://github.com/alfloop-dev/odayplus/actions/runs/33642907363/artifacts/9851902442) |
| uploaded target-absence artifact | [9851903704](https://github.com/alfloop-dev/odayplus/actions/runs/33642907363/artifacts/9851903704) |

The hosted manifest and image handoff bind the same four immutable image refs:

```text
api       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:2a911370f632ade121a672acfe4bd2c13910a22b862a7233c8941e6721320ea4
web       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:cab08fddad3ab3207e6c779bd9e0b973634c977cae11fbf028f07c996c2ecb6f
worker    asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:ec34112db0ae2e2b070ef6a90b5657f01767973bf09141e14a5f1cb55ea2c5d4
scheduler asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:398c02584abd33eb50b7fba0e191112bac367f3c1bb9cf6084eeb90de24507bc
```

The corresponding hosted Cosign signature refs and SBOM attestation refs are recorded
as full 64-hex immutable references in `live-runtime-reconciliation-audit.json` and the
hosted build log; no tag or repeated-nibble value is used.

## 2. Hosted pre-deploy readback

The build's first-release recovery probe, executed with the hosted WIF identity, read
`odayplus-runtime-20260825` / `asia-east1` and found all release targets absent:

| component | resource | observed |
|---|---|---|
| api | Cloud Run service `oday-api` | absent |
| web | Cloud Run service `oday-web` | absent |
| migration | Cloud Run job `oday-migration-r-d858e1c3a754` | absent |
| worker | Cloud Run job `oday-worker-r-d858e1c3a754` | absent |
| scheduler | Cloud Run job `oday-scheduler-r-d858e1c3a754` | absent |

This receipt proves safe first-release eligibility only. It is not a deployment receipt and
does not prove data-platform ordering, job execution, URL smoke, provider-off readback,
or egress behavior after deployment.

## 3. Admission blocker and safety boundary

The GitHub `dev` environment now resolves `ODP_RELEASE_LEASE_PUBLIC_KEY` and
`ODP_RELEASE_LEASE_STATE_URI=gs://odayplus-runtime-20260825-release-leases/leases`.
However, the canonical registry still names candidate
`ebc4fca5c2dd5871275aee39a18406dd67464f04` and its old manifest
digest, while the hosted handoff is candidate
`d858e1c3a75489b5ecae5f67920fb314289a93d9` and digest
`sha256:3fc63dbb7578e425797b4d8f26f9e944202ac6e1cf84957cc5a0572e85bbc2cc`. The worker has no private signing key, and no Supervisor lease was
issued or consumed. A deploy dispatch without that exact signed lease would violate the
Runtime Release admission contract, so no deploy was attempted.

The historical `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` files were not edited,
moved, or deleted. Their pre-existing hashes and the full command transcript are kept in
`live-runtime-reconciliation-audit.json` and `live-readback-transcript.txt`.

## 4. Required next authority action

1. Reconcile the canonical release gate registry to this hosted manifest and exact
   candidate, preserving its staged gate decision rules.
2. Have the Supervisor issue a signed, durable lease bound to manifest digest
   `sha256:3fc63dbb7578e425797b4d8f26f9e944202ac6e1cf84957cc5a0572e85bbc2cc` and
   action `deploy`.
3. Dispatch the existing `Runtime Release` deploy phase with the four exact refs and
   lease; then collect live Cloud Run URL/revision, job execution, authenticated smoke,
   provider-off/16-source, default-deny egress, and exact manifest-binding receipts.

Until those actions complete, this task remains blocked and must not be reported as a
successful live rollout.
