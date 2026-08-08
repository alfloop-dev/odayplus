# ODay Plus — 積壓與部署阻塞盤點（2026-08-08）

證據等級標記：`L1-live` 直接查詢線上資源；`L2-ci` GitHub Actions 執行紀錄；`L3-local` 本機執行；`L4-repo` 版本庫內容。

---

## 1. Deploy Dev 為何連續失敗

**結論：部署流程本身正常，失敗在最後的 fail-closed live E2E 閘門，隨後回滾。**

`L2-ci` run `31248984177`（dev `956170de`，即 #668 合併後的第一次部署）：

```
09:00:30  Running fail-closed live E2E acceptance gate against the promoted release...
09:01:25  Live E2E gate failed. Blocking runtime dependencies:
```

**A 組 — external-data，沒有任何真實 ingestion**

```
data:ingestion_runs: runs=0
data:admin_boundary.official_dataset:run_exists: no persisted ingestion run for a required live provider
data:poi.commercial_api:run_exists:        no persisted ingestion run for a required live provider
```

**B 組 — mlflow，沒有 production 模型**

```
models:registry: versions=0
runtime:model_bindings: mode=mlflow-production-unverified ready=False autoSeeded=False
  error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE
runtime:model_capability:forecastops: available=False reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE
models:forecastops:production_alias: model=forecast_revenue_interval
  versionsWithProductionAlias=0 (exactly one required)
```

閘門擋下後執行 `Deployment failed; restoring the recorded API/Web traffic split.`，把流量還給既有 revision `oday-api-00005-gin`。

### 推論

- **dev 環境跑的是舊 revision，不是近期程式碼。** 自這兩組阻塞出現以來，沒有任何一次部署成功轉正。
- **Cloud Run 上數百個 0% 的 `*-release-*` revision 是症狀不是原因。** 每次部署建 revision → promote → 閘門擋下 → 回滾 → revision 留在 0%。`L2-ci` 最近 7 次部署有 6 次走這條路。
- 這兩組都**不是程式缺陷，是缺真實資料**。對應活看板上 `Human/Ops` 與 `Antigravity` 名下的 blocked 任務。

### 先前一個錯誤結論的更正

PR 上的 `product-e2e-gate` 通過**不代表** live 檢查通過。真正的 live E2E 只在 Deploy Dev 裡對已 promote 的 release 執行，PR 檢查跑的不是它。

---

## 2. 積壓現況

`L2-ci` + `L4-repo`，2026-08-08 盤點：**33 張開啟中 PR**。

| 落後 dev 的幅度 | 張數 |
|---|---|
| ≤ 36 | 5 |
| 100–300 | 8 |
| 500+ | 12 |
| 最大 | 906 |

### 已處理

以下 10 張已併入 dev 並推送，落後幅度從 10–264 降為 **0**，零衝突：

`#691 #688 #672 #665 #658 #643 #641 #640 #634 #472`

其失敗至少部分源於共用原因。`L2-ci` 已在 #691 的 `product` 日誌確認：失敗是 nanoid `GHSA-2v37-7h3g-55p8` 公告，與分支自身內容無關，dev 已修復。

`#692` 已關閉——`package-lock.json` 與 `sbom.json` 與 `origin/dev` 位元組相同，是 no-op（內容隨 #668 進入 dev）。

---

## 3. 六張衝突 PR 的判定

`#680` 實測落後 0、零衝突，GitHub 的 `DIRTY` 標記為過期。其餘五張：

| PR | 落後 | 衝突檔 | 核心檔分歧 | 活看板 task | 判定 |
|---|---|---|---|---|---|
| #579 | 535 | `.orchestrator/github_bus.py` | — | `review_approved` | **已被取代** |
| #574 | 536 | `tests/ops/test_cloud_run_live_deployment.py` | +498 / −148 | `review_approved` | 需重新實作 |
| #555 | 535 | `.orchestrator/test_supervisor.py` 等 2 檔 | +2360 / −103 | `blocked` | 需重新實作 |
| #534 | 758 | `.orchestrator/supervisor.py` 等 3 檔 | +2344 / −417 | **不在看板** | 建議關閉 |
| #508 | 810 | `apps/api/oday_api/main.py` 等 2 檔 | +121 / −22 | **不在看板** | 建議關閉 |

