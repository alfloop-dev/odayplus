"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { OperatorRoleId } from "../navigation";
import styles from "../networkFindAreas.module.css";
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

export type HeatZoneMergeSplitPanelProps = {
  activeRoleId: OperatorRoleId;
  proposals?: HeatZoneProposal[];
  // The deciding operator is taken server-side from the authenticated
  // principal, so the console never names who is approving.
  onApproveProposal?: (proposalId: string, notes?: string) => Promise<void>;
  onRejectProposal?: (proposalId: string, reason: string) => Promise<void>;
  onPreviewProposal?: (proposalId: string) => Promise<ProposalPreviewData | null>;
  selectedProposalId?: string | null;
  onSelectProposal?: (proposalId: string) => void;
  isLoading?: boolean;
};

export function HeatZoneMergeSplitPanel({
  activeRoleId,
  proposals = [],
  onApproveProposal,
  onRejectProposal,
  onPreviewProposal,
  selectedProposalId: controlledSelectedId,
  onSelectProposal,
  isLoading = false,
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
  const [feedbackMessage, setFeedbackMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Offering a button the server will refuse is worse than not offering it.
  const canDecide = canDecideHeatZoneComposition(activeRoleId);

  const selectedId = controlledSelectedId !== undefined ? controlledSelectedId : internalSelectedId;

  const filteredProposals = useMemo(() => {
    if (statusFilter === "ALL") return proposals;
    return proposals.filter((p) => p.status === statusFilter);
  }, [proposals, statusFilter]);

  const activeProposal = useMemo(() => {
    if (!selectedId) return filteredProposals[0] || null;
    return proposals.find((p) => p.proposal_id === selectedId) || filteredProposals[0] || null;
  }, [proposals, selectedId, filteredProposals]);

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
    setPreviewLoading(true);
    setFeedbackMessage(null);
    try {
      const data = await onPreviewProposal(activeProposal.proposal_id);
      setPreviewData(data);
    } catch (err: any) {
      setFeedbackMessage({ type: "error", text: `預覽失敗: ${err?.message || "未知錯誤"}` });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleApprove = async () => {
    if (!activeProposal || !onApproveProposal) return;
    setActionInProgress(true);
    setFeedbackMessage(null);
    try {
      await onApproveProposal(activeProposal.proposal_id, operatorNotes || undefined);
      setFeedbackMessage({ type: "success", text: `提案 ${activeProposal.proposal_id} 已成功核准並生效！` });
      setShowApproveModal(false);
      setOperatorNotes("");
    } catch (err: any) {
      setFeedbackMessage({ type: "error", text: `核准失敗: ${err?.message || "未知錯誤"}` });
    } finally {
      setActionInProgress(false);
    }
  };

  const handleReject = async () => {
    if (!activeProposal || !onRejectProposal) return;
    if (!rejectionReason.trim()) {
      setFeedbackMessage({ type: "error", text: "請輸入拒絕理由" });
      return;
    }
    setActionInProgress(true);
    setFeedbackMessage(null);
    try {
      await onRejectProposal(activeProposal.proposal_id, rejectionReason);
      setFeedbackMessage({ type: "success", text: `提案 ${activeProposal.proposal_id} 已標記為拒絕。` });
      setShowRejectModal(false);
      setRejectionReason("");
    } catch (err: any) {
      setFeedbackMessage({ type: "error", text: `拒絕失敗: ${err?.message || "未知錯誤"}` });
    } finally {
      setActionInProgress(false);
    }
  };

  return (
    <div className={styles.panel} data-testid="heatzone-merge-split-panel">
      <div className={styles.panelHeader}>
        <div>
          <span className={styles.kicker}>ODP-FR-HZ-006 空間治理</span>
          <h3 style={{ margin: "4px 0" }}>熱區合併／拆分提案審批 (Merge & Split Governance)</h3>
          <p className={styles.headerSummary}>
            依據 HZ-004 實績吸收證據、空間相關性及邊界異質性自動產生之熱區拓撲變更提案。
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <select
            aria-label="提案狀態篩選"
            data-testid="proposal-status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{
              padding: "6px 12px",
              borderRadius: "6px",
              border: "1px solid #cbd5e1",
              fontSize: "13px",
              fontWeight: 600,
            }}
          >
            <option value="ALL">全部提案 ({proposals.length})</option>
            <option value="PROPOSED">待審批 (PROPOSED)</option>
            <option value="APPROVED">已核准 (APPROVED)</option>
            <option value="REJECTED">已拒絕 (REJECTED)</option>
          </select>
        </div>
      </div>

      {feedbackMessage && (
        <div
          data-testid="feedback-message"
          style={{
            margin: "12px 0",
            padding: "10px 14px",
            borderRadius: "6px",
            backgroundColor: feedbackMessage.type === "success" ? "#dcfce7" : "#fee2e2",
            color: feedbackMessage.type === "success" ? "#15803d" : "#b91c1c",
            fontSize: "13px",
            fontWeight: 600,
          }}
        >
          {feedbackMessage.text}
        </div>
      )}

      {isLoading ? (
        <div style={{ padding: "32px", textAlign: "center", color: "#64748b" }}>
          正在載入熱區合併／拆分提案數據…
        </div>
      ) : filteredProposals.length === 0 ? (
        <div style={{ padding: "32px", textAlign: "center", color: "#64748b" }} data-testid="empty-proposals">
          目前無符合條件的合併／拆分提案。
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: "16px", marginTop: "12px" }}>
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
                <div
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
                            ? "#16a34a"
                            : prop.status === "REJECTED"
                            ? "#dc2626"
                            : "#d97706",
                      }}
                    >
                      {prop.status}
                    </span>
                  </div>
                  <div style={{ marginTop: "6px", fontWeight: 700, fontSize: "13px", color: "#1e293b" }}>
                    {prop.zone_id}
                  </div>
                  <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px" }}>
                    NDCG 增益: +{(prop.ndcg_gain * 100).toFixed(1)}% | 關聯度: {prop.correlation_rho.toFixed(2)}
                  </div>
                </div>
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
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
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

                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    type="button"
                    onClick={handlePreview}
                    disabled={previewLoading}
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

                  {activeProposal.status === "PROPOSED" && !canDecide && (
                    <span data-testid="composition-decision-denied" style={{ fontSize: "12px", color: "#b45309" }}>
                      {COMPOSITION_DECISION_DENIED_NOTE}
                    </span>
                  )}

                  {activeProposal.status === "PROPOSED" && canDecide && (
                    <>
                      <button
                        type="button"
                        onClick={() => setShowApproveModal(true)}
                        disabled={actionInProgress}
                        data-testid="btn-open-approve"
                        style={{
                          padding: "6px 14px",
                          borderRadius: "6px",
                          border: "none",
                          backgroundColor: "#16a34a",
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
                        onClick={() => setShowRejectModal(true)}
                        disabled={actionInProgress}
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
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: "10px",
                  margin: "16px 0",
                }}
              >
                <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ fontSize: "11px", color: "#64748b" }}>預期 NDCG 增益</div>
                  <div style={{ fontSize: "18px", fontWeight: 800, color: "#16a34a", marginTop: "2px" }}>
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
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", fontSize: "12px" }}>
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
                  >
                    {activeProposal.member_cell_ids.map((cellId) => (
                      <div key={cellId}>{cellId}</div>
                    ))}
                  </div>
                </div>

                <div>
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

              {/* Preview Comparison Box if available */}
              {previewData && (
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
                    <div style={{ color: "#dc2626", marginTop: "4px" }}>
                      拒絕原因: {activeProposal.rejection_reason}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Approve Modal */}
      {showApproveModal && (
        <div
          data-testid="approve-modal"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: "#ffffff",
              borderRadius: "8px",
              padding: "24px",
              width: "480px",
              maxWidth: "90%",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
            }}
          >
            <h4 style={{ margin: "0 0 12px 0", fontSize: "16px", color: "#0f172a" }}>
              確認核准熱區拓撲提案
            </h4>
            <p style={{ fontSize: "13px", color: "#475569", margin: "0 0 16px 0" }}>
              核准將寫入 <code>expansion.heatzone_composition</code> Append-Only 歷史，並將現有衝突活躍關聯自動軟性回滾 (Soft-Rollback)。
            </p>
            <div style={{ marginBottom: "16px" }}>
              <label style={{ display: "block", fontSize: "12px", fontWeight: 700, marginBottom: "6px", color: "#334155" }}>
                決策附註 / Override 說明 (選填)
              </label>
              <textarea
                rows={3}
                value={operatorNotes}
                onChange={(e) => setOperatorNotes(e.target.value)}
                placeholder="例如：依 Q3 實績吸收率確認合併..."
                style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1", fontSize: "13px" }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                onClick={() => setShowApproveModal(false)}
                disabled={actionInProgress}
                style={{ padding: "8px 16px", borderRadius: "6px", border: "1px solid #cbd5e1", backgroundColor: "#ffffff", cursor: "pointer" }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleApprove}
                disabled={actionInProgress}
                data-testid="btn-confirm-approve"
                style={{ padding: "8px 16px", borderRadius: "6px", border: "none", backgroundColor: "#16a34a", color: "#ffffff", fontWeight: 700, cursor: "pointer" }}
              >
                {actionInProgress ? "處理中…" : "確認核准並寫入"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Modal */}
      {showRejectModal && (
        <div
          data-testid="reject-modal"
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              backgroundColor: "#ffffff",
              borderRadius: "8px",
              padding: "24px",
              width: "480px",
              maxWidth: "90%",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1)",
            }}
          >
            <h4 style={{ margin: "0 0 12px 0", fontSize: "16px", color: "#0f172a" }}>
              拒絕熱區拓撲提案
            </h4>
            <p style={{ fontSize: "13px", color: "#475569", margin: "0 0 16px 0" }}>
              請輸入拒絕理由。拒絕記錄將寫入審計軌跡以供合規與覆盤。
            </p>
            <div style={{ marginBottom: "16px" }}>
              <label style={{ display: "block", fontSize: "12px", fontWeight: 700, marginBottom: "6px", color: "#334155" }}>
                拒絕理由 (必填) *
              </label>
              <textarea
                rows={3}
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                placeholder="例如：行政區邊界不連續或待商圈重評估..."
                style={{ width: "100%", padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1", fontSize: "13px" }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button
                type="button"
                onClick={() => setShowRejectModal(false)}
                disabled={actionInProgress}
                style={{ padding: "8px 16px", borderRadius: "6px", border: "1px solid #cbd5e1", backgroundColor: "#ffffff", cursor: "pointer" }}
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleReject}
                disabled={actionInProgress}
                data-testid="btn-confirm-reject"
                style={{ padding: "8px 16px", borderRadius: "6px", border: "none", backgroundColor: "#dc2626", color: "#ffffff", fontWeight: 700, cursor: "pointer" }}
              >
                {actionInProgress ? "處理中…" : "確認拒絕"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
