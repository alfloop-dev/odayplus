"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { OperatorRoleId } from "../navigation";
import styles from "../networkFindAreas.module.css";
import spatial from "./spatialGovernance.module.css";
import { useModalDialogBehavior } from "./useModalDialogBehavior";
import {
  COMPOSITION_DECISION_DENIED_NOTE,
  canDecideHeatZoneComposition,
} from "./listingPermissions";

export type ProposalStatus = "PROPOSED" | "APPROVED" | "REJECTED" | "APPLIED";
export type CompositionKind = "MERGED" | "SPLIT_CHILD" | "ATOMIC";

export type HeatZoneProposal = {
  proposal_id: string;
  zone_id: string;
  tenant_id: string;
  composition_kind: CompositionKind;
  member_cell_ids: string[];
  member_count: number;
  parent_zone_id?: string | null;
  /**
   * Every side a split divides into, and the zone id each side will get. A
   * split is approved as one decision, so the operator has to be able to see
   * the whole resulting topology before approving it -- the member list alone
   * says which cells are involved, not where each one ends up.
   */
  child_partitions?: string[][];
  child_zone_ids?: string[];
  ndcg_gain: number;
  cannibalization_variance_reduction: number;
  correlation_rho: number;
  disconnect_index: number;
  split_density_ratio?: number | null;
  confidence: number;
  model_version: string;
  policy_version_id: string;
  status: ProposalStatus;
  reasons: string[];
  warnings: string[];
  created_at: string;
  approved_by?: string | null;
  approved_at?: string | null;
  rejection_reason?: string | null;
};

export type ProposalPreviewData = {
  proposal: HeatZoneProposal;
  current_active_compositions: Array<Record<string, any>>;
  proposed_zone_id: string;
  proposed_kind: string;
  proposed_member_cells: string[];
  expected_ndcg_gain: number;
  expected_cannibalization_variance_reduction: number;
  correlation_rho: number;
  disconnect_index: number;
  confidence: number;
};

export type ProposalDecisionReceipt = { readbackConfirmed: boolean };

export type HeatZoneMergeSplitPanelProps = {
  activeRoleId: OperatorRoleId;
  proposals?: HeatZoneProposal[];
  // The deciding operator is taken server-side from the authenticated
  // principal, so the console never names who is approving.
  onApproveProposal?: (proposalId: string, notes?: string) => Promise<ProposalDecisionReceipt | void>;
  onRejectProposal?: (proposalId: string, reason: string) => Promise<ProposalDecisionReceipt | void>;
  onPreviewProposal?: (proposalId: string) => Promise<ProposalPreviewData | null>;
  selectedProposalId?: string | null;
  onSelectProposal?: (proposalId: string) => void;
  isLoading?: boolean;
  apiError?: string | null;
  onReloadProposals?: () => Promise<unknown>;
};

function CompositionDecisionDialog({ kind, proposalId, value, onChange, onClose, onConfirm, pending, available, feedback }: {
  kind: "approve" | "reject";
  proposalId: string;
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
  onConfirm: () => void;
  pending: boolean;
  available: boolean;
  feedback: string | null;
}) {
  const ref = useModalDialogBehavior({ dismissible: !pending, onClose });
  const approve = kind === "approve";
  return <div className={spatial.overlay} data-testid={`${kind}-modal`}>
    <div ref={ref} className={spatial.dialog} role="dialog" aria-modal="true" aria-labelledby="composition-decision-title" aria-describedby="composition-decision-description" aria-busy={pending}>
      <h4 id="composition-decision-title">{approve ? "確認核准熱區拓撲提案" : "拒絕熱區拓撲提案"}</h4>
      <p className={spatial.target}>提案 ID：{proposalId}</p>
      <p id="composition-decision-description">{approve
        ? <>核准將寫入 <code>expansion.heatzone_composition</code> Append-Only 歷史，並將現有衝突活躍關聯自動軟性回滾 (Soft-Rollback)。</>
        : "請輸入拒絕理由。拒絕記錄將寫入審計軌跡以供合規與覆盤。"}</p>
      <label htmlFor="composition-decision-notes">{approve ? "決策附註 / Override 說明 (選填)" : "拒絕理由 (必填) *"}</label>
      <textarea id="composition-decision-notes" data-autofocus rows={3} required={!approve} disabled={pending}
        value={value} onChange={(event) => onChange(event.target.value)}
        placeholder={approve ? "例如：依 Q3 實績吸收率確認合併..." : "例如：行政區邊界不連續或待商圈重評估..."} />
      {!available && !pending && <p role="alert">提案或目前權限已變更，請取消並重新載入；不可提交舊決策。</p>}
      {feedback && <p role="alert" className={spatial.decisionError} data-testid="feedback-message">{feedback}</p>}
      <div className={spatial.dialogActions}>
        <button type="button" onClick={onClose} disabled={pending}>取消</button>
        <button type="button" className={approve ? spatial.approve : spatial.reject} onClick={onConfirm}
          disabled={pending || !available || (!approve && !value.trim())} data-testid={`btn-confirm-${kind}`}>
          {pending ? "處理中…" : approve ? "確認核准並寫入" : "確認拒絕"}
        </button>
      </div>
    </div>
  </div>;
}