### #579 為何判定為已被取代

`L4-repo` 逐段比對 `.orchestrator/github_bus.py` 的三處衝突：

- **分支側**：`remote_branch_exists(x) or branch_exists(x)`，接受遠端或本機任一存在的分支。
- **dev 側**：`task_id_matches_branch(task_id, explicit)` 守衛、`!= "HEAD"` 檢查，並先解析 canonical per-task refs 再處理可變的 agent registration，程式碼內有註解說明「canonical per-task refs 是不可變的 task identity」。
- dev 側另有 base/head 同 namespace 配對（`refs/remotes/origin/{base}` 對 `refs/remotes/origin/{branch}`），註解指出混用 namespace 會 "manufacture or suppress a PR delta"——**那正是本 task 要解決的問題**。

dev 側已涵蓋且更嚴謹。分支唯一新增的是 `remote_branch_head_sha()`，dev 以 `ls-remote` 為基礎的 `remote_branch_exists()` 取代。**合併此分支會使 dev 倒退。**

### 為何不硬解衝突

在 2000+ 行分歧上手動解核心 supervisor 的衝突，等同於有機會把那數百個 commit 中已修復的缺陷重新引入。這類分支的價值（若仍存在）應以**新 task 針對現行 dev 重新實作**，而非合併。

關閉 PR 不刪除分支且可還原。#534 / #508 對應的 task 不在活看板上，關閉不會使任何 task 懸空；其餘三張若關閉，需同時處理其看板狀態。

---

## 4. 編排狀態未進版本控制

`L4-repo` / `L3-local`：

| 位置 | 大小 | 內容 |
|---|---|---|
| `origin/dev:ai-status.json` | 13 KB | sprint `2026-04-09`、4 個佔位任務 `P1-001`…`P4-001`、全為 `todo` |
| `origin/dev:ai-task-archive/` | — | **0 個檔案** |
| `/home/lupin/oday-plus-supervisor-live/ai-status.json` | **581 KB** | 50 個任務，`updated_at` 持續更新中 |

**版本庫對實際編排狀態零紀錄。** 真正的看板只存在於 supervisor 的工作目錄，從未提交。

### 活看板狀態分布

| 狀態 | 數量 |
|---|---|
| `review_approved` | **23** |
| `blocked` | 15 |
| `todo` | 6 |
| `review` | 6 |
| **`done`** | **0** |

23 個已完成且已核准的任務，`next` 欄位一致寫著 `CI checks for task ... failed; resolve failing checks before ...`，`last_update` 多數停在 2026-08-02。**沒有任何任務走到 `done`。**

### 附帶缺陷

15 個 `status=blocked` 的任務，`blocked_reason` 欄位**全部為空**——標記為阻塞卻未記錄原因，使阻塞無法被追溯或自動處理。

---

## 5. 存取限制（本次盤點的邊界）

`L1-live` 未能取得。本機 gcloud 對 `alfaloop-data-project` 的 Cloud Run 為 `PERMISSION_DENIED`：

- `admin@` / `admin.dep@` / compute SA：已登入但缺 IAM 角色
- `ajoe734@` / `joe.tsai@` / `ray.tsai@`：憑證過期（重新登入後 `ajoe734@` 仍缺角色）
- 專案 IAM policy 中僅 `github-deployer@` SA 具 `roles/run.admin`；無任何使用者帳號具 run 角色
- 模擬該 SA 亦被拒（缺 `roles/iam.serviceAccountTokenCreator`）

因此第 1 節的結論全部建立在 `L2-ci` 執行紀錄上，未經線上直接查證。Cloud Run revision 的實際數量與流量分配未獨立確認。
