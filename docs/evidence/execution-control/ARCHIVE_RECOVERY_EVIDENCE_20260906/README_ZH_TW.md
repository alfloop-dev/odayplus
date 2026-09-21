# 38 個遺失任務的歷史驗收證據盤點（中立資料集・第 4 輪）

- 產出任務：`ORCH-ARCHIVE-RECOVERY-EVIDENCE-001`（owner Claude，reviewer Codex2）
- 產出時間：2026-09-06T19:05:00Z（第 4 輪由 Claude 接手，重建 CI 執行綁定並修正證據語意）
- 機讀資料：`docs/evidence/execution-control/ARCHIVE_RECOVERY_EVIDENCE_20260906/task_evidence_inventory.json`
- 本輪取代第 1 輪的 `docs/evidence/recovery/ORCH-ARCHIVE-RECOVERY-EVIDENCE-001/`（該路徑在 canonical owned_paths 之外，已移除）

> 本文件與同目錄 JSON 都**只是證據盤點**。不是 archive 收據、不是 `done` 宣告、不是 Human GO、不是部署授權。
> 本次未寫入任何 canonical 狀態：`ai-status.json`、`ai-task-archive/`、config、queue、Supervisor、watchdog 皆未變動；未跑測試、未 dispatch workflow、未讀 credential、未對外抓 provider 資料。

## 結論摘要

- 總數 **38**：建議 `verified_candidate` **35**、建議 `blocked` **3**。
- 信心：high **27**、medium **11**。
- 候選 PR 更正 **1** 筆（見「候選誤配更正」）。
- 候選映射一致（`candidate_mapping.verdict = consistent`）**37/38**；唯一不一致的是 XR 的輸入候選 #996，那正是誤配的證據，其建議套用於更正後候選。
- **CI 執行綁定稽核**：112 條原標 `met_by_test_in_green_ci` 的條款全部重新核對「被引用測試檔 → 實際收集它的 CI job」，其中 5 條的測試檔在精確 head 上未被任何成功 check 執行，已改記 `test_delivered_not_executed_at_exact_head`；其餘綁錯 job 但確有成功 check 執行者已改綁正確的 job／step。
- **逐條 acceptance 共 194 條**，狀態分布：
  - `met_by_test_in_green_ci` 107 條 — 本 PR 交付了對應測試檔，且該檔位於精確 head 上某個 conclusion=success 的 CI job 實際收集的路徑內（綁定依 ci_execution_binding 的 job→收集路徑對照核對；僅到 job／step 層，未取 job log）
  - `met_by_delivered_artifact` 33 條 — 交付了對應程式或文件，本盤點只驗其存在與內容涵蓋，未重算其結論
  - `met_by_receipt` 18 條 — 交付物內既有收據直接記載本條款的量測結果
  - `process_constraint_unverifiable` 18 條 — 本條款約束的是執行過程，無法由交付物與唯讀記錄獨立驗證
  - `partially_met` 9 條 — 本條款可拆成數項，部分有既有證據、部分沒有
  - `test_delivered_not_executed_at_exact_head` 5 條 — 本 PR 交付了對應測試檔並可定位到具名 assertion，但精確 head 上唯一會收集該檔的 CI job 為 skipped（或該路徑不在任何 CI job 的收集範圍內），因此沒有任何成功 check 執行過它
  - `unmet_per_own_receipt` 3 條 — 該任務自身的收據直接記載本條款未達成
  - `not_evidenced` 1 條 — 唯讀範圍內找不到對應本條款的既有證據

### 第 1 輪的四項錯誤與第 2 輪更正

**[P1] JSON decision_rule 要求映射一致，但 31 筆 recommendation 套用於 inventory_candidate 的 verified_candidate 其 candidate_mapping.verdict 為 mismatch（含更正後的 XR 共 32 筆）。PR #1148 body 同時有 Task 與 ReviewBus 任務 ID，JSON 卻記 null/false。**

→ 根因是 PR body task-id 擷取只認得一種格式。本輪解析四種既有格式（ReviewBus `- ID:`／`- 任務 ID:`、task_finalize `Task:` 標頭、自由格式 `任務：`），38 筆全部解析成功：37 筆與本 ID 相符，verdict 由 mismatch 改為 consistent；唯一仍為 mismatch 的是 XR 的輸入候選 #996，其 body 宣告的是 ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001，這正是候選誤配的證據。candidate_mapping 另新增 pr_body_task_id_sources、pr_body_task_id_resolved_from 與 verdict_reasons，並把 recommendation 改為由逐條 acceptance 狀態機械推導，不再有「verdict 與 recommendation 各說各話」。

**[P1] 既有 verification／runtime 證據未妥善對應到 acceptance：INT-MANUAL-CORRECTION 以泛用 CI 成功帶過明確未驗證的 PostgreSQL readback；更正後的 XR data-platform #61 只記 PR／check 名稱與 artifact 存在性，未取出 provider-off／snapshot／SBOM／lineage 的收據內容與 check／receipt 時間。**

→ 新增 acceptance_evidence_map：38 個 ID 共 194 條 acceptance 逐條對應證據種類、精確引用（檔案路徑@merge SHA、測試檔、check 名稱）、時間與狀態。INT-MANUAL-CORRECTION 的 PostgreSQL 條款改為引用具名測試 tests/integration/test_postgresql_persistence.py::test_postgresql_manual_correction_readback_and_rollback，並核對該 merge commit 上的 ci.yml：product job 掛 postgis/postgis:16-3.5 service 且設 INTAKE_TEST_DATABASE_URL，該測試檔只有 skipif(not INTAKE_TEST_DATABASE_URL) 而無 requires_live_env 標記，因此在 CI 內對真實 PostgreSQL 執行——先前「未驗證」的判斷本身有誤。XR 改為引用 merge 7b0670d7 上四份收據的實際內容：source-permission-matrix（16 來源 enabled 0／receipt 0／schedule 0／public egress false）、cross-repo-evidence-ledger（producer SBOM／NOTICE／contract lock digest、egress default-deny 與 manifest sha256、七個品質軸逐項比對計數）、technical-gap-closure-matrix（10 項 gap：6 技術關閉、4 法律待決、unresolved_technical_blockers=0）與 closeout-audit-manifest（TECHNICAL_READINESS_VERIFIED）；並補上精確 head b1824c97 七個 check 的 started_at／completed_at 與 task-review-gate 的 updated_at 2026-08-24T05:54:45Z。

**[P2] 缺證據的宣稱有誤：WEB-PASSWORD-FIRST-SECURITY-E2E-002 說找不到 security E2E 收據，但其 evidence_files 就列了該收據，且 pinned merge 2377168c 的收據 §2／§4／§5 有 OIDC 測試矩陣、2026-09-01 驗證結果與明確的無 live provider 限制；GITHUB-GCP-ENV-BOOTSTRAP 說 production 環境與保護未達成，但 pinned merge 8ad3f10d 的 github-environments-audit.json 記有 production 的 required_reviewers。**

→ 兩項皆已更正。WEB-PASSWORD：收據可定位（本 PR 交付），逐條引用其 §2 證據矩陣與 §4 驗證紀錄（2026-09-01，七層命令與結果）；「完整 OIDC 設定不回歸」改判為 partially_met——測試層有覆蓋，live provider 層是收據 §5 自陳的界線，不是缺收據。另定位到先前說找不到的第二個敘述型 artifact：auth migration rollout checklist = docs/deployment/AUTH_MIGRATION_ROLLOUT_CHECKLIST.md，同由本 PR 交付。GITHUB-GCP-ENV：A1（staging/production 具 required reviewers）改判為 met_by_receipt，引用 production environment id 20574639394（created 2026-08-25T15:41:19Z）與其 required_reviewers（Alien-alfaloop、ajoe734）；A3 改判為 partially_met——GitHub 側環境與保護確實存在，未達成的是 production 的 GCP 基礎設施、環境變數（total_variables=0）與 PROD-GCP-01…PROD-OPS-05 五項具名人類決定。本 ID 仍為 blocked，但理由已改為精確的那一半。

**[P2] 兩份交付檔都在 canonical owned_paths docs/evidence/execution-control/ARCHIVE_RECOVERY_EVIDENCE_20260906/ 之外。**

→ 兩份交付已移入 owned_paths，內部路徑引用同步更新，第一輪路徑刪除並在 supersedes 欄位留下對照。

### 第 2 輪評審意見與第 3 輪更正

**[P1] ODP-JOB-PARTIAL-DISPOSITION-001 A2 把 succeeded/failed item receipt 與 retry 不重做成功項標為 met_by_test_in_green_ci，但測試僅確認文件字串，docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md 明載 PARTIAL absent / BLOCKED_BY_EVIDENCE，功能是未來 Pathway A。**

→ A2 狀態由 `met_by_test_in_green_ci` 更正為 `met_by_delivered_artifact`。按原 task 條件分支（無適用 producer 則保持 absent 並交付 formal handback），因查無真實 producer 證據，PARTIAL 行為（明細收據與重試）未於 runtime 實作，而是於 formal handback 文件（`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md` §4.2-4.3）建立未來 Pathway A 設計契約；測試 `tests/governance/test_job_partial_disposition.py` 中的 `test_shared001_handback_document_exists_and_covers_contracts` 僅驗證 handback 文件與字串存在，不執行 item receipt/retry 行為。按條件分支，此項之實作行為不適用，formal handback 契約已交付。

**[P1] ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001 A9 將明確 deferred 至後續 Codex staging rollout 的 resource/IAM/vars 當成歷史 code task 唯一 blocking_reason。A10 仍 evidence=[]；GCP_DEPLOY_GUIDE.md:227-229 已有 state/recovery bucket 分離流程，須補 pinned 引用。**

→ 拆開 code contract／producer-consumer 接線與 deferred runtime gap。A9 改判為 `met_by_test_in_green_ci`，引用 `tests/release/test_release_environment_precheck.py`（distinct/identical/placeholder recovery bucket assertions）、`tests/ops/test_deploy_workflow_contract.py` 及 `.github/workflows/deploy-dev.yml` @ `b9dd2e7b337e`；說明 live destination 與 GCP resource/IAM/vars 依 task brief 定義明確 deferred 至後續 staging rollout（`ODP-EPHEMERAL-STAGING-ROLLOUT-001`），不倒灌阻擋本 code 任務。A10 補上 pinned 引用 `docs/deployment/GCP_DEPLOY_GUIDE.md:227-236 @ b9dd2e7b337e` 與 `docs/deployment/ENVIRONMENTS.md @ b9dd2e7b337e`。recommendation 重算為 `verified_candidate`，`blocking_reasons` 清空。

**[P1] 前輪 DEV-STAGED-GATE 與 STAGING-LIFECYCLE 的 pending_reviewer_reading 亦未處理：依 code/admission scope 核對 source-off/egress 契約，dry-run metadata 與 live posture 分開。**

→ 依 code/admission scope 核對 source-off 與 egress 契約，將 dry-run metadata 與 live runtime posture 分開。`ODP-DEV-STAGED-GATE-RECONCILIATION-001` A7 更正為 `met_by_test_in_green_ci`，引用 `delivery_toolchain/e2e/check_release_gate_registry.py` 與 `tests/e2e/test_release_gate_registry.py` @ `40bb0246`，確認靜態 gate 與 dry-run metadata 保持 source-off default，本任務不簽發 lease、不 dispatch deploy；recommendation 改為 `verified_candidate`，`blocking_reasons` 清空。`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` A6 更正為 `met_by_test_in_green_ci`，引用 `infra/terraform/modules/ephemeral_staging/main.tf`、`tests/ops/test_ephemeral_staging_lifecycle.py` 與 `tests/ops/test_deploy_workflow_contract.py` @ `462c8cd4ff25`，確認 Terraform 與 lifecycle 實作 source-off 預設值與 default-deny egress；recommendation 改為 `verified_candidate`，`blocking_reasons` 清空。兩項任務之 `pending_reviewer_reading` 全部清除。

> **本段已被第 4 輪部分推翻（保留為歷史紀錄）**：第 3 輪把 `ODP-DEV-STAGED-GATE-RECONCILIATION-001` A7 改判為 `met_by_test_in_green_ci` 並引用該 PR 的兩個測試，第 4 輪逐行掃描該 PR 的完整 diff 後確認那兩個測試並未斷言 source-off／credentials／egress，該條款已改為 `partially_met`；A3–A6 也因該 head 的 `product` job 為 scope skip 而改為 `test_delivered_not_executed_at_exact_head`。`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` 的裁定維持，僅補正 A3 的 CI 執行綁定。詳見下一節。

### 第 4 輪評審意見與本輪更正

**[P1] DEV-STAGED-GATE 的 CI 與條款證據被錯誤升格：A3-A7 標 met_by_test_in_green_ci 並引用 tests/e2e/test_release_gate_registry.py 與 orchestrator @ 40bb0246，但 ci.yml:139 的 orchestrator 指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，不收集該檔；會收集它的 product job 在該 head 是 skipped，product-e2e-gate 亦 skipped。A7 新增的兩個具名測試也沒有斷言 sources disabled／provider credentials 缺席／default-deny egress。請更正 A3-A7 的證據種類與執行狀態，並檢查其餘同類 test_file→ci_check 配對。**

→ 接受，且按要求把修正推廣到全部同類配對。新增 decision_rule.ci_execution_binding：對 37 個 odayplus 候選的每個精確 head 唯讀讀回 ci.yml、pyproject testpaths、Makefile 與 run_product_e2e.sh，建立 job→實際收集路徑／固定清單對照，再逐條重算 114 條 met_by_test_in_green_ci 的綁定，結果全部列在 ci_execution_binding.audit_result.rows。共 23 條配對不成立，分三類更正：（一）真的沒被執行——DEV-STAGED-GATE A3-A6 與 EPHEMERAL-STAGING-IAC A2，改記新狀態 test_delivered_not_executed_at_exact_head；前者是 product job 於該 head 為 skipped，後者是 infra/ 根本不在 pyproject testpaths、也不被該 head 七個 workflow 中任何一支引用。（二）綁錯 job 但確有成功 check 執行——RELEASE-GATE-FIXTURE-STAGING-002 A1-A3、WEB-PASSWORD A1、NETPLAN A4、INT001-CDC A4、RUNTIME-RELEASE-STAGING-LIFECYCLE A3，改綁真正收集它的 job 並寫明依據（例如 product-e2e-gate 不做路徑式收集，只跑寫死的 playwright spec 清單與 PYTEST_NODE_IDS；vitest 由 product job 的 node-check step 執行）。（三）以綠色 job 泛稱代替條款證據——MEASUREMENT-CROSSLAYER A1-A4、MERGE-QUEUE-DISPOSITION A3-A4、REQ-DISPOSITION A1／A2／A4 的測試檔被誤標為 delivered_file，已正規化為 test_file 並補上收集依據；ROLE-PROVIDER A7 原本完全沒具名任何測試，已補上 .orchestrator/test_role_provider_policy.py 內逐項對應條款的 negative test 名稱，並因該條款的過程半（只跑 focused tests 一次）不可驗證而改為 partially_met。A7（DEV-STAGED-GATE）依要求改為 partially_met：逐行掃描該候選 PR 的完整 diff 確認它沒有交付任何涵蓋 source-off／credentials／egress 的程式或測試，改引用既有契約 .github/workflows/deploy-dev.yml @ efdfea1a0c31 的 pinned 行（140-141 external_sources_enabled 預設空值即 standing sources-off、955 ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled、1000 ODP_EXTERNAL_PROVIDER_MODE: disabled、948 ODP_CLOUD_RUN_VPC_EGRESS 取自 environment 變數），live posture 保留為未證明的缺口。另補查 marker 維度：4 個帶 requires_live_env 的測試檔被主 step 排除但由 product job 的資料庫 step 或具名 PostgreSQL step 跑回來，已補上 step 層綁定。新狀態不自動構成 blocked，但一律列入 recommendation_scope.criteria_not_fully_evidenced 並把該 ID 的 confidence 降為 medium。

**[P2] 中文 reviewer 映射指引未同步：README_ZH_TW.md 仍列四筆直接否證與兩筆 pending_reviewer_reading，並指示讀取 blocking_reasons[].readings，但該 head 的 JSON 已清除後三筆 blocking_reasons，摘要為 35 verified_candidate / 3 blocked / 0 pending。**

→ 已重寫「給 reviewer 的映射注意事項」：blocked 現在只有 DPF-EMGI-LIVE-ROLLOUT-001、ODP-DEV-ROLLOUT-001 與 ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 三筆，全部屬證據直接否證；pending_reviewer_reading 分類已不存在，指向 blocking_reasons[].readings 的指引一併移除。同時新增一條指引，說明本輪引入的 test_delivered_not_executed_at_exact_head 該怎麼讀，以及 README 標題、產出者、摘要統計與逐項明細全部依第 4 輪重算結果同步。
### 為什麼「PR 已合併」不等於「任務已完成」

本輪把判定單位從「整個 PR」下放到「逐條 acceptance」。3 筆 `blocked` 全部不是因為 CI 或 PR 有問題，而是因為**至少一條 acceptance 被該任務自己的收據否證，或屬 runtime／外部啟用／人類授權類而查無既有證據**：

- **`DPF-EMGI-LIVE-ROLLOUT-001`**
  - A5：該任務自身收據記載本條款未達成（收據自陳 missing rollback_receipt）
    - 條款原文：部署與 rollback receipts 綁定 digest
- **`ODP-DEV-ROLLOUT-001`**
  - A2：該任務自身收據記載本條款未達成（image digest 為 sha256:1111... 佔位值）
    - 條款原文：所有 components符合 release manifest digests
  - A3：條款屬 runtime／外部啟用／人類授權類，唯讀範圍內查無對應既有證據
    - 條款原文：dev integration/contract/provider-off readback通過
  - A5：該任務自身收據記載本條款未達成
    - 條款原文：receipts綁定 exact SHA與 manifest
- **`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`**
  - A3：條款含人類授權成分且只部分達成（GitHub 側保護有效，但 production GCP 基礎設施、變數 total_variables=0 與人類決定待補）
    - 條款原文：production environment存在且保護有效

## 判定規則

- **判定單位**：逐條 acceptance。每個 ID 的建議由其 acceptance_evidence_map 逐條狀態機械推導，不由 PR 或 CI 整體印象推導。

- **逐條狀態語彙**：

  - `met_by_receipt` — 交付物內既有收據直接記載本條款的量測結果
  - `met_by_test_in_green_ci` — 本 PR 交付了對應測試檔，且該檔位於精確 head 上某個 conclusion=success 的 CI job 實際收集的路徑內（綁定依 ci_execution_binding 的 job→收集路徑對照核對；僅到 job／step 層，未取 job log）
  - `test_delivered_not_executed_at_exact_head` — 本 PR 交付了對應測試檔並可定位到具名 assertion，但精確 head 上唯一會收集該檔的 CI job 為 skipped（或該路徑不在任何 CI job 的收集範圍內），因此沒有任何成功 check 執行過它
  - `met_by_delivered_artifact` — 交付了對應程式或文件，本盤點只驗其存在與內容涵蓋，未重算其結論
  - `partially_met` — 本條款可拆成數項，部分有既有證據、部分沒有
  - `process_constraint_unverifiable` — 本條款約束的是執行過程，無法由交付物與唯讀記錄獨立驗證
  - `not_evidenced` — 唯讀範圍內找不到對應本條款的既有證據
  - `unmet_per_own_receipt` — 該任務自身的收據直接記載本條款未達成

- **`blocked` 的充分條件**（任一成立）：

  - 任一條款狀態為 unmet_per_own_receipt；或
  - 任一 runtime／外部啟用／人類授權類（R／X／H）條款狀態為 not_evidenced；或
  - 任一含人類授權成分（H）的條款只 partially_met；或
  - 所採候選的 candidate_mapping verdict 非 consistent；或
  - 精確 head 有 failure 結論或 commit status rollup 非 success；或
  - 精確 head 無具名且非 owner 的 task-review-gate 核准。

- **`verified_candidate`**：以上皆不成立。此標籤只表示「可作為復原候選送 reviewer 判定」，不表示 acceptance 全數完成。

- **執行過程約束不構成 blocked**：process_constraint_unverifiable 不構成 blocked：這類條款約束的是工作怎麼做（起點 worktree、跑幾次 CI、PR 用什麼語言），不是 acceptance 的結果面。所有這類條款一律在 recommendation_scope 揭露。

- **CI 執行綁定（第 4 輪新增）**：`met_by_test_in_green_ci` 只在「被引用測試檔位於被引用 check 於該 head 實際收集的路徑／清單內，且該 check 結論為 success」時成立。綠色 job 不能替代未被它收集的測試。

  各候選 head 的 job → 實際收集範圍（唯讀讀回該 head 的 `ci.yml`、`pyproject.toml` testpaths、`Makefile` 與 `run_product_e2e.sh` 後建立；37 個 odayplus head 一致）：

  | CI job | 實際收集什麼 |
  |---|---|
  | `orchestrator` | `uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling` — 只收 `.orchestrator/`、`delivery_toolchain/`、`scripts/`、`tests/tooling/`，**不收 `tests/` 底下其他目錄** |
  | `product` | 主 step 收 `tests/`、`modules/`、`apps/`、`shared/`、`models/`（排除 `requires_live_env`／`performance`）；另有具名 PostgreSQL 16 step、資料庫 step（`requires_live_env` 的 `tests/contract`／`tests/ops`／`tests/integration` 由此跑回來）與 `make node-check`（npm workspace 的 vitest 測試）|
  | `performance-gate` | `-m performance tests/performance` |
  | `product-e2e-gate` | **不做路徑式收集**：只跑 `run_product_e2e.sh` 內寫死的 playwright spec 清單與 `PYTEST_NODE_IDS` 固定 node-id 清單；make 前置目標 `release-gate-registry` 另跑 checker 本身（那是 gate step 證據，不等於執行了同名的 pytest 檔）|
  | 任何 job 都不收 | `infra/`——不在 `pyproject.toml` testpaths，也不被候選 head 上任何一支 workflow 引用 |

  稽核結果：112 條受稽核，107 條確由某個結論 success 的 job／step 收集執行，5 條在精確 head 上未被任何成功 check 執行，未解者 0 條。逐條結果見 JSON 的 `decision_rule.ci_execution_binding.audit_result.rows`。

- **未執行不等於被否證**：test_delivered_not_executed_at_exact_head 不自動構成 blocked——它描述的是『這條 acceptance 的證明方式不成立』，而非『acceptance 被否證』。但每一條都會列入該 ID 的 recommendation_scope.criteria_not_fully_evidenced，且該 ID 的 confidence 一律降為 medium，請 reviewer 連同一併判定。

- **信心**：high = 無 skipped check、無 partially_met、無 process_constraint_unverifiable、無 test_delivered_not_executed_at_exact_head；blocked 案件則為證據直接否證；medium = 存在 skipped checks、partially_met、process_constraint_unverifiable 或 test_delivered_not_executed_at_exact_head 條款；blocked 案件則為其 blocking 條款尚待 reviewer 語意裁定。

## 證據來源與其證明範圍

| 來源 | 取得方式 | 能證明 | 不能證明 |
|---|---|---|---|
| orchestrator task brief | 本地唯讀 | 原始 title／owner／reviewer／acceptance／verification／artifacts／source documents | 不是遺失的 archive 記錄本身；`Status` 欄只反映最後一次生成當下的狀態 |
| 其他任務 brief 的 Dependencies | 本地唯讀 | 看板當時對本 ID 記載的狀態（終態旁證） | 不含核准細節或時間點 |
| PR body 的 ReviewBus 區塊 | 遠端唯讀 | 送審當時看板記載的 task id／狀態／負責人／評審人 | 不證明 acceptance 達成 |
| 精確 head 的 check-runs | 遠端唯讀 | 該 commit 上每個 check 的結論與起訖時間 | 只到 job 結論層；未取 job log |
| merge commit 上的 `.github/workflows/ci.yml` | 本地唯讀 | 某支測試在該 head 是真的被執行，還是被 marker／環境變數跳過 | 不證明該測試斷言了什麼 |
| 精確 head 上的 `ci.yml` + `pyproject.toml` testpaths + `Makefile` + `run_product_e2e.sh` | 本地唯讀 | 哪個 CI job／step 會收集哪些路徑，因此某支測試是否落在某個成功 check 的收集範圍內 | 不證明該測試在 job log 內的個別結果 |
| merge commit 上的收據檔案 | 本地／遠端唯讀 | 收據自身記載的量測值、digest、時間與 failures | 收據自我矛盾時只證明矛盾存在 |
| `task-review-gate` commit status | 遠端唯讀 | 綁定精確 head 的核准事實與核准者姓名 | 不證明 acceptance 全數達成 |
| 本地 git 歷史 | 本地唯讀 | head/merge 是否在 dev 歷史、commit trailers、merge commit 的檔案樹與內容 | 不證明 runtime 行為 |

## 已知限制

- 本地 ai-activity-log.jsonl 最早一筆記錄為 2026-09-06T11:01:40Z（事故之後）；38 個 ID 沒有任何一筆以其為 task_id 的歷史活動事件，因此無法從本地活動紀錄取得當時的 review／done 事件時間軸。
- ai-task-archive/tasks 僅存 6 筆（2 completed / 4 superseded），無一屬於本清單。
- CI 證據只到 check-run 結論與 job／step 定義層級。第 4 輪對全部 met_by_test_in_green_ci 條款重建了『被引用測試檔 → 實際收集它的 job／step』綁定（依各候選 head 的 ci.yml、pyproject testpaths、Makefile 與 run_product_e2e.sh 固定清單），但仍未取 job log，因此無法出示個別測試的 PASSED 行。綁定成立只代表該檔被某個結論 success 的 job 收集，不代表該檔內某一條具名 assertion 的個別結果已被查核。
- PR 合併與 CI 綠燈只證明程式與檢查通過，不能單獨證明 runtime 部署、外部來源啟用或人類核准已發生。
- 已定位到的收據以其自身記載為準。收據若自我矛盾（例如 DPF 的 live-rollout-receipt.json），本盤點採其中具體的量測欄位而非摘要旗標，並在該條款註記矛盾所在。
- acceptance 條款中屬「執行過程約束」者（起點 worktree、CI 執行次數、PR 語言、分工），本盤點一律標為 process_constraint_unverifiable 並不據以 blocked，但全部在 recommendation_scope 揭露。

## 候選誤配更正

### `XR-EXT-OSS-FINAL-AUDIT-001`

- 輸入清單候選：https://github.com/alfloop-dev/odayplus/pull/996（**誤配**）
  - head 分支為 `task/ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`；PR body ReviewBus 區塊宣告的 task id 為 `ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`，只是標題文字提及本任務。
  - 本任務宣告的三個 artifact 在該 merge commit 與目前 dev tip **皆不存在**。
  - 但 #996 仍屬本任務證據鏈：XR 的 `cross-repo-evidence-ledger.json` 把它 pin 為 consumer disposition 證據（PR #996 → head `b436a3fc` → merge `0dc5cebc`）。誤配的是「哪個 PR 是交付」，不是「#996 是否為證據」。
- 更正後候選：https://github.com/alfloop-dev/oday-data-platform/pull/61
  - repo `alfloop-dev/oday-data-platform`、分支 `task/XR-EXT-OSS-FINAL-AUDIT-001`、state `MERGED`、merged `2026-08-24T05:54:54Z`
  - head `b1824c979aca008da10aed01fbc0c0a269c581dc`、merge `7b0670d7b37e59e06bc9fea5b6003d1964be2c3c`
  - 精確 head 的 7 個 check 全部 `success`（2026-08-24T05:50:55Z – 2026-08-24T05:54:06Z）；`task-review-gate` 於 2026-08-24T05:54:45Z 記 Approved by assigned reviewer Codex，核准後 9 秒合併。
  - 三個宣告 artifact 均存在於該 repo 的 merge commit。
