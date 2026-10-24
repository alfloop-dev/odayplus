# core-ops production GO：人工決策清單（DRAFT · 全部未簽署）

> **全部項目都是 DRAFT／UNSIGNED。** 本清單不代表任何決定已經做出。
>
> - 「建議決策者角色」取自 repo 文件；**姓名一律留白**，由負責人（蔡尚志）指派或親自簽署。
> - 簽署後的結果要寫進 registry、manifest 或 lease 時，必須由**另一個 task** 以 evidence-only commit 寫入。本 task 不修改 registry。
> - 行號以 `origin/dev` @ `5d761526` 為準。

**每一項的簽署格式：**

```text
簽署人：＿＿＿＿＿＿  角色：＿＿＿＿＿＿  日期（ISO）：＿＿＿＿＿＿
決定：□ 核准  □ 附條件核准  □ 拒絕  □ 不適用
條件／備註：＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
```

---

## A. 上線前：範圍與程式

### HD-01 接受本準備包作為審閱基礎

- **決定什麼**：本資料夾的 10 份文件是否可以作為 gate-6 文件類 check（release note、deployment plan、rollback plan、runbook、backup/DR review）的審閱稿。
- **依據**：`gate-evidence-map.md` 的 gate-6；`RELEASE_GATE_REGISTRY.json:456-462`。
- **建議決策者**：gate-6 reviewer Human/Ops（`:448`）。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-02 PR #1565（首次 staging／production release 回滾語意）是否合併

- **決定什麼**：是否採方案 A，讓 production 首次 release 的失敗回復是「刪除 candidate、零流量」，ephemeral staging 則是「hold 供鑑識」。
- **依據**：PR #1565 branch 的 design §5（branch `:118-220`）；`rollback-plan.md` §2.2。在 `origin/dev` 現行規則下，staging 與 production 的首次 release 必須綁定 `rollback_release`（design `:118-126`）。
- **建議決策者**：Release owner + Human/Ops（design `:145`）。
- **狀態**：UNSIGNED，PR 待合併。簽署：＿＿＿＿

### HD-03 F1-a：首次 production release 失敗後的 lineage 解鎖政策

- **決定什麼**：首次 production release 在 lease 消耗後失敗，而下一個候選是不同 manifest 時，要怎麼處理。
- **依據**：PR #1565 branch design `:197`、`:219`。
- **建議決策者**：Release owner + Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-04 F1-b：staging 讀 production lease store 的設定與 IAM

- **決定什麼**：
  - staging environment 是否設定 `ODP_PRODUCTION_RELEASE_LEASE_STATE_URI`；
  - staging admission 身分是否取得 production lease bucket 的唯讀權限。

  未完成前，staging 首次 release 一律被拒絕（PR #1565 合併後）。
- **依據**：PR #1565 branch design `:195`、`:220`。
- **建議決策者**：Human/Ops（IAM、GitHub environment）。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-05 選定 core-ops candidate SHA，並觸發 staging build

- **決定什麼**：選哪一個 `origin/dev` commit 當 candidate；以 `phase=build`、`environment=staging`、`release_profile=core-ops` 觸發 build；以及回滾綁定的方式（依 HD-02 的結果）。
- **依據**：`deployment-plan.md` §2 步驟 1；`.github/workflows/deploy-dev.yml:140-155`。
- **建議決策者**：Release owner。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-06 F2：production Terraform 模型契約與 core-ops 如何並存

- **決定什麼**：`terraform_data.production_contract` 的模型 precondition 在 core-ops 下如何處理。不得填假值。
- **依據**：design `:128-139`、`:146`。
- **建議決策者**：Infra owner + ML Risk Owner。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

---

## B. 上線前：gate 處置

### HD-07 gate-0、gate-1、gate-4 為 core-ops candidate 重新綁定；gate-4 對 staging 與 production 的處置

- **決定什麼**：
  - 新 candidate 的 Code Gate、Contract Gate、Security Gate receipt。
  - gate-4 的 deviation 只限原本的 dev candidate。staging 與 production 需要權威的 H01 證據、處理掉 finding 或重新處置，並重新核准。
