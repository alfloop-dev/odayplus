ODP 去重與交付邊界（2026-09-19）

本次先核對 18 筆既有任務、13 筆已完成任務及開啟中的 PR，新增 5 筆有獨立交付物的執行任務。文件發布另為行政交付，不增加產品實作範圍。

| 既有工作 | 本次處置與唯一交付責任 | 避免重複的方法 |
|---|---|---|
| Foundation #1046、Bootstrap #1314、XR #1312 | 原任務與原 PR 續辦 | 不另建相同修復 PR；只補已核准 scope／verification 與證據 |
| data-platform #67、#68、#69–#73 來源與 bridge | 保留已完成結果 | runner 只串既有 kernel→compose→export→retention，不重寫 adapter／registry／scheduler |
| DPF-CAPTURE-RETENTION-RUNNER-001 | 操作入口、resume/preflight/readback、focused tests | 程式交付，不冒稱實際 8 域 raw 已存在 |
| DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001 | runner 合併後的真實擷取、保留與 exact-generation 讀回 | 只交 runtime 證據；不實作第二個 runner、不產第二套 masking |
| data-platform #63 masked snapshot | 原 DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001 唯一擁有 masking 與 release snapshot | 改依賴受控 capture，保留 production activation 的逐來源許可驗收 |
| ODP-DEV-RELEASE-GATE-RECONCILIATION-004 | Foundation／Bootstrap 後整合 candidate／gate | 原 dev rollout 仍唯一擁有部署，不重做已完成 runtime release path |
| ODP-STAGING-RECOVERY-STORAGE-ACCEPTANCE-001 | 當前 storage readiness | 不重建 Terraform／bucket；每份 bundle 的生成、hash、restore 歸原 staging rehearsal |
| ODP-BRAND-CLASSIFICATION-SYNC-PRESERVATION-001 | store 同步保留既有 brand_type，獨立 DB 回歸 | 不碰原品牌轉移 SiteScore／轉移率／migration，後者仍需業務輸入 |
| CDC／Nullable 與其餘已完成 13 筆 | 不重開 | 詳見 STATUS-BEFORE.md 已完成清單 |
| Human 業務缺口彙整 | 追蹤具名輸入與決定 | 不是另一套功能實作；OAuth 關閉與 9/26 清理均非本次部署阻礙 |

owned_paths、forbidden_paths、acceptance、verification 與依賴原文見 execution-tasks-registered.json；前後依賴變化見 assignment-dependency-delta.json。已失效歷史依賴只有在具名驗收承接後更新，未以刪除真實 gate 換取派工。

作業隔離：由唯一 Supervisor 配置 task branch／worktree lease；不在同一 task 同時啟動第二個 owner。未提交實作由既有 recovery backup 留存並交原 owner 續辦，不能為清理工作樹而丟棄。公開文件沒有把尚未審查的產品 patch 當作完成品。
