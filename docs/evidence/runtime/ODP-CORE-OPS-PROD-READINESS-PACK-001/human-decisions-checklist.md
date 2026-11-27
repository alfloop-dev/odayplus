# core-ops production GO：人工決策清單（DRAFT · 簽名欄全部空白 · 2026-11-27 版）

> **簽名欄全部留白。** 本清單不代表任何 gate、release decision、lease 或環境核准已經簽署。
>
> - 每一項標示目前狀態，只有三種：
>   - **已決定**：負責人已做方向決定。引用 `live-readback-20261127.md` §2 的決定代號 A–H 與日期。**方向決定不是簽署**；需要具名的部分仍標「待簽」。
>   - **已由程式解決**：`origin/dev` 的程式已處理，附 file:line。
>   - **仍待決／待簽**：還沒有決定，或需要在選定 candidate 之後具名。
> - gate-3、gate-5、gate-6、release decision、lease 與 GitHub environment 核准，一律是**待簽**。
> - 簽署結果要寫進 registry、manifest 或 lease 時，必須由**另一個 task** 以 evidence-only commit 寫入。本 task 不修改 registry。
> - 行號以 `origin/dev` @ `48c5271b5d882d824d432f3483ab4415030bb8e6` 為準。GCP／GitHub 現況引用 `L-…`。

**每一項的簽署格式（全部留白）：**

```text
簽署人：＿＿＿＿＿＿  角色：＿＿＿＿＿＿  日期（ISO）：＿＿＿＿＿＿
決定：□ 核准  □ 附條件核准  □ 拒絕  □ 不適用
條件／備註：＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
```

## 總覽

| 項目 | 主題 | 狀態 |
|---|---|---|
| HD-01 | 接受本準備包作為審閱基礎 | 待簽 |
| HD-02 | PR #1565（首次 release 回滾語意） | 已由程式解決 |
| HD-03 | F1-a lineage 解鎖政策 | 仍待決 |
| HD-04 | F1-b staging 讀 production lease store | 部分已由設定解決；IAM 仍待決 |
| HD-05 | 選定 candidate 並觸發 staging build | 已決定（A）；SHA 待選定、待簽 |
| HD-06 | F2 production Terraform 模型契約 | 已由程式解決 |
| HD-07 | gate-0／1／4 重新綁定與處置 | 仍待決 |
| HD-08 | gate-2 處置 | 仍待決 |
| HD-09 | registry 推進到 staging，release decision（staging） | 待簽 |
| HD-10 | staging lease | 待簽 |
| HD-11 | staging 的 IAM 與網路前提 | 已由程式解決（IaC） |
| HD-12 | GitHub `staging` environment 核准 | 待簽 |
| HD-13 | UAT 環境、資料與帳號 | 已決定（B）；範圍仍待決 |
| HD-14 | 營業日 cutoff | 已決定（F） |
| HD-15 | gate-3 passed-with-deviation | 已決定方向（G）；待簽 |
| HD-16 | gate-5 處置 | 待簽 |
| HD-17 | gate-6 處置 | 待簽 |
| HD-18 | production 環境變數與 IAM | 部分已設；其餘仍待決 |
| HD-19 | production Cloud SQL 基準與 backup checkpoint | 已決定（H）＋已由程式解決；讀回仍待決 |
| HD-20 | production DR 演練 | 已決定（E）；待執行、待簽 |
| HD-21 | 回滾與收尾的三個缺口 | 1 已決定（D）；2、3 已由程式解決；程序待簽 |
| HD-22 | 值班、告警與 watch | 已決定（C）；watch receipt 等仍待決 |
| HD-23 | release notes | 待簽 |
| HD-24 | production GO | 待簽 |
| HD-25 | production lease | 待簽 |
| HD-26 | GitHub `production` environment 核准 | 待簽 |
| HD-27 | watch window 結果 | 待簽（上線後） |
| HD-28 | staging closeout 與清理 | 部分已由程式解決；仍待決 |
| HD-29 | 上線後的觀察義務與 owner | 仍待決（BATCH-002 已決定 F） |
| HD-30 | gate-3 deviation 的追蹤 | 待簽（上線後） |
| HD-31 | release 封存 | 待簽（上線後） |
| HD-32 | 各項人工操作的執行身分 | 仍待決 |

