# 修正計畫

- 日期：2026-09-03
- 基準：`origin/dev` @ `6b893fd3`
- 前置：[待裁決事項](ODP_OPEN_DECISIONS_2026-09-03.md)
- 背景：[FR 查證報告](../evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)、[閘的清查](../evidence/ODP_GATE_SWEEP_2026-09-01.md)、[處理結果](../evidence/ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)

這份文件追蹤二十筆查證發現，不把它們誤叫成二十個實作 task：第 19 項已裁決，第 20 項卡在執行環境證據，其餘項目仍可能以實作、正式豁免或不適用結案。工作分成七批：第 0 批是前置資料確認，第 1–6 批是執行／處置批次。排序依「錯數字離人的決定有多近」，但**執行順序不等於危害順序**——第 0 批必須先做，因為它是第 6 批部分項目的前提。

## 「修好」的定義

第 1–3 批資料品質問題的共同形狀是：**缺席被表示成一個正常的值**。第 4–6 批另含監控盲區、授權政策與未實作需求，不能套用同一個根因。

所以「修好」不是把欄位改成 `None`。改完 `None` 但下游一層再 `or 1.0` 回去，是把同一個缺陷搬了個位置。

判準是：**從資料進來到那個數字被人看到為止，缺席與量測結果始終可區分**。需要三件事同時做到——欄位、消費端、以及一條會失敗的測試。

這裡的「欄位」不是只指 Python dataclass。每一批要逐層勾完：來源 payload／snapshot → dbt view → API/Pydantic → parser／domain → PostgreSQL 與 SQLite → API response／生成 client → UI／核准。任何一層還在 `DEFAULT 1.00`、`coalesce(..., 1.0)`、`.get(..., 1.0)` 或把 nullable 型別收窄回 `number`，都不算修好。

### 已知會漏出 dataclass lint 的位置

| 層 | 已查到的例子 | 這批必做 |
|---|---|---|
| API / parser | `apps/api/app/routes/avm.py`、`modules/avm/domain/valuation.py`、`modules/sitescore/domain/scoring.py` | 移除 1.0 fallback；缺席要維持 `None` 或在邊界明確拒絕 |
| connector / mapper | `modules/external_data/connectors/external.py`、`modules/learninghub/domain/dataset_snapshot.py` | 不得在 `record.get`／`row.get` 補滿分 |
| dbt | `candidate_site_view.sql`、`geo_grid_view.sql` 的 `coalesce(..., 1.0)`；另有多個 view 硬編 `1.0` | 逐欄確認是量測、規則推導或常數契約；量測缺席不得補滿分 |
| persistence | `infra/db/migrations/000002_data_domain_canonical_entities.sql`、`000004_durable_product_domain.sql` 的 `NOT NULL DEFAULT 1.00` | forward migration 改 nullable／約束；PostgreSQL 與 SQLite 同步 |
| generated contracts | `packages/openapi-client/openapi.json`、`packages/schemas/canonical/index.ts` | 重生 OpenAPI/client/TS，nullable 與 required 語意一致 |
| presentation | Operator／估值卡／模型訓練入口 | 顯示「未評估」、棄權或拒絕；不能無聲格式化成 100% |

上述路徑是基準 `6b893fd3` 的已知清單，不是完整性證明。實作 PR 必須附欄位 lineage 與全樹搜尋結果；原始 `ODP-SA-06`／`ODP-FR-AVM-001` 若不在 repo，還要記錄來源版本、取得位置與內容雜湊，否則只能說「依目前轉錄內容」，不能宣稱對 canonical 規格完成追溯。

---

## 先看一個做完的：heatzone

已合併（`#1142`），可作「欄位—消費端—測試」的局部範例；跨 API／DB／生成契約的批次仍須走前述完整鏈路，不能只照抄 dataclass 改法。三個部分缺一不可。

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

## 跨批次 release blocker：NetPlan disclosure 尚未到人

