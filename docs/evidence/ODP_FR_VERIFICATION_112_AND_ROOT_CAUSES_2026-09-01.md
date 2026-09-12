# 規格符合度查證：112 條 MUST 級 FR、15 條落差、5 個結構性成因

- 日期：2026-09-01
- 基準：`origin/dev` @ `cc4b96d7`
- 結果：**96 屬實 · 15 部分 · 1 判不了**
- 線上版：https://claude.ai/code/artifact/fc9abbbe-99f3-4552-918d-3122c39cd706
- 狀態：**本報告不含任何程式修改。** 查證期間曾誤將其中五項派為修改工作，已於未產生任何分支前全數撤除。

先前的覆蓋率矩陣以關鍵字命中判定「有實作」。本次對其中每一條讀實際程式碼，確認的不是名字存在，而是**方向正確**——欄位有沒有被寫入、被讀取，判斷有沒有真的影響它宣稱影響的那個數字。

---

## 這份報告刻意不提供 15 個修補方案

逐條修補會讓症狀消失而成因留下。以 `root_cause` 為例：接上一個生產者，這一條就綠了，但「宣告一個欄位、export 它、寫 docstring 說它已經接線、然後沒有人讀寫它」這件事，下一次仍然不會被任何東西擋住——它已經在八個不同模組發生過。

所以主體是五個成因，15 條 FR 落差列為附錄，是為了可追溯，不是為了逐條發工單。

---

## 成因一：宣告一個能力，沒有任何東西要求它被接上

**出現於 8 個模組。** 欄位存在、被 export、docstring 有時還宣稱自己已接線，但沒有生產者或沒有消費者。

| 實例 | 狀態 |
|---|---|
| `root_cause: str \| None = None` | 全樹只有欄位定義與 TS 型別兩處，**沒有任何程式寫入** |
| `has_blocked_features()` | docstring 寫「Called by the dataset-snapshot registration path」，實際呼叫者是**四條單元測試** |
| `baseline_metrics` | 全樹 20 處，一律「傳參 → 存進 dataclass → `to_dict`」，**無一處比較** |
| `data_quality_score: float = 1.0` | 我自己寫進 heatzone 吸收模組的，宣告後從未讀取。審查者於 `fb75a142` 移除 |
| `manual_override_flag` | 欄位存在，API 只讀不寫，沒有設定入口 |
| `realized_revenue_ratio`（heatzone v2） | 只用來貼標籤，不進 score 也不進 rank |
| `HeatZoneV3State.UNDER_REALIZED` | 宣告了但 `_state_for_v3` 永遠不會回傳它 |
| `evidence_level: str = "medium"` | 預設值，讓「沒說」與「評估為 medium」無法區分 |

### 為什麼它不會被發現

單元測試會確認函式**能運作**，但完全不會說它**有沒有被呼叫**。`has_blocked_features` 有四條測試全綠，這件事本身就是它從未被接線的偽裝——綠燈讓人相信這個能力是活的。

型別檢查同樣沉默：一個從未被讀取的 dataclass 欄位是完全合法的程式碼。Code review 也難：新增欄位的 diff 看起來就是「補上一個資料點」，要發現它沒有被讀，得去找它的所有使用處，而那正是 review 最不會做的事。

### 兩層後果，第二層比第一層危險

1. 讀 schema 的人以為系統具備這個能力。
2. 讀輸出的人**分不出「沒有量到」與「量到的結果是零」**。

第二層危險，是因為它讓缺席看起來像一個正常的觀測值——一個回 `0.0` 的熱區與一個真的沒有被吸收的熱區，排名完全一樣。

### 要動的是什麼

不是把這八個接上。是要有一個機制，讓「宣告了但沒有執行路徑」這件事會失敗。可能形式：對 domain 契約欄位做可達性檢查（每個欄位至少要有一個非測試的寫入點與讀取點）；或把「能力宣告」與「介面欄位」分離，讓前者必須指名它的入口。共同前提是：**目前沒有任何東西在看這件事**。

---

## 成因二：規格沒有逐條驗收標準，所以「做完」是實作者自行認定的

