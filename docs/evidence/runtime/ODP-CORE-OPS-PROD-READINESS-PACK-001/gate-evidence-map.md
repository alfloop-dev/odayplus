# Gate 證據對照（gate-2／3／5／6）（DRAFT · 2026-11-27 版）

本文件逐條對照 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json` 中每個 `required_check` 的證據。

- 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。
- GCP／GitHub 現況引用 `live-readback-20261127.md`（`L-…`）；負責人決定 A–H 見同檔 §2。

**狀態定義：**

| 狀態 | 意思 |
|---|---|
| **已有證據** | repo 內有綁定 candidate C 的 receipt，且符合該 check |
| **部分** | 有程式、文件、live 讀回或離線證據，但沒有綁定 C 的 receipt，或尚未審閱 |
| **缺** | 沒有證據 |
| **需人工決定** | 只能由具名的人做處置，例如 deviation、not-applicable 或簽核 |

**對 candidate C 而言，沒有任何一條是「已有證據」。**

- registry 目前綁定鏈 U 的 `8e5020a29bad…`（`RELEASE_GATE_REGISTRY.json:15-31`）。依 `L-5.2`，這個 candidate **不採用**。
- C 要依決定 A 在凍結後選定。選定後，registry 要由另一個 task 重新綁定 C，每個 gate 都要寫入綁定 C 的 `result: pass` receipt（`delivery_toolchain/release/check_runtime_admission.py:261-278`）。
- admission 只檢查 `admission_target` 等於部署環境的 gate（`check_runtime_admission.py:224-259`）：staging 看 gate-2（`RELEASE_GATE_REGISTRY.json:402`）；production 看 gate-3、gate-5、gate-6（`:588`、`:707`、`:756`）。

---

## gate-2 Data Gate（staging admission）

- registry 位置：`:391-451`；owner／reviewer：Codex2／Claude（`:395-396`）
- 狀態：`passed-with-deviation`（`:397`），**只適用 `8e5020a29` 進 staging**（deviation `:446-451`，`review_by: 2026-11-30`）。deviation 寫明「Any later candidate or environment needs a fresh disposition」（`:447`）。
- 對 C：**需要重新處置**（HD-08）。以下是沿用 `8e5020a29` 的處置時，每項的狀態。

| required_check（`:403-410`） | 狀態（對 C） | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| source contract and sample data reviewed | **需人工決定** | `8e5020a29` 的處置是 not-applicable：外部來源全部關閉、沒有任何憑證（`:447`）。C 沿用同樣的 posture（`deploy-dev.yml:1745`），可以同樣處置，但要重新具名 | Codex2 + 負責人 |
| canonical mapping tests pass | **部分** | `8e5020a29` 以 CI 的 product-db 等 lane 為證（receipt `:433-444`）。C 需要自己的 CI 結果 | Codex2 |
| data quality tests pass | **部分** | 同上 | Codex2 |
| freshness and lineage evidence captured | **缺** | `8e5020a29` 的處置寫明「not satisfied」，延到 staging verify 與 production gate（`:447`） | Codex2；live 執行由 Release owner |
| point-in-time correctness checked for model-ready data | **需人工決定** | `8e5020a29` 處置為 not-applicable：core-ops 不提供模型（`:447`）。C 可同樣處置 | Codex2 + 負責人 |
| backfill, retry, and quarantine paths tested where applicable | **部分** | staging 的 `worker_idempotency` stage 存在，且「佇列為空時失敗」的既有問題已修正（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:366`）。C 的 staging receipt 尚未產生 | Codex2 |

---

## gate-3 Model and Solver Gate（production admission）

- registry 位置：`:577-616`；owner／reviewer：Codex2／Antigravity2（`:581-582`）
- 狀態：`blocked`（`:583`）；`receipts` 為空（`:609`）
- **負責人決定 G（2026-11-20）**：core-ops 記為不適用／passed-with-deviation。**簽署**仍在 C 選定之後（HD-15）。
- 草稿（未簽署）：`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:231-269`

