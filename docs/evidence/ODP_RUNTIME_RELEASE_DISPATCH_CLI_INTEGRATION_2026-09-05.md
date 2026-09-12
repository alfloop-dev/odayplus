# ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001 證據文件

**Task ID**：`ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001`  
**Owner**：`Claude`（第 2 輪；第 1 輪由 `Antigravity7` 執行，後續交接由 `Antigravity` 收尾送審）  
**Reviewer**：`Codex`  
**Date**：`2026-09-05`  
**Target Branch**：`dev`  

---

## 1. 問題與背景 (Problem & Context)

在既有唯一路徑的 Runtime Release 管道中，確認存在以下兩個整合缺口：

1. **Manifest CLI 匯入問題 (Manifest CLI Import Gap)**：
   - 當 `delivery_toolchain/release/release_manifest.py` 作為獨立 CLI 工具執行且未設定 `PYTHONPATH` 時（例如 `env -u PYTHONPATH python3 delivery_toolchain/release/release_manifest.py --manifest ... --structure-only`），會引發錯誤。
   - CLI 拋出 `cannot load Terraform egress contract verifier: No module named 'infra'`，導致在未將專案根目錄加入 `PYTHONPATH` 的隔離 subprocess 環境（例如 CI runner 或本機腳本）中無法驗證 manifest 結構。

2. **Workflow Dispatch Ref 與血統驗證缺口 (Workflow Dispatch Ref & Ancestry Validation Gap)**：
   - GitHub Actions 的 `workflow_dispatch` API (`POST repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches`) 要求 `ref` 必須為有效的 git 分支或標籤名稱；若傳入 40 字元的原始 commit SHA，將遭 GitHub 回傳 HTTP 422 Unprocessable Entity 錯誤。
   - `.orchestrator/release_lease_integration.py` 過去在建構 dispatch payload 時誤用了 `"ref": lease["candidate_sha"]`。
   - 此外，若採用分支 ref（例如 `dev`），必須嚴格驗證候選 commit SHA 是否為目標分支 tip 的祖先（ancestor），且候選 SHA 與分支 tip 之間的中間 commit 必須嚴格限定為純證據檔案（`docs/evidence/**`），以防止未經審查的程式碼漂移（code drift）在先前 commit 的核准名義下被誤部署。
   - 同時，必須透過唯讀遠端查詢（`git ls-remote`）驗證遠端 ref tip 的一致性，在簽發憑證（receipt）與活動日誌（activity log）中記錄實際的 `dispatch_ref` 與 `dispatch_ref_sha`，並由 hosted admission workflow 再次針對 workflow dispatch 事件的 `GITHUB_SHA` 重新驗證候選版本血統。

---

## 2. 實作變更 (Changes Implemented)

### 2.1 Manifest CLI 出站驗證器匯入修正 (Manifest CLI Egress Verifier Import Fix)
- **檔案**：`delivery_toolchain/release/release_manifest.py`
  - 在模組頂層初始化 `ROOT = Path(__file__).resolve().parents[2]`，並確保將 `str(ROOT)` 插入 `sys.path`。
  - 在 `_sources_off_egress_contract_errors(root: Path)` 中，嘗試動態匯入 `infra.terraform.verify_terraform_sources_off_egress_contract` 前，確保 `str(root)` 存在於 `sys.path` 中。
  - 當出站契約（egress contract）遭到破壞或損毀時，維持嚴格的 fail-closed 驗證機制。

