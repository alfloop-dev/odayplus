# 五個結構性成因：處理結果

- 日期：2026-09-01
- 狀態：PR #1133 的實作與後續五張相依 task 已合併（#1138–#1142）；**這是 merge 狀態，不是所有風險已關閉的宣告**。NetPlan 人類決策邊界與跨層 nullable 缺口見下文。
- 來源：[FR 查證報告](ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md) 的五個成因
- 相關：[閘的清查](ODP_GATE_SWEEP_2026-09-01.md)、[NetPlan 限制類別](../design/ODP_NETPLAN_CONSTRAINT_CLASSES_2026-09-01.md)

---

## 一句話

四道 CI 控制進入執行路徑、78 筆原先未被 CI selector 收到的資料庫測試被納入、一組會擋住錯誤展店計畫的 solver/service 限制——但這些仍是**局部控制**，不是每一批 remediation 的端到端保證。尤其 NetPlan constraint disclosure 尚未到 Operator UI／approval，measurement lint 也不掃 Pydantic、SQL、OpenAPI 或 TS。

---

## 逐項

以下改用文件基準 `6b893fd3` 的可達合併歷史：PR #1133 merge `c8f151af`，五個主要 component commit 為 `b6074827`、`9c78040c`、`75b5c771`、`1c54954c`、`e6be0faf`。舊稿的 `a96276aa`、`17c8bb5e`、`64f7fb11`、`a6b32615`、`b8a04e9c` 雖仍可由本機 object database 解析，卻不是 `6b893fd3` 的 ancestor，不能當穩定 merged-history permalink。

### 一、閘的清查 → `b6074827`

清查 13 道閘。失效有**三種形狀**，不是一種：

| 形狀 | 為什麼難察覺 |
|---|---|
| 不在執行路徑上 | 沒有紅燈也沒有綠燈——什麼都沒有 |
| 沒有失敗證明 | 綠燈只證明「沒違規」，不證明「會擋」 |
| **證明本身無效** | **綠燈，而且是負向測試的綠燈** |

第三種最值得記：`test_tenant_isolation_blocks_other_tenant` 是綠的，它手工建了一個 tenant 不同的 `ResourceDescriptor`，證明函式會拒絕。但生產路徑建的是 `ResourceDescriptor(tenant_id=principal.tenant_id)`——拿 principal 的 tenant 跟自己比。**負向測試通過，閘結構上不可能觸發。**

> 得到的規則比原本想的精確：不是「每道閘都要有負向測試」（它已經有了），而是**負向測試必須用生產環境建構輸入的方式建構輸入**。手工組裝的測試恰好繞過了讓閘失效的那段接線。

處理：`requires_live_env` 混了「真的需要 live 環境」與「只是需要一個 PostgreSQL」。在歷史清查 SHA 上，CI selector 收到 83 筆，其中 78 筆是本次重新分類後新啟用，另 5 筆本來就在該 selector；不能把 83 或所有 marker 數都寫成「新啟用」。`pgserver` 一直是專案依賴。修好斷掉的 `VALIDATOR_SQL` 路徑（`549ce261` 搬檔案沒改常數）、補 `uuid-ossp` stub、加 CI 步驟。租戶隔離驗證器**第一次真的執行，通過**——我先前預測它會紅，錯了。

不能造假的部分沒造假：PostGIS 無法 stub，那一條改標 `requires_postgis`，殘餘排除從「隱性 78 筆新啟用缺口」變成「顯性 1 條具名測試」。

### 二、NET-002 → `9c78040c`

八類硬限制只有資本進了求解模型。問題不是少七類，是**少七類而它宣稱可行**——回傳計畫、狀態報最佳、binding constraints 列得整齊。

八類不是同一種東西，硬用同一種機制表達只會得到假限制：

