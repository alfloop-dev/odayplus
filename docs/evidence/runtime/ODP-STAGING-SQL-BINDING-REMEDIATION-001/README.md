# Staging SQL connection binding 修復

Task: `ODP-STAGING-SQL-BINDING-REMEDIATION-001`

Runtime Release 將 `GCP_CLOUD_SQL_INSTANCE=project:region:instance` 傳入 lifecycle。原程式把完整字串當作 instance 名稱，再次串接 project/region，導致 Terraform SQL database/user 及備份還原參數不正確。

修復讓 create 接受 bare name 或完整 connection name，驗證 project、region 與顯式 connection 一致後產生兩個正確欄位。格式錯誤或身分矛盾在任何 state/API 寫入前拒絕。Live rehearsal 會使用 bare instance name 並核對 Terraform output handoff。

驗證：Ruff 通過；完整 lifecycle 與新增 SQL 回歸 125 tests、0 failures。實際 CLI dry-run 驗證輸出的 tfvars；backup/restore 用 mocked API 驗證參數，不能當作 live release 證明。原始測試收據保留在本機 operational handoff；公開版移除本機路徑與內部 hostname 並記錄原件 SHA-256。測試在隔離 foundation 工作樹執行；這三個程式/測試檔在分拆前後 bytes 一致，原 HEAD 與 dev 在既有兩個檔案也完全一致。

去重：2026-09-12 查核 canonical active tasks 與全部 open PR，沒有相同 SQL 接線修復。此任務接手已完成的前景實作，owner 只須完成獨立審查所需交付；不重做基礎設施、依賴升級或 Runtime Release。與 foundation #1046 分開，是因其 forbidden_paths 含 product_ops/。

本 PR 不修改 workflow、ephemeral_staging module、GCP 資源或 canonical manifest。GCP SQL metadata、recovery bucket 建置與 staging GitHub binding 的實際收據歸 foundation #1046。
