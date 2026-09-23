# ODP-OSS-LICENSE-EXEMPTION-REGISTER-001：四案 LGPL 豁免的 receipt 草稿包

- **Task ID**: `ODP-OSS-LICENSE-EXEMPTION-REGISTER-001`
- **對象**: 2026-09-18 對四案 LGPL 元件裁示「附條件允許」的具名負責人
- **狀態**: 草稿，**不在生效狀態**。`docs/security/license_exemptions.json` 的 `exemptions` 維持空白，licence gate 維持 FAIL。
- **前置決策包**: [`../ODP-OSS-DECISION-PACK-001/`](../ODP-OSS-DECISION-PACK-001/README.md)（H01：權威身分與可回讀系統）

---

## 1. 這個目錄是什麼、不是什麼

`exemption-receipts.draft.json` 把 2026-09-18 裁示的七個套件謄成豁免簿條目的形狀，
每一筆已填好可以離線核對的欄位（package、purl、license、scope、policy case、rationale、conditions），
把只有簽核人本人能提供的欄位留成 `<...>` 佔位。

它**不是**核准證據。裁示的謄錄檔（`support/handoffs/remaining-inputs-20260913/OSS-LICENSE-4-CASES-FOR-SIGNOFF.md`，
未追蹤於 git，sha256 `4378244ac13d1b7b0846d7e820f37f1dd8fec011c84bc216c6c7f4d19cd664ac`）自述為
Claude 代為謄錄、非簽核人本人的數位簽署，且該檔不在任何 commit 內。依豁免簿
`receipt_requirements.never_acceptable` 的「repository-local JSON with no external authoritative readback」，
以它為 `approval_reference` 的條目不得生效。本 task 第一輪送審（PR #1357）正是這樣登記的，已被退回。

## 2. 為什麼豁免簿維持空白

豁免簿檔頭明示：每筆條目都需要自己的權威 receipt，AI agent 或 repo 作者不得新增條目。
在簽核人於外部權威系統建立可回讀的核准紀錄之前，沒有任何一筆能滿足 `rules.fail_closed_on`：

| 缺口 | 只能由誰補 |
|---|---|
| `source_system`：能回讀並驗證核准紀錄的外部系統 | 簽核人（H01） |
| `approval_reference`：該系統內的紀錄識別 | 簽核人（H01） |
| `evidence_hashes`：核准紀錄內容的 sha256 | 簽核人封存時計算 |
| `integrity.content_sha256`：條目本身的封存雜湊 | 封存時計算（第 4 節） |
| `issued_at` / `expires_at` / `review_at` | 簽核人封存時填寫 |
| `applicable_releases`：封存時的 release digest | 封存時自 `docs/security/release_bindings.json` 讀取 |

用 AI 自算雜湊填進 repo 內的 JSON，只證明完整性、不證明核准權（決策包 D14），因此本 task 不代填。

## 3. evaluator 現在核對什麼

本 task 同時把 `delivery_toolchain/security/generate_oss_notice.py` 的豁免驗證改成 `validate_exemption()`，
對每個 review_required 元件逐筆核對候選條目，任一項不符即拒絕並在 gate 輸出寫明理由：

1. 必要 receipt 與綁定欄位齊全（含 `exemption_id`、`task_id`、`review_at`、`rationale` 非空，`approved_by` 具名 `principal_id`、`display_name`、`role`）；package、license 相符；**purl 必須等於 SBOM 為安裝版本鑄的 purl**（`psycopg@3.3.4` 的豁免不涵蓋 `3.3.5`）。
2. `policy_case_id` 必須指向 `license_policy.json` 內 license、scope 相符且**列有該套件**的 review case——沒被裁示過的套件不能類推。
3. `applicable_releases` 必須包含 `release_bindings.json` 釘住的 release digest。
4. `issued_at`、`expires_at`、`review_at` 為 UTC 且不在未來；`expires_at` 晚於 `issued_at` 且未過期；`review_at` 不早於 `issued_at`。
5. `approved_by` 為具名人類 principal（display_name 非 placeholder，role/principal 不含 AI）。
6. `source_system` 非 repository-local、`approval_reference` 非空、`evidence_hashes` 為 sha256 digest、`integrity.content_sha256` 與條目內容相符。
7. **權威回讀／核准驗證結果消費**：生效路徑必須消費可信的 `AuthoritativeReceiptVerifier` 驗證結果，缺少驗證結果（離線預設 fail-closed）、來源不可達、reference 無法解析、approver 不符、evidence hash 不符或 status 非 APPROVED 皆拒絕放行。

外部回讀（到 `source_system` 實際查證 `approval_reference`）由 `AuthoritativeReceiptVerifier` 契約驗證；離線或無權威驗證結果時保持 fail-closed。
完整正向 mock 與負向測試在 `tests/security/test_oss_license_gate.py` 的「Acceptance 5」段。

## 4. 簽核人要做的事（封存步驟）

1. 在外部權威系統建立這七筆豁免的核准紀錄（可為單一紀錄涵蓋七筆），取得其識別與可回讀位置。
2. 對每筆草稿填入 `source_system`、`approval_reference`、`issued_at`（UTC）、`expires_at`（≤ 90 天）、`review_at`（30 天）、
   `applicable_releases`（當時 `docs/security/release_bindings.json` 的 `alfloop-dev/odayplus.digest`）、
   `evidence_hashes`（核准紀錄內容的 sha256）。確認 `approved_by.principal_id` 是權威身分系統中的識別。
3. 封存：對每筆條目計算 `integrity.content_sha256`，演算法與 attestation 相同（不含 `integrity` 的 `sort_keys` JSON 的 sha256）：

   ```bash
   uv run python - <<'EOF'
   import json
   from delivery_toolchain.security.generate_oss_notice import exemption_content_sha256
   entry = json.load(open("/path/to/one-filled-entry.json"))
   print(exemption_content_sha256(entry))
   EOF
   ```

4. 由後續 task 把封存後的條目放進 `docs/security/license_exemptions.json`，執行
   `uv run python delivery_toolchain/security/generate_oss_notice.py --reconcile`，
   確認七個套件轉入 allowed_with_obligations，且 `--reconcile` 輸出的拒絕理由為空。

## 5. 排除項目與已知漂移

- **`@img/sharp-wasm32@0.35.4` 未登記**：簽核表只列七個具名套件，policy case `LGPL-SHARP-LIBVIPS` 只列兩個 linux libvips 套件；
  wasm32 的授權是複合表達式、使用形態也不是 native shared library。它需要自己的裁示（或 policy case 擴充）。
  在此之前 gate 對它維持 review_required，這是正確的 fail-closed 狀態。
- **policy case 版本漂移**：`license_policy.json` 的 `LGPL-SHARP-LIBVIPS` 記載 `1.3.2`，安裝與簽核表皆為 `1.3.3`。
  `validate_exemption` 以套件名綁定 case、以 purl 綁定版本，故此漂移不阻擋登記，但 policy 檔（本 task 的 forbidden path）應由其 owner 更新。
