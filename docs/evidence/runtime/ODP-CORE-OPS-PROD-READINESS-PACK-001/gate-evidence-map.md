# Gate 證據對照（gate-2／3／5／6）（DRAFT）

本文件逐條對照 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 中每個 `required_check` 的證據。

**狀態定義：**

| 狀態 | 意思 |
|---|---|
| **已有證據** | repo 內有綁定 candidate 的 receipt，且符合該 check |
| **部分** | 有程式、文件或離線證據，但沒有綁定 candidate 的 live receipt，或尚未審閱 |
| **缺** | 沒有證據 |
| **需人工決定** | 只能由具名的人做處置，例如 deviation、not-applicable 或簽核 |

**沒有任何一條是「已有證據」。** registry 中 gate-2、3、5、6 的 `receipts` 全部是空的（`:280`、`:319`、`:435`、`:491`），而且都綁定 dev candidate `6140d0ef…`，不是 core-ops candidate。

admission 只檢查 `admission_target` 等於部署環境的 gate（`delivery_toolchain/release/check_runtime_admission.py:206-241`）：

- staging 看 gate-2（`:254`）。
- production 看 gate-3、gate-5、gate-6（`:298`、`:405`、`:454`）。

core-ops candidate 必須是**新的** candidate SHA。上述每個 gate 都要重新綁定那個 SHA，並寫入 `result: pass` 的 receipt（`check_runtime_admission.py:243-260`）。

---

## gate-2 Data Gate（staging admission）

- registry 位置：`:243-285`
- owner／reviewer：Codex2／Claude（`:247-248`）
- 狀態：`blocked`（`:249`）
- blockers：`:281-284`

| required_check（`:256-261`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| source contract and sample data reviewed | **需人工決定** | core-ops 的外部來源一律關閉（`deploy-dev.yml:1270`），staging 用 masked snapshot（`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:233`）。缺的是：要嘛提出 snapshot 來源契約的審閱紀錄，要嘛具名把這項處置為 not-applicable 或 deviation | gate owner Codex2 + Human/Ops |
| canonical mapping tests pass | **部分** | registry 證據只列出文件集 `docs/data`（`:271-273`），沒有綁定 candidate 的測試 receipt。缺的是新 candidate 的 CI 結果 | Codex2 |
| data quality tests pass | **缺** | 沒有 receipt | Codex2 |
| freshness and lineage evidence captured | **缺** | `check_live_production_data.py` 從未對這個 candidate 的 live 部署執行（`:283`） | Codex2；live 執行由 Release owner |
| point-in-time correctness checked for model-ready data | **需人工決定** | core-ops 不使用 model-ready data（design `:160-165`）。可能處置為 not-applicable，但需要具名決定 | Codex2 + Human/Ops |
| backfill, retry, and quarantine paths tested where applicable | **部分** | staging 的 `worker_idempotency` stage（`staging_lifecycle.py:2689`）與 live gate 的 worker 檢查程式存在，但沒有 live receipt。另有已知問題：佇列為空時會失敗（design `:285`） | Codex2 |

---

## gate-3 Model and Solver Gate（production admission）

- registry 位置：`:286-325`
- owner／reviewer：Codex2／Antigravity2（`:291-292`）
- 狀態：`blocked`（`:293`）
- 已有一份**未簽署**的 core-ops passed-with-deviation 草稿：`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:151-189`

| required_check（`:300-305`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| dataset snapshot is reproducible and hash-identified | **需人工決定** | 列在草稿 `required_checks_deviated` 內（design `:172`） | Product Owner + ML Risk Owner（design `:147`） |
| baseline comparison and segment metrics reviewed | **需人工決定** | 同上（`:173`） | 同上 |
| calibration or coverage checked where applicable | **需人工決定** | 同上（`:174`） | 同上 |
| model card or decision card completed | **需人工決定** | 同上（`:175`） | 同上 |
| human approval and rollback path recorded | **缺／需人工決定** | **不在**草稿的 deviated 清單內，所以必須真正滿足。需要的是：草稿上的具名簽署（`risk_owner`、`approved_by`、`approved_at`、`review_by`，design `:181-184`），加上回滾路徑。回滾路徑見 `rollback-plan.md`，目前未審閱、未證明 | Product Owner + ML Risk Owner；回滾路徑由 Release owner |
| solver hard constraints, feasibility, infeasibility diagnostics, and runtime report captured | **需人工決定** | 列在 deviated 清單內（`:176`）。solver 功能不在 core-ops 範圍（`:169`） | Product Owner + ML Risk Owner |

