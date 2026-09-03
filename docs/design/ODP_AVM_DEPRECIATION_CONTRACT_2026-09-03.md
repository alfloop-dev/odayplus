# AVM 資產折舊契約：為什麼不共用模擬器，以及缺席的那半要怎麼補回來

- 日期：2026-09-03
- Task：`ODP-AVM-DEPRECIATION-CONTRACT-001`
- 需求：`ODP-FR-AVM-001`「GM_TTM／GM_FWD／折舊／資產／租約／正常化」
- 起因：[修復計畫第 1 批](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)；[FR 查證報告](../evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md) 的 `AVM-001` 列寫著「除折舊外皆有；折舊在 site_economics，AVM 不引用」
- 程式基準：`75d25f65`
- 可執行規格：[`modules/avm/tests/test_avm_depreciation_contract.py`](../../modules/avm/tests/test_avm_depreciation_contract.py)

本文是**契約，不是實作**。第 1 批的另一半（`quality_score` 由 `1.0` 改為 `None`）與這一半共用一個交付計畫，但各自驗收、各自回滾；本文不處理那一半。

---

## 先講追溯限制

repo 內沒有 `ODP-FR-AVM-001` 的條文原文。目前唯一的來源是 FR 查證報告裡的成員轉錄（六個成員：GM_TTM／GM_FWD／折舊／資產／租約／正常化），以及 `docs/design/ODP-SA-06-AMD-001.md` 對 `ODP-SA-06` 缺乏逐條驗收條件的描述。

所以本文的判定只能宣稱**依目前轉錄內容**成立。取得原始規格版本、位置與內容雜湊之前，不得把這份轉錄當成 canonical source——這是修復計畫對第 1 批明寫的條件，不是本文自加的保留。

本 task 一併把 `ODP-FR-AVM-001` 登記進 `delivery_toolchain/governance/set_valued_requirements.json`，六個成員中 `DEPRECIATION` 記為 `absent` 並指回本文。登記的作用是讓「這個成員缺席」變成機器會檢查的事實，不是宣稱裁決已完成。

---

## 判定：AVM-specific，而且連共用純函式都不抽

修復計畫要求先回答「是不是同一個折舊概念」。答案是**不是**，四個證據都在程式裡：

### 一、被量測的對象不同

`site_economics` 的折舊輸入是 format catalog 的機型組合——`format_spec.machine_mix.items`，逐項算 `item_capex`、`item_residual`、`item_life`（`modules/site_economics/domain/simulator.py:331-357`）。它描述的是「一家**還沒開**的店，如果照這個 format 買**全新**設備」。

模擬時鐘 `m` 從 1 開始就是全新設備，這個模型裡**沒有「已使用多久」這個維度**。

AVM 估的是一家**已在營運的特定門市**，它的設備已經用了 N 個月。缺的正是那個維度。

### 二、輸出的去向不同：稅盾，不是價值減損

模擬器裡折舊只走一條路：

```python
ebit = ebitda - (equip_depr + fitout_amort)      # simulator.py:470
taxable_income = ebit - interest_payment          # simulator.py:487
```

而現金流本身**繞過它**：

```python
unlevered_op_cf = ebitda - unlevered_tax          # simulator.py:519
levered_op_cf = ebitda - tax_expense - debt_service  # simulator.py:520
```

折舊在那裡是**稅盾**——它影響現金流的唯一路徑是稅。它從來沒有調低任何資產的帳面價值。

AVM 要的是相反的東西：資產價值隨使用時間減損，直接進 asset lens 的 `asset_p50`（`modules/avm/domain/valuation.py:368-374`）。同一個字，兩個位置，兩個意義。把模擬器的數字接過來，接到的是一個稅務中間量。

### 三、時鐘原點相反

模擬器：原點是開店月，往**後**推 `horizon_months`。
估值：原點是設備投入使用日，往**回**推到估值基準日。

一個共用函式若要同時服務兩者，第一個參數就得分歧。分歧的參數表示分歧的模型。

### 四、殘值不是同一種東西

模擬器的殘值是**期末退場的現金流入**：

```python
terminal_salvage = total_equipment_salvage        # simulator.py:528
```

估值的殘值是**折舊的下限**（carrying value floor），一個不會發生的現金事件，只是價值不再往下掉的那條線。

### 那為什麼連純函式都不抽

真正共用的只有直線折舊那個算式：

```python
depreciable = max(0.0, cost - residual)
monthly = depreciable / max(1, life_months)
```

