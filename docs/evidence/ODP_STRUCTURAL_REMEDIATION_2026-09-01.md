# 五個結構性成因：處理結果

- 日期：2026-09-01
- 狀態：**已合併**（PR #1133，2026-09-02）。後續五張相依 task 亦已全數合併（#1138–#1142）。
- 來源：[FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md) 的五個成因
- 相關：[閘的清查](ODP_GATE_SWEEP_2026-09-01.md)、[NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md)

---

## 一句話

四道新的 CI 閘、七十九條原本從不執行的測試被開起來、一個真的會擋住錯誤展店計畫的求解器限制——**而每一道新閘都附帶一個「它會失敗」的證明**，因為修成因四的時候不證明這件事，就是在修正裡重犯它。

---

## 逐項

### 一、閘的清查 → `a96276aa`

清查 13 道閘。失效有**三種形狀**，不是一種：

| 形狀 | 為什麼難察覺 |
|---|---|
| 不在執行路徑上 | 沒有紅燈也沒有綠燈——什麼都沒有 |
| 沒有失敗證明 | 綠燈只證明「沒違規」，不證明「會擋」 |
| **證明本身無效** | **綠燈，而且是負向測試的綠燈** |

第三種最值得記：`test_tenant_isolation_blocks_other_tenant` 是綠的，它手工建了一個 tenant 不同的 `ResourceDescriptor`，證明函式會拒絕。但生產路徑建的是 `ResourceDescriptor(tenant_id=principal.tenant_id)`——拿 principal 的 tenant 跟自己比。**負向測試通過，閘結構上不可能觸發。**

> 得到的規則比原本想的精確：不是「每道閘都要有負向測試」（它已經有了），而是**負向測試必須用生產環境建構輸入的方式建構輸入**。手工組裝的測試恰好繞過了讓閘失效的那段接線。

處理：`requires_live_env` 混了「真的需要 live 環境」與「只是需要一個 PostgreSQL」。79 條屬於後者，而 `pgserver` 一直是專案依賴。修好斷掉的 `VALIDATOR_SQL` 路徑（`549ce261` 搬檔案沒改常數）、補 `uuid-ossp` stub、加 CI 步驟。租戶隔離驗證器**第一次真的執行，通過**——我先前預測它會紅，錯了。

不能造假的部分沒造假：PostGIS 無法 stub，那一條改標 `requires_postgis`，殘餘排除從「隱性 79 條」變成「顯性 1 條具名測試」。

### 二、NET-002 → `17c8bb5e`

八類硬限制只有資本進了求解模型。問題不是少七類，是**少七類而它宣稱可行**——回傳計畫、狀態報最佳、binding constraints 列得整齊。

八類不是同一種東西，硬用同一種機制表達只會得到假限制：

- **施工／設備／人力**跟資本同形（共用資源池）→ 建模
- **覆蓋**是總量下限 → 建模
- **稀釋**真實形式是配對交互，線性模型表達不了 → 只取「每商圈開店數上限」，並記錄這是近似
- **租約／時序**需要模型沒有的維度 → **不建模，且結果明講**

結構性的部分是 `modelled_constraint_classes` / `unmodelled_constraint_classes`。加限制縮小落差；**說出剩下的落差**才是讓剩餘落差不被讀成合規的東西。

未宣告的成本被拒絕而非讀成零——`None` 是「沒給」，`0.0` 是「量過、不消耗」。

### 三、不得有預設值的 lint → `64f7fb11`

先量規模再選規則。廣義版本（量測欄位不得有預設值）在這棵樹上噴 **311 個**，大多合法（`srid = 4326`、`limit = 100`、`horizon_days = 28`）。**那種噪音量的閘一週內就會被加全域豁免，然後什麼都不守**——正好是這整條工作在講的失效模式。

窄化到「有界分數預設為滿分」：**16 個命中，每一個都是真的**。

