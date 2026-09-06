# 38 個遺失任務的歷史驗收證據盤點（中立資料集）

- 產出任務：`ORCH-ARCHIVE-RECOVERY-EVIDENCE-001`（owner Claude2，reviewer Codex）
- 產出時間：2026-09-06T16:11:37Z
- 機讀資料：`docs/evidence/recovery/ORCH-ARCHIVE-RECOVERY-EVIDENCE-001/task_evidence_inventory.json`

> 本文件與同目錄 JSON 都**只是證據盤點**。不是 archive 收據、不是 `done` 宣告、不是 Human GO、不是部署授權。
> 本次未寫入任何 canonical 狀態：`ai-status.json`、`ai-task-archive/`、config、queue、Supervisor、watchdog 皆未變動；未跑測試、未 dispatch workflow、未讀 credential、未對外抓 provider 資料。

## 結論摘要

- 總數 **38**：建議 `verified_candidate` **35**、建議 `blocked` **3**。
- 信心：high **22**、medium **16**。
- 候選 PR 更正 **1** 筆（見下方「候選誤配更正」）。
- 38 個 ID 全部找得到本地 orchestrator task brief，因此**原始 acceptance／artifacts／owner／reviewer 都可復原**。
- 38 個候選 PR 全部 MERGED，精確 head 的 check runs 皆無 failure，且都有綁定精確 head 的 `task-review-gate=success` commit status，描述含當時指派的核准者姓名。
- 37/38 的 merge commit 在本地 `origin/dev` 歷史中（第 38 個是跨 repo 的 `oday-data-platform`）。

### 為什麼「PR 已合併」不等於「任務已完成」

三筆 `blocked` 全部不是因為 CI 或 PR 有問題，而是因為 **acceptance 中的 runtime／環境條款被該任務自己的收據證實未達成**：

- **`DPF-EMGI-LIVE-ROLLOUT-001`** — 該任務自身的 live-rollout-receipt.json 明列 missing_receipts=["rollback_receipt"]，與 acceptance『部署與 rollback receipts 綁定 digest』直接衝突。
- **`ODP-DEV-ROLLOUT-001`** — Acceptance『所有 components符合 release manifest digests』與『receipts綁定 exact SHA與 manifest』無真實證據。
- **`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`** — Acceptance『production environment存在且保護有效』未達成，且由該任務自身收據證實未達成。此為 fail-closed 正確行為，但不等於 acceptance 全數完成。

## 判定規則

- `verified_candidate`：候選映射一致（分支=task/<ID>、PR body task id 相符、head/merge 與清單相符、merge 在 dev 歷史）＋ 精確 head 無 failure 結論且 commit status rollup=success ＋ task-review-gate 具名核准者且非 owner ＋ 宣告 artifact 於 merge commit 全數存在 ＋ acceptance 無未被真實收據滿足的 runtime／外部啟用／人類授權條款。
- `blocked`：任一上述條件不成立，或有 acceptance 條款被該任務自身的收據證實未達成。
- 信心：high=所有可機檢項目皆通過且無語意爭議；medium=存在 skipped checks、artifact 清單與實際交付不符、身分來源不一致，或 acceptance 條款需 reviewer 語意裁定。

## 證據來源與其證明範圍

| 來源 | 位置 | 能證明 | 不能證明 |
|---|---|---|---|
| orchestrator task brief | `.orchestrator/task-briefs/`（本地唯讀） | 原始 title／owner／reviewer／acceptance／verification／artifacts／source documents | 不是遺失的 archive 記錄本身；`Status` 欄只反映最後一次生成當下的狀態 |
| 其他任務 brief 的 Dependencies | 同上 | 看板當時對本 ID 記載的狀態（終態旁證） | 不含核准細節或時間點 |
| GitHub PR metadata | 遠端唯讀 | 分支、head/merge SHA、合併時間、作者、PR body 的 ReviewBus 區塊 | 不證明部署或人類核准 |
| 精確 head 的 check-runs | 遠端唯讀 | 該 commit 上每個 check 的結論與時間 | 只到結論層級；未取 job log，不知測試實際覆蓋哪條路徑 |
| `task-review-gate` commit status | 遠端唯讀 | 綁定精確 head 的核准事實與核准者姓名 | 不證明 acceptance 全數達成 |
| 本地 git 歷史 | 本地唯讀 | head/merge 是否在 dev 歷史、commit trailers、merge commit 上的檔案樹與檔案內容 | 不證明 runtime 行為 |

## 已知限制

- 本地 ai-activity-log.jsonl 最早一筆記錄為 2026-09-06T11:01:40Z（事故之後）；38 個 ID 沒有任何一筆以其為 task_id 的歷史活動事件，因此無法從本地活動紀錄取得當時的 review／done 事件時間軸。
- ai-task-archive/tasks 僅存 6 筆（2 completed / 4 superseded），無一屬於本清單。
- CI 證據只到 check-run 結論層級；未取 job log，無法證明每個測試實際覆蓋了哪條程式路徑。
- PR 合併與 CI 綠燈只證明程式與檢查通過，不能單獨證明 runtime 部署、外部來源啟用或人類核准已發生。

## 候選誤配更正

### `XR-EXT-OSS-FINAL-AUDIT-001`

- 輸入清單候選：https://github.com/alfloop-dev/odayplus/pull/996（**誤配**）
  - 該 PR 的 head 分支是 `task/ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`，PR body 的 ReviewBus Task ID 是 `ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`；只是標題文字提及本任務。
  - 本任務宣告的三個 artifact 在該 merge commit 與目前 dev tip **皆不存在**。
- 更正後候選：https://github.com/alfloop-dev/oday-data-platform/pull/61
  - repo `alfloop-dev/oday-data-platform`、分支 `task/XR-EXT-OSS-FINAL-AUDIT-001`、state `MERGED`、merged `2026-08-24T05:54:54Z`
  - head `b1824c979aca008da10aed01fbc0c0a269c581dc`、merge `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`
  - 精確 head checks：7 個全部 `success`；commit status rollup `success`，`task-review-gate` 描述：Approved by assigned reviewer Codex
  - 三個宣告 artifact 均存在於該 repo。
- 影響：本 ID 的 repository 欄在輸入清單標為 `odayplus` 亦屬誤判，實際為 `oday-data-platform`。

## 逐項結果