---

## A. 上線前：範圍與程式

### HD-01 接受本準備包作為審閱基礎

- **決定什麼**：本資料夾的文件是否可以作為 gate-6 文件類 check 的審閱稿。
- **依據**：`gate-evidence-map.md` 的 gate-6；`RELEASE_GATE_REGISTRY.json:757-765`。
- **建議決策者**：gate-6 reviewer Human/Ops（`:750`）。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-02 PR #1565（首次 staging／production release 回滾語意）

- **狀態**：**已由程式解決。** PR #1565 在 2026-10-24 合併（merge commit `9a346570a`）。方案 A 已實作：`INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS = ("dev", "staging")`（`delivery_toolchain/release/release_manifest.py:357`）；production 失敗時刪除 candidate、ephemeral staging 失敗時 hold（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:118-204`，尤其 `:164`）。
- 不需要另外簽署。後續項 F1-a、F1-b 見 HD-03、HD-04。

### HD-03 F1-a：首次 production release 失敗後的 lineage 解鎖政策

- **決定什麼**：首次 production release 在 lease 消耗後、commit 前失敗（candidate 被刪除），而下一個候選是不同 manifest 時，要怎麼處理。
- **依據**：design `:201`、`:223`；`rollback-plan.md` §2.1。
- **與決定 D 的關係**：決定 D 處理的是**已 commit** 之後的下線；那種情況下 release 仍在 production，下一個 release 可以綁它當 `rollback_release`，不會碰到 F1-a（`rollback-plan.md` §2.4）。F1-a 只剩「commit 前失敗」這種情況。
- **建議決策者**：Release owner + Human/Ops。
- **狀態**：**仍待決**。簽署：＿＿＿＿

### HD-04 F1-b：staging 讀 production lease store 的設定與 IAM

- **狀態**：
  - `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI` 已在 `staging` environment 設定（`L-4.2`）。
  - staging admission 身分對 production lease bucket 的唯讀權限（`storage.objects.list`／`get`）：**未讀回，仍待決**。缺少時，staging 首次 release 的 admission 會被拒絕，lease 不會被消耗（design `:198`、`:224`）。
- **建議決策者**：Human/Ops（IAM）。
- 簽署：＿＿＿＿

### HD-05 選定 core-ops candidate SHA，並觸發 staging build

- **狀態**：**已決定**程序（決定 A，2026-11-27）：先在不凍結下備妥文件，再凍結 `ready_dispatcher`，以當時的 dev tip 當 candidate C；staging 通過後直接接 production；完成後解凍。具體 SHA **待選定**，build dispatch **待簽**。
- **凍結前要先合併的變更**：只要會改到 evidence 以外的路徑，就必須在凍結前合併，否則會讓 C 失效（`deploy-dev.yml:1104-1127`；`check_release_gate_registry.py:708`、`:731`）。目前已知：
  - `ODP-PROD-WATCH-RECEIPT-REAL-METRICS-261127`（**pending**，`L-6.2`）。不先合併，C 上線後 `staging_closeout` 只會 defer，staging 不會自動清理。
- **build 參數**：`phase=build`、`environment=staging`、`release_profile=core-ops`、`initial_release_recovery=true`（`deploy-dev.yml:145-155`；`deployment-plan.md` §2 步驟 1）。
- **建議決策者**：Release owner／負責人。
- 簽署：＿＿＿＿　candidate SHA：＿＿＿＿

### HD-06 F2：production Terraform 模型契約與 core-ops 如何並存

- **狀態**：**已由程式解決。** 模型相關的 precondition 只在 `release_profile == "full"` 時生效；core-ops 只要求 `ODP_FORECAST_ENGINE`、`ODP_FORECAST_MODEL`（`infra/terraform/checks.tf:131-155`、`:355-385`；`infra/terraform/main.tf:52-53`）。production 範本已設 `release_profile = "core-ops"`（`infra/terraform/env/prod.tfvars.example:14`）。
- 不需要另外簽署。

---

## B. 上線前：gate 處置

