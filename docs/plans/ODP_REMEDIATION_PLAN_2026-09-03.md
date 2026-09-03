# 修正計畫

- 日期：2026-09-03
- 基準：`origin/dev` @ `6b893fd3`
- 前置：[待裁決事項](ODP_OPEN_DECISIONS_2026-09-03.md)
- 背景：[FR 查證報告](../evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)、[閘的清查](../evidence/ODP_GATE_SWEEP_2026-09-01.md)、[處理結果](../evidence/ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)

十九項待修（第二十項判不了），分成六批。排序依「錯數字離人的決定有多近」，但**執行順序不等於危害順序**——有兩批必須先做，因為它們是其他批的前提。

## 「修好」的定義

這批問題有一個共同形狀：**缺席被表示成一個正常的值**。

所以「修好」不是把欄位改成 `None`。改完 `None` 但下游一層再 `or 1.0` 回去，是把同一個缺陷搬了個位置。

判準是：**從資料進來到那個數字被人看到為止，缺席與量測結果始終可區分**。需要三件事同時做到——欄位、消費端、以及一條會失敗的測試。

---

## 先看一個做完的：heatzone

已合併（`#1142`），可以直接照抄。三個部分缺一不可。

### 一、欄位：缺席變明確

```
modules/heatzone/v3/contract.py
    coverage_ratio: float | None = None      ← 原本 float = 1.0
    confidence:     float | None = None      ← 原本 float = 1.0
```

### 二、消費端：None 走 fail-closed，而且說出來

```
modules/heatzone/v3/scoring.py:54
    if feature.coverage_ratio is None or feature.coverage_ratio < 0.50:
modules/heatzone/v3/scoring.py:69
    # 6. Unacceptable confidence / quality floor (None means unmeasured -> fail closed)
    if feature.confidence is None or feature.confidence < 0.25:
```

注意 `is None or < threshold` 這個寫法：未量測與量測到過低**走同一條路（棄權）**，但兩者在紀錄上仍分得開。刻意的——決定相同，理由不同。

### 三、豁免條目刪除

`check_measurement_defaults` 對過期豁免會失敗。所以修正與刪除條目**必須同一個 commit**——這也是為什麼下面每一批都不能拆開送審。

---

## 執行順序，以及為什麼不等於危害順序

| 批 | 內容 | 為什麼排這裡 | 相依 |
|---:|---|---|---|
| 0 | 兩項資料源確認 | 不寫程式。它決定第 6 批裡三項到底存不存在，先做才不會排到假工作 | 無 |
| 1 | AVM 估值兩處 | 唯一錯誤會離開公司的地方；**而且半徑最小**（`quality_score` 全樹 4 個引用） | 無 |
| 2 | SiteScore 兩個 + `ModelReadyRecord` 兩個 | 中等半徑（8–10 個引用），SiteScore 已有 feasibility rules 可承接 | 無 |
| 3 | canonical model 六個 | **危害高但排第三**：`.confidence` 全樹 56 個引用點，工作量最大，先做會卡住其他人 | 建議在 1、2 之後 |
| 4 | 三個盲區 | 新增能力，不改既有語意，可與 1–3 並行 | 無 |
| 5 | 政策與詞彙 | 要先有答案才有工作 | 你的裁決 |
| 6 | 其餘缺席 | 不產生錯答案，可長期排 | 部分依第 0 批 |

第 3 批的位置是這份計畫裡唯一違反危害排序的決定。理由是實務的：56 個引用點的改動會與所有人衝突，而 1、2 做完之後遷移寫法已被三個模組驗證過，第 3 批就變成重複套用而不是探索。

---

## 第 0 批 —— 兩項資料源確認（不寫程式，半天）

### 要回答的三個問題

- `SITE-001`：既有品牌客群移轉的資料存不存在？（Brand Transfer）
- `SITE-001`：店型轉換這個業務動作實際上有沒有在發生？（Format Conversion）
- `NET-002`：租約條件資料（檔期、解約金）存不存在？

### 為什麼先做

時序限制已經證明過：**一條沒有資料餵的限制是裝飾性限制**，而裝飾性限制正是這輪在拔的東西。

若答案是「資料不存在」，這三項應該標成 *decided-not-doing* 並寫下理由，而不是留在待辦清單上假裝有天會做。

### 產出

三個成員在 `set_valued_requirements.json` 的 note 更新為已裁決（照時序與稀釋那兩筆的格式），或轉為實作 task。**沒有中間狀態。**

---

## 第 1 批 —— AVM 估值

**最高危害、最小半徑、1 個 task。**

### 兩件事，同一批

- `ValuationInput.quality_score: float = 1.0` → `float | None = None`（全樹 4 個引用點）
- 折舊接進估值路徑。目前在 `modules/site_economics/domain/simulator.py:325`（per-model useful life、殘值、月折舊），而 `modules/avm` 完全沒有 import `site_economics`

### 折舊那半的做法選擇

**不要直接 import。** `site_economics` 的模擬器算的是門市營運現金流，估值需要的是資產折舊——兩者的 useful life 假設可能本來就不同。

