# 2026-09-12 核准作業完成摘要

本摘要只公開執行結果；完整 live resource/IAM metadata 與原始收據保留在本機 operational handoff。

- 使用者已核准限時 IAM 計畫；指定 recovery Terraform root 實際套用 **2 新增、0 修改、0 刪除**。
- 專用 recovery bucket 已建立，既有 CMEK、30 天保留、versioning、UBLA、PAP 與 additive deployer objectUser 經 readback 驗證。
- 受保護 remote state 的獨立 recovery prefix 管理兩個指定資源。套用後 plan exit 0、No changes。
- Staging recovery binding 已設定並回讀；完整 scope 的 11 個必要變數 presence precheck 通過。
- Foundation SQL metadata 確認 RUNNABLE、PG16、private-only staging network、CMEK、PITR 及 deletion protection。Legacy SQL 保持原狀；staging 參照已對齊 foundation。
- 建立指定 custom roles 使用的 bootstrap grant 已立即撤除；三筆後續限時 grants 也已撤除。帳號原有的 project grants 與作業前完全一致；role 定義留存但未授權。

本次沒有部署 API/Web，沒有新增 live rehearsal 證據。#1046 仍需獨立審查、必要 CI、真實 successor build artifact 與 release-scoped API/Web Direct VPC ALL_TRAFFIC readback。舊 immutable manifest 不會手動改寫。

SQL connection string 會被誤當 bare instance 的程式缺陷，已分拆為 `ODP-STAGING-SQL-BINDING-REMEDIATION-001`；該任務有實作與 125 項通過測試，待獨立審查。Next/MapLibre/sharp 修復 `ODP-RUNTIME-BUILD-DEPENDENCY-REMEDIATION-001` / #1322 已通過全部必要 CI 與 task-review-gate，並合併至 dev，沒有重複派工。

受 retention 約束的舊 plan 清理仍是獨立延後任務，不構成本次 foundation IAM/recovery 阻擋。

已整合 dev `fc4f7529ff8425d57b15dbe39f824c13dbfa2ff4`，包含已審查合併的依賴、Adjust 與 Quota Fence 修復。整合後 Ruff、code boundary 檢查通過；foundation egress 契約已完整恢復包含 12 項定義檔案（含 `modules/runtime_foundation/*.tf`），`delivery_toolchain/release/release_manifest.py` 之修改為必要例外，以在單一 Runtime Release 路徑下正確綁定抽取後的模組與 caller。`RELEASE_MANIFEST.json` 與 `RELEASE_GATE_REGISTRY.json` 已誠實還原為實際 build 過的候選版本 `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`。Gate registry 驗證保持 `release_state: NO-GO`，並如實記錄 2 筆 integrity_errors，代表新抽取的 foundation IaC 契約集合已超出 596b9c9a 建置時之範圍，需於後續建置重新 attest（`migration.re_attestation_required: true`）。Foundation 與 egress 契約測試（`tests/release/test_foundation_egress_contract.py` 與 `infra/terraform/tests`）共 **66 passed / 0 failures** 全數通過。真實 successor build run `34726258529` 六份原始 artifact digest 與 manifest 綁定驗證通過，詳見 `SUCCESSOR_BUILD_20260912.md`。