三行國中數學。為它建立 `modules/avm` → `modules/site_economics` 的依賴，換到的是**「兩邊折舊政策一致」的假象**——而上面四點說明它們本來就不一致。下一次有人改動模擬器的稅務假設時，會有人以為估值也跟著對了。

`config/code-boundaries.yaml` 把兩者都歸在 `product_system`，所以邊界檢查**不會**擋這個 import。擋它的是 `test_avm_depreciation_contract.py::TestTheVerdictIsAVMSpecific::test_avm_does_not_import_site_economics`，那條測試就是這個判定的執行形式。

### 要對齊的是參數，不是程式碼

`MachineModelSpec.useful_life_months = 84`、`residual_value_ratio = 0.10`（`modules/site_economics/domain/models.py:61-62`）；catalog 內各機型實際落在 `0.05`–`0.12`（`modules/site_economics/domain/formats.py:39-40, 147-148`）。這是目前 repo 內**唯一有出處**的設備壽命與殘值假設。

注意 format 層另有一個**名字相近但不同**的欄位 `ResidualValueSpec.equipment_salvage_ratio`（`0.12`–`0.15`，`formats.py:281, 393`），它只在機型明細缺席時當 fallback（`simulator.py:353-357`）。抄參數時抄錯這一個，錯誤會安靜地生效。

AVM 的預設值如果與它相同，記錄成「引用同一份 catalog 假設」；如果不同，必須寫出為什麼不同。**由資料對齊，不由 import 對齊。**

---

## 契約

### C-1 折舊基數的歧義（最容易做錯的一步）

今天的 asset lens 是：

```python
asset_p50 = max(
    item.asset_book_value + item.equipment_fair_value
    + item.working_capital - item.lease_liability, 0.0
)                                                  # valuation.py:368-374
```

`equipment_fair_value` 這個名字宣稱它**已經是公允價值**。如果呼叫端真的送進一份獨立鑑價，再對它折舊一次就是重複扣減；如果呼叫端送的其實是取得成本（今天的 seed 與測試都是整數的取得成本樣態），不折舊就是漏扣。

**兩種情況不能用同一段程式碼靜默處理。** 契約因此不是「加幾個參數」，而是先加一個判別欄位：

| `equipment_depreciation_basis` | 意義 | 折舊處理 |
|---|---|---|
| `original_cost` | `equipment_original_cost` 是取得成本 | 依 C-3 計算 |
| `appraised_fair_value` | `equipment_fair_value` 是獨立鑑價結果 | **不折舊**，版本記為 `avm-depreciation-not-applicable-v1` |
| 未提供 | 呼叫端沒說 | **拒絕**，見 C-5 |

`asset_book_value` 有同一個歧義：會計帳面價值可能已含累計折舊。v1 定義 `asset_book_value` 為**不含設備**的淨帳面值；mapping 層要有 `asset_book_value_includes_equipment` 旗標，為真且 basis 為 `original_cost` 時拒絕，不得靜默相加。

### C-2 輸入欄位（`ValuationInput`）

全部預設 `None`。`None` 一律表示「未提供」，**任何一個都不得被靜默補成合理值**——那正是第 1 批在修的病。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `equipment_depreciation_basis` | `str \| None` | `original_cost` / `appraised_fair_value`，見 C-1 |
| `equipment_original_cost` | `float \| None` | 折舊基數；basis 為 `original_cost` 時必填 |
| `asset_in_service_date` | `date \| None` | 折舊時鐘原點（設備投入使用日），非採購日、非開店日 |
| `depreciation_effective_date` | `date \| None` | 估值基準日。**不得預設為 `prediction_origin_time`**——那會讓重跑同一筆輸入得到不同的卡 |
| `useful_life_months` | `int \| None` | `>= 1` |
| `residual_value_ratio` | `float \| None` | `0.0 <= r <= 1.0` |
| `depreciation_method` | `str \| None` | v1 只接受 `straight_line`；其他值**拒絕**，不是忽略 |

`asset_book_value_includes_equipment: bool | None` 依 C-1 一併加入。

### C-3 計算（v1，`straight_line`）

```
elapsed_months  = 完整月數(asset_in_service_date → depreciation_effective_date)，向下取整，下限 0
residual        = equipment_original_cost * residual_value_ratio
depreciable     = max(0.0, equipment_original_cost - residual)
monthly         = depreciable / max(1, useful_life_months)
accumulated     = min(depreciable, monthly * elapsed_months)
equipment_value_after_depreciation = equipment_original_cost - accumulated
```

由建構方式即滿足 `equipment_value_after_depreciation >= residual`。