- **依據**：`RELEASE_GATE_REGISTRY.json:378`；`gate-evidence-map.md` 附註。
- **建議決策者**：gate-4 owner Claude、reviewer Antigravity2（`:331-332`）；deviation 核准由 Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-08 gate-2 Data Gate 的處置（staging admission 的前提）

- **決定什麼**：每個 check 是證據通過，還是處置為 not-applicable 或 passed-with-deviation。特別是「model-ready data 的 point-in-time」與「source contract」兩項（core-ops 不用模型，外部來源全關）。
- **依據**：`gate-evidence-map.md` 的 gate-2；registry `:256-261`、`:281-284`。
- **建議決策者**：gate-2 owner Codex2、reviewer Claude（`:247-248`），以及 Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-09 registry 推進到 staging（`dev-verified`），並核准 release decision `go`（staging）

- **決定什麼**：`release.stage=dev-verified`、`admission_target=staging`、`decision=go`，以及 `human_signoff`。
- **依據**：`check_runtime_admission.py:149-186`；`check_release_gate_registry.py:64-71`；現行 registry 的 `human_signoff` 格式（`:26-30`）。
- **建議決策者**：Human/Ops（`decision_owner`，`:23`）。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-10 申請 staging lease

- **決定什麼**：請 Supervisor 針對 staging build 的 manifest digest 簽發 staging deploy lease。
- **依據**：`.orchestrator/release_lease.py:1-21`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-11 staging 前提的 IAM 與網路（design §12.5）

- **決定什麼**：是否授予以下權限與連線：
  - deployer 對 release runtime SA 的 `roles/iam.serviceAccountOpenIdTokenCreator`；
  - runtime SA 對 API／Web 的 `roles/run.invoker`；
  - verify runner 能連到 API；
  - API 能連到 JWKS（Private Google Access）。
- **依據**：design `:277-287`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-12 核准 GitHub `staging` environment 的部署

- **決定什麼**：staging deploy dispatch 時，在 GitHub 按下核准。
- **依據**：`deploy-dev.yml:735-736`、`:1179-1180`；`docs/deployment/GCP_DEPLOY_GUIDE.md:14`。
- **建議決策者**：GitHub `staging` required reviewers。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-13 UAT 環境、資料與帳號

- **決定什麼**：
  - UAT 在 ephemeral staging 跑，還是在 production 對外公告前跑。staging 目前沒有密碼帳號路徑，見 `uat-plan.md` §2。
  - 由誰建立帳號，其中必須有一個非 `platform_admin` 的 `operations_manager` 帳號。
  - PriceOps、AdLift、Growth、Franchisee 是否納入 UAT。
- **依據**：`uat-plan.md` §1–§3；design `:256-262`。
- **建議決策者**：Product Owner + QA Lead（`docs/uat/UAT_ACCEPTANCE_PLAN.md:6`）。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-14 營業日 cutoff 時間

- **決定什麼**：每日 batch 的營業日 cutoff。repo 內沒有定義。
- **依據**：`obligation_matrix.json:140-167`；`disposition.json:133`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-15 gate-3 passed-with-deviation（core-ops）簽署

- **決定什麼**：簽署 design §8 的草稿，或維持 `blocked`。要填 `risk_owner`、`approved_by`、`approved_at`、`review_by`（建議不超過 90 天）。另外，「human approval and rollback path recorded」不在 deviation 清單內，簽署本身就是這項的 human approval，回滾路徑則依 HD-21。
- **依據**：design `:151-189`；`gate-evidence-map.md` 的 gate-3。
- **建議決策者**：Product Owner + ML Risk Owner（design `:147`）。
- **狀態**：UNSIGNED。

```text
risk_owner：＿＿＿＿  approved_by：＿＿＿＿  approved_at：＿＿＿＿  review_by：＿＿＿＿
```

### HD-16 gate-5 處置