最嚴重的一個：`HeatZoneV3Input.confidence` 與 `coverage_ratio` 都預設 1.0，而 `check_support_and_abstention` 在低於門檻時棄權。**沒有覆蓋率資料的熱區預設為完美，那道棄權閘對缺資料的情況永遠不會觸發。**

16 個記在 `measurement_default_exemptions.json`，每筆要有 owner 與理由，缺一個就直接拒絕——沒有署名的豁免就是讓債務回到看不見的狀態。理由寫的是「下游實際會發生什麼」，不是「既有問題」。

> 我自己在 heatzone 吸收模組寫過同一個缺陷（`data_quality_score: float = 1.0`，宣告後從未讀取，審查者於 `fb75a142` 移除）。我腦子裡裝著這個 pattern 還是做了——**這就是要機械檢查而不是 review checklist 的論據**。

### 四、列舉型需求的機器可讀清單 → `a6b32615`

十五條落差裡有五條是同一個故事：需求列了 N 項，實作做了 M 項。**沒有一條違反白紙黑字，因為沒有白紙黑字**——`ODP-SA-06` 的 Trigger／Acceptance 是 71 次重複的樣板。

沒有替 112 條寫驗收標準（那是 112 個工作單位，產出一份自己也會漂掉的文件）。只對**真的列舉了集合**的需求：成員寫下來，滿足的指名它住在哪個 symbol，缺的必須說缺什麼。

檢查三件事，不多：證據參照必須解析得到（實作被改名或刪除會在這裡失敗，而不是無聲退回缺口）；缺席必須有註記；`member_count` 守住清單本身（否則刪掉一個成員就能讓需求「通過」）。

種了 6 條需求、32 個成員、23 個有可解析證據、9 個缺口寫下來。

### 五、Evidence Level 與 Job Status 納入 codegen → `b8a04e9c`

Evidence Level 有四套定義。關鍵事實是**這件事已經被修過一次**——`ODP-EVIDENCE-LEVEL-ALIGNMENT-001` 對齊了 Python enum 與 canonical TS type，**仍然漏了三處**，因為對齊是一次性人工掃描，不是持續生效的約束。再掃一次只會買到同樣長度的時間。

所以：單一來源、生成、加上**拒絕第二份定義**的檢查。

兩份完全相同的 Python enum 是**合併掉**而不是登記成債（字元級相同，import 它今天不改變任何行為，但移除了「其中一份被編輯就開始分裂」）。`JobStatus` 宣告時帶上 `PARTIAL`。兩個實際存在的 job enum 登記為 fork 並註明遷移路徑，沒有在呼叫端不知情的狀況下改寫它們。

---

## 新增的 CI 閘

`orchestrator` job（不受 tooling scope skip 影響）：

```
Refuse bounded scores that default to perfect
Check set-valued requirements against their member lists
Check governed vocabularies for drift and new forks
```

`product` job：

```
Test database contracts, migrations and schema gates
```

**每一道都有自己的負向測試**：13 條（measurement defaults）、13 條（requirement members）、11 條（vocabularies）、3 條（schema validator 的 RLS／policy／constraint 違規）。

---

## 這個 lint 在合併時抓到了一個新的

把 base 推進到 `origin/dev`（帶進 `#1117`–`#1120`）之後，`check_measurement_defaults` 立刻拒絕了一個它從沒見過的東西：

```
modules/forecastops/domain/forecasting.py:303
ForecastOutput.data_quality_score = 1.0
```

來自 `ODP-FORECAST-ALERT-POLICY-001`（`fa01d4ba`）——**我在建這個檢查的同時，另一條分支正在寫這個缺陷**。沒有人不小心，這個形狀在 diff 裡就是看不出來。

它比已記錄的 16 個更糟，因為**它有被讀**：`production_model.py:148` 取所有觀測 `data_quality_score` 的 `min()`，而 `forecasting.py:201` 用 `_first_present(..., default=1.0)` 解析該欄位。所以沒有品質欄位的觀測會被算成滿分，再折進那個最小值——聚合值宣稱是「最差觀測的品質」，但完全沒有品質資料的觀測被當成最好的。