`depreciation_effective_date` 早於 `asset_in_service_date` 時 `elapsed_months = 0`；但這是輸入矛盾，要在 evidence 記 `negative_elapsed_clamped`，不得靜默。

asset lens 改為：

```
asset_p50 = max(asset_book_value + equipment_value_after_depreciation
                + working_capital - lease_liability, 0.0)
```

`appraised_fair_value` 時 `equipment_value_after_depreciation == equipment_fair_value`，數字不變，但**版本欄位仍必須寫出來**，讓「沒折舊」與「不需折舊」在卡上可分辨。

### C-4 輸出與版本欄位

模組常數（新增於 `modules/avm/domain/valuation.py`）：

```python
AVM_DEPRECIATION_VERSION = "avm-depreciation-straight-line-v1"
AVM_DEPRECIATION_LEGACY_VERSION = "avm-depreciation-absent-v0"
```

它**必須獨立於** `AVM_MODEL_VERSION` 與 `AVM_POLICY_VERSION`（`valuation.py:10-12`）。折舊政策改版與模型改版是兩個可以各自發生的事件；壓在同一個字串裡，之後就分不出某張卡的差異來自哪一個。

`AVM_FEATURE_VERSION` 由 `valuation-view-v1` 升為 `valuation-view-v2`，因為 `ValuationInput` 的形狀變了。

`ValuationReport` 新增兩個**無預設值**的欄位：

- `depreciation_version: str`
- `depreciation_applied: bool`

無預設是刻意的：有預設就會出現一張沒人決定過版本、卻長得像有版本的卡。`value_store` 與 `build_model_valuation_report` 兩個建構點各自明寫。

asset lens 的 `evidence` 新增 `depreciation` 區塊，內容為 C-3 的每一個中間量：`basis`、`in_service_date`、`effective_date`、`elapsed_months`、`useful_life_months`、`residual_value_ratio`、`residual`、`accumulated_depreciation`、`equipment_value_after_depreciation`、`method`、`version`。

`generate_data_room` 的 `valuation_card`（`valuation.py:534`）新增 `depreciation_version`、`depreciation_applied`、`equipment_depreciation_basis`。

### C-5 缺席處置：fail closed

basis 為 `original_cost` 而 C-2 的必填欄位有任一缺席時：

1. `value_store` **不得**產出估值卡，拋出具名錯誤，訊息點名缺哪幾個欄位；
2. case 停在 `REVIEW_REQUIRED`，不是 `APPROVED`；
3. 資料室 `assets` 檢查項為 `missing`，`is_complete` 因此為偽。

不得走 `or 0.0`、不得走「當作沒折舊」。「當作沒折舊」與 `appraised_fair_value` 在數字上相同、在意義上相反，混起來就回到今天的狀態——只是多了幾個欄位。

---

## 舊估值卡

估值卡是**發給買方的文件**。改變輸出等於改變已發出數字的可比性。

### L-1 不重算

cutover 之前產生的報表**永不重算**。同一個 `report_id` 的內容維持逐位元可比。

需要新政策下的數字時，產生一張**新報表**：新的 `report_id`、`valuation_version + 1`，兩張都留在 `report_history`（`modules/avm/infrastructure/repositories.py:61`）。舊卡不消失、不改寫、不被覆蓋。

### L-2 legacy 版本標記

反序列化 cutover 前的卡（收據、資料室匯出、`deal_outcome` 關聯）時，在邊界**明寫** `depreciation_version = AVM_DEPRECIATION_LEGACY_VERSION`、`depreciation_applied = False`。

這個賦值要發生在 rehydration 函式裡，不是在 dataclass 的預設值上。預設值會讓**新**卡也悄悄變成 legacy；顯式賦值只會發生在真的讀到舊資料的那條路上。

### L-3 資料室匯出必須說出來

`valuation_card` 是外流文件。legacy 卡匯出時要帶一行可讀的處置說明（例如「本估值採 2026-09-03 前之計算版本，資產折舊未納入」），否則買方會用新政策的假設去讀一張舊政策的卡。這比數字本身重要。

### L-4 校準必須分版本（現況會靜默混算）

`AVMService.calibrate_deal_outcomes`（`modules/avm/application/valuation.py:260-293`）把 repository 裡**所有**報表收進 `reports_map`，交給 `evaluate_calibration_coverage`，沒有任何版本切分。

cutover 之後，同一個 cohort 會同時含有「折舊沒進計算」的舊卡與「折舊進了計算」的新卡。coverage 與 MAE 會被算出來，而且看起來正常——這正是第 1 批風險段講的那種錯數字。

