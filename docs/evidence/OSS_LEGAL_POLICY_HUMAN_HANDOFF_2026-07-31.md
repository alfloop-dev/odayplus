# OSS 法務／風險決策交辦單

- Task ID: `ODP-PLAN-OSS-LEGAL-POLICY-001`
- Gap ID: `GAP-P1-007`
- Task class: `human_gate`
- Owner: `Human/Ops` 指派的具名 Legal／Security／Risk owner
- Technical reviewer: `Claude`
- Program: `ODP-PLAN-GAP-CLOSEOUT-2026-07-30`
- Current release decision: `NO-GO`（權威核准 receipt 尚未取得）

## 1. 本次要做的決策

請具名且有權限的 Legal／Security／Risk owner 審閱並核准或退回下列
OSS 治理事項。AI agent、技術 owner 與 technical reviewer 只能整理資料、
驗證格式及執行 fail-closed gate，不得代替法務／風險 owner 作成決策。

### A. License allow／deny／review policy

審閱 `docs/security/license_policy.json` 的 `ODP-OSS-License-Gate-Policy-v1`
提案，決定：

1. permissive license allowlist 是否可核准；
2. GPL／AGPL／SSPL／BSL 等 denied list 是否完整；
3. `UNKNOWN`／`PROPRIETARY` 是否維持逐件 review；
4. LGPL 2.1／3.0 各變體是否：
   - 可直接允許；
   - 僅在符合連結方式、NOTICE、source offer 或其他義務時允許；
   - 必須逐件 review；
   - 禁止使用；
5. Apache NOTICE、第三方聲明、source-code offer、修改揭露及再散布義務
   的處理方式。

目前 policy 內把 LGPL 變體列為 allow，但在 Human/Ops 權威 receipt
核准前，這只是一項提案，不得視為已生效的 production 法務決策。

### B. Exception policy

決定 license 與 vulnerability exception 的共同規則：

1. 哪些角色有核准權；
2. 每筆 exception 必須綁定的 package／PURL、license 或 finding、
   prod／dev scope、適用 release 與原因；
3. 最長有效期限、review date、撤銷方式與重新核准條件；
4. 是否禁止永久或全域 exemption；
5. receipt 的權威來源、完整性雜湊／簽章與可回讀方式；
6. 過期、缺欄、內容不符或權威來源無法解析時，一律 fail closed。

### C. Dev toolchain 風險

目前 production npm audit 為 0 high，完整 audit 仍有 13 個 high，
範圍為 dev toolchain。請在下列三種決策中擇一：

- `REMEDIATE`：要求升級或替換 dependency，不給 exemption；
- `TEMPORARY_ACCEPT`：核准具期限、僅限 dev scope 的暫時風險接受；
- `REJECT`：不接受風險，維持工程與 release gate 阻擋。

不得由 AI 建立 waiver，也不得以不相容的 forced-major upgrade 取代
具名風險決策。

## 2. 核准 receipt 必填欄位

權威 receipt 至少必須可回讀：

| 欄位 | 要求 |
|---|---|
| `task_id` | 固定為 `ODP-PLAN-OSS-LEGAL-POLICY-001` |
| `policy_name` / `policy_version` | 綁定實際核准的 policy 名稱與版本 |
| `decision` / `status` | `approved`、`approved_with_conditions` 或 `rejected`；生效狀態不得模糊 |
| `approved_by.principal_id` | 權威身分系統中的不可混淆 principal ID |
| `approved_by.display_name` | 具名自然人；不得使用角色字串或 placeholder |
| `approved_by.role` | Legal／Security／Risk 的實際授權角色 |
| `approval_reference` | 可回讀的法務、風險或變更管理 reference |
| `source_system` | receipt 的權威來源與查詢位置 |
| `issued_at` | 嚴格 UTC 時間 |
| `expires_at` / `review_at` | 有限期限或明確複核日期 |
| `scope` | prod／dev／all，以及 package、license、finding、release 範圍 |
| `applicable_releases` | 明確 release／commit／image digest 範圍 |
| `rationale` | 可稽核的決策理由與條件 |
| `policy_file_sha256` | 核准版本 `license_policy.json` 的完整 SHA-256 |
| `evidence_hashes` | SBOM、NOTICE、audit report 等輸入證據的 SHA-256 |
| `integrity` | 完整 canonical receipt 的 SHA-256；如有簽章則附 signature reference |

