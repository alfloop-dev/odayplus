# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001

## 結論

本輪先以正常 task workflow 合入最新 `origin/dev`。task branch 的 base merge
commit 為 `6f3624ac9a1e38042eba84c6e0ce8b5b513a84ce`，其第二 parent 是
`origin/dev` `1edb2f834cbf38ccd489cd999802098076e891b7`；原 task history 保持不變。

由於前一輪 candidate `d858e1c3a754` 到目前 base 有 product/build-input 變更，沒有重用
舊 digest，而是重新執行唯一 `Runtime Release` 的 hosted `build` phase。run
[33844319992](https://github.com/alfloop-dev/odayplus/actions/runs/33844319992)
成功完成 exact-SHA checkout、environment binding、secret/SAST/SBOM/dependency gates、
GCP WIF、first-release target absence readback、四個 image 的 build/push/Cosign/SBOM
attest，以及 build-once manifest handoff。

部署仍維持 **fail-closed / NO-GO**：canonical release gate registry 尚綁定舊候選
`ebc4fca5c2dd5871275aee39a18406dd67464f04` 且為 `no-go`，Supervisor 尚未對本次
manifest 發出 signed lease。故本輪沒有執行 Cloud Run deploy、migration/worker/
scheduler execution、traffic mutation 或 authenticated smoke，也沒有產生成功部署
收據。build 成功不等於 live rollout 成功。

## 1. Exact candidate 與 build-once handoff

| 項目 | 真實值 |
|---|---|
| current `origin/dev` / release SHA | `1edb2f834cbf38ccd489cd999802098076e891b7` |
| task base merge | `6f3624ac9a1e38042eba84c6e0ce8b5b513a84ce` |
| workflow | `Runtime Release` / `.github/workflows/deploy-dev.yml` |
| hosted build run | [33844319992](https://github.com/alfloop-dev/odayplus/actions/runs/33844319992) |
| build environment | `dev-build` |
| release ID | `odp-1edb2f834cbf` |
| manifest schema / status | `2` / `ready` |
| manifest digest | `sha256:c9ff71c7557c4009487cf6e093280f47369db2fdd49a17ac8b9e1aa472e8c2c7` |
| uploaded manifest artifact | [9926308373](https://github.com/alfloop-dev/odayplus/actions/runs/33844319992/artifacts/9926308373) |
| uploaded image handoff artifact | [9926308131](https://github.com/alfloop-dev/odayplus/actions/runs/33844319992/artifacts/9926308131) |
| uploaded target-absence artifact | [9926308639](https://github.com/alfloop-dev/odayplus/actions/runs/33844319992/artifacts/9926308639) |

The hosted manifest and image handoff bind these four immutable image refs:

```text
api       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:520c3c0cb49f5b46b8c21c9e3e7e3284c4e3b7576a7b42ea1a8a7e5504d5e7a2
web       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:72bd3c5fc86a2e341af3e4faccf869edd53bba37fc9f1751ef044892ba113de4
worker    asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:68f25db411228bb5fa2d21ad23469bc1045bfe7b19054ffa4e66eda349120538
scheduler asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:82b0bf9be56a301b3c27bac5a22825b2d1cf65a3d1309b3f0ea7250a3ea88a76
```

The manifest records four full 64-hex Cosign signature refs and four full 64-hex SBOM
attestation refs. Hosted logs show each registry push digest and successful Cosign SCT/
certificate verification; no tag, repeated-nibble, or all-zero digest is used. The downloaded
manifest recomputes to the declared `manifest_digest`, and the repository manifest/admission
validators return no errors.

## 2. Hosted pre-deploy readback

The build's first-release recovery probe, executed with the hosted WIF identity, read
`odayplus-runtime-20260825` / `asia-east1` and found every release target absent:

| component | resource | observed |
|---|---|---|
| api | Cloud Run service `oday-api` | absent |
| web | Cloud Run service `oday-web` | absent |
| migration | Cloud Run job `oday-migration-r-1edb2f834cbf` | absent |
| worker | Cloud Run job `oday-worker-r-1edb2f834cbf` | absent |
| scheduler | Cloud Run job `oday-scheduler-r-1edb2f834cbf` | absent |

This is safe first-release eligibility only. It is not a deployment receipt and does not prove
data-platform ordering, job execution, URL smoke, provider-off readback, or egress behavior
after deployment.

## 3. Source posture and admission boundary

The hosted manifest binds `external_sources_expected_enabled=[]` and records all 16 source
entries disabled, zero provider credentials, provider mode disabled, and default-deny egress.
The build environment also resolved `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`. These are build-time
policy/readback assertions; no post-deploy runtime probe exists because deployment was not
authorized.

The canonical registry remains bound to candidate
`ebc4fca5c2dd5871275aee39a18406dd67464f04` and manifest digest
`sha256:fa2f52220951dc89c56b41b7f0fd61280ce00a028709d2124ceefcdc55f24de9`, with
`stage=candidate-built`, `environment=dev`, `admission_target=dev`, and `decision=no-go`.
The worker has no private signing key and no Supervisor lease was issued or consumed. A deploy
dispatch without the exact signed lease would violate the Runtime Release admission contract,
so no deploy was attempted. No DNS, custom domain, or certificate mutation was made.

The historical `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` files were not edited, moved, or
deleted. Their hashes remain matched after base composition and are recorded in the audit; this
current evidence explicitly supersedes their false deployment claims without rewriting them.

## 4. 本輪 build artifact 不是最終候選

承接 gate registry reconciliation 的 board task 為 `ODP-DEV-STAGED-GATE-RECONCILIATION-001`，
其目前狀態為 HARD HOLD：明文不得對 `1edb2f83` 產生或宣稱最終 candidate gate receipt，必須等
PR [#1183](https://github.com/alfloop-dev/odayplus/pull/1183)
(`task/ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001`) 合併後，改用當時最新的 `origin/dev` 才能
bind exact artifact。本輪查核時 #1183 仍為 `OPEN`、未合併。

因此本輪 run 33844319992 產出的 manifest `sha256:c9ff71c7…` 與四個 image digest **確定不會是最終
admitted release**，屬預先被取代的 artifact。基於此，本輪刻意 **不再重跑第三次 build**：對
`1edb2f83` 再 build 一次只會消耗 hosted 資源而不可能成為可部署候選。既有 build evidence 原樣保留、
未重做，也未被改寫。

## 5. 修正後的 unblock 順序

先前 audit 的 `unblock_requirements` 第一項要求把 registry reconcile 到 `1edb2f83`。該項在得知
上述 HARD HOLD 後已確認為錯誤方向，現已更正並保留 `superseded_unblock_requirements` 以留下可稽核
的更正紀錄。正確順序為：

1. 合併 PR #1183，解除 `ODP-DEV-STAGED-GATE-RECONCILIATION-001` 的 HARD HOLD。
2. 取當時最新 `origin/dev` head 作為 deploy candidate；**不得** reconcile 到 `1edb2f83`，
   run 33844319992 的 artifact 不得沿用。
3. 對該新 exact SHA 重跑唯一 `Runtime Release` build phase 一次，產生新的 build-once manifest。
4. 由 `ODP-DEV-STAGED-GATE-RECONCILIATION-001` 依既有 staged dev admission 規則，把 registry 從
   `ebc4fca5c2dd…` / `no-go` reconcile 到新 candidate 與其 manifest digest。
5. 由 Supervisor issuer 對 target `dev`、action `deploy`、該新 manifest digest 簽發並持久記錄
   Ed25519 lease。
6. 以新的 exact image refs 與該 lease dispatch 既有 `Runtime Release` deploy phase。
7. 收集部署後 Cloud Run URL/revision、migration/worker/scheduler execution、authenticated smoke、
   provider-off/16-source、default-deny egress 與 exact manifest binding 收據。

## 6. 授權缺口目前無人承接

第 5 步（簽發 lease）在看板上**沒有任何 task 承接**：worker 無私鑰，而
`ODP-DEV-STAGED-GATE-RECONCILIATION-001` 的驗收第 8 條明寫「本 task 不簽 lease 不 dispatch
deploy」。本 task 因此以 open blocker 承載這個 P0 授權缺口，終態為 `blocked`。

驗收 3-8（live readback、jobs one-shot、authenticated smoke、contract、provider-off、
default-deny egress 等部署後行為）**全數未成立**。build 成功不等於 rollout 成功，evidence PR 也
不能取代部署完成。本 task 不得以 `done` 結案：那會讓「dev 從未部署」隨 task 進入 archive 而在看板
上消失。

歷史 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 收據仍未被編輯、搬移或刪除，其 sha256 與 audit
完全相符；本 evidence 僅明確標示其部署宣稱已被 live reconciliation 推翻。