`ODP-SA-06` 的 Trigger 與 Acceptance 兩欄是 **71 次重複的同一段樣板**。因此不存在任何一句話說明「NET-002 在什麼情況下算滿足」或「SITE-001 的五個組成缺一個算不算滿足」。

### 它如何造成落差

| 需求 | 要求 | 實作 |
|---|---|---|
| `NET-002` | 八類硬限制 | 一類（資本） |
| `SITE-001` | 五個組成 | 三個 |
| `LH-003` | 五種發布模式 | 四種 |
| `INTV-006` | 四種回應 | 三種 |
| `SHARED-001` | 六個狀態 | 五個 |

五條都是**「實作了一部分然後停下來」**，而且沒有一條違反任何白紙黑字的東西——因為沒有白紙黑字。實作者無從得知第八類限制是必要的還是可選的，reviewer 也無從據以退回。這不是紀律問題，是規格缺了一層。

### 連帶影響

`OPS-001`「系統必須提供所有模組工作區」判不了。5 個工作區對 19 個 domain module，但工作區是按使用者工作切分（今日工作／門市營運／營收成長／展店與店網／治理稽核）而非按模組切分，那是比較好的設計。「所有模組」是哪種讀法，規格自己沒說。

---

## 成因三：共用概念沒有單一擁有者，詞彙會無聲分裂

每一次分裂在本地都是合理的，沒有東西偵測「同一個概念現在有兩個定義」。

### Evidence Level —— 四套

```
packages/schemas/canonical/index.ts:352       'L0'..'L5'（ADR-0004 裁定的權威）
modules/intervention/domain/lifecycle.py:134  L0–L5 StrEnum
modules/adlift/domain/incrementality.py:37    L0–L5 StrEnum（內容相同、各自定義一份）
packages/domain-types/src/frontend-contracts.ts:195,224
    "high" | "medium" | "low" | "insufficient"      ← 另一套詞彙
apps/api/app/routes/priceops.py:133                 evidence_level: str = "medium"
apps/api/app/routes/operator_modules/growth.py:135  evidenceLevel: str = "medium"

兩套詞彙之間全樹沒有任何轉換函式。
```

前端契約用的正是 `InterventionTimelineContract` 與 `AdLiftReportCardContract`——也就是那兩個 Python enum 已經是 L0–L5 的**同兩個 domain**。`ODP-EVIDENCE-LEVEL-ALIGNMENT-001` 對齊了 Python enum 與 canonical TS type，沒走到這三處。

### Job Status —— 兩套

```
shared/jobs/queue.py:21              小寫五態
    queued / running / succeeded / failed / cancelled
apps/api/app/routes/listings.py:204  大寫七態
    QUEUED / RUNNING / RETRYING / SUCCEEDED / FAILED / CANCELLED / DEAD_LETTER

兩者皆無規格要求的 PARTIAL。
```

### Recommendation —— 一套被借用到另一個 domain

```
modules/intervention/domain/lifecycle.py:159  Recommendation
    CONTINUE / SCALE / STOP / CHANGE_CHANNEL / INCONCLUSIVE
```

這是 AdLift 的詞彙（正好滿足 `ODP-FR-AD-005` 的 Continue/Stop/Scale/Change Channel），被當成 intervention 的通用建議集使用，於是 `INTV-006` 要求的 Adjust 沒有位置。

### 要動的是什麼

不是把這三個各自對齊一次。`ODP-EVIDENCE-LEVEL-ALIGNMENT-001` 已經對齊過一次 Evidence Level，**而它仍然漏了三處**，因為對齊是一次性的人工掃描，不是持續生效的約束。要處理的是「一個跨層概念由誰擁有、其他層如何從它衍生」，以及讓第二份定義出現時有東西會響。

---

## 成因四：閘存在、內容正確、但結構上不會被執行

### 實例一：資料品質閘被包在一個 API 從不提供的參數底下