這不是第 21 筆新需求，而是先前 NET-002「結果會明講未建模類別」的完成條件。solver result 雖已帶 `modelled_constraint_classes`／`unmodelled_constraint_classes`，OpsBoard projection 會把它們丟掉，Operator UI 看不到，approval 也沒有阻擋或具名 acknowledgement。任何批次宣稱 structural remediation 完成前，必須補 transport/type、UI、policy/receipt 與 production solve → Operator → approval E2E。詳見 [NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md)。

---

## 執行順序，以及為什麼不等於危害順序

| 批 | 內容 | 為什麼排這裡 | 相依 |
|---:|---|---|---|
| 0 | 資料源確認（`SITE-001` 兩項、`NET-002` 一項） | 不寫程式。它決定第 6 批裡這三項要進入實作還是標為不適用，先做才不會排到假工作 | 無 |
| 1 | AVM 品質語意 + 折舊 | 對外輸出危害最高；但跨 API、domain、OpenAPI 與舊估值卡，不能再以「4 個引用」估工 | 無 |
| 2 | SiteScore + ModelReadyRecord | 跨 parser、dbt、dataset snapshot 與訓練入口；SiteScore 已有 feasibility rules 可承接 | 無 |
| 3 | canonical model 六個 | **危害高但排第三**：`.confidence` 的 56 個 Python 非測試屬性引用只是搜尋起點，還要查 SQL、DB、TS、API 與 UI | 建議在 1、2 之後 |
| 4 | 三個盲區 | 會改 schema、告警或人工資料語意；可並行，但各自要有 migration／稽核／回滾 | 無 |
| 5 | 政策與詞彙 | 要先有答案才有工作 | 你的裁決 |
| 6 | 其餘缺席（含第 0 批確認後仍適用的三項） | 相對風險較低但仍會造成能力／規格落差；第 0 批確認資料存在後，才把那三項排入實作 | 第 0 批決定 `SITE-001`／`NET-002`；其餘無 |

第 3 批的位置是這份計畫裡唯一違反危害排序的決定。理由是實務的：56 個引用點的改動會與所有人衝突，而 1、2 做完之後遷移寫法已被三個模組驗證過，第 3 批就變成重複套用而不是探索。

---

## 第 0 批 —— 資料源確認（不寫程式，半天）

### 要回答的三個問題

- `SITE-001`：既有品牌客群移轉的資料存不存在？（Brand Transfer）
- `SITE-001`：店型轉換這個業務動作實際上有沒有在發生？（Format Conversion）
- `NET-002`：租約條件資料（檔期、解約金）存不存在？

### 為什麼先做

時序限制已經證明過：**一條沒有資料餵的限制是裝飾性限制**，而裝飾性限制正是這輪在拔的東西。

若答案是「資料不存在」，這三項應該走正式需求修訂或具期限的 waiver／risk acceptance，而不是只在 note 寫 *decided-not-doing*。原始 `MUST` 在被修訂前仍是未滿足，不能靠現有 manifest 的 `absent` 註記改寫規格。

### 產出

三個成員要有一張可追溯的 disposition：`OPEN` → `DECIDED`／`BLOCKED_BY_EVIDENCE` → `IMPLEMENTATION_READY` → `VERIFIED`。選擇不做時，記錄 decider、日期、原始 requirement 版本、適用範圍、理由、風險 owner、review/expiry date 與 reopen trigger，並連回正式 amendment／waiver；現有 `set_valued_requirements.json` 在 schema 擴充前仍維持 `absent`，只做索引，不冒充裁決本身。

---

## 第 1 批 —— AVM 估值

**最高危害。`quality_score` 與折舊是兩個不同語意的改動，可以同一個交付計畫，但必須有各自的驗收與回滾。**

### 兩件事，同一批

- `ValuationInput.quality_score: float = 1.0` → `float | None = None`，並同步修正 API 的 Pydantic default、mapping fallback、OpenAPI/client 與持久層；Python 的 4 個直接引用不是完整 blast radius
- 折舊接進估值路徑。目前在 `modules/site_economics/domain/simulator.py:325`（per-model useful life、殘值、月折舊），而 `modules/avm` 完全沒有 import `site_economics`

