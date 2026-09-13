# Staging SQL connection binding 修復

Task: `ODP-STAGING-SQL-BINDING-REMEDIATION-001`

Runtime Release 將 `GCP_CLOUD_SQL_INSTANCE=project:region:instance` 傳入 lifecycle。原程式把完整字串當作 instance 名稱，再次串接 project/region，導致 Terraform SQL database/user 及備份還原參數不正確。

修復讓 create 接受 bare name 或完整 connection name，驗證 project、region 與顯式 connection 一致後產生兩個正確欄位。格式錯誤或身分矛盾在任何 state/API 寫入前拒絕。Live rehearsal 會使用 bare instance name 並核對 Terraform output handoff。

驗證：Ruff 通過；完整 lifecycle 與新增 SQL 回歸 125 tests、0 failures。實際 CLI dry-run 驗證輸出的 tfvars；backup/restore 用 mocked API 驗證參數，不能當作 live release 證明。原始測試收據保留在本機 operational handoff；公開版移除本機路徑與內部 hostname 並記錄原件 SHA-256。測試在隔離 foundation 工作樹執行；這三個程式/測試檔在分拆前後 bytes 一致，原 HEAD 與 dev 在既有兩個檔案也完全一致。

去重：2026-09-12 查核 canonical active tasks 與全部 open PR，沒有相同 SQL 接線修復。此任務接手已完成的前景實作，owner 只須完成獨立審查所需交付；不重做基礎設施、依賴升級或 Runtime Release。與 foundation #1046 分開，是因其 forbidden_paths 含 product_ops/。

本 PR 不修改 workflow、ephemeral_staging module、GCP 資源或 canonical manifest。GCP SQL metadata、recovery bucket 建置與 staging GitHub binding 的實際收據歸 foundation #1046。

## Exact candidate 回歸補正

SQL 檔案變更觸發的 release CI 相容修復已正式納入 task A6 與 owned_paths：指定 candidate 時驗證其 Git content，缺 object/blob 必須 fail closed；只有未指定 candidate 才可使用 worktree。Canonical manifest 與 gate 門檻保持不變。

前景補查發現原 missing-blob fixture 的硬編碼 SHA 其實不存在。現在測試建立真實臨時 Git repo，先提交缺少必要 workflow 的 candidate，再把 worktree 補成健康狀態，驗證仍拒絕不完整的 committed candidate。另一個案例實際破壞 worktree 的 provider posture，驗證 candidate digest、contract 與完整 attestation 驗證仍只取用原 commit；直接驗證壞 worktree 則失敗。

最終完整 manifest 回歸 **86 passed / 0 failures**，Ruff 與本次 diff 檢查通過。這次只補測試；原 SQL 三個 source/test bytes 不變，既有 125-test 收據仍對應其原件。獨立 reviewer 仍須審查最新 head。
