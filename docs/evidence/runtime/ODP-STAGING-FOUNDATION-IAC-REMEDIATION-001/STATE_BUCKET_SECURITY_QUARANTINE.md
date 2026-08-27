# State Bucket 安全隔離事件記錄

## 事件狀態

- **事件 ID**：`ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001-STATE-PLAN-QUARANTINE-001`
- **狀態**：`OPEN`
- **發現時間**：`2026-08-27T09:27:25Z`
- **結案條件**：retention 到期後由指定 owner 依核准程序清理並產生獨立 receipt；在此之前不得宣稱 foundation 完成。

## 被隔離物件

此物件是誤置於 Terraform state bucket 的 unauthorized non-state binary plan，
不是合法 state，也不得被分類為一般 artifact 或可長期保存的 reviewable plan：

- **bucket**：`oday-tfstate-staging-odayplus-runtime-20260825`
- **object generation**：`1787822664931431`
- **object SHA256**：`e46221085ffe1fe7243b7857d38191189443f9ea31bd970719687cc07e3404a6`
- **CMEK**：`projects/odayplus-runtime-20260825/locations/asia-east1/keyRings/oday-tfstate-staging-state/cryptoKeys/oday-tfstate-staging-state`
- **retention expiration**：`2026-09-26T09:24:24Z`
- **no early deletion**：`true`
- **expiry-cleanup owner**：`Staging Foundation Owner`

在 retention expiration 前不得刪除物件、解除 CMEK、關閉 versioning/PAP/UBLA、
或降低 retention 以繞過隔離。到期後只能由指定 owner 依核准程序精確清理，並記錄
object generation、刪除結果與新的安全 receipt。

## 後續控制

Terraform state bucket 僅允許 state/lock 物件；binary plan、一般 release artifact
與 `plans/` prefix 上傳均禁止。Plan 驗證只在受控執行範圍使用，receipt 僅保留
digest、generation、action summary 等 metadata。

同一份 live readback 亦記錄 state bucket least-privilege IAM 的人類權限 blocker：
active user 缺少 `storage.buckets.get` 與 `storage.buckets.getIamPolicy`，project
缺少 `roles/storage.admin`。在具權限的人類 operator 完成 readback/convergence 前，
不得宣稱 state bucket IAM 已收斂。
