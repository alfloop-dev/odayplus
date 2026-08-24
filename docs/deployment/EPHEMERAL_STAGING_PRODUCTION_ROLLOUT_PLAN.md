---
doc_id: ODP-DEPLOY-EPHEMERAL-STAGING-PROD-ROLLOUT-PLAN
title: ODay Plus 全系統部署與短生命週期 Staging 規劃
status: proposed
date: 2026-08-24
language: zh-TW
owner: Platform/Ops
reviewers:
  - Product Owner
  - Architecture Owner
  - SRE Owner
  - Security Owner
---

# ODay Plus 全系統部署與短生命週期 Staging 規劃

## 1. 決策摘要

本規劃採用以下唯一部署模型：

1. `dev` 是常駐整合環境，用於持續整合、資料契約與應用整合驗證。
2. `staging` 是每次 release 才建立的短生命週期環境；成功後銷毀，失敗時只保留到除錯期限。
3. `prod` 是常駐環境，使用 blue-green 發布：新版本先以 0% 公開流量驗證，再一次切至 100%。
4. 同一 release 只 build 一次；dev、staging、prod 必須部署相同 commit SHA、相同 image digest、相同 SBOM 與簽章。
5. 延伸現有 `Runtime Release` 工作流程，不建立第二套 deploy、proof 或 release gate 管線。
6. 資料平台先部署，ODay Plus 應用後部署；兩者透過已版本化的資料契約與 snapshot manifest 銜接。
7. 所有第三方資料來源預設保持關閉。沒有逐來源許可、啟用收據與憑證時，部署可以完成，但不得對外抓資料。
8. Supervisor 負責 release task DAG、worker lease、依賴、健康與發布授權；Auto Worker 只執行明確、可驗收、可重跑的實作任務。
9. 正式切換流量、注入正式 secrets、啟用第三方資料來源與法規判定，必須由人類負責人完成或核准。

這不是把 staging 改名成「prod 0%」。Staging 必須隔離資料庫、bucket、tenant、IAM 與排程，才能安全演練 migration、worker、scheduler、backup 與 rollback；prod 0% revision 只負責確認正式環境的 image、secret binding、IAM 與服務啟動是否正確。

## 2. 目標與非目標

### 2.1 目標

- 把資料平台與 ODay Plus 應用部署成可重現、可稽核、可回滾的完整系統。
- 在不長期負擔 staging 成本的前提下，保留 production-like 的 release rehearsal。
- 消除「同一 release 在不同環境重新 build」造成的 artifact 漂移。
- 消除目前 release admission 與 Gate 0–6 之間的循環依賴。
- 讓 Supervisor 能最大化平行派工，同時避免多個 worker 修改同一控制檔或重複建立部署機制。
- 讓第三方 connector 可以先完成部署與測試，但在許可完成前保持 runtime disabled 與 default-deny egress。

### 2.2 非目標

- 本規劃不授權任何第三方資料來源上線抓取。
- 本規劃不替代逐來源的授權、法規、條款與更新頻率決策。
- 本規劃不建立第二套 Supervisor、scheduler、proof collector 或 deployment workflow。
- 本規劃不要求每次 release 建立全新的 GCP project、VPC、GKE cluster 或 Cloud SQL instance。
- 本規劃不以 down migration 作為主要 rollback 手段；資料庫變更必須使用 expand/contract。
- 本規劃不把 dev 測試結果視為 staging 或 production 證據。

## 3. 目前基線與已確認缺口

以下是 2026-08-24 以 `origin/dev` 與 GitHub environment 現況檢查得到的基線；正式實作前仍須由任務重新讀取遠端狀態，不可硬編碼這些 SHA。

