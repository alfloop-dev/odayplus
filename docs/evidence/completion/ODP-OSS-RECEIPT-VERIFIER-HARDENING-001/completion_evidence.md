# ODP-OSS-RECEIPT-VERIFIER-HARDENING-001 Completion Evidence

## 任務摘要
- **Task ID**: `ODP-OSS-RECEIPT-VERIFIER-HARDENING-001`
- **Title**: 補齊 OSS 權威核准驗證器與拒絕案例
- **Owner**: Claude2
- **Reviewer**: Codex
- **Task class**: remediation（P1）
- **Base**: `origin/dev` `d87bc0bfeb544ffac8263563b8a037f1d99243e3`
- **Source of the ported engineering**: PR #1357 exact head `affd1157a1fa5fb547b453a983a27591e956c9df`（僅移植兩個 owned 檔案，未 cherry-pick 任何治理／登記 commit）
- **Parent**: `ODP-OSS-LICENSE-EXEMPTION-REGISTER-001`（保留真實 receipt 收集、豁免生效與 gate FAIL→PASS 的驗收；本任務不縮減、不代為完成）

## 交付物
| 檔案 | 內容 |
|---|---|
| `delivery_toolchain/security/generate_oss_notice.py` | `validate_exemption()` 逐項核對 + `AuthoritativeReceiptVerifier` 可信 readback 邊界；本任務新增權威核准內容綁定（R1）、明確 APPROVED 狀態（R2）、verifier 壞掉時的 fail-closed、CLI 預設不注入 verifier 的明示與文件 |
| `tests/security/test_oss_license_gate.py` | Acceptance 5 段：固定權威 record 的正向 mock，與只變更候選條目／安裝版本的負向案例 |
| `docs/evidence/completion/ODP-OSS-RECEIPT-VERIFIER-HARDENING-001/` | 本文件 |

分支 commit（皆自 `d87bc0bf` 開出）：
1. `428b7dcd` 移植 PR1357 affd1157 驗證器工程 — 兩檔逐位元組等於 `affd1157` 的同名檔（sha256 `2221ff5f…` / `8e4be3ea…`），是後續修正的對照基線。
2. `1525ea43` 綁定權威核准內容並強制明確 APPROVED — R1／R2 修正與測試。

## 1. 系統分析（SA）

### 1.1 起點：PR #1357 第三輪審查的殘留 finding
Codex 於 2026-09-23T05:57:52Z 對 exact head `affd1157` 的審查保留了前兩輪已修正的項目（receipt 必填欄位、無 verifier 拒絕、來源不可達／reference 不可解析／hash 與 approver 不符／非 APPROVED 的負向案例），但指出兩個仍可靜態到達的缺口：

| Finding | 缺陷（affd1157） | 可達的錯誤放行 |
|---|---|---|
| **P2 R1** `generate_oss_notice.py:592-604` | `FixedAuthoritativeReceiptVerifier` 只比對 reference、principal、evidence_hashes；`exemption` 參數傳入但未使用 | 保留核准 `psycopg@3.3.4` 的 record 不動，候選 purl 改成 `pkg:pypi/psycopg@3.3.5` 並本地重算 `integrity.content_sha256`，對安裝的 3.3.5 呼叫 `evaluate_policy(..., authoritative_verifier=原 verifier)`：本地 package／case／scope／purl／integrity 全過、readback 的 principal／hash 也過，結果 PASS。release／期限的變更同樣不會被權威內容核對 |
| **P2 R2** 同檔 `:573-574` | `str(record.get("status") or "APPROVED")` | status 缺失、`null`、空字串或任何 falsy 值被升格成 APPROVED；principal／hash 相符即放行 |

兩者共同的根因：readback 結果沒有把「權威系統核准了什麼」綁到「候選條目是什麼」，且核准與否用預設值推斷而非明確讀回。本地重算的 seal 只證明完整性，不證明核准權（決策包 D14、`receipt_requirements.never_acceptable`）。

### 1.2 環境事實（本機量測）
- 本機 `uv run --frozen` 預設落到 CPython 3.14 而 `pgserver` 只有 cp312 wheel；以 `UV_PYTHON=3.12` 重建 `.venv` 後，verification 命令原文可直接執行。
- worktree 沒有 `node_modules` 時，`tests/security/test_oss_license_gate.py` 有 9 筆與本任務無關的測試必紅（`collect_npm` 的 partial-install 檢查、SBOM／NOTICE `--check`、attestation readback、`--reconcile` CLI）。以 `npm ci --ignore-scripts --prefer-offline` 安裝後全部轉綠；移植基線（`428b7dcd` 的兩檔）在該環境下 98 passed。

## 2. 系統設計（SD）