### 折舊那半的做法選擇

**不要直接 import。** `site_economics` 的模擬器算的是門市營運現金流，估值需要的是資產折舊——兩者的 useful life 假設可能本來就不同。

先確認是否同一個折舊概念；若是，抽成共用函式；若不是，估值需要自己的折舊模型，那是比「接線」大的工作，要單獨評估。

### 驗證

- 缺 `quality_score` 的輸入不得產出宣稱完整品質的估值卡（植入缺值，斷言拒絕或標記）
- API request 省略欄位、explicit `null`、舊資料列與新資料列都要各有契約測試；PostgreSQL／SQLite 語意一致
- 兩份除折舊外相同的輸入，估值必須不同（證明折舊真的進了計算，而不只是被存下來）
- `check_measurement_defaults` 綠燈且豁免條目已刪；另加能抓 Pydantic／mapping／SQL default 的測試，因為現有 lint 不會看它們

### 風險

估值卡是對外文件，改變輸出等於改變已發出數字的可比性。**需要決定既有估值卡怎麼辦**——重算、標記為舊版計算、還是只對新的生效。這個問題屬於業務。

---

## 第 2 批 —— SiteScore 與 ModelReadyRecord

**中危害，兩條實作流可並行；每條都要跨 parser、dbt、snapshot admission 與 consumer 驗收，不能再用 Python 直接引用數估工。**

### SiteScore（`average_confidence`、`data_quality_score`，8 與 10 個引用）

比其他批好接，因為 `ODP-FR-SITE-004` 的 feasibility rules 已經是「不給建議」的既有出口——資料不足可以走那條路，不需要發明新的表達方式。

### ModelReadyRecord（`data_quality_score`、`confidence`）

特殊之處是**會複利**：其他項一次錯一個數字，這個是讓一批髒資料進入訓練，之後那個模型產出的每個數字都帶著它而且無從追溯。

與 `ODP-FR-LH-004` 相關——BLOCKED feature 的閘剛修好可從 API 到達（`#1122`），這是同一道防線的另一半。**建議同一個人做**，兩者的拒絕點應該一致。

不能只改 `ModelReadyRecord` dataclass：`dataset_snapshot.py` 的 row mapper 仍以 1.0 補值，`candidate_site_view.sql`／`geo_grid_view.sql` 也會把來源缺席 coalesce 成 1.0；其他硬編 1.0 的 model-ready views 必須逐一證明是可觀測規則而不是假量測。來源、view、mapper 與 snapshot admission 要在同一張 lineage 表上驗收。

### 驗證

- SiteScore：缺品質分數的候選點不得產出與完整資料同等的分數；走 feasibility 出口時要有具名理由
- ModelReadyRecord：缺分數的記錄不得進入 dataset snapshot，拒絕訊息要指名是哪些記錄
- 兩者都要有「植入缺值 → 斷言拒絕」的測試，不能只測 happy path

---

## 第 3 批 —— canonical model 六個

**高危害、最大半徑、1 個 task 不可拆。**

`shared/domain/models.py` 的六個：`Poi` · `CompetitorStore` · `Listing` · `Prediction` · `HeatZoneScore` · `DataSnapshot`

前五個帶的是 `confidence`，`DataSnapshot` 帶的是 `quality_score`。六個各對應一筆豁免，就是這一批要刪的六筆——`measurement_default_exemptions.json` 裡以 `shared/domain/models.py::` 開頭的全部條目。

待裁決清單把它們寫成兩項：`Prediction.confidence` 是第 1 項（若直達操作者畫面，風險最高；目前仍需補 UI reachability trace），其餘五個是第 5 項。那是**裁決的排序，不是執行的切法**——第 5 項本身就要求 Listing 與 Prediction 得到同一個答案，所以這裡六個一起做。

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

這一批會改變**現有輸出**：某些今天有分數的東西會變成沒有。第 1、2、4 批也可能改變輸出或工作流，因此不能把語意風險只歸給本批。