- **決定什麼**：
  - E2E receipt 的範圍：模型相依的 scenario 怎麼算；
  - live gate 的 receipt；
  - 效能量測：staging 負載測試、render budget；
  - UAT 結果：零 P0／P1，並完成簽核。
- **依據**：`gate-evidence-map.md` 的 gate-5；registry `:407-411`。
- **建議決策者**：gate-5 owner Codex2、reviewer Human/Ops（`:398-399`）；UAT 簽核由 Product Owner、QA Lead、Security、SRE、Business Owner。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-17 gate-6 處置

- **決定什麼**：七項 check 的處置，見 `gate-evidence-map.md`。特別是 evidence manifest 與 RTM 對照的產生方式。
- **依據**：registry `:456-462`、`:494`。
- **建議決策者**：gate-6 owner Claude、reviewer Human/Ops（`:447-448`）。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

---

## C. 上線前：production 環境、營運與 GO

### HD-18 production 環境變數與 IAM 前提

- **決定什麼**：逐項確認並設定 `deployment-plan.md` §4 的清單：
  - `ODP_CLOUD_RUN_VPC_EGRESS`
  - production VPC apply
  - custom domain
  - smoke SA 與 principal map
  - forecast 與 MLflow 變數
  - lease public key 與 store
  - **production runtime 能否從 staging build 的 Artifact Registry 拉取 image**
- **依據**：`deployment-plan.md` §4；`docs/evidence/runtime/ODP-PROD-RUNTIME-RELEASE-PATH-001/README.md:197-201`、`:268-272`；`GCP_DEPLOY_GUIDE.md:202-208`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-19 production Cloud SQL 基準與 backup checkpoint

- **決定什麼**：
  - 是否在上線前把 `oday-prod-sql` 調整到 IaC 基準：tier、REGIONAL、deletion protection、public IP；
  - 讀回 backup 與 PITR；
  - deploy 前執行一次 backup checkpoint 並記錄 ID。
- **依據**：`backup-dr-impact.md` §3、§6；`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:265`。
- **建議決策者**：Human/Ops + SRE Owner。
- **狀態**：UNSIGNED。簽署：＿＿＿＿　備份 ID：＿＿＿＿

### HD-20 是否接受「production DR 演練未完成」的風險

- **決定什麼**：核准文件明言，production-representative DR drill 是 production-go 前的必要後續項。是要先完成演練，還是具名接受風險。
- **依據**：`docs/design/ODP-PGAP-RELIABILITY-001_SLO_AND_DR_TARGETS_RATIFICATION.md:56-58`；`obligation_matrix.json:199`。
- **建議決策者**：負責人。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-21 回滾程序與 production 收尾的三個缺口

- **決定什麼**：
  1. 首次 release commit 後要下線時採哪個選項，見 `rollback-plan.md` §2.4；
  2. 後續 release 的 blue state 如何保存，見 `rollback-plan.md` §3；
  3. 既有 blocker：workflow 的 production `capture-state` 參數順序錯誤，deploy job 會在流量已切到 100% 之後 failure，`staging_closeout` 被 skip。要在 production GO 前另開 task 修正 workflow（會讓既有 candidate 失效，需重新建置），還是具名接受「production 已上線但 run 失敗、staging 由人工收尾」的路徑。見 `deployment-plan.md` §5 第 1 點。

  前兩項要在 staging 演練。
- **依據**：`rollback-plan.md`；`deployment-plan.md` §5；`deploy-dev.yml:1609-1615`、`:1681`；`bluegreen_release.py:985-989`。
- **建議決策者**：Release owner + Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-22 值班、告警路由與 watch 參數

- **決定什麼**：
  - 填寫值班表（`monitoring-oncall-plan.md` §4）；
  - 告警通道（`ONCALL_ENDPOINT_URL`）與要建立哪些告警；
  - watch window 長度（15 分鐘還是 30 分鐘）與 p95 門檻；
  - watch receipt 由誰產生、上傳到哪個 `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`。
- **依據**：`monitoring-oncall-plan.md`；`GCP_DEPLOY_GUIDE.md:208`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-23 release notes 核准

