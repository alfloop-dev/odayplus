---
doc_id: ODP-RUNTIME-GCP-001-LIVE-DIAGNOSIS-2026-08-03
title: Live GCP Runtime Inventory and Deployment Gate Root-Cause Diagnosis
status: superseded-with-corrections
superseded_by: docs/evidence/ODAY_PLUS_CONSOLIDATED_GAP_AUDIT_2026-08-03.md
audit_date: 2026-08-03
audited_release_sha: 40338298a8088c558376a560e2e7fb98e93ef21d
gcp_project: alfaloop-data-project
region: asia-east1
language: zh-TW
---

# Live GCP Runtime 盤點與部署 Gate 根因診斷

> **更正通知（2026-08-03）**
>
> 本文件原稱 Live E2E gate「唯一 blocking reason 是 ForecastOps 沒有 MLflow
> production alias」。**這是錯的。** 原始判讀以帶關鍵字的 grep 讀取失敗日誌，
> 濾掉了 external-data ingestion 相關行。完整日誌顯示有**兩組獨立 blocker**：
>
> 1. `external-data`：`data:ingestion_runs: runs=0`，`admin_boundary.official_dataset`
>    與 `poi.commercial_api` 皆無 persisted ingestion run
> 2. `model registry`：`models:registry: versions=0`、
>    `versionsWithProductionAlias=0`
>
> 兩組必須各自獨立解決，補齊 ForecastOps 資料**不會**讓 gate 通過。
> 受影響段落為 §3、§5、§7-R4。GCP 資源盤點（§2）、provider gateway 冷啟動
> 診斷（§4.2）與 dependency 圖譜分析（§6）不受此更正影響。
>
> 正確且完整的版本見
> `docs/evidence/ODAY_PLUS_CONSOLIDATED_GAP_AUDIT_2026-08-03.md`。

## 1. 執行摘要

本次以唯讀方式盤點 `alfaloop-data-project`，並直接探測 live runtime。三項結論：

1. **Live dev 部署存在，且 revision 綁定 exact `origin/dev` HEAD**
   （`40338298a808`）。先前「無可回讀 live 環境」的判定不成立。
2. **`Deploy Dev` workflow 失敗點只有最後一關**。build / push / deploy /
   migration smoke / scheduler smoke / worker smoke / Cloud Run live smoke
   全部通過，只有 **Live E2E acceptance gate** 失敗。
3. **Live E2E gate 有兩組獨立 blocking reason**（見上方更正通知）：
   external-data ingestion runs=0，以及 model registry 無 production alias。
   兩者追根究柢都是資料問題，不是基礎設施問題，但必須各自解決。

## 2. Live 資源盤點（`ODP-RUNTIME-GCP-001` 驗收項 #3）

### 2.1 Cloud Run

| 服務 | URL | Latest ready revision |
|---|---|---|
| `oday-api` | `https://oday-api-7sxbjoeozq-de.a.run.app` | `oday-api-release-40338298a808` |
| `oday-web` | `https://oday-web-7sxbjoeozq-de.a.run.app` | `oday-web-release-40338298a808` |
| `oday-mlflow` | `https://oday-mlflow-7sxbjoeozq-de.a.run.app` | `oday-mlflow-00004-872` |
| `odp-provider-gateway` | `https://odp-provider-gateway-7sxbjoeozq-de.a.run.app` | `odp-provider-gateway-00003-8nm` |
| `waji` | `https://waji-7sxbjoeozq-de.a.run.app` | `waji-00003-29q`（非 ODP） |

API image：`asia-east1-docker.pkg.dev/alfaloop-data-project/oday-plus-dev/oday-api:dev-40338298a8088c558376a560e2e7fb98e93ef21d`

### 2.2 Cloud SQL

| 執行個體 | 版本 | 區域 | 狀態 | Tier |
|---|---|---|---|---|
| `oday-dev-sql` | POSTGRES_16 | asia-east1 | RUNNABLE | db-custom-1-3840 |
| `oday-plus-dev-postgres` | POSTGRES_15 | asia-east1 | RUNNABLE | db-f1-micro |

### 2.3 GCS

- `alfaloop-data-project-oday-plus-model-artifacts`
- `oday-dev-source-snapshots-alfaloop-data-project`
- `alfaloop-data-project_cloudbuild`
- `run-sources-alfaloop-data-project-asia-east1`