| # | 任務 ID | 候選 PR | 建議 | 信心 | 主要缺口 |
|---|---|---|---|---|---|
| 1 | `DPF-EMGI-LIVE-ROLLOUT-001` | [oday-data-platform#62](https://github.com/alfloop-dev/oday-data-platform/pull/62) | `blocked` | high | 該任務自身的 live-rollout-receipt.json 明列 missing_receipts=["rollback_receipt"]，與 acceptance『部… |
| 2 | `ODP-DEV-ROLLOUT-001` | [odayplus#1013](https://github.com/alfloop-dev/odayplus/pull/1013) | `blocked` | high | Acceptance『所有 components符合 release manifest digests』與『receipts綁定 exact SHA與 manifest』無真實… |
| 3 | `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` | [odayplus#1011](https://github.com/alfloop-dev/odayplus/pull/1011) | `blocked` | high | Acceptance『production environment存在且保護有效』未達成，且由該任務自身收據證實未達成。此為 fail-closed 正確行為，但不等於 acc… |
| 4 | `ODP-AVM-DEPRECIATION-CONTRACT-001` | [odayplus#1148](https://github.com/alfloop-dev/odayplus/pull/1148) | `verified_candidate` | high | 無 |
| 5 | `ODP-CANONICAL-LEGACY-LINEAGE-001` | [odayplus#1150](https://github.com/alfloop-dev/odayplus/pull/1150) | `verified_candidate` | high | Acceptance『缺席率只用可辨識 source payload 或 snapshot 計算』屬文件內容正確性，本次只驗證存在性，未重算數值。 |
| 6 | `ODP-DEV-STAGED-GATE-RECONCILIATION-001` | [odayplus#1193](https://github.com/alfloop-dev/odayplus/pull/1193) | `verified_candidate` | medium | 精確 head 的 7 個 check 中有 3 個 conclusion=skipped（scope skip），代表部分 product 測試未在該 head 實跑。 |
| 7 | `ODP-DRIFT-DEP-REMOVE-002` | [odayplus#1222](https://github.com/alfloop-dev/odayplus/pull/1222) | `verified_candidate` | medium | 宣告 artifact tests/models/evidently_baseline_support.py 在 merge commit 與其第一父皆不存在；該 artifa… |
| 8 | `ODP-EPHEMERAL-STAGING-IAC-001` | [odayplus#1002](https://github.com/alfloop-dev/odayplus/pull/1002) | `verified_candidate` | medium | 候選 PR 未帶任何 docs/evidence/ 收據；acceptance『可重跑建立』若被解讀為需要真實 apply，則缺 runtime 證據。 |
| 9 | `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001` | [odayplus#1135](https://github.com/alfloop-dev/odayplus/pull/1135) | `verified_candidate` | high | Commit trailer 記 Reviewer: Codex，但 task-review-gate 與 brief 皆為 Claude；屬 trailer 過時，不影響核准… |
| 10 | `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` | [odayplus#1170](https://github.com/alfloop-dev/odayplus/pull/1170) | `verified_candidate` | high | Acceptance『Operator approval 的 production-entry 測試通過』屬測試語意，本盤點未重跑測試，僅依精確 head 的 7/7 CI s… |
| 11 | `ODP-INT-MANUAL-CORRECTION-AUDIT-001` | [odayplus#1175](https://github.com/alfloop-dev/odayplus/pull/1175) | `verified_candidate` | high | Acceptance 的 PostgreSQL readback 需 live DB；本盤點僅依 CI 結論，未重跑。 |
| 12 | `ODP-INT001-CDC-DISPOSITION-001` | [odayplus#1166](https://github.com/alfloop-dev/odayplus/pull/1166) | `verified_candidate` | medium | 需求本身仍 OPEN 待人類裁決；這是需求狀態而非任務缺陷，但下游不得據此視為 INT-001 已實作。 |
| 13 | `ODP-JOB-PARTIAL-DISPOSITION-001` | [odayplus#1172](https://github.com/alfloop-dev/odayplus/pull/1172) | `verified_candidate` | medium | 候選 PR head commit 無 LLM-Agent/Task-ID/Reviewer trailer（head 為 base advance merge commit）… |
| 14 | `ODP-LH-PREDICTION-DRIFT-001` | [odayplus#1154](https://github.com/alfloop-dev/odayplus/pull/1154) | `verified_candidate` | high | 無 |
| 15 | `ODP-LH003-BACKTEST-RELEASE-GATE-001` | [odayplus#1165](https://github.com/alfloop-dev/odayplus/pull/1165) | `verified_candidate` | high | 無 |
| 16 | `ODP-MEASUREMENT-CROSSLAYER-GATE-001` | [odayplus#1153](https://github.com/alfloop-dev/odayplus/pull/1153) | `verified_candidate` | medium | 精確 head 有 3 個 check conclusion=skipped，product 層測試未在該 head 實跑。 |
| 17 | `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` | [odayplus#1169](https://github.com/alfloop-dev/odayplus/pull/1169) | `verified_candidate` | high | Acceptance『找不到權威決策時 task 轉 blocked waiting Human/Ops』的負向行為未在本盤點重跑驗證。 |
| 18 | `ODP-MODELREADY-QUALITY-NULLABLE-001` | [odayplus#1168](https://github.com/alfloop-dev/odayplus/pull/1168) | `verified_candidate` | high | 無 |
| 19 | `ODP-NET002-LEASE-DISPOSITION-001` | [odayplus#1187](https://github.com/alfloop-dev/odayplus/pull/1187) | `verified_candidate` | medium | 同需求的 SEQUENCING member 為 DECIDED，decider 欄位為泛稱機構『Human/Ops (Architecture Board)』而非具名人員；此… |
| 20 | `ODP-NETPLAN-DISCLOSURE-UI-E2E-001` | [odayplus#1174](https://github.com/alfloop-dev/odayplus/pull/1174) | `verified_candidate` | medium | E2E 的 DOM 層行為只有 CI 結論可依；本盤點依規定未重跑測試。 |
| 21 | `ODP-OPS002-DECISION-COMMENTS-001` | [odayplus#1158](https://github.com/alfloop-dev/odayplus/pull/1158) | `verified_candidate` | medium | 身分不一致：task-review-gate 記核准者 Antigravity3、commit trailer 記 LLM-Agent Codex2 / Reviewer An… |
| 22 | `ODP-PRICE006-BANDIT-GATED-001` | [odayplus#1179](https://github.com/alfloop-dev/odayplus/pull/1179) | `verified_candidate` | high | commit trailer 的 Reviewer 記 Claude2，與 gate 的 Antigravity2 不同；以 gate 為準。 |
| 23 | `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` | [odayplus#1109](https://github.com/alfloop-dev/odayplus/pull/1109) | `verified_candidate` | high | Acceptance 提到『既有 Runtime Release deploy phase 只接受 immutable digest 與 signed Supervisor l… |
| 24 | `ODP-RELEASE-GATE-FIXTURE-STAGING-002` | [odayplus#1212](https://github.com/alfloop-dev/odayplus/pull/1212) | `verified_candidate` | high | Acceptance 提及前身 ODP-RELEASE-GATE-FIXTURE-STAGING-001 因 mutates_canonical 設定錯誤而只有唯讀檢查並被 s… |
| 25 | `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001` | [odayplus#1030](https://github.com/alfloop-dev/odayplus/pull/1030) | `verified_candidate` | high | Acceptance 綁定的 workflow run 33003734045 本身未在本盤點回查（唯讀 run 查詢未執行）。 |
| 26 | `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001` | [odayplus#1050](https://github.com/alfloop-dev/odayplus/pull/1050) | `verified_candidate` | high | 候選 PR head commit 無 task trailer（head 為 merge commit）。 |
| 27 | `ODP-REQ-DISPOSITION-GOVERNANCE-001` | [odayplus#1146](https://github.com/alfloop-dev/odayplus/pull/1146) | `verified_candidate` | high | Acceptance『負向測試證明 checker 會拒絕 AI 自簽與過期 waiver』依 CI 結論，未重跑。 |
| 28 | `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` | [odayplus#1221](https://github.com/alfloop-dev/odayplus/pull/1221) | `verified_candidate` | medium | 精確 head 有 3 個 check conclusion=skipped。 |
| 29 | `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001` | [odayplus#1206](https://github.com/alfloop-dev/odayplus/pull/1206) | `verified_candidate` | high | 無 |
| 30 | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` | [odayplus#1010](https://github.com/alfloop-dev/odayplus/pull/1010) | `verified_candidate` | medium | Acceptance『依序支援 dev/ephemeral staging/prod blue-green』與『不存在第二套 proof/deploy 狀態機』為 repo 級… |
| 31 | `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` | [odayplus#1041](https://github.com/alfloop-dev/odayplus/pull/1041) | `verified_candidate` | high | 候選 PR head commit 無 task trailer（head 為 merge commit）。 |
| 32 | `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` | [odayplus#1160](https://github.com/alfloop-dev/odayplus/pull/1160) | `verified_candidate` | medium | 兩個需求 member 待人類裁決；候選 PR head commit 無 task trailer。 |
| 33 | `ODP-SITESCORE-QUALITY-NULLABLE-001` | [odayplus#1171](https://github.com/alfloop-dev/odayplus/pull/1171) | `verified_candidate` | high | 無 |
| 34 | `ODP-SPEC-SOURCE-PROVENANCE-001` | [odayplus#1147](https://github.com/alfloop-dev/odayplus/pull/1147) | `verified_candidate` | medium | 身分不一致：task-review-gate 核准者 Antigravity、commit trailer 記 LLM-Agent Codex2 / Reviewer Anti… |
| 35 | `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001` | [odayplus#1208](https://github.com/alfloop-dev/odayplus/pull/1208) | `verified_candidate` | medium | 尚無核准的 recovery bundle 目的地；runtime 驗證（resource/IAM/vars）明確留給後續 staging rollout，本項只能作為 cod… |
| 36 | `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001` | [odayplus#1164](https://github.com/alfloop-dev/odayplus/pull/1164) | `verified_candidate` | high | 候選 PR head commit 無 task trailer（head 為 merge commit）。 |
| 37 | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` | [odayplus#1096](https://github.com/alfloop-dev/odayplus/pull/1096) | `verified_candidate` | medium | 另兩個宣告 artifact『security E2E receipt』與『auth migration rollout checklist』是敘述而非檔案路徑，無法定位對應交… |
| 38 | `XR-EXT-OSS-FINAL-AUDIT-001` | [oday-data-platform#61](https://github.com/alfloop-dev/oday-data-platform/pull/61)（更正） | `verified_candidate` | medium | Acceptance『資料授權決定則逐來源列為待具名人員決定』與『技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴』涉及人類授權，本盤點不… |

## 逐項明細

### `DPF-EMGI-LIVE-ROLLOUT-001` — blocked（信心 high）

- 標題：發布 exact-digest data platform 並完成 EMGI sources-off runtime
- 原 owner／reviewer：`Codex` ／ `Codex2`；brief 生成於 2026-08-25T14:20:46Z，brief 內 Status=`review`
- brief 宣告 SHA256：`4f066c78136597fb93629563c350ebc5567111921b85b790f2e50fa55e1c8770`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`151d7d108c94579cd5b2ba01401eb608344f999d16ce6ba911853fd6b5074f67`
- 候選：PR #62，head `71ecbe0d982f3f93c976fa0104a82903d2a071cf`，merge `e3ecd2f199fe051aba8d3d33005c217036a9c88e`，merged 2026-08-25T14:24:47Z，base `dev`
- 映射一致性：`mismatch`（分支=task/DPF-EMGI-LIVE-ROLLOUT-001；PR body task id=None；merge 在本地 dev 歷史=None）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-08-25T14:24:40Z）；與 brief reviewer 相符=True
- acceptance 性質：runtime 部署／外部來源啟用
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：4 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 跨 repo：候選 PR 在 alfloop-dev/oday-data-platform，merge commit 不在 odayplus 本地歷史，只有 GitHub 唯讀 metadata 與檔案內容可查。
- docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/live-rollout-receipt.json 的 image digest 為真實值 sha256:4f603e3a…（非 placeholder）。
- sources-off-readback.json 顯示 governed/declared/disabled 皆 16，approval receipt 全空，符合『16 sources false 且 receipts 空』。

**缺口**

- 該任務自身的 live-rollout-receipt.json 明列 missing_receipts=["rollback_receipt"]，與 acceptance『部署與 rollback receipts 綁定 digest』直接衝突。

**建議的最小下一步驗證**

- 唯讀取回綁定 image digest sha256:4f603e3acff7a35876fd59593e6725ee0b00226e546b0694eb5e80a12b097e9e 的 rollback receipt；若確認不存在，本項維持 blocked 且不得以部署成功推定 rollback 已驗證。

### `ODP-DEV-ROLLOUT-001` — blocked（信心 high）

- 標題：以同一 release digests 部署資料平台與 ODay Plus dev
- 原 owner／reviewer：`Antigravity2` ／ `Codex`；brief 生成於 2026-08-25T17:32:53Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`80edb285c2b456f1e949c285b5cb55d6216f28eb05c65d31d2a54cea6b35243c`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`39ad59a8ca66dec32ef05894567e11054e89c78f70c8a7c0d0810a62cbe79aea`
- 候選：PR #1013，head `83944bb50c56a5071c992edb28e96eae3155f4c0`，merge `b8262d911c95887767877e7cee23bded0ef7dd61`，merged 2026-08-25T17:31:52Z，base `dev`
- 映射一致性：`consistent`（分支=task/ODP-DEV-ROLLOUT-001；PR body task id=ODP-DEV-ROLLOUT-001；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-08-25T17:06:40Z）；與 brief reviewer 相符=True
- acceptance 性質：runtime 部署／外部來源啟用
- 宣告 artifact 1 項，merge commit 上缺 0 項
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 本次獨立複驗確認 docs/evidence/runtime/ODP-DEV-ROLLOUT-001/odayplus-dev-deployment.json 的五個 image digest 皆為 sha256:1111…/2222…/4444…/5555…/6666… 重複位元佔位值，卻同時標記 status=READY / SUCCEEDED。
- 同檔 candidate_sha e496be62c47c45d758681b8a4d3abfae16f1c96d 確實在 origin/dev 歷史中，因此問題不在 candidate 不存在，而在 component digest 造假。
- 同期真實 manifest（ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001）使用 asia-east1-docker.pkg.dev/… 的真實 digest，與本收據的 ghcr.io 佔位值不同源。

**缺口**

- Acceptance『所有 components符合 release manifest digests』與『receipts綁定 exact SHA與 manifest』無真實證據。
- Acceptance『dev integration/contract/provider-off readback通過』所依據的 dev-integration-readback.json 僅列本機 .odp_data 路徑，無法單獨證明真實 runtime readback。

**建議的最小下一步驗證**

- 唯讀取回 Cloud Run 服務 oday-plus-dev-api / -web 與 job -migration / -worker / -scheduler 的實際 revision image digest；與 RELEASE_MANIFEST.json 對帳。若取不到，記為『部署未經證實』，不得回填 done。

### `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` — blocked（信心 high）

- 標題：建立 staging/production GitHub 與 GCP 環境保護
- 原 owner／reviewer：`Claude2` ／ `Antigravity2`；brief 生成於 2026-08-25T16:42:37Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`692bd98c346b953a220b144196f04517f2a30d5bcd16c5dca821ff581115dbcd`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`d782728205d4469bbe82bedb1a6ca56e24b6b87820a619c570e98448173f285f`
- 候選：PR #1011，head `5edcf009640ae31dde160b1ce4c9123ac2c31a2f`，merge `8ad3f10dab707711bc1394ff3f6a81042b0b5648`，merged 2026-08-25T16:26:32Z，base `dev`
- 映射一致性：`consistent`（分支=task/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001；PR body task id=ODP-GITHUB-GCP-ENV-BOOTSTRAP-001；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity2`（2026-08-25T16:00:33Z）；與 brief reviewer 相符=True
- acceptance 性質：外部來源啟用／人類授權
- 宣告 artifact 2 項，merge commit 上缺 0 項
- 終態旁證：2 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 交付物自帶 production-authority-prerequisites.json，status=blocked_pending_human_authority，列出 PROD-GCP-01…PROD-OPS-05 五項待人類決定。
- github-variables-audit.json 明載 production 變數刻意省略以免造假，符合 acceptance『缺少人類 authority 時明確 blocked而非填 placeholder』。

**缺口**

- Acceptance『production environment存在且保護有效』未達成，且由該任務自身收據證實未達成。此為 fail-closed 正確行為，但不等於 acceptance 全數完成。

**建議的最小下一步驗證**

- 唯讀查詢 GitHub repo environments 是否存在 production 及其 protection rules；若不存在，本項終態應為 blocked 而非 done。

### `ODP-AVM-DEPRECIATION-CONTRACT-001` — verified_candidate（信心 high）

- 標題：定義 AVM 資產折舊契約、版本化與舊估值卡處置
- 原 owner／reviewer：`Claude2` ／ `Antigravity6`；brief 生成於 2026-09-04T12:11:49Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`99e7a1fd42c990c3b3a3de40e36e80f2ad259cc7bc984dc2c1b2567d7ca445bb`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`2a9f0e939e1d72e99ff6fb0f1dcf44c5133d41035ee51d8b431df3d8ff4c5a72`
- 候選：PR #1148，head `aacc6ca19c12e6e9e17a350dbafe76c25e4b3bca`，merge `739cbab19a171ef8d40e0f68f2b428f6b726bac0`，merged 2026-09-04T12:08:34Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-AVM-DEPRECIATION-CONTRACT-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity6`（2026-09-04T12:10:24Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 2 項，merge commit 上缺 0 項

**已核對事實**

- Acceptance 全為設計契約與測試規格；宣告的兩個 artifact 在 merge commit 均存在且由候選 PR 觸及。

### `ODP-CANONICAL-LEGACY-LINEAGE-001` — verified_candidate（信心 high）

- 標題：量測 canonical 六模型的 producer lineage 與 legacy 1.0 遷移風險
- 原 owner／reviewer：`Codex2` ／ `Antigravity`；brief 生成於 2026-09-03T15:39:15Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`b007af294644b9dea9df8f5c8d376f0534d4f56d6344478fb0e32743030ecdd7`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`0ea9ba8bd00970214a9883f32a623045f193b32736a5308e72fb2c0eae51a59e`
- 候選：PR #1150，head `4ffa3122e47029d5143762413ae165d5b8d3c500`，merge `865ff82e817cc56ffff0187f33cd6d3e8718958b`，merged 2026-09-03T15:37:27Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-CANONICAL-LEGACY-LINEAGE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity`（2026-09-03T15:11:43Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 1 項，merge commit 上缺 0 項

**已核對事實**

- 交付物為單一 evidence 文件，merge commit 與 dev tip 皆存在。

**缺口**

- Acceptance『缺席率只用可辨識 source payload 或 snapshot 計算』屬文件內容正確性，本次只驗證存在性，未重算數值。

**建議的最小下一步驗證**

- 若需更高信心，由 reviewer 讀 docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md 檢查 denominator 是否明載。

### `ODP-DEV-STAGED-GATE-RECONCILIATION-001` — verified_candidate（信心 medium）

- 標題：重整 staged dev release gate 與現行 artifact 的 exact reconciliation
- 原 owner／reviewer：`Antigravity5` ／ `Claude2`；brief 生成於 2026-09-04T09:48:15Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`c8758eab7f1b4e4144c17c96c7a13512dab0a48c4281e9506654d4ccbaa5e4ec`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`16e5e241fa39511b00c68e244936c4d49217fbb518db272aa71a8dd63e058149`
- 候選：PR #1193，head `40bb02462ec742cfb615214c1873f15a40b2c602`，merge `efdfea1a0c31c3c9ebcb2acfe1eb61dc4fa2bff6`，merged 2026-09-04T09:47:46Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-DEV-STAGED-GATE-RECONCILIATION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'skipped': 3, 'success': 4}；skipped：performance-gate, product-e2e-gate, product
- 核准證據：`task-review-gate`=`success`，核准者 `Claude2`（2026-09-04T09:20:32Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- Acceptance 明文『本 task 不簽 lease 不 dispatch deploy』，屬純 gate/registry 程式交付；六個宣告 artifact 在 merge commit 全數存在。

**缺口**

- 精確 head 的 7 個 check 中有 3 個 conclusion=skipped（scope skip），代表部分 product 測試未在該 head 實跑。
- Acceptance 第一條『等待 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 合併後才選定 candidate』的時序未在本盤點驗證。

**建議的最小下一步驗證**

- 列出被 skip 的 check 名稱並確認其為 change-scope 設計行為；比對 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 的 merge 時間是否早於本 PR head。

### `ODP-DRIFT-DEP-REMOVE-002` — verified_candidate（信心 medium）

- 標題：原子切換原生監控並移除 Evidently／NLTK 生產依賴
- 原 owner／reviewer：`Codex` ／ `Antigravity4`；brief 生成於 2026-09-06T05:21:45Z，brief 內 Status=`review`
- brief 宣告 SHA256：`5f6173719d8b4b7fce152fe20d1cae55b490f373470c70207d846ab23d0aa838`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`2f3ab9504a08be11d907f3913f5ef7462884d5d1a7c0c1480dbb8df72fa37f5d`
- 候選：PR #1222，head `6d438486c8645e476fcf86b5f675a5ca20592b9d`，merge `66244b30c2615dcad8373e03fff096e298d21e98`，merged 2026-09-06T05:56:17Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-DRIFT-DEP-REMOVE-002；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity4`（2026-09-06T05:31:36Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 25 項，merge commit 上缺 1 項：`tests/models/evidently_baseline_support.py`

**已核對事實**

- 在 merge commit 上直接複驗：uv.lock 與 pyproject.toml 完全不含 evidently / nltk（grep 命中數 0），符合『不得留在 production 或 dev scope』。
- docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/ 內有 pip-audit 的 stdout/stderr 原始輸出與 candidate/committed/integrated 三組 audit 與 inputs JSON，非 fixture 產物。

**缺口**

- 宣告 artifact tests/models/evidently_baseline_support.py 在 merge commit 與其第一父皆不存在；該 artifact 條目與實際交付不符（疑為改名或未建立）。
- Acceptance 第 7 條明文本 task 不能代替 PR1188 的 approval/merge，該部分本質上在此任務範圍外。

**建議的最小下一步驗證**

- 確認 evidently_baseline_support.py 是否改名為其他 baseline 支援檔；若無對應檔，修正 artifact 清單而非視為缺陷。

### `ODP-EPHEMERAL-STAGING-IAC-001` — verified_candidate（信心 medium）

- 標題：實作 ephemeral staging 建立、隔離、TTL 與安全清理
- 原 owner／reviewer：`Claude2` ／ `Codex2`；brief 生成於 2026-08-24T20:00:04Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`3d8930d8de935582ffed3af3f2b96d1a356787916cc6c555c5220ddd60778c59`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`6c27b956de1db55ada21a8f20825aedc03f92e3f7ed8dfeb1a31d829c6739376`
- 候選：PR #1002，head `ee6eddb6951aea752b857183a484ebc22ddf7772`，merge `82ed6a05cf67c6c0e43f6f5b4219882251a87c42`，merged 2026-08-24T19:59:40Z，base `dev`
- 映射一致性：`consistent`（分支=task/ODP-EPHEMERAL-STAGING-IAC-001；PR body task id=ODP-EPHEMERAL-STAGING-IAC-001；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-08-24T19:36:58Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：2 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 宣告 artifact 為 infra/terraform/、product_ops/deployment/staging_lifecycle.py、tests/ops/，皆為程式交付且在 merge commit 存在。

**缺口**

- 候選 PR 未帶任何 docs/evidence/ 收據；acceptance『可重跑建立』若被解讀為需要真實 apply，則缺 runtime 證據。

**建議的最小下一步驗證**

- 請 reviewer 裁定該 acceptance 為 IaC 能力交付或需 live apply；若為後者則需 staging apply/cleanup 收據，本盤點查無。

### `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001` — verified_candidate（信心 high）

- 標題：定義並實作首次 dev release 的 fail-closed recovery admission
- 原 owner／reviewer：`Antigravity4` ／ `Claude`；brief 生成於 2026-09-02T14:17:21Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`27eefc7f69e27bd7f31c9fa0eff63a888a164c0138bf51e4dfb378bce62a1891`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`1edd7a93cc4deed0da9be44502a8a1a5f38d30f04f40b806488c78bed9dd38e0`
- 候選：PR #1135，head `f24003a7e18a48c441252ea67beb16d7285c4614`，merge `dd7013df830cdabe33d1b09e093dd728318d50f5`，merged 2026-09-02T14:15:41Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Claude`（2026-09-02T13:55:14Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 1 項，merge commit 上缺 0 項
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- Acceptance 明文『只改既有 Runtime Release/toolchain』與『補 focused positive/negative tests 與中文 evidence，CI 綁定 exact head』；docs/evidence/runtime/ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001/ 於 merge commit 存在，精確 head 7/7 success。

**缺口**

- Commit trailer 記 Reviewer: Codex，但 task-review-gate 與 brief 皆為 Claude；屬 trailer 過時，不影響核准事實。

### `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` — verified_candidate（信心 high）

- 標題：依 HZ-004 實績契約實作 heat-zone merge／split
- 原 owner／reviewer：`Antigravity2` ／ `Claude`；brief 生成於 2026-09-05T16:08:31Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`4f693296d798f7f791f7b38b0cb2ed4f77a1d755b2a84c0c562725a45e88af7d`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`aa4e78d7fb56fd86413b0803f604078cc711d9ddd33428d18c200e373bc2d69a`
- 候選：PR #1170，head `0585dd49976ebf269248067d4adfd56e278157ae`，merge `eed8d51bb8a18c677baa25ab30a536cc576ae5fe`，merged 2026-09-05T16:07:23Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Claude`（2026-09-05T15:43:12Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 五個宣告 artifact（modules/heatzone、apps/api、apps/web/features/operator、infra/db/migrations、tests）在 merge commit 全存在且由候選 PR 觸及。

**缺口**

- Acceptance『Operator approval 的 production-entry 測試通過』屬測試語意，本盤點未重跑測試，僅依精確 head 的 7/7 CI success。

### `ODP-INT-MANUAL-CORRECTION-AUDIT-001` — verified_candidate（信心 high）

- 標題：建立 INT-006 人工校正寫入、授權、稽核與 rollback
- 原 owner／reviewer：`Antigravity` ／ `Codex2`；brief 生成於 2026-09-05T04:08:18Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`862407d887730a08c6bbef79987a21c3b0e9e466a2b1ac1b9c161385613f9f21`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`cd5ec5b7a1bfed32db52e507c4a4d3088ae11d4820d8040c0950a60aba4ee504`
- 候選：PR #1175，head `0b3e1987d426200928cc5ec62dfd8de4f8d81358`，merge `f02960fa81c2beeef88dd213e86ec96c8380b2d8`，merged 2026-09-05T03:53:16Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-INT-MANUAL-CORRECTION-AUDIT-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-09-05T03:33:39Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 7 項，merge commit 上缺 0 項

**已核對事實**

- 七個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success，含 product 與 product-e2e-gate。

**缺口**

- Acceptance 的 PostgreSQL readback 需 live DB；本盤點僅依 CI 結論，未重跑。

### `ODP-INT001-CDC-DISPOSITION-001` — verified_candidate（信心 medium）

- 標題：依 upstream 證據實作 CDC connector 或正式處置 INT-001
- 原 owner／reviewer：`Codex` ／ `Antigravity7`；brief 生成於 2026-09-03T20:51:14Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`6b95285af5e46b0675b6ce109f5c156e948fe640ef31e80ef428d7c40a12ac38`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`a7befd2c95a80f997c4f0c7e1bb404e31037502b18e4a157c2999fd56a1f0fc8`
- 候選：PR #1166，head `39289e4207b29d2c7adea071f42901f9f0b5ae20`，merge `0f35515ed15aad36c496f30973b2e2ce9fa2b026`，merged 2026-09-03T20:50:26Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-INT001-CDC-DISPOSITION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity7`（2026-09-03T20:07:47Z）；與 brief reviewer 相符=True
- acceptance 性質：人類授權
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- 在 merge commit 的 set_valued_requirements.json 中，ODP-FR-INT-001 的 CDC member 為 status=absent、disposition.state=OPEN，並帶 formal_handback_ref 與 assigned_to／next_review_date，無 decider 欄位。
- 符合 acceptance『無適用 upstream 時不新增 connector 且 AI 不自簽 waiver』：未出現 AI 自簽的 decider。

**缺口**

- 需求本身仍 OPEN 待人類裁決；這是需求狀態而非任務缺陷，但下游不得據此視為 INT-001 已實作。

### `ODP-JOB-PARTIAL-DISPOSITION-001` — verified_candidate（信心 medium）

- 標題：依 producer 證據補 PARTIAL 狀態轉移或正式處置 SHARED-001
- 原 owner／reviewer：`Antigravity6` ／ `Claude`；brief 生成於 2026-09-04T13:16:07Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`a9e27409a278cf816439817b489881461c8d8ca286f52d33a7b959b188afe59b`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`83653dfcbf44eec551c2905bcde6293fbdd74ec825830e0a092702146c475bec`
- 候選：PR #1172，head `f8caf62e11643f9cbe59ea6b958faeab746fcac2`，merge `9647d673ccf2c0f11ef565e78511099821d85c19`，merged 2026-09-04T13:14:12Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-JOB-PARTIAL-DISPOSITION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Claude`（2026-09-04T12:50:50Z）；與 brief reviewer 相符=True
- acceptance 性質：人類授權
- 宣告 artifact 5 項，merge commit 上缺 0 項

**已核對事實**

- ODP-FR-SHARED-001 的 PARTIAL member 為 BLOCKED_BY_EVIDENCE，帶 handback_id、formal_handback_ref、reopen_trigger、history，無自簽 decider，符合 acceptance『formal disposition 缺人類簽署仍保持未結案』。

**缺口**

- 候選 PR head commit 無 LLM-Agent/Task-ID/Reviewer trailer（head 為 base advance merge commit）；owner/reviewer 以 task-review-gate 與 brief 為準。
- handback HB-SHARED001-PARTIAL-001 尚待 Human/Ops 簽署。

**建議的最小下一步驗證**

- 若 reviewer 要求 trailer 佐證，改查該分支最後一個非 merge commit。

### `ODP-LH-PREDICTION-DRIFT-001` — verified_candidate（信心 high）

- 標題：實作 LearningHub prediction drift 的 cohort、持久化、DecisionPolicy 與告警
- 原 owner／reviewer：`Codex` ／ `Antigravity3`；brief 生成於 2026-09-03T14:15:09Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`3575610026103186c7fe4b7f73fe325484142c0084b47403e1d2d8a559554317`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`8d30f00b89825873dab61f60a0ee254ce6ee459364c27748f402846c5d7da747`
- 候選：PR #1154，head `a7f7b7d9d174b45f937d1fb0d12e2c962c97adad`，merge `0cbc5330f6a076344d2dff32370dedae5b82da4a`，merged 2026-09-03T13:07:52Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-LH-PREDICTION-DRIFT-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity3`（2026-09-03T12:39:31Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

### `ODP-LH003-BACKTEST-RELEASE-GATE-001` — verified_candidate（信心 high）

- 標題：把 LearningHub Backtest 接成版本化 model release gate
- 原 owner／reviewer：`Codex` ／ `Antigravity3`；brief 生成於 2026-09-03T21:54:11Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`810995465b172dc769902a81a28a19e272aebf01cab791512fbdccefc4a28aea`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`77546489ad0b079df7ac2e383347f03598e034a4f7563afebc6430f56b0c668d`
- 候選：PR #1165，head `ce7a3fadd758ca19b5ebbc632e3d8aa523003f65`，merge `d3e344353ad4280962ce25078c067f14c9a6df10`，merged 2026-09-03T21:53:56Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-LH003-BACKTEST-RELEASE-GATE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity3`（2026-09-03T21:26:50Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項

**已核對事實**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

### `ODP-MEASUREMENT-CROSSLAYER-GATE-001` — verified_candidate（信心 medium）

- 標題：把量測缺席檢查擴到 Pydantic、mapper、SQL/dbt、OpenAPI 與 TS
- 原 owner／reviewer：`Claude2` ／ `Antigravity4`；brief 生成於 2026-09-03T15:58:06Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`43f8232d0113031304dac7953ce364562c19e9f0634e21e0ca45e3073bbff8eb`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`ca4532ee864bf87774356543c0d9e4f8ffaf7fecb2b38c57c581a609f465aebf`
- 候選：PR #1153，head `aaa9aaee9380d50ae49544e03ea0a9bdaf8e86a2`，merge `8479567d66d9d08d0aa4a27d9f0a901ab092f2ae`，merged 2026-09-03T12:33:14Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-MEASUREMENT-CROSSLAYER-GATE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'skipped': 3, 'success': 4}；skipped：product, product-e2e-gate, performance-gate
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity4`（2026-09-03T12:28:23Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 4 項，merge commit 上缺 0 項

**已核對事實**

- checker、測試、豁免清單與 .github/workflows/ci.yml 四個 artifact 在 merge commit 全存在且由候選 PR 觸及。

**缺口**

- 精確 head 有 3 個 check conclusion=skipped，product 層測試未在該 head 實跑。

**建議的最小下一步驗證**

- 確認被 skip 的 check 是否為 change-scope 設計行為。

### `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` — verified_candidate（信心 high）

- 標題：補齊 merge queue 不實作決定的正式可稽核欄位
- 原 owner／reviewer：`Claude2` ／ `Antigravity4`；brief 生成於 2026-09-03T18:21:51Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`77f373e9b8a8b68830b8f209043ac4ff7fd4b5c25ffa09ce9a8b2884b8cb3d9d`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`b78a2dcdba66ef3c713c47299fb413ef451d85031632fe0965d5f25cc108a1c2`
- 候選：PR #1169，head `f2d1bd88ec2dc89148dd3d7346a01de0e921432b`，merge `830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e`，merged 2026-09-03T18:10:43Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity4`（2026-09-03T17:44:08Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 2 項，merge commit 上缺 0 項

**已核對事實**

- 兩個 artifact（set_valued_requirements.json、ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md）在 merge commit 存在。

**缺口**

- Acceptance『找不到權威決策時 task 轉 blocked waiting Human/Ops』的負向行為未在本盤點重跑驗證。

### `ODP-MODELREADY-QUALITY-NULLABLE-001` — verified_candidate（信心 high）

- 標題：ModelReadyRecord nullable mapper 與 dataset snapshot admission fail-closed
- 原 owner／reviewer：`Codex` ／ `Antigravity5`；brief 生成於 2026-09-03T20:35:02Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`5f526405eb8999404d45dd0594016f36a83cdc43c0eff863b0bbe5ddab4691b3`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`73bda16f8d3e444acb589e0ba176b3516c556140cd86a62d44bdbca04a5e49b5`
- 候選：PR #1168，head `a2a8d9c235e88820572b30700d5f5cc9b31bb0af`，merge `10d47d17a7b3ca8d3c655afaaf02b931c3fb7e0f`，merged 2026-09-03T20:32:14Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-MODELREADY-QUALITY-NULLABLE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity5`（2026-09-03T19:52:25Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項

**已核對事實**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

### `ODP-NET002-LEASE-DISPOSITION-001` — verified_candidate（信心 medium）

- 標題：依租約資料證據實作 per-option feasibility 或正式處置 NET-002 LEASE
- 原 owner／reviewer：`Codex2` ／ `Antigravity6`；brief 生成於 2026-09-04T12:36:01Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`ae7bf45dcd0e7e11e58a3c2d5d316d6c5f63e88cbd89172fb6a2fd38e73a6b37`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`50c45e3b8a8ef4d9bced3e22b96fbf9e8f4f75a0d82a16b8bb648de0e9484f26`
- 候選：PR #1187，head `3bd82e8a401d463ef8301671620d45fc93b6127b`，merge `c65bb54dd1171c8c878d37282470ada6a18f9ed6`，merged 2026-09-04T12:35:05Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-NET002-LEASE-DISPOSITION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity6`（2026-09-04T12:12:18Z）；與 brief reviewer 相符=True
- acceptance 性質：人類授權
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- ODP-FR-NET-002 的 LEASE member 為 BLOCKED_BY_EVIDENCE（無 decider），符合『資料不 ready 時只留待人類簽署的 formal disposition』。

**缺口**

- 同需求的 SEQUENCING member 為 DECIDED，decider 欄位為泛稱機構『Human/Ops (Architecture Board)』而非具名人員；此為既知的 decider 泛稱漏洞，reviewer 需自行裁定是否接受。
- 候選 PR head commit 無 task trailer（head 為 merge commit）。

**建議的最小下一步驗證**

- 若要求具名裁決者，查 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#odp-fr-net-002-sequencing 是否有具名簽署。

### `ODP-NETPLAN-DISCLOSURE-UI-E2E-001` — verified_candidate（信心 medium）

- 標題：在 Operator UI 顯示 NetPlan 未建模限制並完成 approval E2E
- 原 owner／reviewer：`Claude` ／ `Antigravity`；brief 生成於 2026-09-04T05:49:37Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`2017dbd9c16647fedf2930248a72677a71e9f64b6c9be92dd8566184db53a263`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`3b5dd11c5e0153effbfdeead057cf60c4f1b382e89dcef48fbb6474222e86dc5`
- 候選：PR #1174，head `c71599aa116e1bd5a73bf0be005b34163bd0405e`，merge `1edb2f834cbf38ccd489cd999802098076e891b7`，merged 2026-09-04T05:49:33Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-NETPLAN-DISCLOSURE-UI-E2E-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity`（2026-09-04T04:57:57Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 的 product-e2e-gate check 為 success。

**缺口**

- E2E 的 DOM 層行為只有 CI 結論可依；本盤點依規定未重跑測試。

### `ODP-OPS002-DECISION-COMMENTS-001` — verified_candidate（信心 medium）

- 標題：補齊 OPS-002 與任務／決策綁定的 durable comments
- 原 owner／reviewer：`Antigravity2` ／ `Codex`；brief 生成於 2026-09-03T15:13:32Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`1ce69407909e69dd05ae20963a4f083d24d09164e10c64289abb771501b8c812`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`e8de9870bf6d530bcb137b48b70d1e392099fc5253335e41d32a94ed0fba78e7`
- 候選：PR #1158，head `54e8c8f37d9de6f3e60f423dd22a8ffb7f3a76f0`，merge `c1371572af637ab1598370a495a126a4cddfba65`，merged 2026-09-03T14:36:11Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-OPS002-DECISION-COMMENTS-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity3`（2026-09-03T14:04:50Z）；與 brief reviewer 相符=False
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- 六個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 身分不一致：task-review-gate 記核准者 Antigravity3、commit trailer 記 LLM-Agent Codex2 / Reviewer Antigravity3，但 task brief（2026-09-03 產生）記 owner Antigravity2 / reviewer Codex。推定為核准後 reviewer 輪替導致 brief 覆寫，需 reviewer 裁定以何者為準。

**建議的最小下一步驗證**

- 以 task-review-gate 的時間戳為準判定當時核准者身分。

### `ODP-PRICE006-BANDIT-GATED-001` — verified_candidate（信心 high）

- 標題：同一交付完成 PRICE-006 Bandit 與 production activation Gate
- 原 owner／reviewer：`Codex2` ／ `Antigravity2`；brief 生成於 2026-09-04T03:38:26Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`2bd3a431bef5f72ea656d533f1145c33780b34e1f72bcd473dbc727d334de6b5`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`df1916986beadd514c5ed8a4c24d7f19ca9e2290c5042f03ff498c30d642a748`
- 候選：PR #1179，head `282a88fb882cce53c88ef8ac3369ea0676c3d20d`，merge `470f495d957edc38d330a624cf4d56618cbfb4e2`，merged 2026-09-04T03:35:17Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-PRICE006-BANDIT-GATED-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity2`（2026-09-04T02:42:26Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- 六個宣告 artifact 在 merge commit 全存在；task-review-gate 核准者 Antigravity2 與 brief reviewer 一致。

**缺口**

- commit trailer 的 Reviewer 記 Claude2，與 gate 的 Antigravity2 不同；以 gate 為準。

### `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` — verified_candidate（信心 high）

- 標題：修正 Runtime Release build handoff 的 masked snapshot 與 rollback wiring
- 原 owner／reviewer：`Claude2` ／ `Antigravity3`；brief 生成於 2026-09-01T14:52:12Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`c3f3aed17977a7f85b26c6dd9112092a2a8dc17f449cfd0b269f518c0fa29971`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`5ab278ca6051200fdbce3658f7689765804cfb469c6af4085fee7b4e5fb3ba79`
- 候選：PR #1109，head `bdd277568d930757243583336a577a2492b67ca6`，merge `640e35415aa33d5d53af21a8a527431b8f751cea`，merged 2026-09-01T14:49:31Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity3`（2026-09-01T14:24:32Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 1 項，merge commit 上缺 0 項
- 終態旁證：3 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- docs/evidence/runtime/…/ 內有 verification-transcript.txt；掃出的 sha256:dddd…/eeee… 佔位字串出現在 pytest 參數化測試名稱（fail-closed 負向案例），非偽造的部署收據值。

**缺口**

- Acceptance 提到『既有 Runtime Release deploy phase 只接受 immutable digest 與 signed Supervisor lease』屬既有機制不變性，本盤點未重驗。

### `ODP-RELEASE-GATE-FIXTURE-STAGING-002` — verified_candidate（信心 high）

- 標題：實作 gate E2E fixture 解耦，接續只有唯讀檢查的001
- 原 owner／reviewer：`Codex2` ／ `Codex`；brief 生成於 2026-09-06T01:58:26Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`409030a5af8192758638626f3c3efb35e093c52082fde231aaf959b9746e5160`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`77b5784716b7e4197f52d237dc7ebf5f00da384e44dcc5cf2ccd906238b7221d`
- 候選：PR #1212，head `a9e7853f9ed910303eb6b7fa1d81c1e1b46c5787`，merge `17393dd447e9b25978a83641cabf7cd77951f24a`，merged 2026-09-06T01:54:20Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-RELEASE-GATE-FIXTURE-STAGING-002；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-09-06T01:58:14Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 2 項，merge commit 上缺 0 項

**已核對事實**

- 兩個 artifact 在 merge commit 存在；acceptance 明文『不聲稱測試綠就是已部署或 GO』，屬純測試 fixture 解耦。

**缺口**

- Acceptance 提及前身 ODP-RELEASE-GATE-FIXTURE-STAGING-001 因 mutates_canonical 設定錯誤而只有唯讀檢查並被 supersede；該前身不在本 38 項清單內，映射時勿混用。

### `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001` — verified_candidate（信心 high）

- 標題：整理真實 build artifact 與 gate registry 的 exact binding，維持 fail-closed no-go
- 原 owner／reviewer：`Claude2` ／ `Codex`；brief 生成於 2026-08-26T20:34:33Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`53fdca34735e8bbf056ae9b09e3b44177f98d3c8e0781a0a0dfc6c6dbc2b5a95`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`9f0542e2fd07e26f9e008999c0e300a0b19c3353f8200be53859a145e21c2f59`
- 候選：PR #1030，head `74078faecfdd6238909e5157271604074f7eaf96`，merge `fe698e88d91678b18cfd5356a9c10be08dfa5186`，merged 2026-08-26T20:34:11Z，base `dev`
- 映射一致性：`consistent`（分支=task/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001；PR body task id=ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-08-26T19:59:36Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：2 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 在 merge commit 複驗 docs/evidence/gates/RELEASE_MANIFEST.json：candidate_sha=ebc4fca5c2dd5871275aee39a18406dd67464f04（已確認在 origin/dev 歷史中），manifest_digest 與四類 component image 皆為真實 Artifact Registry digest，無佔位值。
- Acceptance 明文『不啟用第三方來源、不進行 deploy』，屬證據對帳任務。

**缺口**

- Acceptance 綁定的 workflow run 33003734045 本身未在本盤點回查（唯讀 run 查詢未執行）。

**建議的最小下一步驗證**

- 若需完整鏈路，唯讀查 Actions run 33003734045 的 artifact 與 manifest digest 是否一致。

### `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001` — verified_candidate（信心 high）

- 標題：Release rollback 與 masked snapshot handoff
- 原 owner／reviewer：`Codex` ／ `Codex2`；brief 生成於 2026-08-27T15:36:46Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`d105d68baec59d4d208fa92ca1f0fbe2038524d59fff082bd05c7cd848260bac`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`151d91d365a853aa15de7f95fe03eb6e3398b6d09da4faec802531ce35abdfc6`
- 候選：PR #1050，head `55de004a8559442ed4ba9cedf800e692bb0b1116`，merge `d41b9328137cef1eceeb5990e8e0bb5963414682`，merged 2026-08-27T15:34:25Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-08-27T15:15:22Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：2 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- brief 明列兩條 Verification 指令（pytest 四個 release 測試檔與 ruff）；三個 artifact 在 merge commit 存在；acceptance 明文『不修改 Runtime Release workflow 或 staging lifecycle』。

**缺口**

- 候選 PR head commit 無 task trailer（head 為 merge commit）。

### `ODP-REQ-DISPOSITION-GOVERNANCE-001` — verified_candidate（信心 high）

- 標題：建立 MUST requirement amendment／waiver 的可機讀 disposition gate
- 原 owner／reviewer：`Antigravity3` ／ `Codex2`；brief 生成於 2026-09-03T13:47:11Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`4cc0990284600f3287b6fa2e2f46d18cac051d3d757d221c059f650af055c477`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`7984379de24bd84a8cb1911a6731fd2b48bf9d83e594f7bf56bd633fcca19d6f`
- 候選：PR #1146，head `7c889b2117803b8fe419f32e7ace84d1ce38b5e4`，merge `61ef9183d7c8ea2af4b5d7d625b1513dc683212f`，merged 2026-09-03T13:29:29Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-REQ-DISPOSITION-GOVERNANCE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-09-03T12:54:31Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 4 項，merge commit 上缺 0 項
- 終態旁證：5 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 四個 artifact（含 checker 與其測試、governance 文件）在 merge commit 全存在。

**缺口**

- Acceptance『負向測試證明 checker 會拒絕 AI 自簽與過期 waiver』依 CI 結論，未重跑。

### `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` — verified_candidate（信心 medium）

- 標題：落實 Agy／Claude 實作、Codex Astra ultra 審查的單一派工政策
- 原 owner／reviewer：`Antigravity2` ／ `Codex`；brief 生成於 2026-09-06T05:56:50Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`a91caa81fb9707dcc5bc79f76f917a3b7242343e326ce338b98edcb835f8f224`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`d3c2d0e117857a535e207589f0461a2edd30434e0a81225c694e9cfdd55f8440`
- 候選：PR #1221，head `c1a382416e4423e22f3bf5dfe86a37d93597e583`，merge `64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a`，merged 2026-09-06T05:56:17Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-ROLE-PROVIDER-CODEX-REVIEW-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'skipped': 3, 'success': 4}；skipped：product, performance-gate, product-e2e-gate
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-09-06T05:47:35Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- brief 未宣告 artifact，無法做存在性檢查
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- brief 的 Artifacts 為空，無法做 artifact 存在性檢查；acceptance 明文『當前 code 任務不可提前改 canonical runtime』，屬純 code/policy 交付。

**缺口**

- 精確 head 有 3 個 check conclusion=skipped。
- commit trailer 無 Verified 欄位。
- Acceptance 要求的 live config 生效屬後續操作；code 合併不等於 live orchestrator config 已改（live config 為顯式覆蓋，程式預設值不會自動生效）。

**建議的最小下一步驗證**

- 映射時明記本項只涵蓋 code/policy，live rollout 需另有受治理執行證據。

### `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001` — verified_candidate（信心 high）

- 標題：修正唯一 Runtime Release 的 dispatch ref 與 manifest CLI 整合
- 原 owner／reviewer：`Antigravity` ／ `Codex`；brief 生成於 2026-09-05T05:58:22Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`425162bf688be323d767fdbccca42c0aa855f35d09ecde3aa8f9d6aca45f1b56`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`bd378229b699a090724a07a3ec751f6f25780ecc7956bc32000ef3967d530c1e`
- 候選：PR #1206，head `8a7fe4b01270d5f5d95cd6996424612250bef372`，merge `74530caf5bbf8ee3802df658e21a3ffdfca56f25`，merged 2026-09-05T05:56:42Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-09-05T05:35:21Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：3 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 三個 artifact 在 merge commit 全存在；acceptance 明文『無 Secret Manager payload 讀取、無真實 lease 簽發、無部署 dispatch。default disabled 保持』。

### `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` — verified_candidate（信心 medium）

- 標題：整合唯一 build-once Runtime Release 狀態機
- 原 owner／reviewer：`Codex` ／ `Claude`；brief 生成於 2026-08-25T14:57:11Z，brief 內 Status=`review`
- brief 宣告 SHA256：`aab45b921aad5fcfd92af57b8c5eb32d2f5a491526c3864bc39eef6192cd3dbe`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`e67e43e15436247ec6f713e08f2d7fe7980342ea167940e06ae116094166e5ff`
- 候選：PR #1010，head `5f80756b84852f935625e502eca9fc038a6bbc9a`，merge `5ae1e5cee8ef6b5047fa72f2426d2a1f42d9f9ce`，merged 2026-08-25T15:34:07Z，base `dev`
- 映射一致性：`consistent`（分支=task/ODP-RUNTIME-RELEASE-SINGLE-PATH-001；PR body task id=ODP-RUNTIME-RELEASE-SINGLE-PATH-001；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Claude`（2026-08-25T15:07:55Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 3 項，merge commit 上缺 0 項
- 終態旁證：5 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 三個 artifact（deploy-dev.yml、workflow 契約測試、docs/deployment/）在 merge commit 全存在。

**缺口**

- Acceptance『依序支援 dev/ephemeral staging/prod blue-green』與『不存在第二套 proof/deploy 狀態機』為 repo 級不變量，本盤點未做全域重驗。
- brief 的 Status 停在 review（非 review_approved），但 task-review-gate 已 success 且有 5 份其他 brief 記其為 done。

### `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` — verified_candidate（信心 high）

- 標題：把 ephemeral staging lifecycle 接進唯一 Runtime Release
- 原 owner／reviewer：`Codex` ／ `Antigravity3`；brief 生成於 2026-08-27T17:51:09Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`a391689ac097ae3a988ff126cd4bf6db01fd594c631a600a4e22e98d595dbf0f`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`ece3bdccd07dee37f91ad3822401001b4c11ee69073b44c19cb81a3f4ae6d793`
- 候選：PR #1041，head `fada677569265ed258649b841e5a67fe7bb5dd80`，merge `462c8cd4ff2569cec0f2c383d9e601c5bdbec715`，merged 2026-08-27T17:50:22Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity3`（2026-08-27T17:30:51Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項

**已核對事實**

- brief 明列三條 Verification 指令（pytest ops 契約測試、terraform fmt -check、bash -n）；五個 artifact 在 merge commit 全存在。

**缺口**

- 候選 PR head commit 無 task trailer（head 為 merge commit）。

### `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` — verified_candidate（信心 medium）

- 標題：依 SITE-001 資料證據實作或正式處置 Brand Transfer／Format Conversion
- 原 owner／reviewer：`Antigravity5` ／ `Codex`；brief 生成於 2026-09-03T16:52:29Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`1ff48b5e85805405fef3388e036b1376a37a9b1c9c74a3d5a1e18e6d5e8d177b`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`eec0f05e81937ca8f806ce9db9682aa80912423e800a643a769d3b5f8e5b86fb`
- 候選：PR #1160，head `ffe02988a1b4def412090c6b422e6efb26081d9f`，merge `9f53418df41e558c8f953c801dd8fd1f25f77b5b`，merged 2026-09-03T16:51:21Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex`（2026-09-03T16:22:20Z）；與 brief reviewer 相符=True
- acceptance 性質：人類授權
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- ODP-FR-SITE-001 的 BRAND_TRANSFER 與 FORMAT_CONVERSION 兩 member 皆為 BLOCKED_BY_EVIDENCE，帶 formal_handback_ref 與 reopen_trigger，無自簽 decider，符合『不適用者只建立 human-authority handback 且 AI 不自簽 waiver』。

**缺口**

- 兩個需求 member 待人類裁決；候選 PR head commit 無 task trailer。

### `ODP-SITESCORE-QUALITY-NULLABLE-001` — verified_candidate（信心 high）

- 標題：SiteScore average_confidence／data_quality_score 缺席走 feasibility fail-closed
- 原 owner／reviewer：`Claude2` ／ `Antigravity4`；brief 生成於 2026-09-03T18:33:39Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`520da1bc75dcac665492254e1b9d0676ff243cae2e28a16e04e08b27a73a6970`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`3906bcd3b1b6d5ffa10864d45b78fcc1c0ad6f4b40ee246ca7b3218071960572`
- 候選：PR #1171，head `7b3536c88359e59355d3e8253acd732ad11efa84`，merge `901b4348ade4fb8cbf5c40ef429c7b49980257cc`，merged 2026-09-03T18:33:11Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-SITESCORE-QUALITY-NULLABLE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity4`（2026-09-03T18:00:27Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 5 項，merge commit 上缺 0 項

**已核對事實**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

### `ODP-SPEC-SOURCE-PROVENANCE-001` — verified_candidate（信心 medium）

- 標題：補齊 ODP-SA-06 與 ODP-FR-AVM-001 canonical source provenance
- 原 owner／reviewer：`Antigravity2` ／ `Codex`；brief 生成於 2026-09-03T15:20:33Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`79bc99fc6f8f12cc1bbe4eb2402c2916ccb1002ad8d2d3c64711c858fc731ef0`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`6d0e6d5197294b1345502d3ef18c0af9bbf59cf34250dad4386a8f9878d525b6`
- 候選：PR #1147，head `fd4947321d4bcbefb54ccf53799d0f12e04e8b96`，merge `b7f9d465ddd71ecdd3dc01a8869e6e6cb485b54d`，merged 2026-09-03T15:08:34Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-SPEC-SOURCE-PROVENANCE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity`（2026-09-03T14:38:09Z）；與 brief reviewer 相符=False
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 2 項，merge commit 上缺 0 項

**已核對事實**

- 兩個 artifact 在 merge commit 存在。

**缺口**

- 身分不一致：task-review-gate 核准者 Antigravity、commit trailer 記 LLM-Agent Codex2 / Reviewer Antigravity5，brief 記 owner Antigravity2 / reviewer Codex。三來源不一致，需 reviewer 依 gate 時間戳裁定。

**建議的最小下一步驗證**

- 以 task-review-gate 的時間戳與 PR body 的 ReviewBus 區塊交叉判定當時 owner/reviewer。

### `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001` — verified_candidate（信心 medium）

- 標題：修正 Staging recovery bundle 與 Terraform state 儲存邊界
- 原 owner／reviewer：`Antigravity2` ／ `Claude2`；brief 生成於 2026-09-05T16:06:41Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`beb27afc1fe6377922d95c184bd4f42e7cca3f8d2459a1731b17b86ae035bc64`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`3b8a70752fa0fcedde90f653493b712fdbbe08e743a74b95e48fc182469b9fff`
- 候選：PR #1208，head `a5e6a2f7074b6c5e679cbf7f0c6d03f9e6141a5a`，merge `b9dd2e7b337e4bab8236cd15b5df07092685100b`，merged 2026-09-05T16:02:58Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Claude2`（2026-09-05T15:39:52Z）；與 brief reviewer 相符=True
- acceptance 性質：人類授權
- brief 未宣告 artifact，無法做存在性檢查
- 終態旁證：1 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- brief 的 Artifacts 為空；acceptance 第 9 條明文『2026-09-05 唯讀 GCP/GitHub 盤點未發現已驗證可供 workflow 寫入的非 state/CMEK/release 隔離目的地』，並自陳『code 完成不等於 runtime 完成』。

**缺口**

- 尚無核准的 recovery bundle 目的地；runtime 驗證（resource/IAM/vars）明確留給後續 staging rollout，本項只能作為 code 階段的 verified_candidate。

**建議的最小下一步驗證**

- 映射時把 runtime 部分獨立記為未完成，不得讓 code 合併把 runtime 一併帶成 done。

### `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001` — verified_candidate（信心 high）

- 標題：統一 PLATFORM_ADMIN 跨租戶政策為共用 fail-closed guard
- 原 owner／reviewer：`Claude2` ／ `Antigravity5`；brief 生成於 2026-09-04T13:35:34Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`a8b05b78b787460267e15feada004aa987eeba9e48407d8cde1872b74f11abe5`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`a6ce6569f28b566d54e97ce99142155b669fcf97bd1b48c56018df3916ffe771`
- 候選：PR #1164，head `b678c18f2055b02adb70f1788951be4269589012`，merge `541eb1fe2d6b426155dc401089bf0588ef82b345`，merged 2026-09-04T13:32:29Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Antigravity5`（2026-09-04T13:08:32Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 6 項，merge commit 上缺 0 項

**已核對事實**

- 六個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 候選 PR head commit 無 task trailer（head 為 merge commit）。

### `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` — verified_candidate（信心 medium）

- 標題：在登入節流修正合併後驗收帳密預設與可選 OIDC 的安全端對端行為
- 原 owner／reviewer：`Codex` ／ `Codex2`；brief 生成於 2026-09-01T09:12:09Z，brief 內 Status=`review_approved`
- brief 宣告 SHA256：`3e8d24a768a5acbb2fff6f9fad2385dd06834a59fcb8c029533c922c52c44e39`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`01f24a0874f9dd2c54075265dad7318700be9811426c017cf8347722b777e4b6`
- 候選：PR #1096，head `69422d71e8d5ac572ade58562c0aeca28d123648`，merge `2377168c2cc07cd2470dd8f43de0486fe8d8fc08`，merged 2026-09-01T09:10:24Z，base `dev`
- 映射一致性：`mismatch`（分支=task/ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002；PR body task id=None；merge 在本地 dev 歷史=True）
- 精確 head CI：7 個 check，結論分布 {'success': 7}
- 核准證據：`task-review-gate`=`success`，核准者 `Codex2`（2026-09-01T08:45:28Z）；與 brief reviewer 相符=True
- acceptance 性質：純 code／文件／CI
- 宣告 artifact 4 項，merge commit 上缺 2 項：`security E2E receipt`, `auth migration rollout checklist`
- 終態旁證：2 份其他任務 brief 在 Dependencies 記本 ID 為 done

**已核對事實**

- 兩個路徑型 artifact（apps/web/tests/login-route.test.ts、tests/security/test_login_throttle_wiring.py）在 merge commit 存在。

**缺口**

- 另兩個宣告 artifact『security E2E receipt』與『auth migration rollout checklist』是敘述而非檔案路徑，無法定位對應交付物。
- Acceptance『完整 OIDC 設定時可選登入不回歸』需要有 OIDC 設定的環境，本盤點查無對應 runtime 收據。

**建議的最小下一步驗證**

- 請 reviewer 指認這兩個敘述型 artifact 對應的實際檔案，或修正 artifact 清單。

### `XR-EXT-OSS-FINAL-AUDIT-001` — verified_candidate（信心 medium）

- 標題：第三方與 OSS 技術閉合稽核（B14 CI token wiring）
- 原 owner／reviewer：`Codex2` ／ `Codex`；brief 生成於 2026-08-24T05:53:48Z，brief 內 Status=`review`
- brief 宣告 SHA256：`5e2112af80f16581bd057c4f5479524986594e52dfa92477bd555106b1c388bf`（orchestrator 自帶欄位）；本次重算檔案 SHA256：`2a746bfdf75d454e25fd57c5723c3672e67efc5289c956cbb5922150bbe3e556`
- **更正後**候選：PR #61（alfloop-dev/oday-data-platform），分支 `task/XR-EXT-OSS-FINAL-AUDIT-001`，head `b1824c979aca008da10aed01fbc0c0a269c581dc`，merge `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`，merged 2026-08-24T05:54:54Z
- 輸入清單原候選 https://github.com/alfloop-dev/odayplus/pull/996 已判定為誤配（詳見上節「候選誤配更正」）
- 精確 head CI：7 個 check，全部 `success`（source-suite, closeout-audit, producer-compatibility, consumer-readback, contract-bundles, emgi-task-manifest, Build and smoke test images）
- 核准證據：`task-review-gate`=`success`，描述「Approved by assigned reviewer Codex」；與 brief reviewer `Codex` 相符
- acceptance 性質：外部來源啟用／人類授權
- 宣告 artifact 3 項，merge commit 上缺 3 項：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/`, `scripts/audit_external_oss_closeout.py`, `tests/integration/test_external_oss_closeout.py`

**已核對事實**

- 輸入清單的候選 odayplus PR #996 為誤配：該 PR 的 headRefName 是 task/ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001，PR body 的 ReviewBus Task ID 亦為該任務，只是標題文字提及本任務；其三個宣告 artifact 在該 merge commit 與 dev tip 皆不存在。
- 本次改以唯讀查詢定位到真正交付：alfloop-dev/oday-data-platform PR #61，分支 task/XR-EXT-OSS-FINAL-AUDIT-001，MERGED 於 2026-08-24T05:54:54Z，merge 7b0670d7b37e59e06bc9fea5b6003d1964be2c3c，精確 head b1824c979aca008da10aed01fbc0c0a269c581dc。
- 該 head 的 7 個 check 全 success；commit status task-review-gate=success，描述為『Approved by assigned reviewer Codex』，與 brief reviewer 一致。
- 三個宣告 artifact（docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/、scripts/audit_external_oss_closeout.py、tests/integration/test_external_oss_closeout.py）在 oday-data-platform 存在，皆非 odayplus 檔案；原清單以 odayplus 為 repo 亦屬誤判。

**缺口**

- Acceptance『資料授權決定則逐來源列為待具名人員決定』與『技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴』涉及人類授權，本盤點不補造。
- 跨 repo，merge commit 不在 odayplus 本地歷史，只有 GitHub 唯讀證據。

**建議的最小下一步驗證**

- 以 oday-data-platform PR #61 取代 #996 作為本 ID 的候選；HUMAN-OSS-LEGAL-APPROVAL-001 的解除仍需具名人類決定，不得由本盤點推定。

## 給 reviewer 的映射注意事項

- 本資料集**不指定** writer，也不建議任何一鍵回填。`verified_candidate` 的意思是「該 ID 的既有證據足以支撐由 Codex／Claude 映射到唯一 existing writer」，不是「可以直接寫成 done」。
- 三筆 `blocked` 不得因為 PR 已合併或 CI 全綠而改判；它們的 acceptance 有被自身收據否證的條款。
- 16 筆 medium 信心的共同原因是：skipped checks、artifact 清單與實際交付不符、owner/reviewer 三來源不一致、或 acceptance 條款需要語意裁定。這些在明細中逐項列出。
- `ODP-RELEASE-GATE-FIXTURE-STAGING-002` 的 acceptance 提到其前身 `ODP-RELEASE-GATE-FIXTURE-STAGING-001` 曾因 `mutates_canonical=false` 只做了唯讀檢查就被 supersede；該前身不在本 38 項清單內，映射時勿混用。

