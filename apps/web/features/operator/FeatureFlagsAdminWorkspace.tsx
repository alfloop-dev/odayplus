"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Form, Modal } from "@oday-plus/ui";
import styles from "./featureFlags.module.css";
import { operatorFixturesAllowed } from "./operatorDataMode";
import {
  approveFeatureFlag,
  disableFeatureFlag,
  enableFeatureFlag,
  fetchFeatureFlags,
  type FeatureFlagDto,
  type FeatureFlagCommandProof,
} from "./featureFlagsAdapter";

export type FeatureFlagsAdminWorkspaceProps = {
  initialFlags?: FeatureFlagDto[];
  baseUrl?: string;
};

export function FeatureFlagsAdminWorkspace({
  initialFlags,
  baseUrl = "",
}: FeatureFlagsAdminWorkspaceProps) {
  const fixturesAllowed = operatorFixturesAllowed();
  const fixtureFlags = fixturesAllowed ? initialFlags : undefined;
  const currentContext = useRef({ baseUrl, fixturesAllowed, mounted: true });
  if (currentContext.current.baseUrl !== baseUrl || currentContext.current.fixturesAllowed !== fixturesAllowed) {
    currentContext.current = { baseUrl, fixturesAllowed, mounted: true };
  }
  const renderContext = currentContext.current;
  const isCurrentContext = useCallback(() => currentContext.current === renderContext && renderContext.mounted, [renderContext]);
  const [apiReadContext, setApiReadContext] = useState<object | null>(fixtureFlags !== undefined ? renderContext : null);
  const [flags, setFlags] = useState<FeatureFlagDto[]>(fixtureFlags ?? []);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    fixtureFlags !== undefined ? "ready" : "loading",
  );
  const loading = loadState === "loading" || apiReadContext !== renderContext;
  const ready = loadState === "ready" && apiReadContext === renderContext;
  const readSequence = useRef(0);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | "disabled" | "high_risk" | "expired">("all");
  const [search, setSearch] = useState("");

  // The list selects an action; only the separate confirmed form submits it.
  const [modalFlag, setModalFlag] = useState<FeatureFlagDto | null>(null);
  const [approverInput, setApproverInput] = useState("");
  const [command, setCommand] = useState<"approve" | "enable" | "disable" | null>(null);
  const [reason, setReason] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [proofError, setProofError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const activeSubmission = useRef<object | null>(null);

  const loadFlags = useCallback(async () => {
    if (!isCurrentContext()) return;
    setApiReadContext(renderContext);
    const sequence = ++readSequence.current;
    setLoadState("loading");
    setError(null);
    setFlags([]);
    setModalFlag(null);
    setCommand(null);
    setSubmitting(false);
    activeSubmission.current = null;
    setToast(null);
    try {
      const data = await fetchFeatureFlags(baseUrl);
      if (sequence !== readSequence.current || !isCurrentContext()) return;
      setFlags(data);
      setLoadState("ready");
    } catch (err: unknown) {
      if (sequence !== readSequence.current || !isCurrentContext()) return;
      setError(err instanceof Error ? err.message : "Failed to load feature flags");
      setLoadState("error");
    }
  }, [baseUrl, isCurrentContext, renderContext]);

  useEffect(() => {
    renderContext.mounted = true;
    setToast(null);
    if (fixturesAllowed && initialFlags !== undefined) {
      setApiReadContext(renderContext);
      setFlags(initialFlags);
      setLoadState("ready");
      setError(null);
      setModalFlag(null);
      setCommand(null);
      setSubmitting(false);
      activeSubmission.current = null;
    } else {
      void loadFlags();
    }
    return () => { renderContext.mounted = false; readSequence.current += 1; };
  }, [fixturesAllowed, initialFlags, loadFlags, renderContext]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => { if (isCurrentContext()) setToast(null); }, 4000);
  };

  const chooseCommand = (flag: FeatureFlagDto, nextCommand: "approve" | "enable" | "disable" | null) => {
    if (!ready || !isCurrentContext() || activeSubmission.current) return;
    setModalFlag(flag);
    setCommand(nextCommand);
    setReason("");
    setRiskAcknowledged(false);
    setProofError(null);
    setError(null);
    setApproverInput("");
  };

  const handleToggle = (flag: FeatureFlagDto) => {
    chooseCommand(flag, flag.is_active ? "disable" : flag.high_risk && flag.approved_by.length < 2 ? null : "enable");
  };

  const closeModal = () => {
    if (activeSubmission.current) return;
    setModalFlag(null);
    setCommand(null);
    setProofError(null);
  };

  const handleConfirmedCommand = async () => {
    const commandSequence = readSequence.current;
    if (!ready || !isCurrentContext() || !modalFlag || !command || activeSubmission.current) return;
    const minimum = command === "approve" ? 10 : 1;
    if (reason.trim().length < minimum || riskAcknowledged !== true || (command === "approve" && fixturesAllowed && !approverInput.trim())) {
      setProofError(command === "approve" ? "核准理由至少需要 10 個字，請確認操作風險並填妥核准資料。" : "請填寫操作理由並確認操作風險。");
      return;
    }
    // The server remains authoritative for expiry and two distinct approvals.
    if (command === "enable" && modalFlag.high_risk && modalFlag.approved_by.length < 2) return;
    const proof: FeatureFlagCommandProof = { reason: reason.trim(), riskAcknowledged: true };
    const selectedFlag = modalFlag;
    const selectedCommand = command;
    const token = {};
    activeSubmission.current = token;
    setSubmitting(true);
    setProofError(null);
    setError(null);
    try {
      const result = selectedCommand === "approve"
        ? await approveFeatureFlag(selectedFlag.key, approverInput.trim(), baseUrl, proof)
        : selectedCommand === "enable"
          ? await enableFeatureFlag(selectedFlag.key, selectedFlag.approved_by, baseUrl, proof)
          : await disableFeatureFlag(selectedFlag.key, baseUrl, proof);
      if (!isCurrentContext() || commandSequence !== readSequence.current || activeSubmission.current !== token) return;
      const updated = result.flag;
      setFlags((prev) => prev.map((flag) => flag.key === updated.key ? updated : flag));
      if (selectedCommand === "approve") {
        setModalFlag(updated);
        setCommand(null);
        setApproverInput("");
        setReason("");
        setRiskAcknowledged(false);
      } else {
        setModalFlag(null);
        setCommand(null);
      }
      const message = selectedCommand === "approve"
        ? fixturesAllowed ? `✍️ Recorded approval from ${approverInput.trim()}` : "已記錄目前登入帳號的核准。"
        : selectedCommand === "disable"
          ? `🛑 Kill-Switch engaged: Feature flag ${selectedFlag.key} disabled!`
          : `✅ Feature flag ${selectedFlag.key} enabled!`;
      const audit = result.audit ? ` 稽核事件：${result.audit.event_id}；傳遞狀態：${result.audit.delivery_status}` : "";
      showToast(message + audit);
    } catch (err: unknown) {
      if (!isCurrentContext() || commandSequence !== readSequence.current || activeSubmission.current !== token) return;
      setError(err instanceof Error ? err.message : "Feature Flag 操作失敗");
      // Keep the exact reason, acknowledgment and action so the user can retry.
    } finally {
      if (activeSubmission.current === token) {
        activeSubmission.current = null;
        if (isCurrentContext() && commandSequence === readSequence.current) setSubmitting(false);
      }
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
        <button className={styles.filterBtn} onClick={loadFlags} disabled={loading || submitting}>
          {loading ? "更新中..." : "🔄 重新整理"}
        </button>
      </div>

      {/* Error alert */}
      {error && !modalFlag && apiReadContext === renderContext && (
        <div role="alert" style={{ background: "rgba(239,68,68,0.2)", color: "#f87171", padding: "0.75rem", borderRadius: "6px", border: "1px solid #ef4444" }}>
          ⚠️ 錯誤: {error}
        </div>
      )}

      {loading && <div role="status">正在讀取 Feature Flag 設定…</div>}
      {ready && flags.length === 0 && (
        <div role="status">目前沒有 Feature Flag 設定。</div>
      )}

      {ready && flags.length > 0 && <>
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
                  disabled={submitting}
                >
                  {flag.is_active ? "🛑 緊急停用 (Kill-Switch)" : "▶️ 啟用功能"}
                </button>

                {flag.high_risk && (
                  <button
                    className={styles.approveBtn}
                    onClick={() => chooseCommand(flag, null)}
                    disabled={submitting}
                  >
                    ✍️ 簽核明細
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      </>}

      {ready && modalFlag && (
        <Modal
          open
          title={modalFlag.high_risk ? "🛡️ 高風險 Feature Flag 雙人核准 (SA-04 §3)" : "Feature Flag 操作確認"}
          onClose={closeModal}
          closeDisabled={submitting}
        >
          <p>Flag: <strong>{modalFlag.key}</strong> · Owner: {modalFlag.owner}</p>
          <p>目前狀態：{modalFlag.is_active ? "已啟用" : "已停用"}；到期日：{modalFlag.expires_on ?? "未設定"}</p>
          {modalFlag.high_risk && <p>高風險功能啟用仍須 2 個獨立核准者；本次確認不會取代雙人核准。</p>}
          <div>
            <p>目前已簽核人員 ({modalFlag.approved_by.length}/2):</p>
            {modalFlag.approved_by.length === 0 ? <p>尚無簽核記錄</p> : (
              <ul>{modalFlag.approved_by.map((actor) => <li key={actor}>{actor}</li>)}</ul>
            )}
          </div>
          {!command ? (
            <div className="odp-actions">
              <button className={styles.approveBtn} onClick={() => chooseCommand(modalFlag, "approve")}>
                {fixturesAllowed ? "+ 簽名" : "以目前登入帳號核准"}
              </button>
              <button
                className={`${styles.toggleBtn} ${styles.toggleBtnEnable}`}
                disabled={modalFlag.high_risk && modalFlag.approved_by.length < 2}
                onClick={() => chooseCommand(modalFlag, "enable")}
              >
                🚀 正式啟用 (核准數 {modalFlag.approved_by.length}/2)
              </button>
            </div>
          ) : (
            <Form
              affectedEntities={[{ entityType: "feature_flag", entityId: modalFlag.key, label: modalFlag.key }]}
              previewBeforeSubmit
              fieldErrors={[
                ...(proofError ? [{ field: "操作理由／風險確認", message: proofError }] : []),
                ...(error && apiReadContext === renderContext ? [{ field: "伺服器操作", message: error }] : []),
              ]}
              submitAction={{ id: "confirm-feature-flag-command", label: command === "approve" ? "確認核准" : command === "enable" ? "確認啟用" : "確認停用", loading: submitting, permitted: !submitting, tone: command === "disable" ? "danger" : undefined }}
              onSubmit={(event) => { event.preventDefault(); void handleConfirmedCommand(); }}
            >
              <p>操作預覽：{command === "approve" ? fixturesAllowed ? "新增 POC 核准紀錄" : "新增目前登入帳號的核准" : command === "enable" ? "啟用此功能" : "停用此功能（Kill-Switch）"}。</p>
              {command === "approve" && fixturesAllowed && (
                <label className="odp-field">
                  <span>核准者 ID（POC）</span>
                  <input className={styles.searchInput} placeholder="輸入核准者 ID / 角色 (如 ops_lead_john)..." value={approverInput} disabled={submitting} onChange={(event) => setApproverInput(event.currentTarget.value)} />
                </label>
              )}
              <label className="odp-field">
                <span>操作理由{command === "approve" ? "（至少 10 個字）" : ""}</span>
                <textarea className="odp-input" required minLength={command === "approve" ? 10 : 1} value={reason} disabled={submitting} onChange={(event) => setReason(event.currentTarget.value)} />
              </label>
              <label className="odp-field">
                <input type="checkbox" required checked={riskAcknowledged} disabled={submitting} onChange={(event) => setRiskAcknowledged(event.currentTarget.checked)} />
                <span>我已確認此操作的風險及影響範圍</span>
              </label>
            </Form>
          )}
        </Modal>
      )}

      {/* Toast Notification */}
      {ready && toast && <div className={styles.toast}>{toast}</div>}
    </div>
  );
}