### 2.4 Secret Manager（12 個）

`oday-plus-dev-api-database-url`、`-api-database-url-pg16`、`-auth-principal-map`、
`-db-password`、`-geocode-gateway-key`、`-google-geocode-key`、`-google-places-key`、
`-intake-cursor-signing-key`、`-mlflow-database-url`、`-mongodb-uri`、
`-web-oidc-client-secret`、`-web-session-secret`

### 2.5 Workload Identity（驗收項 #1、#4）

| 項目 | 狀態 |
|---|---|
| WIF pool `github-actions` | **ACTIVE** |
| Deploy SA `github-deployer@alfaloop-data-project.iam.gserviceaccount.com` | 存在並實際完成部署 |
| 長效 `GCP_SA_KEY` | **未發現**，符合「不得引入長效金鑰」 |

**驗收項 #1、#4 判定：已滿足。** WIF 已可運作 — 本日部署即由此路徑完成。

### 2.6 Deploy identity IAM（驗收項 #2：least privilege）

`github-deployer` 目前綁定 5 個角色：

```
roles/artifactregistry.writer
roles/cloudscheduler.admin
roles/iam.serviceAccountUser
roles/run.admin
roles/serviceusage.serviceUsageConsumer
```

無 `owner` / `editor` 等 primitive role。**驗收項 #2 判定：實質滿足**，
建議後續僅檢視 `cloudscheduler.admin` 是否可降為
`roles/cloudscheduler.jobRunner` + 特定 job 層級授權。

### 2.7 其他 service accounts

`oday-dev-smoke-operator`（RBAC smoke 用）、`oday-dev-scheduler`、
`gke-oday-dev-runtime`、`ci-image-publisher`、`cd-gke-deployer`、
`1067163562451-compute`

## 3. `Deploy Dev` workflow 實際失敗點

Run `30812452823`（head `40338298`，2026-08-03T12:10Z）：

| 步驟 | 結果 |
|---|---|
| Build / push / deploy Cloud Run | ✅ |
| Cloud Run migration Job smoke | ✅ |
| Cloud Run migration compatibility smoke | ✅ |
| Cloud Run scheduler Job smoke | ✅ |
| Cloud Run worker Job smoke | ✅ |
| Cloud Run live deployment smoke | ✅ |
| **Live E2E acceptance gate** | ❌ |
| Cloud Scheduler trigger 還原 | ✅（觸發器原本不存在，刪除 candidate） |

失敗輸出（逐字）：

```
Live E2E gate failed. Blocking runtime dependencies:
  - runtime:model_bindings: mode=mlflow-production-unverified ready=False
    autoSeeded=False error=forecastops: PRODUCTION_MODEL_REGISTRY_UNAVAILABLE:
    forecast_revenue_interval: configured MLflow registry has no production alias
  - runtime:model_capability:forecastops: available=False
    reasonCode=PRODUCTION_MODEL_REGISTRY_UNAVAILABLE
report=.odp_data/deployment/live-e2e-gate.json
```

**[已更正] 實際有兩組獨立 blocker：external-data ingestion runs=0，以及 model
registry 無 production alias。原文此處誤稱只有後者，見文件開頭更正通知。**

`e2e-operational-evidence` job 本身 success。

## 4. Live `/health` 探測結果

### 4.1 目前狀態：`503 unhealthy`

| 依賴 | 狀態 |
|---|---|
| `database` | healthy（PostgreSQL 16，durable、reachable） |
| `job_queue` | healthy |
| `external_providers` | 見 4.2（間歇性） |
| `modes.persistence` | `postgresql` / durable / reachable / production_supported |
| `modes.models` | `productionBindingsReady: false` |
| `modes.data` | `mode: unavailable`, `liveReady: false`, blockingReasons: `["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]` |

注意 `modes.data.operatorRepositoryReady = true`、`persistenceMode = postgresql`、
`errors: []` — **Operator 資料層本身是好的**，是被 model binding 擋住。

### 4.2 成因 A（間歇）：provider gateway 冷啟動

首次探測：

| provider | connectivity | latency | reason |
|---|---|---|---|
| `admin_boundary.official_dataset` | true | 2,953 ms | ok |
| `geocode.primary_api` | true | 1,646 ms | ok |
| `poi.commercial_api` | **false** | – | **timeout** |

