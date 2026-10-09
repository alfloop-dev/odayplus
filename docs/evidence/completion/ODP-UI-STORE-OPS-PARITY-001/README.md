# Store Ops — Package 10 內容與對話框比對

任務：`ODP-UI-STORE-OPS-PARITY-001`；owner Pi；reviewer Codex2。

## 範圍與收據

- 修正前：`963090d6fc320e973c6c2d993b5342fbebbbe3e3`（當時 `origin/dev`）。
- 最終程式／測試：`3e9ecc94cec6dc66c8bfa33e020495bf4760204b`；後續 evidence commit 只封存本目錄，不改程式。
- 設計：`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted/oday-plus-console-r7-standalone.html`。
- 設計 SHA-256：`1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f`，與 Review 003 相符。
- Chromium `149.0.7827.55`、Playwright `1.61.1`、Node `v22.23.2`；viewport 為 1440×900、390×900，另驗證 1024×900 的內容幾何。
- [verification.json](verification.json) 保存原 shell exit code；[playwright.log](playwright.log)、[unit.log](unit.log)、[typecheck.log](typecheck.log) 是原工具輸出。
- 這是本機 fixture UI／mock API 驗證，**不是 live deployment、真實指標、remote visual approval 或發布核准**。

## 實際結構修正

1. 證據融合加入付款失敗率、14 根長條與來源提供的 baseline；不再用 confidence 代替營運指標。`EvidenceItem.paymentTrend` 是可選的來源資料；只有示範 fixture 新增 Package 10 mock 序列。API 未提供時顯示「來源未提供付款失敗率序列」，不補值、不產生長條或 baseline。
2. 獨立「事件與處置時間軸」合併實際 issue 建立時間、證據時間及該 issue 的 audit records，排序顯示，不捏造操作紀錄。
3. 來源 tabs／明細移至時間軸下方獨立卡片；保留 ForecastOps 分頁與正式資料缺失 gate；加入 tab/panel 關聯、方向鍵、Home/End 與 roving tabindex。
4. AI CTA 開啟「建立處置」，**不自動提交 API**；處置仍經既有表單與後端權限檢查。
5. Action rail 預設只保留一主一次，其他既有動作收入「更多動作」；中文主／次按鈕沿用原工作流程端點，去掉與主按鈕同名的重複次動作。
6. 中欄維持上游已修好的 `minmax(0,1fr)` 與單一 main landmark；沒有重加 shell padding。1440px 量到 queue x=20、中欄 742px（設計約 740px）；390px 中欄 366px，所有新卡片均未超出此寬度。
7. 七個對話框採 Package 10 的寬度、14px 圓角、緊湊標頭與中文欄位／CTA；Camera 主按鈕保留設計的紫色，其餘為 indigo。共用現有 modal focus hook，驗證初始焦點、Tab/Shift+Tab trap、Escape 與穩定觸發器焦點回復。對話框標頭不再形成第二個 banner。

## 保留後續功能的整合方式（PR review 重點）

不是刪掉後续規格以追求像素相等；所有既有 API enum、callback、endpoint、idempotency／correlation headers 與後端授權路徑保留。

