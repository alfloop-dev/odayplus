# 已核准／已開店候選點的新版評分報告審查

本增補依使用者於 2026-10-14（臺北時間）的明確決策：「新版另開一輪審查，保留原核准及開店紀錄」。既有 `ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md` 及 `ODAY_PLUS_SITESCORE_REPORT_UI_SPEC.md` 定義報告版本、人工決策、執行／結果、audit 的分離；本文件補上先前未定義的 terminal candidate 新版報告流程，未改動原設計稿。

目前程式實作及自有 PostgreSQL 測試仍需 Root 獨立審查與驗證；本文件不是正式上線或真人核准憑證。

## 觸發與審查輪次

- 候選點實際 `site_status` 為 `approved` 或 `opened`，已有同 tenant／candidate 的原核准 Decision，且新的 saved report ID 不同、正整數 report version 嚴格較新，才可另開一個 Decision。
- 每一新 Decision 有自己的 ID、版本、獨立 Approval ID、retained report、history 與 audit；`previous_decision_id` 指向其前一轮。此欄位由鎖內最新 candidate `review_id` 衍生，使用者提交內容不能選擇前輪。
- 沿前輪鏈逐筆檢查 tenant／candidate、正整數 Decision／report version、retained report 身分、嚴格遞減的 report version，以及無循環。鏈根必須為原 APPROVED。初次 rejected 候選點／Decision 沒有這條原核准鏈，維持現有禁止重開規則。
- 每一前輪的 Approval 必須實際存在，ID／Decision 關聯、terminal status、actor、time、comment 與原決策 history 相符。末筆 transition 的原始與終局狀態、native action、network action、report／tenant／Approval 身分及正整數版本，也必須與該 Decision 一致。每次重送或核決 transition 前重新驗證當前 retained report 與這條前輪鏈；transition history 保留 previous marker，清除或改造 pending 輪次的前輪鏈皆拒絕。
- 後續輪次 GO／WAIT／REJECT 結束後，第三個更新報告仍可再開新輪。後續 REJECT 只代表該輪新版報告的決策，不撤銷原核准或開店紀錄。
- RETURN 保留同一 Decision 與該輪 frozen report；補件後再送審是同輪新 Approval。即使已有較新 report，該次 RETURN 重送仍保留原 frozen report，latest report 只列於 current context。
- 同一 terminal report 使用新 idempotency key 不得另開輪；原 key、原 payload 的授權 receipt replay 保留原行為，不新增紀錄。

## 保留與查詢

- 原 Decision／history／Approval／retained report 保留，仍可透過既有 Decision read 與 canonical review snapshot 查詢。
- 候選點 `review_id` 指向當前輪，但實際以及列表呈現的 `approved`／`opened` 狀態保留。新輪 WAIT／RETURN／REJECT 在該輪 `reviewWorkflow` 呈現，不把已開店候選點顯示為退回、待補件或駁回。
- 新輪 GO 不再執行開店 realization hook，以免覆寫原開店基準及 realized record。原初次 GO 的 realization 行為保持。
- Candidate metadata 僅透過原 review owner 的 CAS 更新當前輪指標。其他開店 metadata 欄位保留；原輪的 durable 決策／簽核紀錄不刪除或覆寫。
- 前端新版入口以現有授權 read 的完整 Decision／Approval／report 鏈核對當前 pointer 與較新 report；入口只請求正式 current context。正式 context 的 `previousReview` 與舊 pointer／最新 report version／actor／tenant／digest 綁定。送審成功還須 receipt 中新 Decision／history／command 的 previous link，以及 fresh pending graph／producer metadata／原 operative state 全部相符，才顯示已送審。

## 權限與原子性

現有實際 authenticated Principal、tenant、RBAC、module／clearance／record scope 先於 replay 檢查，且 fresh mutation 在 report→candidate 鎖內重新檢查。原 expected latest report、candidate pointer／state／digest、Decision version CAS、metadata CAS，以及同 SQL engine 的 Decision／Approval／candidate／audit／receipt 交易保持。每個候選點最多一個 current pending round；不一致 pointer 或多筆 pending 皆拒絕。

原提出者與目前送審者的 SoD 禁止自審規則保持。此增補不授予實際使用者新角色、不建立真人核准、不呼叫模型推論，也不改動既有 Store／開店執行 producer。

## 驗證邊界

記憶體測試驗證選輪、前輪鏈、RETURN、原資料保留與 lifecycle 投影，不能代表 SQL 交易／真實權限通過。Prepared PostgreSQL 案例使用每案新建的自有 local database、實際 signed JWT 與 SQL account role／scope；`opened` 狀態及既存開店 metadata 是明確 synthetic fixture，不宣稱完成正式開店流程。所有 SQL 案例須先由 Root 審核來源及新 actor／record／action／scope／time manifest 才執行，禁止連線原私有 DSN 的 database、reset、FORCE DROP 或終止他人連線。