### 2.2 Dispatch Ref 驗證、血統驗證與 Ref 解析 (Dispatch Ref Validation, Ancestry Verification & Ref Resolution)
- **檔案**：`.orchestrator/release_lease_integration.py`
  - 新增常數 `DEFAULT_DISPATCH_REF = "dev"` 與 `_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")`。
  - 更新 `issuer_settings` 以解析並驗證 `dispatch_ref`（拒絕 40 字元 SHA 與無效字元）。
  - 實作 `resolve_ref_sha(root: Path, ref: str, repository: str | None = None)`：
    - 透過 `git ls-remote` 僅查詢設定之遠端唯讀參照，以確保與遠端 tip 一致。
    - **（歷史註記）**：第 1 輪曾實作本機 fallback（`git rev-parse`），但因可能造成以本機未 push 狀態簽發遠端 lease 的安全隱患，已於第 2 輪完全移除（詳見 §6）。
  - 實作 `check_dispatch_ref_errors(settings, candidate_sha, root, ref_resolver)`：
    - 驗證 ref 格式。
    - 解析對應的 commit SHA。
    - 透過 `check_candidate_ancestry(candidate_sha, ref_sha, root)` 強制執行候選版本祖先檢查與純證據漂移驗證。
  - 更新 `dispatch_runtime_release`：
    - 建構 payload 時採用 `"ref": dispatch_ref`，取代原始候選 commit SHA。
    - 保留所有必要的 runtime release deploy 輸入：`phase="deploy"`、`environment`、`release_sha`、`task_id`、`release_lease`、`manifest_run_id`、`manifest_digest` 以及四個不可變的元件映像檔（`api_image`、`web_image`、`worker_image`、`scheduler_image`）。
  - 更新 `process_release_lease_issuance`：
    - 在保留前與取得金鑰後，均強制執行 `check_dispatch_ref_errors`。
    - 在簽發記錄、憑證與活動日誌中記錄 `dispatch_ref` 與 `dispatch_ref_sha`，且絕不洩漏金鑰機密或 lease bearer token。

### 2.3 Hosted Admission Dispatch 血統閘門 (Hosted Admission Dispatch Ancestry Gate)
- **檔案**：`.github/workflows/deploy-dev.yml`
  - 在 `admission` job 中：新增步驟以驗證候選版本相對於 dispatch 事件 `GITHUB_SHA` 的血統關係。
  - **（歷史註記）**：第 1 輪新增的步驟因參數傳遞錯誤（以 2 參數呼叫 3 參數函式）且未切換 checkout commit，已被第 2 輪完整重構與取代（詳見 §5）。

---

## 3. 測試涵蓋與驗證 (Test Coverage & Verification)

### 3.1 新增的 Subprocess 與單元測試 (Subprocess & Unit Tests Added)
- **檔案**：`tests/release/test_release_manifest_cli.py`
  - `test_sources_off_manifest_structure_only_without_pythonpath`：在乾淨且執行 `env -u PYTHONPATH` 的 subprocess 中執行 `delivery_toolchain/release/release_manifest.py`，斷言 exit code 為 0。
  - `test_sources_off_manifest_corrupted_contract_fails_closed_without_pythonpath`：驗證當 Terraform 出站契約損毀時，維持 fail-closed 拒絕機制。
- **檔案**：`.orchestrator/test_release_lease_integration.py`
  - `test_dispatch_ref_raw_sha_rejected_in_settings`：驗證拒絕 40 字元原始 SHA。
  - `test_dispatch_ref_invalid_characters_rejected`：驗證拒絕包含空格與無效字元的 ref。
  - `test_dispatch_ref_custom_branch_is_used_in_payload`：驗證 payload 的 `"ref"` 正確填入自訂分支名稱。
  - `test_dispatch_ref_ancestry_real_git_evidence_only_passes`：在真實 git 環境中驗證候選 SHA 與 `dev` 之間若僅有純證據 commit 則通過血統檢查，並驗證狀態記錄與憑證中包含 `dispatch_ref` / `dispatch_ref_sha`。
  - `test_resolve_ref_sha_remote_and_local`：**（歷史測試）** 曾用於測試遠端查詢與本機 fallback，第 2 輪已將本機 fallback 移除並重構為嚴格的遠端查詢負向回歸測試（見 §6.3）。
  - `test_dispatch_ref_ancestry_real_git_non_evidence_drift_blocks`：驗證若存在非證據程式碼漂移，將在存取簽發金鑰前阻擋簽發。
  - `test_dispatch_ref_non_ancestor_blocks`：驗證非祖先 ref 阻擋簽發。
  - `test_dispatch_ref_unresolvable_blocks`：驗證無法解析的 ref 阻擋簽發。
  - `test_runtime_release_inputs_missing_components_raises_dispatch_error`：驗證缺少元件映像檔輸入時拋出 dispatch 錯誤。