先確認是否同一個折舊概念；若是，抽成共用函式；若不是，估值需要自己的折舊模型，那是比「接線」大的工作，要單獨評估。

### 驗證

- 缺 `quality_score` 的輸入不得產出宣稱完整品質的估值卡（植入缺值，斷言拒絕或標記）
- 兩份除折舊外相同的輸入，估值必須不同（證明折舊真的進了計算，而不只是被存下來）
- `check_measurement_defaults` 綠燈且豁免條目已刪

### 風險

估值卡是對外文件，改變輸出等於改變已發出數字的可比性。**需要決定既有估值卡怎麼辦**——重算、標記為舊版計算、還是只對新的生效。這個問題屬於業務。

---

## 第 2 批 —— SiteScore 與 ModelReadyRecord

**中危害、中半徑、2 個 task 可並行。**

### SiteScore（`average_confidence`、`data_quality_score`，8 與 10 個引用）

比其他批好接，因為 `ODP-FR-SITE-004` 的 feasibility rules 已經是「不給建議」的既有出口——資料不足可以走那條路，不需要發明新的表達方式。

### ModelReadyRecord（`data_quality_score`、`confidence`）

特殊之處是**會複利**：其他項一次錯一個數字，這個是讓一批髒資料進入訓練，之後那個模型產出的每個數字都帶著它而且無從追溯。

與 `ODP-FR-LH-004` 相關——BLOCKED feature 的閘剛修好可從 API 到達（`#1122`），這是同一道防線的另一半。**建議同一個人做**，兩者的拒絕點應該一致。

### 驗證

- SiteScore：缺品質分數的候選點不得產出與完整資料同等的分數；走 feasibility 出口時要有具名理由
- ModelReadyRecord：缺分數的記錄不得進入 dataset snapshot，拒絕訊息要指名是哪些記錄
- 兩者都要有「植入缺值 → 斷言拒絕」的測試，不能只測 happy path

---

## 第 3 批 —— canonical model 六個

**高危害、最大半徑、1 個 task 不可拆。**

`shared/domain/models.py` 的六個：`Poi` · `CompetitorStore` · `Listing` · `Prediction` · `HeatZoneScore` · `DataSnapshot`

前五個帶的是 `confidence`，`DataSnapshot` 帶的是 `quality_score`。六個各對應一筆豁免，就是這一批要刪的六筆——`measurement_default_exemptions.json` 裡以 `shared/domain/models.py::` 開頭的全部條目。

待裁決清單把它們寫成兩項：`Prediction.confidence` 是第 1 項（它最可能不帶限定詞出現在操作者畫面上），其餘五個是第 5 項。那是**裁決的排序，不是執行的切法**——第 5 項本身就要求 Listing 與 Prediction 得到同一個答案，所以這裡六個一起做。

### 為什麼不能拆

Listing 的 confidence 缺席代表什麼，Prediction 就該一樣。一個一個決定就是**下一次詞彙分裂的起點**——Evidence Level 已經示範過分裂之後要花多少力氣收，而且第一次收還漏了三處。

### 為什麼工作量最大

```
grep -rn "\.confidence\b" --include=*.py modules apps shared models solver
    → 56 個非測試引用點
```

那 56 個不全屬於這批帶 `confidence` 的五個類別（`.confidence` 是共用屬性名），但**每一個都得逐一看過**才能確定哪些會收到 `None`。這是真正的成本，不是欄位本身。

### 做法

1. 先寫一條會失敗的測試：六個類別各建一個沒有量測值的實例（五個缺 `confidence`、`DataSnapshot` 缺 `quality_score`），斷言下游不會把它當滿分
2. 欄位改 `float | None = None`，讓型別檢查與測試把所有需要處理的呼叫點暴露出來
3. 逐點決定：棄權、標記、還是往上傳。**禁止 `or 1.0`**——那是把缺陷搬位置
4. 刪除這六筆豁免——`shared/domain/models.py::` 開頭的條目一筆不留

### 風險

唯一一批可能改變**現有輸出**的：某些今天有分數的東西會變成沒有。

**動手前先量一次**：生產資料裡有多少比例的 Listing／Prediction 實際上沒有 confidence？比例很高的話，一次全改會讓大量東西同時變成「未評估」——那是正確的但衝擊大，可能要分階段。**這個量測列為此批第一步。**

---

## 第 4 批 —— 三個盲區

**新增能力，不改既有語意，可與 1–3 並行。**

### 4a · Prediction Drift（`LH-005`）—— 這批最該先做

成本最低、收益最明確。Evidently 已經在用（`modules/learninghub/infrastructure/evidently_monitor.py`），加一個 preset 與門檻即可，門檻走 `DecisionPolicy` 而不是常數。

**驗證要點**：不是「跑得起來」，而是「輸出分布真的變了會告警、沒變不會」——兩個方向都要有測試。只測會告警的那一半，就是造一個永遠會響的警報。

### 4b · `root_cause`（`FCT-004`）—— 建議不要接生產者

根因推導是一整個功能，不是一個欄位。目前狀態最糟是因為*讀 schema 的人以為有這個能力*。

