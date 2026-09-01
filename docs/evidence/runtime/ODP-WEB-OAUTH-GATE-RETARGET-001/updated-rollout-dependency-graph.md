# Updated Rollout Dependency Graph (更新後 Rollout 依賴圖)

- **Task ID**: `ODP-WEB-OAUTH-GATE-RETARGET-001`
- **Phase**: `Wave Auth 3 - Gate Retarget`
- **Status**: Verified & Retargeted
- **Owner**: `Antigravity3`
- **Reviewer**: `Claude`
- **Date**: `2026-09-01`

---

## 1. 依賴重接架構圖 (Retargeted Architecture DAG)

```mermaid
flowchart TD
    subgraph Wave_Auth ["Wave Auth: Password-First & Optional OIDC (Completed)"]
        AUTH_CONTRACT["ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001<br/>(Done · PR #1097)"]
        LOCAL_CORE["ODP-WEB-LOCAL-IDENTITY-CORE-001<br/>(Done · PR #1098)"]
        LOGIN_ROUTE["ODP-WEB-PASSWORD-FIRST-LOGIN-001<br/>(Done · PR #1100)"]
        API_TRUST["ODP-WEB-LOCAL-AUTH-API-TRUST-001<br/>(Done · PR #1101)"]
        THROTTLE["ODP-WEB-LOGIN-THROTTLE-REMEDIATION-001<br/>(Done · PR #1105)"]
        OIDC_OPT["ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001<br/>(Done · PR #1074)"]
        SEC_E2E["ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002<br/>(Done · PR #1096)"]

        AUTH_CONTRACT --> LOCAL_CORE & OIDC_OPT
        LOCAL_CORE --> LOGIN_ROUTE & API_TRUST
        LOGIN_ROUTE & API_TRUST --> THROTTLE
        THROTTLE & OIDC_OPT --> SEC_E2E
    end

    subgraph Repositioned_Human_Gate ["Repositioned Non-Blocking Human Gate"]
        HUMAN_OAUTH["HUMAN-GCP-WEB-OAUTH-CLIENTS-001<br/>[TODO · Human/Ops]<br/>(Repositioned as Optional OIDC Gate)"]
    end

    subgraph Active_Rollout_Blockers ["Active Rollout Prerequisites (Wave 3)"]
        WIRING["ODP-RELEASE-BUILD-HANDOFF-SNAPSHOT-ROLLBACK-WIRING-001<br/>(Done · PR #1109)"]
        MASKED_SNAP["DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001<br/>[BLOCKED · PR #63 in data-platform]"]
        DPF_ROLLOUT["DPF-EMGI-LIVE-ROLLOUT-001<br/>[BLOCKED / TODO in data-platform]"]
    end

    subgraph Dev_Rollout ["Dev Live Rollout"]
        DEV_REMED["ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001<br/>(Dev Live Rollout Remediation)<br/>[BLOCKED on Snapshot & EMGI]"]
    end

    subgraph Downstream_Rollout_Gates ["Downstream Promotion Gates"]
        STAGING["ODP-EPHEMERAL-STAGING-ROLLOUT-001<br/>(Ephemeral Staging Rehearsal)"]
        PROD["ODP-PROD-BLUEGREEN-ROLLOUT-001<br/>(Production Blue-Green Rollout)"]
    end

    %% Retargeted flow:
    SEC_E2E -->|Explicit Canonical depends_on| DEV_REMED
    OIDC_OPT -.->|Deploy Contract Cleared| DEV_REMED
    WIRING --> DEV_REMED
    MASKED_SNAP --> DEV_REMED
    DPF_ROLLOUT --> DEV_REMED

    DEV_REMED -->|dev-verified| STAGING
    STAGING -->|staging-verified + Human GO| PROD

    %% Optional path:
    OIDC_OPT -.->|Optional Post-Rollout Enablement| HUMAN_OAUTH

    classDef done fill:#d4edda,stroke:#28a745,stroke-width:2px;
    classDef blocked fill:#f8d7da,stroke:#dc3545,stroke-width:2px;
    classDef inprogress fill:#fff3cd,stroke:#ffc107,stroke-width:2px;
    classDef optional fill:#e2e3e5,stroke:#6c757d,stroke-width:2px,stroke-dasharray: 5 5;

    class AUTH_CONTRACT,LOCAL_CORE,LOGIN_ROUTE,API_TRUST,THROTTLE,OIDC_OPT,SEC_E2E,WIRING done;
    class MASKED_SNAP,DPF_ROLLOUT,DEV_REMED,STAGING,PROD blocked;
    class HUMAN_OAUTH optional;
```

---

## 2. 變更對照表 (Before vs After Retarget)

| 維度 | 變更前 (Before Retarget) | 變更後 (After Retarget) |
|---|---|---|
| **Dev Rollout 身分驗證依賴** | 硬性依賴 `HUMAN-GCP-WEB-OAUTH-CLIENTS-001`（等待人工建立 Google OAuth Client ID/Secret） | 權威 CLI 寫入依賴 `ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002`（PR #1096，已完成）並結合 `ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001`（PR #1074，已完成）收據 |
| **`HUMAN-GCP-WEB-OAUTH-CLIENTS-001` 定位** | P0 阻塞性 Human Gate（阻擋 dev/staging/prod 部署） | 可選 OIDC 啟用作業（Optional OIDC Gate），保留於 `status: todo`，不阻擋 password-first 部署 |
| **Rollout 閘門完整性** | 因 OAuth 人工未就緒而無法推進 | 僅解除非必要的 OAuth 硬相依，嚴格保留 Build Once、Direct VPC、Ephemeral Staging 9-stage rehearsal、Production Blue-Green 及 Watch Window 全部閘門 |
| **第三方來源約束** | 16 個外部來源 disabled、無 credentials、default-deny egress | 100% 保持 disabled 與 default-deny egress |

---

## 3. 防呆與 Fail-Closed 保證

1. **未提前解除 Rollout Gate**：`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 目前仍處於 `blocked`，受限於未完成的 `DPF-EMGI-MASKED-RELEASE-SNAPSHOT-001`（PR #63）與 `DPF-EMGI-LIVE-ROLLOUT-001`，未因 OAuth retarget 造成假放行。
2. **零憑證外洩**：所有收據與圖表不包含任何明文秘密（`secret_values_redacted: true`）。
3. **權威狀態可稽核**：`ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001` 的 `depends_on` 異動由 canonical owner `Antigravity3` 於 `2026-09-01T15:41:44Z` 透過 `ai-status.sh set_dependencies` 寫入，並於 `ai-activity-log.jsonl` 留有完整稽核記錄。