### HD-07 gate-0、gate-1、gate-4 為 candidate C 重新綁定；gate-4 的處置

- **決定什麼**：C 的 Code、Contract、Security Gate receipt；gate-4 的 npm deviation 只適用先前的 dev candidate，C 需要重新處置（`RELEASE_GATE_REGISTRY.json:661`）。
- **現況**：gate-1、gate-4 目前是 `blocked`，等待 HD-07（`:286`、`:623`）。
- **建議決策者**：gate-4 owner Claude、reviewer Antigravity2（`:621-622`）；deviation 由負責人核准。
- **狀態**：**仍待決**。簽署：＿＿＿＿

### HD-08 gate-2 Data Gate 的處置（staging admission 的前提）

- **現況**：gate-2 是 `passed-with-deviation`，但只適用 `8e5020a29` 進 staging，而且寫明之後的 candidate 要重新處置（`RELEASE_GATE_REGISTRY.json:446-451`）。
- **決定什麼**：C 的每個 check 是證據通過，還是處置為 not-applicable 或 passed-with-deviation（`gate-evidence-map.md` 的 gate-2）。
- **建議決策者**：gate-2 owner Codex2、reviewer Claude（`:395-396`），以及負責人。
- **狀態**：**仍待決**。簽署：＿＿＿＿

### HD-09 registry 推進到 staging（`dev-verified`），並核准 release decision `go`（staging）