### 2.1 R1：權威核准內容綁定
- 新增 `AUTHORITATIVE_BINDING_FIELDS = (exemption_id, task_id, package, purl, license_or_finding, scope, applicable_releases, policy_case_id, conditions, issued_at, expires_at, review_at)`。
- readback record 必須帶 `approved_content`（權威系統持有的核准條目，`integrity` 忽略）與／或 `approved_content_sha256`（該條目的 canonical digest，演算法同 `exemption_content_sha256`）。
- `authoritative_content_binding_error(record, candidate)`：
  1. 兩者皆缺 → 拒絕（"carries no approved content binding"）。
  2. `approved_content` 非物件、`approved_content_sha256` 非 64 hex → 拒絕。
  3. 兩者同時存在但 digest 不一致 → 拒絕（record 本身不一致）。
  4. 逐一比對 `AUTHORITATIVE_BINDING_FIELDS`，第一個不符即拒絕並點名欄位與雙方值。
  5. 最後比對整份 canonical digest，涵蓋 rationale、approved_by 等其餘欄位。
- 候選條目自己的 `integrity.content_sha256` 不參與授權判定（它在 `validate_exemption` 第 14 步只證明條目未被竄改）。因此候選改 purl／release／期限再本地重封存，仍與權威內容不符而被拒。
- `AuthoritativeReceiptVerification` 新增 `approved_content_sha256`，正向結果回報權威系統實際背書的 digest。

### 2.2 R2：明確 APPROVED
- `authoritative_status_error(record)`：`status` 鍵缺失 → "missing"；非字串（含 `None`、`bool`、`int`、`list`、`dict`）→ "invalid type <type>"；空白 → "is empty"；任何非 `"APPROVED"` 的字串（含小寫、前後空白、`REVOKED`／`REJECTED`／`PENDING`）→ "is <repr>, expected APPROVED"。沒有任何路徑把 falsy 值視為核准。

### 2.3 其他 fail-closed 補強（`validate_exemption` 第 15 步）
- verifier 拋例外 → "verification raised <Type>: <msg>"，不外洩為 pass。
- 回傳非 `AuthoritativeReceiptVerification` → 拒絕。
- 回答的 `(source_system, approval_reference)` 與請求不符 → 拒絕。
- `verified is not True` → 以其 `error` 拒絕。

### 2.4 可信 readback 整合介面（文件化與可執行用法）
模組 docstring 新增「Authoritative readback integration」段。注入點只有一個：

```python
from delivery_toolchain.security.generate_oss_notice import (
    FixedAuthoritativeReceiptVerifier,
    evaluate_policy,
    exemption_content_sha256,
)

# 權威系統對 (source_system, approval_reference) 的 readback 內容；approved_content
# 是它核准的那筆條目（或改給 approved_content_sha256 = 該條目的 canonical digest）。
verifier = FixedAuthoritativeReceiptVerifier(
    {
        ("corp-legal-tracker", "LEGAL-DECISION-0042"): {
            "status": "APPROVED",
            "principal_id": "legal-user-123",
            "evidence_hashes": ["<sha256 of the approval record>"],
            "approved_content": {  # the sealed entry as the source holds it
                "exemption_id": "...", "package": "psycopg",
                "purl": "pkg:pypi/psycopg@3.3.4", "...": "...",
            },
        }
    }
)
result = evaluate_policy(authoritative_verifier=verifier)
for item in result["review_required"]:
    for rejection in item["exemption_rejections"]:
        print(item["component"].name, rejection["exemption_id"], rejection["reason"])
```

預設 CLI 行為（fail-closed）：

```bash
uv run python delivery_toolchain/security/generate_oss_notice.py --reconcile
# main() 呼叫 evaluate_policy(authoritative_verifier=None)：每筆候選條目以
# "authoritative approval verification missing: external authoritative readback
# required (fail-closed)" 被拒，gate 維持 FAIL。
```

`FixedAuthoritativeReceiptVerifier` 是離線測試用的記憶體 readback 與真實 client 的參考實作；它不連任何外部系統。本 repo 沒有連到簽核人外部系統的 client，本任務也沒有取得、模擬或宣稱任何真實核准或來源連線。

### 2.5 測試設計
- 權威 record 固定（`_authoritative_record()`，核准的是未改動的 `_receipt_bound_exemption()` 內容），負向案例只變更候選條目與安裝元件；`_clears_every_local_check()` 先證明候選在離線模式下只剩「缺 readback」一個拒絕理由，再證明 readback 綁定是唯一擋下它的關卡。
- R1：Codex 指出的 3.3.4→3.3.5 情境；expires_at／issued_at／review_at／exemption_id／task_id／conditions 漂移（欄位綁定）；rationale／approved_by.display_name 漂移（digest）；`applicable_releases` 加寬；package／purl／license／scope／policy_case_id 由 verifier 獨立綁定；digest-only record 正負向；record 不完整六類。
- R2：16 種 status 值（缺失、`None`、空、空白、`False`、`True`、`0`、`1`、list、dict、小寫、前後空白、`REVOKED`、`REJECTED`、`PENDING`、`APPROVED_PENDING_REVIEW`）各自對應可辨識理由。
- verifier 壞掉三類（拋例外、回傳 bool、回答別筆 receipt）。
- CLI：`main()` 以 `--reconcile` 對含完整候選條目的暫存登記簿執行，回 1、印出該條目被拒理由、不寫 NOTICE。