**補償控制**（草稿 `:177-180`）：live gate 與 staging verify 的模型拒絕檢查，以及 Web 通知。這些程式存在，但**沒有 live receipt**，見 gate-5 第 2 項。

registry 中 `passed-with-deviation` 的格式要求 `deviation` 物件必須有非空的 `description`、`approver`，以及 ISO 日期格式的 `review_by`（`delivery_toolchain/e2e/check_release_gate_registry.py:545-560`）。

相關的 carryforward 義務 `ODP-SIGN-OFF-MODEL-RISK` 要求真實的訓練紀錄，目前是 `BLOCKED_BY_EVIDENCE`（`docs/evidence/completion/ODP-RELEASE-ACCEPTANCE-CARRYFORWARD-001/obligation_matrix.json:778`）。core-ops 的 deviation 是**不滿足**這項義務、改以 deviation 處置的路徑；兩者不衝突，但不能互相替代。

---

## gate-5 E2E, Performance and UAT Gate（production admission）

- registry 位置：`:393-441`
- owner／reviewer：Codex2／Human/Ops（`:398-399`）
- 狀態：`blocked`（`:400`）

| required_check（`:407-411`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| every canonical E2E spec in the acceptance registry exists and produced an execution receipt | **部分** | registry 是 `delivery_toolchain/e2e/product_e2e_receipt.py`：18 個 spec、130 個 Playwright test（`:20`、`:24`）。最新的 receipt `docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json` 綁定的是 `8aa77355…`（`:8`），只有 129 個 test（`:63-66`），而且是本機 docker compose 執行。缺的是：在 core-ops candidate SHA 上重新產生的 receipt，數量必須是 130。另外要決定模型相依的 scenario 在 core-ops 下怎麼算，見下方說明 | Codex2 |
| live end-to-end gate passes against the deployed release candidate | **缺** | 程式存在：staging verify 的 `live_e2e_gate`（`staging_lifecycle.py:2697`），以及 production 部署腳本內的 gate（`deploy_cloud_run_waji.sh:1286-1303`）。repo 內唯一的 live gate receipt 是 dev、2026-08-09、`ok: false`（`docs/evidence/runtime/ODP-P10-LIVE-FLEET-STATE-REPAIR-001/live-e2e-gate-9c95ecc3.json:353-375`）。缺的是：core-ops candidate 的 staging `live_e2e_gate` 通過 receipt。前提是 design §12.5 的 IAM 與網路條件（`:277-287`） | Release owner（design `:148`） |
| API P95, batch window, and render/runtime budgets measured and within target | **缺** | CI 的 `performance-gate` 只跑 in-process 的 TestClient（`.github/workflows/ci.yml:494-503`；`tests/performance/test_load_and_soak.py`），不是部署環境的量測。NFR 五項全部 `BLOCKED_BY_EVIDENCE`（`docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json:79-183`）。staging 負載測試是 pre-prod blocking（`obligation_matrix.json:82`，負責人 Release／QA Lead）。render budget 沒有任何量測。batch cutoff 沒有定義（`disposition.json:133`） | Release／QA Lead；cutoff 由 Human/Ops 定義 |
| UAT scope, data, and accounts prepared for affected roles | **部分** | 本包的 `uat-plan.md` 是範圍草稿，**尚未**準備資料與帳號。UAT 在哪個環境執行要先決定（`uat-plan.md` §2） | Product Owner + QA Lead |
| P0/P1 UAT defects are zero and high-risk workflows are signed off | **缺／需人工決定** | 沒有任何 UAT 執行紀錄或簽核。`ODP-SIGN-OFF-UAT` 是 `PENDING_HUMAN_AUTHORITY`（`obligation_matrix.json:749`） | Product Owner、QA Lead、Security Owner、SRE Owner、Business Owner（`docs/uat/UAT_ACCEPTANCE_PLAN.md:54`） |

**關於 E2E receipt 的範圍：**

- 18 個 spec 中有一部分是模型相依的，例如 `operator-network-scoring.spec.ts`（SiteScore）與 `operator-network-rebalance.spec.ts`（AVM、NetPlan）。
- 這些 spec 在 deterministic 環境以 fixture 執行，可以通過，但**不能**拿來證明 production 上的模型功能可用。
- core-ops 的 gate-5 處置要寫明：receipt 只證明 spec 存在而且可執行，模型功能在 production 是拒絕的（design `:14`、`:43`）。這需要具名決定。