| 項目 | 目前狀態 | 影響 |
|---|---|---|
| ODay Plus `origin/dev` | `0dc5cebc90cf3a55c0e2805459bcdda19f9c4e36` | 最新功能已在 dev，但尚未完整發布。 |
| ODay Plus `main` | `574dde52b56992b5088aedc74332e2e90fb40b44` | main 明顯落後 dev。 |
| dev → main promotion | 最近一次因 `product-release-gate` 失敗 | 不能把 merge 等同 production deployment。 |
| `Runtime Release` | 只接受 `dev`、`staging` | 尚無 production blue-green 路徑。 |
| Artifact 行為 | 工作流程在各環境 build/push/deploy | 無法保證 staging 與 prod 是同一 image digest。 |
| Release admission | `task_id`、`release_lease` 只檢查字串格式 | 任何有 workflow write 權限的人都可自造 lease；目前不是授權控制。 |
| Gate registry | candidate 舊、decision 為 `no-go`、Gate 0–6 無收據 | 現況不能通過 release admission。 |
| Gate 時序 | dev/staging deploy 前要求七道 gate 全部通過 | staging 才能產生的證據反過來阻擋 staging，形成循環依賴。 |
| GitHub `staging` environment | 已建立但未配置 vars/secrets | staging workflow 無法實際部署。 |
| GitHub `production` environment | 尚未建立 | 沒有正式環境保護、變數與 approval boundary。 |
| 舊 External Proof Follow-up | 只存在舊 main，最新 dev 已移除 | 不修舊 workflow；dev promotion 後自然退役。 |
| Data platform EMGI workflow | 尚未實際執行 | 完整資料平台 runtime 還沒落地。 |
| 第三方來源 | 16 個來源皆 disabled、approval receipts 空、無 provider credentials | 可部署 connector，但不得啟用抓取。 |

### 3.1 必須維持的既有能力

- 現有 secret scan、SAST、SBOM、Cosign、migration、worker、scheduler、live E2E 與 receipt 上傳。
- 現有 `deploy_cloud_run_waji.sh` 與 validation script 中已驗證的 runtime 行為。
- 現有 release gate registry 的 ancestry 與 evidence-only commit 概念，但需修正 gate 階段與 admission 語意。
- 現有 Supervisor 的 task、dependency、worker、approval 與 archive 管理。
- 現有資料平台的 provider-off、default-deny public egress 與 one-shot verification 隔離。

## 4. 目標架構

### 4.1 三個環境的責任

| 環境 | 生命週期 | 主要用途 | 資料與外部連線 | 晉級條件 |
|---|---|---|---|---|
| `dev` | 常駐 | CI 後整合、契約、應用與資料平台持續驗證 | 非正式 snapshot；第三方來源關閉 | Build Gate 與 dev integration 通過。 |
| `staging` | 每個 release 建立，成功後銷毀 | migration、E2E、worker、scheduler、backup、rollback 完整演練 | masked snapshot；隔離 DB/bucket/tenant/IAM；第三方來源關閉 | Staging Gate 全部通過並取得 Human GO。 |
| `prod` | 常駐 | 對外服務與受治理的內部工作 | 正式資料；第三方來源仍依逐來源開關 | 0% revision 驗證通過後 blue-green 切換。 |

### 4.2 長期基礎設施與短生命週期資源

為避免每次 release 都重建昂貴底層資源，staging 分成兩層：

**長期共用但權限隔離的底層：**

- non-production GCP project、VPC、Artifact Registry 與 Workload Identity Federation。
- non-production GKE cluster 與 Cloud SQL staging instance（若現況已有可重用者）。
- Terraform state backend、KMS signing key、log/metric backend。

**每個 release 短暫建立的資源：**

- 具 release ID 的 GKE namespace、Cloud Run services/revisions/jobs。
- 隔離 database 或 schema、bucket prefix、tenant、service accounts 與 IAM binding。
- 預設 paused 的 Cloud Scheduler triggers。
- masked snapshot、release manifest、部署與測試 evidence 路徑。
- TTL label 與自動 cleanup metadata。

若底層尚不存在，第一次 bootstrap 是獨立、一次性的 platform task；不能假裝是每次 release 的 ephemeral staging 工作。

