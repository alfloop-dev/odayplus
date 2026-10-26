# EMGI raw 欄位分類草稿審閱表（DRAFT，待簽）

> **狀態：草稿，尚未核准。** 本表與 `classifications/*.json` 由 AI 依 `oday-data-platform` `origin/dev` `24c40ae7` 的程式碼離線整理，供負責人 **蔡尚志** 審閱、修改、簽署。它不是 approval receipt，也不是來源上線核准。

## 0. 先讀這幾點

1. **分類等級只有四種**：runner 驗證器只接受 `PUBLIC` / `INTERNAL` / `RESTRICTED` / `PROHIBITED`（`scripts/capture_public_source_evidence.py` `CLASSIFICATION_LEVELS`）。沒有 `CONFIDENTIAL`；交辦中「至少 CONFIDENTIAL」的欄位一律取 `RESTRICTED`。
2. **驗證器不認得「草稿」**：artifact 裡的 `draft_status` 只是多餘鍵，驗證器照樣判 `BOUND`。把這些草稿交給 `--live` 執行，runner 會當成已授權分類使用。**簽署前不要把草稿檔交給任何 live 執行。**
3. **簽署後 digest 會變**：`classification_digest` 是整份 artifact（去掉 `classification_digest` 本身）canonical JSON 的 SHA-256。簽署時要刪掉 `draft_status`、把 `authority_reference.reference` 改成真實核准紀錄、依審閱結果修改等級，任何一處改動都會產生新 digest。表內 digest 只用來核對草稿版本。
4. **分類等級在程式裡沒有下游效果**：全 repo 沒有程式依 `RESTRICTED` 遮蔽或拒存欄位。runner 只檢查欄位有沒有涵蓋，並把 artifact digest 綁進 normalized receipt。標成 RESTRICTED 的欄位（例如 MOF 負責人姓名）仍會原樣留在保留的 raw bytes 裡。
5. 「判斷」欄標 ● 的列是需要負責人特別確認的判斷，理由寫在各來源的「判斷說明」。

## 1. 驗證結果總表

驗證方式（全部離線）：(a) 直接呼叫 runner 的 `validate_classification_artifact`（走 DEFAULT 清單，等同 readiness 檢查）；(b) 改用已知的供應者實際鍵當 `observed_raw_fields`（等同 live gate）；(c) 以 `CLASSIFICATION_METADATA_<SOURCE>=<檔案>` 執行 `capture_and_retain_emgi_raw.py --check-readiness --json`；(d) MOF、RIS 另用 2026-09-21 私有擷取，經 runner `--resume` 路徑使用的 `load_bridge_inputs_with_report` 綁定 digest（只讀本機檔，不輸出記錄值）。

| source | domain | 草稿檔 | (a) DEFAULT | (b) 實際鍵 | (c) readiness 分類 | readiness 總狀態 | (d) 私有擷取綁定 | 草稿 digest |
|---|---|---|---|---|---|---|---|---|
| `brand_locator` | `open_poi` | **無法起草** | ADAPTER_GAP | — | — | ADAPTER_GAP | — | — |
| `cwa_hazard` | `cwa_events` | `classifications/cwa_hazard.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `b288a5bcafe7c564…` |
| `cwa_weather` | `cwa_events` | `classifications/cwa_weather.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `423d70a099b96b00…` |
| `fnp` | `listing_observations` | 不需草稿（內建 `FNP_EXTRACTION`） | BOUND | — | BOUND | INPUT_REQUIRED | — | 內建 `b084232d1e7c3ac0…` |
| `foursquare` | `open_poi` | `classifications/foursquare.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `13d2e8f75e5c9a71…` |
| `market_events` | `cwa_events` | **無法起草** | ADAPTER_GAP | — | — | ADAPTER_GAP | — | — |
| `mof` | `mof_moi` | `classifications/mof.json` | BOUND | BOUND | BOUND | READY_FOR_CAPTURE | BOUND（digest 相符） | `278fb65e74332b65…` |
| `moi` | `mof_moi` | `classifications/moi.json` | BOUND | BOUND | BOUND | READY_FOR_CAPTURE | — | `b4f5cb16c75ed559…` |
| `nlsc_boundary` | `ris_nlsc` | `classifications/nlsc_boundary.json` | BOUND | BOUND | BOUND | INPUT_REQUIRED | — | `0a0a52df41b4368a…` |
| `nlsc_register` | `ris_nlsc` | `classifications/nlsc_register.json` | BOUND | — | BOUND | READY_FOR_CAPTURE | — | `8e5eda437607ba98…` |
| `osm` | `transport_osm_tdx` | `classifications/osm.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `21276366ec1e96d6…` |
| `overture` | `open_poi` | `classifications/overture.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `6fc28655190b0319…` |
| `ris` | `ris_nlsc` | `classifications/ris.json` | BOUND | BOUND | BOUND | READY_FOR_CAPTURE | BOUND（digest 相符） | `33c3ab36ae286f42…` |
| `site_context` | `site_market_context` | `classifications/site_context.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `fd6ae5756f34ccba…` |
| `tdx_parking` | `transport_osm_tdx` | `classifications/tdx_parking.json` | BOUND | — | BOUND | INPUT_REQUIRED | — | `5950cb7512c185fd…` |
| `trtc` | `mobility` | `classifications/trtc.json` | BOUND | BOUND | BOUND | INPUT_REQUIRED | — | `0e63d7b3ec54fe44…` |

- `market_events`、`brand_locator`：lane 沒有 record model（`ADAPTER_GAP`：`SOURCE_NOT_VERIFIED` / `SOURCE_NOT_RESOLVABLE`），驗證器在檢查任何欄位前就回 `ADAPTER_GAP`，程式也沒有欄位清單，**無法也不應起草**。
- readiness 總狀態仍是 `INPUT_REQUIRED` 的來源，缺的是憑證、URI 或開關／收據，不是分類。本次檢查環境未設 `ODAY_EXTERNAL_SOURCE_POLICY_ENFORCEMENT`；設為 true 時 mof/moi/ris/nlsc_register 也會要求 `*_ENABLED` 與 `*_APPROVAL_RECEIPT_SHA256`。
- (d) 的 09-21 擷取收據沒有記錄實際鍵，loader 退回 DEFAULT 清單；新擷取會記錄供應者實際鍵，那條路徑由 (b) 覆蓋。