以下內容不得被接受為有效核准：

- AI agent 名稱；
- `Human/Ops`、`Legal/Ops`、`Legal` 等只有角色、沒有具名 principal 的字串；
- `Jane Doe`／`John Doe` 等示例或測試姓名；
- 只有 repo 內自填 JSON、沒有權威來源及完整性驗證的 receipt；
- 未綁定 package／license／finding／scope／release 的廣泛 exemption；
- 已過期、未生效、時間在未來或無法回讀的 reference。

## 3. 建議 receipt 結構

下列為資料交換格式，不代表已核准；必須由 Human/Ops 的權威流程產生：

```json
{
  "schema_version": "1.0.0",
  "task_id": "ODP-PLAN-OSS-LEGAL-POLICY-001",
  "policy_name": "ODP-OSS-License-Gate-Policy-v1",
  "policy_version": "1.0.0",
  "decision": "approved_with_conditions",
  "status": "active",
  "approved_by": {
    "principal_id": "<authoritative-principal-id>",
    "display_name": "<named-human>",
    "role": "<authorized-legal-security-risk-role>"
  },
  "approval_reference": "<authoritative-reference>",
  "source_system": "<authoritative-system>",
  "issued_at": "<UTC timestamp>",
  "expires_at": "<UTC timestamp>",
  "review_at": "<UTC timestamp>",
  "scope": {
    "environment": ["prod", "dev"],
    "applicable_releases": ["<release-or-commit>"]
  },
  "decisions": {
    "license_policy": "<approve-or-revise>",
    "lgpl_disposition": "<decision-and-conditions>",
    "exception_policy": "<decision-and-conditions>",
    "dev_toolchain_risk": "<REMEDIATE|TEMPORARY_ACCEPT|REJECT>"
  },
  "rationale": "<auditable rationale>",
  "evidence": {
    "policy_file_sha256": "<sha256>",
    "sbom_sha256": "<sha256>",
    "audit_report_sha256": "<sha256>",
    "third_party_notices_sha256": "<sha256>"
  },
  "integrity": {
    "algorithm": "SHA-256",
    "content_sha256": "<canonical-receipt-sha256>",
    "signature_reference": "<optional-authoritative-signature-reference>"
  }
}
```

## 4. Human/Ops 回覆格式

請將以下內容填妥後交回：

```text
Decision:
Policy version:
LGPL disposition:
Dev toolchain decision:
Conditions:
Applicable releases:
Named approver:
Principal ID:
Authorized role:
Approval reference:
Source system/readback location:
Issued at:
Expires/review at:
Rationale:
Receipt SHA-256/signature reference:
```

## 5. 驗收與後續執行

收到 receipt 後，technical reviewer 僅驗證：

1. receipt 可從權威來源回讀；
2. approver principal 與角色具有核准權；
3. policy／scope／release／evidence hashes 完整且一致；
4. receipt 未過期，canonical SHA-256／簽章驗證成功；
5. license 與 vulnerability gate 對缺失、竄改、過期及範圍不符情況
   均 fail closed。

通過後才可將 `ODP-PLAN-OSS-LEGAL-POLICY-001` 標為 done，並解除相符
exception 的阻擋。這不會自動證明整體 production-ready；最終仍需
`ODP-PLAN-FINAL-GATE-AUDIT-001` 逐項核准。

## 6. 目前受影響工作

- `ODP-PLAN-OSS-LICENSE-GATE-001`：技術 fail-closed 能力可獨立完成，
  但需要法務判斷的 policy／exception 在 receipt 前不得生效。
- `ODP-PLAN-ENGINEERING-HARDENING-001`：完整 dev audit 的 13 個 high
  尚待 remediation 或具名、有限期的 dev-only risk decision。
- `ODP-PLAN-AVM-OUTCOME-001`：依 execution DAG 等待 OSS technical gate。
- `ODP-PLAN-FINAL-GATE-AUDIT-001`：直接依賴本 Human Gate，未完成前
  整體判定維持 `NO-GO`。