### 4.3 唯一發布流程

```text
merge 到 dev
  │
  ├─ Build Gate
  │   ├─ lint / unit / contract / security
  │   ├─ build 資料平台與 ODay Plus images
  │   └─ 產生 manifest、SBOM、簽章與 immutable digests
  │
  ├─ 部署 dev（同一組 digests）
  │   └─ dev integration receipts
  │
  ├─ 建立 ephemeral staging
  │   ├─ 先部署資料平台
  │   ├─ 再部署 ODay Plus migration / API / Web / jobs
  │   ├─ 執行 E2E、backup、restore、rollback rehearsal
  │   └─ Staging Gate receipts
  │
  ├─ Human GO
  │
  ├─ 部署 prod green（0% 公開流量）
  │   ├─ 正式 IAM / secret binding / startup smoke
  │   ├─ 暫停 scheduler，驗證 one-shot jobs
  │   └─ 失敗則刪除 green，不影響 blue
  │
  ├─ blue → green 100% 切換
  │   ├─ API
  │   ├─ Web
  │   ├─ worker / scheduler image target
  │   └─ 恢復內部排程
  │
  ├─ watch window
  │   ├─ 成功：完成 release 並銷毀 staging
  │   └─ 失敗：流量與 jobs 回切 blue，staging 暫留除錯
  │
  └─ contract migration 延後到相容期結束後的獨立 release
```

`prod green 0%` 不是 staging。它只驗證 production 專屬 binding 與啟動；具有破壞性的 migration rehearsal、restore drill、scheduler 測試仍只能在隔離 staging 執行。

## 5. Artifact 與 Release Manifest

### 5.1 Build once

同一 release 的每個 component 只 build 一次，並以 digest 作為唯一部署身份：

- ODay Plus API image digest。
- ODay Plus Web image digest。
- migration、worker、scheduler job image digest；若共用 image，manifest 必須明確記錄共用關係。
- data platform / EMGI runtime image digest。
- database migration set digest。
- data contract、source policy、provider enablement policy digest。
- SBOM、SLSA/provenance（若已有）、Cosign signature reference。

環境名稱只能是 deployment metadata，不能進入 build 內容。環境差異由受保護的 vars、secret references、runtime flags 與 IAM 提供。

### 5.2 Release manifest 最小欄位

```json
{
  "schema_version": 1,
  "release_id": "odp-YYYYMMDD-NNN",
  "candidate_sha": "40-char-sha",
  "components": {
    "api": {"image": "...@sha256:..."},
    "web": {"image": "...@sha256:..."},
    "data_platform": {"image": "...@sha256:..."}
  },
  "migration_digest": "sha256:...",
  "data_contract_digest": "sha256:...",
  "source_policy_digest": "sha256:...",
  "external_sources_expected_enabled": [],
  "sbom_refs": [],
  "signature_refs": [],
  "created_at": "RFC3339",
  "created_by_workflow": "workflow/run reference"
}
```

manifest 一旦進入 staging 不得改寫。若任何 image、migration、contract 或 policy 改變，必須建立新的 release ID，重新走 staging；不得只補一個 production image。

## 6. Gate 狀態機與 Admission 修正

### 6.1 拆開「能部署到哪裡」的 gate

目前所有環境都要求 Gate 0–6 全過會造成循環依賴。新狀態機應改為：

| 階段 | 必要證據 | 允許動作 |
|---|---|---|
| `candidate-built` | exact SHA、digests、unit/contract/security、SBOM、signature | 部署 dev。 |
| `dev-verified` | dev health、integration、資料契約、provider-off readback | 建立並部署 staging。 |
| `staging-verified` | migration、E2E、worker、scheduler、backup/restore、rollback、observability | 請求人類 production approval。 |
| `prod-admitted` | staging receipts、Human GO、backup checkpoint、rollback owner、有效 lease | 建立 prod 0% green。 |
| `prod-switched` | 0% smoke、正式 IAM/secret binding、job one-shot | 切換 100% 並開始 watch。 |
| `release-complete` | watch window、SLO、audit/evidence manifest | 銷毀 staging 並封存 release。 |