## 2. 簽署後怎麼交給 runner

- CLI：`scripts/capture_and_retain_emgi_raw.py --source-classification <source>=<簽署後檔案路徑或 inline JSON>`（可重複；鍵可用 source 或 domain）。
- 環境變數：`CLASSIFICATION_METADATA_<SOURCE>` 或 `CLASSIFICATION_METADATA_<DOMAIN>`（值為檔案路徑或 inline JSON）。`--check-readiness` 只讀環境變數，不讀 `--source-classification`。
- 擷取子程序：runner 把解析後的 artifact 寫成 `<output-dir>/<domain>.<source>.classification.json`，以 `--classification-metadata` 交給 `capture_public_source_evidence.py`；retention 子程序收到 `classification-artifacts-<uuid>.json`（`--classification-artifacts`）。
- Cloud Run retention job：`scripts/run_emgi_raw_retention_job.py --classification <JSON 檔，以 source/domain 為鍵>`，staging 為 `classification.json`，並以 `oday.emgi/classification-sha256` 記錄檔案雜湊。
- 既有封裝檔（09-21 的 MOF/RIS bundle）內的 normalized receipt `classification_digest` 為 `None`，直接 `--bundle-path --live` 一定 `CLASSIFICATION_UNAVAILABLE`。要用 `--resume --source-classification ...` 重組，讓 digest 綁進新 bundle。

## 3. 逐來源審閱

### 3.1 `mof`（domain `mof_moi`）

- provider_id `provider.mof`；contract_id `emgi.source.mof-business.v1`；record model `MOFBusinessRecord`
- schema_sha256 `03cb313fe2221ea9bd02595ed84f516eed65cf26ce75227e250a39aa449ef25c`
- 對應 policy source `mof_business`，收據變數 `ODAY_SOURCE_MOF_BUSINESS_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `278fb65e74332b65ed024eecf891784ad1907e96b3d1a99760d255a5dcd05781`

**判斷說明**

- businessNm / businessAddress / registered_address / Company_Location 判 PUBLIC：屬法定公示的營業登記資料；但獨資/小規模行號的名稱可能含自然人姓名、地址可能是住居所。若要保守，可升為 INTERNAL。
- 實際 eip.fia.gov.tw API（2026-09-21 擷取）沒有負責人欄位；Responsible_Name 等僅出現在 DEFAULT 清單與 parser 別名，仍判 RESTRICTED。

RESTRICTED 欄位：`Master_Name`、`Responsible_Name`、`responsible_person`、`負責人姓名`

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `Busi_Addr`、`Busi_Chi_Name`、`business_name`、`registered_address`、`營利事業名稱`、`營業人名稱`、`營業地址`、`登記地址` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（parse_mof_record）；商業登記公開欄位 | parser 別名 | ● |
| `Company_Location`、`Company_Name` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；商業登記公開欄位 | DEFAULT 清單 | ● |
| `businessAddress`、`businessNm` | PUBLIC | 財政部營業（稅籍）登記公開資料欄位（eip.fia.gov.tw businessRegistration；2026-09-21 實際擷取鍵）；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 | ● |
| `Master_Name`、`responsible_person`、`負責人姓名` | **RESTRICTED** | parser 接受的別名鍵（目前供應者未必送出）（parse_mof_record）；負責人自然人姓名＝個人資料 | parser 別名 | ● |
| `Responsible_Name` | **RESTRICTED** | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；負責人自然人姓名＝個人資料 | DEFAULT 清單 | ● |
| `Approved_Date`、`Busi_Ban`、`Busi_Item`、`Busi_Item_Name`、`Busi_Status`、`Capital`、`Capital_Stock_Amount`、`Change_Date`、`Chg_Date`、`City`、`District`、`Organization`、`Organization_Desc`、`Setup_Date`、`Use_Invoice`、`capital_amount`、`change_date`、`county`、`established_date`、`industry_codes`、`industry_names`、`organization_type`、`status`、`township`、`use_uniform_invoice`、`使用統一發票`、`營業狀況`、`組織別`、`統一編號`、`縣市`、`行業代號`、`行業名稱`、`設立日期`、`變更日期`、`資本額`、`鄉鎮市區` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（parse_mof_record）；商業登記公開欄位 | parser 別名 |  |
| `Business_Accounting_NO`、`Company_Status_Desc` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；商業登記公開欄位 | DEFAULT 清單 |  |
| `ban`、`businessSetupDate`、`businessType`、`capitalAmount`、`headquartersBan`、`industryCd`、`industryCd1`、`industryCd2`、`industryCd3`、`industryNm`、`industryNm1`、`industryNm2`、`industryNm3`、`isUseInvoice` | PUBLIC | 財政部營業（稅籍）登記公開資料欄位（eip.fia.gov.tw businessRegistration；2026-09-21 實際擷取鍵）；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 |  |

**fields（正規化 `MOFBusinessRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `business_name`、`registered_address` | PUBLIC | 商業登記公開欄位正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | ● |
| `responsible_person` | **RESTRICTED** | 負責人自然人姓名＝個人資料（個資法） | ● |
| `is_candidate_evidence`、`is_physical_store_truth` | *INTERNAL* | 平台固定旗標（候選證據/非實體店真值） |  |
| `metadata` | *INTERNAL* | 只含 raw_source_keys（來源鍵名清單），無值 |  |
| `ban`、`capital_amount`、`change_date`、`established_date`、`industry_codes`、`industry_names`、`organization_type`、`status`、`use_uniform_invoice` | PUBLIC | 商業登記公開欄位正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |
| `county`、`township` | PUBLIC | 由 registered_address 推導的行政區 |  |
| `is_active`、`status_normalized` | PUBLIC | 由 status 正規化推導 |  |

### 3.2 `moi`（domain `mof_moi`）

