# ODP-ORCH-CONTINUATION-GATE-PROSE-FALSE-POSITIVE-001: Continuation Approval Prose Gate 修復與 Rejection 審計紀錄

## 1. 問題概述與根因分析

### 1.1 Prose 閘對程式識別字產生假陽性 (A1, A2, A3)
在治理控制面中，`continuation_approval_gate_error` 是專為 review-churn 升級至 Human/Ops 審核任務所設計的解鎖閘門。為了防止誤放獨立的人工審批閘（如 credentials、deployment、production、external-data 等），系統會透過 `blocked_task_prose_context` 掃描 blocker 相關欄位是否含有 `hard_gate_markers`。

**修復前缺陷**：
原 `blocked_task_prose_context` 僅剝除 task ID 與 `depends_on` 清單中的識別字，未剝除程式識別字、路徑與設定值。當任務的 `next` 或 `blocker` 中包含程式識別字（如 `build_sources_off_attestation`）、檔案路徑（如 `network.tf`、`delivery_toolchain/release/release_manifest.py`）、鍵值對（如 `conclusion=success`、`event=workflow_dispatch`）或 CI job 敘述（如 `deploy 相關 job 全 skipped`）時，字根比對（如 `attestation`、`deploy`）會產生假陽性，誤判該任務帶有獨立人工閘而拒絕消耗合法 Human/Ops continuation approval。

### 1.2 拒絕消耗時裸 continue 遺漏審計紀錄 (A4)
在 `.orchestrator/supervisor.py:consume_human_continuation_approvals` 中：
```python
# 修復前 supervisor.py 第 4412-4420 行：
        validation_error = runtime_ai_status.continuation_approval_validation_error(
            task,
            approval,
            now=now_dt,
        )
        if validation_error or _continuation_approval_nonce_reused(
            status,
            approval,
            tasks_path=tasks_path,
        ):
            # Do not mutate a malformed, unauthorized, or replayed record. The
            # task remains blocked, so a later cycle cannot turn bad state into
            # an execution capability.
            continue
```
當 approval 因 validation 錯誤（如 scope 不符、issuer 非 Human/Ops、過期等）或 nonce 重複使用而被拒絕時，程式直接執行裸 `continue`，未在 `ai-activity-log.jsonl` 中留下任何結構化事件，造成營運人員無法得知審批為何未生效。

---

## 2. 修復設計與實作

### 2.1 Prose 上下文清洗 (`blocked_task_prose_context`)
在 `scripts/ai_status.py` 與 `.orchestrator/supervisor.py` 的 `blocked_task_prose_context` 中，於比對 `hard_gate_markers` 之前進行精準結構化字元過濾：
1. **反引號區塊與 inline 程式片段**：過濾 ```` ```...``` ```` 與 `` `...` ``。
2. **鍵值對**：過濾 `<key>=<value>` 格式（例如 `conclusion=success`、`event=workflow_dispatch`）。
3. **路徑與具特定副檔名檔案**：過濾含 `/` 之路徑及副檔名為 `.py`、`.sh`、`.tf`、`.yml`、`.yaml`、`.json` 之檔案名稱。
4. **Snake_case 識別字**：過濾含底線之識別字（例如 `build_sources_off_attestation`、`SOURCES_OFF_EGRESS_CONTRACT_FILES`）。
5. **CI Job 詞彙**：過濾 `<identifier> 相關 job`、`<identifier> job`（例如 `deploy 相關 job`、`build job`）。

過濾後轉為 lowercase 並移除 task ID 及 `depends_on`，保留自然語言上下文進行 fail-closed 閘門掃描。

### 2.2 拒絕事件審計與 Tick 冪等去重 (A4)
在 `.orchestrator/supervisor.py:consume_human_continuation_approvals` 中：
1. 當發生 `validation_error` 或 `nonce_reused` 時，提取 `rejection_reason`（優先採用 `validation_error`，否則為 `"continuation approval nonce has already been used"`）。
2. 在 runtime `state` 的 `human_continuation_approval_rejected` 字典中以 `{approval_id}:{task_id}:{rejection_reason}` 記錄。
3. 同一 approval 與 reason 僅於首次判定拒絕時寫入一筆 `type="human_continuation_approval_rejected"` 之 activity log，後續 supervisor loop tick 自動去重，不重複刷 log。

---

## 3. 驗收標準對齊 (Acceptance Verification)

### 3.1 A1: Staging Foundation Fixture 驗證
以 `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` 於 `2026-09-17T23:27:08Z` 的真實 `next` 文本（含 `deploy 相關 job 全 skipped` 與 `build_sources_off_attestation`）為 fixture：
- 修復前：`continuation_approval_gate_error(task)` 回傳 `"task carries an independent credentials/deployment/production or human gate"`（命中 `attestation` 與 `deploy`）。
- 修復後：`continuation_approval_gate_error(task)` 回傳 `None`。
- 測試案例：`scripts/test_ai_status.py::HumanContinuationApprovalTests::test_staging_foundation_fixture_code_identifiers_do_not_trigger_hard_gate`。

### 3.2 A2: 既有自然語言語意不得放寬
既有測試維持通過且未修改其斷言：
- `test_non_human_cannot_issue_and_hard_gate_is_not_eligible`：`next="Review churn is also blocked pending production deployment approval."` 仍回傳 independent gate 錯誤。
- `test_production_deployment_title_does_not_block_review_churn_approval`：標題含 production/deployment 但 blocker 純為 review churn 時維持通過。

