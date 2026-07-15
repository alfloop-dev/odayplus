# ODay Plus Project Documentation Archive

本目錄是 9 個正式專案文件壓縮檔的整理歸檔版，並保存後續收到的設計來源包。根目錄的原始 zip 已複製到 `00_source_zips/`，所有正式 Markdown 文件已展開並依批次與主題歸類。

## Archive Status

- Formal source archives: 9 zip files
- Supplemental design sources: 1 canonical Operator Console package under `00_source_zips/operator_console/`
- Operator Console package delivery: tracked on `dev` by `ODP-OC-R4-014`; Fleet workers must use its `LATEST.json` preflight
- Extracted formal documents: 72 Markdown files
- Total extracted document length: 28,438 lines
- Archive generated from root-level `oday_plus_batch_*.zip` files
- `scripts/` 內的同名 zip 視為備份來源，本次未混入歸檔版

## Directory Map

| Folder | Documents | Purpose |
|---|---:|---|
| `00_source_zips/` | 9 formal + 1 design | 原始壓縮檔與 Operator Console 設計交付包保存區 |
| `01_project_governance/` | 5 | 範圍、名詞、RACI、文件治理、需求追蹤 |
| `02_sa_documents/` | 10 | 業務架構、流程、角色、功能與非功能需求 |
| `03_data_integration/` | 7 | 資料來源、交換契約、Connector、Canonical Model、資料品質 |
| `04_platform_sd/` | 12 | 系統架構、GCP 部署、服務邊界、API、Event、資安、CI/CD |
| `05_module_design/` | 12 | 各業務與平台模組詳細設計 |
| `06_ai_causal_optimization/` | 6 | AI/ML、特徵與標籤、模型、因果推論、最佳化 |
| `07_ux/` | 5 | 資訊架構、設計系統、畫面互動、地圖視覺化、前端技術 |
| `08_qa_acceptance/` | 7 | 測試策略、E2E、模型驗收、效能資安 DR、UAT、查核證據 |
| `09_operations/` | 8 | WBS、資料重算、部署、Runbook、事件復原、模型發布、操作手冊 |

## Project Reading Map

建議以這個順序閱讀與落地：

1. `01_project_governance/`：先鎖定系統範圍、共用語意、文件權威與 RTM。
2. `02_sa_documents/`：理解業務生命週期、TO-BE 流程、角色權限與需求基線。
3. `03_data_integration/`：確認資料來源、Canonical Model、mapping、model-ready views 與時間語意。
4. `04_platform_sd/`：落到系統架構、部署、API/Event、工作流、資安與可觀測性。
5. `05_module_design/`：逐一拆解 HeatZone、Listing、SiteScore、ForecastOps、InterventionOps、PriceOps、AdLift、AVM、NetPlan、Learning Hub、OpsBoard。
6. `06_ai_causal_optimization/`：補上模型治理、因果推論、實驗設計與最佳化決策邊界。
7. `07_ux/`：把角色、模組、決策卡、地圖、表格與核准流程轉成前端體驗。
8. `08_qa_acceptance/`：定義測試、驗收與查核證據。
9. `09_operations/`：建立開發計畫、部署、日常維運、事件處理、模型發布與使用者手冊。

## Key Architecture Notes

- ODay Plus 是一套展店、營運、AI 預測、因果評估與網路規劃平台，不只是單一前端或單一模型。
- 文件反覆強調 Prediction、Decision、Execution 分離；模型輸出不能直接取代人工核准。
- 資料治理以 Canonical Data Model、point-in-time correctness、lineage、quality gate、model-ready views 為主軸。
- 模組設計採平台化：Integration Layer 與 Learning Hub 支撐資料、模型與回訓閉環；OpsBoard 作為角色化操作入口。
- 高風險行為必須保留 approval、override reason、audit trail、rollback plan 與查核證據。
- AI 與最佳化文件明確區分模型預測、因果效果、solver 建議與最終業務決策。

## Development Entry Points

| Goal | Start Here |
|---|---|
| 建立需求與任務拆分 | `01_project_governance/ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md`, `09_operations/ODP-OPS-01_DEVELOPMENT_WBS_AND_RELEASE_PLAN.md` |
| 設計資料庫與資料管線 | `03_data_integration/ODP-DATA-04_CANONICAL_DATA_MODEL.md`, `04_platform_sd/ODP-SD-05_DATABASE_AND_STORAGE_DESIGN.md` |
| 設計 API / Event / Job | `04_platform_sd/ODP-SD-06_API_DESIGN_SPECIFICATION.md`, `04_platform_sd/ODP-SD-07_EVENT_AND_MESSAGE_CONTRACTS.md`, `04_platform_sd/ODP-SD-08_WORKFLOW_JOB_AND_STATE_MACHINE_DESIGN.md` |
| 建立前端工作台 | `07_ux/ODP-UX-01_INFORMATION_ARCHITECTURE_AND_NAVIGATION.md`, `07_ux/ODP-UX-03_SCREEN_AND_INTERACTION_SPECIFICATION.md`, `07_ux/ODP-UX-05_FRONTEND_TECHNICAL_DESIGN.md` |
| 建立模型與實驗治理 | `06_ai_causal_optimization/ODP-ML-01_AI_ML_OVERALL_DESIGN.md`, `06_ai_causal_optimization/ODP-ML-05_CAUSAL_INFERENCE_AND_EXPERIMENT_DESIGN.md` |
| 建立測試與驗收 | `08_qa_acceptance/ODP-QA-01_TEST_MASTER_PLAN.md`, `08_qa_acceptance/ODP-QA-03_END_TO_END_TEST_SCENARIOS.md`, `08_qa_acceptance/ODP-QA-06_UAT_AND_FORMAL_ACCEPTANCE_CHECKLIST.md` |
| 建立部署與維運 | `09_operations/ODP-OPS-03_DEPLOYMENT_AND_ENVIRONMENT_SETUP_MANUAL.md`, `09_operations/ODP-OPS-04_RUNBOOK.md` |

完整文件清單請看 `CATALOG.md`。