- **決定什麼**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`、`human_signoff`，綁定 C。
- **依據**：`check_runtime_admission.py:160-205`；`check_release_gate_registry.py:67`；既有 `human_signoff` 格式（`RELEASE_GATE_REGISTRY.json:26-30`）。
- **建議決策者**：Human/Ops（`decision_owner`，`:23`）。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-10 申請 staging lease

- **依據**：`.orchestrator/release_lease.py:1-21`。
- **建議決策者**：Human/Ops。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-11 staging 前提的 IAM 與網路（design §12.5）

- **狀態**：**已由程式解決（IaC）。** design §12.5.1 已改 Terraform：deployer 的 ID token 簽發權、runtime SA 的 invoker、API ingress、私有 Google API 的 DNS（`docs/design/ODP_CORE_OPS_RELEASE_PROFILE.md:370-387`）；`worker_idempotency` 空佇列問題已修（`:366`）。鏈 U 的 staging deploy 曾成功（`L-5.1`），顯示這些前提在 live 上可行；C 的 staging receipt 仍要另外產生。
- 不需要另外簽署。

### HD-12 核准 GitHub `staging` environment 的部署

- **依據**：`deploy-dev.yml:959-960`、`:1649-1650`；required reviewers 是 `Alien-alfaloop`、`ajoe734`（`L-4.5`）。
- **狀態**：**待簽**（每次 dispatch 都要）。簽署：＿＿＿＿

### HD-13 UAT 環境、資料與帳號

- **狀態**：
  - **已決定**（決定 B，2026-11-27）：UAT 在 ephemeral staging 執行，由負責人本人操作並簽核；帳號由 Claude 以該 release 的 bootstrap `platform_admin` 經既有邀請 API 建立；密碼不寫入 repo（`uat-plan.md` §2、§3）。
  - **仍待決**：PriceOps、AdLift、Growth、Franchisee 是否納入（`uat-plan.md` §1）。
  - **仍待決**：UAT-CO-11（跨租戶）在單一 tenant 的 staging 無法執行，處置方式（`uat-plan.md` §4）。
  - **仍待決**：Claude 讀 staging admin 密碼 secret 的身分（HD-32）。
- 簽署（範圍）：＿＿＿＿

### HD-14 營業日 cutoff 時間

- **狀態**：**已決定**（決定 F，2026-11-27）：`ODP-NFR-BATCH-002` 對 core-ops 記為 not-applicable，因為 core-ops 沒有每日 batch；上模型時再定。
- **依據**：`obligation_matrix.json:140-167`；`disposition.json:133`。
- 這是方向決定；若要寫進 registry 或 obligation matrix，仍由另一個 task 寫入。簽署：＿＿＿＿

### HD-15 gate-3 passed-with-deviation（core-ops）簽署

- **狀態**：**已決定方向**（決定 G，2026-11-20）：core-ops 記為不適用／passed-with-deviation。**簽署待簽**，要在 C 選定之後。
- **要填的欄位**：`risk_owner`、`approved_by`、`approved_at`、`review_by`（建議不超過 90 天）（design `:261-264`）。「human approval and rollback path recorded」不在 deviation 清單內：簽署本身是 human approval；rollback path 依 HD-21。
- **建議決策者**：Product Owner + ML Risk Owner（design `:226`）。

```text
risk_owner：＿＿＿＿  approved_by：＿＿＿＿  approved_at：＿＿＿＿  review_by：＿＿＿＿
```

### HD-16 gate-5 處置

- **決定什麼**：E2E receipt 的範圍（模型相依 scenario 怎麼算）；live gate 的 receipt；效能量測（staging 負載測試、render budget；batch window 依決定 F 為 not-applicable）；UAT 結果零 P0／P1 並完成簽核（決定 B：負責人簽核）。
- **依據**：`gate-evidence-map.md` 的 gate-5；registry `:708-714`。
- **建議決策者**：gate-5 owner Codex2、reviewer Human/Ops（`:700-701`）。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-17 gate-6 處置

- **決定什麼**：七項 check 的處置，見 `gate-evidence-map.md`。特別是 evidence manifest 與 RTM 對照的產生方式。
- **依據**：registry `:757-765`、`:796`。
- **建議決策者**：gate-6 owner Claude、reviewer Human/Ops（`:749-750`）。
- **狀態**：**待簽**。簽署：＿＿＿＿

---

## C. 上線前：production 環境、營運與 GO

### HD-18 production 環境變數與 IAM 前提

- **已設定**（`L-4.1`）：`ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`、`ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`、lease public key 與 production lease store、`ODP_PROD_DEPLOY_URL`、`ODP_PROD_API_URL`、API／Web service 名稱、`ODP_CLOUD_RUN_MIN_INSTANCES=2`、`ODP_AUTH_AUDIENCES`。VPC、LB 與憑證已存在（`L-2.1`、`L-2.6`、`L-2.7`）。
- **已由程式解決**：production 不再需要從 staging registry 拉 image；改為部署前 promotion 到自己的 registry（`deploy-dev.yml:1989-2032`；`deploy_cloud_run_waji.sh:432-440`）。
- **仍待決（未讀回）**：
  - promotion 的 AR 前置：production deployer 對 staging AR 的 reader、對 production AR 的 writer、production AR repository `oday-plus` 存在（design `:469-477`）。
  - `GCP_CLOUD_SQL_INSTANCE` 指向 `oday-prod-foundation-sql`；deployer 有 `prodDeployerSqlBackup`（`deploy-dev.yml:2095-2104`；`infra/terraform/iam.tf:236-262`）。
  - `ODP_INTAKE_CURSOR_SIGNING_KEY_SECRET` 的格式（`deploy_cloud_run_waji.sh:161-164`）。
  - forecast 與 MLflow 變數；principal map secret 的內容。
  - `production-build` environment 的變數（`L-4` 沒有讀回）。
- **建議決策者**：Human/Ops。
- 簽署：＿＿＿＿

### HD-19 production Cloud SQL 基準與 backup checkpoint

- **已決定**（決定 H，2026-11-20）：Cloud SQL 照規格新建，舊 `oday-prod-sql` 不動。新的 `oday-prod-foundation-sql` 已存在：REGIONAL、無 public IPv4、CMEK（`L-2.3`、`L-2.4`）。
- **已由程式解決**：每次 production 部署在 migration 前自動建 on-demand 備份並讀回確認（`deploy-dev.yml:2086-2175`）。不需要人工 checkpoint；備份 ID 在 artifact `production-predeploy-cloud-sql-backup-<sha>-<attempt>`。
- **仍待決**：讀回 `oday-prod-foundation-sql` 的備份與 PITR 設定，並確認 billing 中斷後已有新的自動備份（`backup-dr-impact.md` §3、§6.0）。
- 簽署：＿＿＿＿

### HD-20 production DR 演練

- **已決定**（決定 E，2026-11-27）：GO 前先做 production-representative DR 演練：對 `oday-prod-foundation-sql` 建備份、還原到臨時執行個體、量 RPO／RTO（目標 RPO ≤ 60 分、RTO ≤ 240 分），留收據後刪除臨時執行個體。建立與刪除前 Claude 會再向負責人確認。
- **程序**：`backup-dr-impact.md` §6。
- **仍待決**：執行身分（HD-32）；資料核對的連線方式；是否加做 PITR clone 以實測 RPO；演練備份保留或刪除。
- **待執行、待簽**：演練結果。
- 簽署（結果）：＿＿＿＿　收據：＿＿＿＿

### HD-21 回滾程序與 production 收尾的三個缺口

1. **首次 release commit 後要下線**：**已決定**（決定 D，2026-11-27）採選項 (a)，撤銷 Web 的 `allUsers` invoker、保留資料與服務、以下一個 release 前滾。程序寫在 `rollback-plan.md` §2.4 與 `runbook-core-ops.md` §6。**程序本身待簽；staging 演練（`rollback-plan.md` §5.1）待執行。**
2. **後續 release 的 blue state 保存**：**已由程式解決**。切換前狀態在任何 Cloud Run 變更前擷取並上傳 artifact（`deploy-dev.yml:2034-2083`）。
3. **`capture-state` 參數順序錯誤**：**已由程式解決**（ODP-PROD-DEPLOY-SAFETY-001）。`--project`、`--region` 已放在子命令之前（`deploy-dev.yml:2050-2053`），切換後已沒有這一步。
- **新發現、需要知悉**：前滾 release 會在 Web candidate 部署時就重新開放 `allUsers`，早於流量切換；前滾若在 commit 前失敗，Web 會維持公開（`rollback-plan.md` §2.4.4）。
- **建議決策者**：Release owner + 負責人。
- 簽署（程序）：＿＿＿＿　簽署（演練結果）：＿＿＿＿

### HD-22 值班、告警路由與 watch 參數

- **已決定**（決定 C，2026-11-27）：告警以 email 寄給負責人，負責人擔任 on-call；watch window 30 分鐘；Claude 在上線後 24 小時內每小時讀回健康狀態。live 已有 email 通道與 6 條告警（`L-3`）。
- **仍待決**：
  - watch receipt：修正 task pending（`L-6.2`）；`ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設（`L-4.3`）。
  - `ODP_PROD_API_INVOKER_GROUP`（`L-4.4`）：是否設定，讓值班人員能直接讀 API；要在首次部署前決定（`runbook-core-ops.md` §2.3）。
  - 應用層訊號（audit 寫入失敗、未授權暴增、DLQ）是否補告警（`monitoring-oncall-plan.md` §1.3）。
  - Claude 每小時讀回的項目、身分與紀錄位置（`monitoring-oncall-plan.md` §2.2）。
  - API uptime check 的 live 狀態（`monitoring-oncall-plan.md` §1.2）。
