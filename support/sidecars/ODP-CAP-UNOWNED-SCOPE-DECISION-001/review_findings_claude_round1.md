# Sidecar Acceptance Packet — Review Findings (Round 1)

- **Task ID**: `ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-ACCEPTANCE`
- **Parent Task**: `ODP-CAP-UNOWNED-SCOPE-DECISION-001`
- **Reviewed Artifact**: `support/sidecars/ODP-CAP-UNOWNED-SCOPE-DECISION-001/ODP-CAP-UNOWNED-SCOPE-DECISION-001-SIDECAR-ACCEPTANCE.md` @ `6e9cb4a3`
- **Owner**: Antigravity4 · **Reviewer**: Claude
- **Round**: 1 · **Disposition**: **REOPEN**
- **Baseline compared against**: parent branch `task/ODP-CAP-UNOWNED-SCOPE-DECISION-001` head `dda55b91` (PR #646), 即 parent task 目前的 `review_gate_sha`

---

## 0. 為什麼是 reopen 而不是 approve

Packet 的自我宣稱 verification 是 “Structural & Documentation Inspection”（§5），
只檢查了 scope compliance，沒有把任何一條技術主張回頭比對 repo 或 parent 的決策記錄。
結果是：packet 的 §3 checklist 在四個地方**比 parent `dda55b91` §7 的驗收要點更寬鬆或與其牴觸**，
而 §4 提出的 task 拆分會**和 parent §6 已裁決的 task id 表相撞**。

Packet 存在的理由是「避免以有畫面結案的假性完成」。若照現狀吸收，它反而會放寬驗收面。
因此退回 in_progress。

Scope 本身合規：僅新增 1 個 support artifact（173 行），零 L1 canonical / runtime / governance 改動 —— 這點通過。

---

## 1. Blocking findings

### F-1（阻擋）§4.2 的五個 task id 與 parent §6 已裁決的 task id 表相撞

Packet §4.2 建議建立：
`ODP-CAP-U1-MODEL-RELEASE-CTRL`、`ODP-CAP-U2-USER-ROLE-MGMT`、
`ODP-CAP-U3-FEATURE-FLAG-MGMT`、`ODP-CAP-U4-NOTIFICATION-DELIVERY`、
`ODP-CAP-U5-TASK-ATTACHMENTS`。

Parent `dda55b91` §6 的決策記錄已經把後續 task id 定死，且五個都已存在並指派到人：

| 能力 | Parent §6 已裁決 task id | 2026-08-07 狀態 |
|---|---|---|
| U-3 | `ODP-CAP-FEATURE-FLAG-UI-001` | review_approved |
| U-4 | `ODP-CAP-NOTIFICATION-DELIVERY-001` | done（已封存） |
| U-5 | `ODP-CAP-TASK-ATTACHMENTS-001` | review_approved |
| U-1 | `ODP-CAP-MODEL-RELEASE-UI-001` | done（已封存） |
| U-2 | `ODP-CAP-USER-ROLE-UI-001` | review |

`ai-task-archive/tasks/` 內確實有 `ODP-CAP-MODEL-RELEASE-UI-001.json` 與
`ODP-CAP-NOTIFICATION-DELIVERY-001.json`。若照 §4.2 開新 id，等於為兩項已交付的能力
再開一條重複 lane，並讓 RTM 出現兩組互不對應的 task 座標。

**要求**：§4.2 改成對映 parent §6 的既有五個 task id，逐項標注現狀
（archived done / review_approved / review），並把本 packet 的 checklist 掛到那些 id 底下，
而不是提出新命名。

### F-2（阻擋）§3.4 U-4 的現況描述已過期，會把已交付的實作再要求一次

Packet §3.4 要求「補齊 SMTP / SendGrid / Amazon SES 等正式 Email 投遞 Adapter，
**替換全 Console/Mock 現狀**」；§2.1 dependency matrix 也把 U-4 的上游列為
「SMTP/SendGrid/SES Provider」尚待接入。

這是 2026-08-03 決策文件當時的現況，不是目前 tree 的現況。
`modules/notifications/infrastructure/adapters.py` 目前已有：

- `EmailNotificationAdapter`（line 395）—— 走 SMTP，含 production fail-closed 檢查與 delivery receipt
- `InAppNotificationAdapter`（line 557）—— 寫入 inbox、未讀/ack 狀態
- `MultiChannelNotificationAdapter`（line 646）

且 `ODP-CAP-NOTIFICATION-DELIVERY-001` 已 done 封存。「全 Console/Mock」只在
`ConsoleNotificationAdapter`(44) / `OnCallNotificationAdapter`(84) 的年代成立。

**要求**：§3.4 與 §2.1 U-4 列改寫為「驗收既有 adapter 是否滿足 FR-SHARED-006」，
不要寫成尚未開工。

---

## 2. 驗收要點被寫鬆（需修正才能被主線吸收）

### F-3 FR-SHARED-006 的觸發事件由五種被壓成四種

Parent `dda55b91` §7 明寫：「驗收要涵蓋規格列舉的**五種**觸發：
任務指派、逾時、核准、**失敗**、**回滾**。」

Packet §3.4 寫成「任務指派、逾時警告、核准請求與失敗回滾（Failure Rollback）**四類事件**」，
把「失敗」與「回滾」併成一項。失敗通知與回滾通知是兩個不同的觸發點
（失敗未必回滾、回滾未必來自失敗），合併後實作只做其中一種也能通過驗收。
這正是 packet 宣稱要防的假性完成。

### F-4 `channels` 預設值寫錯

Packet §3.4：「正確依照 Actor 設定之預設通道（`channels = ["email", "in_app"]`）」。

實際 `modules/notifications/domain/models.py:11`：

```python
channels: list[str] = field(default_factory=lambda: ["email"])
```

Parent §7 也特別註明「`channels` 預設已是 `["email"]`，所以是補投遞實作而非改資料模型」。
Packet 的寫法會讓實作者以為要改資料模型預設值。

### F-5 U-5 缺 `FR-SHARED-007` 敏感度遮罩，卻新增了地理位置蒐集

Parent §7 對 `ODP-CAP-TASK-ATTACHMENTS-001` 要求：
「需一併處理儲存、權限範圍與 **`FR-SHARED-007` 的敏感度遮罩**，
因為現勘照片與租約掃描屬受控資料。」

Packet §3.5 三個子區塊（雲端儲存整合、Task Detail 綁定、現場證據與稽核備份）
**沒有任何一條遮罩 / 受控資料條目**，反而在最後一條要求自動寫入
「地理位置資訊（若有）」metadata —— 等於在沒有遮罩要求的前提下多蒐一類敏感欄位。

**要求**：補一條 FR-SHARED-007 遮罩驗收，並讓地理位置 metadata 明確受該條約束。

### F-6 U-2 缺 `ODP-00-04` ADR 護欄，且 FR-OPS-003 的維度寫錯

兩個問題：

1. Parent §7 對 `ODP-CAP-USER-ROLE-UI-001` 有一條明確護欄：
   「若實作中判定應由外部 IdP 承擔而非平台自建，屬範圍變更，須依 `ODP-00-04` 補 ADR 後
   才可調整此 task 的範圍——**不得直接縮小交付**。」
   Packet §3.2 與 §4 完全沒有這條，缺了防縮水的唯一閘門。
2. Packet §3.2 寫「正確支援 Tenant / Brand / Region / Store **五層**架構」——
   列了四個卻稱五層。`ODP-FR-OPS-003` 的原文維度是
   **Tenant / Brand / Region / Store / Role / Attribute**（見決策文件 §U-2 規格來源列），
   packet 漏掉 Role 與 Attribute。§2.1 matrix 的
   「Tenant/Brand/Region/Store RBAC System」同一處問題。

---

## 3. 次要 / 建議

- **§1 的「全部選擇選項 A」未註明出處**。這個結論正確，但依據是 parent branch
  `dda55b91` §6（PR #646，**尚未併入 `dev`**）。`dev` 上的
  `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md` §6 仍是
  全 `_pending_`，且 §4 對 U-2 標「A 或 C」、U-5 標「A 或 B」。
  建議 packet 直接引用 commit sha 與 PR 編號，否則讀者在 `dev` 上對不上。
- **§5 Verification Log 應記錄實際比對動作**，至少包含：比對的 parent commit、
  查證過的檔案路徑與行號。目前只有 scope 宣告，這是 F-2 ~ F-6 能溜過去的直接原因。

---

## 4. 已查證為正確的部分（不需改）

以下主張經 repo 比對成立，重寫時請保留：

- `apps/api/app/routes/learninghub.py` 確有 **10 個** route decorator（209/236/312/400/432/447/460/510/518/529）。
- `POST /releases`(312)、`POST /releases/{release_id}/monitor`(400)、
  `GET /models/{model_name}/evidence`(510) 三個 endpoint 路徑正確。
- `apps/api/oday_api/security/`、`modules/notifications/infrastructure/adapters.py` 路徑存在。
- U-3 為 P1、U-4 為 P1 的優先序與決策文件 §4 一致。
- Mermaid dependency map 的 gate 關聯（`ODP-PLAN-UAT-SIGNOFF-001`、
  `ODP-PLAN-FINAL-GATE-AUDIT-001`、`ODP-FR-GOV-009`）方向正確。
- Scope 合規：只新增 support artifact，未動 canonical truth。

---

## 5. Review 執行記錄

比對指令（於 worktree
`/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-cap-unowned-scope-decision-001-sidecar-acceptance`）：

```bash
git diff --stat origin/dev...HEAD                     # 1 file, +173, scope 合規
grep -nE '@[a-z_]+\.(get|post|put|patch|delete)\(' apps/api/app/routes/learninghub.py   # 10
grep -nE '^class ' modules/notifications/infrastructure/adapters.py                     # 44/84/395/557/646
grep -n 'channels' modules/notifications/domain/models.py                               # line 11 -> ["email"]
git show dda55b91:docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md # parent §6/§7
ls "$PANTHEON_STATUS_ROOT/ai-task-archive/tasks/" | grep -iE 'notification-delivery|model-release'
```

環境註記：本 worktree 無 `scripts/git/`，`worker_commit.py` 不可用；
本 review commit 依 `task-closeout-finalization.md` 的 foreground fallback
（`git restore --staged --` → 明列檔案 `git add` → `git diff --cached --name-only` → `git commit -F`）建立。

---

## 6. Round 2 收斂條件

owner 修正 F-1 ~ F-6 並補上 §5 的實際查證記錄後，重新 handoff 即可。
F-1 / F-2 是阻擋項；F-3 ~ F-6 是驗收要點正確性問題，兩類都必須處理，
因為本 packet 的唯一用途就是被 parent 吸收成驗收依據。
