# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001

## 結論（2026-09-21 round，owner Claude2）

本輪先以正常 task workflow 合入最新 `origin/dev`：task branch 的 base merge commit 為
`5414f909b74557b71efbae669850289c2be75c11`，第一 parent 是前一輪 task head
`727cdedb7924a4549c0925b069102a1b8935fd2e`，第二 parent是 `origin/dev`
`cb910b0f45fa340c45c3e965e78ba17f379ffff6`。merge tree 與 `git merge-tree` 預算結果一致
（`22393df79a1395689411d9fc4ba24f29d0686b0d`），原 task history 保持不變。

部署仍維持 **fail-closed / NO-GO**，但阻塞點已經移動：

| 2026-09-04 round 的阻塞 | 2026-09-21 實測現況 |
|---|---|
| registry 綁舊候選 `ebc4fca5` | 已由 ODP-DEV-RELEASE-GATE-RECONCILIATION-004（PR #1352，2026-09-21 併入）重綁到 `39ae43f6fe679f03dd7df459a51835cbd2d54f77` / `sha256:ebe7d305…`，並綁定 hosted build run 35493018607 |
| 候選到 dev 之間有 product 變更，要重 build | `39ae43f6..origin/dev` 共 24 個 commit，**只動 `docs/evidence/`**；不需重 build，本輪未 dispatch 任何 build |
| lease 簽發器「無人承接」 | 簽發器已於 2026-09-17 啟用，並在 2026-09-17T23:26:51Z 對一筆 Human/Ops 核准的 request 做出第一次（拒絕）決定，17 項理由全數記錄在看板 `release_lease_issuance` |
| 上游 task 未 done | 11 個 `depends_on` 全部已 done 並封存 |
| **仍然阻塞** | registry `decision=no-go`；`admission_target=dev` 的 gate-0 / gate-1 / gate-4 皆 `blocked`、receipts 0 筆；新的 lease request 必須由 Human/Ops 以新 approval_id 與 nonce 登記 |

因此本輪沒有執行 Cloud Run deploy、migration/worker/scheduler execution、traffic mutation 或
authenticated smoke，也沒有產生成功部署收據。驗收 3–8 全數未成立。build 成功、候選有效、簽發器可運作，
三者加起來仍不等於 rollout 成功。

採集視窗（UTC）：`2026-09-21T13:45:02Z` 至 `2026-09-21T13:50:56Z`。所有時間都是實際時鐘讀值。
本文件的採集基準是上述 base merge commit；交付本文件的 commit 是它的 scope 受限後代，不在此引用。

## 1. Exact candidate 與 build-once handoff（canonical，非本 task dispatch）

