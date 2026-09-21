# 已合併工具任務的正式結案與 merged review 接續

PR #1305 已合併且 CI 成功，但 canonical `done` 只接受產品 reviewer 的 `review_approved`，使純開發工具任務無法結案。使用者明確要求開發工具採工具流程；本修正加入實際 owner 可執行的 `done_tooling <task-id> <pr-number> <source-sha> <message>`，依合併、CI 與工具範圍證據寫入真實 delivery 並歸檔。原 `done` 的產品審查要求保留。

## 結案驗證

- 重用 merged PR 驗證：實際 owner、原 task branch、configured base、source/merge object 與 ancestry、commit identity、CI 終態成功。
- 用 merge commit 第一個 parent 的 `config/change-review-scopes.json` 分類 task delivery diff，防止 PR 放寬自己的工具範圍。產品、混合、空或未知變更拒絕這條路徑。
- blocked／Human／independent gate 仍拒絕。核對明確 source SHA 後才能結案；原 `review_submission` 與 `approved_head` 不改寫，不產生虛構 reviewer approval。
- 寫入 `verification_mode=merged_tooling_scope_and_ci`、實際 actor、精確 source/merge、CI 與 manifest 收據，完成 canonical archive。同一 owner 對同一已歸檔 delivery 重試不改写收據。

## 最後兩個實作缺陷

1. `resolve_task_sha` 對 timeout、nonzero、missing executable、ambiguous/malformed/unexpected ref 都清除 cache 並明確報錯。只有成功的空回應才代表分支不存在。涵蓋 outbox reconciliation 後同一次 sync emission 不得重新發布成功的實際執行順序。
2. Reviewer 向後切回 submitted head 前，若目前是 detached commit 且沒有 branch/ref 保留該工作，拒絕切換並保留 HEAD。已由 durable branch 保存的 commit 可以安全 pin；owner reconnect 與正常 merged pinning 保留。

既有 owner-only merged review submission、正確 OPEN-to-MERGED transition、穩定 recovery provenance、shell discovery 失敗處理與已刪 branch 的 immutable targeting 繼續由回歸覆蓋。

## 驗證

- 修正前：兩個最後缺陷的回歸記錄 7 failed、1 passed（6 個 cache 子案例與 reviewer detached-work）。
- 焦點驗證：8 passed、20 subtests。
- 完整工具回歸原始結果：1 failed, 3060 passed, 8 skipped, 10 deselected, 3 warnings, 667 subtests passed in 369.11s (0:06:09)。唯一失敗是新增 done_tooling 尚未登記在必備 actor 測試表；已補登記，actor/closeout 的完整針對性重測通過，原始完整失敗收據保留。最終完整 CI 為合併必要條件。
- Ruff 與 boundary 通過；`1305-closeout-source.json` 保存四個程式／測試檔案的精確 SHA-256。原始命令、exit code、時間與輸出見本目錄 `1305-closeout-*.json` / `.log`。
- 真實 #1305 的 GitHub/Git/identity/CI/base-manifest 唯讀預檢成功，source `2c50d5a6ff8d9f986221489d548072e9f8bfbde4`、merge `cd9f45901ccd32b9234563db51d86cc4d82db290`。該預檢只在記憶體模擬後續正式 owner 移交，攔截所有 archive/log 寫入，並非已經結案的證明。

## 交付

這是 development tooling：工具 scope gate、required CI、普通 merge queue。合併後由前景透過正式 runtime rollout 部署，再由實際 canonical owner 執行 `done_tooling`。產品或混合任務繼續由 Supervisor 正常獨立 auto review；不清除 Human/Ops gate、不手改 canonical JSON。