```
modules/learninghub/application/release.py:243-273
    if feature_set_id:                    ← 整段 BLOCKED 檢查在這個條件下
        ... raise LearningHubError(f"feature {name} is BLOCKED ...")

apps/api/app/routes/learninghub.py:41-44  DatasetSnapshotPayload
    rows / dataset_snapshot_id / require_training_eligible
    → 沒有 feature_set_id，也沒有 label_set_id

POST /learninghub/dataset-snapshots 一律以 None 呼叫，閘從不執行。
```

由呼叫端決定自己要不要被檢查的閘，不是閘。

### 實例二：租戶隔離驗證器的路徑在重構時斷掉，而它從不執行所以沒人知道

```
tests/contract/test_assisted_listing_intake_schema.py:29
    VALIDATOR_SQL = REPO_ROOT / "scripts" / "validate_assisted_listing_intake_schema.sql"
    → 該路徑無檔案

實際位置：delivery_toolchain/governance/validate_assisted_listing_intake_schema.sql
    commit 549ce261「refactor: isolate standalone delivery tools」搬移了檔案，常數沒改。
    同檔 line 322 的註解已經寫成新路徑，程式碼沒有。

該測試標記 @live → requires_live_env
CI product job 執行 -m "not requires_live_env and not performance" → 直接排除
```

那支 SQL 檢查每張帶 tenant 的表是否有 FORCE RLS 與 fail-closed tenant policy、每個 tenant-scoped FK 是否有對應的複合外鍵。**自該次重構以來從未真正執行過。** 一旦修好路徑，它很可能立刻是紅的——那才是要處理的東西。

### 實例三：ABAC 的租戶隔離子句拿一個值跟自己比（removed-by-decision）

```
shared/auth/abac.py:96  tenant_isolation
    if resource_tenant is None: return None      ← 棄權

shared/auth/identity.py:85  _axis_allows（brand/region/store/area/heat_zone 共用）
    if value is None: return False               ← 拒絕
    → 只有 tenant 這一軸與它的五個兄弟相反

apps/api/oday_api/security/dependencies.py:551,576,862
    ResourceDescriptor(tenant_id=principal.tenant_id)
    → 拿 principal 的 tenant 與自己比較，該子句在 API 路徑上不可能 deny
```

**2026-09-02 裁定：removed-by-decision。** 這不是破口；真正的隔離在資料層且 fail-closed。Operator Live 的 stores / transactions read chain 以 `scope.tenant_id` 進入 `list_stores` / `list_transactions`，兩個 durable repository 都先經過 `_require_tenant_scope`；assisted-listing 的 tenant-bearing SQL path 由 `_execute(..., tenant_id=...)` 設定 `app.tenant_id`，並由 migration 004 的強制 RLS policy 保護。查證沒有發現上述 API/data paths 在未經 `_require_tenant_scope` 或 RLS-protected session 的情況下進入 tenant-scoped read；本次移除不改動這些 enforcement。

因此本 PR 移除 `tenant_isolation` 函式、default policy chain entry，以及只餵入生產路徑不會產生的 cross-tenant 資源的手工 deny test。原 finding 保留在本節作為歷史紀錄，但不再是 live finding。

### 實例四：效能監控沒有基線，於是不可能偵測退化

```
models/shared_ml/validation.py:28  MetricThreshold.evaluate(self, value: float)
    → 只吃觀測值，沒有基線參數

AUC 從 0.92 掉到 0.80、門檻 0.75 → 不會有任何告警
比現任 champion 更差的 challenger → 只要過絕對底線就通過驗證
```

### 歷史案例

`sign_images.sh` 在 cosign 缺席時一律印 PASSED。當時修那個假閘的 commit，又生出了新的假閘。這說明它不是單一疏忽。

### 要動的是什麼

每一道閘都需要一個「它確實會失敗」的證明——一個刻意違反規則的輸入，且該證明必須跑在**實際會執行的 CI 路徑**上，而不是被 marker 或 scope 排除掉的路徑上。其餘三個實例的根本問題不是邏輯錯，是**執行路徑**錯；ABAC 實例已依裁定移除。

---

## 成因五：「不知道」可以被表示成一個正常的值

沒有一條共通約定說「缺席必須與量測結果可區分」，於是每個作者各自發明或不發明。