- provider_id `provider.moi`；contract_id `emgi.source.moi-rental.v1`；record model `MOIRentalRecord`
- schema_sha256 `7653e9a56f073107f48374edc205dfda7bb0f0f4266eb809e07f6c4c7c09fd0d`
- 對應 policy source `moi_rental`，收據變數 `ODAY_SOURCE_MOI_RENTAL_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `b4f5cb16c75ed559b2950348e235e40972e6f01457193bf931783b3853bd5218`

**判斷說明**

- 土地位置建物門牌 / address_or_section 判 INTERNAL（非 PUBLIC）：官方已區段化，但與租金、樓層組合後可能指向特定住戶。
- 備註 / notes 判 INTERNAL：自由文字。
- DEFAULT 清單的英文欄名（build_type、monthly_rent_twd…）不是官方 CSV 表頭；只為讓 runner 離線/就緒檢查通過而列入。

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `address`、`address_or_section`、`土地區段位置建物區段門牌` | *INTERNAL* | parser 接受的別名鍵（目前供應者未必送出）；住宅位置準識別欄位 | parser 別名 | ● |
| `notes` | *INTERNAL* | parser 接受的別名鍵（目前供應者未必送出）；自由文字備註 | parser 別名 | ● |
| `note` | *INTERNAL* | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；自由文字備註 | DEFAULT 清單 | ● |
| `備註` | *INTERNAL* | lvr_land_c 官方表頭（115S2 臺北市 A_lvr_land_C.csv，轉錄於 tests/sources/official_source_fixtures.py）；自由文字，可能夾帶特殊交易關係描述，保守判 INTERNAL | 供應者實際/官方欄 | ● |
| `土地位置建物門牌` | *INTERNAL* | lvr_land_c 官方表頭（115S2 臺北市 A_lvr_land_C.csv，轉錄於 tests/sources/official_source_fixtures.py）；官方已門牌區段化，但屬住宅位置準識別欄位，保守判 INTERNAL | 供應者實際/官方欄 | ● |
| `District`、`area`、`berthCategory`、`berthFloor`、`berthPrice`、`buildingArea`、`building_completion_date`、`building_state`、`county`、`county_name`、`dataset_type`、`district`、`has_elevator`、`has_furniture`、`has_manager`、`id`、`lease_period_end`、`lease_period_start`、`parking_rent_twd`、`parking_space_type`、`primary_building_materials`、`primary_use`、`rentPrice`、`rent_total_twd`、`rent_unit_price_twd`、`rental_area_sqm`、`rental_type`、`serial_no`、`target`、`target_floor`、`totalFloor`、`total_floors`、`total_price`、`transaction_date`、`transaction_id`、`transaction_target`、`transaction_year_month`、`unitPrice`、`交易年月日`、`單價元`、`有無附家具`、`租賃型態`、`租賃總面積平方公尺`、`租賃起日`、`租賃起訖年月日`、`租賃迄日` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（parse_moi_rental_record） | parser 別名 |  |
| `build_type`、`building_area_sqm`、`contract_date`、`county_code`、`district_name`、`floor`、`manage_org_included`、`monthly_rent_twd`、`rent_type`、`rooms`、`season`、`total_floor` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；實價登錄租賃公開欄位 | DEFAULT 清單 |  |
| `主要建材`、`主要用途`、`交易標的`、`出租型態`、`單價元平方公尺`、`土地面積平方公尺`、`建物型態`、`建物現況格局-廳`、`建物現況格局-房`、`建物現況格局-衛`、`建物現況格局-隔間`、`建物總面積平方公尺`、`建築完成年月`、`有無管理員`、`有無管理組織`、`有無附傢俱`、`有無電梯`、`租賃住宅服務`、`租賃層次`、`租賃年月日`、`租賃期間`、`租賃筆棟數`、`編號`、`總樓層數`、`總額元`、`車位總額元`、`車位面積平方公尺`、`車位類別`、`都市土地使用分區`、`鄉鎮市區`、`附屬設備`、`非都市土地使用分區`、`非都市土地使用編定` | PUBLIC | lvr_land_c 官方表頭（115S2 臺北市 A_lvr_land_C.csv，轉錄於 tests/sources/official_source_fixtures.py）；內政部實價登錄租賃公開資料；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 |  |

**fields（正規化 `MOIRentalRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `address_or_section` | *INTERNAL* | 門牌區段化住址；住宅位置準識別欄位，保守判 INTERNAL | ● |
| `notes` | *INTERNAL* | 自由文字備註，保守判 INTERNAL | ● |
| `metadata` | *INTERNAL* | 只含 raw_keys（來源鍵名清單），無值 |  |
| `release_key` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `building_completion_date`、`building_state`、`county`、`county_code`、`district`、`has_elevator`、`has_furniture`、`has_manager`、`is_rental`、`lease_period_end`、`lease_period_start`、`parking_rent_twd`、`parking_space_type`、`primary_building_materials`、`primary_use`、`rent_total_twd`、`rent_unit_price_twd`、`rental_area_ping`、`rental_area_sqm`、`rental_type`、`target_floor`、`total_floors`、`transaction_id`、`transaction_target`、`transaction_year_month` | PUBLIC | 實價登錄租賃公開欄位正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.3 `ris`（domain `ris_nlsc`）

- provider_id `provider.moi.ris`；contract_id `emgi.source.ris.v1`；record model `RISPopulationRecord`
- schema_sha256 `2c1ddca8b2c81b7427a85976190356a4686cd38ddc476772c6d1965c91108765`
- 對應 policy source `ris_population`，收據變數 `ODAY_SOURCE_RIS_POPULATION_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `33c3ab36ae286f42134eff5788a57f0b10b5e181e13cb8eec4d50b742e18951a`

**判斷說明**

- people_age_###_f/m 判 PUBLIC：官方公開彙總統計；但小村里的高齡單一年齡×性別格可能只有 1 人（小格數）。若要求小格保護，需另行在下游遮蔽，不是分類 artifact 能處理的。

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `age_brackets` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（RISAdapter）；人口彙總統計 | parser 別名 | ● |
| `people_age_000_f` … `people_age_100up_m`（202 欄，0–99 歲與 100 歲以上 × f/m） | PUBLIC | 戶政司 ODRP014 API（2026-09-21 臺北市實際擷取鍵）；村里單一年齡×性別人口彙總統計；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 | ● |
| `CODE`、`COUNTY`、`TOWN`、`VILLAGE`、`admin_code`、`age_0_14`、`age_15_64`、`age_65_plus`、`city_name`、`county_code`、`county_name`、`district_name`、`female`、`female_count`、`household_count`、`household_total`、`households`、`male`、`male_count`、`population`、`population_female`、`population_male`、`population_total`、`total_population`、`town_code`、`town_name`、`village_code`、`village_name` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（RISAdapter）；人口彙總統計 | parser 別名 |  |
| `district_code`、`household_no`、`people_total`、`people_total_f`、`people_total_m`、`site_id`、`statistic_yyymm`、`village` | PUBLIC | 戶政司 ODRP014 API（2026-09-21 臺北市實際擷取鍵）；村里戶數/人口彙總統計；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 |  |
| `page`、`pageDataSize`、`responseCode`、`responseData`、`responseMessage`、`totalDataSize`、`totalPage` | PUBLIC | 戶政司 ODRP014 API（2026-09-21 臺北市實際擷取鍵）；API 回應/分頁封套；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 |  |

**fields（正規化 `RISPopulationRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `age_brackets` | PUBLIC | 村里人口彙總統計正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | ● |
| `observed_at`、`release_key` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `admin_code`、`admin_level`、`aging_index`、`child_population`、`county_code`、`county_name`、`elderly_dependency_ratio`、`elderly_population`、`female_count`、`household_count`、`male_count`、`population_total`、`sex_ratio`、`town_code`、`town_name`、`village_code`、`village_name`、`working_age_population` | PUBLIC | 村里人口彙總統計正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.4 `nlsc_register`（domain `ris_nlsc`）

