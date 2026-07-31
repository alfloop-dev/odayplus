---
title: Development Plan Open Task Execution Control Pack
date: 2026-07-31
packet_id: ODP-PLAN-EXECUTION-CONTROL-PACK-001
program_id: ODP-PLAN-GAP-CLOSEOUT-2026-07-30
status: execution-contract-hardening
release_claim: no-go-until-final-gate-audit
machine_readable: docs/evidence/DEVELOPMENT_PLAN_OPEN_TASK_EXECUTION_PACK_2026-07-31.json
---

# Development Plan Open Task Execution Control Pack

## 1. 為什麼需要這份 control pack

原始完整規劃第 69–81 章已重算為 **84/84 個不重複 RTM rows**，
並聚合成 **26 個治理 execution tasks**。截至本文件建立時，7 個 task
已完成歸檔，19 個尚未完成。

現有盤點並沒有少建 task；執行反覆 reopen 的主因是 live task contract
大多只有一條概括 acceptance。Owner 容易只修 reviewer 最新舉出的例子，
卻沒有重新檢查同一 task 的完整資料真實性、SHA 綁定、負向測試、證據、
依賴、handoff 與 deployment 條件。

本文件不取代 84-row matrix 或 26-task ledger。它把 19 個未完成 task
補成可直接交給 Supervisor／auto worker 的一次性交付包。完整欄位與逐項
條件以同名 JSON 為 machine-readable authority。

## 2. 已完成與未完成範圍

- 完成並歸檔：7
- 未完成：19
- implementation：12（其中 4 項執行／重審中、1 項等 Human/Ops 決策）
- human gate：7
- release：`NO-GO`

已完成 task：

1. `ODP-PLAN-GAP-ARCHIVE-001`
2. `ODP-PLAN-GATE-REGISTRY-001`
3. `ODP-PLAN-CANONICAL-SHELL-LIVE-001`
4. `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001`
5. `ODP-PLAN-DEFERRED-OSS-ADR-001`
6. `ODP-PLAN-HEATZONE-OUTCOME-001`
7. `ODP-PLAN-LEDGER-NETPLAN-HUMAN-GATE-001`

## 3. Supervisor 必須執行的批次規則

### 3.1 派工前

1. 驗證 task 的所有 dependency 已透過 live 或 archive resolver 成立。
2. 驗證 owner 與 reviewer 不同、Human gate 不會被派給 AI 代簽。
3. 從目前 `origin/dev` 建立／更新 task branch；worktree 不得混入其他
   task 的 diff。
4. Auto worker prompt 必須包含：
   - 本 control pack；
   - 84-row gap matrix；
   - 26-task ledger；
   - 該 task 的 current notes 與所有 prior review findings。
5. 對 reopened task 明確要求「重跑整份 packet」，不能只修最新 finding。

### 3.2 Owner handoff 前

Owner 必須一次完成：

1. `batch_deliverables` 全部完成；
2. `must_reject` 全部有負向／mutation proof；
3. `evidence` 全部存在、可解析且綁 exact source/tree/release/artifact hash；
4. `verification` 全套實際執行；
5. 將每條 criterion 標成 `PASS` 或有證據的 `NOT_APPLICABLE`；
6. task branch 已 commit、push、合併目前 `origin/dev`，沒有 unrelated diff。

只要仍有 `UNKNOWN`、未執行命令、缺 artifact、模擬 production proof、
AI 代簽或只修單一 reviewer 範例，就不得 handoff。

### 3.3 Reviewer、PR、合併與 closeout

1. Reviewer 在乾淨 checkout 獨立 review exact pushed head。
2. Reviewer 至少重跑 focused suite、完整 touched-scope gate，以及一組
   未使用 owner fixture 的負向／mutation probe。
3. Source、config 或 test 在核准後有任何變動，approval 立即失效。
4. 完整 local batch handoff-ready 後才開／更新一個 task-scoped PR；
   不得每修一個 finding 就開 PR 或部署。
5. Exact PR head 的 required CI 全綠後才合併 `dev`。
6. 合併後由 owner 登錄 merge SHA、readback、artifact，再標記 `done`。

### 3.4 Deployment 邊界

- 一般 implementation task 只允許本機／CI／必要的 read-only provider
  verification，不得因 focused tests 通過就部署 production。
- 計畫中的 staging/live deployment 只由
  `ODP-PLAN-LIVE-STAGING-PROOF-001` 執行，而且必須等所有 declared
  dependencies 完成並確認 deployment candidate 等於 merged `dev`。
- `ODP-PLAN-FINAL-GATE-AUDIT-001` 與 Human/Ops release decision 通過前，
  不得宣告 GO。

## 4. 未完成 task 一次性交付索引