登記而非修：一份預測在輸入品質未知時該怎麼辦，是它自己的判斷題，塞進一個治理 commit 裡會把它埋掉。豁免條目記下日期、來源與為什麼需要真正的決定。

這是這五項工作裡最能說明問題的一件事——**機制在上線的第一天就攔到了一個人類流程剛剛放過去的東西**。

## 待討論

1. **`tenant_isolation` 要不要真的成為一道閘。** 若要，`ResourceDescriptor` 的 tenant 必須來自被存取的資源而非 principal，且它的 `None` 處理應與五個兄弟軸一致（拒絕，而非棄權）。若不要就刪掉——一道結構上不可能觸發的子句留著，只會讓下一個讀 code 的人以為這一層有把關。
2. **時序限制要不要進 NetPlan 模型。** 需要期別索引、per-period 資源上限、先後次序。是模型擴充不是補一條限制。
3. **稀釋的完整配對形式是否值得。** 需要二次項線性化或改用 CP-SAT。目前的每商圈上限是刻意的近似。
4. ~~17 筆豁免的 owner~~ —— **已指派並部分修正**。合併後 heatzone 四筆與 forecastops 兩筆已實際修掉，剩 11 筆待處理，見修正計畫。
5. **兩個 job status enum 的收斂路徑。** `RETRYING`／`DEAD_LETTER` 是佇列機制而非任務結果，可能該獨立命名成另一個概念。

第 4 項只需要指派；其餘四項需要判斷。

---

## 合併後的實際結果（2026-09-03）

四道閘在 `origin/dev` 上全綠，而且**數字往對的方向動了**——這是機制在量測債務下降，不是宣稱：

| | 交付時 | 五張相依 task 合併後 |
|---|---:|---:|
| 量測預設值豁免 | 17 | **11**（六筆真修，非重新命名） |
| 需求成員缺口 | 9 | **8** |
| 治理詞彙 / 未解分裂 | 2 / 4 | **3 / 2** |

逐項查證（不看核准章）：

- **heatzone**：`coverage_ratio` 與 `confidence` 由 `float = 1.0` 改為 `float | None = None`，
  消費端 `is None or < threshold` 走 fail-closed。棄權閘從「碰不到」變成可達。
- **`baseline_metrics`**：現在真的被讀來比較（`models/shared_ml/validation.py:462`、
  `modules/learninghub/application/monitor.py:109`），另有專門的效能漂移測試檔。
- **job status**：`shared/jobs/queue.py` 改用生成的 `JobStatus`，並新增 `JobDeliveryState`，
  結果與佇列機制拆成兩個詞彙。

### 機制對建它的人也生效了

`ODP-FR-SHARED-001` 的 `PARTIAL` 成員被記為 **`absent`**，附註「詞彙已帶它，但全樹沒有任何 job 會回報」。
我查證過：全樹確實沒有生產者。

**在一份為了防止不實宣稱而建的清單裡，沒有多出一個不實宣稱。**

### 審查抓到的三條，全部成立

Codex2 在 review 時退回三條，第一條打中要害——限制加在生產不會走的求解器路徑上
（詳見 [NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md) 的更正段落）。
另兩條是稀釋在混合 metadata 下 fail-open，以及 `member_count` 是選填讓需求清單能無聲縮水。

---

## 驗證

- 既有 109 條 netplan 測試不受影響（新欄位全有預設值）；283 條 netplan 消費端全綠
- 52 條 governance 測試（含四道新閘各自的負向證明）
- 181 條 adlift／intervention／governance／architecture 全綠
- 79 條原本從不執行的資料庫測試現在跑，全綠
- `ruff check` 在 modules／shared／delivery_toolchain／solver／apps／tests 全綠
- boundary check 1045 檔通過（base 推進後）
- 完整 product 套件與 `origin/dev` baseline **失敗差集為空**：兩邊同樣 12 條紅，全部是本機缺 SBOM／NOTICE 產物與 release-gate SHA 狀態的環境性失敗，CI 會跑 `make` 產生