- 影響：本 ID 的 repository 欄在輸入清單標為 `alfloop-dev/odayplus` 亦屬誤判，實際為 `alfloop-dev/oday-data-platform`。

## 逐項結果

| # | 任務 ID | 候選 PR | 建議 | 信心 | acceptance 條款狀態 |
|---|---|---|---|---|---|
| 1 | `DPF-EMGI-LIVE-ROLLOUT-001` | [oday-data-platform#62](https://github.com/alfloop-dev/oday-data-platform/pull/62) | `blocked` | high | met_by_receipt×4、unmet_per_own_receipt×1 |
| 2 | `ODP-DEV-ROLLOUT-001` | [odayplus#1013](https://github.com/alfloop-dev/odayplus/pull/1013) | `blocked` | high | unmet_per_own_receipt×2、met_by_receipt×1、not_evidenced×1、partially_met×1 |
| 3 | `ODP-DEV-STAGED-GATE-RECONCILIATION-001` | [odayplus#1193](https://github.com/alfloop-dev/odayplus/pull/1193) | `verified_candidate` | medium | test_delivered_not_executed_at_exact_head×4、process_constraint_unverifiable×3、met_by_delivered_artifact×1、partially_met×1 |
| 4 | `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` | [odayplus#1011](https://github.com/alfloop-dev/odayplus/pull/1011) | `blocked` | high | met_by_receipt×3、partially_met×2 |
| 5 | `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` | [odayplus#1041](https://github.com/alfloop-dev/odayplus/pull/1041) | `verified_candidate` | medium | met_by_test_in_green_ci×7、partially_met×1、process_constraint_unverifiable×1 |
| 6 | `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001` | [odayplus#1208](https://github.com/alfloop-dev/odayplus/pull/1208) | `verified_candidate` | medium | met_by_test_in_green_ci×5、process_constraint_unverifiable×3、met_by_delivered_artifact×2 |
| 7 | `ODP-AVM-DEPRECIATION-CONTRACT-001` | [odayplus#1148](https://github.com/alfloop-dev/odayplus/pull/1148) | `verified_candidate` | high | met_by_test_in_green_ci×3、met_by_delivered_artifact×1 |
| 8 | `ODP-CANONICAL-LEGACY-LINEAGE-001` | [odayplus#1150](https://github.com/alfloop-dev/odayplus/pull/1150) | `verified_candidate` | high | met_by_delivered_artifact×4 |
| 9 | `ODP-DRIFT-DEP-REMOVE-002` | [odayplus#1222](https://github.com/alfloop-dev/odayplus/pull/1222) | `verified_candidate` | medium | met_by_test_in_green_ci×4、met_by_receipt×2、process_constraint_unverifiable×2、met_by_delivered_artifact×1 |
| 10 | `ODP-EPHEMERAL-STAGING-IAC-001` | [odayplus#1002](https://github.com/alfloop-dev/odayplus/pull/1002) | `verified_candidate` | medium | met_by_test_in_green_ci×2、partially_met×1、test_delivered_not_executed_at_exact_head×1、met_by_delivered_artifact×1 |
| 11 | `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001` | [odayplus#1135](https://github.com/alfloop-dev/odayplus/pull/1135) | `verified_candidate` | high | met_by_test_in_green_ci×5、met_by_receipt×1、met_by_delivered_artifact×1 |
| 12 | `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` | [odayplus#1170](https://github.com/alfloop-dev/odayplus/pull/1170) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 13 | `ODP-INT-MANUAL-CORRECTION-AUDIT-001` | [odayplus#1175](https://github.com/alfloop-dev/odayplus/pull/1175) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 14 | `ODP-INT001-CDC-DISPOSITION-001` | [odayplus#1166](https://github.com/alfloop-dev/odayplus/pull/1166) | `verified_candidate` | high | met_by_delivered_artifact×2、met_by_test_in_green_ci×2 |
| 15 | `ODP-JOB-PARTIAL-DISPOSITION-001` | [odayplus#1172](https://github.com/alfloop-dev/odayplus/pull/1172) | `verified_candidate` | high | met_by_test_in_green_ci×2、met_by_delivered_artifact×2 |
| 16 | `ODP-LH-PREDICTION-DRIFT-001` | [odayplus#1154](https://github.com/alfloop-dev/odayplus/pull/1154) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 17 | `ODP-LH003-BACKTEST-RELEASE-GATE-001` | [odayplus#1165](https://github.com/alfloop-dev/odayplus/pull/1165) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 18 | `ODP-MEASUREMENT-CROSSLAYER-GATE-001` | [odayplus#1153](https://github.com/alfloop-dev/odayplus/pull/1153) | `verified_candidate` | medium | met_by_test_in_green_ci×4 |
| 19 | `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` | [odayplus#1169](https://github.com/alfloop-dev/odayplus/pull/1169) | `verified_candidate` | high | met_by_delivered_artifact×2、met_by_test_in_green_ci×2 |
| 20 | `ODP-MODELREADY-QUALITY-NULLABLE-001` | [odayplus#1168](https://github.com/alfloop-dev/odayplus/pull/1168) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 21 | `ODP-NET002-LEASE-DISPOSITION-001` | [odayplus#1187](https://github.com/alfloop-dev/odayplus/pull/1187) | `verified_candidate` | high | met_by_test_in_green_ci×3、met_by_delivered_artifact×1 |
| 22 | `ODP-NETPLAN-DISCLOSURE-UI-E2E-001` | [odayplus#1174](https://github.com/alfloop-dev/odayplus/pull/1174) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 23 | `ODP-OPS002-DECISION-COMMENTS-001` | [odayplus#1158](https://github.com/alfloop-dev/odayplus/pull/1158) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 24 | `ODP-PRICE006-BANDIT-GATED-001` | [odayplus#1179](https://github.com/alfloop-dev/odayplus/pull/1179) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 25 | `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` | [odayplus#1109](https://github.com/alfloop-dev/odayplus/pull/1109) | `verified_candidate` | high | met_by_receipt×1 |
| 26 | `ODP-RELEASE-GATE-FIXTURE-STAGING-002` | [odayplus#1212](https://github.com/alfloop-dev/odayplus/pull/1212) | `verified_candidate` | medium | met_by_test_in_green_ci×3、process_constraint_unverifiable×2、met_by_delivered_artifact×1 |
| 27 | `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001` | [odayplus#1030](https://github.com/alfloop-dev/odayplus/pull/1030) | `verified_candidate` | high | met_by_receipt×1 |
| 28 | `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001` | [odayplus#1050](https://github.com/alfloop-dev/odayplus/pull/1050) | `verified_candidate` | high | met_by_test_in_green_ci×6、met_by_delivered_artifact×1 |
| 29 | `ODP-REQ-DISPOSITION-GOVERNANCE-001` | [odayplus#1146](https://github.com/alfloop-dev/odayplus/pull/1146) | `verified_candidate` | high | met_by_test_in_green_ci×3、met_by_delivered_artifact×1 |
| 30 | `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` | [odayplus#1221](https://github.com/alfloop-dev/odayplus/pull/1221) | `verified_candidate` | medium | met_by_delivered_artifact×4、process_constraint_unverifiable×3、partially_met×1 |
| 31 | `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001` | [odayplus#1206](https://github.com/alfloop-dev/odayplus/pull/1206) | `verified_candidate` | medium | met_by_test_in_green_ci×7、process_constraint_unverifiable×3、met_by_delivered_artifact×1 |
| 32 | `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` | [odayplus#1010](https://github.com/alfloop-dev/odayplus/pull/1010) | `verified_candidate` | medium | met_by_test_in_green_ci×4、partially_met×1 |
| 33 | `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` | [odayplus#1160](https://github.com/alfloop-dev/odayplus/pull/1160) | `verified_candidate` | high | met_by_test_in_green_ci×2、met_by_delivered_artifact×2 |
| 34 | `ODP-SITESCORE-QUALITY-NULLABLE-001` | [odayplus#1171](https://github.com/alfloop-dev/odayplus/pull/1171) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 35 | `ODP-SPEC-SOURCE-PROVENANCE-001` | [odayplus#1147](https://github.com/alfloop-dev/odayplus/pull/1147) | `verified_candidate` | high | met_by_delivered_artifact×4 |
| 36 | `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001` | [odayplus#1164](https://github.com/alfloop-dev/odayplus/pull/1164) | `verified_candidate` | high | met_by_test_in_green_ci×4 |
| 37 | `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` | [odayplus#1096](https://github.com/alfloop-dev/odayplus/pull/1096) | `verified_candidate` | medium | met_by_test_in_green_ci×3、partially_met×1、met_by_delivered_artifact×1、process_constraint_unverifiable×1 |
| 38 | `XR-EXT-OSS-FINAL-AUDIT-001` | [oday-data-platform#61](https://github.com/alfloop-dev/oday-data-platform/pull/61)（更正後） | `verified_candidate` | high | met_by_receipt×5 |


## 逐項明細

每個 ID 依序列出：原任務定義來源與 hash、候選映射、精確 head 的 CI 與核准、**逐條 acceptance 的證據對應**，以及缺口與建議。

### `DPF-EMGI-LIVE-ROLLOUT-001` — blocked（信心 high）

- 倉庫：`alfloop-dev/oday-data-platform`
- 候選 PR：[#62](https://github.com/alfloop-dev/oday-data-platform/pull/62) — DPF-EMGI-LIVE-ROLLOUT-001: exact-digest publish 與 EMGI sources-off runtime，state `MERGED`，merged `2026-08-25T14:24:47Z`
- 精確 head：`71ecbe0d982f3f93c976fa0104a82903d2a071cf`；merge：`e3ecd2f199fe051aba8d3d33005c217036a9c88e`
- 候選映射：`consistent`
  - PR body task id：`DPF-EMGI-LIVE-ROLLOUT-001`（來自 finalize_header）
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-25T14:24:40Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/dpf_emgi_live_rollout_001.md`（生成於 2026-08-25T14:20:46Z）
  - brief 宣告 sha256 `4f066c78136597fb93629563c350ebc5567111921b85b790f2e50fa55e1c8770`；重算檔案 sha256 `151d7d108c94579cd5b2ba01401eb608344f999d16ce6ba911853fd6b5074f67`
  - 原 owner `Codex`／reviewer `Codex2`／brief 當下狀態 `review`
  - 其他任務 brief 記載的終態旁證：done（4 筆引用）

**逐條 acceptance 證據對應**

- **A1**（runtime／部署）— `met_by_receipt`
  - 條款：build/publish immutable digest 且產生 SBOM/簽章
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/rollout-binding.json @ e3ecd2f1` — image_publish_receipt：candidate_sha 571fd34e、image_digest sha256:4f603e3a…、SBOM spdx-json sha256 ac299b5f…、cosign keyless signature verified=true、attestation spdxjson verified=true；created_by_workflow run 32854804252
  - 註：SBOM 與簽章皆有具名 digest 與 verified 旗標，非僅宣告。
- **A2**（runtime／部署）— `met_by_receipt`
  - 條款：bootstrap EMGI environment與必要 namespace/RBAC/WI/secret references
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/environment-bootstrap-receipt.json @ e3ecd2f1` — observed_at 2026-08-24T14:10:00Z；namespace oday-emgi 與 oday-emgi-verify、bootstrap 只重用既有 namespace/RBAC/service-account manifests、Workload Identity KSA→GSA 且 exportable_key_material=false、secret 允許鍵只有 mongodb-uri 與 postgres-password、values_recorded_here=false、failures=[]
  - 註：secret 只記 reference 不記值，符合條款。
- **A3**（外部來源啟用狀態）— `met_by_receipt`
  - 條款：16 sources false且 receipts 空
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/sources-off-readback.json @ e3ecd2f1` — observed_at 2026-08-24T14:10:00Z；governed_source_count=16、declared_source_count=16、disabled_source_count=16、empty_approval_receipt_count=16、policy_enforcement_enabled=true、failures=[]；逐 source 記 enabled_env 與 approval_receipt_env
  - 註：此 16 項是 data platform 的第三方 provider 清單（cwa/tdx/osm…），與 ODP-DEV-ROLLOUT-001 的 16 項不是同一份。
- **A4**（runtime／部署）— `met_by_receipt`
  - 條款：default-deny public egress
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/egress-posture-audit.json @ e3ecd2f1` — public_egress_default=DENY、live_spec_asserted_against_cluster=true、reconciled_by_every_apply=true；兩個 NetworkPolicy（oday-emgi 與 oday-emgi-verify）各有 manifest_sha256 與 default_deny_public_egress=true；failures=[]
  - 註：收據明記讀的是 live NetworkPolicy spec 而非只確認 policy 存在。
- **A5**（runtime／部署）— `unmet_per_own_receipt`
  - 條款：部署與 rollback receipts 綁定 digest
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/live-rollout-receipt.json @ e3ecd2f1` — digest_binding.binding_state=BOUND、bound_receipts=[deploy_receipt, image_publish_receipt]、missing_receipts=["rollback_receipt"]
  - 證據（receipt_file）：`docs/evidence/runtime/DPF-EMGI-LIVE-ROLLOUT-001/rollout-binding.json @ e3ecd2f1` — rollback_receipt = null
  - 註：條款要求「部署與 rollback receipts 綁定 digest」；部署那一半有收據，rollback 那一半沒有。同一份 live-rollout-receipt.json 的 acceptance[4].bound_to_digest=true 與其 digest_binding.missing_receipts 自相矛盾，本盤點採 binding 區塊的具體量測而非摘要旗標。

**blocking 條款**

- A5：該任務自身收據記載本條款未達成

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A5(unmet_per_own_receipt)

**查證發現**

- 跨 repo：候選 PR 在 alfloop-dev/oday-data-platform，merge commit 不在 odayplus 本地歷史，本輪以 GitHub contents API 於精確 merge e3ecd2f1 唯讀讀回五份收據內容。
- 四項 acceptance 有實質收據支撐：image publish（digest sha256:4f603e3a…、SBOM spdx-json sha256 ac299b5f…、cosign keyless 簽章與 attestation 皆 verified）、environment bootstrap（Workload Identity 且 exportable_key_material=false、secret 只記 reference、failures=[]）、sources-off readback（16/16 disabled、16/16 approval receipt 為空）、egress posture（public_egress_default=DENY，且明記讀的是 live NetworkPolicy spec 而非只確認 policy 存在）。
- 第五項 acceptance『部署與 rollback receipts 綁定 digest』未達成：rollout-binding.json 的 rollback_receipt 為 null，live-rollout-receipt.json 的 digest_binding.missing_receipts=["rollback_receipt"]。部署那一半有收據，rollback 那一半沒有。
- 同一份 live-rollout-receipt.json 內部自相矛盾：其 acceptance[4].bound_to_digest=true，與同檔 digest_binding.missing_receipts 直接衝突。本盤點採具體的 binding 量測欄位，不採摘要旗標。
- 另記一項證據分歧：PR body 敘述 oday-emgi-webserver 與 daemon 為 revision 2，而 committed 的 deploy_receipt 記 revision 4、previous_revision 3。本盤點不裁定何者為當前狀態，僅記錄兩者不一致。

**缺口**

- A5（unmet_per_own_receipt）：條款要求「部署與 rollback receipts 綁定 digest」；部署那一半有收據，rollback 那一半沒有。同一份 live-rollout-receipt.json 的 acceptance[4].bound_to_digest=true 與其 digest_binding.missing_receipts 自相矛盾，本盤點採 binding 區塊的具體量測而非摘要旗標。
- 盤點觀察：該任務自身的 live-rollout-receipt.json 明列 missing_receipts=["rollback_receipt"]，與 acceptance『部署與 rollback receipts 綁定 digest』直接衝突。

**下一步最小驗證動作**

- 唯讀取回綁定 image digest sha256:4f603e3acff7a35876fd59593e6725ee0b00226e546b0694eb5e80a12b097e9e 的 rollback receipt；若確認不存在，本項維持 blocked 且不得以部署成功推定 rollback 已驗證。

### `ODP-DEV-ROLLOUT-001` — blocked（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1013](https://github.com/alfloop-dev/odayplus/pull/1013) — [ReviewBus] ODP-DEV-ROLLOUT-001 以同一 release digests 部署資料平台與 ODay Plus dev，state `MERGED`，merged `2026-08-25T17:31:52Z`
- 精確 head：`83944bb50c56a5071c992edb28e96eae3155f4c0`；merge：`b8262d911c95887767877e7cee23bded0ef7dd61`
- 候選映射：`consistent`
  - PR body task id：`ODP-DEV-ROLLOUT-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity2`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-25T17:06:40Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_dev_rollout_001.md`（生成於 2026-08-25T17:32:53Z）
  - brief 宣告 sha256 `80edb285c2b456f1e949c285b5cb55d6216f28eb05c65d31d2a54cea6b35243c`；重算檔案 sha256 `39ad59a8ca66dec32ef05894567e11054e89c78f70c8a7c0d0810a62cbe79aea`
  - 原 owner `Antigravity2`／reviewer `Codex`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（runtime／部署）— `met_by_receipt`
  - 條款：data platform 先於 ODay Plus
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/dev-rollout-manifest-binding.json @ b8262d911c95` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`dev-rollout-manifest-binding.json deployment_order` — sequence 1 = data_platform，sequence 2 = oday_plus，順序符合條款
- **A2**（runtime／部署）— `unmet_per_own_receipt`
  - 條款：所有 components符合 release manifest digests
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/dev-rollout-manifest-binding.json @ b8262d911c95` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`dev-rollout-manifest-binding.json components_digest_validation / digests_integrity` — 六個 component 的 manifest_image 與 deployed_image 皆為佔位值 sha256:1111…、2222…、3333…、4444…、5555…、6666…，digests_integrity 為 aaaa…／bbbb…／cccc…，收據卻記 digest_match=true 與 all_digests_match=true
  - 註：條款要求「所有 components 符合 release manifest digests」。收據比對的兩邊都是同一組佔位字串，因此 digest_match=true 不構成真實 digest 一致性證據。
- **A3**（runtime／部署）— `not_evidenced`
  - 條款：dev integration/contract/provider-off readback通過
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/dev-integration-readback.json @ b8262d911c95` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`dev-integration-readback.json reports.*.path` — readback_status=PASSED，但五份被引用的報告（.odp_data/deployment/cloud-run-preflight.json、cloud-run-smoke.json、cloud-run-migration-compatibility.json、live-e2e-gate.json 與 jobs_validation）未隨 PR 提交，倉庫內不存在
  - 註：PASSED 是摘要旗標，其依據的原始報告不在可查範圍，無法獨立核對。
- **A4**（外部來源啟用狀態）— `partially_met`
  - 條款：16 sources disabled且無 credentials/egress
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/external-sources-provider-off-audit.json @ b8262d911c95` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`external-sources-provider-off-audit.json sources_inventory` — 16 項為 ODayPlus canonical snapshot 家族：store_master_snapshot、machine_master_snapshot、machine_cycle_event、machine_status_event、transaction_event、price_schedule_snapshot、maintenance_work_order_event、customer_service_case_event、poi_snapshot、geocode_result_snapshot、admin_boundary_snapshot、listing_raw_snapshot、competitor_store_snapshot、demographics_snapshot、weather_daily_snapshot、store_opening_authority_snapshot；all_sources_disabled=true、zero_credentials_present=true、default_deny_egress_enforced=true
  - 註：此 16 項與 data platform 側的 16 個第三方 provider（cwa、tdx、market_events、mof_business、osm、moi_rental、ris_population、nlsc_boundary、overture、foursquare、brand_locators、tgos、google_places_verifier、listings、mobility、survey）是不同母體。兩份收據都寫「16 sources disabled」不代表驗的是同一份清單。
- **A5**（runtime／部署）— `unmet_per_own_receipt`
  - 條款：receipts綁定 exact SHA與 manifest
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-DEV-ROLLOUT-001/release-receipts-index.json @ b8262d911c95` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`release-receipts-index.json / dev-rollout-manifest-binding.json` — receipts 綁定的 candidate_sha e496be62 與 manifest_digest sha256:23a6d45a 為具體值，但其所綁的 component image digest 全為佔位值（見 A2）
  - 註：SHA 與 manifest 綁定成立，image digest 綁定不成立，故條款整體未達成。

**blocking 條款**

- A2：該任務自身收據記載本條款未達成
- A3：條款屬 runtime／外部啟用／人類授權類，唯讀範圍內查無對應既有證據
- A5：該任務自身收據記載本條款未達成

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A2(unmet_per_own_receipt)、A3(not_evidenced)、A4(partially_met)、A5(unmet_per_own_receipt)

**查證發現**

- 本次獨立複驗確認 docs/evidence/runtime/ODP-DEV-ROLLOUT-001/odayplus-dev-deployment.json 的五個 image digest 皆為 sha256:1111…/2222…/4444…/5555…/6666… 重複位元佔位值，卻同時標記 status=READY / SUCCEEDED。
- 同檔 candidate_sha e496be62c47c45d758681b8a4d3abfae16f1c96d 確實在 origin/dev 歷史中，因此問題不在 candidate 不存在，而在 component digest 造假。
- 同期真實 manifest（ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001）使用 asia-east1-docker.pkg.dev/… 的真實 digest，與本收據的 ghcr.io 佔位值不同源。
- 本輪補充：dev-rollout-manifest-binding.json 的 components_digest_validation 六個 component 兩邊都是同一組佔位字串（sha256:1111…／2222…／3333…／4444…／5555…／6666…），digests_integrity 為 aaaa…／bbbb…／cccc…，收據卻記 digest_match=true 與 all_digests_match=true。比對的兩邊都是佔位值，因此 true 不構成 digest 一致性證據。
- dev-integration-readback.json 記 readback_status=PASSED，但其引用的五份報告位於 .odp_data/deployment/（cloud-run-preflight.json、cloud-run-smoke.json、cloud-run-migration-compatibility.json、live-e2e-gate.json 與 jobs_validation），未隨 PR 提交，倉庫內不存在，無法獨立核對。
- external-sources-provider-off-audit.json 的 16 項是 ODayPlus canonical snapshot 家族（store_master_snapshot、machine_master_snapshot、…、store_opening_authority_snapshot），與 data platform 側 XR／DPF 收據所稽核的 16 個第三方 provider（cwa、tdx、market_events、mof_business、osm、moi_rental、ris_population、nlsc_boundary、overture、foursquare、brand_locators、tgos、google_places_verifier、listings、mobility、survey）不是同一份清單。兩份收據都寫『16 sources disabled』並不代表驗的是同一個母體——這是本輪新發現，reviewer 不應把兩者互相當作佐證。

**缺口**

- A2（unmet_per_own_receipt）：條款要求「所有 components 符合 release manifest digests」。收據比對的兩邊都是同一組佔位字串，因此 digest_match=true 不構成真實 digest 一致性證據。
- A3（not_evidenced）：PASSED 是摘要旗標，其依據的原始報告不在可查範圍，無法獨立核對。
- A4（partially_met）：此 16 項與 data platform 側的 16 個第三方 provider（cwa、tdx、market_events、mof_business、osm、moi_rental、ris_population、nlsc_boundary、overture、foursquare、brand_locators、tgos、google_places_verifier、listings、mobility、survey）是不同母體。兩份收據都寫「16 sources disabled」不代表驗的是同一份清單。
- A5（unmet_per_own_receipt）：SHA 與 manifest 綁定成立，image digest 綁定不成立，故條款整體未達成。

**下一步最小驗證動作**

- 唯讀取回 Cloud Run 服務 oday-plus-dev-api / -web 與 job -migration / -worker / -scheduler 的實際 revision image digest；與 RELEASE_MANIFEST.json 對帳。若取不到，記為『部署未經證實』，不得回填 done。

### `ODP-DEV-STAGED-GATE-RECONCILIATION-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1193](https://github.com/alfloop-dev/odayplus/pull/1193) — [ReviewBus] ODP-DEV-STAGED-GATE-RECONCILIATION-001 重整 staged dev release gate 與現行 artifact 的 exact reconciliat，state `MERGED`，merged `2026-09-04T09:47:46Z`
- 精確 head：`40bb02462ec742cfb615214c1873f15a40b2c602`；merge：`efdfea1a0c31c3c9ebcb2acfe1eb61dc4fa2bff6`
- 候選映射：`consistent`
  - PR body task id：`ODP-DEV-STAGED-GATE-RECONCILIATION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity5`／評審人 `Claude2`
- 精確 head CI：skipped=3、success=4；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T09:20:32Z — Approved by assigned reviewer Claude2
- 原任務定義來源：`.orchestrator/task-briefs/odp_dev_staged_gate_reconciliation_001.md`（生成於 2026-09-04T09:48:15Z）
  - brief 宣告 sha256 `c8758eab7f1b4e4144c17c96c7a13512dab0a48c4281e9506654d4ccbaa5e4ec`；重算檔案 sha256 `16e5e241fa39511b00c68e244936c4d49217fbb518db272aa71a8dd63e058149`
  - 原 owner `Antigravity5`／reviewer `Claude2`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：等待 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 合併後才選定最新 origin/dev candidate
  - 證據：無
  - 註：依賴任務的合併時序屬執行過程，交付物無法獨立驗證。
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：只使用既有 STAGE_CONTRACT 與 Runtime Release 單一路徑
  - 證據（delivered_file）：`delivery_toolchain/e2e/check_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit
  - 註：沿用既有 checker，未新增第二條路徑。
- **A3**（可由測試證明）— `test_delivered_not_executed_at_exact_head`
  - 條款：candidate-built dev boundary 僅接受有同 SHA 同 manifest digest 真實 receipt 的 cleared gate
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit；本 PR 於此檔新增兩個測試：test_go_decision_with_no_gates_bound_to_admission_target_is_rejected 與 test_blocked_gates_with_unmatched_target_fails_closed_under_require_go（+92 行，唯讀 diff 核對）。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → 精確 head 40bb02462ec7 的 CI job 對照` — 該 head 的 ci.yml 只有 product job 會收集 tests/ 路徑，而 product 的 conclusion=skipped（scope skip）；product-e2e-gate 同為 skipped，且其兩份固定清單（playwright spec、PYTEST_NODE_IDS）都不含本檔；orchestrator job 只收集 .orchestrator／delivery_toolchain／scripts／tests/tooling，結構上不會收集本檔。因此本檔在精確 head 上未被任何成功 check 執行。前輪把它綁到 orchestrator check 是錯誤的。
  - 註：本條款（同 SHA 同 manifest digest 的 cleared gate）由本 PR 新增的 test_go_decision_with_no_gates_bound_to_admission_target_is_rejected 與既有 registry 測試界定，但該測試檔在精確 head 上未被任何成功 check 執行。條款本身未被否證，缺的是「在此 head 跑過」這件事的證據。
- **A4**（可由測試證明）— `test_delivered_not_executed_at_exact_head`
  - 條款：staging 或 production boundary gate 不得反向阻擋 dev
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit；本 PR 於此檔新增兩個測試：test_go_decision_with_no_gates_bound_to_admission_target_is_rejected 與 test_blocked_gates_with_unmatched_target_fails_closed_under_require_go（+92 行，唯讀 diff 核對）。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → 精確 head 40bb02462ec7 的 CI job 對照` — 該 head 的 ci.yml 只有 product job 會收集 tests/ 路徑，而 product 的 conclusion=skipped（scope skip）；product-e2e-gate 同為 skipped，且其兩份固定清單（playwright spec、PYTEST_NODE_IDS）都不含本檔；orchestrator job 只收集 .orchestrator／delivery_toolchain／scripts／tests/tooling，結構上不會收集本檔。因此本檔在精確 head 上未被任何成功 check 執行。前輪把它綁到 orchestrator check 是錯誤的。
  - 註：本條款（staging／production gate 不得反向阻擋 dev）對應 test_blocked_gates_with_unmatched_target_fails_closed_under_require_go 與既有 test_blocking_gates_filters_by_target，同樣未在精確 head 被執行。
- **A5**（可由測試證明）— `test_delivered_not_executed_at_exact_head`
  - 條款：無證據的 gate 保持 blocked
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit；本 PR 於此檔新增兩個測試：test_go_decision_with_no_gates_bound_to_admission_target_is_rejected 與 test_blocked_gates_with_unmatched_target_fails_closed_under_require_go（+92 行，唯讀 diff 核對）。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → 精確 head 40bb02462ec7 的 CI job 對照` — 該 head 的 ci.yml 只有 product job 會收集 tests/ 路徑，而 product 的 conclusion=skipped（scope skip）；product-e2e-gate 同為 skipped，且其兩份固定清單（playwright spec、PYTEST_NODE_IDS）都不含本檔；orchestrator job 只收集 .orchestrator／delivery_toolchain／scripts／tests/tooling，結構上不會收集本檔。因此本檔在精確 head 上未被任何成功 check 執行。前輪把它綁到 orchestrator check 是錯誤的。
  - 註：本條款（無證據的 gate 保持 blocked）對應該檔既有 fail-closed 測試，同樣未在精確 head 被執行。
- **A6**（可由測試證明／人類授權）— `test_delivered_not_executed_at_exact_head`
  - 條款：release decision go 必須保留真實 human_signoff 且不得偽造
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit；本 PR 於此檔新增兩個測試：test_go_decision_with_no_gates_bound_to_admission_target_is_rejected 與 test_blocked_gates_with_unmatched_target_fails_closed_under_require_go（+92 行，唯讀 diff 核對）。
  - 證據（delivered_file）：`delivery_toolchain/e2e/check_release_gate_registry.py @ efdfea1a0c31` — 由本 PR 交付並存在於 merge commit；GO 決策的 human_signoff 驗證在此檢查器內。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → 精確 head 40bb02462ec7 的 CI job 對照` — 該 head 的 ci.yml 只有 product job 會收集 tests/ 路徑，而 product 的 conclusion=skipped（scope skip）；product-e2e-gate 同為 skipped，且其兩份固定清單（playwright spec、PYTEST_NODE_IDS）都不含本檔；orchestrator job 只收集 .orchestrator／delivery_toolchain／scripts／tests/tooling，結構上不會收集本檔。因此本檔在精確 head 上未被任何成功 check 執行。前輪把它綁到 orchestrator check 是錯誤的。
  - 註：本條款是對 release GO 的否定約束（必須是真實 human_signoff，不得偽造），不是要求本任務取得人類簽署。程式面由 checker 交付；其 E2E 測試檔在精確 head 上未被任何成功 check 執行，故本條款的測試證明不成立，只剩 checker 交付本身。本盤點未查到任何由本任務簽發的 release GO。若 reviewer 認為本條款要求出示一筆真實 human_signoff，則此項應改判為缺證據。
- **A7**（程式或文件交付／runtime／部署）— `partially_met`
  - 條款：第三方來源保持 disabled 且無 credentials 與 default deny egress
  - 證據（candidate_diff_scan）：`PR #1193 完整 diff @ 40bb02462ec7（2 檔、+129/-6）` — 逐行唯讀掃描本候選 PR 的完整 diff：delivery_toolchain/e2e/check_release_gate_registry.py 與 tests/e2e/test_release_gate_registry.py 皆無 sources／egress／credential／disabled／deny 任一字樣；新增的兩個測試只斷言 admission target 綁定與 require-go fail-closed。本候選 PR 自身沒有交付任何涵蓋本條款的程式或測試。前輪引用這兩個測試作為本條款證據是錯誤的。
  - 證據（delivered_file）：`.github/workflows/deploy-dev.yml @ efdfea1a0c31` — 既有契約（非本 PR 交付，但為本 task brief 列出的 source document）：第 140-141 行 workflow_dispatch 輸入 external_sources_enabled 預設空值，其說明明載空值即 standing sources-off posture；第 955 行 ODP_COMPETITOR_MANUAL_SOURCE_STATUS: disabled 與第 1000 行 ODP_EXTERNAL_PROVIDER_MODE: disabled 為寫死預設；第 948 行 ODP_CLOUD_RUN_VPC_EGRESS 取自 environment 變數 vars.ODP_CLOUD_RUN_VPC_EGRESS。
  - 註：拆成兩半判定。契約半：deploy-dev.yml 既有契約把第三方來源預設關閉、egress 交由 environment 變數決定，這份契約在候選 merge commit 上可 pinned 引用，但不是本候選 PR 的交付物。live posture 半：來源是否確實 disabled、有無投影 credentials、egress 是否 default deny，屬 runtime 類，唯讀範圍內查無任何對應此候選的 runtime receipt，且 vars.ODP_CLOUD_RUN_VPC_EGRESS 的實際值不在唯讀範圍內。依原 task 明文『本 task 不簽 lease 不 dispatch deploy』，live posture 屬後續 rollout scope，本盤點據此記為 partially_met：不宣稱 live 已證明，也不把後續 rollout 的缺口倒灌成此 code/gate 任務未完成。　本輪同時把本條款的 classes 由 [T,D] 更正為 [D,R]：條款要求的是 live 來源／credentials／egress 的狀態，屬 runtime 類，不是可由本 PR 測試證明的類別。此更正只影響描述準確性，不改變 blocked 判定——decision_rule 中會 blocked 的是「R／X／H 類條款為 not_evidenced」，本條款為 partially_met，在更正前後都不觸發 blocked；更正後反而更明確地把它列入 criteria_not_fully_evidenced。
- **A8**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：本 task 不簽 lease 不 dispatch deploy
  - 證據：無
  - 註：「不簽 lease、不 dispatch deploy」是否定要求；本盤點未查到本任務簽發 lease 或 dispatch 的紀錄。
- **A9**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：PR 文件中文並只跑 focused regression 與一次 PR CI
  - 證據：無
  - 註：PR 內文為中文可觀察；CI 執行次數本盤點未核。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A1(process_constraint_unverifiable)、A3(test_delivered_not_executed_at_exact_head)、A4(test_delivered_not_executed_at_exact_head)、A5(test_delivered_not_executed_at_exact_head)、A6(test_delivered_not_executed_at_exact_head)、A7(partially_met)、A8(process_constraint_unverifiable)、A9(process_constraint_unverifiable)
- CI 執行綁定：本 ID 有 4 條 acceptance 的測試檔已交付但在精確 head 上未被任何成功 check 執行（詳見各條 evidence 的 ci_execution_binding）。這不是條款被否證，而是『在此 head 跑過』這件事沒有證據。

**查證發現**

- Acceptance 明文『本 task 不簽 lease 不 dispatch deploy』，屬純 gate/registry 程式交付；六個宣告 artifact 在 merge commit 全數存在。

**缺口**

- 第 4 輪更正：A3-A6 的唯一測試檔 tests/e2e/test_release_gate_registry.py 在精確 head 40bb0246 上未被任何成功 check 執行。該 head 的 product job（tests/ 的唯一收集者）conclusion=skipped，product-e2e-gate 亦 skipped 且其固定清單不含本檔，orchestrator job 結構上不收集 tests/e2e/。前輪把這四條綁到 orchestrator check 屬錯誤綁定。
- 第 4 輪更正：A7（第三方來源保持 disabled、無 credentials、default deny egress）在本候選 PR 的完整 diff 內查無任何對應程式或測試——該 PR 只有 2 檔 +129/-6，且無 sources／egress／credential 字樣。既有契約 deploy-dev.yml 可 pinned 引用，但 live posture（實際 disabled 狀態、credentials 投影、egress 是否 default deny）無既有 runtime receipt。此為後續 rollout scope 的缺口，不倒灌成本 code/gate 任務未完成。
- 盤點觀察：Acceptance 第一條『等待 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 合併後才選定 candidate』的時序未在本盤點驗證。

**下一步最小驗證動作**

- 若要把 A3-A6 由『未執行』升為『已驗證』，最小動作是在 merge commit efdfea1a0c31 上單獨執行 tests/e2e/test_release_gate_registry.py 並保留退出碼收據；本盤點依範圍限制不執行測試。
- A7 需要一筆與該候選對應、記載來源 disabled／無 credentials／default-deny egress 的既有 runtime receipt；若不存在，應由後續 staging／dev rollout 任務產生，不由本盤點補造。
- 比對 ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001 的 merge 時間是否早於本 PR head。

### `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` — blocked（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1011](https://github.com/alfloop-dev/odayplus/pull/1011) — [ReviewBus] ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 建立 staging/production GitHub 與 GCP 環境保護，state `MERGED`，merged `2026-08-25T16:26:32Z`
- 精確 head：`5edcf009640ae31dde160b1ce4c9123ac2c31a2f`；merge：`8ad3f10dab707711bc1394ff3f6a81042b0b5648`
- 候選映射：`consistent`
  - PR body task id：`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-25T16:00:33Z — Approved by assigned reviewer Antigravity2
- 原任務定義來源：`.orchestrator/task-briefs/odp_github_gcp_env_bootstrap_001.md`（生成於 2026-08-25T16:42:37Z）
  - brief 宣告 sha256 `692bd98c346b953a220b144196f04517f2a30d5bcd16c5dca821ff581115dbcd`；重算檔案 sha256 `d782728205d4469bbe82bedb1a6ca56e24b6b87820a619c570e98448173f285f`
  - 原 owner `Claude2`／reviewer `Antigravity2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（2 筆引用）

**逐條 acceptance 證據對應**

- **A1**（runtime／部署）— `met_by_receipt`
  - 條款：staging/production environments具 required reviewers
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-environments-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`github-environments-audit.json environments[]` — audited_at 2026-08-25T15:44:00Z；staging（id 17295059155，created 2026-06-26T22:58:23Z）與 production（id 20574639394，created 2026-08-25T15:41:19Z）各有 type=required_reviewers 的 protection rule，具名 reviewer 為 Alien-alfaloop 與 ajoe734；dev 無 protection rule
  - 註：條款只要求 staging/production 具 required reviewers，收據以 GitHub API 讀回的 environment 物件證明兩者皆有。
- **A2**（runtime／部署）— `partially_met`
  - 條款：WIF/IAM/vars/secret references 完整且只有 references 被記錄
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/gcp-wif-iam-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-variables-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`gcp-wif-iam-audit.json` — audited_at 2026-08-25T15:44:00Z；workload_identity_pool state=ACTIVE、provider state=ACTIVE 且 attributeCondition 為 assertion.repository == 'alfloop-dev/odayplus'、deployer SA github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com 附 IAM policy、artifact_registry_repositories 1 筆、secret_manager_secret_references 4 筆、secret_values_redacted=true
  - 證據（receipt_field）：`github-variables-audit.json environments` — dev 38 個變數、staging 7 個變數皆記 name/updatedAt/value；production total_variables=0，status=pending_human_authority
  - 註：WIF/IAM 與 dev、staging 變數的 reference 完整且只記 reference；production 側的變數與 IAM 尚未存在，故此條款只對 dev/staging 成立。
- **A3**（runtime／部署／人類授權）— `partially_met`
  - 條款：production environment存在且保護有效
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-environments-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/production-authority-prerequisites.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`production-authority-prerequisites.json` — status=blocked_pending_human_authority；human_authority_checklist 五項 PROD-GCP-01（production GCP Project／region／命名）、PROD-GCP-02（production WIF pool provider 與最小權限 SA）、PROD-GCP-03（production Cloud SQL 與 Secret Manager secrets）、PROD-GCP-04（Web OAuth client 與正式網域 URL）、PROD-OPS-05（watch window／SLO／rollback owner）皆為 pending_human_decision
  - 註：更正先前盤點：GitHub 側的 production environment 確實存在（id 20574639394）且其 required_reviewers 保護有效，這一半是達成的。未達成的是 production 的 GCP 基礎設施、環境變數與五項具名人類決定，因此整體條款仍未完成。
- **A4**（人類授權）— `met_by_receipt`
  - 條款：缺少人類 authority 時明確 blocked而非填 placeholder
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/production-authority-prerequisites.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-variables-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`github-variables-audit.json environments.production.notes` — Production variables are intentionally omitted to prevent fake/placeholder configs until human Ops authority provides dedicated production GCP resources.
  - 註：條款要求「缺少人類 authority 時明確 blocked 而非填 placeholder」；收據以 status=blocked 與具名待決清單滿足，且變數刻意留空。這是 fail-closed 的正確行為。
- **A5**（runtime／部署）— `met_by_receipt`
  - 條款：redacted readback receipts 完整
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/gcp-wif-iam-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-variables-audit.json @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/README.md @ 8ad3f10dab70` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`兩份 audit 的 secret_values_redacted` — 皆為 true；secret 以名稱或 Secret Manager reference 記錄

**blocking 條款**

- A3：條款含人類授權成分且只部分達成

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A2(partially_met)、A3(partially_met)

**查證發現**

- 更正第一輪：GitHub 側的 production environment 確實存在。github-environments-audit.json（audited_at 2026-08-25T15:44:00Z）記 production id 20574639394、created_at 2026-08-25T15:41:19Z，帶一條 type=required_reviewers 的 protection rule，具名 reviewer 為 Alien-alfaloop 與 ajoe734；staging（id 17295059155）同樣有 required_reviewers。因此 acceptance『staging/production environments 具 required reviewers』是達成的。
- 未達成的是 production 的另一半：github-variables-audit.json 記 production total_variables=0、status=pending_human_authority；gcp-wif-iam-audit.json 記的 WIF pool／provider／deployer SA／Secret Manager references 全部屬 odayplus-runtime-20260825 這個 dev 專案，沒有 production 對應資源。
- 交付物自帶 production-authority-prerequisites.json，status=blocked_pending_human_authority，列出 PROD-GCP-01（production GCP Project／region／命名）、PROD-GCP-02（production WIF 與最小權限 SA）、PROD-GCP-03（production Cloud SQL 與 Secret Manager secrets）、PROD-GCP-04（Web OAuth client 與正式網域）、PROD-OPS-05（watch window／SLO／rollback owner）五項待具名人類決定。
- github-variables-audit.json 明載 production 變數刻意省略以免造假，符合 acceptance『缺少人類 authority 時明確 blocked 而非填 placeholder』。兩份 audit 皆 secret_values_redacted=true。

**缺口**

- A2（partially_met）：WIF/IAM 與 dev、staging 變數的 reference 完整且只記 reference；production 側的變數與 IAM 尚未存在，故此條款只對 dev/staging 成立。
- A3（partially_met）：更正先前盤點：GitHub 側的 production environment 確實存在（id 20574639394）且其 required_reviewers 保護有效，這一半是達成的。未達成的是 production 的 GCP 基礎設施、環境變數與五項具名人類決定，因此整體條款仍未完成。
- 盤點觀察（第 1 輪判斷已更正）：GitHub 側的 production environment 與其 required_reviewers 保護確實存在；未達成的是 production 的 GCP 資源、環境變數與五項具名人類決定。fail-closed 行為正確，但 acceptance 未全數完成。

**下一步最小驗證動作**

- 本項的技術就緒與人類授權必須分開記錄：GitHub 側 production environment 與其 required_reviewers 保護已存在，可直接採信收據；尚缺的是 production GCP 專案／WIF／IAM／Cloud SQL／Secret Manager 與環境變數，以及 PROD-GCP-01…PROD-OPS-05 五項具名人類決定。
- 最小驗證動作：唯讀取回 alfloop-dev/odayplus 的 production environment 變數清單與 production GCP 專案是否存在。若仍為 0 個變數且無 production 專案，本 ID 維持 blocked，且不得由 GitHub 側保護存在推定 acceptance 全數完成。

### `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1041](https://github.com/alfloop-dev/odayplus/pull/1041) — [ReviewBus] ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001 把 ephemeral staging lifecycle 接進唯一 Runtime R，state `MERGED`，merged `2026-08-27T17:50:22Z`
- 精確 head：`fada677569265ed258649b841e5a67fe7bb5dd80`；merge：`462c8cd4ff2569cec0f2c383d9e601c5bdbec715`
- 候選映射：`consistent`
  - PR body task id：`ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity3`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-27T17:30:51Z — Approved by assigned reviewer Antigravity3
- 原任務定義來源：`.orchestrator/task-briefs/odp_runtime_release_staging_lifecycle_integration_001.md`（生成於 2026-08-27T17:51:09Z）
  - brief 宣告 sha256 `a391689ac097ae3a988ff126cd4bf6db01fd594c631a600a4e22e98d595dbf0f`；重算檔案 sha256 `ece3bdccd07dee37f91ad3822401001b4c11ee69073b44c19cb81a3f4ae6d793`
  - 原 owner `Codex`／reviewer `Antigravity3`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：既有 Runtime Release staging 分支直接呼叫既有 staging lifecycle create/verify/hold/cleanup 且不新增 workflow 或 wrapper entrypoint
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：release_id candidate SHA manifest digest 與 API Web worker scheduler exact image digests 綁在同一不可變 handoff 且不得 rebuild
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：release-scoped lifecycle outputs 成為 staging endpoint database bucket tenant 與 IAM 唯一 authority 靜態 environment vars 只提供長期 foundation inputs
  - 證據（delivered_file）：`infra/terraform/tests/test_ephemeral_staging.py @ 462c8cd4ff25` — 已交付並存在於 merge commit，但 infra/ 不在 pyproject testpaths、也不被任何 workflow 引用，CI 從未執行它；本條款的 CI 執行證明來自下面的 tests/ops 測試檔，不是這一份。
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
  - 證據（ci_execution_binding）：`tests/ops/test_ephemeral_staging_lifecycle.py → check-run product @ fada67756926` — tests/ops/ 落在 product job 的 tests 收集路徑內，該 head product conclusion=success；同條款引用的 infra/terraform/tests/test_ephemeral_staging.py 則不被任何 CI job 收集，已在其證據列註明。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：staging smoke proof 不得 impersonate dev smoke operator 必須使用 release-scoped least-privilege identity
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
- **A5**（可由測試證明／runtime／部署）— `partially_met`
  - 條款：API Web worker scheduler migration one-shot backup restore rollback rehearsal 均可由同一狀態機產生 secret-free receipts
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
  - 註：「可由同一狀態機產生 secret-free receipts」的能力由測試覆蓋；本 PR 未帶任何已產生的 staging receipt 檔。
- **A6**（可由測試證明／程式或文件交付）— `met_by_test_in_green_ci`
  - 條款：第三方來源維持 disabled 且 public egress default-deny
  - 證據（delivered_file）：`infra/terraform/modules/ephemeral_staging/main.tf @ 462c8cd4ff25` — Terraform 模組實作 source disabled 預設值與 default-deny egress
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 462c8cd4ff25` — lifecycle 契約測試涵蓋 isolation 與 ingress/egress 邊界
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
  - 註：依 code/admission scope，Terraform 模組與 lifecycle 腳本實作 sources disabled 預設值與 default-deny egress 設定；本 PR 為 workflow 與 IaC 整合，未啟用外部來源。live runtime posture 依 recommendation_scope 宣告由後續 rollout 驗證，不在此 code task 產生 blocking。
- **A7**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：失敗環境依 TTL 保留成功環境由 prod closeout 精確清理且 orphan cleanup fail closed
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
- **A8**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：focused contract tests 必須在 staging 繞過 lifecycle 或使用 dev identity/靜態 service names 時失敗
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_workflow_expression_contexts.py @ 462c8cd4ff25` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ fada67756926`，2026-08-27T17:31:33Z — conclusion=success
  - 註：「繞過 lifecycle 或使用 dev identity 時必須失敗」是負向要求，由 focused 契約測試覆蓋。
- **A9**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：PR 與部署文件使用中文並說明取代關係與 rollback
  - 證據：無
  - 註：中文 PR 與部署文件可觀察。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A5(partially_met)、A9(process_constraint_unverifiable)

**查證發現**

- brief 明列三條 Verification 指令（pytest ops 契約測試、terraform fmt -check、bash -n）；五個 artifact 在 merge commit 全存在。

**缺口**

- A5（partially_met）：「可由同一狀態機產生 secret-free receipts」的能力由測試覆蓋；本 PR 未帶任何已產生的 staging receipt 檔。
- 第 4 輪更正（結論不變）：A3 同時引用 infra/terraform/tests/test_ephemeral_staging.py 與 tests/ops/test_ephemeral_staging_lifecycle.py。前者不被任何 CI job 收集，已在證據列標明；本條款的 CI 執行證明只來自後者。

### `ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1208](https://github.com/alfloop-dev/odayplus/pull/1208) — [ReviewBus] ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001 修正 Staging recovery bundle 與 Terraform state 儲存邊界，state `MERGED`，merged `2026-09-05T16:02:58Z`
- 精確 head：`a5e6a2f7074b6c5e679cbf7f0c6d03f9e6141a5a`；merge：`b9dd2e7b337e4bab8236cd15b5df07092685100b`
- 候選映射：`consistent`
  - PR body task id：`ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity2`／評審人 `Claude2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-05T15:39:52Z — Approved by assigned reviewer Claude2
- 原任務定義來源：`.orchestrator/task-briefs/odp_staging_recovery_bundle_storage_001.md`（生成於 2026-09-05T16:06:41Z）
  - brief 宣告 sha256 `beb27afc1fe6377922d95c184bd4f42e7cca3f8d2459a1731b17b86ae035bc64`；重算檔案 sha256 `3b8a70752fa0fcedde90f653493b712fdbbe08e743a74b95e48fc182469b9fff`
  - 原 owner `Antigravity2`／reviewer `Claude2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：依PR1041已完成之唯一Runtime Release lifecycle修補而非另建workflow或重新開同名task
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 註：沿用 PR #1041 的單一 lifecycle，未另建 workflow。
- **A2**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：從最新origin/dev及PR1206合併後的workflow開始以單一owner修改
  - 證據：無
  - 註：worktree 起點與單一 owner 屬執行過程。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：核對create hold resume closeout destroy所有recovery bundle讀寫不得寫入Terraform state/lock-only bucket
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_environment_precheck.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a5e6a2f7074b`，2026-09-05T15:34:06Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：使用現有受治理非state儲存及既有protected配置機制保持CMEK最小權限retention與release隔離及可恢復性
  - 證據（test_file）：`tests/release/test_release_environment_precheck.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a5e6a2f7074b`，2026-09-05T15:34:06Z — conclusion=success
  - 註：CMEK／最小權限／retention／release 隔離的契約由 precheck 與 workflow 契約測試界定；實際 live bucket 與 IAM 屬後續 staging rollout 範圍。
- **A5**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：未有核准目的地時fail closed並回報缺少的具體ref不得猜bucket或fallback到state bucket
  - 證據（test_file）：`tests/release/test_release_environment_precheck.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a5e6a2f7074b`，2026-09-05T15:34:06Z — conclusion=success
  - 註：缺值 fail-closed 由 precheck 測試覆蓋。
- **A6**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：新增focused契約與失敗路徑測試涵蓋目的地相同缺值跨release回復與cleanup精準scope
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_environment_precheck.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a5e6a2f7074b`，2026-09-05T15:34:06Z — conclusion=success
- **A7**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：不改historical receipts不刪既有quarantine物件不執行GCP apply或lease或dispatch
  - 證據：無
  - 註：「不執行 GCP apply／lease／dispatch」為否定要求；本盤點未查到本任務簽發此類動作。
- **A8**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：中文PR说明根因單一路徑差異驗證風險與rollback且與PR1206不得同檔平行
  - 證據：無
  - 註：中文 PR 可觀察。
- **A9**（可由測試證明／程式或文件交付）— `met_by_test_in_green_ci`
  - 條款：2026-09-05唯讀GCP/GitHub盤點未發現已驗證可供workflow寫入的非state/CMEK/release隔離目的地。程式階段仍應完成唯一required protected recovery destination contract、producer/consumer一致切換與缺值/同state bucket fail-closed回歸；不得用猜測bucket補值，也不得因live destination缺少而放棄code。實際resource/IAM/vars驗證屬後續Codex staging rollout，code完成不等於runtime完成。
  - 證據（test_file）：`tests/release/test_release_environment_precheck.py @ b9dd2e7b337e` — test_staging_scope_requires_foundation_variables_including_recovery_bundle_bucket, test_staging_scope_fails_closed_when_recovery_and_state_buckets_are_identical, test_staging_scope_fails_closed_on_placeholder_recovery_bucket, test_staging_scope_admits_distinct_valid_buckets
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ b9dd2e7b337e` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`.github/workflows/deploy-dev.yml @ b9dd2e7b337e` — workflow producer/consumer 完整接線，使用 ODP_STAGING_RECOVERY_BUNDLE_BUCKET
  - 證據（ci_check）：`check-run product @ a5e6a2f7074b`，2026-09-05T15:34:06Z — conclusion=success
  - 註：依 task brief 定義，本任務為 code/contract 任務，已完成唯一 required recovery destination contract、producer/consumer 接線、相同/缺值/佔位值 fail-closed 測試；live destination 建立與 GCP resource/IAM/vars 驗證明確 deferred 至後續 Codex staging rollout（ODP-EPHEMERAL-STAGING-ROLLOUT-001），不倒灌阻擋本 code 任務。
- **A10**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：同步既有docs/deployment/GCP_DEPLOY_GUIDE.md中與state bucket共用recovery bundle的舊說明；只保留一份權威操作流程。Terraform state以外的snapshot/model/MLflow/lease bucket不可為省事直接挪用，未核准目的地保持blocked。
  - 證據（delivered_file）：`docs/deployment/GCP_DEPLOY_GUIDE.md:227-236 @ b9dd2e7b337e` — 同步 state bucket 與 recovery bundle bucket 分離之權威操作流程
  - 證據（delivered_file）：`docs/deployment/ENVIRONMENTS.md @ b9dd2e7b337e` — 更新環境變數與儲存隔離說明
  - 註：GCP_DEPLOY_GUIDE.md:227-236 已同步 state bucket 與 recovery bundle bucket 分離之權威操作流程；未授權 live destination 保持 blocked，待後續 rollout 落地。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A2(process_constraint_unverifiable)、A7(process_constraint_unverifiable)、A8(process_constraint_unverifiable)

**查證發現**

- brief 的 Artifacts 為空；acceptance 第 9 條明文『2026-09-05 唯讀 GCP/GitHub 盤點未發現已驗證可供 workflow 寫入的非 state/CMEK/release 隔離目的地』，並自陳『code 完成不等於 runtime 完成』。本 PR 已完成 code contract、fail-closed precheck 測試與 GCP_DEPLOY_GUIDE.md 權威文件同步。

**缺口**

- 盤點觀察：實際 GCP 資源／IAM／vars 屬後續 staging rollout（ODP-EPHEMERAL-STAGING-ROLLOUT-001），依 task brief code 完成不等於 runtime 完成，已於 recommendation_scope 揭露。

**下一步最小驗證動作**

- 映射時把 runtime 部分獨立記為未完成，不得讓 code 合併把 runtime 一併帶成 done。

### `ODP-AVM-DEPRECIATION-CONTRACT-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1148](https://github.com/alfloop-dev/odayplus/pull/1148) — [ReviewBus] ODP-AVM-DEPRECIATION-CONTRACT-001 定義 AVM 資產折舊契約、版本化與舊估值卡處置，state `MERGED`，merged `2026-09-04T12:08:34Z`
- 精確 head：`aacc6ca19c12e6e9e17a350dbafe76c25e4b3bca`；merge：`739cbab19a171ef8d40e0f68f2b428f6b726bac0`
- 候選映射：`consistent`
  - PR body task id：`ODP-AVM-DEPRECIATION-CONTRACT-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review`／負責人 `Claude2`／評審人 `Antigravity6`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T12:10:24Z — Approved by assigned reviewer Antigravity6
- 原任務定義來源：`.orchestrator/task-briefs/odp_avm_depreciation_contract_001.md`（生成於 2026-09-04T12:11:49Z）
  - brief 宣告 sha256 `99e7a1fd42c990c3b3a3de40e36e80f2ad259cc7bc984dc2c1b2567d7ca445bb`；重算檔案 sha256 `2a9f0e939e1d72e99ff6fb0f1dcf44c5133d41035ee51d8b431df3d8ff4c5a72`
  - 原 owner `Claude2`／reviewer `Antigravity6`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：以 requirement 與現有計算證據判定 shared 或 AVM-specific 且寫明理由
  - 證據（delivered_file）：`docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/ODP_AVM001_DEPRECIATION_DISPOSITION_2026-09-04.md @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 註：判定理由寫在設計文件與 disposition 文件內；本盤點驗其存在與內容涵蓋，未重新論證結論。
- **A2**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：定義可測的 useful-life／residual-value／effective-date 輸入與版本欄位
  - 證據（delivered_file）：`docs/design/ODP_AVM_DEPRECIATION_CONTRACT_2026-09-03.md @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`modules/avm/tests/test_avm_depreciation_contract.py @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ aacc6ca19c12`，2026-09-04T10:20:13Z — conclusion=success
  - 註：契約欄位由 modules/avm 測試覆蓋。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：既有估值卡採可追溯 legacy version 而非靜默重算
  - 證據（test_file）：`modules/avm/tests/test_avm_depreciation_contract.py @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ aacc6ca19c12`，2026-09-04T10:20:13Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：提供兩筆除折舊外相同輸入應產生不同估值的失敗測試規格與 rollback
  - 證據（test_file）：`modules/avm/tests/test_avm_depreciation_contract.py @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/governance/test_avm001_disposition.py @ 739cbab19a17` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ aacc6ca19c12`，2026-09-04T10:20:13Z — conclusion=success
  - 註：失敗測試規格與 rollback 由兩支測試覆蓋。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- Acceptance 全為設計契約與測試規格；宣告的兩個 artifact 在 merge commit 均存在且由候選 PR 觸及。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-CANONICAL-LEGACY-LINEAGE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1150](https://github.com/alfloop-dev/odayplus/pull/1150) — [ReviewBus] ODP-CANONICAL-LEGACY-LINEAGE-001 量測 canonical 六模型的 producer lineage 與 legacy 1.0 遷移風險，state `MERGED`，merged `2026-09-03T15:37:27Z`
- 精確 head：`4ffa3122e47029d5143762413ae165d5b8d3c500`；merge：`865ff82e817cc56ffff0187f33cd6d3e8718958b`
- 候選映射：`consistent`
  - PR body task id：`ODP-CANONICAL-LEGACY-LINEAGE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Antigravity`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T15:11:43Z — Approved by assigned reviewer Antigravity
- 原任務定義來源：`.orchestrator/task-briefs/odp_canonical_legacy_lineage_001.md`（生成於 2026-09-03T15:39:15Z）
  - brief 宣告 sha256 `b007af294644b9dea9df8f5c8d376f0534d4f56d6344478fb0e32743030ecdd7`；重算檔案 sha256 `0ea9ba8bd00970214a9883f32a623045f193b32736a5308e72fb2c0eae51a59e`
  - 原 owner `Codex2`／reviewer `Antigravity`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：六模型各自有 producer→DB→API/client→UI/consumer lineage
  - 證據（receipt_file）：`docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md @ 865ff82e817c` — 由本 PR 交付並存在於 merge commit
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：缺席率只用可辨識 source payload 或 snapshot 計算且明載 denominator
  - 證據（receipt_file）：`docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md @ 865ff82e817c` — 由本 PR 交付並存在於 merge commit
  - 註：文件載有 denominator 定義，但本盤點未以來源資料重算缺席率數值。
- **A3**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：舊 1.0 不被批次改 NULL 並有 legacy_unknown／schema-version 策略
  - 證據（receipt_file）：`docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md @ 865ff82e817c` — 由本 PR 交付並存在於 merge commit
- **A4**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：列出 56 個 Python 引用及 SQL／TS／API reachability disposition
  - 證據（receipt_file）：`docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md @ 865ff82e817c` — 由本 PR 交付並存在於 merge commit

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 交付物為單一 evidence 文件，merge commit 與 dev tip 皆存在。

**缺口**

- 盤點觀察：Acceptance『缺席率只用可辨識 source payload 或 snapshot 計算』屬文件內容正確性，本次只驗證存在性，未重算數值。

**下一步最小驗證動作**

- 若需更高信心，由 reviewer 讀 docs/evidence/ODP_CANONICAL_MEASUREMENT_LINEAGE_2026-09-03.md 檢查 denominator 是否明載。

### `ODP-DRIFT-DEP-REMOVE-002` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1222](https://github.com/alfloop-dev/odayplus/pull/1222) — [ReviewBus] ODP-DRIFT-DEP-REMOVE-002 原子切換原生監控並移除 Evidently／NLTK 生產依賴，state `MERGED`，merged `2026-09-06T05:56:17Z`
- 精確 head：`6d438486c8645e476fcf86b5f675a5ca20592b9d`；merge：`66244b30c2615dcad8373e03fff096e298d21e98`
- 候選映射：`consistent`
  - PR body task id：`ODP-DRIFT-DEP-REMOVE-002`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity4`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-06T05:31:36Z — Approved by assigned reviewer Antigravity4
- 原任務定義來源：`.orchestrator/task-briefs/odp_drift_dep_remove_002.md`（生成於 2026-09-06T05:21:45Z）
  - brief 宣告 sha256 `5f6173719d8b4b7fce152fe20d1cae55b490f373470c70207d846ab23d0aa838`；重算檔案 sha256 `2f3ab9504a08be11d907f3913f5ef7462884d5d1a7c0c1480dbb8df72fa37f5d`
  - 原 owner `Codex`／reviewer `Antigravity4`／brief 當下狀態 `review`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：依賴三task done後從最新dev整合其已審查core及reference golden；在同一PR接入原生引擎並移除Evidently/NLTK，不能只把不安全套件刪掉造成broken import或只停監控。保持EvidentlyDriftMonitor/EvidentlyDriftResult公開類別、run/run_prediction/run_prediction_drift、metadata/DecisionPolicy/cohort/outputs及report consumer契約；engine欄位如實說明新引擎而非偽造來源。
  - 證據（delivered_file）：`modules/learninghub/infrastructure/native_drift.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`modules/learninghub/infrastructure/evidently_monitor.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`tests/models/test_evidently_monitor.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`tests/models/test_evidently_monitor_baseline.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/models/test_evidently_monitor.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 6d438486c864`，2026-09-06T05:20:31Z — conclusion=success
  - 註：原生引擎接入與公開類別／方法契約由 tests/models/test_evidently_monitor.py 覆蓋。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：保留data/feature/prediction/performance四類完整能力。使用已凍結且具hash的baseline逐case比對方法/統計量/pvalue或distance/threshold/drift flag/share/columns/report格式；任何差異明列並修正，不修改golden迎合新引擎、不只測簡單KS。Text適用性依真實production type inference與caller證據，不能無證據縮scope。
  - 證據（test_file）：`tests/models/test_evidently_monitor_baseline.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 6d438486c864`，2026-09-06T05:20:31Z — conclusion=success
  - 註：凍結 baseline 逐 case 比對由 baseline 測試覆蓋；宣告 artifact tests/models/evidently_baseline_support.py 在 merge commit 與其第一父皆不存在。
- **A3**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：重新解析uv.lock；evidently/nltk不得留在production或會被project整體scan安裝的dev scope。不使用override/--no-deps/fake package version。共享click/joblib/tqdm須按resolver保留，regex/defusedxml等只有實際無其他reverse dependency才移除。新增直接數值依賴時必須由真實import需要且完整audit。
  - 證據（delivered_file）：`uv.lock @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`pyproject.toml @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/candidate-inputs.json @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/committed-inputs.json @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 註：lock 重解析結果與輸入 hash 記錄在 completion 收據內。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：移除reference環境依賴不能讓baseline回歸變成skip：project tests重播固定golden驗證native；需要重生reference時用隔離pinned reference runner並明列依賴/步驟，不再把Evidently/NLTK裝回candidate環境。不得改reference輸出或覆寫baseline歷史receipts。
  - 證據（test_file）：`tests/models/test_evidently_monitor_baseline.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 6d438486c864`，2026-09-06T05:20:31Z — conclusion=success
  - 註：baseline 回歸未變成 skip。
- **A5**（程式或文件交付／runtime／部署）— `met_by_receipt`
  - 條款：更新NOTICE、current candidate SBOM及必要OSS capability/governance symbols，保留合理的歷史reference/授權。使用已修好的generate_sbom --output task專屬receipt與--check，記錄candidate SHA、lock hash、SBOM hash與實際installed packages，驗證Evidently/NLTK不在安裝scope和SBOM。
  - 證據（receipt_file）：`docs/evidence/sbom.json @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`NOTICE-THIRD-PARTY.md @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/candidate-audit.json @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/verification.md @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 註：SBOM 與 NOTICE 隨 PR 更新，candidate 收據記 candidate SHA、lock hash 與 SBOM hash。
- **A6**（runtime／部署）— `met_by_receipt`
  - 條款：在exact candidate的已同步venv跑真實pip-audit（明確--path/完整scan、工具版本/資料來源/時間/raw輸出/hash），不得用fixture scan或advisory DB fixed欄位代替remediation證明；任何真finding仍fail closed。完整相關監控/regression/OSS flow/security檢查及新head CI通過，中文PR交獨立review。
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/committed-pip-audit-1.stdout.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/integrated-pip-audit-1.stdout.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/pip-audit-1.stdout.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/committed-pip-audit-1.stderr.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/integrated-pip-audit-1.stderr.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/pip-audit-1.stderr.txt @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/candidate-audit.json @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/remediation.md @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 註：pip-audit 原始 stdout/stderr 與 remediation 說明皆為committed 檔案，非 fixture 摘要。
- **A7**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：PR1188仍需owner合入此合併後dev並重新取得required CI/獨立review；本task不能代替其approval或merge。回滾舊依賴重新帶回漏洞時保持NO-GO，不能當安全release。
  - 證據：無
  - 註：PR #1188 由其 owner 另行合入並重取 CI；本盤點於 2026-09-06 觀察時 #1188 尚未併入 dev。
- **A8**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：禁止waiver/suppression/ignore、假造patched版本、--no-deps偷漏production依賴、移動漏洞到未掃描scope、降低安全門檻；不得停用任何現有監控功能或以fixture/mocked scan宣稱真漏洞已修復。
  - 證據（test_file）：`tests/security/test_supply_chain_security_gate.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/security/test_oss_license_gate.py @ 66244b30c261` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 6d438486c864`，2026-09-06T05:20:31Z — conclusion=success
  - 註：禁止 waiver／suppression 的否定要求由 security gate 測試覆蓋。
- **A9**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：保留既有worktree與其他task變更；只在task-owned paths施工，按正常task_start/task_finalize提交PR；獨立review及required CI成功才合併，不自行寫canonical JSON或假簽Human/Ops。
  - 證據：無
  - 註：worktree 隔離與 task_start／task_finalize 流程屬執行過程。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A7(process_constraint_unverifiable)、A9(process_constraint_unverifiable)

**查證發現**

- 在 merge commit 上直接複驗：uv.lock 與 pyproject.toml 完全不含 evidently / nltk（grep 命中數 0），符合『不得留在 production 或 dev scope』。
- docs/evidence/completion/ODP-DRIFT-DEP-REMOVE-002/ 內有 pip-audit 的 stdout/stderr 原始輸出與 candidate/committed/integrated 三組 audit 與 inputs JSON，非 fixture 產物。

**缺口**

- 盤點觀察：宣告 artifact tests/models/evidently_baseline_support.py 在 merge commit 與其第一父皆不存在；該 artifact 條目與實際交付不符（疑為改名或未建立）。
- 盤點觀察：Acceptance 第 7 條明文本 task 不能代替 PR1188 的 approval/merge，該部分本質上在此任務範圍外。

**下一步最小驗證動作**

- 確認 evidently_baseline_support.py 是否改名為其他 baseline 支援檔；若無對應檔，修正 artifact 清單而非視為缺陷。

### `ODP-EPHEMERAL-STAGING-IAC-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1002](https://github.com/alfloop-dev/odayplus/pull/1002) — [ReviewBus] ODP-EPHEMERAL-STAGING-IAC-001 實作 ephemeral staging 建立、隔離、TTL 與安全清理，state `MERGED`，merged `2026-08-24T19:59:40Z`
- 精確 head：`ee6eddb6951aea752b857183a484ebc22ddf7772`；merge：`82ed6a05cf67c6c0e43f6f5b4219882251a87c42`
- 候選映射：`consistent`
  - PR body task id：`ODP-EPHEMERAL-STAGING-IAC-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Codex2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-24T19:36:58Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/odp_ephemeral_staging_iac_001.md`（生成於 2026-08-24T20:00:04Z）
  - brief 宣告 sha256 `3d8930d8de935582ffed3af3f2b96d1a356787916cc6c555c5220ddd60778c59`；重算檔案 sha256 `6c27b956de1db55ada21a8f20825aedc03f92e3f7ed8dfeb1a31d829c6739376`
  - 原 owner `Claude2`／reviewer `Codex2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（2 筆引用）

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `partially_met`
  - 條款：release-scoped namespace/service/job/database或schema/bucket/tenant/IAM 可重跑建立
  - 證據（delivered_file）：`infra/terraform/modules/ephemeral_staging/main.tf @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`infra/terraform/tests/test_ephemeral_staging.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ee6eddb6951a`，2026-08-24T19:37:57Z — conclusion=success
  - 註：Terraform 模組與其契約測試存在且 CI 綠。若條款的「可重跑建立」被解讀為需要真實 terraform apply 的 runtime 收據，本 PR 未帶任何 docs/evidence/ 收據。
- **A2**（可由測試證明）— `test_delivered_not_executed_at_exact_head`
  - 條款：resources 有 owner/created_at/expires_at labels
  - 證據（test_file）：`infra/terraform/tests/test_ephemeral_staging.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_execution_binding）：`infra/terraform/tests/test_ephemeral_staging.py → 精確 head ee6eddb6951a 的 CI job 對照` — infra/ 不在該 head 的 pyproject.toml testpaths（.orchestrator、delivery_toolchain、scripts、tests、modules、apps、shared、models）之內，且該 head 七個 workflow（ci、deploy-dev、promote-dev-to-main、merge-queue-review-gate、tooling-scope-review-gate、emgi-consumer-boundary、assisted-intake-design-validation）皆無任何一支引用 infra/terraform/tests。因此此測試檔雖已交付，CI 從未執行過它——即使該 head 的 product check 結論為 success。前輪把它綁到 product check 是錯誤的。
  - 註：Terraform 模組測試已交付且可定位，但不在任何 CI job 的收集範圍內；此條款的最小補證動作是在本地或 CI 明確執行 infra/terraform/tests/，本盤點依範圍限制不執行。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：cleanup 只依精確 labels 且有 orphan scanner
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ee6eddb6951a`，2026-08-24T19:37:57Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：成功清除失敗保留不超過 24h
  - 證據（test_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ee6eddb6951a`，2026-08-24T19:37:57Z — conclusion=success
- **A5**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：不修改唯一 workflow entrypoint
  - 證據（delivered_file）：`product_ops/deployment/staging_lifecycle.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`tests/ops/test_ephemeral_staging_lifecycle.py @ 82ed6a05cf67` — 由本 PR 交付並存在於 merge commit
  - 註：未新增第二個 workflow entrypoint。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A1(partially_met)、A2(test_delivered_not_executed_at_exact_head)
- CI 執行綁定：本 ID 有 1 條 acceptance 的測試檔已交付但在精確 head 上未被任何成功 check 執行（詳見各條 evidence 的 ci_execution_binding）。這不是條款被否證，而是『在此 head 跑過』這件事沒有證據。

**查證發現**

- 宣告 artifact 為 infra/terraform/、product_ops/deployment/staging_lifecycle.py、tests/ops/，皆為程式交付且在 merge commit 存在。

**缺口**

- A1（partially_met）：Terraform 模組與其契約測試存在且 CI 綠。若條款的「可重跑建立」被解讀為需要真實 terraform apply 的 runtime 收據，本 PR 未帶任何 docs/evidence/ 收據。
- 盤點觀察：候選 PR 未帶任何 docs/evidence/ 收據；acceptance『可重跑建立』若被解讀為需要真實 apply，則缺 runtime 證據。
- 第 4 輪更正：A2 的測試檔 infra/terraform/tests/test_ephemeral_staging.py 已交付，但 infra/ 不在該 head 的 pyproject testpaths，也不被該 head 七個 workflow 中任何一支引用，CI 從未執行它。該 head 的 product check 雖為 success，但它不收集這個路徑，前輪的綁定不成立。

**下一步最小驗證動作**

- 請 reviewer 裁定該 acceptance 為 IaC 能力交付或需 live apply；若為後者則需 staging apply/cleanup 收據，本盤點查無。
- 在 merge commit 上單獨執行 infra/terraform/tests/ 並保留退出碼收據，或把該路徑納入某個 CI job 的收集範圍；本盤點依範圍限制不執行測試。

### `ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1135](https://github.com/alfloop-dev/odayplus/pull/1135) — [ReviewBus] ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001 定義並實作首次 dev release 的 fail-closed recovery admission，state `MERGED`，merged `2026-09-02T14:15:41Z`
- 精確 head：`f24003a7e18a48c441252ea67beb16d7285c4614`；merge：`dd7013df830cdabe33d1b09e093dd728318d50f5`
- 候選映射：`consistent`
  - PR body task id：`ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity4`／評審人 `Claude`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-02T13:55:14Z — Approved by assigned reviewer Claude
- 原任務定義來源：`.orchestrator/task-briefs/odp_first_release_rollback_recovery_001.md`（生成於 2026-09-02T14:17:21Z）
  - brief 宣告 sha256 `27eefc7f69e27bd7f31c9fa0eff63a888a164c0138bf51e4dfb378bce62a1891`；重算檔案 sha256 `1edd7a93cc4deed0da9be44502a8a1a5f38d30f04f40b806488c78bed9dd38e0`
  - 原 owner `Antigravity4`／reviewer `Claude`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明／runtime／部署）— `met_by_receipt`
  - 條款：首次 dev deployment 僅在 target 無既有已核准 release 的可驗證 readback 下，才能通過 explicit initial-release recovery admission
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001/verification-receipt.json @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_probe_release_target_absence.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
  - 證據（receipt_field）：`verification-receipt.json runs[]` — 五組 focused run 皆 exit_code=0，綁 head 0253a5a682eb：first_release_recovery 4 tests、probe_release_target_absence 19 tests、release_manifest -k 篩選 17 tests、build_release_handoff 10 tests、deploy_workflow_contract 6 tests
  - 註：收據記錄的是逐命令 exit code 與 test count，而非只有一句通過。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：manifest 與 receipt 必須綁定 candidate SHA、manifest digest、target environment 與 recovery method
  - 證據（test_file）：`tests/release/test_release_manifest.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：initial-release 分支不得接受 placeholder、舊 schema v1 manifest 或任意手填 rollback binding
  - 證據（test_file）：`tests/release/test_release_manifest.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_first_release_recovery.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：後續 release、staging、production 與任一 external source enabled 仍強制既有 rollback/snapshot requirements
  - 證據（test_file）：`tests/release/test_release_manifest.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
  - 註：後續 release／staging／production 仍強制既有 requirement 由 manifest admission 測試覆蓋。
- **A5**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：deploy failure 的 recovery 只能清理 candidate 或維持零流量，不得宣稱回滾至不存在版本
  - 證據（test_file）：`tests/ops/test_first_release_recovery.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
- **A6**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：只改既有 Runtime Release/toolchain，不建立第二條 workflow 或 admission path
  - 證據（delivered_file）：`.github/workflows/deploy-dev.yml @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/release/release_manifest.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`tests/release/test_release_manifest.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 註：只改既有 Runtime Release 與 toolchain。
- **A7**（可由測試證明／執行過程約束）— `met_by_test_in_green_ci`
  - 條款：補 focused positive/negative tests 與中文 evidence，CI 綁定 exact head
  - 證據（test_file）：`tests/ops/test_first_release_recovery.py @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001/README.md @ dd7013df830c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f24003a7e18a`，2026-09-02T13:54:15Z — conclusion=success
  - 證據（review_gate）：`task-review-gate @ f24003a7e18a`，2026-09-02T13:55:14Z — Approved by assigned reviewer Claude
  - 註：正反向 focused tests 與中文 evidence 皆存在；CI 綁精確 head。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- Acceptance 明文『只改既有 Runtime Release/toolchain』與『補 focused positive/negative tests 與中文 evidence，CI 綁定 exact head』；docs/evidence/runtime/ODP-FIRST-RELEASE-ROLLBACK-RECOVERY-001/ 於 merge commit 存在，精確 head 7/7 success。

**缺口**

- 盤點觀察：Commit trailer 記 Reviewer: Codex，但 task-review-gate 與 brief 皆為 Claude；屬 trailer 過時，不影響核准事實。

### `ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1170](https://github.com/alfloop-dev/odayplus/pull/1170) — [ReviewBus] ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001 依 HZ-004 實績契約實作 heat-zone merge／split，state `MERGED`，merged `2026-09-05T16:07:23Z`
- 精確 head：`0585dd49976ebf269248067d4adfd56e278157ae`；merge：`eed8d51bb8a18c677baa25ab30a536cc576ae5fe`
- 候選映射：`consistent`
  - PR body task id：`ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity2`／評審人 `Claude`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-05T15:43:12Z — Approved by assigned reviewer Claude
- 原任務定義來源：`.orchestrator/task-briefs/odp_hz006_merge_split_implementation_001.md`（生成於 2026-09-05T16:08:31Z）
  - brief 宣告 sha256 `4f693296d798f7f791f7b38b0cb2ed4f77a1d755b2a84c0c562725a45e88af7d`；重算檔案 sha256 `aa4e78d7fb56fd86413b0803f604078cc711d9ddd33428d18c200e373bc2d69a`
  - 原 owner `Antigravity2`／reviewer `Claude`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：merge/split 同時使用相鄰關係與 HZ-004 outcome evidence
  - 證據（test_file）：`tests/models/test_heatzone_merge_split.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0585dd49976e`，2026-09-05T15:27:38Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：結果保存 parent/child zone lineage／model version／policy version
  - 證據（test_file）：`tests/contract/test_heatzone_composition_schema.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_heatzone_composition_migration.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0585dd49976e`，2026-09-05T15:27:38Z — conclusion=success
  - 證據（ci_execution_binding）：`tests/contract/test_heatzone_composition_schema.py → check-run product @ 0585dd49976e` — 該檔帶 requires_live_env 標記，被 product job 主 step 的 -m "not requires_live_env" 排除，但由同 job 的 Test database contracts, migrations and schema gates step （-m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration）跑回來；該檔無 requires_postgis 標記。product check 於該 head conclusion=success。
  - 註：lineage／model version／policy version 由 schema 契約測試與 migration 測試覆蓋。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：未達信心或資料門檻時 abstain 而非猜測
  - 證據（test_file）：`tests/models/test_heatzone_merge_split.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0585dd49976e`，2026-09-05T15:27:38Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：反事實／rollback／Operator approval 的 production-entry 測試通過
  - 證據（test_file）：`tests/integration/test_heatzone_composition_api.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_official_real_estate_postgresql.py @ eed8d51bb8a1` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0585dd49976e`，2026-09-05T15:27:38Z — conclusion=success
  - 證據（ci_step）：`ci.yml product job` — product job 掛 postgis/postgis:16-3.5 service 並設 INTAKE_TEST_DATABASE_URL；另有專步 `uv run pytest tests/integration/test_official_real_estate_postgresql.py`
  - 證據（ci_execution_binding）：`tests/integration/test_official_real_estate_postgresql.py → check-run product @ 0585dd49976e` — 該檔帶 requires_live_env 標記，被 product job 主 step 排除，但 product job 另有具名 step Test official real-estate outcomes on PostgreSQL 16 直接執行此檔（不帶 marker 篩選）。product check 於該 head conclusion=success。
  - 註：production-entry 測試在有真實 PostgreSQL service 的 job 內執行；本盤點未取 job log，只到 check 結論層。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact（modules/heatzone、apps/api、apps/web/features/operator、infra/db/migrations、tests）在 merge commit 全存在且由候選 PR 觸及。

**缺口**

- 盤點觀察：Acceptance『Operator approval 的 production-entry 測試通過』屬測試語意，本盤點未重跑測試，僅依精確 head 的 7/7 CI success。
- 第 4 輪補查（結論不變）：A2／A4 的測試檔帶 requires_live_env 標記，被 product job 主 step 的 marker 運算式排除，但由同 job 的資料庫 step 與具名 PostgreSQL 16 step 跑回來，已補上 step 層綁定。

### `ODP-INT-MANUAL-CORRECTION-AUDIT-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1175](https://github.com/alfloop-dev/odayplus/pull/1175) — [ReviewBus] ODP-INT-MANUAL-CORRECTION-AUDIT-001 建立 INT-006 人工校正寫入、授權、稽核與 rollback，state `MERGED`，merged `2026-09-05T03:53:16Z`
- 精確 head：`0b3e1987d426200928cc5ec62dfd8de4f8d81358`；merge：`f02960fa81c2beeef88dd213e86ec96c8380b2d8`
- 候選映射：`consistent`
  - PR body task id：`ODP-INT-MANUAL-CORRECTION-AUDIT-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity`／評審人 `Codex2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-05T03:33:39Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/odp_int_manual_correction_audit_001.md`（生成於 2026-09-05T04:08:18Z）
  - brief 宣告 sha256 `862407d887730a08c6bbef79987a21c3b0e9e466a2b1ac1b9c161385613f9f21`；重算檔案 sha256 `cd5ec5b7a1bfed32db52e507c4a4d3088ae11d4820d8040c0950a60aba4ee504`
  - 原 owner `Antigravity`／reviewer `Codex2`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：API 可對具名 canonical record 提交 correction 且 server-side actor 不可信任 payload spoof
  - 證據（test_file）：`tests/contract/test_manual_correction_contract.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`apps/api/app/routes/listings.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0b3e1987d426`，2026-09-05T01:15:35Z — conclusion=success
  - 註：actor 不可由 payload spoof 由契約測試覆蓋。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：immutable audit 保存 old/new／reason／actor／time／revision
  - 證據（test_file）：`tests/integration/test_manual_correction_persistence.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`infra/db/migrations/000021_manual_corrections_audit_schema.sql @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0b3e1987d426`，2026-09-05T01:15:35Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：跨租戶／未授權／stale revision／無 reason 全部拒絕
  - 證據（test_file）：`tests/contract/test_manual_correction_contract.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_manual_correction_persistence.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0b3e1987d426`，2026-09-05T01:15:35Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：PostgreSQL 與 SQLite readback 及 rollback/compensation 有 production-entry 測試
  - 證據（test_file）：`tests/integration/test_manual_correction_persistence.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_postgresql_persistence.py @ f02960fa81c2` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 0b3e1987d426`，2026-09-05T01:15:35Z — conclusion=success
  - 證據（ci_step）：`ci.yml product job（merge f02960fa 當時）` — product job 掛 postgis/postgis:16-3.5 service container，job 層 env 設 INTAKE_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/oday_product_test；步驟「Test product code」為 `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`
  - 證據（test_gate）：`tests/integration/test_postgresql_persistence.py @ f02960fa` — 模組層 pytestmark 只有 skipif(not INTAKE_TEST_DATABASE_URL)，沒有 requires_live_env 標記；因此在 product job 內不被 marker 排除也不被 skipif 跳過。檔內第 473 行 test_postgresql_manual_correction_readback_and_rollback 即本條款的 PostgreSQL readback 與 rollback 測試
  - 證據（test_gate）：`tests/integration/test_manual_correction_persistence.py @ f02960fa` — SQLite 半邊由 test_sqlite_durable_manual_correction_and_restart_survival、test_durable_sqlite_app_entry_and_multi_rollback_lifecycle 等多支測試覆蓋，含 rollback 與 compensation 路徑，無環境變數閘
  - 註：更正先前盤點：本條款不是只有泛用 CI 成功可依。PostgreSQL readback 與 rollback 有具名測試，且該測試在 product job 內對真實 PostgreSQL 16 service 執行——其唯一閘是 INTAKE_TEST_DATABASE_URL，而 CI 有設。剩餘的證據強度限制是本盤點未取 job log，無法出示該支測試個別的 PASSED 行。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 更正第一輪：本 ID 的 PostgreSQL readback 不是「僅依 CI 結論、未驗證」。逐條核對如下。
- 本 PR 交付 tests/integration/test_postgresql_persistence.py，其中第 473 行 test_postgresql_manual_correction_readback_and_rollback 即 acceptance 第 4 條的 PostgreSQL readback 與 rollback 測試。
- 該檔在 merge f02960fa 上的模組層 pytestmark 只有 skipif(not os.environ.get('INTAKE_TEST_DATABASE_URL'))，沒有 requires_live_env 標記。
- 同一 merge commit 的 .github/workflows/ci.yml：product job 掛 postgis/postgis:16-3.5 service container，job 層 env 設 INTAKE_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/oday_product_test，步驟「Test product code」為 `uv run pytest -m "not requires_live_env and not performance" tests modules apps shared models -n auto`。因此該測試既不被 marker 排除、也不被 skipif 跳過，是在真實 PostgreSQL 16 上執行。
- SQLite 半邊由 tests/integration/test_manual_correction_persistence.py 覆蓋，含 restart survival、multi-rollback lifecycle、tenant isolation 與 compensation 路徑，無環境變數閘。
- 七個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success，product 於 2026-09-05T01:15:35Z 完成。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

**下一步最小驗證動作**

- 剩餘的證據強度限制只有一項：本盤點未取 job log，無法出示該支測試個別的 PASSED 行。若 reviewer 需要該層證據，最小動作是取回 product job（2026-09-05T01:15:35Z 完成）的 log 並搜尋該測試名稱；但條款層級的對應已成立，不需再以「需要 live DB」為由保留缺口。

### `ODP-INT001-CDC-DISPOSITION-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1166](https://github.com/alfloop-dev/odayplus/pull/1166) — [ReviewBus] ODP-INT001-CDC-DISPOSITION-001 依 upstream 證據實作 CDC connector 或正式處置 INT-001，state `MERGED`，merged `2026-09-03T20:50:26Z`
- 精確 head：`39289e4207b29d2c7adea071f42901f9f0b5ae20`；merge：`0f35515ed15aad36c496f30973b2e2ce9fa2b026`
- 候選映射：`consistent`
  - PR body task id：`ODP-INT001-CDC-DISPOSITION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity7`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T20:07:47Z — Approved by assigned reviewer Antigravity7
- 原任務定義來源：`.orchestrator/task-briefs/odp_int001_cdc_disposition_001.md`（生成於 2026-09-03T20:51:14Z）
  - brief 宣告 sha256 `6b95285af5e46b0675b6ce109f5c156e948fe640ef31e80ef428d7c40a12ac38`；重算檔案 sha256 `a7befd2c95a80f997c4f0c7e1bb404e31037502b18e4a157c2999fd56a1f0fc8`
  - 原 owner `Codex`／reviewer `Antigravity7`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：實作時使用真 upstream contract 並保存 offset／ordering／delete／replay／idempotency semantics
  - 證據（receipt_file）：`docs/evidence/ODP_INT001_CDC_DISPOSITION_2026-09-03.md @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 註：無適用 upstream 時本條款以 disposition 文件記載處置，而非實作 connector。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：credential 與 tenant boundary fail closed 且有 production-entry 測試
  - 證據（test_file）：`tests/integration/test_int001_cdc_disposition.py @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 39289e4207b2`，2026-09-03T20:09:20Z — conclusion=success
- **A3**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：無適用 upstream 時不新增 connector 且 AI 不自簽 waiver
  - 證據（receipt_file）：`docs/evidence/ODP_INT001_CDC_DISPOSITION_2026-09-03.md @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/governance/set_valued_requirements.json @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 註：未新增 connector 且未自簽 waiver；requirement 仍記為待人類裁決，這是需求狀態而非任務缺陷。下游不得據此視為 INT-001 已實作。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：requirement member 與 formal disposition ref 維持一致
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_int001_cdc_disposition.py @ 0f35515ed15a` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ 39289e4207b2`，2026-09-05T04:03:03Z — conclusion=success；收集 delivery_toolchain/，涵蓋 delivery_toolchain/governance/test_check_requirement_members.py
  - 證據（ci_check）：`check-run product @ 39289e4207b2` — conclusion=success；收集 tests/，涵蓋 tests/integration/test_int001_cdc_disposition.py
  - 註：本條款的兩個測試檔分屬不同 job 的收集範圍：delivery_toolchain/ 由 orchestrator job 收集，tests/integration/ 由 product job 收集；該 head 兩個 check 結論皆為 success。前輪只綁 orchestrator check，對 tests/integration/ 的那一半是錯誤綁定。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 在 merge commit 的 set_valued_requirements.json 中，ODP-FR-INT-001 的 CDC member 為 status=absent、disposition.state=OPEN，並帶 formal_handback_ref 與 assigned_to／next_review_date，無 decider 欄位。
- 符合 acceptance『無適用 upstream 時不新增 connector 且 AI 不自簽 waiver』：未出現 AI 自簽的 decider。

**缺口**

- 盤點觀察：需求本身仍 OPEN 待人類裁決；這是需求狀態而非任務缺陷，但下游不得據此視為 INT-001 已實作。
- 第 4 輪更正（結論不變）：A4 兩個測試檔分屬不同 job 的收集範圍，原本只綁 orchestrator check；已補上收集 tests/integration/ 的 product check，兩者於該 head 皆為 success。

### `ODP-JOB-PARTIAL-DISPOSITION-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1172](https://github.com/alfloop-dev/odayplus/pull/1172) — [ReviewBus] ODP-JOB-PARTIAL-DISPOSITION-001 依 producer 證據補 PARTIAL 狀態轉移或正式處置 SHARED-001，state `MERGED`，merged `2026-09-04T13:14:12Z`
- 精確 head：`f8caf62e11643f9cbe59ea6b958faeab746fcac2`；merge：`9647d673ccf2c0f11ef565e78511099821d85c19`
- 候選映射：`consistent`
  - PR body task id：`ODP-JOB-PARTIAL-DISPOSITION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity6`／評審人 `Claude`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T12:50:50Z — Approved by assigned reviewer Claude
- 原任務定義來源：`.orchestrator/task-briefs/odp_job_partial_disposition_001.md`（生成於 2026-09-04T13:16:07Z）
  - brief 宣告 sha256 `a9e27409a278cf816439817b489881461c8d8ca286f52d33a7b959b188afe59b`；重算檔案 sha256 `83653dfcbf44eec551c2905bcde6293fbdd74ec825830e0a092702146c475bec`
  - 原 owner `Antigravity6`／reviewer `Claude`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：只有證據列名的 job 能產生 PARTIAL
  - 證據（receipt_file）：`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md @ 9647d673ccf2` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/governance/test_job_partial_disposition.py @ 9647d673ccf2` — test_shared001_partial_disposition_state_and_handback_metadata 斷言 PARTIAL status == absent 與 disposition.state == BLOCKED_BY_EVIDENCE，且 resolve() 查無未列名 producer
  - 證據（ci_check）：`check-run product @ f8caf62e1164`，2026-09-04T12:51:24Z — conclusion=success
  - 註：查無真實 producer 證據，PARTIAL 保持 absent，由治理測試直接斷言。
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：PARTIAL receipt 可區分 succeeded／failed items 且 retry 不重做成功項
  - 證據（receipt_file）：`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md @ 9647d673ccf2` — §4.2 明細收據與成員識別架構契約及 §4.3 重試契約（不重做成功項）規範未來 Pathway A 實作規範
  - 證據（test_file）：`tests/governance/test_job_partial_disposition.py @ 9647d673ccf2` — test_shared001_handback_document_exists_and_covers_contracts 驗證 handback 文件與字串存在，未執行 item receipt/retry 行為測試
  - 註：按原 task 條件分支（無適用 producer 時保持 absent 並交付 formal handback），因查無真實 producer 證據，PARTIAL 行為（明細收據與重試）未於 runtime 實作，而是於 formal handback 文件（docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md §4.2-4.3）建立未來 Pathway A 設計契約；tests/governance/test_job_partial_disposition.py 僅驗證 handback 文件與字串存在，不執行 item receipt/retry 行為。按條件分支，此項之實作行為不適用，formal handback 契約已交付。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：business outcome 與 delivery state 型別分離
  - 證據（test_file）：`tests/governance/test_job_partial_disposition.py @ 9647d673ccf2` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ f8caf62e1164`，2026-09-04T12:51:24Z — conclusion=success
- **A4**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：無適用 producer 時不造功能且 formal disposition 缺人類簽署仍保持未結案
  - 證據（receipt_file）：`docs/evidence/ODP_JOB_PARTIAL_DISPOSITION_2026-09-03.md @ 9647d673ccf2` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md @ 9647d673ccf2` — 由本 PR 交付並存在於 merge commit
  - 註：formal disposition 缺人類簽署時保持未結案，由 disposition 文件與 requirement manifest 記載。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 按原 task 條件分支，因查無 producer 證據，PARTIAL 保持 absent，交付 formal handback 文件（HB-SHARED001-PARTIAL-001）並登錄 BLOCKED_BY_EVIDENCE；型別分離由測試直接驗證，handback 契約涵蓋未來 Pathway A。

**缺口**

- 盤點觀察：候選 PR head commit 無 LLM-Agent/Task-ID/Reviewer trailer（head 為 base advance merge commit）；owner/reviewer 以 task-review-gate 與 brief 為準。
- 盤點觀察：handback HB-SHARED001-PARTIAL-001 尚待 Human/Ops 簽署。

**下一步最小驗證動作**

- 若 reviewer 要求 trailer 佐證，改查該分支最後一個非 merge commit。

### `ODP-LH-PREDICTION-DRIFT-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1154](https://github.com/alfloop-dev/odayplus/pull/1154) — [ReviewBus] ODP-LH-PREDICTION-DRIFT-001 實作 LearningHub prediction drift 的 cohort、持久化、DecisionPolicy 與告警，state `MERGED`，merged `2026-09-03T13:07:52Z`
- 精確 head：`a7f7b7d9d174b45f937d1fb0d12e2c962c97adad`；merge：`0cbc5330f6a076344d2dff32370dedae5b82da4a`
- 候選映射：`consistent`
  - PR body task id：`ODP-LH-PREDICTION-DRIFT-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity3`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T12:39:31Z — Approved by assigned reviewer Antigravity3
- 原任務定義來源：`.orchestrator/task-briefs/odp_lh_prediction_drift_001.md`（生成於 2026-09-03T14:15:09Z）
  - brief 宣告 sha256 `3575610026103186c7fe4b7f73fe325484142c0084b47403e1d2d8a559554317`；重算檔案 sha256 `8d30f00b89825873dab61f60a0ee254ce6ee459364c27748f402846c5d7da747`
  - 原 owner `Codex`／reviewer `Antigravity3`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：相同 prediction 分布不告警且顯著漂移會告警
  - 證據（test_file）：`modules/learninghub/tests/test_prediction_drift.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_prediction_drift_migration.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a7f7b7d9d174`，2026-09-03T12:42:45Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：monitor 僅比較相同 model/version 與合規 cohort
  - 證據（test_file）：`modules/learninghub/tests/test_prediction_drift.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_prediction_drift_migration.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a7f7b7d9d174`，2026-09-03T12:42:45Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：receipt 保存 reference/current snapshot id／model version／policy version
  - 證據（test_file）：`tests/ops/test_prediction_drift_migration.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`infra/db/migrations/000017_learninghub_prediction_drift.sql @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a7f7b7d9d174`，2026-09-03T12:42:45Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：production-entry 測試證明監控有接線且不依賴不存在的 PredictionDriftPreset
  - 證據（test_file）：`modules/learninghub/tests/test_prediction_drift.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_prediction_drift_migration.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`modules/learninghub/infrastructure/evidently_monitor.py @ 0cbc5330f6a0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a7f7b7d9d174`，2026-09-03T12:42:45Z — conclusion=success
  - 註：「不依賴不存在的 PredictionDriftPreset」是否定要求，由 monitor 測試覆蓋。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-LH003-BACKTEST-RELEASE-GATE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1165](https://github.com/alfloop-dev/odayplus/pull/1165) — [ReviewBus] ODP-LH003-BACKTEST-RELEASE-GATE-001 把 LearningHub Backtest 接成版本化 model release gate，state `MERGED`，merged `2026-09-03T21:53:56Z`
- 精確 head：`ce7a3fadd758ca19b5ebbc632e3d8aa523003f65`；merge：`d3e344353ad4280962ce25078c067f14c9a6df10`
- 候選映射：`consistent`
  - PR body task id：`ODP-LH003-BACKTEST-RELEASE-GATE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity3`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T21:26:50Z — Approved by assigned reviewer Antigravity3
- 原任務定義來源：`.orchestrator/task-briefs/odp_lh003_backtest_release_gate_001.md`（生成於 2026-09-03T21:54:11Z）
  - brief 宣告 sha256 `810995465b172dc769902a81a28a19e272aebf01cab791512fbdccefc4a28aea`；重算檔案 sha256 `77546489ad0b079df7ac2e383347f03598e034a4f7563afebc6430f56b0c668d`
  - 原 owner `Codex`／reviewer `Antigravity3`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：FULL／CANARY 在缺失／失敗／stale backtest receipt 時 fail closed
  - 證據（test_file）：`modules/learninghub/tests/test_backtest_release_gate.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ce7a3fadd758`，2026-09-03T21:28:15Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：receipt 綁 model version／dataset snapshot／code version／policy version
  - 證據（test_file）：`modules/learninghub/tests/test_backtest_release_gate.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_backtest_migration.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ce7a3fadd758`，2026-09-03T21:28:15Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：通過 backtest 仍不能繞過既有 model card／approval／rollback gates
  - 證據（test_file）：`tests/integration/test_production_model_lifecycle.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`modules/learninghub/tests/test_backtest_release_gate.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ce7a3fadd758`，2026-09-03T21:28:15Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：production release entry 有正反向與 stale-version 測試
  - 證據（test_file）：`tests/integration/test_learninghub_postgresql_release.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_production_model_lifecycle.py @ d3e344353ad4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ce7a3fadd758`，2026-09-03T21:28:15Z — conclusion=success
  - 證據（ci_step）：`ci.yml product job` — 掛 postgis/postgis:16-3.5 service 並設 INTAKE_TEST_DATABASE_URL，production release entry 測試在有真實 PostgreSQL 的 job 內執行
  - 註：本盤點未取 job log，只到 check 結論層。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-MEASUREMENT-CROSSLAYER-GATE-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1153](https://github.com/alfloop-dev/odayplus/pull/1153) — [ReviewBus] ODP-MEASUREMENT-CROSSLAYER-GATE-001 把量測缺席檢查擴到 Pydantic、mapper、SQL/dbt、OpenAPI 與 TS，state `MERGED`，merged `2026-09-03T12:33:14Z`
- 精確 head：`aaa9aaee9380d50ae49544e03ea0a9bdaf8e86a2`；merge：`8479567d66d9d08d0aa4a27d9f0a901ab092f2ae`
- 候選映射：`consistent`
  - PR body task id：`ODP-MEASUREMENT-CROSSLAYER-GATE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity4`
- 精確 head CI：skipped=3、success=4；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T12:28:23Z — Approved by assigned reviewer Antigravity4
- 原任務定義來源：`.orchestrator/task-briefs/odp_measurement_crosslayer_gate_001.md`（生成於 2026-09-03T15:58:06Z）
  - brief 宣告 sha256 `43f8232d0113031304dac7953ce364562c19e9f0634e21e0ca45e3073bbff8eb`；重算檔案 sha256 `ca4532ee864bf87774356543c0d9e4f8ffaf7fecb2b38c57c581a609f465aebf`
  - 原 owner `Claude2`／reviewer `Antigravity4`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：至少各有 Pydantic／mapper／SQL-dbt／OpenAPI-TS 的會紅負向案例
  - 證據（test_file）：`delivery_toolchain/governance/test_check_measurement_defaults.py @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/governance/check_measurement_defaults.py @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ aaa9aaee9380`，2026-09-03T12:23:05Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_measurement_defaults.py → check-run orchestrator @ aaa9aaee9380` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：負向案例由 delivery_toolchain 自帶測試覆蓋；精確 head 的 product／product-e2e-gate／performance-gate 為 skipped（scope skip），實跑的是 orchestrator。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：不把合法常數如 SRID／limit／horizon 誤判為量測
  - 證據（test_file）：`delivery_toolchain/governance/test_check_measurement_defaults.py @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ aaa9aaee9380`，2026-09-03T12:23:05Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_measurement_defaults.py → check-run orchestrator @ aaa9aaee9380` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：同上 skip 註記。
- **A3**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：目前債務以逐欄窄豁免維持 CI 可執行且過期會紅
  - 證據（delivered_file）：`delivery_toolchain/governance/measurement_default_exemptions.json @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`.github/workflows/ci.yml @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`delivery_toolchain/governance/test_check_measurement_defaults.py @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit。條款的兩半各有具名測試：逐欄窄豁免的必要欄位 → test_an_exemption_without_an_owner_is_refused、test_an_exemption_without_a_reason_is_refused、test_an_exemption_without_an_expiry_is_refused、test_an_unreadable_expiry_is_refused_rather_than_ignored；過期會紅 → test_an_exemption_past_its_date_is_reported、test_an_expired_exemption_fails_the_check_itself、test_no_exemption_is_already_past_its_date；邊界 → test_an_exemption_on_its_last_day_is_still_live。
  - 證據（ci_step）：`ci.yml:105-106 『Refuse bounded scores that default to perfect』 @ 8479567d66d9` — orchestrator job 另有具名 step 直接執行 uv run python delivery_toolchain/governance/check_measurement_defaults.py，因此『維持 CI 可執行且過期會紅』除了測試層之外，在該 head 還有 gate step 層的執行證據。
  - 證據（ci_check）：`check-run orchestrator @ aaa9aaee9380`，2026-09-03T12:23:05Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_measurement_defaults.py → check-run orchestrator @ aaa9aaee9380` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：第 4 輪更正：本條款原本只有兩個 delivered_file 與一個 ci_check，沒有任何 test_file，屬以綠色 job 泛稱代替條款證據。現補上具名測試與 ci.yml 的 gate step，兩者都由該 head 結論 success 的 orchestrator check 執行。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：修復欄位時同 commit 可移除對應豁免
  - 證據（test_file）：`delivery_toolchain/governance/test_check_measurement_defaults.py @ 8479567d66d9` — 由本 PR 交付並存在於 merge commit；本條款（修復欄位時同 commit 可移除對應豁免）對應具名測試 test_the_exemption_is_live_while_the_field_is_and_stale_after 與 test_no_exemption_outlives_the_field_it_covers。
  - 證據（ci_check）：`check-run orchestrator @ aaa9aaee9380`，2026-09-03T12:23:05Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_measurement_defaults.py → check-run orchestrator @ aaa9aaee9380` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：同上 skip 註記。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- checker、測試、豁免清單與 .github/workflows/ci.yml 四個 artifact 在 merge commit 全存在且由候選 PR 觸及。

**缺口**

- 盤點觀察：精確 head 有 3 個 check conclusion=skipped，product 層測試未在該 head 實跑。
- 第 4 輪更正（結論不變）：A1-A4 的 delivery_toolchain/governance/test_check_measurement_defaults.py 原被標為 delivered_file，已正規化為 test_file，並補上 orchestrator job 收集 delivery_toolchain/ 的依據。

**下一步最小驗證動作**

- 確認被 skip 的 check 是否為 change-scope 設計行為。

### `ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1169](https://github.com/alfloop-dev/odayplus/pull/1169) — [ReviewBus] ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001 補齊 merge queue 不實作決定的正式可稽核欄位，state `MERGED`，merged `2026-09-03T18:10:43Z`
- 精確 head：`f2d1bd88ec2dc89148dd3d7346a01de0e921432b`；merge：`830a8ebf919cd3f9dc3d53fc13c4f76e65eeff6e`
- 候選映射：`consistent`
  - PR body task id：`ODP-MERGE-QUEUE-DISPOSITION-AUDIT-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity4`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T17:44:08Z — Approved by assigned reviewer Antigravity4
- 原任務定義來源：`.orchestrator/task-briefs/odp_merge_queue_disposition_audit_001.md`（生成於 2026-09-03T18:21:51Z）
  - brief 宣告 sha256 `77f373e9b8a8b68830b8f209043ac4ff7fd4b5c25ffa09ce9a8b2884b8cb3d9d`；重算檔案 sha256 `b78a2dcdba66ef3c713c47299fb413ef451d85031632fe0965d5f25cc108a1c2`
  - 原 owner `Claude2`／reviewer `Antigravity4`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：既有正式決策存在時完整連結 decider／date／scope／review-expiry／reopen trigger
  - 證據（receipt_file）：`docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
- **A2**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：找不到權威決策時 task 轉 blocked waiting Human/Ops 而不製造簽署
  - 證據（receipt_file）：`docs/evidence/ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`docs/plans/ODP_OPEN_DECISIONS_2026-09-03.md @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 註：找不到權威決策時轉 blocked 待 Human/Ops，由 open decisions 文件記載，未製造簽署。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：manifest note 不被當成 requirement amendment
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/governance/check_requirement_members.py @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ f2d1bd88ec2d`，2026-09-03T17:28:13Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_requirement_members.py → check-run orchestrator @ f2d1bd88ec2d` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：manifest note 不得當成 requirement amendment，由 checker 測試覆蓋。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：checker 能拒絕缺欄位或過期的 nonimplementation disposition
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 830a8ebf919c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ f2d1bd88ec2d`，2026-09-03T17:28:13Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_requirement_members.py → check-run orchestrator @ f2d1bd88ec2d` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 兩個 artifact（set_valued_requirements.json、ODP_MERGE_QUEUE_DISPOSITION_2026-09-03.md）在 merge commit 存在。

**缺口**

- 盤點觀察：Acceptance『找不到權威決策時 task 轉 blocked waiting Human/Ops』的負向行為未在本盤點重跑驗證。
- 第 4 輪更正（結論不變）：A3-A4 的 delivery_toolchain/governance/test_check_requirement_members.py 同上 kind 正規化與收集依據。

### `ODP-MODELREADY-QUALITY-NULLABLE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1168](https://github.com/alfloop-dev/odayplus/pull/1168) — [ReviewBus] ODP-MODELREADY-QUALITY-NULLABLE-001 ModelReadyRecord nullable mapper 與 dataset snapshot admission ，state `MERGED`，merged `2026-09-03T20:32:14Z`
- 精確 head：`a2a8d9c235e88820572b30700d5f5cc9b31bb0af`；merge：`10d47d17a7b3ca8d3c655afaaf02b931c3fb7e0f`
- 候選映射：`consistent`
  - PR body task id：`ODP-MODELREADY-QUALITY-NULLABLE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Antigravity5`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T19:52:25Z — Approved by assigned reviewer Antigravity5
- 原任務定義來源：`.orchestrator/task-briefs/odp_modelready_quality_nullable_001.md`（生成於 2026-09-03T20:35:02Z）
  - brief 宣告 sha256 `5f526405eb8999404d45dd0594016f36a83cdc43c0eff863b0bbe5ddab4691b3`；重算檔案 sha256 `73bda16f8d3e444acb589e0ba176b3516c556140cd86a62d44bdbca04a5e49b5`
  - 原 owner `Codex`／reviewer `Antigravity5`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：mapper 對 missing／null 不補 1.0 且型別保持 nullable
  - 證據（test_file）：`tests/integration/test_model_ready_materialization.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`shared/infrastructure/persistence/model_ready.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a2a8d9c235e8`，2026-09-03T19:54:11Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：任一缺品質欄位的 record 被 snapshot admission 拒絕並具名 ids／fields
  - 證據（test_file）：`tests/integration/test_learninghub_dataset_snapshot_api.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/data/test_pit_snapshot.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a2a8d9c235e8`，2026-09-03T19:54:11Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：完整資料仍能產生 immutable snapshot receipt
  - 證據（test_file）：`tests/data/test_pit_snapshot.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_learninghub_dataset_snapshot_api.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a2a8d9c235e8`，2026-09-03T19:54:11Z — conclusion=success
- **A4**（可由測試證明／程式或文件交付）— `met_by_test_in_green_ci`
  - 條款：植入缺值 production-entry 測試與兩筆 exemption 移除在同一 commit
  - 證據（test_file）：`modules/learninghub/tests/test_learninghub_production_runtime.py @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/governance/measurement_default_exemptions.json @ 10d47d17a7b3` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a2a8d9c235e8`，2026-09-03T19:54:11Z — conclusion=success
  - 註：植入缺值的 production-entry 測試與兩筆 exemption 移除同在本 PR 的 merge commit。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-NET002-LEASE-DISPOSITION-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1187](https://github.com/alfloop-dev/odayplus/pull/1187) — [ReviewBus] ODP-NET002-LEASE-DISPOSITION-001 依租約資料證據實作 per-option feasibility 或正式處置 NET-002 LEASE，state `MERGED`，merged `2026-09-04T12:35:05Z`
- 精確 head：`3bd82e8a401d463ef8301671620d45fc93b6127b`；merge：`c65bb54dd1171c8c878d37282470ada6a18f9ed6`
- 候選映射：`consistent`
  - PR body task id：`ODP-NET002-LEASE-DISPOSITION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Antigravity6`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T12:12:18Z — Approved by assigned reviewer Antigravity6
- 原任務定義來源：`.orchestrator/task-briefs/odp_net002_lease_disposition_001.md`（生成於 2026-09-04T12:36:01Z）
  - brief 宣告 sha256 `ae7bf45dcd0e7e11e58a3c2d5d316d6c5f63e88cbd89172fb6a2fd38e73a6b37`；重算檔案 sha256 `50c45e3b8a8ef4d9bced3e22b96fbf9e8f4f75a0d82a16b8bb648de0e9484f26`
  - 原 owner `Codex2`／reviewer `Antigravity6`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：資料 ready 時 MIP 與 CP-SAT 對相同 lease inputs 有一致 allow/deny 結果
  - 證據（test_file）：`tests/integration/test_net002_lease_disposition.py @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 3bd82e8a401d`，2026-09-04T12:05:42Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：None availability／cost fail closed 而 explicit zero 有不同語意
  - 證據（test_file）：`tests/integration/test_net002_lease_disposition.py @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 3bd82e8a401d`，2026-09-04T12:05:42Z — conclusion=success
- **A3**（可由測試證明／程式或文件交付）— `met_by_test_in_green_ci`
  - 條款：LEASE class 的 UI／policy／receipt 與 solver classification 同步
  - 證據（test_file）：`tests/integration/test_net002_lease_disposition.py @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 3bd82e8a401d`，2026-09-04T12:05:42Z — conclusion=success
- **A4**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：資料不 ready 時不寫裝飾性限制且只留下待人類簽署的 formal disposition
  - 證據（receipt_file）：`docs/evidence/ODP_NET002_LEASE_DISPOSITION_2026-09-03.md @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md @ c65bb54dd117` — 由本 PR 交付並存在於 merge commit
  - 註：資料未 ready，故只留待人類簽署的 formal disposition，未寫裝飾性限制。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- ODP-FR-NET-002 的 LEASE member 為 BLOCKED_BY_EVIDENCE（無 decider），符合『資料不 ready 時只留待人類簽署的 formal disposition』。

**缺口**

- 盤點觀察：同需求的 SEQUENCING member 為 DECIDED，decider 欄位為泛稱機構『Human/Ops (Architecture Board)』而非具名人員；此為既知的 decider 泛稱漏洞，reviewer 需自行裁定是否接受。
- 盤點觀察：候選 PR head commit 無 task trailer（head 為 merge commit）。

**下一步最小驗證動作**

- 若要求具名裁決者，查 docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md#odp-fr-net-002-sequencing 是否有具名簽署。

### `ODP-NETPLAN-DISCLOSURE-UI-E2E-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1174](https://github.com/alfloop-dev/odayplus/pull/1174) — [ReviewBus] ODP-NETPLAN-DISCLOSURE-UI-E2E-001 在 Operator UI 顯示 NetPlan 未建模限制並完成 approval E2E，state `MERGED`，merged `2026-09-04T05:49:33Z`
- 精確 head：`c71599aa116e1bd5a73bf0be005b34163bd0405e`；merge：`1edb2f834cbf38ccd489cd999802098076e891b7`
- 候選映射：`consistent`
  - PR body task id：`ODP-NETPLAN-DISCLOSURE-UI-E2E-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude`／評審人 `Antigravity`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T04:57:57Z — Approved by assigned reviewer Antigravity
- 原任務定義來源：`.orchestrator/task-briefs/odp_netplan_disclosure_ui_e2e_001.md`（生成於 2026-09-04T05:49:37Z）
  - brief 宣告 sha256 `2017dbd9c16647fedf2930248a72677a71e9f64b6c9be92dd8566184db53a263`；重算檔案 sha256 `3b5dd11c5e0153effbfdeead057cf60c4f1b382e89dcef48fbb6474222e86dc5`
  - 原 owner `Claude`／reviewer `Antigravity`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：Operator 無法把 unmodelled classes 誤讀為已驗證且主案／替代案都可見
  - 證據（test_file）：`tests/governance/test_netplan_disclosure_policy.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`apps/web/features/operator/network/__tests__/RebalanceDisclosurePartition.test.tsx @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ c71599aa116e`，2026-09-04T04:59:59Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：blocked 類別沒有可繞過 approval control 的 UI 路徑
  - 證據（test_file）：`tests/integration/test_netplan_disclosure_ui_e2e.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/contract/test_operator_network_rebalance_api.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ c71599aa116e`，2026-09-04T04:59:59Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：acknowledgement 顯示影響並要求具權限 actor 與非空 reason
  - 證據（test_file）：`tests/integration/test_netplan_disclosure_ui_e2e.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ c71599aa116e`，2026-09-04T04:59:59Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：E2E 從 production-required CP-SAT solve 驗證 UI 與 durable approval receipt
  - 證據（test_file）：`tests/integration/test_netplan_disclosure_ui_e2e.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`modules/netplan/tests/test_netplan_production_execution.py @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/e2e/operator-network-rebalance.spec.ts @ 1edb2f834cbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product-e2e-gate @ c71599aa116e` — conclusion=success；tests/e2e/operator-network-rebalance.spec.ts 具名列於該 head 的 delivery_toolchain/e2e/run_product_e2e.sh playwright spec 清單內，確由此 job 執行。
  - 證據（ci_check）：`check-run product @ c71599aa116e` — conclusion=success；tests/integration/test_netplan_disclosure_ui_e2e.py 與 modules/netplan/tests/test_netplan_production_execution.py 落在 product job 的 tests／modules 收集路徑內。
  - 證據（ci_execution_binding）：`本條款三個測試檔的 job 歸屬` — product-e2e-gate 不做路徑式收集，只跑寫死的 playwright spec 清單與 PYTEST_NODE_IDS 固定清單；兩個 pytest 檔都不在 PYTEST_NODE_IDS 內，因此它們由 product job 而非 product-e2e-gate 執行。前輪把三個檔一律綁 product-e2e-gate 是錯誤的。
  - 註：E2E 由 product-e2e-gate 於精確 head 執行並結論 success；本盤點未取 job log。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 的 product-e2e-gate check 為 success。

**缺口**

- 盤點觀察：E2E 的 DOM 層行為只有 CI 結論可依；本盤點依規定未重跑測試。
- 第 4 輪更正（結論不變）：A4 的三個測試檔原本一律綁 product-e2e-gate。該 job 不做路徑式收集，只跑寫死的 playwright spec 清單與 PYTEST_NODE_IDS；已拆綁為 spec.ts→product-e2e-gate、兩個 pytest 檔→product。

### `ODP-OPS002-DECISION-COMMENTS-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1158](https://github.com/alfloop-dev/odayplus/pull/1158) — [ReviewBus] ODP-OPS002-DECISION-COMMENTS-001 補齊 OPS-002 與任務／決策綁定的 durable comments，state `MERGED`，merged `2026-09-03T14:36:11Z`
- 精確 head：`54e8c8f37d9de6f3e60f423dd22a8ffb7f3a76f0`；merge：`c1371572af637ab1598370a495a126a4cddfba65`
- 候選映射：`consistent`
  - PR body task id：`ODP-OPS002-DECISION-COMMENTS-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Antigravity3`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T14:04:50Z — Approved by assigned reviewer Antigravity3
- 原任務定義來源：`.orchestrator/task-briefs/odp_ops002_decision_comments_001.md`（生成於 2026-09-03T15:13:32Z）
  - brief 宣告 sha256 `1ce69407909e69dd05ae20963a4f083d24d09164e10c64289abb771501b8c812`；重算檔案 sha256 `e8de9870bf6d530bcb137b48b70d1e392099fc5253335e41d32a94ed0fba78e7`
  - 原 owner `Antigravity2`／reviewer `Codex`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：comment 綁定 canonical task/decision id 與 tenant 且跨租戶 fail closed
  - 證據（test_file）：`tests/integration/test_operator_comments_api.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`apps/api/app/routes/operator_modules/comments.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`modules/opsboard/application/comments.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`shared/infrastructure/persistence/operator_comments.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 54e8c8f37d9d`，2026-09-03T14:06:45Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：create/list 與允許的 edit semantics 都留下 actor/time audit
  - 證據（test_file）：`tests/integration/test_operator_comments_api.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`infra/db/migrations/000017_durable_operator_comments.sql @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 54e8c8f37d9d`，2026-09-03T14:06:45Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：comment 不能修改或偽裝 approval decision
  - 證據（test_file）：`tests/integration/test_operator_comments_api.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 54e8c8f37d9d`，2026-09-03T14:06:45Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：API→DB→Operator UI 測試含空狀態／權限拒絕／持久化 readback
  - 證據（test_file）：`tests/integration/test_operator_comments_api.py @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`apps/web/features/operator/__tests__/DecisionCommentsPanel.test.tsx @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`apps/web/features/operator/__tests__/GovernanceWorkspace.test.tsx @ c1371572af63` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 54e8c8f37d9d`，2026-09-03T14:06:45Z — conclusion=success
  - 註：API→DB→UI 三層各有測試；UI 層為 vitest 元件測試而非部署後瀏覽器旅程。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 六個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 盤點觀察：身分不一致：task-review-gate 記核准者 Antigravity3、commit trailer 記 LLM-Agent Codex2 / Reviewer Antigravity3，但 task brief（2026-09-03 產生）記 owner Antigravity2 / reviewer Codex。推定為核准後 reviewer 輪替導致 brief 覆寫，需 reviewer 裁定以何者為準。

**下一步最小驗證動作**

- 以 task-review-gate 的時間戳為準判定當時核准者身分。

### `ODP-PRICE006-BANDIT-GATED-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1179](https://github.com/alfloop-dev/odayplus/pull/1179) — [ReviewBus] ODP-PRICE006-BANDIT-GATED-001 同一交付完成 PRICE-006 Bandit 與 production activation Gate，state `MERGED`，merged `2026-09-04T03:35:17Z`
- 精確 head：`282a88fb882cce53c88ef8ac3369ea0676c3d20d`；merge：`470f495d957edc38d330a624cf4d56618cbfb4e2`
- 候選映射：`consistent`
  - PR body task id：`ODP-PRICE006-BANDIT-GATED-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Antigravity2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T02:42:26Z — Approved by assigned reviewer Antigravity2
- 原任務定義來源：`.orchestrator/task-briefs/odp_price006_bandit_gated_001.md`（生成於 2026-09-04T03:38:26Z）
  - brief 宣告 sha256 `2bd3a431bef5f72ea656d533f1145c33780b34e1f72bcd473dbc727d334de6b5`；重算檔案 sha256 `df1916986beadd514c5ed8a4c24d7f19ca9e2290c5042f03ff498c30d642a748`
  - 原 owner `Codex2`／reviewer `Antigravity2`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：沒有有效 gate 時 bandit 絕不改變 production 價格
  - 證據（test_file）：`tests/integration/test_priceops_bandit_gating.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 282a88fb882c`，2026-09-04T02:49:48Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：offline/shadow replay 可由固定 seed 與 immutable input 重現
  - 證據（test_file）：`tests/integration/test_priceops_bandit_gating.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 282a88fb882c`，2026-09-04T02:49:48Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：activation receipt 綁 policy version／actor／experiment／guardrails／rollback target
  - 證據（test_file）：`tests/contract/test_price_exploration_gate_schema.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_price_exploration_gate_migration.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 282a88fb882c`，2026-09-04T02:49:48Z — conclusion=success
  - 證據（ci_execution_binding）：`tests/contract/test_price_exploration_gate_schema.py → check-run product @ 282a88fb882c` — 該檔帶 requires_live_env 標記，被 product job 主 step 排除，由同 job 的資料庫 step （-m "requires_live_env and not requires_postgis" tests/contract tests/ops tests/integration）執行；該檔無 requires_postgis 標記。product check 於該 head conclusion=success。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：正反向 production-entry 測試證明 gate 真會阻擋且 bandit 與 gate 不可分拆發布
  - 證據（test_file）：`tests/integration/test_priceops_bandit_gating.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_official_real_estate_postgresql.py @ 470f495d957e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 282a88fb882c`，2026-09-04T02:49:48Z — conclusion=success
  - 證據（ci_step）：`ci.yml product job` — 掛 postgis/postgis:16-3.5 service，另有專步跑 tests/integration/test_official_real_estate_postgresql.py
  - 證據（ci_execution_binding）：`tests/integration/test_official_real_estate_postgresql.py → check-run product @ 282a88fb882c` — 該檔帶 requires_live_env 標記，被 product job 主 step 排除，由 product job 的具名 PostgreSQL 16 step 直接執行。product check 於該 head conclusion=success。
  - 註：本盤點未取 job log。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 六個宣告 artifact 在 merge commit 全存在；task-review-gate 核准者 Antigravity2 與 brief reviewer 一致。

**缺口**

- 盤點觀察：commit trailer 的 Reviewer 記 Claude2，與 gate 的 Antigravity2 不同；以 gate 為準。
- 第 4 輪補查（結論不變）：A3／A4 的測試檔同樣帶 requires_live_env 標記，由 product job 的資料庫 step 與具名 PostgreSQL 16 step 執行，已補上 step 層綁定。

### `ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1109](https://github.com/alfloop-dev/odayplus/pull/1109) — [ReviewBus] ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001 修正 Runtime Release build handoff 的 masked s，state `MERGED`，merged `2026-09-01T14:49:31Z`
- 精確 head：`bdd277568d930757243583336a577a2492b67ca6`；merge：`640e35415aa33d5d53af21a8a527431b8f751cea`
- 候選映射：`consistent`
  - PR body task id：`ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity3`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-01T14:24:32Z — Approved by assigned reviewer Antigravity3
- 原任務定義來源：`.orchestrator/task-briefs/odp_release_build_handoff_snapshot_rollback_wiring_001.md`（生成於 2026-09-01T14:52:12Z）
  - brief 宣告 sha256 `c3f3aed17977a7f85b26c6dd9112092a2a8dc17f449cfd0b269f518c0fa29971`；重算檔案 sha256 `5ab278ca6051200fdbce3658f7689765804cfb469c6af4085fee7b4e5fb3ba79`
  - 原 owner `Claude2`／reviewer `Antigravity3`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（3 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明／程式或文件交付／runtime／部署）— `met_by_receipt`
  - 條款：workflow build handoff 傳入並驗證 approved masked snapshot file/id/uri/content sha；workflow build handoff 傳入並驗證完整上一核准 release manifest 且 canonical digest 可重算；缺任一 binding 必須 fail closed；補 focused contract tests 與現場可讀 evidence；不得新增第二套 workflow 或 deployment path；既有 Runtime Release deploy phase 只接受 immutable digest 與 signed Supervisor lease
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/README.md @ 640e35415aa3` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verification-transcript.txt @ 640e35415aa3` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/verify_build_handoff_wiring.py @ 640e35415aa3` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_build_release_handoff.py @ 640e35415aa3` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 640e35415aa3` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ bdd277568d93`，2026-09-01T14:26:34Z — conclusion=success
  - 證據（receipt_field）：`docs/evidence/runtime/ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001/README.md @ 640e35415aa3` — 結論記為 PASS：build handoff 傳入並驗證 Schema v2 masked data snapshot（id/uri/content_sha256/data_contract_digest/masked=true）與上一核准 release manifest；缺任一 binding fail closed；一個 binding 有兩個來源時同樣 fail closed；deploy phase 仍只接受 immutable digest 與 signed Supervisor lease
  - 註：本任務的 acceptance 是單一長條款，涵蓋 binding 驗證、fail-closed、focused tests、evidence 與「不新增第二條 workflow」。收據附可重跑的 verify 腳本與逐步 transcript，非只有結論句。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- docs/evidence/runtime/…/ 內有 verification-transcript.txt；掃出的 sha256:dddd…/eeee… 佔位字串出現在 pytest 參數化測試名稱（fail-closed 負向案例），非偽造的部署收據值。

**缺口**

- 盤點觀察：Acceptance 提到『既有 Runtime Release deploy phase 只接受 immutable digest 與 signed Supervisor lease』屬既有機制不變性，本盤點未重驗。

### `ODP-RELEASE-GATE-FIXTURE-STAGING-002` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1212](https://github.com/alfloop-dev/odayplus/pull/1212) — [ReviewBus] ODP-RELEASE-GATE-FIXTURE-STAGING-002 實作 gate E2E fixture 解耦，接續只有唯讀檢查的001，state `MERGED`，merged `2026-09-06T01:54:20Z`
- 精確 head：`a9e7853f9ed910303eb6b7fa1d81c1e1b46c5787`；merge：`17393dd447e9b25978a83641cabf7cd77951f24a`
- 候選映射：`consistent`
  - PR body task id：`ODP-RELEASE-GATE-FIXTURE-STAGING-002`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review`／負責人 `Codex2`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-06T01:58:14Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_release_gate_fixture_staging_002.md`（生成於 2026-09-06T01:58:26Z）
  - brief 宣告 sha256 `409030a5af8192758638626f3c3efb35e093c52082fde231aaf959b9746e5160`；重算檔案 sha256 `77b5784716b7e4197f52d237dc7ebf5f00da384e44dcc5cf2ccd906238b7221d`
  - 原 owner `Codex2`／reviewer `Codex`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：只修正 release gate E2E 的 fixture 耦合，沿現有checker，不修改validator/lease/workflow/真正gate證據。PR1205 目前在 tests/e2e/test_release_gate_registry.py 的分階段測試改動若留到 evidence-only E，會違反 C→E ancestry；這份必要測試修補必須先合入新candidate C。不能把任何產品/測試改動包成evidence。
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/ODP_RELEASE_GATE_FIXTURE_2026-09-06.md @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a9e7853f9ed9` — conclusion=success；tests/e2e/test_release_gate_registry.py 落在 product job 的 tests 收集路徑內。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → product（非 product-e2e-gate）` — product-e2e-gate 於該 head 的 make 前置目標 release-gate-registry 只執行 delivery_toolchain/e2e/check_release_gate_registry.py 這支檢查器本身，並不執行同名的 pytest 檔；該檔也不在 PYTEST_NODE_IDS 清單內。實際收集執行它的是 product job（該 head conclusion=success）。前輪綁 product-e2e-gate 是錯誤的。
  - 註：只動 E2E fixture 與說明文件，未改 validator／lease／workflow。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：從最新origin/dev乾淨taskworktree開始。針對blocking_gates/dev/staging/prod/no-matched-target CLI case，以明確local/deepcopied fixture 設定 rollout §6.1 dev gates0/1/4、staging2、prod3/5/6，不再硬假設 committed registry 永久全部 target=dev。保持 committed registry 的結構/來源綁定與fail-closed整合測試，不刪測試/放寬斷言。
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a9e7853f9ed9` — conclusion=success；tests/e2e/test_release_gate_registry.py 落在 product job 的 tests 收集路徑內。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → product（非 product-e2e-gate）` — product-e2e-gate 於該 head 的 make 前置目標 release-gate-registry 只執行 delivery_toolchain/e2e/check_release_gate_registry.py 這支檢查器本身，並不執行同名的 pytest 檔；該檔也不在 PYTEST_NODE_IDS 清單內。實際收集執行它的是 product job（該 head conclusion=success）。前輪綁 product-e2e-gate 是錯誤的。
  - 註：dev/staging/prod 與 no-matched-target 的 CLI case 以 local fixture 設定，committed registry 的結構綁定與 fail-closed 整合測試保留。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：逐一檢查PR1205目前tests/e2e/test_release_gate_registry.py delta，必要語意在本task先涵蓋，不搬C04manifest或真實gate mutation。不造成與未更新canonical registry的矛盾；測試可用fixture但實際registry檢查必須保留。
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ a9e7853f9ed9` — conclusion=success；tests/e2e/test_release_gate_registry.py 落在 product job 的 tests 收集路徑內。
  - 證據（ci_execution_binding）：`tests/e2e/test_release_gate_registry.py → product（非 product-e2e-gate）` — product-e2e-gate 於該 head 的 make 前置目標 release-gate-registry 只執行 delivery_toolchain/e2e/check_release_gate_registry.py 這支檢查器本身，並不執行同名的 pytest 檔；該檔也不在 PYTEST_NODE_IDS 清單內。實際收集執行它的是 product job（該 head conclusion=success）。前輪綁 product-e2e-gate 是錯誤的。
- **A4**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：一次修改完成後只跑該E2E fixture檔和必要checker focused test一次，完整CI交PR；中文PR與evidence說明先code入C再build再evidence E，不聲稱測試綠就是已部署或GO。
  - 證據（receipt_file）：`docs/evidence/ODP_RELEASE_GATE_FIXTURE_2026-09-06.md @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 註：「不聲稱測試綠就是已部署或 GO」為否定要求；evidence 文件內即如此聲明。CI 執行次數本盤點未核。
- **A5**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：agy實作、root Codex獨立整合review；使用worker_commit/task_finalize，合併後後續candidate任務不得再改此testfile或沿旧PR含code修改的first-parent歷史作E。
  - 證據：無
  - 註：實作者與 review 分工屬執行過程。
- **A6**（執行過程約束／程式或文件交付）— `met_by_delivered_artifact`
  - 條款：接續ODP-RELEASE-GATE-FIXTURE-STAGING-001：該task因root誤設mutates_canonical=false只完成唯讀檢查且被supersede archive，沒有程式/測試/PR交付。本task是唯一實作接續；務必產生真實code與PR，不將原archive done視為本項已修。沿原scope，不新增部署機制。
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/ODP_RELEASE_GATE_FIXTURE_2026-09-06.md @ 17393dd447e9` — 由本 PR 交付並存在於 merge commit
  - 註：接續 ODP-RELEASE-GATE-FIXTURE-STAGING-001（該任務被 supersede 且無程式交付）；本 PR 確實帶有真實測試與文件變更，符合「務必產生真實 code 與 PR」。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A4(process_constraint_unverifiable)、A5(process_constraint_unverifiable)

**查證發現**

- 兩個 artifact 在 merge commit 存在；acceptance 明文『不聲稱測試綠就是已部署或 GO』，屬純測試 fixture 解耦。

**缺口**

- 盤點觀察：Acceptance 提及前身 ODP-RELEASE-GATE-FIXTURE-STAGING-001 因 mutates_canonical 設定錯誤而只有唯讀檢查並被 supersede；該前身不在本 38 項清單內，映射時勿混用。
- 第 4 輪更正（結論不變）：A1-A3 的 tests/e2e/test_release_gate_registry.py 原綁 product-e2e-gate。該 job 的 make 前置目標只執行 check_release_gate_registry.py 這支檢查器本身，不執行同名 pytest 檔；已改綁真正收集它的 product check（該 head success）。

### `ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1030](https://github.com/alfloop-dev/odayplus/pull/1030) — [ReviewBus] ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001 整理真實 build artifact 與 gate registry 的 exact bindi，state `MERGED`，merged `2026-08-26T20:34:11Z`
- 精確 head：`74078faecfdd6238909e5157271604074f7eaf96`；merge：`fe698e88d91678b18cfd5356a9c10be08dfa5186`
- 候選映射：`consistent`
  - PR body task id：`ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-26T19:59:36Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_release_manifest_live_artifact_reconcile_001.md`（生成於 2026-08-26T20:34:33Z）
  - brief 宣告 sha256 `53fdca34735e8bbf056ae9b09e3b44177f98d3c8e0781a0a0dfc6c6dbc2b5a95`；重算檔案 sha256 `9f0542e2fd07e26f9e008999c0e300a0b19c3353f8200be53859a145e21c2f59`
  - 原 owner `Claude2`／reviewer `Codex`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（2 筆引用）

**逐條 acceptance 證據對應**

- **A1**（runtime／部署／可由測試證明）— `met_by_receipt`
  - 條款：以 run 33003734045 的 manifest 綁定 dev SHA ebc4fca5c2dd5871275aee39a18406dd67464f04；manifest digest、四個 image digest、SBOM/signature refs 可驗證；registry candidate 與 manifest 一致；未有七道 gate receipt 時維持 no-go；不啟用第三方來源、不進行 deploy；提交中文 PR與驗證證據。
  - 證據（receipt_file）：`docs/evidence/gates/RELEASE_MANIFEST.json @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/gates/RELEASE_GATE_REGISTRY.json @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/release-phase-receipt.json @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/release-environment-receipt.json @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/runtime-release-images.json @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/verification-transcript.txt @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/runtime/ODP-RELEASE-MANIFEST-LIVE-ARTIFACT-RECONCILE-001/verify_live_artifact_binding.py @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_manifest.py @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/e2e/test_release_gate_registry.py @ fe698e88d916` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 74078faecfdd`，2026-08-26T20:02:08Z — conclusion=success
  - 註：本任務的 acceptance 是單一長條款。交付物同時含 manifest、gate registry、四份 runtime 收據與可重跑的 verify 腳本，且 acceptance 明定「未有七道 gate receipt 時維持 no-go、不啟用第三方來源、不進行 deploy」——這些是否定要求，本盤點未查到本任務簽發任何 deploy 或來源啟用。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 在 merge commit 複驗 docs/evidence/gates/RELEASE_MANIFEST.json：candidate_sha=ebc4fca5c2dd5871275aee39a18406dd67464f04（已確認在 origin/dev 歷史中），manifest_digest 與四類 component image 皆為真實 Artifact Registry digest，無佔位值。
- Acceptance 明文『不啟用第三方來源、不進行 deploy』，屬證據對帳任務。

**缺口**

- 盤點觀察：Acceptance 綁定的 workflow run 33003734045 本身未在本盤點回查（唯讀 run 查詢未執行）。

**下一步最小驗證動作**

- 若需完整鏈路，唯讀查 Actions run 33003734045 的 artifact 與 manifest digest 是否一致。

### `ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1050](https://github.com/alfloop-dev/odayplus/pull/1050) — [ReviewBus] ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001 Release rollback 與 masked snapshot handoff，state `MERGED`，merged `2026-08-27T15:34:25Z`
- 精確 head：`55de004a8559442ed4ba9cedf800e692bb0b1116`；merge：`d41b9328137cef1eceeb5990e8e0bb5963414682`
- 候選映射：`consistent`
  - PR body task id：`ODP-RELEASE-ROLLBACK-DATA-HANDOFF-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Codex2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-27T15:15:22Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/odp_release_rollback_data_handoff_001.md`（生成於 2026-08-27T15:36:46Z）
  - brief 宣告 sha256 `d105d68baec59d4d208fa92ca1f0fbe2038524d59fff082bd05c7cd848260bac`；重算檔案 sha256 `151d91d365a853aa15de7f95fe03eb6e3398b6d09da4faec802531ce35abdfc6`
  - 原 owner `Codex`／reviewer `Codex2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（2 筆引用）

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：沿用RELEASE_MANIFEST.json與build_release_handoff作唯一immutable handoff不得新增sidecar manifest或workflow
  - 證據（test_file）：`tests/release/test_build_release_handoff.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/release/release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`tests/release/test_release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 註：沿用單一 handoff，未新增 sidecar manifest。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：新schema綁定本次masked data snapshot id uri content sha256 data contract digest與masked=true
  - 證據（test_file）：`tests/release/test_release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：新schema綁定上一核准release id candidate sha manifest digest API Web exact image digests與上一snapshot pointer
  - 證據（test_file）：`tests/release/test_release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：rollback release必須是不同candidate且自身manifest digest與components驗證通過不得接受tag或手填摘要
  - 證據（test_file）：`tests/release/test_runtime_admission.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
- **A5**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：舊v1 manifest仍可讀供歷史稽核但新的staging production admission對缺rollback或snapshot資料fail closed
  - 證據（test_file）：`tests/release/test_runtime_admission.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
- **A6**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：同一candidate重建輸出byte deterministic且任一snapshot/image/digest竄改使manifest自驗失敗
  - 證據（test_file）：`tests/release/test_release_manifest_cli.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_manifest.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
  - 註：byte-deterministic 重建與竄改自驗失敗由 manifest 測試覆蓋。
- **A7**（可由測試證明／執行過程約束）— `met_by_test_in_green_ci`
  - 條款：不修改Runtime Release workflow或staging lifecycle且中文PR與focused tests通過
  - 證據（test_file）：`tests/release/test_build_release_handoff.py @ d41b9328137c` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 55de004a8559`，2026-08-27T15:15:43Z — conclusion=success
  - 證據（brief_verification）：`task brief verification 欄位` — `uv run --frozen pytest -q tests/release/test_release_manifest.py tests/release/test_release_manifest_cli.py tests/release/test_build_release_handoff.py tests/release/test_runtime_admission.py` 與對應 ruff check
  - 註：本任務 brief 帶有具名 verification 命令，四支測試檔皆由本 PR 交付。本盤點未重跑。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- brief 明列兩條 Verification 指令（pytest 四個 release 測試檔與 ruff）；三個 artifact 在 merge commit 存在；acceptance 明文『不修改 Runtime Release workflow 或 staging lifecycle』。

**缺口**

- 盤點觀察：候選 PR head commit 無 task trailer（head 為 merge commit）。

### `ODP-REQ-DISPOSITION-GOVERNANCE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1146](https://github.com/alfloop-dev/odayplus/pull/1146) — [ReviewBus] ODP-REQ-DISPOSITION-GOVERNANCE-001 建立 MUST requirement amendment／waiver 的可機讀 disposition gate，state `MERGED`，merged `2026-09-03T13:29:29Z`
- 精確 head：`7c889b2117803b8fe419f32e7ace84d1ce38b5e4`；merge：`61ef9183d7c8ea2af4b5d7d625b1513dc683212f`
- 候選映射：`consistent`
  - PR body task id：`ODP-REQ-DISPOSITION-GOVERNANCE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity3`／評審人 `Codex2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T12:54:31Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/odp_req_disposition_governance_001.md`（生成於 2026-09-03T13:47:11Z）
  - brief 宣告 sha256 `4cc0990284600f3287b6fa2e2f46d18cac051d3d757d221c059f650af055c477`；重算檔案 sha256 `7984379de24bd84a8cb1911a6731fd2b48bf9d83e594f7bf56bd633fcca19d6f`
  - 原 owner `Antigravity3`／reviewer `Codex2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（5 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：五個 disposition 狀態與合法轉移有 schema 驗證
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 61ef9183d7c8` — 由本 PR 交付並存在於 merge commit
  - 證據（delivered_file）：`delivery_toolchain/governance/check_requirement_members.py @ 61ef9183d7c8` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ 7c889b211780`，2026-09-03T12:43:57Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_requirement_members.py → check-run orchestrator @ 7c889b211780` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：本 PR 未帶 docs/evidence/ 收據，交付為 governance checker 與其測試。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：未實作 MUST 缺 formal decision ref／decider／scope／risk owner／expiry／reopen trigger 時 CI 必須失敗
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 61ef9183d7c8` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ 7c889b211780`，2026-09-03T12:43:57Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_requirement_members.py → check-run orchestrator @ 7c889b211780` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
- **A3**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：既有 absent 仍只是索引且不得冒充裁決
  - 證據（delivered_file）：`docs/governance/ODP_REQUIREMENT_DISPOSITIONS.md @ 61ef9183d7c8` — 由本 PR 交付並存在於 merge commit
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：負向測試證明 checker 會拒絕 AI 自簽與過期 waiver
  - 證據（test_file）：`delivery_toolchain/governance/test_check_requirement_members.py @ 61ef9183d7c8` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run orchestrator @ 7c889b211780`，2026-09-03T12:43:57Z — conclusion=success
  - 證據（ci_execution_binding）：`delivery_toolchain/governance/test_check_requirement_members.py → check-run orchestrator @ 7c889b211780` — orchestrator job 的指令為 uv run pytest -m "not requires_live_env" .orchestrator delivery_toolchain scripts tests/tooling，delivery_toolchain/ 在其收集路徑內；該檔無 requires_live_env 標記，故確由該成功 check 收集執行。
  - 註：負向測試證明 checker 會拒絕 AI 自簽與過期 waiver。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 四個 artifact（含 checker 與其測試、governance 文件）在 merge commit 全存在。

**缺口**

- 盤點觀察：Acceptance『負向測試證明 checker 會拒絕 AI 自簽與過期 waiver』依 CI 結論，未重跑。
- 第 4 輪更正（結論不變）：A1／A2／A4 的 delivery_toolchain/governance/test_check_requirement_members.py 同上 kind 正規化與收集依據。

### `ODP-ROLE-PROVIDER-CODEX-REVIEW-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1221](https://github.com/alfloop-dev/odayplus/pull/1221) — [ReviewBus] ODP-ROLE-PROVIDER-CODEX-REVIEW-001 落實 Agy／Claude 實作、Codex Astra ultra 審查的單一派工政策，state `MERGED`，merged `2026-09-06T05:56:17Z`
- 精確 head：`c1a382416e4423e22f3bf5dfe86a37d93597e583`；merge：`64f3b2399442e8cd7531284d1e7a3e33bcc3ca9a`
- 候選映射：`consistent`
  - PR body task id：`ODP-ROLE-PROVIDER-CODEX-REVIEW-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity2`／評審人 `Codex`
- 精確 head CI：skipped=3、success=4；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-06T05:47:35Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_role_provider_codex_review_001.md`（生成於 2026-09-06T05:56:50Z）
  - brief 宣告 sha256 `a91caa81fb9707dcc5bc79f76f917a3b7242343e326ce338b98edcb835f8f224`；重算檔案 sha256 `d3c2d0e117857a535e207589f0461a2edd30434e0a81225c694e9cfdd55f8440`
  - 原 owner `Antigravity2`／reviewer `Codex`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（1 筆引用）

**逐條 acceptance 證據對應**

- **A1**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：使用者2026-09-06再次明確釐清：所有實作都交Agy/Claude，Codex只review。實作包括修bug、整合程式碼、IaC、workflow、文件、測試與部署工具修改；不存在『Codex可做整合實作』例外。從最新origin/dev延伸唯一assignment viability/dispatch，不新增scheduler/router/service/輪詢腳本。
  - 證據：無
  - 註：使用者釐清的角色政策屬指示本身，不由交付物驗證。
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：建立單一可配置role/provider eligibility：所有可自動執行的owner/helper工作只允許antigravity/claude，review只允許codex。不得以integration/verification/runtime_release/rollout或缺失/未知task_class繞過Codex禁止實作；純review驗證不屬實作。human_gate/non_dispatchable與既有部署授權保持fail closed，不因owner可用就取得human GO。既有owner_provider_preference僅在合格集合排序，不複製selector。
  - 證據：無
  - 註：單一 role/provider eligibility 政策由本 PR 的 .orchestrator 程式變更實作；精確 head 的 product／product-e2e-gate／performance-gate 為 skipped（scope skip），實跑的是 orchestrator check。
- **A3**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：共同eligibility必须覆蓋initial assignment、repair/reconcile、reviewer conflict/failover、owner failure帶入reviewer fallback、review churn rotation、helper claim、logical→physical slot映射及queued pre-launch重新核對。Codex忙/quota/unknown時review等待，不能fallback Agy/Claude；也不能只改canonical reviewer後被舊reconcile改回。未知provider/malformed enabled policy fail closed，無政策維持原行為。
  - 證據：無
  - 註：eligibility 覆蓋面（initial assignment、repair、failover、rotation、slot 映射、queued 重核）由同一政策模組實作。
- **A4**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：保留owner!=reviewer及account-pool獨立性。新政策前Codex已寫過的提交保留真實作者身份，須另一獨立Codex pool review或明確等待，不洗owner規避自審。Codex在途WIP先保存窄scope checkpoint再handoff Agy/Claude，不再接新實作；已提交review身份/歷史approval/archive/receipt不能被新policy追溯改寫，approved/finalize owner保持凍結。不得清除reopen/cooldown/lease，不雙派。
  - 證據：無
  - 註：owner!=reviewer 與 account-pool 獨立性保持；歷史身分不追溯改寫。
- **A5**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：Codex既有adapter新增明確model_reasoning_effort設定並透過-c model_reasoning_effort="ultra"傳給codex exec；保持providers.codex.codex.model=gpt-6-astra為模型唯一配置，兩個Codex pool及其physical slots都須繼承且不能被launch模型偏好覆蓋回Luna。不得另建CLI wrapper、另寫個人config或變更auth/sandbox。schema/example/invalid effort failure與command-array regression一併完成。
  - 證據：無
  - 註：Codex adapter 的 model_reasoning_effort 設定與 schema/example/invalid-effort 回歸。
- **A6**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：本機codex-cli0.147.0 /home/lupin/.codex/models_cache.json fetched2026-09-06T03:44:11Z列gpt-6-astra支持ultra，且codex -c model_reasoning_effort="ultra" features list已成功。不要以公開API只列max或舊docs只列xhigh擅自換成xhigh/max；本task不啟動假API probe、無權讀auth secrets。
  - 證據：無
  - 註：本機 codex-cli 能力查核為執行當下的一次性觀察，交付物內無法重現。
- **A7**（可由測試證明／執行過程約束）— `partially_met`
  - 條款：整批修改完成只跑影響範圍focused tests一次及exact-head PR CI；必要negative tests涵蓋review不會逃回Agy/Claude、unknown/empty codex capacity、pool independence、queued policy change、helper/rotation、owner fallback、Codex exact model+effort。不要重跑無關完整suite、不要以mock生成的command當live執行證據。
  - 證據（test_file）：`.orchestrator/test_role_provider_policy.py @ 64f3b2399442` — 由本 PR 交付（+2006 行）並存在於 merge commit。條款逐項點名的 negative tests 皆可具名定位：review 不會逃回 Agy/Claude → test_reviewer_selection_never_falls_back_to_an_implementation_lane；unknown/empty codex capacity → test_unmeasurable_codex_capacity_does_not_release_review_to_another_provider、test_exhausted_codex_pools_make_review_wait_rather_than_reassign；pool independence → test_account_pool_independence_survives_a_single_provider_review_rule；queued policy change → test_queued_event_is_skipped_when_the_policy_stopped_permitting_it；helper/rotation → test_helper_claim_is_filtered_by_the_same_rule_as_owner、test_review_churn_rotation_rotates_owner_without_moving_review_off_codex；owner fallback → test_owner_failure_fallback_does_not_drag_the_reviewer_off_codex；Codex exact model+effort → test_codex_command_carries_the_configured_model_and_ultra_effort、test_invalid_effort_fails_the_delivery_instead_of_running_at_a_default。
  - 證據（ci_check）：`check-run orchestrator @ c1a382416e44`，2026-09-06T05:35:33Z — conclusion=success；.orchestrator/ 在該 job 的收集路徑內，該測試檔確由此成功 check 執行。
  - 證據（ci_execution_binding）：`.orchestrator/test_role_provider_policy.py → check-run orchestrator @ c1a382416e44` — orchestrator job 指令收集 .orchestrator，該檔無 requires_live_env 標記；該 head 的 product／product-e2e-gate／performance-gate 為 scope skip，但本條款的測試不在那些 job 的範圍內，skip 不影響本條款。
  - 註：本條款可拆成兩半。測試半：條款點名的每一類 negative test 都能在交付的測試檔內具名定位，且該檔確由精確 head 上結論 success 的 orchestrator check 收集執行。過程半：『只跑影響範圍 focused tests 一次』『不要重跑無關完整 suite』『不以 mock 生成的 command 當 live 執行證據』約束的是當時怎麼執行，交付物與唯讀記錄無法獨立驗證，故整條記為 partially_met。前輪此條款只有一個 ci_check、未具名任何測試，屬於以綠色 job 泛稱代替條款證據，本輪更正。
- **A8**（執行過程約束／人類授權）— `process_constraint_unverifiable`
  - 條款：中文PR/收據區分code/test/merge與live。Claude/Agy完成實作，Codex獨立review與CI綁exacthead；合併後live config/既有rollout_supervisor_runtime.py操作交Claude/Agy受治理執行，Codex只做審查與讀取真實loaded digest/command/model/effort/連續健康loops驗證。當前code任務不可提前改canonical runtime；後續操作需明確同一任務phase/授權scope，不能自行啟用來源、簽Human GO或部署GCP。
  - 證據：無
  - 註：「不提前改 canonical runtime、不自行啟用來源、不簽 Human GO、不部署 GCP」是否定要求；本盤點未查到本任務簽發此類動作。live config 的實際套用屬後續授權範圍，不在本 PR。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A1(process_constraint_unverifiable)、A6(process_constraint_unverifiable)、A7(partially_met)、A8(process_constraint_unverifiable)

**查證發現**

- brief 的 Artifacts 為空，無法做 artifact 存在性檢查；acceptance 明文『當前 code 任務不可提前改 canonical runtime』，屬純 code/policy 交付。

**缺口**

- 盤點觀察：精確 head 有 3 個 check conclusion=skipped。
- 盤點觀察：commit trailer 無 Verified 欄位。
- 盤點觀察：Acceptance 要求的 live config 生效屬後續操作；code 合併不等於 live orchestrator config 已改（live config 為顯式覆蓋，程式預設值不會自動生效）。
- 第 4 輪更正：A7 原本只有一個 orchestrator ci_check、未具名任何測試，屬以綠色 job 泛稱代替條款證據。本輪已在 .orchestrator/test_role_provider_policy.py 內逐項定位條款點名的 negative tests；但該條款另含『只跑影響範圍 focused tests 一次、不重跑無關完整 suite、不以 mock 生成的 command 當 live 執行證據』這一段執行過程約束，交付物與唯讀記錄無法獨立驗證，故整條為 partially_met。

**下一步最小驗證動作**

- 映射時明記本項只涵蓋 code/policy，live rollout 需另有受治理執行證據。
- A7 的過程半若要驗證，需要當時的執行紀錄（跑了哪些測試、跑幾次）；本盤點唯讀範圍內無此紀錄，不推定。

### `ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1206](https://github.com/alfloop-dev/odayplus/pull/1206) — [ReviewBus] ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001 修正唯一 Runtime Release 的 dispatch ref 與 manifest CL，state `MERGED`，merged `2026-09-05T05:56:42Z`
- 精確 head：`8a7fe4b01270d5f5d95cd6996424612250bef372`；merge：`74530caf5bbf8ee3802df658e21a3ffdfca56f25`
- 候選映射：`consistent`
  - PR body task id：`ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-05T05:35:21Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_runtime_release_dispatch_cli_integration_001.md`（生成於 2026-09-05T05:58:22Z）
  - brief 宣告 sha256 `425162bf688be323d767fdbccca42c0aa855f35d09ecde3aa8f9d6aca45f1b56`；重算檔案 sha256 `bd378229b699a090724a07a3ec751f6f25780ecc7956bc32000ef3967d530c1e`
  - 原 owner `Antigravity`／reviewer `Codex`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（3 筆引用）

**逐條 acceptance 證據對應**

- **A1**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：從最新 origin/dev 乾淨 worktree 開始；與 Antigravity4 的 ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 僅交接 CLI patch，不修改其 worktree 或 gate 文件。
  - 證據：無
  - 註：worktree 起點與跨 task 交接屬執行過程。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：重現 baseline：env -u PYTHONPATH python3 delivery_toolchain/release/release_manifest.py --manifest 真實run33942097235 manifest --structure-only 回 cannot load Terraform egress contract verifier: No module named infra。修正正常CLI載入並新增無PYTHONPATH subprocess回歸，保留錯誤contract fail closed。
  - 證據（test_file）：`tests/release/test_release_manifest_cli.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_file）：`docs/evidence/ODP_RUNTIME_RELEASE_DISPATCH_CLI_INTEGRATION_2026-09-05.md @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
  - 註：無 PYTHONPATH 的 subprocess 回歸由 CLI 測試覆蓋。
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：GitHub官方workflow-dispatch ref只接受branch/tag；現行 dispatch_runtime_release payload ref=lease.candidate_sha 會遭422。修正既有bridge的ref選擇與精確版本驗證；不得降低lease candidate/manifest/target/CAS/nonce約束，不得自動force ref或新增workflow。
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_release_manifest.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：檢驗 _runtime_release_inputs 與唯一workflow所有deploy必填輸入、initial_release_recovery及sources-off語意；覆蓋實際payload與CLI鏈路，不能mock成任何ref都成功。
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
- **A5**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：若選既有dev ref，明確核對候選版/執行workflow SHA與允許evidence-only後代的既有規則，處理版本漂移；不能把浮動dev當exact approval，不得將舊image的證據綁成新code。
  - 證據（test_file）：`tests/release/test_release_manifest.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_probe_release_target_absence.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
- **A6**（runtime／部署／執行過程約束）— `process_constraint_unverifiable`
  - 條款：無 Secret Manager payload讀取、無真實lease簽发、無部署dispatch。default disabled保持。所有程式變更須說明對下一candidate/build的影響。
  - 證據（receipt_file）：`docs/evidence/ODP_RUNTIME_RELEASE_DISPATCH_CLI_INTEGRATION_2026-09-05.md @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 註：「無 Secret Manager 讀取、無真實 lease 簽發、無部署 dispatch、default disabled 保持」全為否定要求；本盤點未查到本任務簽發 lease 或 dispatch 的紀錄。
- **A7**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：修完一批跑focused tests一次，CI以實際PR head核對；中文PR交Claude獨立審查；不偽造Human/Ops GO、不新增安全掃描ignore。
  - 證據：無
  - 註：focused tests 執行次數與中文 PR 屬執行過程；中文 PR 可觀察。
- **A8**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：同一修復需要納入 admission registry handoff：code candidate C build之後以純證據descendant E承載registry；hosted admission固定讀事件GITHUB_SHA=E並沿用check_candidate_ancestry(C,E)，其餘lease/manifest/image/checkout身份保持C，不新增第二個registry或gate引擎。
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
  - 註：admission registry handoff 的 ancestry 檢查由 workflow 契約測試覆蓋。
- **A9**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：dispatch_ref若成為設定須同步既有config.schema.json（issuer additionalProperties=false）與config.example.json；只保留單一dispatch_ref字段，不新增無既有caller的ref別名兼容路徑。維持live config停用。
  - 證據：無
  - 註：config.schema.json 與 config.example.json 同步；live config 維持停用。
- **A10**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：遠端ref必須對應設定的github_repository，不以本機dev當遠端證據；包含本機/遠端不同tip與dispatch前後漂移回歸。hosted admission對收到的GITHUB_SHA再次驗證，遇非evidence-only drift拒絕。
  - 證據（test_file）：`tests/release/test_release_manifest_cli.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
  - 註：本機／遠端 tip 漂移回歸由 CLI 測試覆蓋。
- **A11**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：從PR1205交接其程式與fixtures更新（包含test_probe_release_target_absence.py）到本task，本task不修改gate evidence文件；據真實v2 artifact驗證CLI與fixtures獨立性，不沿用v1特例以掩盖問題。
  - 證據（test_file）：`tests/release/test_probe_release_target_absence.py @ 74530caf5bbf` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 8a7fe4b01270`，2026-09-05T05:32:36Z — conclusion=success
  - 註：自 PR #1205 交接的 fixtures 更新已納入本 PR。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A1(process_constraint_unverifiable)、A6(process_constraint_unverifiable)、A7(process_constraint_unverifiable)

**查證發現**

- 三個 artifact 在 merge commit 全存在；acceptance 明文『無 Secret Manager payload 讀取、無真實 lease 簽發、無部署 dispatch。default disabled 保持』。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-RUNTIME-RELEASE-SINGLE-PATH-001` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1010](https://github.com/alfloop-dev/odayplus/pull/1010) — [ReviewBus] ODP-RUNTIME-RELEASE-SINGLE-PATH-001 整合唯一 build-once Runtime Release 狀態機，state `MERGED`，merged `2026-08-25T15:34:07Z`
- 精確 head：`5f80756b84852f935625e502eca9fc038a6bbc9a`；merge：`5ae1e5cee8ef6b5047fa72f2426d2a1f42d9f9ce`
- 候選映射：`consistent`
  - PR body task id：`ODP-RUNTIME-RELEASE-SINGLE-PATH-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Claude`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-25T15:07:55Z — Approved by assigned reviewer Claude
- 原任務定義來源：`.orchestrator/task-briefs/odp_runtime_release_single_path_001.md`（生成於 2026-08-25T14:57:11Z）
  - brief 宣告 sha256 `aab45b921aad5fcfd92af57b8c5eb32d2f5a491526c3864bc39eef6192cd3dbe`；重算檔案 sha256 `e67e43e15436247ec6f713e08f2d7fe7980342ea167940e06ae116094166e5ff`
  - 原 owner `Codex`／reviewer `Claude`／brief 當下狀態 `review`
  - 其他任務 brief 記載的終態旁證：done（5 筆引用）

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：現有 Runtime Release 成為唯一入口
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 5f80756b8485`，2026-08-25T15:11:12Z — conclusion=success
  - 註：本 PR 未帶 docs/evidence/ 收據；唯一入口性質由 workflow 契約測試界定。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：build job 只執行一次且 deploy-by-digest
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 5f80756b8485`，2026-08-25T15:11:12Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：依序支援 dev/ephemeral staging/prod blue-green
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/release/test_runtime_admission.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 5f80756b8485`，2026-08-25T15:11:12Z — conclusion=success
  - 註：dev/ephemeral staging/prod blue-green 三階段以 workflow 契約與 admission 測試界定；此為 workflow 定義層，非已執行的部署。
- **A4**（可由測試證明／runtime／部署）— `partially_met`
  - 條款：各階段使用正確 admission 與 protected environments
  - 證據（test_file）：`tests/release/test_runtime_admission.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 5f80756b8485`，2026-08-25T15:11:12Z — conclusion=success
  - 註：各階段 admission 與 protected environments 的宣告由測試覆蓋；實際 protected environment 生效狀態屬 runtime，由 ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 的 github-environments-audit.json 另行佐證，不在本 PR。
- **A5**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：不存在第二套 proof/deploy 狀態機
  - 證據（test_file）：`tests/ops/test_deploy_workflow_contract.py @ 5ae1e5cee8ef` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 5f80756b8485`，2026-08-25T15:11:12Z — conclusion=success
  - 註：「不存在第二套狀態機」為否定要求，由契約測試覆蓋。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A4(partially_met)

**查證發現**

- 三個 artifact（deploy-dev.yml、workflow 契約測試、docs/deployment/）在 merge commit 全存在。

**缺口**

- A4（partially_met）：各階段 admission 與 protected environments 的宣告由測試覆蓋；實際 protected environment 生效狀態屬 runtime，由 ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 的 github-environments-audit.json 另行佐證，不在本 PR。
- 盤點觀察：Acceptance『依序支援 dev/ephemeral staging/prod blue-green』與『不存在第二套 proof/deploy 狀態機』為 repo 級不變量，本盤點未做全域重驗。
- 盤點觀察：brief 的 Status 停在 review（非 review_approved），但 task-review-gate 已 success 且有 5 份其他 brief 記其為 done。

### `ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1160](https://github.com/alfloop-dev/odayplus/pull/1160) — [ReviewBus] ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001 依 SITE-001 資料證據實作或正式處置 Brand Transfer／Format Conver，state `MERGED`，merged `2026-09-03T16:51:21Z`
- 精確 head：`ffe02988a1b4def412090c6b422e6efb26081d9f`；merge：`9f53418df41e558c8f953c801dd8fd1f25f77b5b`
- 候選映射：`consistent`
  - PR body task id：`ODP-SITE001-MISSING-COMPONENTS-DISPOSITION-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Antigravity5`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T16:22:20Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/odp_site001_missing_components_disposition_001.md`（生成於 2026-09-03T16:52:29Z）
  - brief 宣告 sha256 `1ff48b5e85805405fef3388e036b1376a37a9b1c9c74a3d5a1e18e6d5e8d177b`；重算檔案 sha256 `eec0f05e81937ca8f806ce9db9682aa80912423e800a643a769d3b5f8e5b86fb`
  - 原 owner `Antigravity5`／reviewer `Codex`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付／可由測試證明）— `met_by_test_in_green_ci`
  - 條款：Brand Transfer 與 Format Conversion 各自有獨立 outcome
  - 證據（receipt_file）：`docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md @ 9f53418df41e` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/governance/test_site001_disposition.py @ 9f53418df41e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ffe02988a1b4`，2026-09-03T16:18:24Z — conclusion=success
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：實作者以真 source lineage 接到 production consumer 且有反事實測試
  - 證據（receipt_file）：`docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md @ 9f53418df41e` — 由本 PR 交付並存在於 merge commit
  - 註：本 PR 的處置為不實作；「真 source lineage 接到 production consumer」只對被判定為實作者的成員成立。
- **A3**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：不適用者只建立 human-authority handback 且 AI 不自簽 waiver
  - 證據（receipt_file）：`docs/evidence/ODP_SITE001_COMPONENT_DISPOSITIONS_2026-09-03.md @ 9f53418df41e` — 由本 PR 交付並存在於 merge commit
  - 註：不適用者建立 human-authority handback，未自簽 waiver。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：manifest member 狀態與 formal disposition ref 一致且 checker 綠燈
  - 證據（test_file）：`tests/governance/test_site001_disposition.py @ 9f53418df41e` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ ffe02988a1b4`，2026-09-03T16:18:24Z — conclusion=success

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- ODP-FR-SITE-001 的 BRAND_TRANSFER 與 FORMAT_CONVERSION 兩 member 皆為 BLOCKED_BY_EVIDENCE，帶 formal_handback_ref 與 reopen_trigger，無自簽 decider，符合『不適用者只建立 human-authority handback 且 AI 不自簽 waiver』。

**缺口**

- 盤點觀察：兩個需求 member 待人類裁決；候選 PR head commit 無 task trailer。

### `ODP-SITESCORE-QUALITY-NULLABLE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1171](https://github.com/alfloop-dev/odayplus/pull/1171) — [ReviewBus] ODP-SITESCORE-QUALITY-NULLABLE-001 SiteScore average_confidence／data_quality_score 缺席走 feasibility，state `MERGED`，merged `2026-09-03T18:33:11Z`
- 精確 head：`7b3536c88359e59355d3e8253acd732ad11efa84`；merge：`901b4348ade4fb8cbf5c40ef429c7b49980257cc`
- 候選映射：`consistent`
  - PR body task id：`ODP-SITESCORE-QUALITY-NULLABLE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity4`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T18:00:27Z — Approved by assigned reviewer Antigravity4
- 原任務定義來源：`.orchestrator/task-briefs/odp_sitescore_quality_nullable_001.md`（生成於 2026-09-03T18:33:39Z）
  - brief 宣告 sha256 `520da1bc75dcac665492254e1b9d0676ff243cae2e28a16e04e08b27a73a6970`；重算檔案 sha256 `3906bcd3b1b6d5ffa10864d45b78fcc1c0ad6f4b40ee246ca7b3218071960572`
  - 原 owner `Claude2`／reviewer `Antigravity4`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：省略與 explicit null 都保持 unmeasured 且不折回 1.0
  - 證據（test_file）：`tests/integration/test_sitescore_decision.py @ 901b4348ade4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 7b3536c88359`，2026-09-03T18:04:19Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：缺任一品質欄位走既有 feasibility no-recommendation 出口並具名 reason
  - 證據（test_file）：`tests/integration/test_sitescore_decision.py @ 901b4348ade4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 7b3536c88359`，2026-09-03T18:04:19Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：完整量測的既有 scoring semantics 保持可比較
  - 證據（test_file）：`tests/integration/test_sitescore_decision.py @ 901b4348ade4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 7b3536c88359`，2026-09-03T18:04:19Z — conclusion=success
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：植入缺值的 production-entry 測試與 exemption 移除在同一 commit
  - 證據（test_file）：`modules/sitescore/tests/test_sitescore_production_runtime.py @ 901b4348ade4` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 7b3536c88359`，2026-09-03T18:04:19Z — conclusion=success
  - 註：植入缺值的 production-entry 測試與 exemption 移除同在本 PR 的 merge commit。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 五個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 逐條 acceptance 皆已定位到既有證據；僅餘證據強度限制（未取 job log、未重跑測試）。

### `ODP-SPEC-SOURCE-PROVENANCE-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1147](https://github.com/alfloop-dev/odayplus/pull/1147) — [ReviewBus] ODP-SPEC-SOURCE-PROVENANCE-001 補齊 ODP-SA-06 與 ODP-FR-AVM-001 canonical source provenance，state `MERGED`，merged `2026-09-03T15:08:34Z`
- 精確 head：`fd4947321d4bcbefb54ccf53799d0f12e04e8b96`；merge：`b7f9d465ddd71ecdd3dc01a8869e6e6cb485b54d`
- 候選映射：`consistent`
  - PR body task id：`ODP-SPEC-SOURCE-PROVENANCE-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Antigravity`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-03T14:38:09Z — Approved by assigned reviewer Antigravity
- 原任務定義來源：`.orchestrator/task-briefs/odp_spec_source_provenance_001.md`（生成於 2026-09-03T15:20:33Z）
  - brief 宣告 sha256 `79bc99fc6f8f12cc1bbe4eb2402c2916ccb1002ad8d2d3c64711c858fc731ef0`；重算檔案 sha256 `6d0e6d5197294b1345502d3ef18c0af9bbf59cf34250dad4386a8f9878d525b6`
  - 原 owner `Antigravity2`／reviewer `Codex`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：ODP-SA-06 與 ODP-FR-AVM-001 各有版本／位置／hash 或明確 blocked evidence 記錄
  - 證據（receipt_file）：`docs/evidence/ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md @ b7f9d465ddd7` — 由本 PR 交付並存在於 merge commit
  - 註：本 PR 只交付一份 provenance 文件，無測試；本盤點驗其存在與內容涵蓋，未向外部來源核對。
- **A2**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：所有結論區分 canonical source 與 repo transcription
  - 證據（receipt_file）：`docs/evidence/ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md @ b7f9d465ddd7` — 由本 PR 交付並存在於 merge commit
- **A3**（程式或文件交付／人類授權）— `met_by_delivered_artifact`
  - 條款：來源不可得時指定 owner／查詢方式／next-check date
  - 證據（receipt_file）：`docs/evidence/ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md @ b7f9d465ddd7` — 由本 PR 交付並存在於 merge commit
  - 註：來源不可得時指定 owner 與 next-check date，屬待人類跟進事項。
- **A4**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：不建立無法驗證的來源引用
  - 證據（receipt_file）：`docs/evidence/ODP_SPEC_SOURCE_PROVENANCE_2026-09-03.md @ b7f9d465ddd7` — 由本 PR 交付並存在於 merge commit

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 兩個 artifact 在 merge commit 存在。

**缺口**

- 盤點觀察：身分不一致：task-review-gate 核准者 Antigravity、commit trailer 記 LLM-Agent Codex2 / Reviewer Antigravity5，brief 記 owner Antigravity2 / reviewer Codex。三來源不一致，需 reviewer 依 gate 時間戳裁定。

**下一步最小驗證動作**

- 以 task-review-gate 的時間戳與 PR body 的 ReviewBus 區塊交叉判定當時 owner/reviewer。

### `ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1164](https://github.com/alfloop-dev/odayplus/pull/1164) — [ReviewBus] ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001 統一 PLATFORM_ADMIN 跨租戶政策為共用 fail-closed guard，state `MERGED`，merged `2026-09-04T13:32:29Z`
- 精確 head：`b678c18f2055b02adb70f1788951be4269589012`；merge：`541eb1fe2d6b426155dc401089bf0588ef82b345`
- 候選映射：`consistent`
  - PR body task id：`ODP-TENANT-PLATFORM-ADMIN-FAILCLOSED-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Claude2`／評審人 `Antigravity5`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-04T13:08:32Z — Approved by assigned reviewer Antigravity5
- 原任務定義來源：`.orchestrator/task-briefs/odp_tenant_platform_admin_failclosed_001.md`（生成於 2026-09-04T13:35:34Z）
  - brief 宣告 sha256 `a8b05b78b787460267e15feada004aa987eeba9e48407d8cde1872b74f11abe5`；重算檔案 sha256 `a6ce6569f28b566d54e97ce99142155b669fcf97bd1b48c56018df3916ffe771`
  - 原 owner `Claude2`／reviewer `Antigravity5`／brief 當下狀態 `review_approved`

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：三個 production entry 使用同一共用 guard 且 PLATFORM_ADMIN 預設不能跨租戶
  - 證據（test_file）：`tests/integration/test_listing_platform_observations.py @ 541eb1fe2d6b` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ b678c18f2055`，2026-09-04T12:52:09Z — conclusion=success
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：resource tenant 取自目標資源而非 principal 自己
  - 證據（test_file）：`tests/integration/test_listing_platform_observations.py @ 541eb1fe2d6b` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ b678c18f2055`，2026-09-04T12:52:09Z — conclusion=success
- **A3**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：同租戶 allow／跨租戶 deny／missing tenant deny 均由 production-shaped 測試覆蓋
  - 證據（test_file）：`tests/integration/test_listing_platform_observations.py @ 541eb1fe2d6b` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/integration/test_external_data_cutover_prep.py @ 541eb1fe2d6b` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ b678c18f2055`，2026-09-04T12:52:09Z — conclusion=success
- **A4**（可由測試證明／人類授權）— `met_by_test_in_green_ci`
  - 條款：若存在正式例外則驗證 scope／expiry 並寫 actor／resource／reason audit
  - 證據（test_file）：`tests/integration/test_listing_platform_observations.py @ 541eb1fe2d6b` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ b678c18f2055`，2026-09-04T12:52:09Z — conclusion=success
  - 註：「若存在正式例外」為條件式條款；本盤點未查到已生效的正式例外，故驗到的是 guard 與 audit 的實作面。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 六個宣告 artifact 在 merge commit 全存在；精確 head 7/7 success。

**缺口**

- 盤點觀察：候選 PR head commit 無 task trailer（head 為 merge commit）。

### `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002` — verified_candidate（信心 medium）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#1096](https://github.com/alfloop-dev/odayplus/pull/1096) — [ReviewBus] ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 在登入節流修正合併後驗收帳密預設與可選 OIDC 的安全端對端行為，state `MERGED`，merged `2026-09-01T09:10:24Z`
- 精確 head：`69422d71e8d5ac572ade58562c0aeca28d123648`；merge：`2377168c2cc07cd2470dd8f43de0486fe8d8fc08`
- 候選映射：`consistent`
  - PR body task id：`ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex`／評審人 `Codex2`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-09-01T08:45:28Z — Approved by assigned reviewer Codex2
- 原任務定義來源：`.orchestrator/task-briefs/odp_web_password_first_security_e2e_002.md`（生成於 2026-09-01T09:12:09Z）
  - brief 宣告 sha256 `3e8d24a768a5acbb2fff6f9fad2385dd06834a59fcb8c029533c922c52c44e39`；重算檔案 sha256 `01f24a0874f9dd2c54075265dad7318700be9811426c017cf8347722b777e4b6`
  - 原 owner `Codex`／reviewer `Codex2`／brief 當下狀態 `review_approved`
  - 其他任務 brief 記載的終態旁證：done（2 筆引用）

**逐條 acceptance 證據對應**

- **A1**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：帳密登入成功失敗與 account threshold 的正式 TypeScript route 證據正確
  - 證據（receipt_file）：`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`apps/web/tests/login-route.test.ts @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_section）：`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md §2 證據矩陣 @ 2377168c` — 「帳密登入成功／失敗」對應 Web route suite，預期 200 建立 opaque session、無效憑證固定 401 且無 cookie；「account / IP threshold」對應 Web route suite 與 tests/security/test_login_throttle_wiring.py，預期 429 AUTH_RATE_LIMITED、gate 在 credential verification 之前、狀態持久於 identity.login_attempts
  - 證據（ci_check）：`check-run product @ 69422d71e8d5` — conclusion=success；apps/web/tests/login-route.test.ts 是 npm workspace（vitest）測試，由 product job 的 Run Node workspace checks step 執行（make node-check → npm run test --workspaces --if-present）。
  - 證據（ci_execution_binding）：`apps/web/tests/login-route.test.ts → product（非 product-e2e-gate）` — product-e2e-gate 只跑寫死的 playwright spec 清單與 PYTEST_NODE_IDS，兩者都不含本檔；npm workspace 測試由 product job 的 node-check step 執行。前輪綁 product-e2e-gate 是錯誤的。
  - 註：更正先前盤點：宣告 artifact「security E2E receipt」可定位為 docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md（由本 PR 交付）。
- **A2**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：未設定 OIDC 時 deploy validation 可通過且 OIDC 路由 fail closed
  - 證據（receipt_file）：`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（test_file）：`tests/e2e/test_password_first_security_e2e.py @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 69422d71e8d5`，2026-09-01T08:49:57Z — conclusion=success
  - 證據（receipt_section）：`同上收據 §2` — 「local default / OIDC disabled」列 route suite 與 Python E2E，預期無 OIDC 變數可通過 preflight、OIDC token 與 OIDC route fail closed
  - 證據（receipt_section）：`同上收據 §4 Verification record` — 2026-09-01 UTC 完成分層驗證，`uv run --python 3.12 pytest ... tests/ops/test_conditional_oidc_deployment.py tests/identity` 記 151 passed / 22 skipped
- **A3**（可由測試證明／外部來源啟用狀態）— `partially_met`
  - 條款：完整 OIDC 設定時可選登入不回歸
  - 證據（receipt_file）：`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 69422d71e8d5`，2026-09-01T08:49:57Z — conclusion=success
  - 證據（receipt_section）：`同上收據 §2` — 「complete OIDC optional path」列 route suite 與 Python E2E，預期 local 與 OIDC 解析到同一 authoritative principal、帳密不回歸
  - 證據（receipt_section）：`同上收據 §5 Limitations` — 明載 No live GCP or external OIDC provider is contacted by this receipt；且測試不宣稱已部署瀏覽器 HTTP run
  - 註：更正先前盤點：不是「查無對應收據」。收據涵蓋此條款的測試矩陣，但同一份收據明確聲明未接觸真實 OIDC provider，因此此條款在測試層達成、在 live provider 層是收據自陳的界線。
- **A4**（可由測試證明）— `met_by_test_in_green_ci`
  - 條款：RBAC tenant isolation audit event 有整合測試
  - 證據（test_file）：`tests/e2e/test_password_first_security_e2e.py @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（ci_check）：`check-run product @ 69422d71e8d5`，2026-09-01T08:49:57Z — conclusion=success
  - 證據（receipt_section）：`同上收據 §2` — 「RBAC tenant isolation」對應 Python E2E，預期跨 tenant API request 回 403 並寫入 operator.tenant_isolation deny audit event
- **A5**（程式或文件交付）— `met_by_delivered_artifact`
  - 條款：無 secret value 寫入 logs receipts 或 PR
  - 證據（receipt_file）：`docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md @ 2377168c2cc0` — 由本 PR 交付並存在於 merge commit
  - 證據（receipt_field）：`同上收據 frontmatter 與 §3` — secret_values_redacted: true；§3 記所有部署 secret 只以變數名稱或 Secret Manager reference 出現，payload 一律 <REDACTED>
- **A6**（執行過程約束）— `process_constraint_unverifiable`
  - 條款：所有變更完成後只跑一次完整分層 suite且 PR 中文
  - 證據（receipt_section）：`同上收據 §4` — 記錄 7 層驗證命令與結果：apps/web test 53 files/474 tests、typecheck、lint、Python 151 passed/22 skipped、ruff、Terraform contract 14 files、Terraform unit 32 tests
  - 註：「只跑一次完整分層 suite」的次數本盤點未核；收據列出的是最終一次分層驗證的命令與結果。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。
- 未完全定位到證據的條款：A3(partially_met)、A6(process_constraint_unverifiable)

**查證發現**

- 更正第一輪：兩個敘述型 artifact 都可定位，且都由本 PR 於 merge 2377168c 交付。『security E2E receipt』= docs/evidence/e2e/ODP_WEB_PASSWORD_FIRST_SECURITY_E2E_RECEIPT.md；『auth migration rollout checklist』= docs/deployment/AUTH_MIGRATION_ROLLOUT_CHECKLIST.md（六節：Preflight／Authentication modes／Session, RBAC, and audit／Rollout and rollback／Verification commands／Task verification result）。
- 收據 frontmatter 記 verdict: pass、secret_values_redacted: true、contract ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001、contract_matrix 涵蓋 Password login、throttle、T27–T30。
- 收據 §2 證據矩陣逐列對應本任務的七個控制面（帳密成功／失敗、account 與 IP threshold、production fail closed、local default 與 OIDC disabled、complete OIDC optional path、RBAC tenant isolation、secret exclusion），各列都寫出預期結果而非只寫「通過」。
- 收據 §4 記 2026-09-01 UTC 完成的七層驗證與結果：apps/web 53 files/474 tests、typecheck、lint、Python `uv run --python 3.12 pytest …` 151 passed/22 skipped、ruff、Terraform contract 14 files、Terraform unit 32 tests，並附可重跑命令清單。
- 收據 §5 明確聲明限制：不接觸真實 GCP 或外部 OIDC provider，且測試不宣稱已部署瀏覽器 HTTP run。因此『完整 OIDC 設定時可選登入不回歸』在測試矩陣層達成、在 live provider 層是收據自陳的界線——這是已揭露的限制，不是缺收據。
- 兩個路徑型 artifact（apps/web/tests/login-route.test.ts、tests/security/test_login_throttle_wiring.py）在 merge commit 存在。

**缺口**

- A3（partially_met）：更正先前盤點：不是「查無對應收據」。收據涵蓋此條款的測試矩陣，但同一份收據明確聲明未接觸真實 OIDC provider，因此此條款在測試層達成、在 live provider 層是收據自陳的界線。
- 第 4 輪更正（結論不變）：A1 的 apps/web/tests/login-route.test.ts 是 vitest，原綁 product-e2e-gate；實際由 product job 的 Run Node workspace checks step（make node-check → npm run test --workspaces）執行，已改綁。

**下一步最小驗證動作**

- 無需再尋找收據。若 reviewer 認為『完整 OIDC 設定時可選登入不回歸』必須有真實 provider 證據，最小動作是取回一筆設定完整 OIDC 的環境登入收據；在此之前該條款維持 partially_met，不得由測試綠推定 live 行為。

### `XR-EXT-OSS-FINAL-AUDIT-001` — verified_candidate（信心 high）

- 倉庫：`alfloop-dev/odayplus`
- 候選 PR：[#996](https://github.com/alfloop-dev/odayplus/pull/996) — [ReviewBus] ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001 將 runtime gate closure invariant 納入 ODayPlus disposition ，state `MERGED`，merged `2026-08-24T04:54:38Z`
- 精確 head：`b436a3fcab9cad520a4d10a511d1ab3f4eb7dc9a`；merge：`0dc5cebc90cf3a55c0e2805459bcdda19f9c4e36`
- 候選映射：`mismatch`（head 分支不是 task/<ID>；PR body 宣告的 task id 是 ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001）
  - PR body task id：`ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001`（來自 review_bus_block）
  - ReviewBus 區塊：狀態 `review_approved`／負責人 `Codex2`／評審人 `Codex`
- 精確 head CI：success=7；commit status rollup `success`
- 核准：`task-review-gate` `success` @ 2026-08-24T04:30:51Z — Approved by assigned reviewer Codex
- 原任務定義來源：`.orchestrator/task-briefs/xr_ext_oss_final_audit_001.md`（生成於 2026-08-24T05:53:48Z）
  - brief 宣告 sha256 `5e2112af80f16581bd057c4f5479524986594e52dfa92477bd555106b1c388bf`；重算檔案 sha256 `2a746bfdf75d454e25fd57c5723c3672e67efc5289c956cbb5922150bbe3e556`
  - 原 owner `Codex2`／reviewer `Codex`／brief 當下狀態 `review`

**逐條 acceptance 證據對應**

- **A1**（runtime／部署／程式或文件交付）— `met_by_receipt`
  - 條款：驗證兩個 repo 的精確 commit、PR、CI、image、snapshot readback、資料新鮮度、coverage、lineage、SBOM 與 NOTICE 可重算。
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/cross-repo-evidence-ledger.json @ 7b0670d7` — producer：PR #60、approved head 0708eef7、merge d3069c93、4 個 required workflow（container.yml、cross-repo-contract.yml、emgi-task-manifest.yml、source-suite.yml）ci_status=PASS；reviewed_image_digest sha256:f6934705…、SBOM sha256 df9238a1…、NOTICE sha256 e3aa79c2…、contract lock sha256 c2c153fb…
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/README.md @ 7b0670d7` — snapshot readback 8/8 domains、54443 records；freshness／coverage／lineage 皆 PASS
  - 證據（receipt_field）：`cross-repo-evidence-ledger.json single_consumer_architecture.quality_axes` — checksums 11/11、consumer_readback 11/11、counts 11/11、coverage 11/11、freshness 11/11、lineage 11/11、identities 44471/44471，全部 status=PASS
  - 註：條款要求兩 repo 的 commit／PR／CI／image／snapshot readback／新鮮度／coverage／lineage／SBOM／NOTICE 可重算。收據逐項給出可重算的 digest 與逐軸比對計數，非只列 PR 與 check 名稱。
- **A2**（人類授權／程式或文件交付）— `met_by_receipt`
  - 條款：所有非人為許可的技術 gap 必須關閉；資料授權決定則逐來源列為待具名人員決定，不得混成工程失敗。
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/technical-gap-closure-matrix.json @ 7b0670d7` — total_initial_gaps=10、technical_gaps_closed=6、unresolved_technical_blockers=0、legal_gates_itemized=4、verdict=ALL_TECHNICAL_GAPS_RESOLVED_SOURCES_OFF。四項 LEGAL_GATE 逐項 ITEMIZED_PENDING_LEGAL_GATE 並指向 HUMAN-OSS-LEGAL-APPROVAL-001：LICENSE-BLOCKED-CONSUMER（779 個元件中 163 個未過授權閘）、LICENSE-BLOCKED-PRODUCER（154 中 24）、LICENSE-POLICY-NOT-APPROVED（唯一授權政策 ODP-OSS-License-Gate-Policy-v1 狀態仍為 proposed）、SOURCE-DATA-LICENCE-NOT-MODELLED
  - 註：技術 gap 與法律待決被明確分開，未混成工程失敗。
- **A3**（外部來源啟用狀態）— `met_by_receipt`
  - 條款：證明未核准來源的 enabled=false、核准收據欄位為空、schedule STOPPED、provider credential 未投影且 public egress 為 default deny。
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/source-permission-matrix.json @ 7b0670d7` — total_sources=16、enabled_sources=0、sources_with_receipt=0、running_schedules=0、public_egress_open=false；canonical_policy_module 指向 src/oday_data_platform/external/policy/update.py:SOURCE_UPDATE_POLICIES，逐 source 記 enabled_env_var、approval_receipt_sha256、dagster_schedule_state 與 provider_credentials_projected
  - 證據（receipt_field）：`cross-repo-evidence-ledger.json egress_audit` — policy emgi-default-deny-public-egress、manifest deploy/k8s/emgi/network-policy.yaml、manifest_sha256 349c0446…、default_deny=true、public_internet_blocked=true、allowed_cidrs 只含 RFC1918 與 metadata server 169.254.169.254/32
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/README.md @ 7b0670d7` — 16 個來源逐列表：11 個 SCHEDULED 為 STOPPED、5 個 ON_DEMAND／EVENT_DRIVEN 為 NOT_SCHEDULED；enabled 全 false、核准收據全空
  - 註：條款的四個子項（enabled=false、核准收據空、schedule STOPPED、credential 未投影且 public egress default deny）逐項都有具體量測值。
- **A4**（runtime／部署）— `met_by_receipt`
  - 條款：證明 ODayPlus 只有 platform snapshot consumer，沒有第二個 external producer 或開發期 ingestion 旁路。
  - 證據（receipt_field）：`cross-repo-evidence-ledger.json single_consumer_architecture` — active_external_producers_in_default_mode=0、facade_mode=PLATFORM_PRIMARY、default_external_fetch_enabled=false、manual_ingestion_trigger_default=HTTP_410_GONE、scheduler_external_fetch_default=NOT_ENQUEUED、worker_external_fetch_default=NON_RETRYABLE_REJECT_BEFORE_SERVICE、legacy_code_state=RETAINED_FROZEN_ROLLBACK_ONLY
  - 證據（receipt_field）：`cross-repo-evidence-ledger.json consumer` — consumer 證據由 alfloop-dev/odayplus 的 pinned merge 重算：provider-off PR #995（head e53fc5f6 → merge 9199e59f）、disposition PR #996（head b436a3fc → merge 0dc5cebc）、cutover PR #991（head 786b638f → merge b32fd65f）、license gate PR #983；consumer_iac_verification 掃 46 個 deployment surface，credentials_projected=false、cloud_nat_removed=true、default_deny_egress_enforced=true
  - 註：「ODayPlus 只有 platform snapshot consumer、沒有第二個 external producer」由 consumer repo 的 pinned tree 重算，而非由 producer 自陳。
- **A5**（人類授權）— `met_by_receipt`
  - 條款：技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴；本任務不自行批准任何來源。
  - 證據（receipt_file）：`docs/evidence/final/XR-EXT-OSS-FINAL-AUDIT-001/closeout-audit-manifest.json @ 7b0670d7` — technical_audit_verdict=TECHNICAL_READINESS_VERIFIED、open_technical_gaps_count=0、source_deployment_status=ALL_16_SOURCES_DISABLED_AND_STOPPED、egress_status=DEFAULT_DENY_ENFORCED、single_consumer_architecture_verified=true；pending_human_legal_gates 一筆，task_id=HUMAN-OSS-LEGAL-APPROVAL-001，status=UNBLOCKED_BY_TECHNICAL_AUDIT，摘要為「由具名 Legal/Security/Risk 人員逐來源與逐套件決定授權；任何來源在決定前維持關閉」
  - 註：條款要求技術稽核完成後才解除 HUMAN-OSS-LEGAL-APPROVAL-001 的依賴、且本任務不自行批准任何來源。收據以 UNBLOCKED_BY_TECHNICAL_AUDIT 記錄前者，以 enabled_sources=0／sources_with_receipt=0 證明後者。法律決定本身仍待具名人類，本盤點不補造。

**建議涵蓋範圍**

- 涵蓋：本建議只涵蓋：候選 PR 與本 ID 的映射一致、精確 head 的 CI 結論、綁定精確 head 的 task-review-gate 核准者，以及上表逐條 acceptance 已定位到的既有證據。
- 不涵蓋：不涵蓋 runtime 部署是否仍然有效、外部來源是否已被授權啟用、任何人類 GO／lease／provider permission，以及測試在 job log 層的個別結果。

**查證發現**

- 輸入清單的候選 odayplus PR #996 為誤配：該 PR 的 headRefName 是 task/ODP-LEGACY-DISPOSITION-RUNTIME-GATES-001，PR body ReviewBus 區塊宣告的 task id 亦為該任務（狀態 review_approved、負責人 Codex2、評審人 Codex），只是標題文字提及本任務；本任務三個宣告 artifact 在該 merge commit 與 dev tip 皆不存在。
- 但 #996 並非與本任務無關：XR 的 cross-repo-evidence-ledger.json 把它 pin 為 consumer disposition 證據（PR #996 → approved head b436a3fc → merge 0dc5cebc），與 provider-off PR #995、cutover PR #991、license gate PR #983 並列。誤配的是「哪個 PR 是本任務的交付」，不是「#996 是否屬於本任務的證據鏈」。
- 真正交付為 alfloop-dev/oday-data-platform PR #61，分支 task/XR-EXT-OSS-FINAL-AUDIT-001，MERGED 於 2026-08-24T05:54:54Z，merge 7b0670d7b37e59e06bc9fea5b6003d1964be2c3c，精確 head b1824c979aca008da10aed01fbc0c0a269c581dc。
- 精確 head 的 7 個 check 全 success，執行區間 2026-08-24T05:50:55Z 至 05:54:06Z（source-suite、closeout-audit、producer-compatibility、consumer-readback、contract-bundles、emgi-task-manifest、Build and smoke test images）；commit status task-review-gate=success 於 2026-08-24T05:54:45Z，描述『Approved by assigned reviewer Codex』，與 brief reviewer 一致；核准後 9 秒合併。
- 更正第一輪：本輪已於 merge 7b0670d7 唯讀讀回四份收據的實際內容，不再只記 PR 與 check 名稱。source-permission-matrix.json：16 來源、enabled_sources=0、sources_with_receipt=0、running_schedules=0、public_egress_open=false，canonical 來源為 SOURCE_UPDATE_POLICIES（11 個 SCHEDULED 為 STOPPED、5 個 ON_DEMAND／EVENT_DRIVEN 為 NOT_SCHEDULED）。
- cross-repo-evidence-ledger.json：producer PR #60、approved head 0708eef7、merge d3069c93，4 個 required workflow ci_status=PASS；reviewed image digest sha256:f6934705…、SBOM sha256 df9238a1…、NOTICE sha256 e3aa79c2…、contract lock sha256 c2c153fb…；egress_audit 記 policy emgi-default-deny-public-egress、manifest sha256 349c0446…、public_internet_blocked=true；七個品質軸（checksums／consumer_readback／counts／coverage／freshness／lineage 各 11/11、identities 44471/44471）全部 PASS；snapshot readback 8/8 domains、54443 records。
- technical-gap-closure-matrix.json：total_initial_gaps=10、technical_gaps_closed=6、unresolved_technical_blockers=0、legal_gates_itemized=4，verdict=ALL_TECHNICAL_GAPS_RESOLVED_SOURCES_OFF；四項法律待決逐項指向 HUMAN-OSS-LEGAL-APPROVAL-001。
- closeout-audit-manifest.json：technical_audit_verdict=TECHNICAL_READINESS_VERIFIED、open_technical_gaps_count=0、source_deployment_status=ALL_16_SOURCES_DISABLED_AND_STOPPED、egress_status=DEFAULT_DENY_ENFORCED、single_consumer_architecture_verified=true；pending_human_legal_gates 一筆 status=UNBLOCKED_BY_TECHNICAL_AUDIT。技術就緒與人類授權在收據內即已分開，本盤點沿用該區分，不補造任何授權。
- 三個宣告 artifact 皆存在於 oday-data-platform 的該 merge commit；輸入清單把本 ID 的 repository 記為 odayplus 亦屬誤判。

**缺口**

- 盤點觀察（第 1 輪判斷已細化）：這兩條 acceptance 都由 closeout-audit-manifest.json 與 technical-gap-closure-matrix.json 直接記載——四項法律議題逐項 ITEMIZED_PENDING_LEGAL_GATE，依賴狀態記為 UNBLOCKED_BY_TECHNICAL_AUDIT。尚未存在的是法律決定本身（需具名 Legal／Security／Risk 人員），本盤點不補造。
- 盤點觀察：跨 repo，merge commit 不在 odayplus 本地歷史，只有 GitHub 唯讀證據。

**下一步最小驗證動作**

- 以 oday-data-platform PR #61 取代 #996 作為本 ID 的候選，並把 repository 由 odayplus 更正為 oday-data-platform。
- HUMAN-OSS-LEGAL-APPROVAL-001 的四項法律待決仍需具名 Legal／Security／Risk 人員逐來源與逐套件決定，不得由本盤點或技術稽核推定。


## 給 reviewer 的映射注意事項

1. `verified_candidate` 只表示「可作為復原候選送 reviewer 判定」，**不表示 acceptance 全數完成**。每個 ID 的 `recommendation_scope.criteria_not_fully_evidenced` 列出未完全定位到證據的條款，請連同一併判定。
2. `blocked` 目前只有三筆，且全部屬**證據直接否證**：`DPF-EMGI-LIVE-ROLLOUT-001`（A5 收據自陳 missing rollback_receipt）、`ODP-DEV-ROLLOUT-001`（A2 image digest 為佔位值 `sha256:1111…`、A5 receipts 未綁 exact SHA、A3 runtime readback 查無證據）、`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`（A3 含人類授權成分且只部分達成）。第 2 輪曾存在的「條款讀法待裁定」分類（`blocking_reasons[].readings`／`blocked_pending_reviewer_reading`）在第 3 輪已全部裁定並清除，**本輪資料集中不再有這個分類**；請勿再依該分類映射 writer。
3. 第 4 輪新增的 `test_delivered_not_executed_at_exact_head` 要當成「證明方式不成立」，不是「acceptance 被否證」。目前 5 條，集中在 `ODP-DEV-STAGED-GATE-RECONCILIATION-001`（A3–A6，該 head 的 `product` job 為 scope skip）與 `ODP-EPHEMERAL-STAGING-IAC-001`（A2，`infra/` 不在任何 CI job 的收集範圍）。若 reviewer 要把這兩個 ID 回填為完成態，最小補證動作已寫在各自的「下一步最小驗證動作」；本盤點依範圍限制不執行測試。
4. 技術就緒與人類授權必須分開記錄。`ODP-GITHUB-GCP-ENV-BOOTSTRAP-001` 的 GitHub 側 production environment 與其 required reviewers 保護**確實存在**；缺的是 production 的 GCP 資源、環境變數與五項具名人類決定。`XR-EXT-OSS-FINAL-AUDIT-001` 的技術稽核 verdict 為 TECHNICAL_READINESS_VERIFIED，四項法律決定仍待具名人員。
5. code scope 與 deferred runtime scope 分開。`ODP-DEV-STAGED-GATE-RECONCILIATION-001` A7、`ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001` A9 與 `ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001` 的 live posture，本盤點一律記為未證明的缺口並指向後續 rollout，**不**倒灌成這些 code 任務未完成，也**不**因此宣稱 runtime 已完成。
6. 兩份收據都寫「16 sources disabled」不代表驗的是同一份清單：`ODP-DEV-ROLLOUT-001` 稽核的是 ODayPlus canonical snapshot 家族，DPF／XR 稽核的是 data platform 的 16 個第三方 provider。請勿互相當作佐證。
7. 本資料集不含任何 canonical 寫入建議。哪些 ID 該回填、以何種 status 回填、由誰簽署，都由 reviewer 與唯一 existing writer 決定；本盤點不代為決定，也未預先產生任何 archive 記錄。
