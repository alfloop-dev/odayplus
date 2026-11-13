# 開發與修正原則

- 動手前先讀規格、現有入口、實際呼叫者、被呼叫的實作及相關測試，確認原程式如何運作；不要只依檔名、對話或舊報告推測。
- 優先把既有程式修對。同一業務流程保留一份共用規則；不要在旁邊新增第二套驗證、狀態機、保存流程、fallback 或註冊機制。
- 新 helper 必須有具體既有呼叫點及明確職責，並在同一修改中移除被取代的重複實作。單獨元件測試通過不是新增架構的理由。
- 合併前先分清重複計算與必要的信任邊界檢查；來源限制、授權、租戶範圍、版本及前後身分核對不可因簡化而略過。
- 平行工作按既有模組與檔案責任拆分；共用介面及相依整合由主代理序列處理。每份派工都要指出既有程式及最終接回的入口，避免各自發明流程。
- 未接線、已被取代或決定不採用的實驗不得混入交付候選。歷史證據可封存，但不可把隔離副本、報告或測試數量當成已完成整合。
- 修改後驗證實際使用的那一條流程及相關回歸。尚未整合、未驗證或未上線的部分要明確列出，不得以局部通過宣稱全部完成。

## Intake 修改範圍

- 先讀 `docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md` 的現行程式責任表，再沿實際 API／UI／worker 呼叫鏈修改。
- 共用必填與比對規則沿用 `modules/external_data/application/assisted_intake.py`；canonical case、身分執行與服務驗證沿用責任表的既有 owner。不得另建 matcher、case repository、service registry 或保存路徑。
- 未接線的 native matcher 實驗已移出候選。歷史封存與獨立測試不是採用依據；不得直接復原成另一條產品流程。
- 原生流程尚未整合與正式遷移是不同事項；未完成 backfill、reconciliation、shadow、canary 及核准切換，不得刪除現用 operator 路徑、雙寫或加入靜默 fallback。
- 主代理整合共用介面；需要 auto worker 時，經既有 supervisor 指派具體既有檔案、接回入口及驗收條件，先確認無其他 worker 持有同一責任。