- provider_id `provider.moi.nlsc`；contract_id `emgi.source.nlsc-admin-code.v1`；record model `NLSCAdminCodeRecord`
- schema_sha256 `fcce035d0a8d31cfd2368ce250fe8c17c5b5d1f828a069a162fc183a9da4a3b9`
- 對應 policy source `nlsc_boundary`，收據變數 `ODAY_SOURCE_NLSC_BOUNDARY_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `8e5eda437607ba984ca3cc6960872f025971c2c06d4b8c01487c674183e58de0`

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `admin_code`、`admin_level`、`citycode`、`cityname`、`cityno`、`county_code`、`county_name`、`district_code`、`district_name` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；行政區代碼公開資料 | DEFAULT 清單 |  |
| `countycode`、`countycode01`、`countyname` | PUBLIC | 國土測繪中心 ListCounty（parse_county_register 讀取的實際鍵）；行政區代碼公開資料；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 供應者實際/官方欄 |  |

**fields（正規化 `NLSCAdminCodeRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `observed_at`、`release_key` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `admin_code`、`admin_level`、`admin_name`、`legacy_code`、`parent_code` | PUBLIC | 行政區代碼公開資料正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.5 `nlsc_boundary`（domain `ris_nlsc`）

- provider_id `provider.moi.nlsc`；contract_id `emgi.source.nlsc.v1`；record model `NLSCBoundaryRecord`
- schema_sha256 `0422cba26a3065972385dae6141854953227800e7b1260900d3d7869e3f6a866`
- 對應 policy source `nlsc_boundary`，收據變數 `ODAY_SOURCE_NLSC_BOUNDARY_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `0a0a52df41b4368a65c8846990cdfa48eac8e6cc9a9da8f235acd5018ac944c1`

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `CODE`、`COUNTY`、`NAME`、`TOWN`、`VILLAGE`、`admin_code`、`admin_name`、`city_name`、`county_code`、`county_name`、`district_name`、`town_code`、`town_name`、`village_code`、`village_name` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（NLSCAdapter）；界圖屬性 | parser 別名 |  |
| `NOTE` | PUBLIC | data.gov.tw 7439/7440 fieldDesc 列出的村里界 DBF 欄位（DPF-NLSC-OFFICIAL-ZIP-PREPROCESSOR-001/catalog）；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | 官方目錄欄位 |  |
| `COUNTYCODE`、`COUNTYENG`、`COUNTYID`、`COUNTYNAME`、`TOWNCODE`、`TOWNENG`、`TOWNID`、`TOWNNAME`、`VILLCODE`、`VILLENG`、`VILLNAME` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；村里/鄉鎮/縣市界圖 DBF 屬性（data.gov.tw 7439/7441/7442 fieldDesc 亦列）；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | DEFAULT 清單 |  |

**fields（正規化 `NLSCBoundaryRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `observed_at`、`release_key` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `admin_code`、`admin_level`、`admin_name`、`area_sqm`、`centroid`、`county_code`、`county_name`、`geometry_geojson`、`geometry_wkt`、`h3_cells`、`is_valid_geometry`、`is_within_taiwan`、`srid`、`town_code`、`town_name`、`village_code`、`village_name` | PUBLIC | 行政界線幾何與代碼正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.6 `cwa_weather`（domain `cwa_events`）

- provider_id `provider.cwa`；contract_id `emgi.source.operating-context.v1`；record model `CWAStationObservation`
- schema_sha256 `693809ab8f6f9289915ccd60cf6185ad59cf71cdb264182b9654a81d9dd442c4`
- 對應 policy source `cwa`，收據變數 `ODAY_SOURCE_CWA_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `423d70a099b96b00cc85951b11c22cc3071f67d17b5feb7175301b11c7807a22`

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `AirPressure`、`AirTemperature`、`CountyCode`、`CountyName`、`DailyPrecipitation`、`Now`、`RelativeHumidity`、`StationAltitude`、`StationLatitude`、`StationLongitude`、`TownCode`、`TownName`、`UVIndex`、`Weather`、`WindDirection`、`WindSpeed`、`air_pressure_hpa`、`air_temperature_c`、`altitude_m`、`comfort_index`、`county_code`、`county_name`、`humidity`、`id`、`lat`、`latitude`、`lng`、`lon`、`longitude`、`name`、`observed_at`、`precipitation`、`precipitation_1h_mm`、`precipitation_24h_mm`、`relative_humidity_pct`、`station_id`、`station_name`、`temperature`、`timestamp`、`town_code`、`town_name`、`uv_index`、`weather_condition`、`weather_description`、`wind_direction_deg`、`wind_speed_mps` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（CWAAdapter.parse_station_observations）；測站觀測公開資料 | parser 別名 |  |
| `GeoInfo`、`ObsTime`、`Station`、`StationId`、`StationName`、`WeatherElement` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；氣象署 O-A0001-001 測站觀測公開資料；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | DEFAULT 清單 |  |

**fields（正規化 `CWAStationObservation` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `air_pressure_hpa`、`air_temperature_c`、`altitude_m`、`comfort_index`、`county_code`、`county_name`、`h3_cell`、`latitude`、`longitude`、`observed_at`、`precipitation_1h_mm`、`precipitation_24h_mm`、`relative_humidity_pct`、`station_id`、`station_name`、`town_code`、`town_name`、`uv_index`、`weather_condition`、`weather_description`、`wind_direction_deg`、`wind_speed_mps` | PUBLIC | 氣象測站觀測正規化（測站為政府設施，非個人）；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.7 `cwa_hazard`（domain `cwa_events`）

- provider_id `provider.cwa`；contract_id `emgi.source.operating-context.v1`；record model `CWAHazardWarning`
- schema_sha256 `59b130a57ea2939120640de1b968977179205c55f26cdac5b7e26c3172c90b20`
- 對應 policy source `cwa`，收據變數 `ODAY_SOURCE_CWA_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `b288a5bcafe7c564b473fc19649335a36458ddf506fa2e2d0df72073772ecb01`

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `metadata` | *INTERNAL* | parser 接受的別名鍵（目前供應者未必送出）；附帶中繼資料，內容未定義 | parser 別名 |  |
| `Description`、`EffectiveTime`、`ExpireTime`、`HazardType`、`Headline`、`Identifier`、`SentTime`、`Severity`、`Status`、`affected_h3_cells`、`counties`、`event`、`id`、`towns` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（CWAAdapter.parse_hazard_warnings）；特報公開資料 | parser 別名 |  |
| `affected_counties`、`affected_towns`、`datasetInfo`、`description`、`effective_from`、`effective_to`、`hazardConditions`、`hazard_type`、`headline`、`issued_at`、`records`、`severity`、`status`、`title`、`warning_id` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；氣象署 W-C0033-001 天氣特報公開資料；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 | DEFAULT 清單 |  |

