# Deployment Environments

Source baseline: `ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN`,
`ODP-OPS-02_DEPLOYMENT_AND_ENVIRONMENT_MANAGEMENT`,
`ODP-OPS-04_RUNBOOK`.

| Environment | Purpose | Data | Promotion rule |
|---|---|---|---|
| `local` | Developer compose stack and smoke checks | Synthetic/local only | No promotion. |
| `dev` | Integration baseline and migration rehearsal | Non-production snapshots | Merge to `dev` and deploy immutable image. |
| `staging` | Release candidate validation | Production-like masked data | All release gates passed or documented deviation. |
| `prod` | Production serving and governed jobs | Production | Approved release, backup checkpoint, rollback owner. |

Required environment variables:

| Variable | Required in | Purpose |
|---|---|---|
| `ODAY_ENV` | all | Runtime environment label. |
| `ODAY_DATABASE_URL` | API, Web, worker, migration | PostgreSQL connection string from secret manager. |
| `ODAY_LOG_FORMAT` | all | Use `json` for shared structured logging. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | deployed envs | Trace export endpoint. |

Secrets must be injected by the deployment platform. They are never committed to
Terraform variable files, Docker compose, or CLI plan outputs.

The target lifecycle, release gates, ephemeral staging isolation, production
blue-green rollout, and Supervisor/Auto Worker task DAG are defined in
[`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md).

## Unified Single-Path Runtime Release Pipeline

The system uses a single CI/CD release workflow entrypoint (`.github/workflows/deploy-dev.yml`, named `Runtime Release`) to orchestrate releases across all environments:

1. **Admission Gate**: Authoritative verifier (`delivery_toolchain/release/check_runtime_admission.py`) checks the Ed25519-signed Supervisor release lease and staged gate registry (`RELEASE_GATE_REGISTRY.json`) for the requested environment (`dev`, `staging`, `production`).
2. **Build Once**: The first dispatch leaves the four optional image handoff inputs empty, so a dedicated `build` job runs secret scanning, SAST (Bandit), SBOM generation, and container image builds/Cosign signing once. It resolves the pushed tags to four immutable digests and publishes them as the cross-environment handoff. Staging and production dispatches pass all four `repo/service@sha256:...` values, skip the build job, and reuse the exact same images.
3. **Deploy by Digest**:
   - **`dev`**: Deploys immutable digests, executes migrations, runs live preflight, Cloud Run Job validations, and live E2E gate.
   - **`staging`**: Provisions short-lived ephemeral staging instance with isolated database schema, tenant partitioning, and masked snapshot via `staging_lifecycle.py create`; executes the 9-stage rehearsal verification via `staging_lifecycle.py verify`, including a real rollback-target switch/health/restore and a live public-egress deny probe; checks remote staging proof; then the independent `environment: staging` closeout job verifies the production watch receipt against the same candidate/manifest/release and cleans up with staging WIF, or holds up to 24h for debugging on failure (`staging_lifecycle.py hold`).
   - **`production`**: Deploys green revisions (0% public traffic), validates green smoke and IAM bindings, atomistically promotes traffic to green (100%), updates Cloud Scheduler targets to green digests, and arms fail-closed rollback primitives.

## 短生命週期 Staging 生命週期與整合架構 (Ephemeral Staging Lifecycle Integration)

依據 `EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md` 規劃，Staging 環境不再使用長期靜態基礎設施或沿用 dev 身份，而是完全整合進唯一 `Runtime Release` 狀態機：

### 1. Release-Scoped 權限與資源邊界
- **唯一權限來源**：由 `staging_lifecycle.py create` 產生並寫入 output handoff 的 release-scoped lifecycle outputs（隔離 Database `stg_<slug>_<hash>`、專屬 User `stg_<slug>_<hash>_app`、Storage Bucket `stg-<slug>-<hash>-data`、Tenant ID `tenant-<slug>-<hash>`、專屬 Service Accounts `stg-...-rt` / `stg-...-web` / `stg-...-wkr`、Cloud Run Services 與 Jobs）作為 staging 部署的唯一 authority。靜態環境變數僅提供長期共用的 foundation inputs（專案 ID、區域、底層 VPC、Cloud SQL instance、CMEK、Terraform deployer SA）；不得以 static URL、service/job 名稱或 dev operator 取代 outputs。
- **最小權限身份隔離**：Staging smoke proof 與驗證嚴禁冒用（impersonate）dev smoke operator 身份；必須使用 release-scoped 專屬 least-privilege service account (`sa_runtime` / `sa_web`)。
- **排程初始狀態**：Staging 的 Cloud Scheduler trigger 建立時預設為 `PAUSED`（`paused = true`），避免在 rehearsal 驗證前自動觸發排程。
- **Durable state**：Terraform 使用受保護的 GCS backend，以 `oday-plus/ephemeral-staging/<release_id>` 作為每個 release 的獨立 state key；recovery sidecar 與 output handoff 亦寫入同一受保護 storage，不能依賴 runner `/tmp` 或一般 artifact。

### 2. 九階段 Rehearsal 演練 (9-Stage Rehearsal Verification)
由 `product_ops/deployment/staging_lifecycle.py verify` 執行完整演練狀態機；五個 release-scoped Cloud Run service/job 均以 `ALL_TRAFFIC` 經受控 VPC，並由 live probe 證明 public destination 被 default-deny foundation 拒絕：
1. **DB Expand Migration**：驗證資料庫向後相容的 expand migration 與新舊 schema 相容性。
2. **Data Platform Snapshot**：演練 masked snapshot 資料物化與契約 readback。
3. **API / Web Authenticated Smoke & E2E**：以 staging 專屬權限執行 API/Web authenticated smoke 與端對端測試。
4. **Worker Idempotency**：驗證 worker 工作冪等性、重試、dead-letter/quarantine 機制。
5. **Scheduler One-Shot**：驗證暫停排程之單次手動觸發與執行。
6. **Backup Checkpoint & Restore Drill**：執行 release-scoped point-in-time 備份與還原演練。
7. **Rollback Rehearsal**：建立無流量 release revision，切換至該 revision 做 health readback，再實際回切已核准的既有 revision，並驗證 traffic allocation 等價。
8. **Public Egress Deny Probe**：從 release-scoped worker job 執行固定 public canary；只有連線被拒絕才算通過。
9. **External Providers Disabled Readback**：確認 16 個外部資料來源維持 `disabled`、零 provider credentials 且 public egress 維持 default-deny。

所有階段皆產生不含機密值的 secret-free 收據（`secret_values_redacted: true`），並上傳至工作流程 artifacts（`.odp_data/staging-lifecycle/`）。

### 3. TTL 保留、精確清理與回滾 (TTL Retention, Cleanup & Rollback)
- **成功發布清理**：只有 production watch window 的 durable closeout receipt 驗證通過後，才觸發 release-scoped 精確清理，僅依精確 labels 刪除對應資源，嚴禁使用萬用字元（wildcard）；staging verify 成功本身不會立即刪除環境。
- **失敗除錯保留 (Hold)**：Staging 驗證若失敗，觸發 `staging_lifecycle.py hold`，環境依 TTL 保留供除錯（預設不超過 24 小時，延長須附帶 owner 與 reason，最高 168 小時）。
- **Orphan Scanner**：每小時定期執行孤兒資源掃描，對超過 TTL 之短生命週期資源 fail-closed 告警並安全清理。
- **Rollback 機制**：若 staging 階段發生嚴重錯誤，立即中斷部署管線、保留失敗環境供除錯，dev 與 prod 不受任何影響；生產環境若有異常則依 blue-green 迅速回切 blue 版本，不執行破壞性 down migration。