Gate 0–6 可以保留為證據分類，但 registry 必須額外記錄 `stage`、`environment` 與 `admission_target`；不能再把七道分類是否全部完成，直接等同每一階段的 admission。

### 6.2 Supervisor release lease

目前 lease 只做 shape check，必須替換成真正的單一授權機制：

1. Supervisor 只在 task dependency 與對應 gate 已滿足時建立 lease。
2. lease 至少綁定：`lease_id`、`task_id`、`release_id`、`candidate_sha`、manifest digest、target environment、允許 action、issued/expiry、nonce。
3. lease 由 KMS 或等價不可匯出的 key 簽署；workflow 只取得驗章能力。
4. durable Supervisor state 以 compare-and-set 將 lease 從 `issued` 改成 `consumed`，防止 replay。
5. workflow 必須同時驗證簽章、期限、target、SHA、manifest digest 與尚未使用狀態。
6. GitHub environment approval 與 concurrency 仍保留：前者是人類 production gate，後者是同環境互斥；兩者不能冒充 Supervisor lease。
7. lease 驗證失敗必須 fail closed，並輸出不含 secret 的 receipt。

只新增一個 authoritative lease verifier，取代 `check_runtime_admission.py` 的 shape-only 語意；不保留「舊 admission + 新 admission」兩條路徑。

## 7. Ephemeral Staging 詳細生命週期

### 7.1 建立

輸入只有 release manifest、Supervisor lease 與 staging protected environment 設定。建立程序必須：

1. 驗證 lease 與 immutable digests。
2. 產生唯一 `release_id` namespace/suffix，避免與其他 release 衝突。
3. 建立隔離 DB/schema、bucket prefix、tenant、service account、IAM 與 log labels。
4. 從核准的 masked snapshot 還原資料；禁止直接掛 production writable DB。
5. 部署 data platform，所有 external source flags 保持 false，public egress 保持 default deny。
6. 部署 ODay Plus migration、API、Web、worker 與 scheduler；scheduler trigger 起始為 paused。
7. 寫入 `created_at`、`expires_at`、owner task、release SHA 與 digests。

### 7.2 驗證

必跑項目：

- DB expand migration 與新舊 app schema compatibility。
- data platform snapshot materialization 與契約 readback。
- API/Web authenticated smoke 與主要 E2E。
- worker、scheduler one-shot execution；確認 idempotency、retry、dead-letter/quarantine。
- backup checkpoint、restore drill、應用與資料指標 readback。
- rollback rehearsal：service/image/snapshot pointer 回到前一版本。
- security、IAM、tenant isolation、secret reference 與 audit log。
- 16 個第三方來源的 disabled readback、零 provider credentials、default-deny egress。

### 7.3 清理與 TTL

- 成功 release：watch window 結束後自動清理 staging。
- staging 驗證失敗：保留供除錯，但預設 TTL 不超過 24 小時；延長必須有 owner 與原因。
- cleanup 必須依 release labels 精確刪除，不得使用寬泛 project/namespace wildcard。
- cleanup 失敗要建立一個 remediation task；不得因此把 release 誤標完成。
- 每小時執行 orphan scanner，針對超過 TTL 的 staging 資源告警並安全回收。

## 8. Production Blue-Green 詳細步驟

### 8.1 前置條件

- `staging-verified` 全部通過且 receipts 綁定同一 manifest digest。
- GitHub `production` environment 已建立，required reviewer、WIF、vars 與 secret references 完整。
- production backup checkpoint 已完成。
- rollback owner 與 watch owner 在線。
- external sources expected enabled 仍為空；任何非空值要走獨立逐來源 activation release。

### 8.2 部署 green，維持 0% 公開流量

