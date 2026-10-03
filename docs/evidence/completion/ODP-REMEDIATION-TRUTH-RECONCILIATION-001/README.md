---
evidence_id: ODP-REMEDIATION-TRUTH-RECONCILIATION-001
title: "需求 manifest 與規格來源追溯的過時狀態更正"
date: 2026-10-03
owner: Claude
reviewer: Codex
repository: alfloop-dev/odayplus
base_ref: 1b14b276447a7778f1dad1045ced1a5bd6abe01e
---

# 需求 manifest 與規格來源追溯的過時狀態更正

依 `support/handoffs/remaining-work-corrections-20261003/EXECUTION.md` 第 1 項執行。
本文件只主張**程式交付狀態與來源 bytes 的紀錄已更正**；不主張任何 live 驗收、
部署或人工批准。

## 1. 四個成員：從「absent／尚待實作」更正為「satisfied + BLOCKED_BY_EVIDENCE」

逐項讀取 `1b14b276` 上已合併的實作，對照原登錄。判準：symbol 存在且有接線與離線
測試 → `status: satisfied`；沒有任何 live／production 收據 → disposition 停在
`BLOCKED_BY_EVIDENCE`，並寫出剩餘範圍。沒有任何一項升為 `VERIFIED`。

| Requirement::Member | 舊紀錄 | 已合併實作（merge） | 新紀錄 | 仍欠（live／人工） |
|---|---|---|---|---|
| `ODP-FR-LH-005::PREDICTION_DRIFT` | absent／`IMPLEMENTATION_READY`（「沒有任何東西監控模型輸出分布」） | `LearningHubService.monitor_prediction_drift` → `EvidentlyDriftMonitor.run_prediction`，release worker 呼叫（PR #1154，`0cbc5330`） | satisfied／`BLOCKED_BY_EVIDENCE` | 對真實 PRODUCTION alias 模型的 production 執行收據；真實 Forecast 模型／歷史本身仍缺（`ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001`） |
| `ODP-FR-AVM-001::DEPRECIATION` | absent／`IMPLEMENTATION_READY`（「估值路徑沒有折舊」） | `calculate_depreciation`，於 `AVMProductionExecutor` 套用；八條 strict xfail 已移除（PR #1295，`898c192d`） | satisfied／`BLOCKED_BY_EVIDENCE` | Finance 的 `DepreciationCutoverEvidence`（核准人、時間、R-4 三門檻）；缺此證據時 production v1 路徑拒絕執行 |
| `ODP-FR-INT-001::EVENT` | absent／`OPEN`（「全樹無 Stream Consumer」） | `scoped_cdc_device_log_sensor` 經 `ScopedCdcAdapter` 消費 `device_log` change stream（H07 第 5 項，無 Broker；PR #1340，`dc0eb370`） | satisfied／`BLOCKED_BY_EVIDENCE` | `core.machine_status_events` 記錄生命週期欄位（`ODP-CDC-MACHINE-EVENT-LIFECYCLE-001`）；sensor 預設 `STOPPED` 從未 live 啟動；延遲未實測 |
| `ODP-FR-INT-001::CDC` | absent／`OPEN`（「Stage 34B 待 H07」） | `apps/data_platform/cdc.py` scoped adapter（orders／device_log），checkpoint、recovery、遮罩、軟刪除、GDPR（PR #1340，`dc0eb370`） | satisfied／`BLOCKED_BY_EVIDENCE` | replica set／oplog 讀回、`odp_cdc_reader` 角色讀回、控制表 DDL 於真實 PostgreSQL、sub-10s 延遲實測 |

H07（2026-09-13）與 H08 已定案，`evidence_needed` 不再要求使用者重答。各成員的舊
state、日期與原文保存在 disposition `history` 中，未改寫。

## 2. `ODP-SA-06`／`ODP-FR-AVM-001` 來源：bytes 已找到，批准仍缺

- 原始 bytes：repo 根目錄 `oday_plus_batch_02_sa_documents.zip` 的
  `ODP-SA-06_FUNCTIONAL_REQUIREMENTS_SPECIFICATION.md`，sha256
  `43dad7bf171a5e80511a01fd289bf2132c060e08c27799dcd8f91f86fb2073ec`，`version: 0.1.0`，
  自 initial commit `297a7618` 起就在 repo 中。2026-09-03 的「未找到」是查找遺漏。
- `ODP-FR-AVM-001` 是該檔第 104 行，row sha256
  `0a516d3b167aea0cd4858250b5b3630651597de2ad72d22ba11957f6faabcc3a`；與 manifest 轉錄
  只差措辭，六個成員相同。