### 3.2 測試憑證 (Test Receipts)

1. **Release Test Suite**：
   ```
   .venv/bin/pytest .orchestrator/test_release_lease_integration.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   ============================= 112 passed in 3.43s ==============================
   ```

2. **Full Release Tests**：
   ```
   .venv/bin/pytest tests/release/
   ============================= 359 passed in 12.52s =============================
   ```

3. **Orchestrator Release Lease Suite**：
   ```
   .venv/bin/pytest .orchestrator/test_release_lease*.py
   ============================== 66 passed in 1.42s ==============================
   ```

4. **Code Boundaries & Lint**：
   ```
   .venv/bin/python delivery_toolchain/governance/check_code_boundaries.py
   Code boundary checks passed for 1110 files.

   .venv/bin/python -m ruff check .orchestrator/release_lease_integration.py .orchestrator/test_release_lease_integration.py delivery_toolchain/release/release_manifest.py tests/release/test_release_manifest_cli.py tests/release/test_release_manifest.py
   All checks passed!
   ```

---

## 4. 不變量與安全防護 (Invariants & Security Guardrails)
- **無 Secret Manager 讀取**：未進行任何實際 Secret Manager API 存取。
- **無 Lease 簽發與部署**：維持預設停用（default disabled）狀態。
- **無 Gate Registry 變更**：`docs/evidence/gates/` 目錄保持未修改。
- **嚴格憑證衛生**：簽發金鑰與 bearer token 絕不記錄於日誌或持久化儲存。

---

# 第 2 輪 — 整合審查交接修補 (Round 2 — Integration Review Handback, Claude, 2026-09-05)

Codex 在 PR #1206 head `def8fd6200c70942370d35070094eecd78a79d87` 審查退回，指出兩項 P0 缺口與 fixture 交接需求。第 1 輪完成之 CLI/`sys.path` 修正、schema/example 設定項、分支 ref payload 及其既有回歸測試均完整保留。

## 5. P0-1 — Admission 於錯誤 commit 讀取 registry (Admission read the registry at the wrong commit)

### 5.1 問題根因 (What was wrong)

Release 決策是在程式碼候選版本 `C` 建置**之後**，記錄於純證據後代 commit `E` 上的 `docs/evidence/gates/RELEASE_GATE_REGISTRY.json`。
原先 Admission 步驟 checkout 了 `inputs.release_sha` (`C`)，斷言 HEAD 等於 `C`，並執行 `check_runtime_admission.py --sha C`。然而，在 `C` 版本的 tree 上尚未存在該核准決策（早於核准建立時間），導致 admission 讀取到的是核准前的舊狀態（即過期的 `no-go`）。第 1 輪雖然加入了血統檢查步驟，但 checkout 與 `--sha` 仍停留在 `C`，使得下一次 dispatch 仍會讀到舊 verdict。

此外，第 1 輪的血統檢查步驟存在更嚴重的語法缺陷，其呼叫方式為：

```python
check_candidate_ancestry('${RELEASE_SHA}', '${GITHUB_SHA}')
```

而實際函式定義為 `check_candidate_ancestry(candidate_sha, expected_sha, root)`。缺少 `root` 參數會直接拋出 `TypeError`，在 workflow 設定 `set -euo pipefail` 下會導致該步驟失敗——意即每一次 dispatch 都會**必然失敗**。先前所有結構性斷言均未真正執行該步驟代碼，因而掩蓋了此問題。