**fields（正規化 `CWAHazardWarning` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `metadata` | *INTERNAL* | 原樣帶入 row.metadata，內容未定義，保守判 INTERNAL |  |
| `affected_counties`、`affected_h3_cells`、`affected_towns`、`description`、`effective_from`、`effective_to`、`hazard_type`、`headline`、`issued_at`、`severity`、`status`、`title`、`warning_id` | PUBLIC | 天氣/災害特報正規化；政府資料開放授權條款第1版（data.gov.tw/license）公開資料 |  |

### 3.8 `osm`（domain `transport_osm_tdx`）

- provider_id `provider.openstreetmap`；contract_id `emgi.source.osm-pbf.v1`；record model `OSMRoadEdge`
- schema_sha256 `52270ba79e17ccb40c5e980cdd265436a695755340c5bafc8f7293d63eba9453`
- 對應 policy source `osm`，收據變數 `ODAY_SOURCE_OSM_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `21276366ec1e96d6f81673a8dc49e22c2ee2871f00f7062fa0bbcb64e4cdc2bc`

**判斷說明**

- tags 判 PUBLIC：ODbL 公開資料；保留的原始 PBF 含全部節點/路徑標籤（不只道路），可能有 contact:phone/email 等。
- 程式不解碼 PBF 的 user/uid/changeset；DEFAULT 清單也沒有。保留的 PBF 是否含這些貢獻者欄位取決於下載物件（Geofabrik 公開版文件稱已移除，需在核准時確認）。若含，原始 bytes 內就有貢獻者識別資料，應判 RESTRICTED。

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `tags` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS；OSM 自由標籤（ODbL 1.0 公開資料；可能含 POI 名稱/電話/網址，屬公開貢獻） | DEFAULT 清單 | ● |
| `elements`、`lat`、`lon` | PUBLIC | _osm_pbf_to_elements 解碼後元素鍵（座標/集合封套）；ODbL 1.0 公開資料 | parser 別名 |  |
| `id`、`nodes`、`timestamp`、`type`、`version` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS；OSM 元素識別/版本/時間（ODbL 1.0 公開資料） | DEFAULT 清單 |  |

**fields（正規化 `OSMRoadEdge` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `access`、`backward_access`、`edge_id`、`forward_access`、`from_node`、`geometry`、`h3_indices`、`highway_type`、`length_meters`、`maxspeed_kph`、`oneway`、`to_node`、`way_id` | PUBLIC | OSM 道路網邊正規化（ODbL 1.0，需署名/相同方式分享） |  |

### 3.9 `tdx_parking`（domain `transport_osm_tdx`）

- provider_id `provider.tdx`；contract_id `emgi.source.tdx-parking.v1`；record model `TDXParkingLotRecord`
- schema_sha256 `4c0fe074ac78728c862c099b1c7db0768009a4fd83bb2db82c17a8c0b2332f2b`
- 對應 policy source `tdx`，收據變數 `ODAY_SOURCE_TDX_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `5950cb7512c185fd7a8ad037c212e84e472c7c7c03da846f874fd0af2cd21221`

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `Address`、`City`、`County`、`District`、`Latitude`、`Longitude`、`ParkingLotID`、`ParkingLotName`、`ParkingLots`、`Position`、`PositionLat`、`PositionLon`、`Township`、`UpdateTime`、`address`、`available`、`capacity`、`county`、`id`、`lat`、`lon`、`name`、`parking_lot_id`、`parking_name`、`remaining_spaces`、`source_update_time`、`total_spaces`、`township` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（TDXAdapter.parse_parking_raw）；停車場公開資料 | parser 別名 |  |
| `AvailableSpaces`、`CarParkID`、`CarParkName`、`TotalSpaces` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS；TDX 路外停車場名稱/代碼/格位公開資料 | DEFAULT 清單 |  |

**fields（正規化 `TDXParkingLotRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `observed_at` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `address`、`county`、`fee_description`、`h3_index`、`lat`、`lon`、`occupancy_rate`、`parking_lot_id`、`parking_name`、`remaining_spaces`、`service_type`、`source_update_time`、`status`、`total_spaces`、`township` | PUBLIC | TDX 路外停車場與即時格位正規化（停車場為設施，非個人） |  |

### 3.10 `overture`（domain `open_poi`）

- provider_id `provider.overture`；contract_id `emgi.source.overture-places.v1`；record model `SourceObservation`
- schema_sha256 `8f5bd9a6a249fa519fb2edee2ee7367888ede3ebba1849d4456d6bd6b219c21e`
- 對應 policy source `overture`，收據變數 `ODAY_SOURCE_OVERTURE_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `6fc28655190b0319bd118e80a02473714f7d3572d3ccb7081a77e3ada22fe4ac`

**判斷說明**