### 已修

```
modules/listing/application/promotion.py（PR #1091）
    h3_val = listing.get("h3_index") or "HZ-01"
    fit_score = hz_results[0].score if hz_results else 75.0
    except Exception: fit_score = 75.0

modules/external_data/application/source_snapshots.py（PR #1105）
    except Exception: raw_gen = 1
    ← 這個 1 會被寫進 object_generation，也就是 provenance 錨點本身
```

### 仍在

```
apps/api/app/routes/priceops.py:133                 evidence_level: str = "medium"
apps/api/app/routes/operator_modules/growth.py:135  evidenceLevel: str = "medium"
```

`shared/auth/abac.py` 的 `resource_tenant is None → 棄權` 已於 2026-09-02
依 removed-by-decision 裁定移除；它不再列為仍在的 finding。

### 為什麼這是成因而不是八個獨立 bug

`ADR-0004 D3` 已經針對 Evidence Level 裁定過：`null` 代表未評級，不是階梯上的一級。**裁定之後，兩個 API payload 仍然帶著 `"medium"` 預設值。** 一次針對單一概念的裁決，擋不住下一個模組的下一個作者寫出同樣的東西，因為那份裁決存在於一份 ADR 裡，不存在於任何會執行的地方。

我自己也犯了同一個錯：在 heatzone 吸收模組寫了 `data_quality_score: float = 1.0`，宣告後從未讀取——「低品質的觀測會被以全權重計入，而且沒有聲音」。我整輪都在拔這個形狀，同時又生了一個。這不是紀律能解決的。

---

## 附錄 A：15 條落差逐條對照

列出以供追溯與討論。**不建議按此清單逐條發工單**——理由見前五節。

| 需求 | 要求 | 實際 | 成因 |
|---|---|---|---|
| `NET-002` | 八類硬限制：資本／租約／施工／設備／人力／覆蓋／稀釋／時序 | 只有資本（`max_budget`）進了求解模型 | 02 |
| `PRICE-001` | 彈性保存適用範圍與不確定性 | 不確定性有；適用範圍完全沒有 | 01 |
| `LH-005`/`007` | Data／Feature／Prediction／Performance 四種漂移 | 前二有；Prediction 無；Performance 是絕對門檻不是漂移 | 01·04 |
| `LH-004` | DQ Fail 可阻擋發布／訓練／評分 | 閘正確但 API 到不了；另有一份無人呼叫的重複實作 | 04·01 |
| `LH-003` | Backtest／Champion-Challenger／Shadow／Canary／Rollback | 後四有；Backtest 只在訓練側，不是發布模式 | 02 |
| `OPS-005` | 呈現版本／新鮮度／Confidence／PI／Evidence Level | 前四齊全；Evidence Level 有預設值且前端契約是另一套詞彙 | 03·05 |
| `OPS-002` | 任務／指派／留言／附件／核准／升級／通知 | 六項有；留言全樹不存在 | 02 |
| `SITE-001` | External Demand／Brand Transfer／Format Conversion／Ramp／Seasonality | 三項有；Brand Transfer 與 Format Conversion 皆不存在 | 02 |
| `AVM-001` | GM_TTM／GM_FWD／折舊／資產／租約／正常化 | 除折舊外皆有；折舊在 site_economics，AVM 不引用 | 02 |
| `FCT-004` | 成長階段／轉折機率／異常證據／根因候選 | 前三有；根因候選是一個沒有生產者的欄位 | 01 |
| `INTV-006` | Continue／Adjust／Stop／Rollback | 三項有；Adjust 無，建議集用的是 AdLift 詞彙 | 03 |
| `SHARED-001` | 全域 job 查詢，六種狀態含 PARTIAL | 查詢有；PARTIAL 無；兩套不相容的 enum | 03·02 |
| `INT-001` | 五種接入型態 | CDC 缺席，全樹唯一命中是一段 docstring 註解 | 02 |
| `INT-006` | 支援人工校正 | 只有 `manual_override_flag` 欄位，API 只讀不寫，無設定入口 | 01 |
| `PRICE-006` | Bandit 框架受 Gate 控制啟用 | bandit 與 gate 在 priceops 與 solver/pricing 皆為 0 | — |
| `HZ-006` | 相鄰熱區合併與拆分 | merge／split 在 modules/heatzone 為 0 | — |
| `FCT-005`/`006` | Decision Policy 產生四燈；追蹤提前天數與 Precision | 進行中（PR #1106）／已排隊 | — |
| `SHARED-008` | 多環境隔離，不共用 Production Secret | **判不了**，屬部署設定事實，不在 repo 內 | — |

