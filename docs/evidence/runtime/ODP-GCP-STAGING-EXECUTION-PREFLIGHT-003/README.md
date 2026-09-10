# GCP staging 唯讀 preflight 分類修正

本交付沿用 2026-09-08 11:46:07–09 UTC 的原始觀測。2026-09-10 的變更是修正該觀測的 release contract 分類，沒有重新執行 GCP／GitHub vars probe，也沒有新增部署或資源變更。

- production 的 `ODP_PROD_API_URL`、`ODP_PROD_DEPLOY_URL` 依 `product_ops/deployment/deploy_cloud_run_waji.sh:43-51` 列為 required，原值均通過 HTTPS 字串 guard；這不代表 endpoint 可達或 runtime 已驗證。
- dev／production 的 tenant 依 shell `:104-106,346-349` 為 required alternative：優先 `ODP_SCHEDULED_INGESTION_TENANT_ID`，空值時才採 `ODP_TENANT_ID`。inventory 以群組記錄，沒有把兩個 member 都列成獨立必填。
- staging 仍使用 Terraform tenant authority；dev／staging 的 production URLs 保留 not_applicable。
- `release_contract_model`、`variable_inventory` 與六項 checks 同步；230 項總數保留，摘要為 93 pass／15 fail／18 error／30 unknown／61 not_applicable／11 informational／2 skip。六項原值已存在，沒有新增缺值 blocker；25 項 remediation 與原權限界線保留。
- 每項衍生 GitHub variable check 的 `timestamp_utc` 改為對應 receipt 的觀測時間；根層 `execution_timestamp_utc` 保留原 run anchor。原 executor identity 不改寫成這次文件修正者。

前景修正者 Codex；審查者 Claude（針對 Codex 新修正獨立審查）。原六次退件歷史保留。指定 verification 為 none，本次只做既有來源靜態比對、JSON 解碼／計數及 26 份 raw receipt SHA-256 不變檢查；没有重跑 tests/build/lint/scan。新 head 仍須獨立審查、required CI 與合併，不能把文件修正升格為 live readiness 或 Human GO。
