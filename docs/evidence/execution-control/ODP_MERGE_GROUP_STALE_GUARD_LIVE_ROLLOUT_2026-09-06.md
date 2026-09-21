# 舊 merge-group 失敗防護：Supervisor 實際部署收據

- 任務：`ODP-MERGE-GROUP-STALE-GUARD-LIVE-ROLLOUT-001`
- 日期：2026-09-06（UTC）
- 操作與收據草稿：Codex；文書提交、獨立審查及封存狀態以 canonical task／PR 為準。
- 範圍：僅 Supervisor；不是 GCP dev／staging／prod 發布收據。

## 已核對的實作與 CI

[PR #1215](https://github.com/alfloop-dev/odayplus/pull/1215) 的核准 head 是 `79049370fbcdd5be57b5294385455009ea0e5742`，於 `03:15:27Z` 合併成 `ba58ed6723960244d567c5314bfc200ffdf7bae8`。

[PR CI](https://github.com/alfloop-dev/odayplus/actions/runs/34008129764)、[合併版本 CI](https://github.com/alfloop-dev/odayplus/actions/runs/34008379673) 與 [merge queue review gate](https://github.com/alfloop-dev/odayplus/actions/runs/34008379690) 均通過；product／performance／E2E jobs 依 tooling scope 正常略過，不代表產品發布驗證完成。原實作任務已由 owner 完成 immutable closeout。

## 部署與健康讀回

沿既有 `scripts/orchestrator/rollout_supervisor_runtime.py` 與 watchdog 執行，沒有新增部署入口。部署來源為當時最新、乾淨且已驗證的 `origin/dev`；primitive 退出碼為 0。

| 項目 | 實測結果 |
| --- | --- |
| 更新前 SHA／PID | `62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a`／`1473872` |
| 更新後 loaded SHA／PID | `ba58ed6723960244d567c5314bfc200ffdf7bae8`／`1544799` |
| 新程序啟動 | `2026-09-06T03:19:35Z` |
| stable link 與真實 process cwd | `/home/lupin/oday-plus-supervisor-runtime-current` → `/home/lupin/oday-plus-supervisor-runtime-ba58ed672396` |
| 兩次獨立健康讀回 | `03:20:52Z` 讀到成功 loop `03:20:20Z`；`03:22:10Z` 讀到成功 loop `03:21:32Z` |
| loop error | 兩次皆為 `null` |
| Claude／Agy 容量 | 各自 configured max = effective concurrency = `5`；兩個 pool 均 `healthy`、generation `0` |

設定完全未修改，磁碟 SHA256 仍為 `5e9f4279b14ba6f4595317d4064ccebc952c678693ec2abd2bb243a89be8a12c`，loaded config digest 為 `5e9f4279b14ba6f4`。原 owner provider preference、poll interval、timeout、quota 與 Human gates 均保留。

新 runtime 的 `.orchestrator/github_reconciliation.py` SHA256 為 `d105039b056cf99386043f67c4ac0ad182afa98da181bd5557bed57a91a82bce`，與已審查實作檔案一致。

## Worker 續接與正常完成

`03:18:51Z` 的 Supervisor state 快照仍列出三個 running worker；切換前的直接 PID 檢查及 runner 完成收據顯示，當時只有 native drift 仍在執行。不能把這三筆都描述成「換版後保持同 PID 執行」。

| Run | 已驗證結果 |
| --- | --- |
| `claude-20260906T023108Z-bb62fc06` | native drift worker `1487838` 換版前後皆存活；`03:22:10Z` 仍 running、PPID 為 1，由新 Supervisor 繼續追蹤，未重啟。 |
| `claude-20260906T021655Z-4b09378e` | NLTK baseline worker `1472598` 已於切換前 `03:18:21Z` completed、exit 0。 |
| `antigravity-20260906T031733Z-e1e89175` | 收據 reviewer worker `1542109` 已於切換前 `03:18:54Z` completed、exit 0。 |

本次只替換 Supervisor，未清除 worker lease、quota cooldown 或歷史紀錄。

## 回滾與證據界線

本次未執行回滾。需要回滾時仍由 operator 使用同一 rollout primitive，從乾淨的前一版 `62dfc845925ed1b90bf06c0cbb490a0b82ed9b3a` source 配合同值的 `--tracking-ref` 執行；不是手動另設 link／launcher 流程。本次設定未改，前一 runtime 已支援相同的 Claude 5／Agy 5 設定，不涉及縮容。

未為驗收製造 live stale-failure 事件或修改 state；防護語意依已審查的 regression／CI 證據，不能宣稱本次另有真實故障重演。部署與 immutable closeout 未重跑測試。

本次未執行 GCP rollout、正式 credentials 變更、第三方來源啟用或 GCP provider-off live readback，不將 Supervisor 健康等同整套產品已上線。

脫敏原始收據：`/tmp/odp-supervisor-guard-rollout.eDW3C4/live-rollout-receipt.json`；SHA256：`fdb1dd2b8194d5dca9b9fe27ba4c5e454a420ac6231d7c0564c538adaf626e43`。私有 config／launcher 備份未複製進本文件。
