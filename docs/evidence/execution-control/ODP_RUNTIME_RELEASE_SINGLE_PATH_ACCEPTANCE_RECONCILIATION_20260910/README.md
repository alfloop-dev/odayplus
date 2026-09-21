# ODP-RUNTIME-RELEASE-SINGLE-PATH-001 驗收核對與補證記錄 (2026-09-10)

## 1. 任務背景與復原目標

- **任務 ID**: `ODP-RUNTIME-RELEASE-SINGLE-PATH-001`
- **任務名稱**: 歷史復原佔位：ODP-RUNTIME-RELEASE-SINGLE-PATH-001
- **執行身分 (Owner)**: `Antigravity6`
- **指派審查者 (Reviewer)**: `Codex`
- **復原目標分支**: `task/ODP-RUNTIME-RELEASE-SINGLE-PATH-001-RECOVERY-20260910`
- **對照基準 (Pinned Dev)**: `025323f36d05c81f40085ba80c87f370d7c49c83`

本任務原始交付已於 2026-08-25 經由 PR [#1010](https://github.com/alfloop-dev/odayplus/pull/1010) 合併入 `dev`（PR head: `5f80756b84852f935625e502eca9fc038a6bbc9a`，merge commit: `5ae1e5cee8ef6b5047fa72f2426d2a1f42d9f9ce`）。2026-09-06 archive 事故後，在歷史盤點中因條款 A4（各階段使用正確 admission 與 protected environments）缺乏 GitHub 實體環境保護規則之直接唯讀證明，暫列為 `partially_met` 並維持 blocked 佔位狀態。

2026-09-10 23:31–23:34 UTC，Codex 執行了 36 條只讀命令查核包（`support/handoffs/max-dispatch-20260910/archive-evidence/`），成功補齊 GitHub protected environments 與 workflow bindings 的實體查核收據，使得 A1–A5 全部條件技術上已具備充分可驗證證據。

---

## 2. 唯讀查核證據盤點

本次補證所依據之最新 GitHub API 與 Git 查核收據包含：

| 查核項目 | 查核結果 | 具體證據 |
|---|---|---|
| **Staging 環境保護** | `true` | GitHub Environment `staging` (id `17295059155`) 設定 `required_reviewers`，名單為 `Alien-alfaloop`、`ajoe734` |
| **Production 環境保護** | `true` | GitHub Environment `production` (id `20574639394`) 設定 `required_reviewers`，名單為 `Alien-alfaloop`、`ajoe734` |
| **Build 環境分離** | `true` | `dev-build` (`20655859872`)、`staging-build` (`20655927684`)、`production-build` (`20655943111`) 均存在且無部署 review 限制，符合 build/deploy 分工契約 |
| **Dev 環境無部署保護** | `true` | `dev` (`17295066036`) 無 protection rules，符合原契約僅要求 staging/production 保護之定義 |
| **Workflow 環境綁定** | `true` | pinned dev `.github/workflows/deploy-dev.yml` 中：`build` 綁定 `${{ inputs.environment }}-build`，`admission` 與 `deploy` 綁定 `${{ inputs.environment }}`，`staging_closeout` 綁定 `staging` |
| **Admission 檢查器** | `true` | `delivery_toolchain/release/check_runtime_admission.py` 在 workflow 中具名被調用 |
| **唯一部署與證明入口** | `true` | 盤點 7 份 active workflow 定義，`deploy_cloud_run_waji.sh` 與 `delivery_toolchain/e2e/check_remote_staging_proof.py` 僅出現在 `deploy-dev.yml` |
| **歷史 CI 與審查記錄** | `true` | 原 PR #1010 head `5f80756b` 之 7 項 check-runs 全數 `success`；原 reviewer Claude 於 2026-08-25 批准記錄完整保留；2026-09-08 placeholder blocked 之 failure gate 如實記錄，不偽造歷史 |

---

## 3. A1–A5 逐條驗收核對結果

| 項次 | 原驗收條款 | 類別 | 判定結果 | 核對依據與證據路徑 |
|---|---|---|---|---|
| **A1** | 現有 Runtime Release 成為唯一入口 | 程式交付 (D)<br>測試證明 (T) | **已滿足 (met)** | 經 PR #1010 交付之 `tests/ops/test_deploy_workflow_contract.py` 在 merge commit `5ae1e5ce` 存在，且 7 項 CI 檢查為 success；pinned dev workflow 盤點確認無第二套部署入口。 |
| **A2** | build job 只執行一次且 deploy-by-digest | 測試證明 (T) | **已滿足 (met)** | `tests/ops/test_deploy_workflow_contract.py` 契約測試完整覆蓋 build 一次與 digest 部署邏輯；PR #1010 exact-head `product` CI 成功。 |
| **A3** | 依序支援 dev/ephemeral staging/prod blue-green | 測試證明 (T)<br>程式交付 (D) | **已滿足 (met)** | 歷史 PR #1010 (head `5f80756b`) 之 `test_deploy_workflow_contract.py` 與 `test_runtime_admission.py`（merge commit `5ae1e5ce`，product CI success）界定各階段 admission 邊界；歷史 workflow (@5f80756b:465-478, :480-490) 採用 static staging 與 prod dry-run capture。完整 ephemeral staging lifecycle（create/verify/proof）與 prod blue-green state capture 則經由 pinned dev `deploy-dev.yml` (@025323f3:1226-1293, :1337-1352) 原始碼查核證明。 |
| **A4** | 各階段使用正確 admission 與 protected environments | 測試證明 (T)<br>部署運行 (R) | **已滿足 (met)** | **缺口已補齊**：2026-09-10T23:31Z 讀回確認 staging/production 具備 `required_reviewers`，三個 build 環境無阻擋，`deploy-dev.yml` 正確綁定環境與 admission 檢核。 |
| **A5** | 不存在第二套 proof/deploy 狀態機 | 測試證明 (T) | **已滿足 (met)** | 否定要求由 `tests/ops/test_deploy_workflow_contract.py` 覆蓋，並與 pinned dev 之 7 份 workflow 結構一致。 |

---

## 4. 權限邊界與不變量原則

1. **不推斷部署授權**：本次補證確認了環境設定與程式碼契約，未簽發任何 Human GO、未生成 Supervisor Release Lease、未觸發 live 部署。
2. **歷史真實性保留**：
   - 原 PR #1010 (head `5f80756b`) 的 7 項 CI check-runs 與原審查者 Claude 之歷史 approval 原樣記錄。
   - 2026-09-08 因 placeholder blocked 所產生的 failure gate status 原樣保留，不以當前觀察偽稱過去執行。
3. **單一證據 Scope**：所有新交付物局限於 `docs/evidence/execution-control/ODP_RUNTIME_RELEASE_SINGLE_PATH_ACCEPTANCE_RECONCILIATION_20260910/`，不修改任何 workflow、runtime、release validator 或部署設定。

---

## 5. 驗證方式 (Verification)

本任務交付物由以下宣告命令驗證：

```bash
git diff --check
python3 -c 'import json
from pathlib import Path
p=Path("docs/evidence/execution-control/ODP_RUNTIME_RELEASE_SINGLE_PATH_ACCEPTANCE_RECONCILIATION_20260910/")
assert (p/"README.md").is_file()
x=json.loads((p/"acceptance-reconciliation.json").read_text())
assert isinstance(x,dict) and x
print("receipt present and valid JSON")'
```
