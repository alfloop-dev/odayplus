> 歷史快照：本文中的「目前／本次」以文內觀測時間為準；後續派工見 README.md。

ODP 派工續辦 SA／SD 與去重補充（2026-09-19）

使用者指示：「協助讓tasks盡量能夠派工出去，讓agy來做codex審核」。本次由 Codex 協調既有 Supervisor；產品程式由 Antigravity worker 交付。所有狀態變更均使用 AI_NAME=Codex 的 canonical ai-status CLI；不代寫 Human/Ops 裁決，不取消真正的來源、live 或 production 驗收。

SA：現況與根因

目前 18 個活躍追蹤項目、0 worker，唯一與 9/18 freeze 備份的 config 差異是 ready_dispatcher.enabled=false。已完成的 13 筆不得重開。來源 adapter/retained bridge 已完成，origin/dev=74f07cb09f263096ee9dc1db7132a058a1f17782 的 export_retained_import_bundle 呼叫僅出現在測試；scripts 無生產執行接線。實際捕取和 GCS retained raw 缺承接。來源 SA/SD 基線明文區分受控工程擷取與 production activation，不能把 production enabled 當作所有工程準備的先決條件。

SD：派工與驗收

1. 自動 owner/helper 僅派 antigravity；reviewer 僅派 codex。保留 Codex/Codex2 的原 account-pool 配置及真實 quota 訊號，不手改 runtime quota。按原 5 個 agy slots 上限運行；審查失敗依既有 failover 在 Codex 帳號內重派。
2. 先保留停派，透過 canonical CLI 重指派與補齊 metadata，驗證 config 後重啟原唯一 Supervisor，讓正式 dispatcher 派工。不得另開並行 supervisor。
3. Foundation 沿用 #1046，完全承接 9/17 既有路線(c)裁決：root 六檔恆納；module candidate 六個 module tf 全納 digest/proof_source；復原九個測試及兩組 candidate 反事實測試；重跑新 head 的真實收據。P0-2 已撤銷；P0-1/P1-3 仍必修。只把裁決點名 release_manifest.py 及 focused tests 加入有效 scope。
4. Bootstrap 沿用 #1314；把實際待改 production-authority-prerequisites.json 加入 scope；舊 verification 強制 status=cleared_per_human_adjudication 與已裁定的撤回要求矛盾，改為驗證撤回與來源分離，並保留原五項要求。不是豁免原驗收。重新產可執行 runner 收據，不重造核准。
5. XR 沿用 #1312，先清理不實收據宣稱與驗收層級映射。runtime 與原始物件證據仍由既有 rollout/#63 承接，缺口不假裝已過。需要裁決的 acceptance 衝突須交具體條款及替代依賴提案。
6. 新增 DPF-CAPTURE-RETENTION-RUNNER-001：僅補現有 kernel→compose→export→retention 的唯一操作入口、resume/preflight/readback，重用 #68，不重寫 adapters、registry 或 scheduler；離線測試與 dry-run 不需要 production activation。
7. 新增 DPF-BOUNDED-CAPTURE-RETENTION-EXECUTION-001：runner 合併後，以既有已授權 engineering acquisition lane 執行受控真實擷取，逐來源檢查實際允許範圍；在受治理 bucket 保留並 exact-generation 下載驗 hash/count。8 域齊備才滿足 full release；可先做符合範圍來源，逐項回報真缺口。不得以 synthetic/fixture 冒充 live，不啟用 production configmap。#63 依賴此 task，移除誤套的 production activation 前置；保留後续 activation 的來源許可條件。
8. 原 dev/staging/prod/watch/NFR/結案仍沿原 ID 接續；未完成 dependencies 保留。對已封存失效依賴，需有實際驗收承接才可更新，不能純刪除換可派工。
9. 三項業務功能維持既有已定案方向；尚缺實際資料/參數的 admission 不以本次派工指示補造。調整未來 owner/reviewer，不重建 contract-prep 任務。

去重與交付邊界

- Foundation/Bootstrap/XR 各沿原 PR。Foundation 不碰 canonical registry；bootstrap 只修自己的證據；XR 只修自己的稽核證據。
- 新 runner 僅填 #68 已明示未執行層級的操作接線；不重做 #67/#69–#73 的來源工程。execution 是 runtime 收據任務，與 runner 程式 PR 分開，#63 仍唯一擁有 masking/release snapshot。
- 人工許可、OAuth 選用、retention 到期清理不冒領为 AI 任務；聚合任務更新已完成 CDC/Nullable 不重新開發。
- 每條測試與完成宣稱綁真實命令/退出碼/提交；required CI、獨立 Codex review、merge/finalize 沿既有流程。

補充承接：ODP-DEV-RELEASE-GATE-RECONCILIATION-004 在 foundation/bootstrap 後整合新 candidate 與 gate 證據，原 dev task 保持唯一部署 owner。ODP-STAGING-RECOVERY-STORAGE-ACCEPTANCE-001 唯讀驗當前 storage readiness 並重用 foundation 收據，取代不能滿足的 archived superseded edge；每份實際 recovery bundle 生成/hash/restore 仍由原 staging rehearsal 驗收。Production/watch 的 manual-only hold 依本次使用者指示解除，Human GO/lease 與依賴保留。

額外可獨立執行：ODP-BRAND-CLASSIFICATION-SYNC-PRESERVATION-001 修復已實讀 store.py 的 ON CONFLICT brand_type=owned 覆寫；只擁有 data-platform store 與新增 DB 行為測試，不碰品牌轉移指標/SQL migration。原品牌轉移任務保留資料與參數 admission，增加此工程前置，避免缺業務數字阻塞可確定的同步缺陷。
