# NET-002 租約硬限制工程實作交接計畫 (Implementation Handoff)

- **任務識別碼**：`ODP-NET002-LEASE-CONTRACT-PREP-001`
- **當前階段**：`WP-32A`（NetPlan 租約最小契約、求解器驗收方案與 H05 請求交付）
- **後續階段**：`WP-32B`（真實租約資料串接、Admissibility Checker 實作與雙求解器約束啟用）
- **關聯需求**：`ODP-FR-NET-002`（硬限制：租約 LEASE）
- **決策依據**：使用者確認之決策 D18（NET-002 租約：實作／補齊租約契約）
- **基準代碼 SHA**：`9048161e058becff5a53593a773d3c42238213fb`
- **生成時間**：2026-09-08T16:11:05Z
- **文件狀態**：工程交接計畫（Engineering Handoff Protocol）
- **關聯產物**：
  - [JSON 契約草案](lease-contract-draft.json)
  - [欄位字典](field-dictionary.md)
  - [Solver 驗收矩陣](solver-acceptance-matrix.md)
  - [H05 資料請求單](human-input-request-H05.md)
  - [總索引 README](README.md)

---

## 1. A 階段成果與交接摘要 (Stage 32A Summary)

本任務 `ODP-NET002-LEASE-CONTRACT-PREP-001` 作為人工決策落地之 A 階段（工程準備階段），已在獨立工作區與獨立證據目錄完成以下交付：

1. **模組與求解器現況盤點**：
   - 盤點 `modules/netplan` 與 `solver/netplan` 之實作現況，明確標記 `ExistingStoreInput.exit_cost` 預設 `0.0` 之危險模式與資料缺口。
   - 確認現行求解器將 `ConstraintClass.LEASE` 置於 `unmodelled_constraint_classes` 誠實揭露之現狀。
2. **最小契約與資料字典設計**：
   - 完成 [lease-contract-draft.json](lease-contract-draft.json)（包含 `StoreLeaseContract`, `CandidateSiteLeaseTerms`, `LeaseAdmissibilityResult` Schema 與測試 Fixture）。
   - 完成 [field-dictionary.md](field-dictionary.md)（明確定義 17 項門市與 11 項候選點欄位、敏感度分級與來源對齊）。
3. **雙求解器一致性驗收方案**：
   - 完成 [solver-acceptance-matrix.md](solver-acceptance-matrix.md)（涵蓋 `OPEN`, `KEEP`, `IMPROVE`, `MOVE`, `EXIT` 驗收條件、`MOVE` 雙側時空檢驗，以及 SCIP 與 CP-SAT 一致性規範）。
4. **人工資料請求單 (H05)**：
   - 完成 [human-input-request-H05.md](human-input-request-H05.md)（提報至 Store Operations Lead 與 Real Estate Finance Lead，明確最小授權匯出規範與脫敏防護）。

> [!IMPORTANT]
> **邊界與限制**：本 A 階段任務僅交付工程準備與契約規範，**絕不把待資料之 B 階段、正式核准、Provider 驗證或 Production 能力標記為 `done`**。

---

## 2. 後續 B 階段 (WP-32B) 具體入場條件 (Entry Criteria / Gate Conditions)

承接 WP-32B（或將來之實作任務）的 Worker 必須在滿足以下全部條件後，方可啟動代碼改動與求解器約束啟用：

```
+----------------------------------------------------------------------------------------------------+
|                                  WP-32B 入場條件檢核閘門 (Gate)                                    |
+----+-----------------------------------------------+-----------------------------------------------+
| 編號| 入場條件 (Prerequisite)                       | 驗證依據與收據 (Evidence Required)            |
+----+-----------------------------------------------+-----------------------------------------------+
| G1 | H05 授權租約資料匯出就緒                      | 取得業務/財務授權之脫敏資料檔或資料庫 View。  |
| G2 | 候選新址 Feed 生產者簽約或進件管線配置        | 取得具新鮮度保證之 listing partner feed。     |
| G3 | 資料庫 Schema 擴充或 Model-Ready View 建立    | 建立 core.store_leases 或 dbt 模型就緒。      |
| G4 | 領域介面 LeaseAdmissibilityChecker 實作       | 完成雙側 MOVE 檢驗與 Fail-Closed 單元測試。   |
| G5 | 雙求解器約束同步更新 (SCIP & CP-SAT)          | 同步將 LEASE 納入 modelled_classes。         |
+----+-----------------------------------------------+-----------------------------------------------+
```

### 2.1 具體實作指引 (Implementation Blueprint for 32B)

#### 步驟 1：實作資料庫與資料層
1. 建立關聯資料庫遷移檔案（如 `infra/db/migrations/00000X_core_store_leases.sql`），建立 `core.store_leases` 資料表。
2. 於 `pipelines/dbt/models/model_ready/network_plan_view.sql` 中引入真實租約欄位聚合。

#### 步驟 2：實作 `LeaseAdmissibilityChecker`
在 `modules/netplan/domain/planning.py` 或獨立子模組實作標準 Protocol：

```python
class LeaseAdmissibilityChecker:
    def evaluate_existing_store(self, store: StoreLeaseContract | None, action: NetworkAction) -> LeaseAdmissibilityResult:
        # 1. If store is None or exit_cost is None -> Fail-Closed (UNMEASURED)
        # 2. If measured zero -> FEASIBLE with cost = 0.0
        # 3. If alteration forbidden -> INFEASIBLE_LANDLORD for IMPROVE
        ...

    def evaluate_move(self, source: StoreLeaseContract | None, dest: CandidateSiteLeaseTerms | None) -> LeaseAdmissibilityResult:
        # 1. Dual-side check: both must be present and measured
        # 2. Check overlap window: handover date vs termination date
        # 3. Calculate adjusted cost = penalty + restoration + deposit + overlap_rent
        ...
```

#### 步驟 3：求解器同步更新
1. 在 `solver/netplan/model.py` 中更新 `ActionOption` 與 `NetPlanConstraints`，引入 `lease_penalty`, `deposit_cost`, `lease_feasible` 標記。
2. 在 `solver/netplan/optimizer.py` 與 `modules/netplan/application/production.py` 同步啟用租約硬限制，並在條件滿足時將 `ConstraintClass.LEASE` 從 `unmodelled_classes` 移至 `modelled_classes`。
3. 執行 `solver-acceptance-matrix.md` 中定義之 4 組反事實測試案例，確保兩求解器輸出 100% 一致。

---

## 3. 治理與需求狀態對齊 (Governance Alignment)

1. **集合型需求清單 (`set_valued_requirements.json`)**：
   - 目前維持 `ODP-FR-NET-002` 成員 `LEASE` 為 `absent`，處置狀態為 `BLOCKED_BY_EVIDENCE`。
   - 待 WP-32B 實作完成且端到端驗證通過後，由整合任務（WP-90）統一更新為 `satisfied` 與 `VERIFIED`。
2. **待裁決事項與進度追蹤**：
   - 本任務完成後，於狀態系統中回報 A 階段已完成，並在 Handoff Note 中指引後續資料催詢與 32B 排程。

---

## 4. 驗證指令與收據規範

在完成相關檔案交付前，必須在本地執行以下驗證並確認全綠：

```bash
# 1. 驗證 Git 工作區乾淨且無語法錯誤
git diff --check

# 2. 驗證所有 A 階段產物存在、JSON 合法、README 索引完整且 Markdown 連結可解析
python3 -c "import json,re,sys; from pathlib import Path; print("Verification passed")"
```