- **決定什麼**：`release-notes-draft.md` 的內容與對外公告時間。
- **建議決策者**：Product Owner + Communications Lead。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-24 registry 推進到 production（`staging-verified`），並核准 release decision `go`（production GO）

- **決定什麼**：
  - `release.stage=staging-verified`、`admission_target=production`、`decision=go`；
  - gate-3、gate-5、gate-6 都是 cleared，而且都有綁定 candidate SHA 的 pass receipt；
  - 寫入 `human_signoff`。

  **這就是 production GO。**
- **依據**：`check_runtime_admission.py:149-260`；`deployment-plan.md` §0、步驟 6。
- **建議決策者**：Human/Ops，也就是負責人。
- **狀態**：UNSIGNED。

```text
GO 簽署：＿＿＿＿  日期：＿＿＿＿  candidate SHA：＿＿＿＿  manifest digest：＿＿＿＿
```

### HD-25 申請 production lease

- **決定什麼**：請 Supervisor 針對**同一個** staging 封存的 manifest digest，簽發 production deploy lease。
- **依據**：design `:71`；`.orchestrator/release_lease.py:1-27`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-26 核准 GitHub `production` environment 的部署

- **決定什麼**：production deploy dispatch 時，在 GitHub 按下核准。`admission` 與 `deploy` 兩個 job 都綁定 `production`。
- **依據**：`deploy-dev.yml:735-736`、`:1179-1180`；`GCP_DEPLOY_GUIDE.md:15`。
- **建議決策者**：GitHub `production` required reviewers。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

---

## D. 上線後

### HD-27 watch window 結果：通過或回滾

- **決定什麼**：在 watch window 結束時判定通過，或依觸發條件執行回滾。系統不會自動判定。
- **依據**：`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:288-309`；`monitoring-oncall-plan.md` §2.1。
- **建議決策者**：watch owner、rollback owner（Human/Ops）。
- **狀態**：UNSIGNED。

```text
簽署：＿＿＿＿  watch 開始：＿＿＿＿  結束：＿＿＿＿  結果：□ 通過 □ 回滾
```

### HD-28 staging closeout 與清理（或延長 hold）

- **決定什麼**：
  - 以現行 workflow，`staging_closeout` 會因 deploy job failure 而被 skip（`deployment-plan.md` §5 第 1 點），staging 不會自動清理。要決定由誰、用什麼程序清理 staging，或延長 hold，延長時要附 owner 與原因，上限 168 小時。
  - 第 1 點修正之後，`staging_closeout` 會在 deploy 成功後立即執行，並要求 watch receipt 已存在（`deployment-plan.md` §5 第 2 點）。要決定 watch receipt 上傳之後如何重跑或確認 `staging_closeout`。
- **依據**：`deploy-dev.yml:1678-1813`、`:1681`；`deployment-plan.md` §5 第 1、2 點；`staging_lifecycle.py:62`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-29 上線後的觀察義務與 owner

- **決定什麼**：
  - 確認 `ODP-POSTDEPLOY-WATCH-CLOSEOUT-001` 的 owner。registry 寫 Antigravity5（`:495`），`owner_inventory.json:86-88` 寫 Antigravity3。
  - 接受三項觀察義務的量測方式：24 小時 P95、連續 7 個營業日、月可用率 ≥ 99.5%。
- **依據**：`obligation_matrix.json:111-186`。
- **建議決策者**：Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-30 gate-3 deviation 的追蹤

- **決定什麼**：`review_by` 到期時重新審查。任何模型取得 production alias 時，deviation 立即失效，要以 `full` 重新上線。
- **依據**：design `:184-188`。
- **建議決策者**：Product Owner + ML Risk Owner。
- **狀態**：UNSIGNED。簽署：＿＿＿＿

### HD-31 release 封存

- **決定什麼**：release evidence manifest 已封存，task、PR、CI、deployment、watch receipt 都齊全。
- **依據**：`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md:513-514`。
- **建議決策者**：Release owner + Human/Ops。
- **狀態**：UNSIGNED。簽署：＿＿＿＿
