# ORCH-ARCHIVE-HISTORY-EXECUTE-003 後續收尾指引

協調者：Codex。本文指引必須搭配同目錄實際命令收據使用，不能以本文替代執行證據。

請先檢查 `maintenance-window.json` 的 status 是 `applied_and_verified`、`restart_command_succeeded` 是 true，且後續 runtime 健康收據的 loaded_code_sha 是 `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`。不成立就回報具體差異；不得自行重做 live 操作。

工具 PR #1287 已合併，exact reviewed head 為 `88285f59e52845761247d3719eb2953590972903`，merge commit 為 `50581b3b180aedaf76fbe4aaffb3bf7e2d120050`。原 owner 已於 2026-09-09T16:19:07Z 正式 done。此次工具、runtime rollout 和兩筆 live invalidation 是不同交付步驟。

你的工作是沿既有 task branch / PR #1278 收尾證據。可修改原任務證據目錄及 `docs/evidence/execution-control/ORCH_ARCHIVE_HISTORY_EXECUTE_APPLY_20260908_ZH_TW.md`；不另開工具重構、產品修補、維護窗口或回填批次。

1. 將必要的實際命令收據、before/after resolver 讀回、rollout/maintenance/重啟健康收據複製到本任務證據目錄，保留原始 bytes，建立帶來源路徑、SHA256 的清單。所有時間引用原始收據的 UTC，不以整理文件的時間冒充量測時間。原 actor 是 Codex，worker 是證據整理者 Antigravity3。
2. 更新目前結論：原始 23 份 reconstructed snapshots 保持原 bytes；effective 狀態現為 21 done、2 blocked。2 個被撤回 ID 為 ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001、ODP-MODELREADY-QUALITY-NULLABLE-001。各自 dependency_satisfied 從 true 變 false。兩個 downstream 的整體 admission 在更正前已因其他依賴為 false，不能寫成原本可以派送。15 份 placeholder 仍 blocked/non_dispatchable/Human-Ops，歷史 actor unknown 保留。
3. 追加或更新 disposition/blocker 文件，清楚区分原始歷史快照 done 與 resolver effective blocked。失效完成判定已處理，但 Human/Ops disposition 缺口、ModelReady production-entry A4 證據缺口仍存在；此次沒有補成產品功能完成。原始 batch/hold/checkpoint 不可改寫，舊 hold 已過期，這次是新維護窗口與窄範圍 invalidation 指令。
4. 原第 3/4 輪時間問題需收尾：commit 時間不是 command 量測時間。不能找回原命令 timestamp/exit receipt 的欄位，明列 unknown 並說明是沿用舊觀察；新收據只能支持新觀察。保留原歷史檔，另加更正即可，不必偽造舊終端紀錄。
5. NLTK 最終驗證既有 archived done、AVM Human-only churn=11 都不變。維護後 Supervisor 需引用實際新 PID、loaded SHA、heartbeat 與 successful loop；沒有新增 worker 派工也要照實說明，不能聲稱本次釋放新產品任務。
6. 必要驗證限 JSON 可解析、receipt manifest 的 hash/source 對應、三份原始 batch/hold/checkpoint bytes 不變與文件相互一致。不要跑 pytest 全套、GCP、測試產品線或再執行 live mutation。
7. 依既有 worker anchor / task_finalize 流程提交有實際證據變更的 head 到同一 PR #1278，以真實 Antigravity3 身分 canonical 送審。指定 Codex 將獨立審查該 exact head。未 merge 前不能 done；review-approved head 保持 immutable。

嚴禁手寫 board/archive/index、重跑 archive_recovery_apply、重跑 invalidation、降版 runtime、操作 Supervisor/watchdog 或假冒其他 actor。這些不是本次 owner 收尾工作。