1. 先部署 backward-compatible data platform green 與 versioned snapshot/contract。
2. 執行 expand migration，不執行破壞舊版的 contract migration。
3. 建立 ODay Plus API/Web green revisions，不分配公開流量；以受保護 tag URL 執行 smoke。
4. scheduler 維持 paused；worker/scheduler 以 green digest 做一次性 dry-run 或受控執行。
5. 驗證正式 secret binding、IAM、database、bucket、tenant、logs、metrics、traces 與 audit event。

任一步驟失敗，就刪除或停用 green，production blue 維持 100%，不執行 traffic switch。

### 8.3 100% 切換

本系統第一階段不採 10%/90% 長時間混跑，避免新舊 API、job 與 schema 同時寫入造成不一致：

1. 確認 API contract 對舊 Web backward compatible。
2. API 從 blue 100% 原子切到 green 100%，立即跑 authenticated smoke。
3. Web 從 blue 100% 原子切到 green 100%，立即跑 E2E。
4. 將 worker、scheduler job target 更新為 green digest。
5. 先 one-shot 驗證，再恢復內部 scheduler triggers。
6. 開始 watch window；blue revisions 與舊 job definitions 暫不刪除。

### 8.4 回滾

觸發條件包括但不限於：錯誤率、P95、auth failure、資料品質、job failure、queue lag、audit 缺失或 operator 判定。

回滾順序：

1. 暫停 scheduler triggers。
2. Web 與 API 流量切回 blue 100%。
3. worker、scheduler target 回到 blue digest。
4. data platform service selector與 snapshot pointer 回到上一個已核准版本。
5. 驗證舊版仍可讀取 expand migration 後 schema。
6. 執行 rollback smoke 與資料一致性檢查。
7. 保留 staging 與失敗 green evidence，建立 incident/remediation task。

不在緊急回滾中執行 destructive down migration。contract migration 必須延後到相容觀察期結束後的獨立任務。

## 9. 第三方資料與 OSS 上線原則

### 9.1 Connector 可部署，但來源預設關閉

- connector code、schema、mapping、tests 與 runtime image 可以進入 dev/staging/prod。
- 每個環境的 `expected_enabled_sources` 預設為空陣列。
- 未取得逐來源 approval receipt 時，source flag 必須為 false，credentials 必須不存在，public egress 必須被拒絕。
- one-shot provider verification 必須使用隔離 namespace 與短期 egress lease；驗證後立即撤銷。
- 一般 staging/prod release 不得隱含取得 provider egress 權限。

### 9.2 逐來源 activation 是後續獨立流程

每個資料來源必須依資料種類決定合理更新頻率與風險控制，至少包含：

- 授權依據、使用條款、資料類型、地域與保存限制。
- 預設更新 interval、可調整範圍、人工 pause/disable 開關。
- rate limit、backoff、增量抓取、資料 freshness 與失敗告警。
- credentials owner、rotation、egress allowlist 與 kill switch。
- activation/disable receipt 與 audit trail。

人可以關閉自動更新或在核准範圍內調整頻率；系統不以「人工定期上傳」取代正常排程設計。

### 9.3 OSS

- build/release gate 必須驗證 lockfile、SBOM、license policy、NOTICE 與 critical vulnerability。
- 測試需要抓取的 package/model/artifact 必須進入內部 registry 或 immutable cache。
- production runtime 不應持續從公開 package/model registry 抓取相同依賴。
- 對外抓取 provider 資料與下載 OSS artifact 是兩種政策，但兩者都必須以明確 egress allowlist 管理。

## 10. 單一部署機制的改造原則

### 10.1 保留與改造

- 將現有 `.github/workflows/deploy-dev.yml` 的 `Runtime Release` 提升為唯一 release orchestration entrypoint；檔名可在同一任務中重新命名，但不得同時保留兩個可部署 workflow。
- 抽出 build 與 deploy-by-digest；deploy job 不再 build。
- 增加 `production` target 與 blue-green actions。
- staging create、verify、destroy 是同一 state machine 的階段，不另建平行的 staging proof workflow。
- 原有 validation scripts 盡量作為 receipts producer 重用；只有語意衝突時才替換。

