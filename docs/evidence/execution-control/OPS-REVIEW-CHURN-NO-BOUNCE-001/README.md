# OPS-REVIEW-CHURN-NO-BOUNCE-001: 避免 review churn owner 來回彈跳驗收紀錄

## 1. 問題概述與根因分析

### 問題背景
在現行 Supervisor 控制面中，`worker_failure_policy.reassign_tasks_after_review_churn` 負責處理經由 Reviewer 連續退審（reopen）多次的任務。當任務累積 reopen 次數達到門檻（例如 2 次）時，系統會自動切換 owner 至備用候選人，以避免同一 owner 在同一個實作思維中陷入死循環。

然而在既有實作中存在以下缺陷：
1. **無 Review Epoch 歷史記憶**：每次觸發 churn 重指派時，候選人排除清單僅排除「當前 owner」與「reviewer」，並未記錄在同一審查週期（review epoch）中先前已經失敗過並被替換掉的 owner。
2. **Owner 來回彈跳（Bounce Loop）**：當 Owner A（例如 Antigravity）因兩次 reopen 被替換為 Owner B（例如 Codex2）後，若 Owner B 在後續兩次審查中亦被退審，由於 Owner B 的 fallback 清單包含 Owner A，系統會將任務再次指派回已失敗過的 Owner A，造成 A -> B -> A -> B 的無窮震盪與資源浪費。
3. **無健康候選人時缺乏 Fail-Closed 守衛**：當備用候選人全部耗盡或無其他健康 owner 可用時，原函式僅單純 `continue`，導致任務仍停留在原失敗 owner 身上或在派發迴圈中空轉，未對看板與營運人員提供明確可稽核的阻塞原因。

---

## 2. 修復架構與設計

依據驗收標準，本任務直接延伸既有 `worker_failure_policy.reassign_tasks_after_review_churn`，不新增第二個函式、外部 helper 或平行 scheduler：

1. **Review Churn Epoch 失敗 Owner 追蹤與排除**：
   - 在任務狀態中維護 `review_churn_epoch_failed_owners` 欄位，記錄在當前 review epoch 中所有曾遭退審替換的 owner。
   - 同時結合 `review_churn_previous_owner` 與 `review_reopen_history` 中的歷史 owner 紀錄，建立完整的已失敗清單。
   - 在透過 `first_viable_agent` 尋找新 owner 時，將 `exclude` 集合擴充為 `set(epoch_failed_owners) | {owner, reviewer}`，嚴格確保同一 epoch 內絕不會重新選到已因 reopen 被替換的 owner。

2. **Epoch 生命週期與重置規則**：
   - 當任務發生明確重設（例如 `reopen_count` 被歸零、或 `reopen_count` 低於前次記錄的 `raw_last_reassigned` 計數）或成功合併完成後，epoch 歷史自動重置為空清單，允許新週期重新評估候選人。
   - 當前週期未重設前，累積的所有已失敗 owner 將持續被排除。

3. **無可用健康 Owner / Reviewer 時 Fail-Closed**：
   - 當所有備用 owner 均已在當前 epoch 失敗或無健康候選人（`not new_owner`）時，系統立即 fail-closed：
     - 將任務狀態轉為 `blocked`。
     - `waiting_for` 設定為 `Human/Ops`。
     - 留下明確可稽核的阻塞訊息（記錄已失敗的候選人名單與 reopen 次數）。
     - 寫入 `review_churn_blocked` 結構化日誌。
   - 若找不到可用 reviewer，亦採取相同 fail-closed 機制，避免產生無效指派。

4. **單一權威來源與架構邊界遵循**：
   - 不修改 `.orchestrator/supervisor.py`、`dispatch_engine.py`、`capacity_controller.py` 或 `.github/workflows/`。
   - 所有改動局限於 `.orchestrator/worker_failure_policy.py` 與 `.orchestrator/test_supervisor.py`。

---

## 3. 驗證與測試結果

### 3.1 測試套件執行
執行指令（使用 repository `.venv`）：
```bash
.venv/bin/pytest .orchestrator/test_supervisor.py -k review_churn -v
python3 delivery_toolchain/governance/check_code_boundaries.py
git diff --check origin/dev
```

### 3.2 測試項目清單
1. `test_second_review_reopen_reassigns_owner_to_different_account_pool`：確認第 2 次 reopen 時成功觸發 churn 重指派至不同 account pool，並記錄 `review_churn_epoch_failed_owners`。
2. `test_review_churn_reassignment_is_idempotent_until_two_more_reopens`：確認在未累積滿新的 2 次 reopen 前維持等冪不重指派。
3. `test_review_churn_does_not_bounce_back_to_failed_owner_in_same_epoch`：**核心 Regression 測試**，驗證 Antigravity -> Codex2 之後，Codex2 再次被退審兩次時，系統排除了 Antigravity，成功指派給 Claude2 而未回彈至 Antigravity。
4. `test_review_churn_fails_closed_when_no_other_healthy_owner_available`：驗證當所有備用 owner 均已在當前 epoch 失敗且無其他候選人時，任務 fail-closed 轉為 `blocked`、`waiting_for: Human/Ops` 並產生 `review_churn_blocked` 審計日誌。
5. `test_review_churn_epoch_history_cleared_on_explicit_reset`：驗證當任務明確 reset 時，epoch 歷史正確清除並重新允許指派。

### 3.3 測試輸出實錄
```text
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0
collecting ... collected 462 items / 458 deselected / 4 selected

.orchestrator/test_supervisor.py ....                                    [100%]

====================== 4 passed, 458 deselected in 0.46s =======================
Code boundary checks passed for 982 files.
git diff --check origin/dev (clean)
```
全部 review churn 測試 100% 通過；程式邊界檢查與 `git diff --check` 皆通過。
