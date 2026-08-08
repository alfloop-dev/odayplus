"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./featureFlags.module.css";
import {
  approveFeatureFlag,
  DEFAULT_FEATURE_FLAGS,
  disableFeatureFlag,
  enableFeatureFlag,
  fetchFeatureFlags,
  type FeatureFlagDto,
} from "./featureFlagsAdapter";

export type FeatureFlagsAdminWorkspaceProps = {
  initialFlags?: FeatureFlagDto[];
  baseUrl?: string;
};

export function FeatureFlagsAdminWorkspace({
  initialFlags,
  baseUrl = "",
}: FeatureFlagsAdminWorkspaceProps) {
  const [flags, setFlags] = useState<FeatureFlagDto[]>(initialFlags || DEFAULT_FEATURE_FLAGS);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | "disabled" | "high_risk" | "expired">("all");
  const [search, setSearch] = useState("");

  // Approval modal state
  const [modalFlag, setModalFlag] = useState<FeatureFlagDto | null>(null);
  const [approverInput, setApproverInput] = useState("");

  const loadFlags = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchFeatureFlags(baseUrl);
      setFlags(data);
    } catch (err: any) {
      setError(err.message || "Failed to load feature flags");
    } finally {
      setLoading(false);
    }
  }, [baseUrl]);

  useEffect(() => {
    if (!initialFlags) {
      loadFlags();
    }
  }, [initialFlags, loadFlags]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const handleToggle = async (flag: FeatureFlagDto) => {
    if (flag.is_active) {
      // Emergency Kill-Switch: Disable
      try {
        const updated = await disableFeatureFlag(flag.key, baseUrl);
        setFlags((prev) => prev.map((f) => (f.key === flag.key ? updated : f)));
        showToast(`🛑 Kill-Switch engaged: Feature flag ${flag.key} disabled!`);
      } catch (err: any) {
        setError(err.message || "Failed to disable flag");
      }
    } else {
      // Enable action: if high risk, show approval modal or attempt enable
      if (flag.high_risk && flag.approved_by.length < 2) {
        setModalFlag(flag);
      } else {
        try {
          const updated = await enableFeatureFlag(flag.key, flag.approved_by, baseUrl);
          setFlags((prev) => prev.map((f) => (f.key === flag.key ? updated : f)));
          showToast(`✅ Feature flag ${flag.key} enabled!`);
        } catch (err: any) {
          if (flag.high_risk) {
            setModalFlag(flag);
          } else {
            setError(err.message || "Failed to enable flag");
          }
        }
      }
    }
  };

  const handleAddApproval = async () => {
    if (!modalFlag || !approverInput.trim()) return;
    try {
      const updated = await approveFeatureFlag(modalFlag.key, approverInput.trim(), baseUrl);
      setModalFlag(updated);
      setFlags((prev) => prev.map((f) => (f.key === updated.key ? updated : f)));
      setApproverInput("");
      showToast(`✍️ Recorded approval from ${approverInput.trim()}`);
    } catch (err: any) {
      setError(err.message || "Failed to record approval");
    }
  };

  const handleEnableFromModal = async () => {
    if (!modalFlag) return;
    try {
      const updated = await enableFeatureFlag(modalFlag.key, modalFlag.approved_by, baseUrl);
      setFlags((prev) => prev.map((f) => (f.key === updated.key ? updated : f)));
      setModalFlag(null);
      showToast(`🚀 Dual approval complete! Feature flag ${modalFlag.key} enabled.`);
    } catch (err: any) {
      setError(err.message || "Dual approval requirement not met");
    }
  };

  const stats = useMemo(() => {
    const total = flags.length;
    const active = flags.filter((f) => f.is_active).length;
    const disabled = flags.filter((f) => !f.is_active).length;
    const highRisk = flags.filter((f) => f.high_risk).length;
    const expired = flags.filter((f) => f.is_expired).length;
    return { total, active, disabled, highRisk, expired };
  }, [flags]);

  const filteredFlags = useMemo(() => {
    return flags.filter((f) => {
      const matchesSearch =
        f.key.toLowerCase().includes(search.toLowerCase()) ||
        f.description.toLowerCase().includes(search.toLowerCase()) ||
        f.owner.toLowerCase().includes(search.toLowerCase());

      if (!matchesSearch) return false;

      if (filter === "active") return f.is_active;
      if (filter === "disabled") return !f.is_active;
      if (filter === "high_risk") return f.high_risk;
      if (filter === "expired") return f.is_expired;
      return true;
    });
  }, [flags, search, filter]);

  return (
    <div className={styles.container}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <h2 className={styles.headerTitle}>
            ⚙️ Feature Flag Management UI (UX-SCR-ADMIN-002)
          </h2>
          <p className={styles.headerSubtitle}>
            FR-SHARED-004 / FR-GOV-009 Kill-Switch 控制面板：即時切換 UI、API、Job 執行權限
          </p>
        </div>
        <button className={styles.filterBtn} onClick={loadFlags} disabled={loading}>
          {loading ? "更新中..." : "🔄 重新整理"}
        </button>
      </div>

      {/* Error alert */}
      {error && (
        <div style={{ background: "rgba(239,68,68,0.2)", color: "#f87171", padding: "0.75rem", borderRadius: "6px", border: "1px solid #ef4444" }}>
          ⚠️ 錯誤: {error}
        </div>
      )}

      {/* Stats Summary Bar */}
      <div className={styles.statsBar}>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>Total Flags</span>
          <span className={styles.statValue}>{stats.total}</span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>Active</span>
          <span className={`${styles.statValue} ${styles.statValueActive}`}>
            {stats.active}
          </span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>Disabled (Kill-Switch)</span>
          <span className={`${styles.statValue} ${styles.statValueDisabled}`}>
            {stats.disabled}
          </span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>High Risk Flags</span>
          <span className={`${styles.statValue} ${styles.statValueHighRisk}`}>
            {stats.highRisk}
          </span>
        </div>
      </div>

      {/* Controls Bar */}
      <div className={styles.controlsBar}>
        <div className={styles.filterGroup}>
          <button
            className={`${styles.filterBtn} ${filter === "all" ? styles.filterBtnActive : ""}`}
            onClick={() => setFilter("all")}
          >
            全部 ({stats.total})
          </button>
          <button
            className={`${styles.filterBtn} ${filter === "active" ? styles.filterBtnActive : ""}`}
            onClick={() => setFilter("active")}
          >
            已啟用 ({stats.active})
          </button>
          <button
            className={`${styles.filterBtn} ${filter === "disabled" ? styles.filterBtnActive : ""}`}
            onClick={() => setFilter("disabled")}
          >
            已停用 ({stats.disabled})
          </button>
          <button
            className={`${styles.filterBtn} ${filter === "high_risk" ? styles.filterBtnActive : ""}`}
            onClick={() => setFilter("high_risk")}
          >
            高風險 ({stats.highRisk})
          </button>
        </div>

        <input
          type="text"
          placeholder="搜尋 Feature Flag key / owner / 描述..."
          className={styles.searchInput}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Flags Grid */}
      <div className={styles.grid}>
        {filteredFlags.map((flag) => (
          <div
            key={flag.key}
            className={`${styles.flagCard} ${flag.high_risk ? styles.flagCardHighRisk : ""}`}
          >
            <div>
              <div className={styles.flagHeader}>
                <span className={styles.flagKey}>{flag.key}</span>
                <span
                  className={`${styles.badge} ${
                    flag.is_active ? styles.badgeActive : styles.badgeDisabled
                  }`}
                >
                  {flag.is_active ? "● ACTIVE" : "○ DISABLED"}
                </span>
              </div>

              <p className={styles.flagDescription}>
                {flag.description || "無詳細描述"}
              </p>

              <div className={styles.badgesGroup}>
                {flag.high_risk && (
                  <span className={`${styles.badge} ${styles.badgeHighRisk}`}>
                    🛡️ 高風險雙人核准 ({flag.approved_by?.length || 0}/2)
                  </span>
                )}
                <span className={`${styles.badge} ${styles.badgeReadiness}`}>
                  Readiness: {flag.readiness}
                </span>
                {flag.is_expired && (
                  <span className={`${styles.badge} ${styles.badgeDisabled}`}>
                    已到期
                  </span>
                )}
              </div>
            </div>

            <div>
              <div className={styles.metaRow}>
                <span>Owner: <strong>{flag.owner}</strong></span>
                {flag.expires_on && <span>Expiry: {flag.expires_on}</span>}
              </div>

              <div className={styles.actionRow}>
                <button
                  className={`${styles.toggleBtn} ${
                    flag.is_active ? styles.toggleBtnDisable : styles.toggleBtnEnable
                  }`}
                  onClick={() => handleToggle(flag)}
                >
                  {flag.is_active ? "🛑 緊急停用 (Kill-Switch)" : "▶️ 啟用功能"}
                </button>

                {flag.high_risk && (
                  <button
                    className={styles.approveBtn}
                    onClick={() => setModalFlag(flag)}
                  >
                    ✍️ 簽核明細
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Dual Approval Modal */}
      {modalFlag && (
        <div className={styles.modalOverlay} onClick={() => setModalFlag(null)}>
          <div className={styles.modalBox} onClick={(e) => e.stopPropagation()}>
            <h3 className={styles.modalTitle}>
              🛡️ 高風險 Feature Flag 雙人核准 (SA-04 §3)
            </h3>
            <p style={{ color: "#9ca3af", fontSize: "0.875rem", margin: 0 }}>
              Flag: <strong style={{ color: "#60a5fa" }}>{modalFlag.key}</strong>
            </p>
            <p style={{ color: "#d1d5db", fontSize: "0.875rem" }}>
              高風險功能啟用必須取得至少 <strong>2 個獨立核准者</strong> 簽名。未達標準前 UI、API 與 Job 均會保持停用 (Fail-Safe)。
            </p>

            <div style={{ background: "rgba(0,0,0,0.3)", padding: "0.75rem", borderRadius: "6px" }}>
              <div style={{ fontSize: "0.8rem", color: "#9ca3af", marginBottom: "0.4rem" }}>
                目前已簽核人員 ({modalFlag.approved_by.length}/2):
              </div>
              {modalFlag.approved_by.length === 0 ? (
                <div style={{ fontSize: "0.85rem", color: "#f87171" }}>尚無簽核記錄</div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: "1.2rem", color: "#34d399", fontSize: "0.875rem" }}>
                  {modalFlag.approved_by.map((appr) => (
                    <li key={appr}>{appr}</li>
                  ))}
                </ul>
              )}
            </div>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                type="text"
                placeholder="輸入核准者 ID / 角色 (如 ops_lead_john)..."
                className={styles.searchInput}
                style={{ flex: 1 }}
                value={approverInput}
                onChange={(e) => setApproverInput(e.target.value)}
              />
              <button className={styles.approveBtn} onClick={handleAddApproval}>
                + 簽名
              </button>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", marginTop: "1rem" }}>
              <button
                className={styles.filterBtn}
                onClick={() => setModalFlag(null)}
              >
                取消
              </button>
              <button
                className={`${styles.toggleBtn} ${styles.toggleBtnEnable}`}
                disabled={modalFlag.approved_by.length < 2}
                onClick={handleEnableFromModal}
                style={{
                  opacity: modalFlag.approved_by.length < 2 ? 0.5 : 1,
                  cursor: modalFlag.approved_by.length < 2 ? "not-allowed" : "pointer",
                }}
              >
                🚀 正式啟用 (核准數 {modalFlag.approved_by.length}/2)
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast Notification */}
      {toast && <div className={styles.toast}>{toast}</div>}
    </div>
  );
}