### 5.2 變更內容 (`.github/workflows/deploy-dev.yml`, `admission` job)

| 身分識別項 (Identity) | 變更前 (Before) | 變更後 (After) |
| --- | --- | --- |
| `actions/checkout` ref | `inputs.release_sha` (C) | `github.sha` (E) |
| HEAD 斷言 | `== inputs.release_sha` | `== github.sha`，並加上針對 event SHA 的 exact-40-hex 格式檢查 |
| 血統檢查 (ancestry check) | 2 參數呼叫 → 拋出 `TypeError` | `check_candidate_ancestry(C, E, Path.cwd())`，SHA 透過 `argv` 傳遞 |
| `check_runtime_admission.py --sha` | `C` | `E` |
| lease, images, downloaded manifest, `--expected-sha`, probe `--candidate-sha`, deploy | `C` | `C`（維持不變） |

將 `--sha` 擴展至 `E` 並不會擴大 lease 的授權範圍：
`admit_release` 是從 `registry.release.candidate_sha`（而非 `--sha`）衍生出 `expected_candidate_sha`，因此 lease 仍嚴格綁定於 `C`。`--sha` 僅作為 registry 內部 `check_candidate_ancestry(registry.candidate_sha, --sha)` 的比對對象，這完全符合 C→E 的關係。

### 5.3 真實 Workflow 交接涵蓋 (Coverage of the real workflow handoff)

`tests/ops/test_deploy_workflow_contract.py` 現在針對真實 git 儲存庫，在 GitHub Actions 提供的環境變數下，**實際執行** admission 血統檢查步驟的 `run:` 腳本區塊：

- 純證據後代 commit → exit 0
- 修改了 `src/app.py` 的後代 commit → exit 1，stderr 標註 `non-evidence paths` 及該檔案
- 事件 SHA 等於候選版本 SHA → exit 0
- 事件 SHA 落後於候選版本 SHA (*behind*) → exit 1，回報 `is not an ancestor of`
- 格式錯誤的事件 SHA → exit 1

此外，還包含結構性斷言以確認 checkout 綁定 `github.sha` 且設定 `fetch-depth: 0`、`--sha` 採用 `${EVENT_SHA}`、`RELEASE_SHA` **不存在**於 admission 步驟的環境變數中，且 `release_phase` / `build` / `deploy` 仍嚴格 checkout `C`。

回歸證明——在第 1 輪的 workflow 內容（`git show def8fd62:.github/workflows/deploy-dev.yml`）上執行這 6 個可執行／結構性測試的失敗輸出：

```
FAILED test_admission_checks_out_the_dispatch_event_sha_and_proves_it
FAILED test_admission_ancestry_step_admits_an_evidence_only_descendant
FAILED test_admission_ancestry_step_refuses_code_smuggled_in_behind_the_approval
FAILED test_admission_ancestry_step_accepts_an_event_sha_equal_to_the_candidate
FAILED test_admission_ancestry_step_refuses_an_event_sha_behind_the_candidate
FAILED test_admission_ancestry_step_refuses_a_malformed_event_sha
```

---

## 6. P0-2 — `resolve_ref_sha` 曾針對本地 ref 簽發 lease (`resolve_ref_sha` signed leases against local refs)

### 6.1 問題根因 (What was wrong)

在第 1 輪實作中，當設定的儲存庫 `git ls-remote` 失敗後，會退回（fall through）嘗試 `origin`，再退回 `refs/remotes/origin/<ref>`、`refs/heads/<ref>` 以及 `<ref>`。
這意味著離線的 Supervisor、錯誤的憑證或連線逾時，將會從本機 worktree 恰好存在的狀態中取出一個**看似確定**的 SHA。然而，lease 所指定的是 GitHub 實際將 checkout 的 commit；本機 clone 不能作為遠端狀態的證據。

