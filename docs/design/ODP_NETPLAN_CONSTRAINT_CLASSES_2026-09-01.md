# NetPlan 硬限制：哪幾類進求解模型，哪幾類沒有，以及為什麼要講出來

- 日期：2026-09-01
- 需求：`ODP-FR-NET-002`「系統必須考量資本、租約、施工、設備、人力、覆蓋、稀釋與時序硬限制」
- 起因：[FR 查證報告](../evidence/ODP_FR_VERIFICATION_112_AND_ROOT_CAUSES_2026-09-01.md)，八類只有資本進了求解模型
- 實作基準：PR #1133 與後續審查修正，已合併至 `origin/dev`

---

## 問題不是「少了七類」，是「少了七類而它宣稱可行」

改動前 `solver/netplan/model.py` 的 `NetPlanConstraints` 只有 `max_budget` 是八類之一，其餘欄位（`min_expected_gross_margin`、`min_capacity_delta`、`max_average_risk`、action counts）都不對應 FR 的任何一類。

它不會失敗。它回傳一份計畫、`solver_status` 報最佳、`binding_constraints` 列得整整齊齊——一個讀起來像「這份計畫可以執行」的答案，而它其實只驗證了「這份計畫付得起」。

一季開四家、施工隊只蓋得動兩家的計畫，會以一個滿足的最佳化器的語氣回報出來。**缺功能是已知的未知；這個是錯答案穿著對答案的衣服。**

所以這次的核心改動不是限制的數量，是讓求解器**說出它驗證了哪幾類**。

---

## 四種形狀，四種處理

八類不是同一種東西。硬要用同一種機制表達，只會得到假的限制。

### 形狀一：共用資源池（施工、設備、人力）—— 已建模

跟資本完全同形：每個 option 消耗一些，計畫不能消耗超過存量。

```python
sum(x[e][j] * option.construction_days) <= max_construction_days
```

`ActionOption` 增加 `construction_days`、`equipment_units`、`labour_headcount`；
`NetPlanConstraints` 增加對應的 `max_*`。

### 形狀二：總量下限（覆蓋）—— 已建模

跟 `min_capacity_delta` 同形，是計畫整體的下限：

```python
sum(x[e][j] * option.coverage_delta) >= min_coverage_delta
```

意義是「計畫不得把網絡覆蓋削到這條線以下」。EXIT 在帳面上便宜又賺，覆蓋下限是攔住它的東西。

### 形狀三：站點之間的交互作用（稀釋）—— 部分建模

**這一類的真實形式是配對的**：在重疊商圈開兩家會互相稀釋。現行 MIP／CP-SAT formulation 都沒有配對變數或交互係數，所以目前不表達完整效應。它不是數學上不可做：MIP 可用 O(n²) 輔助變數線性化，CP-SAT 也能建模；本次選擇近似，是因為輸入係數品質與複雜度不值得，而不是「線性模型天生表達不了」。

所以只取它能誠實承載的那一部分——**每個商圈的開店數上限**：

```python
for zone in zones:
    sum(x[e][j] for OPEN options in that zone) <= max_open_per_dilution_zone
```

「不要在同一個商圈開三家」是稀釋效應裡數得出來的那半。`ConstraintClass.DILUTION` 記錄的是**這個近似**被套用了，不是完整效應。

### 形狀四：需要模型本身沒有的維度（租約、時序）—— 不建模，且明講

- **時序**需要時間維度：每個 option 屬於哪一期、每期的資源上限、先後次序。`ActionOption` 現在帶 `period_key`，但**只被攜帶與回報，從不被限制**。
- **租約**是 per-option 的可行性問題（這個檔期能不能簽下來、有沒有解約金），不是共用資源。它需要一個這個模型沒有的可行性檢查。

在目前 formulation 中，這兩類**不會出現在 `modelled_classes()` 裡**；若未來加入資料與限制，必須連同分類規則、transport、UI 與核准政策一起改。

---

## 結構性的部分：宣告驗證了什麼

```python
result.modelled_constraint_classes    # 這次求解真的綁住的
result.unmodelled_constraint_classes  # 這次求解沒說到的
```

`modelled_classes()` 的規則：資本永遠在（`max_budget` 是必填）；其餘只在對應的上限被提供時才出現；租約與時序永不出現。