標成保留欄位（註明擁有者與預計時程）或直接刪掉，兩個都比現狀好。**不要在這一批把它實作出來**，那會變成一個沒人要的半成品。

### 4c · 人工校正寫入路徑（`INT-006`）

**必須連稽核一起做**：人工覆寫要留痕（誰、何時、原值、理由），否則是把一個沉默的錯誤換成一個沉默的修改。`OPS-004` 的 DecisionCard 已有現成形狀可用。

---

## 第 5 批 —— 政策與詞彙

工作量都不大，但都卡在沒有答案。

### 5a · `PLATFORM_ADMIN` 能不能跨租戶

三份實作各有答案。**不是破口**——三份都真的 `raise 403`——但「政策是什麼」目前沒有答案。

回答之後統一，並抽成共用檢查，這樣第四份出現時不會又是第四個答案。

### 5b · `PARTIAL` 有沒有生產者

要回答的是「**哪些 job 真的會半成功**」——批次匯入、多來源抓取這類。

若答案是「目前沒有」，維持 absent 並記下來就是正確的；有的話，那幾個 job 的狀態機補這個轉移。**不要為了讓清單好看而硬接一個生產者。**

---

## 第 6 批 —— 其餘缺席

| 項目 | 做法要點 |
|---|---|
| `HZ-006` 熱區合併／拆分 | 唯一會隨時間惡化的一項。但**建議等 `HZ-004` 吸收數字累積幾個月**——那些實績正好是判斷「兩個熱區該不該合併」的依據，現在做等於憑結構猜 |
| `PRICE-006` Bandit + Gate | **兩者必須同一批出。** 沒有閘控制的 bandit 會自己在生產上做價格實驗。目前完全沒有所以安全——這一項「不做」比「做一半」好得多 |
| `INT-001` CDC 接入 | 先問有沒有來源系統真的需要。若批次與 API 夠用，標為不適用 |
| `INTV-006` Adjust | 先問實務：現在遇到要調整的介入，人是怎麼做的？若是「停掉再開一個」，可能只需要把兩者關聯記下來 |
| `LH-003` Backtest 當發布閘 | 低。Shadow／Canary／Rollback／Champion-Challenger 已覆蓋大部分發布風險 |
| `OPS-002` 留言 | 純缺功能。價值在把討論與決策綁在一起，不在提供溝通管道 |

---

## 修完之後，什麼防止它回來

這是這份計畫與一般修 bug 清單最大的差別：每一批都有一道**已經在 CI 跑**的閘接住回歸。

| 批 | 接住它的閘 | 怎麼接住 |
|---|---|---|
| 1–3 | `check_measurement_defaults` | 新的有界分數預設滿分會被拒；豁免必須具名 owner 與理由；**過期豁免也會失敗**，所以修完不刪條目會紅 |
| 0、6 | `check_requirement_members` | 列舉型需求每個成員都要交代；`member_count` 必填，清單不能無聲縮水 |
| 5b | 同上 | `PARTIAL` 現在記為 `absent`；接了生產者才能改 satisfied，而 evidence 必須指得到真的 symbol |
| 4a | `generate_vocabularies --check` | 若 drift 類型成為新詞彙，第二份定義出現時會響 |
| 全部 | 資料庫閘（新 CI 步驟） | 79 條原本從不執行的契約與 migration 測試現在會跑 |

### 但要記得閘也會失效

這輪查證的成因四就是「閘存在、內容正確、但結構上不會被執行」。上面那些閘目前*都有自己的負向測試*（13／13／11／3 條），那是它們可信的理由。

往後每加一道閘，同一個問題要再問一次：**什麼輸入會讓它失敗，那個輸入跑在實際會執行的路徑上嗎。**

我自己在 NetPlan 就犯過——限制加在生產不會走的那個求解器上，所有測試都綠，是 reviewer 用直接重現抓到的。詳見 [NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md) 的更正段落。

---

## 明確不做的

- **`NET-002` 時序限制** —— 已裁決。per-period 產能資料不存在，沒有資料餵的限制是裝飾性的。重啟條件：有需要排期的規劃週期*且*資料存在，兩者缺一不可
- **`NET-002` 稀釋的完整配對形式** —— 已裁決。配對係數本身是模型輸出且不確定性大，拿它做精確最佳化是製造輸入不支持的精確度
- **`root_cause` 的實作** —— 建議標記或刪除
- **merge queue 批次** —— 已裁決不做
- **`SHARED-008` 與四條 NFR** —— 判不了，需要可觀測的執行環境。維持空白，不給猜測

---

## 如果只能做一件事

**第 0 批。** 不寫任何程式，半天，而且它決定了第 6 批裡三個項目到底是「待做」還是「不適用」。

在確認資料源之前把它們留在待辦清單上，是在維護一份自己知道不會做完的清單——那正是這輪查證發現的、規格層面的同一個問題。

程式面若只能做一件：**第 1 批 AVM**。半徑最小（4 個引用點），而它是唯一一個錯誤會離開公司的地方。