**動手前先量一次，但不能直接查現在的 `confidence = 1.0` 來推估缺席率。** 舊 default 已把「真滿分」與「未提供」壓成同一值，事後無法可靠拆回來。要從 source snapshot／provider payload／field lineage 重建缺席率；無法重建的舊列標成 `legacy_unknown` 或保留舊 schema version。禁止把所有 1.0 一次改成 `NULL`，那會破壞真的量測到滿分的資料。遷移要明定新寫入 cutover、舊資料標記、是否重算，以及 rollback。

---

## 第 4 批 —— 三個盲區

**三項都會改變既有契約或工作流；可與 1–3 並行，但不能視為零語意變更。** Prediction Drift 新增告警，`root_cause` 的刪除／保留改 schema 契約，人工校正則新增可變資料與稽核責任。

### 4a · Prediction Drift（`LH-005`）—— 這批最該先做

Evidently 已經在用（`modules/learninghub/infrastructure/evidently_monitor.py`），但鎖定版本目前沒有 `PredictionDriftPreset`，不能把工作寫成「加一個 preset」。先定義被監控的 prediction 欄位、輸出型別、reference cohort、model/version 邊界與快照持久化；實作可用逐欄 `ValueDrift`，或將 `DataDriftPreset` 限定在 prediction 輸出欄位。門檻走 `DecisionPolicy` 而不是常數，告警要保存 reference/current snapshot id 與 model version。

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

三份實作各有答案：非管理員跨租戶路徑會拒絕，但 `PLATFORM_ADMIN` 的例外在兩處允許、一處拒絕。不能因此宣稱「不是破口」；在政策未定、跨租戶稽核未證明前，這是授權語意不一致。預設處置應 fail closed，或由安全／產品 owner 對暫時允許寫下具期限的風險接受。

回答之後統一，並抽成共用檢查，這樣第四份出現時不會又是第四個答案。

### 5b · `PARTIAL` 有沒有生產者

要回答的是「**哪些 job 真的會半成功**」——批次匯入、多來源抓取這類。

若答案是「目前沒有」，manifest 維持 `absent` 是誠實的技術現況；若原需求仍要求 `PARTIAL`，還要有正式 amendment／waiver 才能結案。有的話，那幾個 job 的狀態機補這個轉移。**不要為了讓清單好看而硬接一個生產者。**

---

## 第 6 批 —— 其餘缺席（含第 0 批確認後仍適用的項目）

| 項目 | 做法要點 |
|---|---|
| `SITE-001` Brand Transfer | 第 0 批確認資料源存在後才進入實作；若不存在，走正式 amendment／waiver 並留下完整 disposition |
| `SITE-001` Format Conversion | 第 0 批確認這個業務動作確實發生且有資料後才進入實作；若不存在，走正式 amendment／waiver 並留下完整 disposition |
| `NET-002` 租約條件限制（檔期、解約金） | 第 0 批確認資料源存在後才評估實作；per-period 時序與完整稀釋目前只有技術方向，原 `MUST` 的正式 amendment／waiver 另列必要產出 |
| `HZ-006` 熱區合併／拆分 | 唯一會隨時間惡化的一項。但**建議等 `HZ-004` 吸收數字累積幾個月**——那些實績正好是判斷「兩個熱區該不該合併」的依據，現在做等於憑結構猜 |
| `PRICE-006` Bandit + Gate | **兩者必須同一批出。** 沒有閘控制的 bandit 會自己在生產上做價格實驗。目前完全沒有所以安全——這一項「不做」比「做一半」好得多 |
| `INT-001` CDC 接入 | 先問有沒有來源系統真的需要。若批次與 API 夠用，標為不適用 |
| `INTV-006` Adjust | 先問實務：現在遇到要調整的介入，人是怎麼做的？若是「停掉再開一個」，可能只需要把兩者關聯記下來 |
| `LH-003` Backtest 當發布閘 | 低。Shadow／Canary／Rollback／Champion-Challenger 已覆蓋大部分發布風險 |
| `OPS-002` 留言 | 純缺功能。價值在把討論與決策綁在一起，不在提供溝通管道 |