- 簽署（值班表具名）：＿＿＿＿

### HD-23 release notes 核准

- **決定什麼**：`release-notes-draft.md` 的內容與對外公告時間。
- **建議決策者**：Product Owner + Communications Lead。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-24 registry 推進到 production（`staging-verified`），並核准 release decision `go`（production GO）

- **決定什麼**：`release.stage=staging-verified`、`admission_target=production`、`decision=go`；gate-3、gate-5、gate-6 都是 cleared，而且都有綁定 C 的 pass receipt；寫入 `human_signoff`。**這就是 production GO。**
- **依據**：`check_runtime_admission.py:160-278`；`deployment-plan.md` §0、步驟 6。
- **建議決策者**：Human/Ops，也就是負責人。
- **狀態**：**待簽**。

```text
GO 簽署：＿＿＿＿  日期：＿＿＿＿  candidate SHA：＿＿＿＿  manifest digest：＿＿＿＿
```

### HD-25 申請 production lease

- **決定什麼**：請 Supervisor 針對**同一個** staging 封存的 manifest digest，簽發 production deploy lease。
- **依據**：design `:70-75`；`.orchestrator/release_lease.py:1-27`。
- **狀態**：**待簽**。簽署：＿＿＿＿

### HD-26 核准 GitHub `production` environment 的部署