同時，`check_dispatch_ref_errors` 過去使用 `try/except TypeError` 包裝解析器呼叫以相容 2 參數的測試 stub——這種僅為讓測試通過的 production fallback，會掩蓋解析器內部真正的 `TypeError`。

### 6.2 變更內容 (`.orchestrator/release_lease_integration.py`)

`resolve_ref_sha` 現在**僅且只**讀取 `https://github.com/<github_repository>.git`：

- 缺少或格式錯誤的 `github_repository` → 回傳 `None`
- `git ls-remote` 回傳非零值、`OSError` 或逾時 → 回傳 `None`（表示「未知 unknown」，絕不假定為「無此 ref」）
- 該遠端上不存在該 ref → 回傳 `None`
- 透過 `refs/tags/<ref>^{}` 解析 annotated tag，絕不回傳 tag 物件本身的 SHA
- 若同名的 branch 與 tag 指向不同 commit → 回傳 `None`；因為 `workflow_dispatch` 接收純名稱並在伺服器端解析衝突，Supervisor 無法確定將執行哪一個 commit，因此絕不簽發宣稱該 ref 的 lease
- 傳入 40 字元原始 hex `ref` → 回傳 `None`

`None` 回傳給呼叫端後將作為命名該設定儲存庫的 blocker。移除了 `TypeError` shim，使測試 stub 與正式環境函式簽名保持嚴格一致。

### 6.3 負向回歸測試（真實 `git ls-remote`，無網路依賴，無 patched `subprocess`）

設定的 HTTPS URL 透過 git 原生的 `url.<local-bare-repo>.insteadOf` 重新導向，因此測試組建並執行與 Supervisor 完全相同的命令。

| 測試項目 (Test) | 斷言內容 (Asserts) |
| --- | --- |
| `..._reads_the_configured_remote_not_the_local_tip` | 本地 `dev` 與 `origin/dev` 領先遠端時，以**遠端** SHA 為準 |
| `..._refuses_when_the_configured_remote_cannot_be_read` | 遠端無法讀取且本地有相符 ref 時 → 回傳 `None` |
| `..._refuses_an_unknown_ref_on_a_readable_remote` | 可讀取的遠端上不存在該 ref → 回傳 `None` |
| `..._refuses_without_a_configured_repository` | 未設定／為空／格式錯誤的 repository → 回傳 `None` |
| `..._peels_an_annotated_tag_to_its_commit` | tag 物件 SHA ≠ 回傳的 commit SHA（正確 peel） |
| `..._resolves_a_lightweight_tag` | lightweight tag 正常解析 |
| `..._refuses_a_branch_and_tag_of_the_same_name` | 同名但 commit 衝突時 → 回傳 `None` |
| `..._accepts_a_branch_and_tag_that_agree` | 同名且指向相同 commit 時 → 正常解析 |
| `..._refuses_a_raw_sha_as_a_ref` | 40-hex 原始 SHA 作為 ref → 回傳 `None` |
| `test_unreadable_remote_blocks_issuance_even_with_a_matching_local_ref` | 鏈路終端驗證：阻擋簽發，不進行本機簽署 |

回歸證明——在第 1 輪的模組內容（`git show def8fd62:.orchestrator/release_lease_integration.py`）上執行測試的失敗輸出：

```
FAILED test_resolve_ref_sha_refuses_when_the_configured_remote_cannot_be_read
FAILED test_resolve_ref_sha_refuses_without_a_configured_repository
FAILED test_resolve_ref_sha_peels_an_annotated_tag_to_its_commit
FAILED test_resolve_ref_sha_refuses_a_branch_and_tag_of_the_same_name
FAILED test_resolve_ref_sha_refuses_a_raw_sha_as_a_ref
FAILED test_unreadable_remote_blocks_issuance_even_with_a_matching_local_ref
```