沒有這一對，「只用資本約束的計畫」和「用全部八類約束的計畫」回傳的東西**長得一模一樣**。加三類限制縮小了落差；**說出剩下的落差**才是讓剩餘落差不被讀成合規的東西。

這是同一條規則在限制上的應用：*缺席必須與量測可區分*。

---

## 尚未關閉：宣告停在 solver result，沒有到人的決策邊界

基準 `6b893fd3` 的 solver result 與持久化 solve record 已帶 `modelled_constraint_classes`／`unmodelled_constraint_classes`，但 Operator 生產鏈路仍有缺口：

- `modules/opsboard/application/network_rebalance.py` 建 `plan_rows` 時只複製 `bindingConstraints`，主方案與替代方案都丟掉兩個 class 集合
- `apps/web/features/operator/network/RebalancePanel.tsx` 只把 binding constraints 傳給 `PlanGanttChart`，沒有顯示未建模類別
- `modules/netplan/application/planning.py::decide` 會擋 stale solve，但不會因 required class 未建模而擋核准或要求具名風險確認

因此目前只能說「solver/service 知道自己沒驗什麼」，**不能說操作者知道**。結案要同時完成 transport/type、UI 顯示、核准政策與 production-entry E2E：由 `DecisionPolicy` 決定哪些未建模類別必須阻擋，哪些可在顯示影響後由具權限的人明確 acknowledge；receipt 要保存 classes、policy version、actor、reason 與 solve hash。測試必須從 production solve 走到 Operator response／UI／approval，而不是只斷言 result dataclass。

---

## Fail-closed：未宣告的成本不等於零成本

`construction_days=None` 代表「呼叫端沒給這個數字」。
`construction_days=0.0` 代表「量過了，這個 option 不消耗施工」。

把前者讀成後者，就是讓一個沒被計入成本的 option 進入計畫，而計畫接著回報自己在上限之內。所以：

- 設了 `max_construction_days` 但有 option 的 `construction_days` 是 `None` → **拒絕**，錯誤訊息指名是哪些 option
- 設了 `max_open_per_dilution_zone` 時，任何一個 OPEN option 沒宣告 `dilution_zone_id` → **拒絕**（混合「有宣告／未宣告」也會讓部分開店逃過限制，卻把 DILUTION 誤報為已建模）

共用 validation 同時被函式庫 MIP 與 production-required CP-SAT 路徑呼叫；`solve_network_plan` 本身是函式庫入口，不應再稱作唯一生產路徑。生產路徑由 `NetPlanService.solve` 在 `production_required` 時路由到 `NetPlanProductionExecutor`。

---

## 替代方案也要過同一關

`_is_feasible` 也套用了新的上限。替代方案是拿給人看、讓人可以改採的計畫；**一份超出施工產能的替代方案不是替代方案**。

---

## 驗證

`tests/integration/test_netplan_hard_constraints.py` 收集 15 條，`tests/integration/test_netplan_production_constraints.py` 收集 9 條。前者證明函式庫限制形狀，後者才覆蓋 production-required 入口：

- 施工產能不足時第二家開不了；**池子放大後兩家都開**（反事實，證明是上限在起作用而不是別的原因）
- 設備與人力同形
- 覆蓋下限攔住帳面漂亮的 EXIT
- 同商圈兩家開店被擋、不同商圈兩家都放行
- 未宣告成本被拒絕、宣告為 0.0 被接受
- 拒絕穿過行程隔離
- 只給資本時，其餘七類全部回報為未建模
- 給滿所有可給的上限時，未建模集合**恰好是** `{LEASE, SEQUENCING}`
- 宣告能通過序列化
- 替代方案不得違反資源上限

在文件基準上可用 `.venv/bin/python -m pytest --collect-only -q tests/integration/test_netplan_hard_constraints.py tests/integration/test_netplan_production_constraints.py` 重現 15／9；執行同兩檔應為 24 passed。這只證明 solver/service，不覆蓋上一節列出的 Operator transport、UI 與 approval gap。

「既有測試不受影響」若要引用，必須附當次 SHA、完整 selector 與結果；不能用會隨後續提交漂移的裸數字當永久證據。

---

## 更正（2026-09-02）：有兩個求解器，不是一個

