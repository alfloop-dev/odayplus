# NetPlan 管理層 baseline 核准交辦單（2026-08-08）

- Task ID：`ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001`
- Task class：`human_gate`
- Owner：`Human/Ops` 指派的具名管理層負責人
- Technical reviewer：`Antigravity`
- 目前狀態：`BUSINESS_UAT_UNVERIFIED` + `GOVERNED_DISABLED`

本文件是交辦單，**不是核准**。核准由外部權威系統的收據承載。

---

## 0. 為什麼到現在才有這份文件

blocker 記載「Human/Ops 必須提供 `docs/evidence/netplan/baseline-approval/`
描述的完整決策集」——**但那個目錄不存在**，dev 上 `docs/evidence/netplan/`
零個檔案。也就是說，過去沒有任何地方寫明「要交什麼」，這個 gate 從
2026-08-05 卡到現在，一部分原因是需求本身沒有被寫出來。

本文件補上這個缺口，內容全部從程式碼契約推導，不是範本。

---

## 1. 必須先解決的前提：驗證器沒有接上

`modules/netplan/application/planning.py:389`

```python
if self.approval_verifier is None:
    raise NetPlanApprovalError(
        "authoritative management approval verifier is not configured"
    )
```

`apps/api/oday_api/main.py:129` 的 `netplan_approval_verifier` 預設為 `None`，
且 production 沒有任何地方注入實作。

**即使你今天交出一份完全合規的收據，系統也無法驗證它**——會直接在這一行拋錯。

`solver/netplan/model.py:276` 有 `FixedManagementApprovalReceiptVerifier`
可作為接線的起點，它要求 `source_system`、`principal_id`、`principal_role`
三者皆為固定非萬用字元值（傳入 `ANY` 或空字串會直接 `ValueError`）。

**這是工程工作，不是你的決策**，但必須先做完，否則收據無處可用。

---

## 2. 你要核准的實質內容

| 項目 | 說明 |
|---|---|
| 不可變的 baseline actions | 每個 entity 對應的 `NetworkAction`，核准後不得變動 |
| entity / scenario 領域範圍 | 本次核准涵蓋哪些對象與情境 |
| policy / constraints | `NetPlanConstraints`，含 `policy_version` |
| objective / risk penalty | 風險懲罰係數，程式碼目前寫死為 `100000.0` |
| source snapshots | 產生這些 action 所依據的來源快照 ID 集合 |

---

## 3. 收據必填欄位（19 項，缺一即 fail closed）

出自 `solver/netplan/model.py:147` 的 `ManagementApprovalReceipt`：

| 欄位 | 來源 | 說明 |
|---|---|---|
| `receipt_id` | 權威系統 | 不得為空、不得是 `ANY` 或 `UNVERIFIED` |
| `source_system` | 權威系統 | 固定值，不得萬用 |
| `principal_id` | 權威系統 | **必須等於送出核准的 actor_id**，見 §4 |
| `principal_role` | 權威系統 | 固定值，不得萬用 |
| `decision` | 你 | 核准決定 |
| `approval_reference_id` | 權威系統 | 可回讀的核准 reference |
| `issued_at` | 權威系統 | UTC |
| `expires_at` | 權威系統 | UTC，有限期限 |
| `scenario_id` | 系統 | 見 §5 |
| `baseline_id` | 系統 | 等於 `scenario_id` |
| `baseline_name` | 系統 | 等於 `scenario_name` |
| `scope` | 系統 | 格式為 `tenant:{tenant_id}` |
| `release_id` | 系統 | 等於 `scenario.planning_horizon` |
| `policy_version` | 系統 | 出自 `scenario.constraints.policy_version` |
| `actions_by_entity` | 系統 | entity → action 對應表 |
| `source_snapshot_ids` | 系統 | 已排序的來源快照 ID |
| `baseline_content_hash` | 系統計算 | 見 §5 |
| `solver_problem_hash` | 系統計算 | 見 §5 |
| `receipt_hash` | 權威系統 | 對前 18 項的 canonical SHA-256 |