### 10.2 必須移除或退役

- shape-only release lease admission。
- 每個環境重新 build image 的路徑。
- 任何可繞過唯一 Runtime Release 直接部署相同 runtime 的 workflow/script entrypoint。
- dev promotion 後，舊 main 上已不存在於 dev 的 External Proof Follow-up 自然退役；不為它補第二套 proof。
- 新機制驗收後，刪除已無 caller、只服務舊流程的 dead code、vars、docs 與 tests。

退役必須用 repository search、workflow references、runtime unit/cron 與 GitHub Actions usage 證明沒有 caller；不能只因檔名像舊版就刪除。

## 11. Supervisor 與 Auto Worker 的責任邊界

### 11.1 Supervisor 負責

- 建立並維護下節的 task DAG、owner、dependency 與互斥 scope。
- 根據 worker 能力、模型適配、quota、stall 與可用 slot 派工。
- 發放短期 helper claim，讓閒置 worker 接手可分割且無衝突的工作。
- 偵測 stalled/blocked worker，重新派工或建立 remediation task。
- 只有 gate 與 dependency 真實通過時才簽發 release lease。
- 收集 task、PR、CI、deployment 與 evidence receipts，完成 archive。

Supervisor 不應直接修改產品程式碼，也不應把「task 已指派」當成「release 已授權」。

### 11.2 Auto Worker 負責

- 在自己的 task worktree 完成限定 scope 的程式、IaC、workflow、測試與文件。
- 產生 commit、PR、CI 與驗收證據。
- 遇到 credentials、人類 approval、法規決策或 production GO 時回報 blocked reason，不自行猜測或繞過。
- 不得自行建立第二套 deployment/gate/scheduler 機制。
- 不得在未取得 release lease 的情況下 dispatch production workflow。

### 11.3 人類負責

- 提供或核准 GCP/GitHub environment、WIF、IAM、secret references 與 billing/quota。
- 核准 production GO、正式 rollback decision 與 incident response。
- 完成第三方來源逐項授權與法規判定。
- 對 destructive migration、資料不可逆變更與正式來源 activation 作最後核准。

## 12. 建議派工 DAG

規劃文件本身不需要派給 Supervisor／Auto Worker；應先由架構與 Ops owner 接受本規劃。接受後，再一次把以下實作任務上板，讓 Supervisor 依 DAG 派工。

### Wave 0：基線與介面凍結

| Task ID | 工作 | 主要產出 | 依賴 |
|---|---|---|---|
| `ODP-RELEASE-MANIFEST-GATES-001` | 定義 immutable release manifest 與分階段 gate registry | schema、validator、migration、tests | 無 |
| `ODP-RELEASE-ADMISSION-AUTHORITY-001` | 以 signed durable lease 取代 shape-only admission | issuer/verifier/CAS state/receipts/tests | manifest schema |
| `ODP-DEPLOY-DEAD-CODE-AUDIT-001` | 盤點 workflow、scripts、vars、proof 與 caller | 保留/替換/刪除清單，不先刪 code | 無 |

Wave 0 中，manifest 與 admission 會接觸共同介面，必須明確指定單一整合 owner；dead-code audit 可平行執行但只提交報告。

### Wave 1：可平行的底層實作