初版這份文件描述限制被加進 `solver/netplan/model.py` 與 `optimizer.py`，並且以為那就是全部。**不是。**

```
solver/netplan/optimizer.py                              pywraplp MIP
modules/netplan/application/production.py::_solve_ortools_cp_sat   CP-SAT
```

`NetPlanService` 在 `production_required` 為真時，把生產求解**路由到第二個**（`planning.py:200-206`）。
五個新上限——`max_construction_days`、`max_equipment_units`、`max_labour_headcount`、
`min_coverage_delta`、`max_open_per_dilution_zone`，對應 CONSTRUCTION、EQUIPMENT、LABOUR、
COVERAGE、DILUTION 五個非 CAPITAL 的 constraint class——全部只加在第一個。

我寫的每一條測試都通過，因為**每一條都在跑函式庫，沒有一條碰到 runtime 實際呼叫的路徑**。
Codex2 在審查時用直接重現抓到：兩個 40 天的 OPEN 選項對上 `max_construction_days=50`，
兩個都被選中，而且 `modelled_constraint_classes` 與 `unmodelled_constraint_classes` **都是空的**——
一份生產計畫回來時，對自己被驗證過什麼完全沒有陳述。

這正是這批工作在編目的那個形狀（「保證加在 runtime 不會走的路徑上」），出現在編目它的那個改動裡面。

### 修正

CP-SAT 路徑現在套用同樣的資源上限、覆蓋下限與稀釋上限，兩個回傳點都宣告 constraint class。
`tests/integration/test_netplan_production_constraints.py` 跑的是**生產進入點**而不是函式庫，
包含 Codex2 的重現案例，所以兩個求解器不會再無聲地分岔。

### 連帶修正的第二個 fail-open

稀釋上限原本只在「**沒有任何** OPEN 選項宣告 `dilution_zone_id`」時拒絕。
實際會發生的是混合情況：有些宣告、有些沒有。那時上限照樣生效——只作用在有宣告的那些——
所以求解結果報告 DILUTION 已建模，而沒宣告的開店不受任何約束。
**那比完全不約束更糟，因為結果宣稱約束套用了。**

現在設了上限時每個 OPEN 都必須宣告，由兩個求解器共用的同一個 helper 強制，
`_is_feasible` 也拒絕帶著未宣告開店的候選方案。

---

## 已決（2026-09-02）

### 時序：不建模，且不排程

不是成本問題。per-period 限制只有在**存在 per-period 產能資料**時才有意義，而目前施工與人力產能是以單一總量餵進求解器的。用一個沒人在量的數字餵一條限制，得到的是裝飾性限制——**正是這批工作在移除的形狀**。

它本來要防的風險（計畫回報可行但排不出來）在 solver result 中以 `ConstraintClass.SEQUENCING` 列入 `unmodelled_constraint_classes`；但在 Operator transport/UI/approval 補完前，這個資訊尚未可靠到達讀的人，所以風險只在服務層被記錄，沒有在人類決策邊界閉環。

重啟條件：有一個真的需要排期的規劃週期，**而且** per-period 產能數字存在。兩個條件缺一不可。若原始 `ODP-FR-NET-002` 仍把時序列為 `MUST`，此產品決定還必須連到正式 requirement amendment 或具期限 waiver，不能只靠本文改寫需求。

### 稀釋：不做完整配對形式

線性化二次項要 O(n²) 個輔助變數，但那不是決定性理由。決定性的是：**配對稀釋係數本身是模型輸出，帶著相當大的不確定性**。拿一個不確定的係數矩陣去做精確最佳化，製造的是輸入不支持的精確度——會得到一份對著不夠好的數字看起來最佳的計畫。

更好的下一筆投資在**量測端**：`ODP-FR-HZ-004` 的吸收閉環現在會產出真實的稀釋數字，改善那些數字對答案的影響，大於改善對現有數字的最佳化。

現有的每商圈開店上限保留，`ConstraintClass.DILUTION` 已記錄它是近似而非完整效應。若原始 `MUST` 要求完整配對效果，仍需正式 amendment／風險接受並寫明重啟條件。

### 租約：仍待確認資料來源

需要 per-option 可行性檢查（這個檔期能不能簽下來、解約金多少）。在確認資料來源存不存在之前，這一項無從決定——與時序同樣的道理：先有量測，才有限制。