---

## gate-6 Ops, Release and Audit Gate（production admission）

- registry 位置：`:442-497`
- owner／reviewer：Claude／Human/Ops（`:447-448`）
- 狀態：`blocked`（`:449`）

| required_check（`:456-462`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| release note and deployment plan completed | **部分** | 本包的 `release-notes-draft.md` 與 `deployment-plan.md` 都是 DRAFT。缺的是審閱與簽核 | gate owner Claude；核准由 Human/Ops |
| rollback plan completed, reviewed, and proven | **部分（completed）／缺（reviewed、proven）** | `rollback-plan.md` 是草稿。首次 release 的語意在 PR #1565，**待合併**。staging 與 production 都沒有任何回滾執行的 receipt（`rollback-plan.md` §5）。「首次 release commit 後下線」程序與「blue state 保存」都**需人工決定**。另有既有 blocker：workflow 的 production `capture-state` 參數順序錯誤，不會寫出 state，deploy job 在 commit 後 failure（`deployment-plan.md` §5 第 1 點） | Release owner + Human/Ops |
| monitoring watch window and on-call route assigned and live | **缺** | Terraform 沒有任何監控資源；告警接收者只是角色字串；沒有 on-call 通道；watch receipt 沒有產生者（`monitoring-oncall-plan.md` §1、§3）。值班表留白 | Human/Ops |
| runbook updated for changed operational behaviour | **部分** | 本包的 `runbook-core-ops.md` 是差異草稿，**尚未**併入 `docs/runbooks/` | gate owner Claude；審閱由 SRE／Human/Ops |
| backup/DR impact reviewed | **部分** | 本包的 `backup-dr-impact.md` 是草稿。production 沒有備份讀回或演練；production Cloud SQL 與 IaC 不符 | SRE Owner／Human/Ops |
| evidence IDs mapped to RTM rows and evidence manifest reproducible from its hash | **缺** | RTM 在 `docs/rtm/ODAY_PLUS_EXECUTION_RTM.md`（`status: approved`，`version 0.1.0`），但沒有對 core-ops candidate 的 evidence 對照。`docs/release/RELEASE_GATE_CHECKLIST.md:139-143` 都是 `not-started`。注意：release manifest digest（artifact identity）不等於 evidence manifest | Release owner／QA Lead |
| decision logs record actor, role, policy version, reason, override, and timestamp | **部分** | 應用層的稽核與保存有整合測試（`docs/evidence/AUDIT_EVIDENCE_RETENTION_PROOF.md:60-90`）。PostgreSQL 持久化接線**已存在**：`_postgres_bundle` 建立帶 WORM sink 的 `DurableAuditLog` 與 `DurableEvidenceBundleStore`（`shared/infrastructure/persistence/factory.py:609-632`），`build_persistence` 在 PostgreSQL 模式走這條路（`:709-725`），API 啟動時實際取用（`apps/api/oday_api/main.py:180`、`:254-260`）。`AUDIT_EVIDENCE_RETENTION_PROOF.md:111-113` 把這項列為後續項，已過時。仍缺：production 實際設定與 bucket retention 讀回、保存效果、排程 retention sweep（repo 內只找到手動 API `POST /evidence/retention/purge`，`apps/api/app/routes/audit.py:505-515`），以及從部署環境匯出的 decision log 樣本 | Claude；審閱由 Human/Ops |

gate-6 的既有 blocker 指出，pre-production 項目要在 `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` 由 Human/Ops 簽核（registry `:494`）。該檔的 Human/Ops checklist 全部是 `pending-human`（`PRODUCT_RELEASE_GO_NO_GO.md:51-58`），而且沒有 on-call 與 watch 的列。

---

## 附註：gate-0／1／4（不由 production admission 檢查，但與 core-ops 有關）

- gate-0、gate-1、gate-4 的 `admission_target` 都是 `dev`（registry `:44-45`；gate-4 `:337-338`）。只要 registry 推進到 staging 或 production，admission 就**不再**檢查它們（`check_runtime_admission.py:218-220`）。
- 但 gate-4 的 deviation 文字寫明，它只適用該 dev candidate；staging 與 production 需要權威的 H01 證據、處理掉相關 finding 或重新具名處置，並重新核准（registry `:378`）。
- 結論：程式不會擋，但**治理上需要人工決定**。見 `human-decisions-checklist.md`。
