"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "../governance.module.css";
import {
  approveModelCard,
  fallbackLearningHubModels,
  fallbackLearningHubReleases,
  fetchModelDetail,
  fetchModels,
  fetchReleases,
  monitorRelease,
  requestRelease,
  type MetricThreshold,
  type ModelCard,
  type ModelReleaseDecisionItem,
  type ModelVersionItem,
  type ReleaseMonitorAssessment,
} from "./learningHubLoader";

export type ModelReleaseControllerProps = {
  roleId?: string;
  actor?: string;
  onOpenAudit?: (auditEventId: string) => void;
};

export function ModelReleaseController({
  roleId = "ops-lead",
  actor = "ModelOps Engine",
  onOpenAudit,
}: ModelReleaseControllerProps) {
  const [models, setModels] = useState<ModelVersionItem[]>(fallbackLearningHubModels);
  const [releases, setReleases] = useState<ModelReleaseDecisionItem[]>(fallbackLearningHubReleases);
  const [selectedModelName, setSelectedModelName] = useState<string>("forecast_revenue_interval");
  const [selectedVersion, setSelectedVersion] = useState<ModelVersionItem | null>(fallbackLearningHubModels[0]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Active drawer mode
  const [activeDrawer, setActiveDrawer] = useState<"card" | "validation" | "release" | "rollback" | "monitor" | null>(null);

  // Release Controller Form State
  const [releaseType, setReleaseType] = useState<"SHADOW" | "CANARY" | "FULL" | "ROLLBACK">("FULL");
  const [releaseReason, setReleaseReason] = useState<string>("");
  const [approvalId, setApprovalId] = useState<string>("ap-model-rel-" + Math.floor(Math.random() * 1000));
  const [approvedBy, setApprovedBy] = useState<string>("ModelReviewBoard");
  const [monitoringWindow, setMonitoringWindow] = useState<string>("7d");
  const [successCriteria, setSuccessCriteria] = useState<string>("MAPE <= 0.09, Zero service errors");
  const [failCriteria, setFailCriteria] = useState<string>("MAPE > 0.12, Latency > 300ms");
  const [affectedModules, setAffectedModules] = useState<string>("Store Ops, Growth");
  const [rollbackTarget, setRollbackTarget] = useState<string>("v2.0.4");
  const [submittingRelease, setSubmittingRelease] = useState<boolean>(false);

  // Monitor Form State
  const [monitorReleaseId, setMonitorReleaseId] = useState<string>("");
  const [observedMetricsText, setObservedMetricsText] = useState<string>('{"mape": 0.085, "rmse": 1400.0}');
  const [monitorResult, setMonitorResult] = useState<ReleaseMonitorAssessment | null>(null);

  // Load models and release history
  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);

    const [modelsData, releasesData] = await Promise.all([
      fetchModels(roleId),
      fetchReleases(undefined, roleId),
    ]);

    if (modelsData && modelsData.length > 0) {
      setModels(modelsData);
      const match = modelsData.find((m) => m.model_name === selectedModelName) || modelsData[0];
      setSelectedModelName(match.model_name);
      setSelectedVersion(match);
    } else {
      // Fallback local mode
      setModels(fallbackLearningHubModels);
    }

    if (releasesData) {
      setReleases(releasesData);
    } else {
      setReleases(fallbackLearningHubReleases);
    }

    setLoading(false);
  }, [roleId, selectedModelName]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Model names list
  const modelNames = useMemo(() => {
    const set = new Set(models.map((m) => m.model_name));
    return Array.from(set);
  }, [models]);

  // Versions for selected model
  const currentModelVersions = useMemo(() => {
    return models.filter((m) => m.model_name === selectedModelName);
  }, [models, selectedModelName]);

  // Pre-release Gate Checklist computation for selectedVersion
  const releaseGateChecklist = useMemo(() => {
    if (!selectedVersion) {
      return {
        validationPassed: false,
        cardComplete: false,
        cardApproved: false,
        rollbackTargetPresent: false,
        canRelease: false,
        reasons: ["No version selected"],
      };
    }

    const valPassed = Boolean(selectedVersion.validation_run?.passed ?? true);
    const card = selectedVersion.model_card;
    const cardComplete = Boolean(
      card &&
        card.intended_use &&
        card.not_intended_use &&
        card.metrics_summary &&
        Object.keys(card.metrics_summary).length > 0 &&
        card.rollback_conditions &&
        card.rollback_conditions.length > 0 &&
        card.privacy_review !== "FAILED" &&
        card.security_review !== "FAILED"
    );
    const cardApproved = Boolean(
      card && card.approvals && card.approvals.some((a) => a.decision === "approved")
    );
    const targetPresent =
      releaseType === "SHADOW" ? true : Boolean(rollbackTarget || selectedVersion.rollback_target);

    const reasons: string[] = [];
    if (!valPassed) reasons.push("Validation checks failed or incomplete");
    if (!cardComplete) reasons.push("Model Card incomplete or security/privacy review FAILED");
    if (!cardApproved) reasons.push("Model Card lacks required reviewer signoff");
    if (!targetPresent) reasons.push("Rollback target version is required for FULL/CANARY/ROLLBACK");

    return {
      validationPassed: valPassed,
      cardComplete,
      cardApproved,
      rollbackTargetPresent: targetPresent,
      canRelease: valPassed && cardComplete && cardApproved && targetPresent,
      reasons,
    };
  }, [selectedVersion, releaseType, rollbackTarget]);

  // Model card approval handler
  const handleApproveCard = async (decision: "approved" | "rejected") => {
    if (!selectedVersion) return;
    setError(null);
    setSuccessMessage(null);

    const res = await approveModelCard({
      modelName: selectedVersion.model_name,
      version: selectedVersion.version,
      decision,
      roleId,
    });

    if (res.ok) {
      setSuccessMessage(`Model Card for ${selectedVersion.model_name}:${selectedVersion.version} marked as ${decision}.`);
      loadData();
    } else {
      setError(`Card approval failed: ${res.detail}`);
    }
  };

  // Submit release request handler (Non-optimistic, audit-tracked)
  const handleRequestRelease = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedVersion) return;
    setError(null);
    setSuccessMessage(null);

    // Validate reason length (FR-GOV-009 requirement)
    if (!releaseReason || releaseReason.trim().length < 10) {
      setError("發布與回滾理由必須至少包含 10 個字 (FR-GOV-009 稽核要求)。");
      return;
    }

    // Validate segregation of duties (self-review forbidden)
    if (approvedBy.trim().toLowerCase() === actor.trim().toLowerCase()) {
      setError("MODEL_RELEASE_SELF_REVIEW: 申請者與核准者不得為同一人 (Segregation of Duties).");
      return;
    }

    setSubmittingRelease(true);
    const result = await requestRelease({
      model_name: selectedVersion.model_name,
      version: selectedVersion.version,
      release_type: releaseType,
      reason: releaseReason.trim(),
      approval_id: approvalId.trim(),
      approved_by: approvedBy.trim(),
      rollback_target: rollbackTarget.trim() || selectedVersion.rollback_target,
      monitoring_window: monitoringWindow,
      success_criteria: successCriteria.split(",").map((s) => s.trim()).filter(Boolean),
      fail_criteria: failCriteria.split(",").map((s) => s.trim()).filter(Boolean),
      affected_modules: affectedModules.split(",").map((s) => s.trim()).filter(Boolean),
      expected_release_revision: 1,
      idempotency_key: `ik-rel-${Date.now()}`,
      requested_by: actor,
      roleId,
    });

    setSubmittingRelease(false);

    if (result.ok) {
      setSuccessMessage(
        `Release ${result.decision.release_id} (${releaseType}) completed successfully. Audit Event: ${result.decision.audit_event_id}`
      );
      setActiveDrawer(null);
      setReleaseReason("");
      loadData();
    } else {
      setError(`Release request rejected by gate/policy: ${result.detail}`);
    }
  };

  // Monitor release evaluation handler
  const handleMonitorRelease = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!monitorReleaseId) return;
    setError(null);

    let observed: Record<string, number> = {};
    try {
      observed = JSON.parse(observedMetricsText);
    } catch {
      setError("Observed metrics must be valid JSON object, e.g. {\"mape\": 0.08}");
      return;
    }

    const guardrails: MetricThreshold[] = [
      { metric_name: "mape", max_value: 0.10, warning_max_value: 0.09 },
      { metric_name: "rmse", max_value: 1500.0, warning_max_value: 1450.0 },
    ];

    const result = await monitorRelease({
      release_id: monitorReleaseId,
      observed_metrics: observed,
      guardrails,
      evaluated_by: actor,
      roleId,
    });

    if (result.ok) {
      setMonitorResult(result.assessment);
      setSuccessMessage(`Release monitoring evaluated: status = ${result.assessment.status}`);
    } else {
      setError(`Monitor evaluation failed: ${result.detail}`);
    }
  };

  return (
    <div className={styles.workspace} data-testid="model-release-controller">
      {/* Workspace Header */}
      <header className={styles.header}>
        <div>
          <div className={styles.eyebrow}>AI Governance & Learning Hub (UX-SCR-LEARN-003 / FR-LH-003)</div>
          <h1 className={styles.title}>Model Release Controller UI</h1>
          <p className={styles.summary}>
            模型登錄、驗證門檻、Model Card 簽核與 Backtest / Champion / Challenger / Shadow / Canary / Rollback 控制台
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <span style={{ fontSize: "12px", opacity: 0.8 }}>Actor: <strong>{actor}</strong></span>
          <button className={styles.secondaryButton} onClick={loadData} disabled={loading}>
            {loading ? "載入中..." : "重新整理"}
          </button>
        </div>
      </header>

      {/* Global Alerts */}
      {error ? (
        <div className={styles.errorAlert} role="alert" style={{ marginBottom: "16px", padding: "12px", borderRadius: "6px", background: "rgba(220, 38, 38, 0.1)", border: "1px solid rgba(220, 38, 38, 0.4)", color: "#ef4444" }}>
          <strong>錯誤 / 門檻拒絕：</strong> {error}
        </div>
      ) : null}

      {successMessage ? (
        <div role="status" style={{ marginBottom: "16px", padding: "12px", borderRadius: "6px", background: "rgba(16, 185, 129, 0.1)", border: "1px solid rgba(16, 185, 129, 0.4)", color: "#10b981" }}>
          <strong>成功：</strong> {successMessage}
        </div>
      ) : null}

      {/* Model Selector Bar */}
      <div style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px", background: "rgba(255, 255, 255, 0.03)", padding: "12px", borderRadius: "8px" }}>
        <label htmlFor="model-select" style={{ fontWeight: 600, fontSize: "14px" }}>選擇模型 (Model):</label>
        <select
          id="model-select"
          aria-label="選擇模型 (Model)"
          value={selectedModelName}
          onChange={(e) => {
            const name = e.target.value;
            setSelectedModelName(name);
            const match = models.find((m) => m.model_name === name);
            if (match) setSelectedVersion(match);
          }}
          style={{ padding: "6px 12px", borderRadius: "4px", background: "#1f2937", color: "#f3f4f6", border: "1px solid #374151" }}
        >
          {modelNames.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <span style={{ fontSize: "12px", color: "#9ca3af", marginLeft: "auto" }}>
          共 {currentModelVersions.length} 個版本
        </span>
      </div>

      {/* Version Registry & Release Controller Layout */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: "20px", marginBottom: "24px" }}>
        {/* Left Column: Version History Table & Details */}
        <div>
          <h2 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "12px" }}>
            模型版本歷史與 Stage 狀態
          </h2>
          <table className={styles.table} style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid #374151", fontSize: "13px", color: "#9ca3af" }}>
                <th style={{ padding: "8px" }}>Version</th>
                <th style={{ padding: "8px" }}>Stage & Aliases</th>
                <th style={{ padding: "8px" }}>Risk</th>
                <th style={{ padding: "8px" }}>Validation</th>
                <th style={{ padding: "8px" }}>Model Card</th>
                <th style={{ padding: "8px" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {currentModelVersions.map((ver, idx) => {
                const isSelected = selectedVersion?.version === ver.version;
                const card = ver.model_card;
                const val = ver.validation_run;

                return (
                  <tr
                    key={`${ver.model_name}-${ver.version}-${idx}`}
                    style={{
                      borderBottom: "1px solid #1f2937",
                      background: isSelected ? "rgba(99, 102, 241, 0.15)" : "transparent",
                    }}
                  >
                    <td style={{ padding: "8px", fontWeight: 600 }}>
                      <button
                        onClick={() => setSelectedVersion(ver)}
                        style={{ background: "none", border: "none", color: "#818cf8", cursor: "pointer", fontWeight: "bold" }}
                      >
                        {ver.version}
                      </button>
                    </td>
                    <td style={{ padding: "8px" }}>
                      <span
                        style={{
                          padding: "2px 8px",
                          borderRadius: "4px",
                          fontSize: "11px",
                          fontWeight: 600,
                          textTransform: "uppercase",
                          background:
                            ver.stage === "production"
                              ? "#6b21a8"
                              : ver.stage === "canary"
                              ? "#1e40af"
                              : ver.stage === "shadow"
                              ? "#1e3a8a"
                              : ver.stage === "rolled_back" || ver.stage === "blocked"
                              ? "#991b1b"
                              : "#374151",
                          color: "#ffffff",
                          marginRight: "6px",
                        }}
                      >
                        {ver.stage}
                      </span>
                      {ver.aliases?.map((alias) => (
                        <span key={alias} style={{ fontSize: "11px", color: "#a7f3d0", background: "rgba(16, 185, 129, 0.2)", padding: "2px 6px", borderRadius: "3px", marginRight: "4px" }}>
                          {alias}
                        </span>
                      ))}
                    </td>
                    <td style={{ padding: "8px" }}>
                      <span style={{ fontWeight: 600, color: card?.risk_level === "R4" || card?.risk_level === "R3" ? "#f87171" : "#34d399" }}>
                        {card?.risk_level || "R2"}
                      </span>
                    </td>
                    <td style={{ padding: "8px" }}>
                      {val ? (
                        val.passed ? (
                          <span style={{ color: "#34d399", fontSize: "12px", fontWeight: 600 }}>✓ Passed</span>
                        ) : (
                          <span style={{ color: "#f87171", fontSize: "12px", fontWeight: 600 }}>✗ Failed</span>
                        )
                      ) : (
                        <span style={{ color: "#9ca3af", fontSize: "12px" }}>未對比</span>
                      )}
                    </td>
                    <td style={{ padding: "8px", fontSize: "12px" }}>
                      {card ? (
                        <span>
                          {card.is_complete ? "✓ 完整" : "⚠ 未完"} / {card.approvals?.some((a) => a.decision === "approved") ? "✓ 已簽核" : "待簽核"}
                        </span>
                      ) : (
                        "缺卡"
                      )}
                    </td>
                    <td style={{ padding: "8px" }}>
                      <div style={{ display: "flex", gap: "6px" }}>
                        <button
                          className={styles.secondaryButton}
                          onClick={() => {
                            setSelectedVersion(ver);
                            setActiveDrawer("card");
                          }}
                          style={{ padding: "2px 6px", fontSize: "11px" }}
                        >
                          Model Card
                        </button>
                        <button
                          className={styles.secondaryButton}
                          onClick={() => {
                            setSelectedVersion(ver);
                            setActiveDrawer("validation");
                          }}
                          style={{ padding: "2px 6px", fontSize: "11px" }}
                        >
                          Validation
                        </button>
                        <button
                          className={styles.primaryButton}
                          onClick={() => {
                            setSelectedVersion(ver);
                            setActiveDrawer("release");
                          }}
                          style={{ padding: "2px 6px", fontSize: "11px" }}
                        >
                          發布
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Right Column: Pre-Release Gate Checklist (FR-GOV-009) */}
        <div style={{ background: "rgba(31, 41, 55, 0.6)", padding: "16px", borderRadius: "8px", border: "1px solid #374151" }}>
          <h3 style={{ fontSize: "14px", fontWeight: 600, marginBottom: "8px", color: "#f3f4f6" }}>
            發布前置 Gate Checklist (FR-GOV-009)
          </h3>
          <p style={{ fontSize: "12px", color: "#9ca3af", marginBottom: "12px" }}>
            對應 selected version: <strong>{selectedVersion?.version}</strong>
          </p>

          <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: "13px" }}>
            <li style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <span style={{ color: releaseGateChecklist.validationPassed ? "#34d399" : "#f87171", fontWeight: "bold" }}>
                {releaseGateChecklist.validationPassed ? "✓" : "✗"}
              </span>
              <span>1. Validation Passed (指標達標)</span>
            </li>
            <li style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <span style={{ color: releaseGateChecklist.cardComplete ? "#34d399" : "#f87171", fontWeight: "bold" }}>
                {releaseGateChecklist.cardComplete ? "✓" : "✗"}
              </span>
              <span>2. Model Card Complete (卡片完整)</span>
            </li>
            <li style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <span style={{ color: releaseGateChecklist.cardApproved ? "#34d399" : "#f87171", fontWeight: "bold" }}>
                {releaseGateChecklist.cardApproved ? "✓" : "✗"}
              </span>
              <span>3. Model Card Approved (審查簽核)</span>
            </li>
            <li style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
              <span style={{ color: releaseGateChecklist.rollbackTargetPresent ? "#34d399" : "#fbbf24", fontWeight: "bold" }}>
                {releaseGateChecklist.rollbackTargetPresent ? "✓" : "⚠"}
              </span>
              <span>4. Rollback Target (回滾目標存在)</span>
            </li>
          </ul>

          {releaseGateChecklist.reasons.length > 0 ? (
            <div style={{ background: "rgba(239, 68, 68, 0.1)", border: "1px solid rgba(239, 68, 68, 0.3)", padding: "8px", borderRadius: "4px", fontSize: "11px", color: "#f87171" }}>
              <strong>未滿足條件：</strong>
              <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                {releaseGateChecklist.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          ) : (
            <div style={{ background: "rgba(52, 211, 153, 0.1)", border: "1px solid rgba(52, 211, 153, 0.3)", padding: "8px", borderRadius: "4px", fontSize: "12px", color: "#34d399" }}>
              ✓ 所有門檻通過，允許申請發布。
            </div>
          )}

          <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "8px" }}>
            <button
              className={styles.primaryButton}
              onClick={() => setActiveDrawer("release")}
              disabled={!releaseGateChecklist.canRelease}
              style={{ width: "100%", opacity: releaseGateChecklist.canRelease ? 1 : 0.5 }}
            >
              開啟發布控制器 (Release Controller)
            </button>
            <button
              className={styles.secondaryButton}
              onClick={() => setActiveDrawer("rollback")}
              style={{ width: "100%" }}
            >
              開啟 Rollback Console
            </button>
          </div>
        </div>
      </div>

      {/* Drawers and Modals */}
      {/* 1. Model Card Drawer */}
      {activeDrawer === "card" && selectedVersion?.model_card ? (
        <div style={{ background: "#111827", border: "1px solid #374151", padding: "20px", borderRadius: "8px", marginBottom: "24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600 }}>
              Model Card: {selectedVersion.model_name}:{selectedVersion.version}
            </h3>
            <button className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>關閉</button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", fontSize: "13px" }}>
            <div>
              <p><strong>Owner:</strong> {selectedVersion.model_card.owner}</p>
              <p><strong>Risk Level:</strong> <span style={{ color: "#f87171" }}>{selectedVersion.model_card.risk_level}</span></p>
              <p><strong>Intended Use:</strong> {selectedVersion.model_card.intended_use}</p>
              <p><strong>Not Intended Use:</strong> {selectedVersion.model_card.not_intended_use}</p>
              <p><strong>Algorithm:</strong> {selectedVersion.model_card.algorithm}</p>
              <p><strong>Baseline:</strong> {selectedVersion.model_card.baseline}</p>
            </div>
            <div>
              <p><strong>Dataset Snapshot:</strong> {selectedVersion.model_card.dataset_snapshot_id}</p>
              <p><strong>Training Period:</strong> {selectedVersion.model_card.training_period}</p>
              <p><strong>Privacy Review:</strong> {selectedVersion.model_card.privacy_review}</p>
              <p><strong>Security Review:</strong> {selectedVersion.model_card.security_review}</p>
              <p><strong>Explainability:</strong> {selectedVersion.model_card.explainability_method}</p>
            </div>
          </div>

          <div style={{ marginTop: "12px", fontSize: "13px" }}>
            <p><strong>Rollback Conditions:</strong></p>
            <ul>
              {selectedVersion.model_card.rollback_conditions.map((cond, i) => (
                <li key={i}>{cond}</li>
              ))}
            </ul>
          </div>

          <div style={{ marginTop: "16px", paddingTop: "12px", borderTop: "1px solid #374151", display: "flex", gap: "12px", alignItems: "center" }}>
            <span style={{ fontSize: "13px", fontWeight: 600 }}>簽核處置 (Model Review Board):</span>
            <button className={styles.primaryButton} onClick={() => handleApproveCard("approved")}>
              Approve Model Card
            </button>
            <button className={styles.secondaryButton} onClick={() => handleApproveCard("rejected")}>
              Reject Model Card
            </button>
          </div>
        </div>
      ) : null}

      {/* 2. Validation Run Drawer */}
      {activeDrawer === "validation" && selectedVersion?.validation_run ? (
        <div style={{ background: "#111827", border: "1px solid #374151", padding: "20px", borderRadius: "8px", marginBottom: "24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600 }}>
              Validation Run & Threshold Comparison: {selectedVersion.version}
            </h3>
            <button className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>關閉</button>
          </div>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", marginBottom: "16px" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #374151", color: "#9ca3af" }}>
                <th style={{ padding: "6px" }}>Metric</th>
                <th style={{ padding: "6px" }}>Threshold</th>
                <th style={{ padding: "6px" }}>Actual Value</th>
                <th style={{ padding: "6px" }}>Baseline Value</th>
                <th style={{ padding: "6px" }}>Delta (Δ)</th>
                <th style={{ padding: "6px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {selectedVersion.validation_run.thresholds.map((t) => {
                const actual = selectedVersion.validation_run?.metrics[t.metric_name] ?? 0;
                const base = selectedVersion.validation_run?.baseline_metrics[t.metric_name] ?? actual;
                const delta = actual - base;
                const passed =
                  t.max_value != null
                    ? actual <= t.max_value
                    : t.min_value != null
                    ? actual >= t.min_value
                    : true;

                return (
                  <tr key={t.metric_name} style={{ borderBottom: "1px solid #1f2937" }}>
                    <td style={{ padding: "6px", fontWeight: 600 }}>{t.metric_name}</td>
                    <td style={{ padding: "6px" }}>
                      {t.max_value != null ? `<= ${t.max_value}` : t.min_value != null ? `>= ${t.min_value}` : "—"}
                    </td>
                    <td style={{ padding: "6px" }}>{actual}</td>
                    <td style={{ padding: "6px" }}>{base}</td>
                    <td style={{ padding: "6px", color: delta <= 0 ? "#34d399" : "#f87171" }}>
                      {delta > 0 ? `+${delta.toFixed(4)}` : delta.toFixed(4)}
                    </td>
                    <td style={{ padding: "6px", fontWeight: 600, color: passed ? "#34d399" : "#f87171" }}>
                      {passed ? "PASSED" : "FAILED"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* 3. Release Controller Form Drawer */}
      {activeDrawer === "release" && selectedVersion ? (
        <div style={{ background: "#111827", border: "1px solid #374151", padding: "20px", borderRadius: "8px", marginBottom: "24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600, color: "#818cf8" }}>
              申請模型發布 (Release Controller) — {selectedVersion.model_name}:{selectedVersion.version}
            </h3>
            <button className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>關閉</button>
          </div>

          <form onSubmit={handleRequestRelease} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
              <div>
                <label htmlFor="release-type-select" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
                  Release Type (發布類型):
                </label>
                <select
                  id="release-type-select"
                  aria-label="Release Type (發布類型)"
                  value={releaseType}
                  onChange={(e) => setReleaseType(e.target.value as never)}
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                >
                  <option value="FULL">FULL (Promote to Champion & Production)</option>
                  <option value="CANARY">CANARY (Canary Stage Release)</option>
                  <option value="SHADOW">SHADOW (Shadow Evaluation Stage)</option>
                  <option value="ROLLBACK">ROLLBACK (Revert to Previous Champion)</option>
                </select>
              </div>

              <div>
                <label htmlFor="approved-by-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
                  Approver Principal (核准者):
                </label>
                <input
                  id="approved-by-input"
                  type="text"
                  value={approvedBy}
                  onChange={(e) => setApprovedBy(e.target.value)}
                  placeholder="e.g. ModelReviewBoard (must not be requester)"
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                  required
                />
              </div>
            </div>

            <div>
              <label htmlFor="release-reason-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
                Release Reason (發布理由，≥10字，寫入 Audit Trail):
              </label>
              <textarea
                id="release-reason-input"
                value={releaseReason}
                onChange={(e) => setReleaseReason(e.target.value)}
                placeholder="說明此模型版本發布的原因與驗證依據 (例如：Promote v2.1.0 to champion after 7-day shadow run showing lower MAPE)"
                rows={3}
                style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                required
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px" }}>
              <div>
                <label htmlFor="approval-id-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>Approval ID:</label>
                <input
                  id="approval-id-input"
                  type="text"
                  value={approvalId}
                  onChange={(e) => setApprovalId(e.target.value)}
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                  required
                />
              </div>
              <div>
                <label htmlFor="monitoring-window-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>Monitoring Window:</label>
                <input
                  id="monitoring-window-input"
                  type="text"
                  value={monitoringWindow}
                  onChange={(e) => setMonitoringWindow(e.target.value)}
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                  required
                />
              </div>
              <div>
                <label htmlFor="rollback-target-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>Rollback Target Version:</label>
                <input
                  id="rollback-target-input"
                  type="text"
                  value={rollbackTarget}
                  onChange={(e) => setRollbackTarget(e.target.value)}
                  placeholder="e.g. v2.0.4"
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                />
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
              <div>
                <label htmlFor="success-criteria-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>Success Criteria:</label>
                <input
                  id="success-criteria-input"
                  type="text"
                  value={successCriteria}
                  onChange={(e) => setSuccessCriteria(e.target.value)}
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                />
              </div>
              <div>
                <label htmlFor="fail-criteria-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>Fail Criteria:</label>
                <input
                  id="fail-criteria-input"
                  type="text"
                  value={failCriteria}
                  onChange={(e) => setFailCriteria(e.target.value)}
                  style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
                />
              </div>
            </div>

            <div>
              <label htmlFor="affected-modules-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
                Affected Modules (Blast Radius 影響領域):
              </label>
              <input
                id="affected-modules-input"
                type="text"
                value={affectedModules}
                onChange={(e) => setAffectedModules(e.target.value)}
                style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px" }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "12px" }}>
              <button type="button" className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>
                取消
              </button>
              <button type="submit" className={styles.primaryButton} disabled={submittingRelease || !releaseGateChecklist.canRelease}>
                {submittingRelease ? "寫入中..." : "送出發布 (Non-Optimistic Submit)"}
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {/* 4. Rollback Console Drawer */}
      {activeDrawer === "rollback" && selectedVersion ? (
        <div style={{ background: "#111827", border: "1px solid #991b1b", padding: "20px", borderRadius: "8px", marginBottom: "24px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600, color: "#f87171" }}>
              Rollback Console (緊急回滾)
            </h3>
            <button className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>關閉</button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "16px", background: "rgba(0,0,0,0.2)", padding: "12px", borderRadius: "6px" }}>
            <div>
              <h4 style={{ fontSize: "13px", color: "#f87171" }}>現行線上版本 (Current Production)</h4>
              <p style={{ fontSize: "14px", fontWeight: 600 }}>{selectedVersion.model_name}:{selectedVersion.version}</p>
              <p style={{ fontSize: "12px", color: "#9ca3af" }}>Stage: {selectedVersion.stage}</p>
            </div>
            <div>
              <h4 style={{ fontSize: "13px", color: "#34d399" }}>回滾目標版本 (Rollback Target)</h4>
              <p style={{ fontSize: "14px", fontWeight: 600 }}>{rollbackTarget || selectedVersion.rollback_target || "無指定"}</p>
              <p style={{ fontSize: "12px", color: "#9ca3af" }}>Target Stage after Rollback: production & champion</p>
            </div>
          </div>

          <form onSubmit={(e) => { setReleaseType("ROLLBACK"); handleRequestRelease(e); }}>
            <label htmlFor="rollback-reason-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
              Rollback Audit Reason (回滾理由，≥10字):
            </label>
            <textarea
              id="rollback-reason-input"
              value={releaseReason}
              onChange={(e) => setReleaseReason(e.target.value)}
              placeholder="說明觸發緊急回滾的異常現象或指標惡化 (例如：線上監控發現 MAPE 暴增至 0.18，緊急回滾至上一穩定版本)"
              rows={3}
              style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px", marginBottom: "12px" }}
              required
            />
            <button type="submit" className={styles.primaryButton} style={{ background: "#dc2626", borderColor: "#b91c1c" }} disabled={submittingRelease}>
              {submittingRelease ? "執行中..." : "確認執行緊急回滾 (Execute Rollback)"}
            </button>
          </form>
        </div>
      ) : null}

      {/* Release History & Monitor Section */}
      <div style={{ marginTop: "24px" }}>
        <h2 style={{ fontSize: "16px", fontWeight: 600, marginBottom: "12px" }}>
          發布歷史與 24h/7d 監控窗 (Release Audit & Monitoring)
        </h2>

        <table className={styles.table} style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid #374151", fontSize: "13px", color: "#9ca3af" }}>
              <th style={{ padding: "8px" }}>Release ID</th>
              <th style={{ padding: "8px" }}>Model & Version</th>
              <th style={{ padding: "8px" }}>Type</th>
              <th style={{ padding: "8px" }}>Approver & Requester</th>
              <th style={{ padding: "8px" }}>Window</th>
              <th style={{ padding: "8px" }}>Audit Event ID</th>
              <th style={{ padding: "8px" }}>監控</th>
            </tr>
          </thead>
          <tbody>
            {releases.map((rel, idx) => (
              <tr key={rel.release_id ? `${rel.release_id}-${idx}` : idx} style={{ borderBottom: "1px solid #1f2937", fontSize: "13px" }}>
                <td style={{ padding: "8px", fontWeight: 600, color: "#818cf8" }}>{rel.release_id}</td>
                <td style={{ padding: "8px" }}>
                  {rel.model_name}:{rel.to_version || rel.version}
                </td>
                <td style={{ padding: "8px" }}>
                  <span style={{ fontWeight: 600, color: rel.release_type === "FULL" ? "#c084fc" : rel.release_type === "ROLLBACK" ? "#f87171" : "#60a5fa" }}>
                    {rel.release_type}
                  </span>
                </td>
                <td style={{ padding: "8px" }}>
                  {rel.approved_by} / {rel.requested_by}
                </td>
                <td style={{ padding: "8px" }}>{rel.monitoring_window}</td>
                <td style={{ padding: "8px", color: "#a7f3d0" }}>
                  <button
                    onClick={() => onOpenAudit && onOpenAudit(rel.audit_event_id)}
                    style={{ background: "none", border: "none", color: "#a7f3d0", cursor: "pointer", textDecoration: "underline" }}
                  >
                    {rel.audit_event_id}
                  </button>
                </td>
                <td style={{ padding: "8px" }}>
                  <button
                    className={styles.secondaryButton}
                    onClick={() => {
                      setMonitorReleaseId(rel.release_id);
                      setActiveDrawer("monitor");
                    }}
                    style={{ padding: "2px 8px", fontSize: "11px" }}
                  >
                    評估 Guardrails
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Monitor Assessment Modal */}
      {activeDrawer === "monitor" ? (
        <div style={{ background: "#111827", border: "1px solid #374151", padding: "20px", borderRadius: "8px", marginTop: "16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h3 style={{ fontSize: "16px", fontWeight: 600 }}>
              Release Monitor Evaluation: {monitorReleaseId}
            </h3>
            <button className={styles.secondaryButton} onClick={() => setActiveDrawer(null)}>關閉</button>
          </div>

          <form onSubmit={handleMonitorRelease}>
            <label htmlFor="observed-metrics-input" style={{ display: "block", fontSize: "12px", marginBottom: "4px" }}>
              Observed Metrics (JSON format):
            </label>
            <textarea
              id="observed-metrics-input"
              value={observedMetricsText}
              onChange={(e) => setObservedMetricsText(e.target.value)}
              rows={3}
              style={{ width: "100%", padding: "6px", background: "#1f2937", border: "1px solid #374151", color: "#fff", borderRadius: "4px", marginBottom: "12px" }}
            />
            <button type="submit" className={styles.primaryButton}>
              Evaluated Guardrails (POST /monitor)
            </button>
          </form>

          {monitorResult ? (
            <div style={{ marginTop: "16px", padding: "12px", background: "rgba(0,0,0,0.3)", borderRadius: "6px" }}>
              <p><strong>Status:</strong> <span style={{ color: monitorResult.status === "HEALTHY" ? "#34d399" : "#f87171" }}>{monitorResult.status}</span></p>
              <p><strong>Evaluated By:</strong> {monitorResult.evaluated_by}</p>
              <p><strong>Audit Event:</strong> {monitorResult.audit_event_id}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