| Wave | Task | 類別 | 一次性交付重點 | Deployment |
|---|---|---|---|---|
| A | `ODP-PLAN-OSS-LICENSE-GATE-001` | implementation | license-aware SBOM、lock-bound prod/dev audit、policy readback、NOTICE、release attestation、負向收據驗證 | 禁止 |
| A | `ODP-PLAN-OBSERVABILITY-LIVE-001` | implementation | 全信號 exporter/dashboard/alert/runbook、per-signal watch coverage、真實 route delivery | 禁止 |
| B | `ODP-PLAN-ACCEPTANCE-REAL-EXEC-001` | implementation | 16 specs/107 tests 真實執行、exact test id、runner metadata、source/evidence SHA、全 runner exit | 禁止 |
| B | `ODP-PLAN-SITESCORE-OUTCOME-001` | implementation | 真實 M6/M12 outcome、coverage/calibration、Gate 2/model card、backfill、不得虛構 governance | 禁止 |
| B | `ODP-PLAN-SITESCORE-PREDICTION-SOURCE-001` | implementation | prediction/model-registry lineage、正確 join、真實 horizon、aligned population、禁止 y_pred=y_true | 禁止 |
| B | `ODP-PLAN-AVM-OUTCOME-001` | implementation | >=120 成交 outcome contract、coverage/calibration/value band、RBAC/audit、backfill | 禁止 |
| B | `ODP-PLAN-NETPLAN-ACCEPTANCE-001` | implementation | hard constraints、solver result 重算、immutable baseline/problem hash、權威 approval verifier、未核准維持 disabled | 禁止 |
| B | `ODP-PLAN-ENGINEERING-HARDENING-001` | implementation | OpenAPI/build/bundle/warnings/decomposition 與完整 dependency audit；13 dev highs 要具名決策 | 禁止 |
| C | `ODP-PLAN-FORECAST-RELEASE-EVIDENCE-001` | implementation | authoritative dataset/model/card/metrics/MLflow alias/shadow/canary/watch/rollback 同版綁定 | 禁止 |
| C | `ODP-PLAN-FORECAST-BUSINESS-001` | implementation | baseline、segment、四燈 precision/recall/lead-time、dashboard/model 同版 | 禁止 |
| C | `ODP-PLAN-PRICE-ADLIFT-PILOT-001` | implementation | 0 hard violation、pre-trend/causal/incremental GM、guardrail/kill/rollback、Finance handoff | 禁止 |
| D | `ODP-PLAN-LIVE-STAGING-PROOF-001` | implementation | exact merged release 的 Cloud Run/DB/OIDC/provider/event/model/audit/E2E/watch/backup/rollback proof | 唯一可執行 |
| C | `ODP-PLAN-HEATZONE-LABEL-BACKFILL-001` | human_gate | >=200 真實成熟 labels 與 dataset lineage/hash/owner/freshness | 不適用 |
| C | `ODP-PLAN-SITESCORE-OUTCOME-BACKFILL-001` | human_gate | >=200 真實 M6/M12 outcomes 與 lineage/hash/owner/freshness | 不適用 |
| C | `ODP-PLAN-AVM-OUTCOME-BACKFILL-001` | human_gate | >=120 真實成熟成交 outcomes、confidential access 與 lineage/hash | 不適用 |
| C | `ODP-PLAN-OSS-LEGAL-POLICY-001` | human_gate | Legal/Security/Risk 具名 policy/LGPL/exception 決策與 authoritative signed/readback receipt | 不適用 |
| C | `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` | human_gate | 具名管理 owner 核准 immutable baseline/problem/source/policy/scope | 不適用 |
| D | `ODP-PLAN-UAT-SIGNOFF-001` | human_gate | 6 種角色、exact staging release、scenario results、defect ledger、正式 signoff | 不適用 |
| E | `ODP-PLAN-FINAL-GATE-AUDIT-001` | human_gate | 重跑 84 RTM／26 tasks／全部 production proof 與 Human/Ops release decision | 不適用 |

## 5. 目前四個 active task 的 batch correction

### Acceptance

不得只修 hard-coded count。完整批次還要同時處理：

- runner-start source/tree SHA；
- evidence-only child diff；
- Playwright/Pytest command、version、time、exit；
- exact normalized test ids；
- unique spec files；
- zero/skipped/unexpected/stale/tampered result；
- Python failure傳回 shell；
- 清除 unrelated screenshot diff。

### OSS

不得只把 local receipt 改成 default reject。完整批次還要同時處理：

- concrete authoritative readback 或所有 active exemption disabled；
- release/source/SBOM/lock/evidence hashes；
- non-empty frozen inventory；
- export failure不得 fallback；
- prod/dev scope；
- NOTICE與attestation；
- 所有 local-lookalike／expiry／scope／hash mutation。

### Observability

不得只補兩個已知反例。完整批次要覆蓋全部 signal inventory、每個
signal/category window coverage、finite/non-negative/unit/domain、project/release、
route delivery、owner/runbook、receipt tamper 與 provider readback。

### SiteScore

不得只修 count 或 maturity alias。完整批次要同時清除所有 invented model
governance 欄位，並讓 prediction source、true horizon、population alignment、
interval policy、Gate 2 receipt、model card 與 backfill handoff一致。

## 6. Human/Ops 邊界

Auto worker 可以：

- 準備模板；
- 檢查 schema；
- 驗證 returned receipt；
- 在缺資料／簽核時維持 governed-disabled／NO-GO。

Auto worker 不可以：

- 產生真實標籤或成交／開店 outcomes；
- 代替 Legal、Finance、Product、Management 或 UAT tester 簽核；
- 把 repository JSON、角色字串或 AI fixture 當成 authority；
- 把本機／mock 結果說成 staging/live production proof。

## 7. 完成定義

這個 program 只有在以下條件全部成立時才算完成：

1. 26 個治理 task 全部依各自完整 packet 完成、獨立 review、CI、merge、
   owner closeout；
2. Human/Ops data、legal、management、UAT 與 release receipts 真實可回讀；
3. Live staging proof 綁 exact merged/deployed SHA；
4. Final Gate 對 84/84 rows 提供正面證據並由 Human/Ops 正式核准。

任何一項不成立，結論仍為 `NO-GO`。