Hosted 對非證據漂移的重新驗證見 §5.3：admission 步驟會依據 GitHub 解析 ref 所得的實際 SHA 重新驗證 verdict，因此在 dispatch 與 admission 之間移動的 ref 無法將新程式碼夾帶在舊核准之後執行。

---

## 7. PR #1205 Fixture 交接：不修改 Gate 證據文件 (PR #1205 fixture handoff, no gate evidence document touched)

PR #1205 commit `3b5de7e7` 曾包含 fixture 與 `sys.path` 變更；`c70216db` 隨後予以 revert，使該 PR 維持純證據狀態並將程式碼交接至本任務。

其原先的 fixture 處理方式是強制將 `schema_version = 1` 並移除 v2 posture 欄位。將版本降為 1 只是將衝突移至不受 v2 posture 規則檢查的版本——表象消失了，但耦合依舊存在。本輪修正不採用降版做法，而是直接解決耦合：

- **`tests/release/test_probe_release_target_absence.py`**：一般 release 的 fixture 現在使用 `build_handoff`（包含自有的 snapshot 與 rollback release）建構，不再複製 `docs/evidence/gates/RELEASE_MANIFEST.json`。該檔案在每次 build 時都會重新綁定；當某次 build 發布了 initial-release-recovery manifest 時，該 fixture 便不再是一般 release，導致測試因 release 事件而非回歸問題失敗。該檔案現在完全不再讀取 gate 證據文件。
- **`tests/release/test_release_manifest_cli.py`**：`manifest_identity()` 在 `schema_version: 2` 下將 committed manifest 縮減為識別欄位（移除 `sources_off_attestation`、`initial_release_recovery`、`blockers`）；`ready`、`blocked` 與 `sources-off` 的 fixture 隨後自行宣告其 posture。blocked fixture 綁定了 snapshot 與 rollback release，因為 blocked release 仍須記錄其原定的 fallback 對象。
- **`tests/release/test_release_manifest.py`**：對 `blocked_manifest()` 進行相同處理。`test_committed_manifest_is_honest_about_whether_it_has_an_artifact` 現在依據 manifest 的**結構形態**（是否綁定 fallback，即 `rollback_release` 或 `initial_release_recovery`）來判斷 admissibility，而非直接斷言單一 verdict。PR #1205 曾將該斷言改為 `== []`，這對 v2 artifact 是正確的，但對 v1 則會再次錯誤。

### 7.1 針對真實 v2 Artifact 的驗證 (Verified against the real v2 artifact)

自 PR #1205 head `c70216dba7c1eb750c7766d37a5b154d76aff038` 取出 `docs/evidence/gates/RELEASE_MANIFEST.json` 與 `RELEASE_GATE_REGISTRY.json`，於本地 stage 後進行量測並 revert（每次執行後確認 worktree 保持乾淨）。本任務未修改任何 gate 證據文件。

| 測試之 Tree 狀態 | 測試結果 |
| --- | --- |
| v2 artifact，fixtures 處於 `def8fd62` 狀態 | **12 failed** |
| v2 artifact，fixtures 為本次交付狀態，`deploy-dev.yml` 處於 `origin/dev` 狀態 | **111 passed, 0 failed** |
| v2 artifact，fixtures 為本次交付狀態，`deploy-dev.yml` 為本次交付狀態 | 5 failed — 均為同一原因，見 §8 |

---

## 8. 對下一候選版本與 Build 的影響 — 排程前必讀 (Effect on the next candidate and build — read this before sequencing)

`.github/workflows/deploy-dev.yml` 是 `SOURCES_OFF_EGRESS_CONTRACT_FILES` 定義的 6 個出站契約檔案之一，因此它是 `compute_sources_off_egress_contract_digest` 的計算輸入。實測 digest 如下：