---

## 4. 驗證規則與失敗模式

程式碼會逐項比對，以下任一不符即拒絕：

**核准階段**（`planning.py:212`）

```python
if actor_id != verification.receipt.principal_id:
    raise NetPlanApprovalError(
        "audit actor does not match the verified approval principal"
    )
```

送出核准動作的 actor，**必須就是收據上的 principal 本人**。代送不成立。

**執行階段**（`planning.py:263`）

```python
if (approval.actor_id != verification.receipt.principal_id
    or approval.authority_receipt.receipt_hash != verification.receipt.receipt_hash):
    raise NetPlanApprovalError(
        "persisted approval does not match authoritative management readback"
    )
```

執行時會**重新向權威系統回讀一次**，並比對已保存的 `receipt_hash`。
收據在核准後被更動，執行就會失敗。

**其他 fail-closed 條件**

- `receipt_id` 為空、或為 `ANY` / `UNVERIFIED` → `approval_receipt_id_invalid`
- 權威系統查不到該 `receipt_id` → `authoritative_approval_unresolved`
- 任何 `violations` 非空 → `authority_attests_receipt()` 回傳 `False`
- solve 結果本身驗證失敗 → `persisted solve result verification failed`

task acceptance 另列明不接受：AI 撰寫的核准、把 actor 字串當認證、
任意 receipt id、可變的 caller baseline、缺少／過期／非 UTC 收據、
hash 或 scope 或 release 不符。

---

## 5. 兩個雜湊你不能手算

`baseline_content_hash` 與 `solver_problem_hash` **由系統從 solve 結果計算**，
你無法也不應自行填入。

`baseline_content_hash`（`solver/netplan/model.py:112`）
對以下內容取 canonical SHA-256：

```
baseline_id, baseline_name, scenario_id,
actions_by_entity（依 entity 排序，取 action.value）,
source_snapshot_ids（已排序）,
scope, release_id,
risk_penalty = 100000.0,
constraints
```

`solver_problem_hash`（`planning.py:441`）

```
compute_solver_problem_hash(options_by_entity, constraints, 100000.0, alternative_limit)
```

### 因此流程必須是這個順序

```
1. 系統跑 solve，產出 baseline actions 與上述兩個雜湊
2. 把 baseline 內容 + 兩個雜湊送進你的權威核准系統
3. 你在該系統核准，系統產生收據並回填全部 19 個欄位
4. NetPlan 從該系統回讀收據並驗證
```

**不能反過來**——先開收據再產 baseline，雜湊必然不符。

---

## 6. 目前缺什麼

| 項目 | 狀態 |
|---|---|
| 交辦單（本文件） | 本次補上 |
| 不可變的 baseline / source 產物 | **缺** |
| 權威核准系統的收據 | **缺** |
| `approval_verifier` production 接線 | **缺**（§1，工程工作） |

blocker 記載的 anchored intake commit 為 `08ea878b`。

---

## 7. 建議收據結構

以下為資料交換格式，**不代表已核准**；必須由權威系統產生。

```json
{
  "receipt_id": "<authoritative-receipt-id>",
  "source_system": "<authoritative-approval-system>",
  "principal_id": "<authoritative-principal-id>",
  "principal_role": "<authorized-management-role>",
  "decision": "approved",
  "approval_reference_id": "<resolvable-reference>",
  "issued_at": "<UTC timestamp>",
  "expires_at": "<UTC timestamp>",
  "scenario_id": "<from-solve>",
  "baseline_id": "<equals scenario_id>",
  "baseline_name": "<from-solve>",
  "scope": "tenant:<tenant-id>",
  "release_id": "<scenario.planning_horizon>",
  "policy_version": "<constraints.policy_version>",
  "actions_by_entity": { "<entity-id>": "<action>" },
  "source_snapshot_ids": ["<snapshot-id>"],
  "baseline_content_hash": "<computed-by-system>",
  "solver_problem_hash": "<computed-by-system>",
  "receipt_hash": "<canonical-sha256-of-the-above>"
}
```