- **依據**：`deploy-dev.yml:959-960`、`:1649-1650`；required reviewers 是 `Alien-alfaloop`、`ajoe734`（`L-4.5`）。`admission` 與 `deploy` 兩個 job 都綁定 `production`。
- **狀態**：**待簽**。簽署：＿＿＿＿

---

## D. 上線後

### HD-27 watch window 結果：通過、撤銷或回滾

- **決定什麼**：30 分鐘 watch window 結束時判定通過；或依 PROD-OPS-05 觸發條件，首次 release 依決定 D 撤銷 Web 公開存取，後續 release 執行 rollback。系統不會自動判定。
- **依據**：`ODP-PROD-OPS-WATCH-PARAMS-001/README.md:22-25`；`runbook-core-ops.md` §6；`rollback-plan.md` §3。
- **建議決策者**：負責人（決定 C）。
- **狀態**：**待簽**。

```text
簽署：＿＿＿＿  watch 開始：＿＿＿＿  結束：＿＿＿＿  結果：□ 通過 □ 撤銷公開存取 □ 回滾
```

### HD-28 staging closeout 與清理

- **已由程式解決**：watch receipt 不存在時，`staging_closeout` 改為 defer、保留 staging，job 不再失敗（`deploy-dev.yml:2674-2707`）。
- **仍待決**：
  - `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` 未設時，job 在讀 receipt 之前就失敗（`deploy-dev.yml:2647`；`L-4.3`）。
  - watch receipt 修正（pending）合併之前，staging 不會被自動清理。要決定由誰、何時以何種程序清理 C 的 staging：`phase=cleanup` 的 `staging_retained_cleanup` 只處理 verify **失敗**而 hold 的 release；verify 成功的 release 要等 TTL 到期、並有 registry supersession 等條件（`deploy-dev.yml:2736-2751`）。
- **依據**：`deployment-plan.md` §5 第 1、2 點。
- **建議決策者**：Human/Ops。
- 簽署：＿＿＿＿

### HD-29 上線後的觀察義務與 owner

- **已決定**：`ODP-NFR-BATCH-002` 對 core-ops 為 not-applicable（決定 F）。
- **仍待決**：
  - `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` 的 owner：registry 寫 Antigravity5（`RELEASE_GATE_REGISTRY.json:797`），`owner_inventory.json:86-88` 寫 Antigravity3。
  - 其餘兩項義務的量測方式：24 小時 P95、月可用率 ≥ 99.5%（`obligation_matrix.json:111-127`、`:170-186`）。
- **建議決策者**：Human/Ops。
- 簽署：＿＿＿＿

### HD-30 gate-3 deviation 的追蹤

- **決定什麼**：`review_by` 到期時重新審查。任何模型取得 production alias 時 deviation 立即失效，要以 `full` 重新上線（design `:265-268`）。
- **建議決策者**：Product Owner + ML Risk Owner。
- **狀態**：**待簽**（上線後）。簽署：＿＿＿＿

### HD-31 release 封存

- **決定什麼**：release evidence manifest 已封存，task、PR、CI、deployment、watch receipt 都齊全。
- **依據**：`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:538`。
- **建議決策者**：Release owner + Human/Ops。
- **狀態**：**待簽**（上線後）。簽署：＿＿＿＿

### HD-32 各項人工操作的執行身分

- **決定什麼**：以下操作由誰、用哪個 GCP 身分執行。唯讀讀回用的 `deborah.lu` 都不能用。
  - 讀 staging release 的 `…-admin-sign-in` secret 並建立 UAT 帳號（`uat-plan.md` §3.2）
  - 決定 D 的撤銷與恢復（`rollback-plan.md` §2.4.2）
  - 決定 E 的 DR 演練（`backup-dr-impact.md` §6）
  - Claude 上線後每小時讀回時讀 release identity（`monitoring-oncall-plan.md` §2.2）
- **建議決策者**：負責人。
- **狀態**：**仍待決**。簽署：＿＿＿＿