| required_check（`:589-596`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| dataset snapshot is reproducible and hash-identified | **需人工決定**（方向已定，待簽） | 列在草稿 `required_checks_deviated`（design `:251-256`） | Product Owner + ML Risk Owner（design `:226`）；方向：決定 G |
| baseline comparison and segment metrics reviewed | **需人工決定**（方向已定，待簽） | 同上 | 同上 |
| calibration or coverage checked where applicable | **需人工決定**（方向已定，待簽） | 同上 | 同上 |
| model card or decision card completed | **需人工決定**（方向已定，待簽） | 同上 | 同上 |
| human approval and rollback path recorded | **部分／待簽** | **不在**草稿的 deviated 清單內，必須真正滿足。human approval：草稿上的具名簽署（`risk_owner`、`approved_by`、`approved_at`、`review_by`，design `:261-264`），仍空白。rollback path：首次 release 的下線方向已定（決定 D），程序寫在 `rollback-plan.md` §2.4，**尚未演練、尚未簽** | Product Owner + ML Risk Owner；回滾程序由負責人 |
| solver hard constraints, feasibility, infeasibility diagnostics, and runtime report captured | **需人工決定**（方向已定，待簽） | 列在 deviated 清單；solver 不在 core-ops 範圍 | 同上 |

**補償控制**（草稿 design `:257-260`）：live gate 與 staging verify 的模型拒絕檢查，以及 Web 通知。程式存在；C 的 staging live gate receipt 尚未產生（gate-5 第 2 項）。

registry 的 `passed-with-deviation` 格式要求 `deviation` 物件有非空的 `description`、`approver` 與 ISO 日期的 `review_by`（`delivery_toolchain/e2e/check_release_gate_registry.py:545-560`）。

---

## gate-5 E2E, Performance and UAT Gate（production admission）

- registry 位置：`:696-744`；owner／reviewer：Codex2／Human/Ops（`:700-701`）
- 狀態：`blocked`（`:702`）；`receipts` 為空（`:737`）

| required_check（`:708-714`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| every canonical E2E spec in the acceptance registry exists and produced an execution receipt | **部分** | registry 是 `delivery_toolchain/e2e/product_e2e_receipt.py`：18 個 spec、130 個 Playwright test（`:20`、`:24`）。repo 內的 receipt `docs/evidence/e2e/PRODUCT_E2E_EXECUTION_RECEIPT.json` 不是 C 的。缺：C 的 receipt；以及模型相依 scenario 在 core-ops 下怎麼算的具名處置（見下） | Codex2 |
| live end-to-end gate passes against the deployed release candidate | **部分** | staging verify 的 `live_e2e_gate`（`staging_lifecycle.py:2884-2886`）與 production 部署腳本的 gate（`deploy_cloud_run_waji.sh:1505-1519`）都存在。鏈 U 的 staging deploy 曾成功（`L-5.1`），表示這條路徑在 staging 可以通過；但 candidate 不採用、receipt 也沒有收進 repo。缺：C 的 staging receipt | Release owner |
| API P95, batch window, and render/runtime budgets measured and within target | **缺** | CI 的 `performance-gate` 只跑 in-process TestClient（`.github/workflows/ci.yml:1412`；`tests/performance/test_load_and_soak.py`），不是部署環境量測。NFR 全部 `BLOCKED_BY_EVIDENCE`（`docs/evidence/runtime/ODP-NFR-RUNTIME-EVIDENCE-001/disposition.json:79-183`）。batch window 對 core-ops 記為 not-applicable（決定 F）。API P95 有 production 告警（`L-3.2`），但沒有量測 receipt；render budget 沒有量測 | Release／QA Lead |
| UAT scope, data, and accounts prepared for affected roles | **部分** | 環境與帳號方式已定（決定 B）：ephemeral staging、bootstrap `platform_admin` 加邀請 API（`uat-plan.md` §2、§3）。帳號尚未建立；PriceOps／AdLift／Growth／Franchisee 是否納入仍待決；UAT-CO-11 在 staging 無法執行（`uat-plan.md` §4） | 負責人；帳號由 Claude 建立 |
| P0/P1 UAT defects are zero and high-risk workflows are signed off | **缺／待簽** | 沒有 UAT 執行紀錄。依決定 B 由負責人本人簽核。`ODP-SIGN-OFF-UAT` 仍是 `PENDING_HUMAN_AUTHORITY`（`obligation_matrix.json:749`） | 負責人 |

**E2E receipt 的範圍：** 18 個 spec 中有模型相依的（例如 SiteScore、AVM、NetPlan）。它們在 deterministic 環境以 fixture 執行，可以通過，但**不能**拿來證明 production 上的模型功能可用。core-ops 的 gate-5 處置要寫明：receipt 只證明 spec 存在且可執行，模型功能在 production 是拒絕的（design `:14`、`:43`）。這需要具名決定（HD-16）。