`PRICE-006` 與 `HZ-006` 是單純未實作，不是結構性成因的產物。`HZ-004`（依新店實績更新需求吸收）在查證期間補上並已合併（PR #1110）；其資料組裝在 `ODP-HEATZONE-ABSORPTION-INPUTS-001`。

---

## 附錄 B：方法與這份查證本身的限制

- **子字串假陽性是主要風險。** 在 netplan 底下搜尋 `lease` 會得到約 15 個命中，*全部是 `release_id` 裡的子字串*；搜尋 `gate` 命中的是 `aggregate`。因此每一條「存在」的判定都回到實際的 dataclass 欄位或函式呼叫點，不採計命中數。`NET-002` 差點因此被判為屬實。
- **截斷輸出同樣危險。** 查 champion/challenger 時 `grep | head -12` 的結果全被 adlift 的 `CausalChallenger*` 佔滿，差點判定 LearningHub 沒有 champion/challenger——實際上是有的。判定「不存在」之前不能截斷輸出。
- **無法對照驗收條件。** 見成因二。遇到需求敘述可有多種讀法時記為「判不了」，不自行裁定。
- **已結案的誤判線索：** `sitescore/application/reporting.py:151` 的 `SITESCORE_MODEL_VERSION` 回退*不是缺陷*——生產環境 `require_production_model` 為真，走模型路徑，該行不可達。

---

## 附錄 C：做得比要求更好的三處

列在這裡是因為它們是這個 codebase 已經知道怎麼做對的證據，前面五個成因的解法可以從這裡長出來。

- **`SITE-002`** —— P10/P50/P90 取自模型自己的分位數（`inference.lower/point/upper`，`zip(strict=True)`），且 metadata 缺 `output_transform` 時*拒絕評分*而不是猜一個轉換。這正是成因五的反例。
- **`OPS-004`** —— `build_subsidy_matrix` 要求某要求項下每一張決策卡都 READY 才算 READY，且*沒有任何卡對應到該要求項時算 MISSING*，而不是把空集合視為通過。加上 `build_bundle_checksum` 與 `/export/verify/{export_id}`，匯出是可驗證的而不只是可下載。
- **`SHARED-006`** —— webhook 以 channel 名稱映射到 `OnCallNotificationAdapter` 實作（沒有叫 Webhook 的 class，初次掃描容易誤判為缺），端點缺失或非 HTTP 時 fail closed。

---

## 附錄 D：全部 112 條分佈

| 模組 | 條數 | 屬實 | 部分 | 判不了 | 部分項 |
|---|---:|---:|---:|---:|---|
| AD | 8 | 8 | — | — | — |
| AVM | 8 | 7 | 1 | — | AVM-001 |
| FCT | 8 | 5 | 3 | — | FCT-004·005·006 |
| HZ | 8 | 7 | 1 | — | HZ-006 |
| INT | 8 | 6 | 2 | — | INT-001·006 |
| INTV | 9 | 8 | 1 | — | INTV-006 |
| LH | 11 | 8 | 3 | — | LH-003·004·005 |
| LST | 8 | 8 | — | — | — |
| NET | 9 | 8 | 1 | — | NET-002 |
| OPS | 10 | 8 | 2 | — | OPS-002·005 |
| PRICE | 9 | 7 | 2 | — | PRICE-001·006 |
| SHARED | 8 | 6 | 1 | 1 | SHARED-001·008 |
| SITE | 8 | 7 | 1 | — | SITE-001 |

LH-005 與 LH-007 為同一項發現，計為一條。
