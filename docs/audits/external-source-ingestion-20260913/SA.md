# SA：外部來源接入、資料留存與release需求分析

版本：2026-09-13。依據：INVESTIGATION.md、source-index.json。狀態：供Supervisor／autoworker執行的需求基線。

## 目標與角色

把「可查證的外部來源」經平台擷取、解析、分類、留存、衍生，轉成ODP可驗證的資料release。完整目標涵蓋八域；不把所有來源啟用當成sources-off基礎部署的條件。

| 角色 | 責任／輸出 |
|---|---|
| 根對話 | 調查事實、差距報告、SA／SD、execution tasks、去重、跨任務進度與驗收證據核對；不直接改產品程式。 |
| Supervisor | 驗證task dependencies與owned_paths、派工、監控quota/lease、改派、送審、合併及收尾。 |
| Autoworker | 依task查證來源、實作、測試、產生真實證據、anchor/PR；遇阻礙回報精確步驟與可續作範圍。 |
| 獨立reviewer | 按相同commit／artifact核對設計、程式與receipt；同一作者不能自核。 |
| 使用者／有權帳號持有人 | 僅處理工程不能代辦的真正account interaction或必要具名決定；不負責代工程提供raw檔、SHA、筆數。 |

## 功能需求與驗收

| ID | 需求 | 驗收依據 |
|---|---|---|
| FR01 | 逐來源列出provider、dataset、official endpoint/release、格式、範圍、頻率、terms、auth、cost、retention與實際使用目的。 | 可讀回來源登錄與官方引用；未查明與已不存在不同標示。 |
| FR02 | 同一LiveAcquisitionKernel做有界HTTP與短期provider lease，對每次attempt保留成功／失敗證據。 | 429、timeout、HTTP200 application-error、缺憑證、過期許可測試；real HTTP收據獨立於mock。 |
| FR03 | Parser與live入口能消化實際格式；schema/行政代碼/年月/數值/單位有驗證。 | 真实payload解析；不接受示例代替，格式漂移有quarantine及版本。 |
| FR04 | Query scope、page/cursor/release partition與完整性明確；不把full page當全量。 | 無重複頁、總頁變動、缺頁、錯誤月份、預算耗盡全部可偵測；filtered total與global total分開。 |
| FR05 | 原始bytes與normalized output分開，兩者都能以URI與hash重新讀取；可重跑、可追到原來源。 | 原始每頁hash、衍生hash、parent refs、captured_at／effective_at、獨立process讀回、重跑不覆寫不同bytes。 |
| FR06 | 各domain分類與masking保留真實語意。 | 未分類欄位拒絕；公開欄位顯式pass-through；候選登記≠營業實體；transport count≠實際店前footfall；observed≠synthetic。 |
| FR07 | 受治理retention以本次manifest的真實範圍／count驗證，不使用固定舊fixture筆數。 | 真實不同count的兩版資料皆可驗證；少頁、改count或改hash則失敗；原八域release要求未任意放寬。 |
| FR08 | site_market_context用真實component manifests組裝並標示缺域，不製造資料。 | 每component content SHA可讀回；缺來源有unavailable reason，unknown不轉0。 |
| FR09 | masked release在獨立sources-off lane讀取受治理raw，綁定exact candidate/image/contracts/mask policy及GCS generation。 | 沿用#63，下載並hash驗證，所有關鍵binding篡改失敗；非任意JSON receipt即通過。 |
| FR10 | 按來源啟用及自動排程，單一來源失敗只限制自己的live能力。 | 沿用XR activation；未決/過期/停用跳過或fail closed，approved來源續作；sources-off基礎部署獨立。 |
| FR11 | 歷史對照與runtime acceptance使用真實證據。 | XR任務核對historical vs new快照差異與真實runtime metrics/restart/egress，無SIMULATED收據。 |
| FR12 | 交付留有乾淨owner工作樹與可恢復交接。 | 已停止原writer、具名scope、durable anchor、重派可續；不得用廣泛stage/stash清掉其他人的修改。 |

## 非功能需求

1. 可追溯：唯一真實來源到各衍生物的內容與版本鏈；執行者、時間、query與程式版本俱全。
2. 可重現：pin dataset release、檔案或query邊界；不使用「現在」替代歷史資料期別。
3. 有界成本：明確region/date/page/byte/request/time預算；預算不足是partial，不能偽造complete。
4. 安全與隱私：不在logs/PR附密鑰、phone/person/device等raw；沿用分類/mask政策；測試與production環境區分。
5. 韌性：頁面錯誤、parser drift、source downtime、quota不可拖停其他來源；保存可續跑進度與失敗證據。
6. 相容性：沿用現有registry/kernel/Dagster/retention/release workflow，不建第二套scheduler或registry；契約變更需要版本化和consumer相容測試。

## 層級與狀態

每來源個別記錄：`DISCOVERED → CONFIGURED → CONTRACT_TESTED → LIVE_CAPTURED → RAW_READBACK_VERIFIED → NORMALIZED_VERIFIED → RETAINED_IN_CLOUD → RELEASE_ELIGIBLE → ACTIVATED`。

這是SA驗收層級，不要求新增一套系統狀態機。應映射現有模型／task fields。任一层未完成都不能用後一层名稱回報。例：目前MOF/RIS局部到local normalized驗證，尚未cloud retained/release eligible。

## 業務邊界

- 八域為release交付分組，不等於八個API；可多來源合成一domain，site context是衍生domain。
- source scopes首輪可有界驗證；正式資料覆蓋範圍必須由既有需求與registry規格落實。不能用100筆成功宣稱完整MOF。
- 缺少的來源授權／帳號只能阻擋其啟用或live capture；仍可完成調查、adapter、分頁、分類、測試與接線。
- 工程先查現存Secret reference與已決授權，最後才提出無法代辦的精確缺口，不反覆詢問已核准事項。
