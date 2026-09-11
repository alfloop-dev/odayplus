# ODP-INT001-CDC-DISPOSITION-001 歷史驗收核對與處置續辦記錄 (2026-09-11)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-INT001-CDC-DISPOSITION-001`
- **任務名稱**: 歷史驗收續辦：ODP-INT001-CDC-DISPOSITION-001
- **原始任務名稱**: 依 upstream 證據實作 CDC connector 或正式處置 INT-001
- **執行身分 (Owner)**: `Antigravity6`
- **指派審查者 (Reviewer)**: `Codex2`
- **復原交付分支**: `task/ODP-INT001-CDC-DISPOSITION-001-RECOVERY-20260911`
- **基準 SHA**: `4499a2993e37b62033926b07de8d8d2e8469a6c7`
- **歷史 PR 交付**: PR [#1166](https://github.com/alfloop-dev/odayplus/pull/1166)（PR head: `39289e4207b29d2c7adea071f42901f9f0b5ae20`，merge commit: `0f35515ed15aad36c496f30973b2e2ce9fa2b026`，合併時間: 2026-09-03T20:50:26Z）

本任務原始定位為**條件型處置任務 (Conditional Disposition Task)**：「只有 source evidence 證明 change log、ordering、delete 與 credential boundary 可用時才實作 governed CDC；否則維持 absent 並產出需人類授權的 amendment/waiver handback。不可建立沒有真 upstream 的空 connector。」

原始交付於 2026-09-03 經審查者 Antigravity7 批准，通過 7 項 required CI check-runs 並由 ajoe734 合併入 `dev`。在 2026-09-06 archive 事故後的歷史盤點中，本任務之 PR、CI、Approval、Local Merge 與 4 項驗收條款均被判定為 `consistent` 與 `high confidence`。

本次工作依據使用者指示，由 Supervisor Auto Worker 續辦可執行的歷史驗收，核對 PR #1166、決策 D20、已完成之 CDC 契約與刪除傳播任務、以及既有 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`，釐清處置交付與實際 CDC 能力邊界，完成正式驗收核對。

---

## 2. 原始條款 (A1–A4) 逐條驗收核對

| 項次 | 原始驗收條款 | 類別 | 判定結果 | 核對依據與證據鏈路 |
|---|---|---|---|---|
| **A1** | 實作時使用真 upstream contract 並保存 offset／ordering／delete／replay／idempotency semantics | 程式或文件交付 (D) | **已滿足 (met)** | 依 conditional 契約，因當時全系統唯一內部生產來源為 MongoDB 全量/增量快照、無 CDC 串流必要性，且下游無刪除傳播路徑，本任務嚴禁建立假 connector，交付了正式處置報告 `docs/evidence/ODP_INT001_CDC_DISPOSITION_2026-09-03.md` 及 `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md`（merge commit `0f35515ed15a` 完整保存）。 |
| **A2** | credential 與 tenant boundary fail closed 且有 production-entry 測試 | 可由測試證明 (T) | **已滿足 (met)** | PR #1166 交付了 `tests/integration/test_int001_cdc_disposition.py`，確保未經授權的 cluster 級憑證擴大與租戶邊界嚴格 fail-closed；該測試在 exact-head `39289e4207b2` 的 `product` check-run（以及當前 focused pytest）執行通過（conclusion=success）。 |
| **A3** | 無適用 upstream 時不新增 connector 且 AI 不自簽 waiver | 程式或文件交付 (D)<br>人類授權 (H) | **已滿足 (met)** | 交付物 `delivery_toolchain/governance/set_valued_requirements.json` 與處置報告中，`ODP-FR-INT-001::CDC` 維持 `status: "absent"`、`disposition.state: "OPEN"`，指派予 `Data Platform Lead` 並附 `formal_handback_ref`，未出現任何 AI 自簽之 decider 或 waiver。 |
| **A4** | requirement member 與 formal disposition ref 維持一致 | 可由測試證明 (T) | **已滿足 (met)** | `delivery_toolchain/governance/test_check_requirement_members.py` 由 `orchestrator` check-run 收集，`tests/integration/test_int001_cdc_disposition.py` 由 `product` check-run 收集；於 exact-head `39289e4207b2` 兩項 check-run 均為 `success`，且當前本地執行 `check_requirement_members.py` 退出碼為 0。 |

---

## 3. 治理演進與後續任務分工 (D20 & Follow-up Mapping)

本處置任務完成後，相關需求之架構與治理演進脈絡如下：

1. **決策 D20 (2026-09-08)**：
   在 `docs/plans/ODP_HUMAN_DECISIONS_EXECUTION_PLAN_2026-09-08.md` 中，使用者於 D20 選擇**「選項 A：實作／補齊 CDC 適用性與契約」**（非 waiver、非刪除需求），正式將 CDC 推進為工程接續項目。
2. **前置工程已完工**：
   - **WP-34A (`ODP-CDC-SOURCE-CONTRACT-PREP-001`)**：已於 PR #1258（merge commit `414b5c17927e`）完成，產出 23 來源矩陣及 H07 具體資料請求。
   - **刪除傳播 (`ODP-DATA-PLANE-DELETE-PROPAGATION-001`)**：已於 PR #1282 完成並合併入 dev。
   - **元資料對齊 (`ODP-DATA-CATALOG-METADATA-ALIGNMENT-001`)**：已於 PR #1286 完成並合併入 dev。
3. **實作任務持有 H07 輸入**：
   - **WP-34B (`ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001`)**：為目前 canonical 看板上 blocked 狀態之實作任務，正式持有 `H07` 具體入場條件（等待提供指定來源/collection、SLA、順序與刪除語意及最小讀取權限）。

---

## 4. 權限邊界與責任不變量宣告

1. **分清處置交付與實際能力**：
   `ODP-INT001-CDC-DISPOSITION-001` 的職責是「依 upstream 證據實作或建立正式 handback」。當時依據無上游需求的查證結果交付了 formal handback 與 fail-closed 治理保護，其任務條款已完整達成。
2. **嚴禁誤標 VERIFIED**：
   本處置任務之驗收核對通過，**絕不代表將原始需求 `ODP-FR-INT-001` 或 CDC adapter 標記為 `VERIFIED`**。原始需求仍處於 `OPEN` 待實作狀態，實際 CDC 串流與刪除傳播能力由 `ODP-CDC-SCOPED-ADAPTER-IMPLEMENTATION-001` 持續追蹤。
3. **不變更產品與權限**：
   本次補證工作僅寫入 `docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-INT001-CDC-DISPOSITION-001/`，不修改產品程式、不自簽 waiver、不開啟任何 live 憑證或資料庫連線。

---

## 5. 驗證方式 (Verification Receipts)

```bash
# 1. 檔案與目錄語法檢查
git diff --check

# 2. 集合型需求成員檢查器
python3 delivery_toolchain/governance/check_requirement_members.py

# 3. 治理與處置整合測試
uv run --python 3.12 pytest delivery_toolchain/governance/test_check_requirement_members.py tests/integration/test_int001_cdc_disposition.py -q

# 4. JSON 產物結構驗證
python3 -c '
import json, pathlib
p = pathlib.Path("docs/evidence/execution-control/ARCHIVE_RECOVERY_RECONCILIATION_20260911/ODP-INT001-CDC-DISPOSITION-001/acceptance-reconciliation.json")
assert p.is_file(), "JSON missing"
data = json.loads(p.read_text())
assert data["task_id"] == "ODP-INT001-CDC-DISPOSITION-001"
assert data["summary"]["criteria_met"] == 4
print("acceptance-reconciliation.json valid")
'
```
