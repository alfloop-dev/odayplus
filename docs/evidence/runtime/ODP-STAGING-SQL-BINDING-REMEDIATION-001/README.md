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


## 2026-09-13：candidate posture 與 rollback root 補正

針對 `ff199e9b75354c1be7870a4bf6d7628b9620c243` 的兩項獨立審查 finding：handoff 現在從同一 exact candidate 讀取 workflow 與 deploy entrypoint，包含 provider mode、credential inventory、VPC 與 probe 接線；不同內容的 workflow override 直接拒絕。Admission 也從 candidate workflow 獨立檢查 provider credential、endpoint 與啟用狀態，不能用正確 digest 搭配偽造的乾淨 inventory 放行。未指定 candidate 的 posture 檢查保留 worktree 模式。

Rollback extraction 將指定 repository root 傳到 admission。新增真實獨立 repository 與 depth-1 clone，predecessor 只存在該 repo/local remote，測試 dict、檔案、inline JSON 三條輸入路徑；不能借用 module ROOT 裡已有的 historical SHA 掩蓋錯誤。缺 object/blob 持續 fail closed。

同一批新回歸測試對舊碼有 **2 個預期失敗**，分別重現 credential 被乾淨 worktree 隱藏及 extraction 使用錯誤 root。修補後完整 manifest、handoff、target-probe、SQL lifecycle 五個 suites 為 **314 passed、16 subtests passed**；其中新增 11 個真實 repository 案例。Ruff、boundary（1159 files）及 diff check 通過。原 SQL 三個 source/test SHA-256 與既有 125-case receipt 完全一致。

[驗證摘要與原件雜湊](candidate-posture-root-tests-20260913.json) 記錄原工具結果；原始 logs/JUnit 留在本機 operational handoff。這是本機測試證據，仍須 owner 正式送審及 Codex2 獨立核准；沒有 live deployment 或 GCP 變更。