| 項目 | 真實值 |
|---|---|
| canonical candidate SHA | `39ae43f6fe679f03dd7df459a51835cbd2d54f77`（`refs/heads/dev` 的 2026-09-20 tip） |
| current `origin/dev` | `cb910b0f45fa340c45c3e965e78ba17f379ffff6`（candidate 是其祖先；中間 24 commit 只動 `docs/evidence/`） |
| registry 綁定 task | `ODP-DEV-RELEASE-GATE-RECONCILIATION-004`（generated_at 2026-09-20；PR #1352） |
| workflow | `Runtime Release` / `.github/workflows/deploy-dev.yml` |
| image producer run | [35492613570](https://github.com/alfloop-dev/odayplus/actions/runs/35492613570)（build/push/sign/attest 成功，handoff 步驟因未設 `initial_release_recovery` 而失敗） |
| artifact handoff run | [35493018607](https://github.com/alfloop-dev/odayplus/actions/runs/35493018607)（重用上述 digest、`cosign verify` 4 次、發布 6 份 artifact；lease/deploy/watch 三個 job 皆 `skipped`） |
| build environment | `dev-build` |
| release ID / manifest schema / status | `odp-39ae43f6fe67` / `2` / `ready` |
| manifest digest | `sha256:ebe7d305e930471d2e7492a1fffced10dbaa05a710bb5f6573f370e7bfa8ae8a` |
| repo manifest raw sha256 | `10acdfc1460de9a9bc72c816dea6f81d0ec88778c2f2e23c727e05f9bd06d5de`（與 hosted artifact 10600115848 byte-identical） |

四個 immutable image refs（manifest、image handoff artifact 10600485125、job 106031101660 log 三方一致）：

```text
api       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api@sha256:e03442186309eb35d1f153979de21490f1346e0e098f8df02cff2bc3eb1d2842
web       asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-web@sha256:d4202a96e0ab62a8d526ae6ebf74b075dab7ecb6905fa982eee0792b7565b89f
worker    asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-worker@sha256:6935ab7eb49e528c8f24739500e40944a01718933c4012daa2d3bc434df59907
scheduler asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-scheduler@sha256:9d1267ad5a0c1334a812641b6c92dc702046ca09b48a8b5db6c8f61bd6c44373
migration 共用 worker image
```

本輪獨立重驗（不是抄 004 的收據）：

- `gh run download 35493018607` 於 `13:45:02Z`–`13:45:07Z` 重新下載 6 份 artifact，sha256 與
  ODP-DEV-RELEASE-GATE-RECONCILIATION-004 記錄的值逐一相同；manifest artifact 與 repo 內
  `docs/evidence/gates/RELEASE_MANIFEST.json` byte-identical。
- `gh run view --job 106031101660 --log`（`13:46:27Z`–`13:46:30Z`，6039 行）：四個 component digest 各出現 5 次、
  manifest digest 出現 1 次、`cosign verify --certificate-identity-regexp` 4 次。
- manifest 記錄 4 個 64-hex Cosign signature ref 與 4 個 64-hex SBOM attestation ref；無 tag、無重複 nibble、
  無全零 digest。

## 2. Hosted pre-deploy readback

run 35493018607 的 first-release recovery probe（hosted WIF identity，`05:58:54Z`–`06:03:11Z`，
artifact 10600440351，sha256 `f117e1af…80a5`）讀 `odayplus-runtime-20260825` / `asia-east1`：

| component | resource | observed |
|---|---|---|
| api | Cloud Run service `oday-api` | absent |
| web | Cloud Run service `oday-web` | absent |
| migration | Cloud Run job `oday-migration-r-39ae43f6fe67` | absent |
| worker | Cloud Run job `oday-worker-r-39ae43f6fe67` | absent |
| scheduler | Cloud Run job `oday-scheduler-r-39ae43f6fe67` | absent |

另有 2026-09-17 owner Antigravity2 的直接 gcloud readback（記錄於看板 blocker，
`2026-09-17T06:05:38Z`）：Cloud Run services 只有 `oday-mlflow` 與 `oday-staging-mlflow`，jobs 為空，
lease bucket `gs://odayplus-runtime-20260825-release-leases/leases` 無任何物件。
本輪 auto worker 未執行 gcloud。這些都只是 first-release eligibility，**不是部署收據**。

## 3. Source posture and admission boundary

hosted manifest 的 `sources_off_attestation` 記錄 16 個來源全部 `disabled`、`credentials_present=false`、
`provider_mode=disabled`、`egress_posture=default-deny`、`cloud_run_egress=ALL_TRAFFIC`；dev-build 環境收據
解析 `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`。這些是 build-time 政策與 readback 斷言；因為沒有部署，沒有
post-deploy runtime probe。

canonical registry：`candidate_sha=39ae43f6…`、`manifest_digest=sha256:ebe7d305…`、`stage=candidate-built`、
`environment=dev`、`admission_target=dev`、`decision=no-go`（decision_owner Human/Ops，2026-09-20）。

## 4. Lease 簽發器：已啟用、可運作、但拒絕

2026-09-04 round 寫「簽發 lease 在看板上無人承接」。2026-09-17 owner Antigravity2 定位真因是
live config 缺 `release_lease_issuer` 區段（簽發器靜默停用）；操作者當日補上並重啟。本輪唯讀讀取 live config：
`enabled=true`、`workflow=.github/workflows/deploy-dev.yml`、`dispatch_ref=dev`、`ttl_seconds=600`、
secret_reference 與 state_uri 皆與看板記錄一致。

簽發器第一次、也是唯一一次決定（看板 `release_lease_issuance`）：

| 欄位 | 值 |
|---|---|
| recorded_at | `2026-09-17T23:26:51+00:00` |
| approval_id / approved_by | `HUMANOPS-DEV-FIRST-RELEASE-20260917` / Human/Ops |
| candidate / manifest / run | `307e6e72…` / `sha256:903ca109…c0d4` / 35231472494 |
| dispatch_ref_sha（當時 dev tip） | `dc0eb370…` |
| state / admitted | `blocked` / `false`，17 項錯誤 |

17 項錯誤對 **目前候選 `39ae43f6`** 重新評估：

| 類別 | 項次 | 現況 |
|---|---|---|
| A. registry 綁舊候選 | 1–6、9 | **已清除**（PR #1352 把 release、manifest、candidate_rebind 全綁到 39ae43f6） |
| B. candidate 落後 dev tip | 7、11 | **已清除**（視窗結束時 39ae43f6..cb910b0f 只動 docs/evidence/） |
| C. 上游 dependency 未 done | 8 | **已清除**（ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 已 done 封存） |
| D. decision 與 dev gates | 10、12–17 | **仍失敗**：`decision=no-go`；gate-0/1/4 `blocked` 且無 receipt |

因此對 39ae43f6 提出新 request 的結果是可預先決定的（至少 7 項錯誤）。且 request 必須 `approved_by=Human/Ops`
並帶新 `approval_id` 與 `nonce`，worker 無法自行登記——這是人類閘，不是工具缺口。本輪未提出 request、
未消耗 nonce、未偽造或重放任何 lease。

## 5. 三道 dev gate 為何清不掉、誰能清

| gate | owner | 未滿足項 | 路徑 | 承接 |
|---|---|---|---|---|
| gate-0 Code | Codex2 | C1 zero build warnings：`apps/web/src/middleware.ts` 經 sessionStore 匯入 `pg-connection-string` / `pg-pool`，Edge Runtime 警告 | product path（`apps/web`） | 看板無任何 task；004 只提交 scoped proposal 給協調者 |
| gate-1 Contract | Claude | event compatibility policy 要求忽略未知 optional 欄位，但 16 個 payload schema `additionalProperties: false` 且 `shared/domain/events.py` 拒絕額外欄位 | product path（`shared/domain`、`docs/events`） | 看板無任何 task |
| gate-4 Security | Claude | HUMAN-OSS-LEGAL-APPROVAL-001 具名簽署 `license_policy.json` / `license_exemptions.json`；H02 dev-tool CVE 風險接受 | 人類法務 / 安全簽署 | HUMAN-OSS-LEGAL-APPROVAL-001 為 `todo`，`depends_on` XR-EXT-OSS-FINAL-AUDIT-001（`blocked`），而該稽核把 live 量測缺口具名交給本 task |

前兩項依驗收第 10 條不得在本 rollout task 內修 product code；第三項是人類簽署，且與本 task 形成
prose 層級的環（XR 稽核等本 task 的 live 量測，本 task 等 gate-4 等法務簽署等 XR 稽核）。這個環只能由
Human/Ops 切一條邊：要嘛 XR 稽核對尚不存在的環境不要求 live 量測，要嘛 gate-4 對 `admission_target=dev`
不要求 production 等級法務簽署。

## 6. 修正後的 unblock 順序

1. 協調者鑄出 gate-0 C1 修復 task 與 gate-1 event schema 對齊 task（product path，非本 task）；gate owner
   對 39ae43f6 寫 receipt 並改 `passed`。
2. Human/Ops 完成 HUMAN-OSS-LEGAL-APPROVAL-001 簽署與 H02 風險接受（或裁決切斷上述環），gate-4 寫 receipt。
3. Human/Ops 以 reviewed PR 把 `registry.release.decision` 翻為 `go`（registry 無寫入工具）。
4. 簽發前再量一次 `39ae43f6..origin/dev`：若已有 product / build-input commit 併入，對新 tip 重跑唯一
   `Runtime Release` build phase 一次並由 reconciliation task 重綁 registry。
5. Human/Ops 在本 task 登記新的 `release_lease_request`（`approved_by=Human/Ops`、新 approval_id 與 nonce、
   candidate `39ae43f6…`、manifest `sha256:ebe7d305…`、`manifest_run_id=35493018607`、target `dev`、
   action `deploy`），此時 task 須為 `in_progress` 且無 open blocker；已啟用的簽發器自行簽 Ed25519 lease
   並 dispatch 既有 deploy phase。
6. 本 task 收集部署後 Cloud Run URL/revision、migration/worker/scheduler execution、authenticated smoke、
   provider-off/16-source、default-deny egress 與 exact manifest binding 收據（驗收 3–8）。

2026-09-04 的 unblock 清單與更早的 superseded 紀錄保留在 audit JSON 的 `superseded_unblock_requirements`
與 `history`，供稽核比對，未被刪除。

## 7. 終態

驗收 3–8 全數未成立。本 task 不得以 `done` 結案：那會讓「dev 從未部署」隨 task 進入 archive 而在看板上
消失。本輪終態為 `blocked`，以 open blocker（waiting_for Human/Ops）承載第 5 節的人類閘與尚無承接 task 的
兩項 product 修復。

歷史 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 七份收據在 base merge 後重算 sha256，與 2026-09-04 audit
記錄完全相符；未被編輯、搬移或刪除。本 evidence 僅明確標示其部署宣稱已被 live reconciliation 推翻。