- phones / emails / socials 判 RESTRICTED（依「個人電話/信箱」規則保守處理；商家聯絡方式與個人無法區分）。注意：這三欄只存在原始 Parquet bytes，payload_excerpt 不含。
- addresses 判 PUBLIC：POI 營業地址；家庭式工作室可能是住址，若要保守可升 INTERNAL。
- 實際 Parquet 欄位清單不在程式內（runner 在擷取時讀 footer）。新版 Overture 若多出 operating_status、basic_category、taxonomy 等欄，live gate 會以 raw_source_fields 缺漏拒絕，需補欄重簽。

RESTRICTED 欄位：`emails`、`phones`、`socials`

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `emails` | **RESTRICTED** | Overture Maps places 欄位（CDLA-Permissive-2.0）；電子郵件，可能是個人信箱，保守判 RESTRICTED | parser 別名 | ● |
| `phones` | **RESTRICTED** | Overture Maps places 欄位（CDLA-Permissive-2.0）；電話，可能是個人/獨資業者手機，無法區分，保守判 RESTRICTED | parser 別名 | ● |
| `socials` | **RESTRICTED** | Overture Maps places 欄位（CDLA-Permissive-2.0）；社群帳號連結，可能是個人帳號，保守判 RESTRICTED | parser 別名 | ● |
| `address`、`addresses`、`freeform` | PUBLIC | Overture Maps places 欄位（CDLA-Permissive-2.0）；POI 營業地址（商家地點，非個人住址） | parser 別名 | ● |
| `data`、`features`、`items`、`places`、`properties`、`records`、`results`、`stores`、`type` | *INTERNAL* | defs/external/poi.py 認得的集合/GeoJSON 封套鍵（JSON 輸入時） | parser 別名 |  |
| `bbox`、`brand`、`categories`、`confidence`、`geometry`、`id`、`names`、`sources`、`version`、`websites` | PUBLIC | Overture Maps places 欄位（CDLA-Permissive-2.0）；record_reader._normalise 讀取的頂層欄（POI 名稱/類別/幾何/來源） | parser 別名 |  |
| `category`、`lat`、`latitude`、`lon`、`longitude`、`main_category`、`name`、`primary_name`、`x`、`y` | PUBLIC | parser 接受的別名鍵（目前供應者未必送出）（record_reader）；POI 名稱/類別/座標 | parser 別名 |  |

**fields（正規化 `SourceObservation` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `payload_excerpt` | PUBLIC | Overture Maps places 欄位（CDLA-Permissive-2.0）；excerpt 只含 name/category/confidence/coordinates/brand/addresses/upstream_sources，不含 phones/emails/socials | ● |
| `available_at`、`blob_id`、`dataset_version_id`、`fetched_at`、`first_seen_at`、`ingested_at`、`last_seen_at`、`observation_id`、`observed_at`、`scope_principal_id`、`status` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `confidence`、`source_entity_id` | PUBLIC | Overture Maps places 欄位（CDLA-Permissive-2.0）；GERS ID 與信心值 |  |

### 3.11 `foursquare`（domain `open_poi`）