| Task ID | 工作 | 主要產出 | 依賴/互斥 |
|---|---|---|---|
| `ODP-EPHEMERAL-STAGING-IAC-001` | staging create/destroy/TTL、隔離 DB/bucket/tenant/IAM | Terraform/modules、cleanup、orphan scanner、tests | 依賴 manifest；避免修改 Runtime Release workflow |
| `DPF-EMGI-LIVE-ROLLOUT-001` | data platform exact-digest publish、EMGI bootstrap、sources-off deploy | image、GKE runtime、receipts、rollback | 跨 data platform repo，可獨立平行 |
| `ODP-RELEASE-EVIDENCE-RECEIPTS-001` | 統一 dev/staging/prod receipts 與 redaction | receipt schema、artifact allowlist、tests | 依賴 manifest/gate schema |
| `ODP-PROD-BLUEGREEN-PRIMITIVES-001` | Cloud Run/K8s traffic、job target、snapshot pointer 切換與回滾 primitives | 可重跑 scripts、dry-run、tests | 不直接修改 workflow entrypoint |

### Wave 2：唯一管線整合

| Task ID | 工作 | 主要產出 | 依賴 |
|---|---|---|---|
| `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` | 把既有 Runtime Release 整合為 build once → dev → staging → prod state machine | 唯一 workflow、environment protection、concurrency、tests | Wave 0、Wave 1 primitives |
| `ODP-DEPLOY-DEAD-CODE-REMOVAL-001` | 刪除已被唯一管線取代且證明無 caller 的 code/config/docs | deletion PR、negative search、regression tests | single-path 完成、audit 清單 |

`ODP-RUNTIME-RELEASE-SINGLE-PATH-001` 必須只有一個主要 owner。其他 worker 可以提供 tests 或 reviewer sidecar，但不能同時改同一 workflow，否則會造成 merge conflict 與兩套狀態機。

### Wave 3：環境落地

| Task ID | 工作 | 主要產出 | 依賴 |
|---|---|---|---|
| `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` | 補齊 staging/production environment、WIF、vars、secret refs、required reviewers | redacted config receipts | 需要人類 credentials/IAM authority |
| `ODP-DEV-ROLLOUT-001` | 同一 digests 部署資料平台與 ODay Plus dev | dev receipts | single-path、EMGI、environment bootstrap |
| `ODP-EPHEMERAL-STAGING-ROLLOUT-001` | 建立 staging、完整 rehearsal、失敗保留/成功清理 | staging receipts | dev-verified、staging IaC |

### Wave 4：正式發布與收尾

| Task ID | 工作 | 主要產出 | 依賴 |
|---|---|---|---|
| `ODP-PROD-BLUEGREEN-ROLLOUT-001` | prod 0% smoke、100% switch、job/scheduler 切換 | production receipts、Human GO | staging-verified、production env |
| `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` | watch、rollback decision、staging cleanup、release archive | SLO/audit/cleanup receipts | prod-switched |

### 12.1 最大化平行派工規則

- 只有 DAG dependency 已完成且檔案 ownership 不重疊的任務可以平行。
- 資料平台 repo、staging IaC、receipt schema、blue-green primitives 可在各自界面凍結後平行。
- 唯一 workflow、gate registry migration 與 lease contract 各自只允許一個整合 owner。
- helper claim 只接手可切割的 tests、docs、evidence 或獨立 module，不接手另一 owner 正在修改的 control-plane file。
- underutilization sidecar 只能產生可獨立驗收的支援任務；不得複製 canonical task。
- chair scheduler 依 dependency、能力、quota 與互斥 scope 決定先後，不以 token 消耗量當完成工作量。

## 13. 各任務共同驗收標準

每個實作 task 至少需要：

- 從當時最新 `origin/dev` 或核准 base 建立乾淨 task worktree。
- 單一 task scope、明確 source docs、acceptance criteria 與 owner。
- unit/contract/integration tests；涉及 deployment 時另需 dry-run 與 failure-path tests。
- PR 使用中文說明：問題、根因、設計、未做事項、測試、風險與 rollback。
- CI 綁定精確 head SHA；不得以舊 run 或其他 commit 的成功結果代替。
- receipt 不含 secret value，只記錄 secret reference、resource identity、digest、結果與時間。
- 合併後確認 dev tip、runtime rollout 與實際 process/image digest一致。
- 只有部署與 watch 真正完成後，Supervisor 才能把 rollout task archive 為 done。