暖機後（同一 revision，未改任何設定）重新探測：

| provider | connectivity | latency | reason |
|---|---|---|---|
| `admin_boundary.official_dataset` | true | **133 ms** | ok |
| `geocode.primary_api` | true | **114 ms** | ok |
| `poi.commercial_api` | **true** | **671 ms** | **ok** |

直接打 gateway（暖）三個路徑皆 52–82 ms。

**根因**：`odp-provider-gateway` 只設定 `autoscaling.knative.dev/maxScale=3`，
**沒有 minScale** → scale-to-zero → 冷啟動延遲超過
`ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS=3.0` → health 間歇 503。
`admin_boundary` 的 2,953 ms 距離 3,000 ms 只剩 47 ms，屬同一現象。

三個 provider 全部指向同一個 gateway 服務的不同路徑，共用同一把 key
（`oday-plus-dev-geocode-gateway-key`），因此是單點冷啟動問題，不是憑證問題。

### 4.3 成因 B（持續）：ForecastOps 無 production alias

直接查 live MLflow：

```
GET /api/2.0/mlflow/registered-models/search        → {}
GET /api/2.0/mlflow/registered-models/alias
      ?name=forecast_revenue_interval&alias=production
   → RESOURCE_DOES_NOT_EXIST: Registered Model with
     name=forecast_revenue_interval not found
```

**MLflow registry 完全是空的**，連 registered model 都不存在。

## 5. 根因鏈（完整）

```
ForecastOps 權威資料 1,303 rows 只涵蓋 2026-06-19..22（4 個日曆天）
  → FORECASTOPS_HORIZON_WEEKS = (4, 8, 12, 24) 需要每店
    28 / 56 / 84 / 168 天「連續」日資料才能成窗
    (product_ops/modeling/forecast_training.py: len(window) != horizon_days → skip)
  → 0 個 horizon 訓練樣本 → 訓練 fail closed（execution 2dzlg），無 DEV candidate
  → MLflow registry 空，無 forecast_revenue_interval production alias
  → productionBindingsReady = false
  → modes.data.mode = unavailable / liveReady = false
  → /health 503 + Live E2E acceptance gate 失敗
  → Deploy Dev workflow 失敗
  → ODP-P10-DEV-REDEPLOY-VERIFY-001 / ODP-LIVE-RUNTIME-DEV-COMPOSE-001 /
    ODP-RUNTIME-GCP-001 全部維持 blocked
  → Gate 2 / 3 / 5 / 6 無法取得 receipt
```

**[已更正] 這是兩條關鍵路徑之一。另一條是 required provider 的真實 ingestion，
兩者必須各自解決。**

## 6. Task dependency 圖譜損壞

`ODP-RUNTIME-GCP-001` 的 `depends_on` 有 3 項，展開後共 9 個不重複依賴。
以 Control Pack §3.1 的 resolver 規則（必須在 live board 或官方 archive 解析成立）
逐一檢查：

| 依賴 | live board | 官方 archive | repo evidence | resolver |
|---|:---:|:---:|---|:---:|
| `ODP-AUTH-RUNTIME-RECONCILE-001` | ✗ | ✗ | `docs/evidence/runtime/…` 有 | **無法成立** |
| `ODP-MODEL-READY-COMPOSE-001` | ✗ | ✗ | `docs/evidence/model_ready/…` 有 | **無法成立** |
| `ODP-LEARNINGHUB-PROD-FIX-001` | ✗ | ✗ | `docs/evidence/completion/…` 有 | **無法成立** |
| `ODP-HEATZONE-PIT-LABEL-AUTHORITY-001` | ✗ | ✗ | `docs/evidence/runtime/…` 有 | **無法成立** |
| `ODP-P10-DEV-LANDING-FIX-001` | ✗ | ✗ | `docs/evidence/fleet_dispatch/…` 有 | **無法成立** |
| `ODP-OPERATOR-LIVE-PREFLIGHT-001` | ✗ | ✗ | `docs/evidence/completion/…` 有 | **無法成立** |
| `ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001` | ✗ | ✗ | **無** | **無法成立** |
| `ODP-MODEL-CAPABILITY-READINESS-001` | ✗ | ✗ | **無** | **無法成立** |
| `ODP-P10-R3CD-DEV-COMPOSE-001` | ✗ | ✗ | **無** | **無法成立** |