## 3. 驗證收據

### 3.1 正式 receipt（`delivery_toolchain/git/task_verification.py run`，agent `Claude2`，run_id `claude-20260923T143950Z-8632e574`，存於 gitignored `.orchestrator/evidence/`）
| head SHA | command | exit | outcome | duration | started_at | receipt_id |
|---|---|---|---|---|---|---|
| `1525ea43f01954cc29646275d516be6687540ef2` | `git diff --check` | 0 | passed | 0.037 s | 2026-09-23T15:03:10Z | `2bd86ae054879d99` |
| `1525ea43f01954cc29646275d516be6687540ef2` | `uv run --frozen python -m pytest tests/security/test_oss_license_gate.py` | 0 | passed（140 passed） | 27.572 s | 2026-09-23T15:03:10Z | `f0f39d1dea743514` |

本文件的 commit 本身不在上表的 head 內；送審前會在最終 head 重跑同一組命令產生 receipt（`task_finalize.sh` 的 verification gate 要求 exact head）。

### 3.2 補充量測（同一 worktree，`.venv` = CPython 3.12.14，node_modules 已安裝）
| 量測 | 命令 | 結果 |
|---|---|---|
| 移植基線 | `uv run --frozen python -m pytest tests/security/test_oss_license_gate.py`（`428b7dcd` 兩檔，node_modules 安裝前） | exit 1；9 failed, 89 passed，9 筆全為 partial-install／SBOM／NOTICE／attestation 環境失敗 |
| 修正後 | 同上（`1525ea43`） | exit 0；140 passed in 21.48s |
| 回歸 | `uv run --frozen python -m pytest tests/security` | exit 0；464 passed, 5 warnings（427.07s） |
| Lint | `uv run --frozen ruff check delivery_toolchain/security/generate_oss_notice.py tests/security/test_oss_license_gate.py` | exit 0；All checks passed! |
| Boundary | `uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py` | exit 0 |
| Identity | `delivery_toolchain/git/check_task_delivery_identity.py --base origin/dev --head HEAD` | exit 0 |

### 3.3 A/B：新測試能否抓到 affd1157 的缺陷
以 `git archive HEAD` 建立完整 tmp 複本，覆蓋為 `affd1157` 版 `generate_oss_notice.py` 與本任務版測試檔，`PYTHONPATH` 釘住複本、確認 `generate_oss_notice.__file__` 指向複本後，以 `-k "resealed or drift or widening or binds_fields or digest_only or incomplete_authoritative or explicit_approved or broken_readback or reports_the_approved"` 執行：

| 版本 | 結果 |
|---|---|
| `affd1157`（舊驗證器） | exit 1；43 failed, 97 deselected — 其中 42 筆為 R1／R2／broken-readback／digest 測試，1 筆 `test_negative_hash_drift_rejected` 是複本沒有 node_modules 的環境失敗，與本任務無關 |
| `1525ea43`（本任務） | 同選擇全數通過（含於上表 140 passed） |

舊驗證器上失敗分佈：`status_must_be_explicit_approved` 16、`drift_from_approved_content` 8、`incomplete_authoritative_record` 6、`binds_fields_the_local_policy_also_checks` 5、`broken_readback_is_not_a_pass` 3、`resealed_candidate_for_other_installed_version` 1、`widening_applicable_releases` 1、`digest_only_readback` 1、`verified_readback_reports_the_approved_content_digest` 1。

## 4. 父任務仍持有的生產輸入責任（本任務不代為完成）
| 缺口 | 由誰補 |
|---|---|
| 七筆草稿的 `source_system`、`approval_reference`、`evidence_hashes`、`issued_at`／`expires_at`／`review_at`、`applicable_releases` 與封存 `integrity.content_sha256` | 簽核人（H01），依 PR #1357 草稿包 README 第 4 節 |
| 連到該外部權威系統、實作 `AuthoritativeReceiptVerifier` 並回傳 `approved_content`／`approved_content_sha256` 的 readback client，以及把它注入 `evaluate_policy()` 的生產接線 | 父任務 `ODP-OSS-LICENSE-EXEMPTION-REGISTER-001` 與 Human/Ops；本 repo 目前沒有這個 client |
| `@img/sharp-wasm32@0.35.4` 的明確裁示或 policy case 擴充 | 父任務／`HUMAN-OSS-LEGAL-APPROVAL-001` |
| 把封存後條目放進 `docs/security/license_exemptions.json` 並讓 gate FAIL→PASS | 父任務 |

在上述輸入到位前，`--reconcile` 對任何條目都維持拒絕；這是設計的 fail-closed 狀態，不是缺陷。

## 5. 不宣稱事項
- 未取得任何真實法律核准、未連線任何外部權威系統、未啟用任何豁免；`docs/security/license_exemptions.json` 未被本任務觸碰（forbidden path），`exemptions` 仍為空。
- 工程完成不等於法律核准或豁免登記生效。