契約：校準必須**依 `depreciation_version` 分群**，或在偵測到混版時拒絕產出單一報表。不得預設把它們當同一個 cohort。

---

## Rollback

### R-1 v0 pin 必須逐位元還原

實作必須是**加法式**的：把生效版本釘回 `avm-depreciation-absent-v0` 時，`value_store` 對同一筆輸入必須產出與 cutover 前**完全相同**的 `asset_p50`、`fair_price`、`reserve_price`、`asking_price`。

這是可測的，也是本文最重要的一條回滾保證：能一鍵回到已知狀態，才敢往前推。

### R-2 回滾不動已發出的卡

pin 只改變**未來**的寫入。以 v1 發出的卡維持 v1，不因回滾而重算——理由與 L-1 相同。

### R-3 回滾本身是一次版本事件

pin 要記錄 decider、時間、理由、預計期限。一個沒有期限的回滾就是靜默地放棄這次修復。

### R-4 觸發條件

三類，形狀先定，門檻由財務 owner 在 cutover 前填入並記在本文的修訂段：

- **數值**：重估 cohort 中 asset lens 相對 v0 的變動幅度超過門檻、且無資料面解釋的比例超過門檻；
- **結構**：出現 `depreciation_applied = true` 但 evidence 區塊鍵位不全的卡（表示計算路徑與紀錄路徑脫節）；
- **校準**：分版本後，v1 cohort 的 P10–P90 coverage 較 v0 顯著劣化。

門檻未填之前不得 cutover。**「之後再定」等於沒有回滾條件。**

---

## 測試規格

檔案：`modules/avm/tests/test_avm_depreciation_contract.py`。

一條常綠的架構守衛，加八條 `xfail(strict=True)` 的契約規格。`strict=True` 是刻意的——實作落地後這些測試會 XPASS，`strict` 會把 XPASS 判成失敗，**強迫實作者回來把標記拿掉**。標記留著就是紅燈，契約不會被默默繞過。

| 測試 | 釘住的東西 | 狀態 |
|---|---|---|
| `test_avm_does_not_import_site_economics` | 判定本身：不 import 模擬器 | 綠（且應長期綠） |
| `test_two_inputs_differing_only_in_depreciation_produce_different_valuation` | 折舊**真的進了計算**，不只是被存下來 | xfail |
| `test_valuation_input_carries_the_depreciation_contract_fields` | C-2 七個輸入欄位 | xfail |
| `test_the_asset_lens_publishes_its_depreciation_evidence` | C-4 evidence 區塊 | xfail |
| `test_missing_depreciation_inputs_do_not_yield_a_complete_card` | C-5 fail closed | xfail |
| `test_an_appraised_basis_is_not_depreciated_twice` | C-1 基數歧義 | xfail |
| `test_a_legacy_card_keeps_its_legacy_version_and_is_not_recomputed` | L-1／L-2 | xfail |
| `test_a_v0_pin_reproduces_the_pre_cutover_numbers` | R-1 回滾 | xfail |
| `test_calibration_does_not_silently_mix_depreciation_versions` | L-4 | xfail |

驗收關鍵那一條（第二列）刻意寫成兩筆**除折舊參數外完全相同**的輸入：同一個 `store_id`、同一份毛利、同一組可比倍數、同一個 `prediction_origin_time`，只有 `asset_in_service_date` 差 60 個月。今天兩者輸出相同——這就是缺陷的形狀。

---

## 不在本契約範圍

- **`quality_score` 那半**。同一批、不同驗收、不同回滾。
- **抽出 `site_economics` 共用函式**。上面四個證據說明不該做。
- **折舊參數的業務數值**。84 個月與 0.10–0.15 是門市模擬 catalog 的假設；AVM 用哪一組由財務決定，本文只要求寫出來源與差異理由。
- **既有卡的重算**。L-1 明確排除。
- **持久層 migration**。目前 `InMemoryAVMRepository` 是唯一的估值報表儲存（`modules/avm/infrastructure/repositories.py`），沒有 SQL 資料表要遷移。出現 durable store 時，L-1／L-2 的規則原樣適用於該層。

---

## 後續

1. 實作 task 依 C-1..C-5 修改 `modules/avm/domain/valuation.py` 與 `apps/api/app/routes/avm.py:31-45` 的 Pydantic payload，並同步 `packages/openapi-client/openapi.json`。
2. 財務 owner 填入 R-4 的三個門檻，之後才排 cutover。
3. 取得 `ODP-FR-AVM-001` 原始條文的版本／位置／雜湊，補進本文開頭的追溯段。