官方 archive 目前只有 32 筆（28 completed / 4 superseded），這 9 個依賴
**一個都不在裡面**。依 Control Pack §3.1「派工前必須驗證所有 dependency 已透過
live 或 archive resolver 成立」，這三個部署 task **在圖譜修好之前永遠不會被派工**。

### 6.1 循環依賴

```
ODP-RUNTIME-GCP-001  ──depends_on──▶  ODP-PRODUCTION-MODEL-REGISTRY-001
                                              │
        acceptance 要求「Remote MLflow resolves the ForecastOps
        production alias」→ 需要 live GCP runtime 才能滿足
                                              │
                                              ▼
                                     需要 ODP-RUNTIME-GCP-001
```

基礎設施佈建被綁在模型核准後面，是依賴方向倒置。

## 7. 建議修復方案

### R1 — 立即（工程，低風險，可解間歇 503）

為 `odp-provider-gateway` 設定 `minScale=1`，或把
`ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS` 由 3.0 調高到涵蓋冷啟動
（實測冷啟約 1.6–3.0 s，建議 8.0），二擇一或並用。
建議優先 `minScale=1`：probe timeout 調高會延後真實故障的偵測。

> 本項需要異動 live Cloud Run 設定，屬 outward-facing 變更，未在本次執行。

### R2 — 修復 dependency 圖譜（工程）

1. 對 6 個「有 repo evidence 但不在 archive」的依賴，用官方 CLI 補寫
   archive snapshot（`terminal_status=done`，附既有 evidence 路徑）。
2. 對 3 個「完全無 evidence」的依賴（`ODP-FORECAST-LEARNINGHUB-TEMPORAL-COMPOSE-001`、
   `ODP-MODEL-CAPABILITY-READINESS-001`、`ODP-P10-R3CD-DEV-COMPOSE-001`）
   確認是否真正存在過；若否，從 `depends_on` 移除並記錄決策。
3. 加一個 CI check：`depends_on` 的每個 id 必須能被 resolver 解析，否則 fail closed。
   目前沒有這個檢查，才會累積出 9 個死依賴。

> 本項需要異動 live supervisor 狀態，屬 outward-facing 變更，未在本次執行。

### R3 — 打破循環依賴（工程 + 治理決策）

把 `ODP-PRODUCTION-MODEL-REGISTRY-001` 拆成兩個 task：

| 新 task | 內容 | 依賴 |
|---|---|---|
| `…-INFRA-001` | MLflow tracking/registry 可達性、artifact bucket、alias 解析機制、governed-disabled binding 契約 | 不依賴任何模型核准 |
| `…-GOVERNANCE-001` | ForecastOps 真實訓練 → DEV → SHADOW → production alias → model card → rollback candidate | 依賴 INFRA-001 + 資料回填 |

如此 `ODP-RUNTIME-GCP-001` 只需依賴 `…-INFRA-001`，即可先取得基礎設施驗收。

### R4 — 關鍵路徑之一（Human/Ops）

`ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`：回填權威每日交易歷史。
需求規格見 `docs/evidence/models/forecastops/human-data-gate/`。
**在此完成前，R1–R3 全部做完仍無法讓 Live E2E gate 通過。**

## 8. 對既有判定的修正

| 先前判定 | 修正後 |
|---|---|
| 「無可回讀 live 環境」 | **不成立**。live dev 存在且 revision = exact `origin/dev` HEAD |
| 「ForecastOps 是唯一可訓練模型（1,303 rows）」 | **row 數正確，但實務上不可訓練**。4 天資料無法形成任何 4/8/12/24 週窗口 |
| 「三個模型 data-blocked」 | **四個全部 data-blocked**（含 ForecastOps） |
| 「P0-1 是修 WIF 變數」 | **WIF 已可運作**。真正的 P0 是 dependency 圖譜 + ForecastOps 歷史資料 |

## 9. 本次未執行的動作

以下屬 outward-facing 變更，已列入建議但未執行，待 Human/Ops 決定：

- 修改 `odp-provider-gateway` 的 Cloud Run scaling 設定
- 修改 live supervisor `ai-status.json` 或 `ai-task-archive/`
- 重跑 `Deploy Dev` workflow
- 任何 GCP IAM 異動

本次所有 GCP 與 live 端點操作均為唯讀（`list` / `describe` / `GET`）。
