# ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE

- doc_id: ODP-UXD-003-ADD-002-RESPONSE（Package 10）
- responds_to: ODP-UXD-003-ADD-002 v1.0.0 · Package 9 修正指令（2026-07-20）
- engineering_task: ODP-INTAKE-UX-001
- canonical_artifact: 互動原型 **Oday Plus Operator Console.dc.html（R7 · DEMO_STATE_VERSION oday-plus-r7-20260720）** — 本專案以 Claude Design 互動原型為 canonical visual-design package（產品方 2026-07-18 決議，Package 10 沿用；不提交 Figma）。
- runnable_artifact: **oday-plus-console-r7-standalone.html** — 由同一 R7 source 打包，見下方 checksum。
- updated_at: 2026-07-20（rev 2 — 修正 heal 版本閘：__healed 升為 r7＋intake 種子指紋檢查，舊 hot-reload session 會重灌 R7 種子並保留使用者新建收件 id≥3011）

## Checksums（SHA-256）

- source（Oday Plus Operator Console.dc.html）:
  `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`
- standalone（oday-plus-console-r7-standalone.html）:
  `1aefb8068faa39666599ceeafe74ba24f1ddc8abd57ba9a6513a724abaee7d0f`
- Package ZIP：本環境的 ZIP 由平台下載時即時產生，無法預先計算 zip-level checksum；以上兩個 file-level SHA-256 為版本一致性依據（zip 內容即此兩檔＋docs/）。
- 一致性 probe（source＝standalone 皆通過）：R7 版本字串 ✓ · 無「系統排程／每日掃描／掃物件／來源掃描」✓ · EXACT_DUPLICATE 短路徑 ✓ · canonical codes ✓ · seq ink:3011 ✓ · data-screen-label ✓。

## VDR 逐項回覆

- **VDR-001（自動掃描暗示）：已修正。** 來源卡改「核准來源（URL 送件）／最近收件」；591＝SRC-591 v4（效期 2026-12-31，僅限使用者送件之單頁）、樂屋＝SRC-RKY v2（效期 2026-10-31）；僅合作 feed 顯示「核准 feed（推送）· SRC-FEED v3 · 效期 2027-03-31」；Today 卡、Govern 資料源列、追蹤／搜尋條件 toast 全部改寫（「新收件將優先比對此區」等）。證據：Network 物件雷達來源卡＋grep 無「掃描／排程」字樣（probe ✓）。
- **VDR-003（Tablet／Mobile）：已修正。** 移除 min-width:1280/1240 固定值（root min-width 依 viewport 動態為 0）；斷點 <760 mobile、760–1159 tablet、≥1160 desktop。Mobile：URL 送件、佇列（列可點）、狀態追蹤、認領、簡單確認、receipt 檢視全可操作；解析欄位改堆疊 lineage 卡；REVISION 比對改變更欄位摘要列表；僅 POSSIBLE_MATCH side-by-side 顯示 inline DESKTOP_REQUIRED 卡（保留 deep link 與輸入，無全畫面遮罩）。Tablet：雷達改二欄、stepper 3 欄、詳情全功能。驗收尺寸 1440／1024／390 無頁面級水平溢出（intake 流程）。
- **VDR-004（Accessibility）：已修正。** 全部 dialog：role=dialog＋aria-modal＋aria-label＋開啟 initial focus＋Tab focus trap＋關閉 focus return；Esc 明確行為（決策確認 dialog 不受 Esc 關閉並提示，其餘 Esc 關閉）。所有 intake 輸入含 aria-label；「×」按鈕 aria-label=關閉對話框（×17）；「修正」按鈕帶欄位名 accessible name。佇列 role=list＋列 tabIndex=0＋Enter/Space 開啟＋逐列 aria-label 摘要＋排序說明（無互動排序，aria-sort 以文字聲明 descending）。動態階段有 aria-live=polite live region；toast 容器 role=status。錯誤訊息 role=alert＋tabIndex=-1 自動聚焦並指名關聯欄位。html lang=zh-Hant、document.title、prefers-reduced-motion 覆蓋、focus-visible outline。對比：主要灰階文字 token 全域調深（#8A93A8→#6E7891 ≈4.6:1、#98A1B3→#6B7590 ≈4.9:1、#B6BDCC→#737D97 ≈4.5:1），body 文字 #1C2333／#3A4362／#5A6478 均 ≥7:1（WCAG 2.2 AA）。
- **VDR-005（Durable route）：已修正。** 送件成功（含 EXACT_DUPLICATE 攔截）立即寫入 location.hash=#intake/IN-xxxx；hashchange 監聽支援 browser back/forward；reload／direct open 由 hash 還原同一筆 detail；Inbox filters（inkF）、選取（selIntake）、detail 開啟狀態（inkView）持久化於 session state，重載可恢復；receipt／compare 區塊隨 intake 資料還原。非僅 component state。
- **VDR-006（交付決議）：已接受並記錄。** Claude Design 互動原型＝canonical package（產品方決議 2026-07-18，Package 10 確認沿用）。Reviewer status：Product ✅（使用者本人，2026-07-20 指令即審查依據）；System Design ⏳ 待指派；Frontend ⏳ 待指派；Accessibility ⏳ 待指派（VDR-004 證據已備）；QA ⏳ 待指派（P0 驗收步驟見下）。
- **VDR-007（Transfer／Pause／WORM）：已修正。** Transfer：必填 target＋handoff note、無 resume time、成功後顯示新 owner／version bump／receipt（RCPT-ASG-xxxx-T，寫入 Audit 與時間軸）；IN-3003（ESCALATED）首次轉交觸發 409 OWNER_CONFLICT — 顯示目前 owner／版本、輸入保留、重新整理後可重送。Pause：必填核准原因＋resume time（顯示且可編輯，非隱藏預設），成功後 SLA=PAUSED＋歷程＋receipt（RCPT-ASG-xxxx-P）。WORM Evidence 面板 11 列：WORM state／purpose binding／classification／access expiry（IN-3008=PURPOSE_EXPIRED）／retention+legal hold（IN-3005=LEGAL_HOLD）／masking（受限角色=FIELD_MASKED）／export（受限=EXPORT_DENIED 403；privacy=purpose-bound）／verification（快照雜湊）／actor·role·time／snapshot·parser lineage／evidence receipt＋correlation ID。
- **VDR-009（版本一致）：已修正。** standalone 於本輪由 R7 source 重新打包（先修正殘留文案再 build）；上方兩組 SHA-256 與 probe 表為 checksum evidence；舊 R6 standalone 已刪除，package 不含兩套 UI。

## P0 可實測驗收（runnable artifact）

1. 佇列送 591 URL → hash 立即為 #intake/IN-3011+ → reload 仍在同筆 → back 返回雷達。2. 貼 L-2024 既有 URL → EXACT_DUPLICATE，階段僅 3 步。3. IN-3002 加入版本 → 409 → 重新整理 → 成功＋RCPT-REV。4. IN-3003 轉交 → 409 OWNER_CONFLICT → 重新整理 → RCPT-ASG。5. 切受限使用者 → FIELD_MASKED＋EXPORT_DENIED。6. 390px 寬：送件／追蹤／認領／receipt 可操作，POSSIBLE_MATCH 比對顯示 DESKTOP_REQUIRED 卡。7. Tab 進入任一 dialog → focus 受困於 dialog，關閉後回原按鈕；決策 dialog 按 Esc 不關閉。