| 對話框 | 設計核心與整合差異 | 1440px 高度：設計 → 修正後 |
|---|---|---|
| Triage | 主區回到根因、信心度、研判備註；嚴重度、決策、觀察窗、補證據及 fixture-only 快轉放入「進階研判與補證據」；Production 不渲染快轉選項／checkbox | 419.6 → 447.8 |
| Assign | 保留可編輯角色、負責人、實際期限與交辦備註，不以設計的固定示範人名取代 API 人員資料 | 353.5 → 355.5 |
| Create Action | 保留可編輯 checklist、觀察窗、證據／核准 flags；標題與執行說明放入展開區；遠端重啟才渲染且強制填寫稽核備註，不新增虛假的 Owner 選項 | 510.7 → 381.5 |
| Outcome Review | 回到前後指標表與三張成效選擇卡；可選 `Issue.outcomeMetrics` 未提供時表格明確留缺，不能以設計 mock 改善數字宣稱成效。保留證據摘要、結案 flag；無效／無法判定才渲染後續欄位且禁止直接結案 | 683.8 → 686.0 |
| Escalate | 回到三張目標選擇卡與理由；既有 urgency、期望結果、通知負責人整合到展開區 | 402.4 → 466.4 |
| Camera Purpose | 自由輸入目的、稽核備註、隱私告知及必須確認的 checkbox 保留；位置／時段／保留時數置於展開區，不為縮短對話框取消調閱前置 | 347.7 → 477.6 |
| Reply Review | 回到事件摘要及可編輯回覆主區；管道、核准／退回／拒絕、理由與發布旗標置於展開區。退回／拒絕會展開設定且仍強制理由；CTA 送審，不冒稱直接對 Google 發布 | 374.7 → 370.9 |

Mobile 對話框均為 350px（左右 20px），可用縱向捲動而不裁切；完整幾何及剩餘高度差見 `geometry-dialogs-*.json`。備註、資料內容與保留的額外欄位造成高度不同，不宣稱逐像素相等。

## 同寬截圖索引

48 張 PNG：8 個畫面 × 2 個 viewport × 設計／修正前／修正後。Store 內容是 full-page，其餘是 900px viewport。比對結構與密度，不把不同 fixture 的文字／數字差異當缺陷。

### 1440px

| 畫面 | 設計 | 修正前 | 修正後 |
|---|---|---|---|
| Store | [design](shots/design-store-1440.png) | [before](shots/before-store-1440.png) | [after](shots/after-store-1440.png) |
| Triage | [design](shots/design-triage-1440.png) | [before](shots/before-triage-1440.png) | [after](shots/after-triage-1440.png) |
| Assign | [design](shots/design-assign-1440.png) | [before](shots/before-assign-1440.png) | [after](shots/after-assign-1440.png) |
| Create Action | [design](shots/design-action-1440.png) | [before](shots/before-action-1440.png) | [after](shots/after-action-1440.png) |
| Outcome | [design](shots/design-outcome-1440.png) | [before](shots/before-outcome-1440.png) | [after](shots/after-outcome-1440.png) |
| Escalate | [design](shots/design-escalate-1440.png) | [before](shots/before-escalate-1440.png) | [after](shots/after-escalate-1440.png) |
| Camera Purpose | [design](shots/design-cameraPurpose-1440.png) | [before](shots/before-cameraPurpose-1440.png) | [after](shots/after-cameraPurpose-1440.png) |
| Reply Review | [design](shots/design-replyReview-1440.png) | [before](shots/before-replyReview-1440.png) | [after](shots/after-replyReview-1440.png) |

### 390px

| 畫面 | 設計 | 修正前 | 修正後 |
|---|---|---|---|
| Store | [design](shots/design-store-390.png) | [before](shots/before-store-390.png) | [after](shots/after-store-390.png) |
| Triage | [design](shots/design-triage-390.png) | [before](shots/before-triage-390.png) | [after](shots/after-triage-390.png) |
| Assign | [design](shots/design-assign-390.png) | [before](shots/before-assign-390.png) | [after](shots/after-assign-390.png) |
| Create Action | [design](shots/design-action-390.png) | [before](shots/before-action-390.png) | [after](shots/after-action-390.png) |
| Outcome | [design](shots/design-outcome-390.png) | [before](shots/before-outcome-390.png) | [after](shots/after-outcome-390.png) |
| Escalate | [design](shots/design-escalate-390.png) | [before](shots/before-escalate-390.png) | [after](shots/after-escalate-390.png) |
| Camera Purpose | [design](shots/design-cameraPurpose-390.png) | [before](shots/before-cameraPurpose-390.png) | [after](shots/after-cameraPurpose-390.png) |
| Reply Review | [design](shots/design-replyReview-390.png) | [before](shots/before-replyReview-390.png) | [after](shots/after-replyReview-390.png) |