- provider_id `provider.foursquare`；contract_id `emgi.source.foursquare-places.v1`；record model `SourceObservation`
- schema_sha256 `928d84baf4b1d92bb99249595ea9d0e21fad9fd9ece1d79d8bba212652a57100`
- 對應 policy source `foursquare`，收據變數 `ODAY_SOURCE_FOURSQUARE_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `13d2e8f75e5c9a71eebe1a034179b6c389695273f1b3bbe69a37bc51d7786946`

**判斷說明**

- 程式只讀上列鍵；FSQ OS Places 實際檔可能還有 tel、email、website、instagram、facebook_id 等欄（程式未引用，無法確認）。若 live 擷取觀察到這些鍵，gate 會拒絕；補欄時 tel/email/社群帳號應判 RESTRICTED。
- foursquare 目前另有 PARQUET_READER_GAP：URI 為 .parquet 時 readiness 直接回 ADAPTER_GAP。

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `address` | PUBLIC | Foursquare OS Places 欄位（Apache-2.0）；POI 營業地址（商家地點） | parser 別名 | ● |
| `data`、`features`、`items`、`places`、`properties`、`records`、`results`、`stores`、`type` | *INTERNAL* | defs/external/poi.py 認得的集合/GeoJSON 封套鍵 | parser 別名 |  |
| `metadata` | *INTERNAL* | Foursquare OS Places 欄位（Apache-2.0）；附帶中繼資料，內容未定義 | parser 別名 |  |
| `chain_id`、`chain_name`、`confidence`、`country`、`date_closed`、`date_created`、`date_refreshed`、`delta_action`、`fsq_category_ids`、`fsq_category_labels`、`fsq_place_id`、`latitude`、`locality`、`longitude`、`merge_target_fsq_id`、`name`、`postcode`、`region` | PUBLIC | Foursquare OS Places 欄位（Apache-2.0）；FoursquarePlacesAdapter.parse_record 讀取的鍵 | parser 別名 |  |

**fields（正規化 `SourceObservation` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `payload_excerpt` | PUBLIC | Foursquare OS Places 欄位（Apache-2.0）；excerpt 含 name/coordinates/address/locality/categories/chain/delta_action/is_closed | ● |
| `available_at`、`blob_id`、`dataset_version_id`、`fetched_at`、`first_seen_at`、`ingested_at`、`last_seen_at`、`observation_id`、`observed_at`、`scope_principal_id`、`status` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |
| `confidence`、`source_entity_id` | PUBLIC | Foursquare OS Places 欄位（Apache-2.0）；fsq_place_id 與信心值 |  |

### 3.12 `trtc`（domain `mobility`）

- provider_id `provider.mobility`；contract_id `emgi.mobility-observation.v1`；record model `ObservedFlowRecord`
- schema_sha256 `9e79d5e487d49a246d93f0075bbcd396eb184de7ddeb4deff818e87f89276328`
- 對應 policy source `mobility`，收據變數 `ODAY_SOURCE_MOBILITY_APPROVAL_RECEIPT_SHA256`
- 草稿 digest `0e63d7b3ec54fe44ac4b4b1ecf298c278d106e05d18017349cad024410c57739`

**判斷說明**

- 與交辦規則衝突：交辦把 mobility 視為「專有契約 feed → 至少 CONFIDENTIAL」，但此 lane 的實際來源是臺北捷運公開資料（TRTC_STATION_OD_TERMS：OGDL 1.0、免費、營運者已彙總）。草稿依實際條款判 PUBLIC；若負責人要求依規則處理，應全部升為 RESTRICTED（本 enum 無 CONFIDENTIAL）。
- trtc 另有 retention_boundary_gap RECORD_FIELD_COLLIDES_WITH_RETENTION_PROVENANCE，分類 BOUND 後仍會在 retention 被拒。

RESTRICTED 欄位：無

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `entry_station_id`、`exit_station_id`、`hourly_entry_exit_flow`、`transaction_date`、`transaction_hour` | PUBLIC | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；站間逐時彙總人次 | DEFAULT 清單 | ● |
| `人次`、`出站`、`日期`、`時段`、`進站` | PUBLIC | 臺北捷運各站分時進出量 CSV 表頭（trtc_station_od.STATION_OD_HEADER）；營運者已彙總、無個別旅客；政府資料開放授權條款第1版 | 供應者實際/官方欄 | ● |

**fields（正規化 `ObservedFlowRecord` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `business_date`、`contract_id`、`destination_unit`、`feed_id`、`flow_count`、`measure`、`modality`、`origin_unit`、`provider_id`、`time_contract`、`unit` | PUBLIC | 站對站逐時彙總人次（TRTC_STATION_OD_TERMS：OGDL 1.0、營運者發布前已彙總） | ● |
| `blob_id`、`coverage_id`、`coverage_state`、`dataset_version_id`、`metadata`、`negative_evidence_valid`、`observation_id`、`provenance`、`record_id`、`scope_principal_id` | *INTERNAL* | 平台產生的血緣/作業中繼資料，非供應者內容 |  |

### 3.13 `site_context`（domain `site_market_context`）

- provider_id `provider.site_context`；contract_id `emgi.site-market-context.v1`；record model `SiteMarketContextDocument`
- schema_sha256 `d1c0934c8e37e76f1546f9b4e503b78b7b9cab86f4e9ce2a3340cddb0428a8ae`
- 對應 policy source `（無）`，收據變數 `（無；衍生產品）`
- 草稿 digest `fd6ae5756f34ccba06c1457a9f75eea93a1dcc1ea734a69fe1c8acf7950f22ff`

**判斷說明**

- contexts 判 RESTRICTED：客戶點位評估屬商業機密（本 enum 無 CONFIDENTIAL，故取 RESTRICTED）；另 service.py:826 以 representative_name 計算 brands_present/stores_by_brand，若上游記錄帶此欄就會把自然人姓名寫進 contexts。
- tenant_id 判 RESTRICTED（帳號識別碼規則）。
- 此 lane 的 classification_policy 是 DERIVED_PRODUCT_ARTIFACT_REQUIRED：分類只涵蓋衍生文件，不取代各組件來源的分類。

RESTRICTED 欄位：`contexts`、`tenant_id`

**raw_source_fields（供應者原始欄）**

| 欄位 | 分類 | 依據 | 欄位來源 | 判斷 |
|---|---|---|---|---|
| `contexts` | **RESTRICTED** | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；客戶候選點位的商圈衍生內容（客戶商業機密） | DEFAULT 清單 | ● |
| `component_manifest_refs`、`document_id`、`effective_as_of`、`generated_at`、`knowledge_as_of`、`period_key`、`source_support` | *INTERNAL* | DEFAULT_OBSERVED_RAW_FIELDS 預設名稱（離線/就緒檢查必列；非供應者實際欄名）；平台衍生產品文件的血緣/期間欄 | DEFAULT 清單 |  |

**fields（正規化 `SiteMarketContextDocument` 欄位）**

| 欄位 | 分類 | 依據 | 判斷 |
|---|---|---|---|
| `contexts` | **RESTRICTED** | 客戶候選點位的商圈衍生內容（客戶商業機密）；競品彙總以 representative_name 分組，若上游帶入該欄會含自然人姓名 | ● |
| `tenant_id` | **RESTRICTED** | 客戶租戶識別碼（帳號識別） | ● |
| `component_manifest_refs`、`contract_version`、`document_id`、`effective_as_of`、`generated_at`、`knowledge_as_of`、`metadata`、`period_grain`、`period_key`、`product_version`、`scope_principal_id`、`source_support` | *INTERNAL* | 平台衍生產品文件的版本/血緣/期間欄 |  |

## 4. 不需要或無法起草的 lane

- `fnp`：runner 內建 `reusable_classification_artifact('fnp')`，由 `FNP_LEASE_TENDER.extraction` 推導，已 BOUND。其 authority_reference 為 `https://data.gov.tw/dataset/9656` / 財政部國有財產署開放授權；`contact`、`tel` 判 RESTRICTED，其餘 PUBLIC。若負責人要改這份分類，需另交顯式 artifact 覆蓋內建值。
- `market_events`、`brand_locator`：見第 1 節，無 record model、無欄位清單。

## 5. 待負責人決定的問題

1. CONFIDENTIAL 對應：本 enum 沒有 CONFIDENTIAL，草稿一律用 RESTRICTED。是否接受？
2. `trtc`：實際來源是臺北捷運 OGDL 公開彙總資料，草稿判 PUBLIC，與交辦的「mobility 至少 CONFIDENTIAL」不同。要依條款還是依交辦規則？
3. 獨資行號名稱／地址（MOF）、POI 地址（Overture/Foursquare）、租賃門牌區段（MOI）：草稿分別判 PUBLIC / PUBLIC / INTERNAL，是否一致處理？
4. 未在程式中出現、但供應者實際可能送出的欄位（CWA 回應封套 `success`/`result`、TDX ParkingLot 其他欄、Overture 新欄、FSQ 的 tel/email、NLSC ListCounty 其他鍵）：草稿未列入（不杜撰）。live 擷取觀察到時 gate 會以 `raw_source_fields.<name> (observed in provider bytes)` 拒絕，需補欄後重簽。是否接受「先簽、遇到再補」？
5. artifact 不帶 `dataset_version_id`，因此一份簽署可套用到同一來源的所有 release。若要逐 release 簽，需加上該欄（驗證器會比對）。
6. RESTRICTED 沒有程式強制力（見 §0-4）。MOF 負責人姓名仍會進 raw 保留與 normalized 記錄。是否需要另開遮蔽／拒存工作？