### 3.3 A3: 三組新增測試（正反例）
在 `scripts/test_ai_status.py` 中實作：
- **組 (a) CI job 與函式識別字** (`test_ci_job_and_attestation_identifier_prose_group_a`)：
  - 正例：含 `deploy 相關 job 全 skipped` 與 `build_sources_off_attestation` 的 review-churn task -> 回傳 `None`。
  - 反例：含 `requires manual deploy approval before proceeding` -> 回傳 independent gate 錯誤。
- **組 (b) 檔名/路徑/反引號標記 vs 真實憑證/流程日誌閘** (`test_credential_and_vpc_flow_logs_code_tokens_group_b`)：
  - 正例：含 `credential_helper.py`、`` `GCP VPC Flow Logs` ``、`infra/vpc_flow_logs.tf`、`tests/credentials/test_auth.py`、`gcp_vpc_flow_logs` -> 回傳 `None`。
  - 反例 1：`requires manual credential injection from Ops before proceeding` -> 回傳 independent gate 錯誤。
  - 反例 2：`awaiting GCP VPC Flow Logs manual verification and operator signoff` -> 回傳 independent gate 錯誤。
- **組 (c) 自然語句獨立閘維持 fail-closed** (`test_natural_language_independent_gates_group_c`)：
  - 正例（真硬閘）：`pending production deployment approval`、`requires manual approval before production deployment`、`waiting for operator intervention on production rollout`、`blocked pending human gate authorization` 均回傳 independent gate 錯誤。
  - 反例（純 review churn）：`Review churn only after repeated reviewer reopenings.` 回傳 `None`。

### 3.4 A4: Supervisor 拒絕消耗審計與去重
在 `.orchestrator/test_supervisor.py` 中實作 `test_rejected_continuation_approval_writes_activity_log_and_deduplicates`：
- 驗證 validation error（如 `issued_by="Codex2"`）時寫入 `human_continuation_approval_rejected`（含 `task_id`、`approval_id`、`reason`）。
- 驗證 nonce 重複使用時寫入 `human_continuation_approval_rejected`（`reason="continuation approval nonce has already been used"`）。
- 驗證第二個 tick 傳入相同 state 時去重不重複寫入。

---

## 4. A5: 看板 5 張 waiting_for=Human/Ops Task 實跑量測

對目前 canonical 看板上 5 張 waiting_for=Human/Ops 任務逐一實跑 `continuation_approval_gate_error`：

| Task ID | 執行結果 | 命中 Marker / 判定理由 | 原文片段與自然語言證明 |
| :--- | :--- | :--- | :--- |
| `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` | Independent Gate Error | `token` | **真實憑證阻塞（自然語言）**：`未完成：BLK-ENG-EXACT-C-IAM-001 需 gcloud 憑證（本日 14:0x 後 deborah.lu token 再次失效，需 gcloud auth login）`。非程式識別字，確屬需要人類登入的憑證阻塞。 |
| `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001` | **None** (Eligible) | 無 (Cleared) | **假陽性已排除**：原文中之 `deploy 相關 job 全 skipped`、`build_sources_off_attestation`、`SOURCES_OFF_EGRESS_CONTRACT_FILES`、`network.tf`、`release_manifest.py` 均已正確剝除，無殘留自然語言硬閘。 |
| `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001` | **None** (Eligible) | 無 (Cleared) | **假陽性已排除**：原文中之 `gs://...`、`scripts/retain_emgi_raw_snapshots.py`、`_ENABLED="false"`、`configmap.yaml` 均已正確剝除，無殘留自然語言硬閘。 |
| `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` | Independent Gate Error | `production`, `token` | **生產環境治理限制（自然語言）**：`ODP_PROD_... 兩個 production 變數於 ... 被寫入，而 ODP-PROD-NETWORK-PARITY-PLAN-001 明訂兩者刻意不設（資源不存在，先填會製造齊備假象）。Human/Ops 將移除；evidence 不得以其存在佐證 production 就緒。` 確屬生產治理規則限制。 |
| `XR-EXT-OSS-FINAL-AUDIT-001` | Independent Gate Error | `credential`, `secret`, `token`, `production` | **授權範圍限制與人類閘（自然語言）**：`本 task 明文禁止 live 部署、cloud writes、credentials 與 production gate 的授權 scope 下結構性無法取得，維持阻擋 HUMAN-OSS-LEGAL-APPROVAL-001`、`workload Secret 投影檢查依賴 SIMULATED_RESTART_AND_ROLLOUT 模擬收據`。確屬自然語句治理限制。 |

---

## 5. A6: 不變性與邊界保證

- **不改 approve_continuation 發放條件**：`command_approve_continuation` 驗證邏輯完整保留。
- **不改 hard_gate_markers 清單內容**：清單中的 24 個 markers 逐字維持原貌。
- **不動 capacity_controller.py**：`.orchestrator/capacity_controller.py` 未被修改。
- **不改 canonical 看板記錄**：本工作未直接竄改 `ai-status.json`、`ai-activity-log.jsonl` 等 canonical 檔案。

---

## 6. 驗證指令與收據 (Verification Receipts)

```bash
# 1. ai_status prose/continuation 測試 (8 passed)
uv run pytest -q scripts/test_ai_status.py -k "continuation or prose_context"

# 2. supervisor continuation 測試 (7 passed)
uv run pytest -q .orchestrator/test_supervisor.py -k continuation

# 3. ruff 靜態分析 (All checks passed)
uv run ruff check scripts/ai_status.py .orchestrator/supervisor.py scripts/test_ai_status.py .orchestrator/test_supervisor.py

# 4. git diff 格式檢查 (Clean)
git diff --check
```