修正前在編輯前以瀏覽器真實 click 取得（fixture fallback、Store API abort，capture command exit 0）；修正後使用同一套 issue fixtures 的攔截 API。兩者資料載入策略不同，稽核／頂部計數不能當作資料一致性證明。

設計 HTML 沒有修改。設計截圖停用 CSS animation，阻擋可選的外部 CDN 請求；已知的 390px prototype tabs 裁切缺陷會阻擋 pointer click，因此**只在設計參照捕捉**使用 DOM click events 切換對話框。實作測試全部使用真實 click／keyboard，不採 force click，也不藉隱藏 overflow 讓幾何斷言通過。

## 驗證與重現

```sh
npm ci --ignore-scripts
npm run typecheck --workspace=@oday-plus/web
npm run test --workspace=@oday-plus/web -- \
  features/operator/__tests__/StoreOpsPackage10Parity.test.tsx \
  features/operator/__tests__/StoreOpsWorkflowDialogs.production.test.tsx
OPSBOARD_PORT=3186 STORE_OPS_PARITY_DESIGN=1 \
STORE_OPS_PARITY_EVIDENCE_DIR="$PWD/docs/evidence/completion/ODP-UI-STORE-OPS-PARITY-001/shots" \
npx playwright test --config tests/e2e/operator-store-ops-parity.config.ts
python3 delivery_toolchain/governance/check_code_boundaries.py
```

Focused config 只啟動既有 fixture web server；兩份 spec 都攔截 Store Ops API，不需 Python backend。一般 CI 使用根 config 亦會發現新 spec，沒有另開 workflow。

- typecheck **exit 0**；兩個 unit files，**15 passed / exit 0**。
- Playwright **9 passed / exit 0**：缺付款序列與 CTA 無寫入、三個內容 viewport、兩個含七對話框的 viewport、既有四燈／完整 lifecycle／Camera 解鎖。
- 幾何：document scrollWidth 不超過 viewport；三張卡與 metric 不超過 detail；卡片順序獨立；對話框寬度、圓角、邊界、內部寬度與設計實測相等。Triage 主區只有兩個可見 select；展開後仍可編輯嚴重度。
- axe：恢復的付款 metric、時間軸、來源卡，以及七個對話框在兩個寬度均 **零 violations**。這是明確 scoped 結果，不冒稱整個產品零問題。
- unit callbacks 證明 triage advanced enum/flags、remote restart audit guard、ineffective follow-up、escalation target/urgency/notify 與 reply rejection/publish flag 保留。
- boundary check **exit 0**；`git diff --check` **exit 0**。
- 初期曾有測試 selector／設計 animation／CDN／prototype mobile pointer 的 harness failures；一次 300s terminal timeout 無完整 exit 收據，未視為成功。修正後以完整的原工具 exit 0 與本目錄 logs 判定，不從程序名稱或 passed grep 推斷。
- [shots.sha256](shots.sha256) 綁定 PNG 與幾何 JSON。

## VDC 邊界與審查

| 條件 | 本任務結果／邊界 |
|---|---|
| VDC-001 | Intake Transfer／Pause 不在本 scope，也未改其控制分支；Store 既有流程不得為版型而刪除。新增條件欄位測試同時斷言 presence／absence。 |
| VDC-002 | 390／1024／1440 內容幾何與七對話框 390／1440 均通過；不照抄 prototype 的 mobile 裁切。 |
| VDC-003 | restored surfaces 零 axe violations；單一 main、dialog focus return/trap、tabs 鍵盤完成；不改共用文件 title/lang。 |
| VDC-004 | Intake URL serialization 不在 scope，既有 workspace/entity/tab entry routing 保留；未把 Store local source-tab state 冒稱成完整 inbox URL contract。 |
| VDC-005 | Pi 記錄工程實作與 focused QA receipts，提交 Codex2 審查；不冒簽 Product/System Design/Frontend/Accessibility 的獨立 release approval。本 PR 不是取代既有多專業與 remote visual release gates。 |