## 14. 全系統 Definition of Done

只有以下條件全部成立，才能回答「整個系統已部署完成」：

1. data platform 與 ODay Plus 所有 production components 都以 release manifest 的 exact digests 執行。
2. dev、ephemeral staging 與 prod receipts 可追溯至同一 candidate SHA 與 manifest digest。
3. staging 完成 migration、E2E、worker、scheduler、backup/restore 與 rollback rehearsal。
4. prod green 0% smoke、blue-green 100% switch 與 watch window 通過。
5. worker/scheduler 已指向 green digest，沒有舊版排程持續執行。
6. production monitoring、logs、traces、audit、backup 與 rollback owner 有效。
7. 16 個第三方來源仍為 disabled、approval receipts 空、credentials 不存在、public egress default deny。
8. 不存在第二套可部署同一 runtime 的 workflow；舊 proof/deploy code 已依 caller audit 退役。
9. release lease 已綁定 durable Supervisor state 且不可 replay。
10. ephemeral staging 已成功清理，或失敗保留具有 owner 與未逾期 TTL。
11. release evidence manifest 已封存，Supervisor tasks/PR/CI/deployment/watch receipts 全部完整。

## 15. 失敗與阻塞處理

| 狀況 | 處理方式 | 是否可繼續 release |
|---|---|---|
| Auto Worker stalled | Supervisor 確認 process activity、log、lease；安全終止後從 checkpoint 重派 | 視 task dependency；不得假裝完成 |
| Auto Worker blocked by credentials/approval | 轉為明確 human action，其他無依賴 task 繼續 | 該 dependency 不可越過 |
| Staging create 失敗 | 執行精確 cleanup，保留 IaC/CLI receipt | 否 |
| Staging test 失敗 | 保留環境至 TTL，建立 remediation task | 否 |
| Prod 0% smoke 失敗 | 刪除/停用 green，blue 保持 100% | 否，但不影響現行 prod |
| Prod switch 後異常 | 暫停 scheduler，回切 traffic/jobs/snapshot pointer | release failed，進 incident |
| Cleanup 失敗 | 建 remediation task 與 orphan alert | release 可服務，但 closeout 未完成 |
| 第三方來源無許可 | 保持 disabled、無 credentials、無 egress | 可部署系統，不可啟用來源 |

## 16. 執行前需要的人類決策與資料

在 Supervisor 實際派發 Wave 3/4 前，負責人必須提供或確認：

- staging 與 production GCP project/region/resource naming。
- staging 要使用隔離 database 或隔離 schema；正式建議至少隔離 database 與 credentials。
- WIF provider、deployment service account、runtime service accounts、KMS key。
- Cloud SQL、GKE、Artifact Registry、Cloud Run、Cloud Scheduler 與 bucket 的既有或待建資源。
- GitHub `staging`、`production` environment required reviewers 與 secrets/vars ownership。
- production watch window 長度、SLO/rollback threshold、on-call owner。
- masked staging snapshot 的來源、遮罩責任人與保存期限。

這些外部 authority 未準備好時，Auto Worker 可以完成 code、IaC、tests 與 dry-run，但不能宣稱環境已實際部署。

## 17. 文件核准後的下一步

1. 由 Architecture、Ops、SRE、Security review 本文件並記錄接受或修改意見。
2. 一次建立第 12 節 task DAG，不把 implementation 塞進單一超大 task。
3. Supervisor 先派 Wave 0，凍結 manifest、gate 與 lease 介面。
4. 介面凍結後啟動 Wave 1 的最大安全平行度。
5. Wave 2 由單一 owner 整合唯一 Runtime Release。
6. 外部 environment 與 credentials 到位後才執行 Wave 3/4。

在完成第 1 步之前，不需要為「寫規劃」本身啟動 Auto Worker；在完成第 2 步之後，也不應由人工逐一指定所有 worker，而應由 Supervisor 按 DAG、能力、quota 與互斥 scope 自動派工。