---

## gate-6 Ops, Release and Audit Gate（production admission）

- registry 位置：`:745-800`；owner／reviewer：Claude／Human/Ops（`:749-750`）
- 狀態：`blocked`（`:751`）；`receipts` 為空（`:793`）

| required_check（`:757-765`） | 狀態 | 證據或缺少的項目 | 負責角色 |
|---|---|---|---|
| release note and deployment plan completed | **部分** | 本包的 `release-notes-draft.md` 與 `deployment-plan.md`（2026-11-27 版）。缺審閱與簽核 | gate owner Claude；核准由負責人 |
| rollback plan completed, reviewed, and proven | **部分（completed）／缺（reviewed、proven）** | `rollback-plan.md` 已依 `origin/dev` 更新：PR #1565 已合併；切換前狀態以 artifact 保存（`deploy-dev.yml:2034-2083`）；首次 release 的下線程序依決定 D 寫成（§2.4）。缺：審閱；§5.1 的 staging 演練；任何回滾執行的 receipt | Release owner + 負責人 |
| monitoring watch window and on-call route assigned and live | **部分** | Terraform `monitoring.tf` 存在；live 有 email 通道、6 條告警、Web uptime（`L-3`）；on-call 為負責人、watch 30 分鐘、Claude 24 小時每小時讀回（決定 C）。缺：watch receipt 無法產生（`L-6`，pending）；`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設（`L-4.3`）；API uptime check 未讀回；值班表具名簽核 | 負責人 |
| runbook updated for changed operational behaviour | **部分** | `runbook-core-ops.md`（2026-11-27 版）含 release identity 讀法與決定 D 的程序；**尚未**併入 `docs/runbooks/` | gate owner Claude；審閱由負責人 |
| backup/DR impact reviewed | **部分** | `backup-dr-impact.md`（2026-11-27 版）。migration 前備份已由程式處理；新的 Cloud SQL 與鎖定的 WORM 已存在（`L-2.3`、`L-2.5`）。缺：備份與 PITR 讀回；§6 的 DR 演練與收據（決定 E） | 負責人 |
| evidence IDs mapped to RTM rows and evidence manifest reproducible from its hash | **缺** | RTM 在 `docs/rtm/ODAY_PLUS_EXECUTION_RTM.md`，沒有 C 的 evidence 對照。`docs/release/RELEASE_GATE_CHECKLIST.md:139-143` 都是 `not-started`。release manifest digest 不等於 evidence manifest | Release owner／QA Lead |
| decision logs record actor, role, policy version, reason, override, and timestamp | **部分** | 應用層稽核與保存有整合測試（`docs/evidence/AUDIT_EVIDENCE_RETENTION_PROOF.md:60-90`）；PostgreSQL 持久化接線存在（`shared/infrastructure/persistence/factory.py:654`、`:768-778`、`:830`；`apps/api/oday_api/main.py:263`、`:269`）；production WORM bucket 已鎖定 7 年（`L-2.5`）。缺：production sink 設定讀回、排程 retention sweep、部署環境匯出的 decision log 樣本 | Claude；審閱由負責人 |

gate-6 的 blocker 指出，pre-production 項目要在 `docs/evidence/PRODUCT_RELEASE_GO_NO_GO.md` 由 Human/Ops 簽核（registry `:796`）。該檔的 Human/Ops checklist 全部是 `pending-human`（`PRODUCT_RELEASE_GO_NO_GO.md:51-58`）。

---

## 附註：gate-0／1／4（production admission 不檢查，但與 core-ops 有關）

- gate-0、gate-1、gate-4 的 `admission_target` 都是 `dev`（`RELEASE_GATE_REGISTRY.json:45`、`:291`、`:628`）。registry 推進到 staging 或 production 之後，admission 就**不再**檢查它們（`check_runtime_admission.py:236-238`）。
- 目前 gate-1 與 gate-4 是 `blocked`，等待 HD-07（`:286`、`:623`；registry `description` `:6`）。gate-4 的 npm deviation 只適用先前的 dev candidate，`8e5020a29` 也需要重新處置（`:661`）；C 同樣需要。
- 結論：程式不會擋，但**治理上需要人工決定**（HD-07）。
