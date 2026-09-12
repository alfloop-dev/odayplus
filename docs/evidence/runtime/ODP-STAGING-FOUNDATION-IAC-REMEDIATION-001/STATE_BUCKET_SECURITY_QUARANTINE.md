# State Bucket 安全隔離事件記錄

## 事件狀態

- **事件 ID**：`ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001-STATE-PLAN-QUARANTINE-001`
- **狀態**：`CONTAINED_CLEANUP_DEFERRED`
- **發現時間**：`2026-08-27T09:27:25Z`
- **live metadata recheck**：`2026-09-01T09:14:13Z`；exact generation、size、CMEK 與 retention expiration 均一致。
- **後續 task**：`ODP-STAGING-STATE-PLAN-QUARANTINE-CLEANUP-001`
- **cleanup not-before**：`2026-09-26T09:24:24Z`
- **foundation delivery gate**：`false`；隔離控制已驗證，cleanup lifecycle 不再阻擋 foundation。
- **事件結案條件**：retention 到期後由指定 owner 依核准程序精確清理並產生獨立 receipt。

## 被隔離物件

此物件是誤置於 Terraform state bucket 的 unauthorized non-state binary plan，
不是合法 state，也不得被分類為一般 artifact 或可長期保存的 reviewable plan：

- **bucket**：`oday-tfstate-staging-odayplus-runtime-20260825`
- **object generation**：`1787822664931431`
- **object SHA256**：`e46221085ffe1fe7243b7857d38191189443f9ea31bd970719687cc07e3404a6`
- **CMEK**：`projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-tfstate-staging-state/cryptoKeys/oday-tfstate-staging-state`
- **retention expiration**：`2026-09-26T09:24:24Z`
- **no early deletion**：`true`
- **expiry-cleanup owner**：`Human/Ops`
- **cleanup executor**：`Antigravity2`

在 retention expiration 前不得刪除物件、解除 CMEK、關閉 versioning/PAP/UBLA、
或降低 retention 以繞過隔離。到期後只能由 cleanup task 指定 executor 對 exact
object generation 精確清理，並記錄刪除結果與新的安全 receipt；禁止 wildcard 或
prefix cleanup。

## 後續控制

Terraform state bucket 僅允許 state/lock 物件；binary plan、一般 release artifact
與 `plans/` prefix 上傳均禁止。Plan 驗證只在受控執行範圍使用，receipt 僅保留
digest、generation、action summary 等 metadata。

同一份 live readback 原先記錄的 state bucket IAM 人類權限 blocker 已於
`2026-08-30T15:52:48Z` 解除：bucket-scoped `roles/storage.admin` admin 完成
metadata/IAM readback，`roles/storage.objectUser` WIF deployer 再由 GitHub Actions
run `33320822376` 成功讀取兩個 remote-state object metadata。IAM 已收斂；binary
plan 的隔離控制已驗證為 `CONTAINED_CLEANUP_DEFERRED`。事件 lifecycle 由獨立
`ODP-STAGING-STATE-PLAN-QUARANTINE-CLEANUP-001` 追蹤至精確清理，但不再扣留
foundation completion claim。Foundation 仍須另行通過 API/Web Direct VPC
`ALL_TRAFFIC` authoritative live readback。