export function HeatZoneMergeSplitPanel({
  activeRoleId,
  proposals = [],
  onApproveProposal,
  onRejectProposal,
  onPreviewProposal,
  selectedProposalId: controlledSelectedId,
  onSelectProposal,
  isLoading = false,
  apiError = null,
  onReloadProposals,
}: HeatZoneMergeSplitPanelProps) {
  const [internalSelectedId, setInternalSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [actionInProgress, setActionInProgress] = useState<boolean>(false);
  const [previewData, setPreviewData] = useState<ProposalPreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState<boolean>(false);
  const [operatorNotes, setOperatorNotes] = useState<string>("");
  const [rejectionReason, setRejectionReason] = useState<string>("");
  const [showApproveModal, setShowApproveModal] = useState<boolean>(false);
  const [showRejectModal, setShowRejectModal] = useState<boolean>(false);
  const [decisionTarget, setDecisionTarget] = useState<{ id: string; role: OperatorRoleId } | null>(null);
  const [acknowledgedIds, setAcknowledgedIds] = useState<Set<string>>(() => new Set());
  const decisionPending = useRef(false);
  const decisionScope = useRef(0);
  const [feedbackMessage, setFeedbackMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Offering a button the server will refuse is worse than not offering it.
  const canDecide = canDecideHeatZoneComposition(activeRoleId);

  const selectedId = controlledSelectedId !== undefined ? controlledSelectedId : internalSelectedId;

  const filteredProposals = useMemo(() => {
    if (statusFilter === "ALL") return proposals;
    return proposals.filter((p) => p.status === statusFilter);
  }, [proposals, statusFilter]);

  const activeProposal = useMemo(() => {
    if (isLoading || apiError) return null;
    return filteredProposals.find((p) => p.proposal_id === selectedId) || filteredProposals[0] || null;
  }, [selectedId, filteredProposals, isLoading, apiError]);
  const previewGeneration = useRef(0);
  useEffect(() => {
    previewGeneration.current += 1;
    setPreviewData(null);
    setPreviewLoading(false);
  }, [activeProposal?.proposal_id, activeProposal?.status, activeRoleId]);
  useEffect(() => {
    // A response from the previous persona must not acknowledge in this scope.
    decisionScope.current += 1;
    setShowApproveModal(false);
    setShowRejectModal(false);
    setDecisionTarget(null);
    setOperatorNotes("");
    setRejectionReason("");
    setFeedbackMessage(null);
  }, [activeRoleId]);

  const targetAvailable = !!activeProposal && activeProposal.status === "PROPOSED" &&
    canDecide && !acknowledgedIds.has(activeProposal.proposal_id) && decisionTarget?.id === activeProposal.proposal_id && decisionTarget.role === activeRoleId;
  const openDecision = (kind: "approve" | "reject") => {
    if (!activeProposal || activeProposal.status !== "PROPOSED" || !canDecide || actionInProgress || acknowledgedIds.has(activeProposal.proposal_id)) return;
    setDecisionTarget({ id: activeProposal.proposal_id, role: activeRoleId });
    setFeedbackMessage(null);
    setShowApproveModal(kind === "approve");
    setShowRejectModal(kind === "reject");
  };

  const handleSelect = (propId: string) => {
    if (onSelectProposal) {
      onSelectProposal(propId);
    } else {
      setInternalSelectedId(propId);
    }
    setPreviewData(null);
    setFeedbackMessage(null);
  };

  const handlePreview = async () => {
    if (!activeProposal || !onPreviewProposal) return;
    const generation = ++previewGeneration.current;
    const proposalId = activeProposal.proposal_id;
    setPreviewLoading(true);
    setFeedbackMessage(null);
    try {
      const data = await onPreviewProposal(proposalId);
      if (generation !== previewGeneration.current) return;
      if (!data || data.proposal.proposal_id !== proposalId) throw new Error("無可用的提案預覽回應");
      setPreviewData(data);
    } catch (err: any) {
      if (generation === previewGeneration.current) setFeedbackMessage({ type: "error", text: `預覽失敗: ${err?.message || "未知錯誤"}` });
    } finally {
      if (generation === previewGeneration.current) setPreviewLoading(false);
    }
  };

  const handleDecision = async (kind: "approve" | "reject") => {
    if (!targetAvailable || !decisionTarget || decisionPending.current ||
        (kind === "approve" ? !onApproveProposal : !onRejectProposal || !rejectionReason.trim())) return;
    const proposalId = decisionTarget.id;
    const scope = decisionScope.current;
    decisionPending.current = true;
    setActionInProgress(true);
    setFeedbackMessage(null);
    try {
      const receipt = kind === "approve"
        ? await onApproveProposal!(proposalId, operatorNotes.trim() || undefined)
        : await onRejectProposal!(proposalId, rejectionReason.trim());
      if (scope !== decisionScope.current) return;
      setAcknowledgedIds((ids) => new Set(ids).add(proposalId));
      const label = kind === "approve" ? "核准" : "拒絕";
      setFeedbackMessage({ type: "success", text: receipt?.readbackConfirmed
        ? `提案 ${proposalId} ${label}請求已成功；最新提案狀態已讀回確認。`
        : `提案 ${proposalId} ${label}請求已成功；最新狀態尚未確認，請重新載入提案，勿重複提交。` });
      setShowApproveModal(false);
      setShowRejectModal(false);
      setOperatorNotes("");
      setRejectionReason("");
    } catch (err: any) {
      if (scope === decisionScope.current) setFeedbackMessage({ type: "error", text: `決策請求未確認成功: ${err?.message || "未知錯誤"}` });
    } finally {
      decisionPending.current = false;
      setActionInProgress(false);
    }
  };

  return (
    <div className={`${styles.panel} ${spatial.panel}`} data-testid="heatzone-merge-split-panel">
      <div className={`${styles.panelHeader} ${spatial.header}`}>
        <div>
          <h3>熱區合併／拆分</h3>
          <p className={styles.headerSummary}>
            依據 HZ-004 實績吸收證據、空間相關性及邊界異質性自動產生之熱區拓撲變更提案。
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <select
            aria-label="提案狀態篩選"
            data-testid="proposal-status-filter"
            value={statusFilter}
            disabled={isLoading || !!apiError || actionInProgress || previewLoading || showApproveModal || showRejectModal}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{
              padding: "6px 12px",
              borderRadius: "6px",
              border: "1px solid #cbd5e1",
              fontSize: "13px",
              fontWeight: 600,
            }}
          >
            <option value="ALL">{isLoading || apiError ? "全部提案（尚未確認）" : `全部提案 (${proposals.length})`}</option>
            <option value="PROPOSED">待審批 (PROPOSED)</option>
            <option value="APPROVED">已核准 (APPROVED)</option>
            <option value="REJECTED">已拒絕 (REJECTED)</option>
          </select>
        </div>
      </div>

      {feedbackMessage && !showApproveModal && !showRejectModal && (
        <div
          data-testid="feedback-message"
          role={feedbackMessage.type === "error" ? "alert" : "status"}
          style={{
            margin: "12px 0",
            padding: "10px 14px",
            borderRadius: "6px",
            backgroundColor: feedbackMessage.type === "success" ? "#dcfce7" : "#fee2e2",
            color: feedbackMessage.type === "success" ? "#166534" : "#b91c1c",
            fontSize: "13px",
            fontWeight: 600,
          }}
        >
          {feedbackMessage.text}
        </div>
      )}

      {isLoading ? (
        <div role="status" data-testid="loading-proposals" style={{ padding: "32px", textAlign: "center", color: "#475569" }}>
          正在載入熱區合併／拆分提案數據…
        </div>
      ) : apiError ? (
        <div className={spatial.readError} data-testid="proposal-read-error">
          <p role="alert">{apiError}。尚無法確認目前提案，請重新載入。</p>
          <button type="button" disabled={!onReloadProposals} onClick={() => void onReloadProposals?.()}>
            重新載入提案
          </button>
        </div>
      ) : filteredProposals.length === 0 ? (
        <div style={{ padding: "32px", textAlign: "center", color: "#475569" }} data-testid="empty-proposals">
          目前無符合條件的合併／拆分提案。
        </div>
      ) : (
        <div className={spatial.layout}>
          {/* Proposal List */}
          <div
            style={{
              border: "1px solid #e2e8f0",
              borderRadius: "8px",
              overflowY: "auto",
              maxHeight: "560px",
              backgroundColor: "#ffffff",
            }}
            data-testid="proposal-list"
          >
            {filteredProposals.map((prop) => {
              const isSelected = activeProposal?.proposal_id === prop.proposal_id;
              const isMerged = prop.composition_kind === "MERGED";
              return (
                <button
                  type="button"
                  className={spatial.row}
                  aria-pressed={isSelected}
                  disabled={actionInProgress || previewLoading || showApproveModal || showRejectModal}
                  key={prop.proposal_id}
                  onClick={() => handleSelect(prop.proposal_id)}
                  data-testid={`proposal-item-${prop.proposal_id}`}
                  style={{
                    padding: "12px",
                    borderBottom: "1px solid #f1f5f9",
                    cursor: "pointer",
                    backgroundColor: isSelected ? "#eff6ff" : "transparent",
                    borderLeft: isSelected ? "4px solid #2563eb" : "4px solid transparent",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        padding: "2px 6px",
                        borderRadius: "4px",
                        backgroundColor: isMerged ? "#dbeafe" : "#fef3c7",
                        color: isMerged ? "#1d4ed8" : "#b45309",
                      }}
                    >
                      {prop.composition_kind}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        color:
                          prop.status === "APPROVED"
                            ? "#166534"
                            : prop.status === "REJECTED"
                            ? "#b91c1c"
                            : "#92400e",
                      }}
                    >
                      {prop.status}
                    </span>
                  </div>
                  <div style={{ marginTop: "6px", fontWeight: 700, fontSize: "13px", color: "#1e293b" }}>
                    {prop.zone_id}
                  </div>
                  <div style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                    NDCG 增益: +{(prop.ndcg_gain * 100).toFixed(1)}% | 關聯度: {prop.correlation_rho.toFixed(2)}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Proposal Detail & Preview */}
          {activeProposal && (
            <div
              style={{
                border: "1px solid #e2e8f0",
                borderRadius: "8px",
                padding: "16px",
                backgroundColor: "#ffffff",
              }}
              data-testid="proposal-detail"
              className={spatial.detail}
            >
              <div className={spatial.detailHeader}>
                <div>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    <h4 style={{ margin: 0, fontSize: "16px", color: "#0f172a" }}>
                      {activeProposal.zone_id}
                    </h4>
                    <span
                      style={{
                        fontSize: "12px",
                        fontWeight: 700,
                        padding: "2px 8px",
                        borderRadius: "4px",
                        backgroundColor:
                          activeProposal.composition_kind === "MERGED" ? "#dbeafe" : "#fef3c7",
                        color:
                          activeProposal.composition_kind === "MERGED" ? "#1d4ed8" : "#b45309",
                      }}
                    >
                      {activeProposal.composition_kind}
                    </span>
                  </div>
                  <div style={{ fontSize: "12px", color: "#64748b", marginTop: "4px" }}>
                    提案 ID: {activeProposal.proposal_id} | 模型: {activeProposal.model_version} | 政策: {activeProposal.policy_version_id}
                  </div>
                </div>

                <div className={spatial.actions}>
                  <button
                    type="button"
                    onClick={handlePreview}
                    disabled={previewLoading || actionInProgress || !onPreviewProposal}
                    data-testid="btn-preview-proposal"
                    style={{
                      padding: "6px 14px",
                      borderRadius: "6px",
                      border: "1px solid #cbd5e1",
                      backgroundColor: "#f8fafc",
                      fontSize: "12px",
                      fontWeight: 700,
                      cursor: "pointer",
                    }}
                  >
                    {previewLoading ? "預覽計算中…" : "預覽拓撲效果"}
                  </button>

                  {activeProposal.status === "PROPOSED" && acknowledgedIds.has(activeProposal.proposal_id) && (
                    <span role="status">決策請求已成功，等待最新狀態確認。
                      <button type="button" disabled={actionInProgress || !onReloadProposals} onClick={() => void onReloadProposals?.()}>重新載入提案</button>
                    </span>
                  )}

                  {activeProposal.status === "PROPOSED" && !canDecide && (
                    <span data-testid="composition-decision-denied" style={{ fontSize: "12px", color: "#b45309" }}>
                      {COMPOSITION_DECISION_DENIED_NOTE}
                    </span>
                  )}

                  {activeProposal.status === "PROPOSED" && canDecide && (
                    <>
                      <button
                        type="button"
                        onClick={() => openDecision("approve")}
                        disabled={actionInProgress || previewLoading || !onApproveProposal || acknowledgedIds.has(activeProposal.proposal_id)}
                        data-testid="btn-open-approve"
                        style={{
                          padding: "6px 14px",
                          borderRadius: "6px",
                          border: "none",
                          backgroundColor: "#166534",
                          color: "#ffffff",
                          fontSize: "12px",
                          fontWeight: 700,
                          cursor: "pointer",
                        }}
                      >
                        核准生效
                      </button>
                      <button
                        type="button"
                        onClick={() => openDecision("reject")}
                        disabled={actionInProgress || previewLoading || !onRejectProposal || acknowledgedIds.has(activeProposal.proposal_id)}
                        data-testid="btn-open-reject"
                        style={{
                          padding: "6px 14px",
                          borderRadius: "6px",
                          border: "1px solid #ef4444",
                          backgroundColor: "#ffffff",
                          color: "#dc2626",
                          fontSize: "12px",
                          fontWeight: 700,
                          cursor: "pointer",
                        }}
                      >
                        拒絕提案
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Metrics Cards */}
              <div className={spatial.metrics}>
                <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>預期 NDCG 增益</div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "#166534", marginTop: "2px" }}>
                    +{(activeProposal.ndcg_gain * 100).toFixed(2)}%
                  </div>
                </div>
                <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>自相殘殺殘差縮減</div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "#2563eb", marginTop: "2px" }}>
                    -{(activeProposal.cannibalization_variance_reduction * 100).toFixed(1)}%
                  </div>
                </div>
                <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>實績相關係數 (ρ)</div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "#0f172a", marginTop: "2px" }}>
                    {activeProposal.correlation_rho.toFixed(2)}
                  </div>
                </div>
                <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>需求斷層指數</div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "#0f172a", marginTop: "2px" }}>
                    {activeProposal.disconnect_index.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Details & Reasons */}
              <div className={spatial.facts}>
                <div>
                  <h5 style={{ margin: "0 0 6px 0", color: "#475569" }}>涵蓋 H3 單元成員 ({activeProposal.member_cell_ids.length})</h5>
                  <div
                    style={{
                      maxHeight: "100px",
                      overflowY: "auto",
                      backgroundColor: "#f8fafc",
                      padding: "8px",
                      borderRadius: "4px",
                      border: "1px solid #e2e8f0",
                      fontFamily: "monospace",
                    }}
                    tabIndex={0}
                    aria-label="涵蓋 H3 單元成員"
                  >
                    {activeProposal.member_cell_ids.map((cellId) => (
                      <div key={cellId}>{cellId}</div>
                    ))}
                  </div>
                </div>

                <div>
                  {activeProposal.composition_kind === "SPLIT_CHILD" &&
                    (activeProposal.child_partitions?.length ?? 0) > 0 && (
                      <div data-testid="split-children" style={{ marginBottom: "10px" }}>
                        <h5 style={{ margin: "0 0 6px 0", color: "#475569" }}>
                          分割後子熱區 ({activeProposal.child_partitions!.length})
                        </h5>
                        <dl data-testid="split-density" style={{ margin: "0 0 8px", display: "flex", flexWrap: "wrap", gap: "4px 8px", color: "#475569" }}>
                          <dt>實績吸收密度比</dt>
                          <dd style={{ margin: 0, fontWeight: 700 }}>
                            {activeProposal.split_density_ratio != null && Number.isFinite(activeProposal.split_density_ratio)
                              ? `${activeProposal.split_density_ratio.toFixed(2)} 倍`
                              : "資料未提供"}
                          </dd>
                        </dl>
                        {activeProposal.child_partitions!.map((partition, index) => (
                          <div
                            key={activeProposal.child_zone_ids?.[index] ?? index}
                            style={{
                              marginBottom: "6px",
                              padding: "6px 8px",
                              backgroundColor: "#fffbeb",
                              border: "1px solid #fde68a",
                              borderRadius: "4px",
                              fontFamily: "monospace",
                              fontSize: "11px",
                            }}
                          >
                            <div style={{ color: "#b45309", fontWeight: 700 }}>
                              {activeProposal.child_zone_ids?.[index] ?? `子熱區 ${index + 1}`}
                            </div>
                            {partition.map((cellId) => (
                              <div key={cellId} style={{ color: "#334155" }}>
                                {cellId}
                              </div>
                            ))}
                          </div>
                        ))}
                        <div style={{ color: "#64748b", fontFamily: "inherit" }}>
                          {activeProposal.status === "REJECTED"
                            ? "提案已拒絕；本次決策不建立子熱區或退場父熱區。"
                            : activeProposal.status === "PROPOSED"
                              ? "核准一次即同時建立以上全部子熱區，父熱區同時退場。"
                              : "此為已核准的拆分提案；子熱區與父熱區現況以拓撲讀回為準。"}
                        </div>
                      </div>
                    )}
                  <h5 style={{ margin: "0 0 6px 0", color: "#475569" }}>治理觸發理由與依據</h5>
                  <ul style={{ margin: 0, paddingLeft: "18px", color: "#334155" }}>
                    {activeProposal.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                  {activeProposal.parent_zone_id && (
                    <div style={{ marginTop: "8px", color: "#64748b" }}>
                      父熱區 ID: <code>{activeProposal.parent_zone_id}</code>
                    </div>
                  )}
                </div>
              </div>

              {activeProposal.warnings.length > 0 && (
                <div className={spatial.warning} aria-label="提案警示">
                  <strong>警示與限制</strong>
                  <ul>{activeProposal.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
                </div>
              )}

              {/* Preview Comparison Box if available */}
              {previewData?.proposal.proposal_id === activeProposal.proposal_id && (
                <div
                  data-testid="preview-box"
                  style={{
                    marginTop: "16px",
                    padding: "14px",
                    backgroundColor: "#f0fdf4",
                    borderRadius: "6px",
                    border: "1px solid #bbf7d0",
                  }}
                >
                  <h5 style={{ margin: "0 0 8px 0", color: "#166534" }}>拓撲預覽生效評估結果</h5>
                  <div style={{ fontSize: "12px", color: "#15803d" }}>
                    預期變更將涵蓋 {previewData.proposed_member_cells.length} 個 H3 空間單元，
                    取代目前 {previewData.current_active_compositions.length} 筆活躍關聯，
                    提供 +{(previewData.expected_ndcg_gain * 100).toFixed(2)}% NDCG 排序品質增益。
                  </div>
                </div>
              )}

              {/* Decision History */}
              {activeProposal.approved_by && (
                <div style={{ marginTop: "14px", padding: "10px", backgroundColor: "#f1f5f9", borderRadius: "6px", fontSize: "12px" }}>
                  <strong>審批記錄:</strong> 由 <code>{activeProposal.approved_by}</code> 於 {activeProposal.approved_at || activeProposal.created_at} 處理。
                  {activeProposal.rejection_reason && (
                    <div style={{ color: "#b91c1c", marginTop: "4px" }}>
                      拒絕原因: {activeProposal.rejection_reason}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {(showApproveModal || showRejectModal) && decisionTarget && <CompositionDecisionDialog
        kind={showApproveModal ? "approve" : "reject"}
        proposalId={decisionTarget.id}
        value={showApproveModal ? operatorNotes : rejectionReason}
        onChange={showApproveModal ? setOperatorNotes : setRejectionReason}
        onClose={() => { if (!decisionPending.current) { setShowApproveModal(false); setShowRejectModal(false); } }}
        onConfirm={() => void handleDecision(showApproveModal ? "approve" : "reject")}
        pending={actionInProgress}
        available={targetAvailable}
        feedback={feedbackMessage?.type === "error" ? feedbackMessage.text : null}
      />}
    </div>
  );
}