## 附錄 A. runner lane 與 16 個 policy source 對照

| policy source_id | approval_receipt_env | runner lane |
|---|---|---|
| `cwa` | `ODAY_SOURCE_CWA_APPROVAL_RECEIPT_SHA256` | `cwa_weather`、`cwa_hazard` |
| `tdx` | `ODAY_SOURCE_TDX_APPROVAL_RECEIPT_SHA256` | `tdx_parking` |
| `market_events` | `ODAY_SOURCE_MARKET_EVENTS_APPROVAL_RECEIPT_SHA256` | `market_events`（ADAPTER_GAP；lane 未設 policy_source_id，且 provider_id `provider.market_events` ≠ policy 的 `provider.market_watch`） |
| `mof_business` | `ODAY_SOURCE_MOF_BUSINESS_APPROVAL_RECEIPT_SHA256` | `mof` |
| `osm` | `ODAY_SOURCE_OSM_APPROVAL_RECEIPT_SHA256` | `osm` |
| `moi_rental` | `ODAY_SOURCE_MOI_RENTAL_APPROVAL_RECEIPT_SHA256` | `moi` |
| `ris_population` | `ODAY_SOURCE_RIS_POPULATION_APPROVAL_RECEIPT_SHA256` | `ris` |
| `nlsc_boundary` | `ODAY_SOURCE_NLSC_BOUNDARY_APPROVAL_RECEIPT_SHA256` | `nlsc_register`、`nlsc_boundary` |
| `overture` | `ODAY_SOURCE_OVERTURE_APPROVAL_RECEIPT_SHA256` | `overture` |
| `foursquare` | `ODAY_SOURCE_FOURSQUARE_APPROVAL_RECEIPT_SHA256` | `foursquare` |
| `brand_locators` | `ODAY_SOURCE_BRAND_LOCATORS_APPROVAL_RECEIPT_SHA256` | `brand_locator`（ADAPTER_GAP；lane 未設 policy_source_id） |
| `tgos` | `ODAY_SOURCE_TGOS_APPROVAL_RECEIPT_SHA256` | 無 |
| `google_places_verifier` | `ODAY_SOURCE_GOOGLE_PLACES_APPROVAL_RECEIPT_SHA256` | 無（刻意列在 NOT_WIRED_ASSETS） |
| `listings` | `ODAY_SOURCE_LISTINGS_APPROVAL_RECEIPT_SHA256` | `fnp` |
| `mobility` | `ODAY_SOURCE_MOBILITY_APPROVAL_RECEIPT_SHA256` | `trtc` |
| `survey` | `ODAY_SOURCE_SURVEY_APPROVAL_RECEIPT_SHA256` | 無 |

`site_context` 是衍生 lane，沒有對應 policy source，也不需要收據。

## 附錄 B. Approval receipt 的現況

- `approval_receipt_digest()`（`src/oday_data_platform/external/policy/update.py`）只檢查環境變數是否為 64 位小寫十六進位。oday-data-platform 全 repo 沒有任何程式讀取、比對或驗證收據文件本身的內容或 schema；唯一用途是「有/無」判斷（policy gate、`defs/external/mobility.py`、`defs/external/transport.py`、readiness gap、稽核腳本）。
- 有文件化的收據格式：odayplus repo `docs/evidence/human-decisions/ODP-OSS-DECISION-PACK-001/unsigned-receipt-template.json`（`receipt_type: oss_legal_policy_approval`，含 `approved_by.principal_id/display_name/role`、`approval_reference`、`source_system`、`issued_at/expires_at/review_at`、`scope`、`decisions.per_source_data_approvals[]`（`source_id`、`disposition`、`approved_datasets`、`approved_usage`）、`evidence.*_sha256`、`integrity.content_sha256`）。它明寫「hash 只能證明完整性，不能證明批准權」。16 張逐來源決策卡在同目錄 `source-decision-cards.json`，全部 `PENDING_HUMAN_DECISION`。
- odayplus 的 `AuthoritativeReceiptVerifier`（`delivery_toolchain/security/generate_oss_notice.py`）只驗 OSS 授權豁免收據，不驗資料來源收據。
- runbook `docs/runbooks/external-source-update-policy.md` 要求「將權威收據內容與雜湊 readback 驗證」，但這一步是人工程序，沒有程式實作。

## 附錄 C. google_places_verifier 與「不保留 raw」條件的衝突

- runner 不會保留它：`SOURCE_LANES` 沒有此 lane，`NOT_WIRED_ASSETS` 明列 `google_places_verifier_manifest`（"verifier-only commercial source; raw display and retention as open POI forbidden"）。`raw_snapshot_retention` 與 retained-import 也沒有它的路徑。
- 但 Dagster asset 本身會保留 raw response：
  - `src/oday_data_platform/defs/external/poi.py:781` `_do_live_fetch` 以 `EvidenceStore(storage_root=context.instance.storage_directory())` 建立 `LiveAcquisitionKernel`，供應者回應 bytes 會以 blob 寫入 Dagster instance 儲存目錄；`:728`/`:1111` 另把 raw_bytes 放進行程內 `_POI_RUN_CACHE`。
  - `poi.py:640-660`（`_persist_poi_domain_evidence` 的 google 分支）逐筆 `store.put_blob(...)`，並寫入 `"cache_raw_response": True`，註解直言 raw response 保留在 internal EvidenceStore。
  - `poi.py:1221-1231` 在收據上標 `"raw_response_persisted": True`、`raw_response_storage_uri`。
  - `src/oday_data_platform/external/sources/google_places_verifier/verifier.py:351` `GooglePlacesVerifier` 也 `put_blob` 比對結果。
  - `max_retention_days = 30`（`google_places_verifier/models.py:87`）只是 metadata，全 repo 沒有到期清除的程式。
- 若核准條件是「僅按需驗證、不保留 raw response」，需關掉的點：`_do_live_fetch` 對 `provider.google_places` 的 EvidenceStore 持久化（改為不落地或驗證後即刪）、`_persist_poi_domain_evidence` google 分支的 `put_blob` 與 `cache_raw_response: True`、`_POI_RUN_CACHE` 的 raw_bytes，以及 `verifier.py:351`。目前 `ODAY_SOURCE_GOOGLE_PLACES_ENABLED` 預設關閉，asset 不會執行。