---

## 修完之後，什麼防止它回來

目前有四類 CI 控制，但它們只覆蓋各自能機械判斷的部分；**不是每一批都已有端到端閘**。下表把現有能力與仍需補的控制分開。

| 批 | 現有控制能證明什麼 | 仍然證明不了／必補 |
|---|---|---|
| 1–3 | `check_measurement_defaults` 只掃 `modules/shared/solver/models/apps` 的 Python dataclass、annotation 恰為 `float`、default 恰為 `1.0`；會拒絕未登記項與過期豁免 | Pydantic、parser fallback、SQL/dbt、migration、OpenAPI/TS、舊資料與 UI；要加跨層契約測試或擴充 lint |
| 0、5b、6 | `check_requirement_members` 守住成員數、`satisfied/absent` 狀態與 symbol 可解析 | 它明確**不驗證實作正確**，也沒有 `decided-not-doing` 狀態；需要正式 requirement amendment／waiver schema 與 reviewer |
| 4a | `generate_vocabularies --check` 防第二份受治理詞彙與已知 fork 漂移 | 不證明 prediction drift 真的計算、分 cohort、持久化或告警；補正反向案例與 production-entry 測試 |
| 4b、4c、5a | 尚無對應的完整 CI 閘 | schema consumer 測試、人工校正 audit/authorization、跨租戶政策矩陣與 production wiring 測試 |
| DB 路徑 | product job 現在會執行 PostgreSQL 合約／migration/schema tests | 歷史「78 筆新啟用、83 筆被 selector 收集」不是所有批次的保障；SQLite 與欄位 lineage 仍要具名驗收 |

### 但要記得閘也會失效

這輪查證的成因四就是「閘存在、內容正確、但結構上不會被執行」。負向測試能提高可信度，不能取代生產進入點測試。基準上的治理測試收集數為 measurement 13、requirement 16、vocabulary 13；這些是整檔測試數，不應全部稱為負向測試。資料庫 schema validator 的 3 個違規案例另計。精確命令與 SHA 見[處理結果](../evidence/ODP_STRUCTURAL_REMEDIATION_2026-09-01.md#可重現的驗證收據)。

往後每加一道閘，同一個問題要再問一次：**什麼輸入會讓它失敗，那個輸入跑在實際會執行的路徑上嗎。**

我自己在 NetPlan 就犯過——限制加在生產不會走的那個求解器上，所有測試都綠，是 reviewer 用直接重現抓到的。詳見 [NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md) 的更正段落。

---

## 目前不進入實作的方向

- **`NET-002` 時序限制** —— 已有產品／技術決定不在目前模型實作；但若原需求仍是 `MUST`，仍需正式 amendment 或 waiver 才能關閉。重啟條件：有需要排期的規劃週期*且*per-period 產能資料存在
- **`NET-002` 稀釋的完整配對形式** —— 已有決定保留每區上限近似；若原需求要求完整配對效果，同樣需正式 amendment／風險接受。重啟條件是配對係數的品質與不確定性足以支撐最佳化
- **`root_cause` 的實作** —— 建議標記或刪除
- **merge queue 批次** —— 已裁決不做；結案紀錄仍須指向正式決策與適用期限
- **`SHARED-008` 與四條 NFR** —— `BLOCKED_BY_EVIDENCE`；指定環境、證據 owner、命令／查詢、門檻與下次檢查日期，不以空白結案

---

## 如果只能做一件事

**第 0 批。** 不寫任何程式，半天，而且它決定了第 6 批中 `SITE-001` 的兩個成員與 `NET-002` 租約條件限制到底是「待做」還是「不適用」。

在確認資料源之前把它們留在待辦清單上，是在維護一份自己知道不會做完的清單——那正是這輪查證發現的、規格層面的同一個問題。

程式面若只能做一件：**第 1 批 AVM**。這是本次 trace 中明確識別的對外估值風險；實際半徑跨 API、domain、生成契約、持久層與既有估值卡，不能再用 4 個 Python 引用點低估。