- **施工／設備／人力**跟資本同形（共用資源池）→ 建模
- **覆蓋**是總量下限 → 建模
- **稀釋**真實形式是配對交互，現行 MIP／CP-SAT formulation 沒有配對變數；雖可線性化或在 CP-SAT 建模，本次因係數品質與複雜度採「每商圈開店數上限」近似，並明記不是完整效應
- **租約／時序**需要模型沒有的維度 → **不建模，且結果明講**

結構性的部分是 `modelled_constraint_classes` / `unmodelled_constraint_classes`。加限制縮小落差；**說出剩下的落差**才是讓剩餘落差不被讀成合規的東西。

合併後複查發現 disclosure 目前只到 solver result／solve record：`modules/opsboard/application/network_rebalance.py` 投影 `plan_rows` 時丟掉兩欄，`RebalancePanel.tsx` 不顯示，approval 也沒有對 unmodelled required class 阻擋或要求 acknowledgement。因此 NET-002 的 solver 修正成立，但「人會知道」尚未閉環；這是待修缺口，不是已完成證據。

未宣告的成本被拒絕而非讀成零——`None` 是「沒給」，`0.0` 是「量過、不消耗」。

### 三、不得有預設值的 lint → `75b5c771`

先量規模再選規則。廣義版本（量測欄位不得有預設值）在這棵樹上噴 **311 個**，大多合法（`srid = 4326`、`limit = 100`、`horizon_days = 28`）。**那種噪音量的閘一週內就會被加全域豁免，然後什麼都不守**——正好是這整條工作在講的失效模式。

窄化到「有界分數預設為滿分」：**16 個命中，每一個都是真的**。

最嚴重的一個：`HeatZoneV3Input.confidence` 與 `coverage_ratio` 都預設 1.0，而 `check_support_and_abstention` 在低於門檻時棄權。**沒有覆蓋率資料的熱區預設為完美，那道棄權閘對缺資料的情況永遠不會觸發。**

16 個記在 `measurement_default_exemptions.json`，每筆要有 owner 與理由，缺一個就直接拒絕——沒有署名的豁免就是讓債務回到看不見的狀態。理由寫的是「下游實際會發生什麼」，不是「既有問題」。

這個結果只適用於 checker 的掃描域：指定 Python roots、dataclass、annotation 恰為 `float`、constant default 恰為 `1.0`。它不會抓 Pydantic default、`.get(..., 1.0)`、dbt `coalesce`、DB `DEFAULT 1.00`、OpenAPI 或 TS，所以「16 個全為真」不能推成「全樹只有 16 個」。後續 nullable remediation 必須另做跨層 lineage 與契約測試。

> 我自己在 heatzone 吸收模組寫過同一個缺陷（`data_quality_score: float = 1.0`，宣告後從未讀取，審查者於 `fb75a142` 移除）。我腦子裡裝著這個 pattern 還是做了——**這就是要機械檢查而不是 review checklist 的論據**。

### 四、列舉型需求的機器可讀清單 → `1c54954c`

十五條落差裡有五條是同一個故事：需求列了 N 項，實作做了 M 項。**沒有一條違反白紙黑字，因為沒有白紙黑字**——`ODP-SA-06` 的 Trigger／Acceptance 是 71 次重複的樣板。

沒有替 112 條寫驗收標準（那是 112 個工作單位，產出一份自己也會漂掉的文件）。只對**真的列舉了集合**的需求：成員寫下來，滿足的指名它住在哪個 symbol，缺的必須說缺什麼。

檢查三件事，不多：證據參照必須解析得到（實作被改名或刪除會在這裡失敗，而不是無聲退回缺口）；缺席必須有註記；`member_count` 守住清單本身（否則刪掉一個成員就能讓需求「通過」）。

`check_requirement_members` 自己明載它**不驗證 implementation 正確**，且狀態只有 `satisfied`／`absent`。symbol 存在不等於 production path 有效，`absent` note 也不能把原始 `MUST` 改成 decided-not-doing；後者需要正式 amendment 或具期限 waiver/risk acceptance。