- 該文件是 `status: draft-for-review`，repo 內沒有批准紀錄。兩筆 record 維持
  `BLOCKED_BY_EVIDENCE`（`blocked_on: authority_ratification`，`ratified: false`），
  owner `Product Lead`，next check `2026-10-17`。
- 2026-09-03 的 `observed_ref`、`recorded_at` 與原 `canonical_source` 區塊保存在
  record `history[0]`；evidence 文件原文保留，新增「2026-10-03 更正」一節。

## 3. 未變更、刻意保留的缺口

`PARTIAL`、`ADJUST`、`BRAND_TRANSFER`、`FORMAT_CONVERSION`、`LEASE` 維持原狀態；
`ROOT_CAUSE_CANDIDATE` 維持 absent／`IMPLEMENTATION_READY`（RESERVED）；NetPlan
`SEQUENCING`／`DILUTION` 的有效 waiver 未動。回歸測試把這些形狀釘住。

## 4. 變更檔案

| 檔案 | 性質 |
|---|---|
| `delivery_toolchain/governance/set_valued_requirements.json` | owned；四成員與兩筆 provenance record |
| `docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md` | owned；§4.6、§4.7 EVENT／CDC、§4.9 |
| `docs/evidence/ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md` | owned；新增 2026-10-03 更正節，原文保留 |
| `tests/governance/test_remediation_truth_reconciliation.py` | owned；新回歸測試 |
| `tests/governance/test_avm001_disposition.py` | **不在目前 canonical owned_paths**；必要連動：原測試把 DEPRECIATION 釘在 absent／`IMPLEMENTATION_READY`，與已合併實作矛盾 |
| `tests/integration/test_int001_cdc_disposition.py` | **不在目前 canonical owned_paths**；同上，把 EVENT／CDC 釘在 absent／`OPEN` |
| `delivery_toolchain/governance/test_check_requirement_members.py` | **不在目前 canonical owned_paths**；同上（且為宣告的 verification 檔） |

上列三個測試檔若不連動，manifest 更正後即紅燈；但 README 說明不等於範圍授權。
Codex 第一輪審查（PR #1400 `45ec3f00`）據此退回，這三個路徑的 owned_paths 登記
須由 coordinator 經正式 canonical 流程完成，owner 不自行以 `assign` metadata 擴權；
登記完成前本任務以 blocker 標示此缺口。
| `docs/audits/code-boundary-inventory.csv` | `check_code_boundaries.py --write-inventory` 因新增測試檔而強制產生的一列 |

## 5. 回歸測試涵蓋

`tests/governance/test_remediation_truth_reconciliation.py` 的期望值（merge SHA、
symbol、ZIP member hash）獨立寫在測試裡，不是拿 JSON 跟自己比：

- 四個 symbol 實際 import 並以 checker `resolve()` 解析；四個 merge 是 `HEAD` 的祖先。
- ZIP 不讀工作樹：解析 `ODP-SA-06` 與 `ODP-FR-AVM-001` 兩筆 record 的 `location`
  （`github://…@<40 位 commit>/<zip>!<member>[#L104]`），要求該 commit 等於 record 的
  `located_ref`、等於測試獨立釘住的 `1b14b276`、且是 `HEAD` 的祖先，再以
  `git cat-file blob <ref>:<zip>` 讀出 ZIP，核對 git blob id、container sha256、
  member 大小與 sha256、front matter、第 104 行 row 與 row sha256。
- 負向：改回舊 absent／舊 state、升為 `VERIFIED`（checker 本身會接受 satisfied +
  VERIFIED，所以由本測試拒絕）、關閉任一保留缺口、竄改 manifest hash、竄改 bytes、
  竄改 row hash、location 指向不存在的 commit、location 的 commit 與 `located_ref`
  不一致、AVM row 與 SA-06 位於不同 commit、location 指向別的 member 或別行、
  把 provenance 改回「未找到」、把找到的 bytes 當成已批准、刪掉
  2026-09-03 歷史觀察——每一種都必須被拒絕。

## 6. 驗證

宣告的 verification 命令由 `delivery_toolchain/git/task_verification.py run` 在
exact PR head 執行，收據（head SHA、命令、exit code、時間、選取範圍）存於
supervisor 的 `.orchestrator/evidence`，由 `task_finalize.sh` 檢核。

## 7. 完成語言（分開陳述）

- 程式／紀錄更正：本 PR 交付。
- 獨立審查／合併：第一輪 Codex 審查退回（scope、ref 驗證），本版修正 ref 驗證後重送。
- 真實資料輸入：不適用於本任務；上表「仍欠」各項均未取得。
- 部署：無。
- Live 驗證：無；四成員均為 `BLOCKED_BY_EVIDENCE`。
- 正式批准：無；`ODP-SA-06` 未經 authority 批准。