```
digest at candidate 04e1572f : sha256:ff71103d410c1682acae61b5091130ddfb6bf849fbeca6abb015abcfbd421f57
PR #1205 v2 artifact records  : sha256:ff71103d410c1682acae61b5091130ddfb6bf849fbeca6abb015abcfbd421f57
digest at this branch HEAD    : sha256:cb678825a210b4e17946befb7ed9f075db2890b513be06e2c0f1d7f7696a2e84
```

`git diff --name-only 04e1572f origin/dev -- <6個契約檔案>` 的比對結果為空；本分支唯一修改的契約檔案為 `deploy-dev.yml`。

**影響後果**：此處的所有 workflow 與代碼變更均屬於**下一個**候選版本，且需要進行新的 build。在該 build 執行前，於 `04e1572f` 建置且 committed 的 sources-off manifest 將無法與目前的 tree 匹配，`manifest.sources_off_attestation.egress_evidence.contract_digest` 檢查將會失敗。

**排程風險（非本分支缺陷）**：本分支目前測試全數通過（green），因為當前 committed 的 manifest 為 v1 且未帶有 sources-off attestation。若 PR #1205 先合併了 v2 sources-off artifact，隨後本 PR 再合併，`dev` 分支將在以下 5 個測試中出現失敗，且均為上述同一原因：

```
tests/release/test_release_manifest.py::test_manifest_digest_matches_canonical_payload
tests/release/test_release_manifest.py::test_committed_manifest_is_honest_about_whether_it_has_an_artifact
tests/release/test_release_manifest.py::test_staging_admission_is_dev_verified_not_staging_verified
tests/release/test_release_manifest.py::test_legacy_migration_adds_identity_and_requires_re_attestation
tests/release/test_release_manifest_cli.py::test_committed_manifest_verifies_against_its_own_candidate
```

此結構性問題比單一本分支更為廣泛：commit sources-off manifest 會使這 6 個一般原始碼檔案在下一次 build 前實質上處於不可變狀態。本任務**刻意不**在此處進行侵入式修改——真正誠實的修復方式是讓 committed manifest 針對其所宣告命名的 tree 進行驗證，而非直接針對當前 working tree 驗證，這需要在 `validate_manifest` / `_sources_off_egress_contract_errors` 中傳遞 `root` 參數。這屬於對 release validator 的正式 API 變更，超出了「只修程式與 focused tests」的範圍；而若弱化此檢查則會造成虛假 gate。此架構問題留待 release gate 負責人統籌規劃。

---

## 9. 第 2 輪驗證憑證 (Verification receipts, round 2)

在 task worktree 中使用 `uv run --frozen` 執行驗證（避免直接使用系統 `python3` 缺少 pytest 或管道錯誤碼吞噬）：

```
$ uv run --frozen python -m pytest .orchestrator/test_release_lease_integration.py       tests/ops/test_deploy_workflow_contract.py tests/release/ -p no:randomly
473 passed in 24.14s

$ uv run --frozen python -m ruff check .orchestrator/release_lease_integration.py       .orchestrator/test_release_lease_integration.py tests/ops/test_deploy_workflow_contract.py       tests/release/test_release_manifest.py tests/release/test_release_manifest_cli.py       tests/release/test_probe_release_target_absence.py
All checks passed!

$ uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py
Code boundary checks passed for 1110 files.
```

---

## 10. 第 2 輪維持之不變量 (Invariants held, round 2)

- 無讀取 Secret Manager payload、無簽發 lease、無 dispatch 實際部署。
- `config.example.json` 中的 `release_lease_issuer.enabled` 維持 `false`；正式環境 `.orchestrator/config.json` 完全不包含 `release_lease_issuer` 區塊。
- `dispatch_ref` 在設定中維持單一欄位，且其所屬 issuer 物件維持 `additionalProperties: false`；未新增任何別名或向下相容路徑。
- 未修改任何 Gate 證據文件，亦未修改 Antigravity4 worktree 中的任何檔案。
- 未新增任何安全掃描 ignore；未假造 Human/Ops GO 核准。