種了 6 條需求、32 個成員、23 個有可解析證據、9 個缺口寫下來。

### 五、Evidence Level 與 Job Status 納入 codegen → `e6be0faf`

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

三個 governance 測試檔在文件基準收集數是 13（measurement defaults）、16（requirement members）、13（vocabularies）；這是**整檔測試數**，不是每一條都能稱為負向案例。schema validator 另有 3 個 RLS／policy／constraint 違規案例。負向案例證明 checker 能紅，production-entry 測試才證明真實接線會把錯誤送進 checker。

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

## 合併後仍需處理

1. **`tenant_isolation` 要不要真的成為一道閘。** 若要，`ResourceDescriptor` 的 tenant 必須來自被存取的資源而非 principal，且它的 `None` 處理應與五個兄弟軸一致（拒絕，而非棄權）。若不要就刪掉——一道結構上不可能觸發的子句留著，只會讓下一個讀 code 的人以為這一層有把關。
2. **NetPlan disclosure 到人。** 把 modelled/unmodelled classes 穿過 OpsBoard transport 與 TS types，在 UI 顯示，並由 policy 決定阻擋或具名 acknowledgement；補 production solve → Operator → approval E2E。
3. **時序與完整稀釋的正式 disposition。** 技術方向已決，但若原始 `MUST` 未改，必須補 requirement amendment 或含 owner／期限／reopen trigger 的 waiver。
4. **量測缺席的非 dataclass 層。** Pydantic、mapping fallback、dbt、PostgreSQL/SQLite、OpenAPI/TS、舊資料與 UI 尚不受現有 lint 完整保護。
5. **原始規格追溯。** repo 內若只有 `ODP-SA-06`／`ODP-FR-AVM-001` 的轉錄，需補來源版本／位置／hash；未補前不得把轉錄等同 canonical source。

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

## 可重現的驗證收據

數字必須綁 SHA 與 selector；後續新增測試會改變收集數，不應覆寫歷史事實。

| 基準 | 命令／selector | 可重現結果 | 限制 |
|---|---|---:|---|
| 歷史清查 object `a96276aa` | `pytest --collect-only -q tests/contract tests/ops tests/integration -m "requires_live_env and not requires_postgis"` | 83 selected | 其中 78 筆為新啟用、5 筆原已在 selector；不是 83 筆都新啟用 |
| 同上 | `pytest --collect-only -q tests -m requires_live_env` | 101 marked；另有 1 筆 `requires_postgis` | 歷史 object 非 merged-history permalink，只用來重現當時計數 |
| 文件基準 `6b893fd3` | 同一 CI selector | 87 selected | 後續合併新增 4 筆，不能回寫成初次 remediation 成果 |
| task head（文件修正） | `pytest --collect-only -q` 指定兩個 NetPlan 檔與三個 governance 檔 | 15 + 9 + 13 + 16 + 13 = 66 | 收集數，不等於 66 個負向案例 |
| task head（文件修正） | `pytest -q` 同五檔 | 66 passed | 覆蓋 solver/service 與 governance；不覆蓋 NetPlan Operator/UI/approval 缺口 |

五檔的完整命令：

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_netplan_hard_constraints.py \
  tests/integration/test_netplan_production_constraints.py \
  delivery_toolchain/governance/test_check_measurement_defaults.py \
  delivery_toolchain/governance/test_check_requirement_members.py \
  delivery_toolchain/governance/test_generate_vocabularies.py
```

合併歷史驗證可用 `git merge-base --is-ancestor <sha> 6b893fd3`：`c8f151af` 與上述五個 component SHA 都回傳 0。其餘較大的 suite、ruff、boundary 與 baseline-diff 若要作 release evidence，必須附 CI run URL／artifact、精確 SHA、selector、通過／失敗數及環境限制；本文件不再保留無法由現有內容重現的裸數字。
