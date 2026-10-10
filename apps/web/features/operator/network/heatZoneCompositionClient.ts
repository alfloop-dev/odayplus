import type { OperatorRoleId } from "../navigation";
import { operatorSecurityHeaders } from "../operatorSecurityHeaders";
import type {
  HeatZoneProposal,
  ProposalPreviewData,
} from "./HeatZoneMergeSplitPanel";

export type HeatZoneCompositionClient = {
  fetchProposals: (status?: string) => Promise<HeatZoneProposal[]>;
  getProposal: (proposalId: string) => Promise<HeatZoneProposal | null>;
  previewProposal: (proposalId: string) => Promise<ProposalPreviewData | null>;
  // Neither call carries a decider: the API derives the operator from the
  // authenticated principal and rejects a body that claims an identity.
  approveProposal: (proposalId: string, notes?: string) => Promise<boolean>;
  rejectProposal: (proposalId: string, reason: string) => Promise<boolean>;
  fetchZoneLineage: (zoneId: string) => Promise<Record<string, unknown> | null>;
};

// A successful envelope is not enough: malformed items must never become
// decision controls or crash the detail's numeric/array render paths.
function isProposal(value: unknown): value is HeatZoneProposal {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((entry) => typeof entry === "string");
  const finite = (v: unknown) => typeof v === "number" && Number.isFinite(v);
  if (!["proposal_id", "zone_id", "tenant_id", "model_version", "policy_version_id", "created_at"].every((key) => typeof item[key] === "string" && (item[key] as string).trim()) ||
      !["MERGED", "SPLIT_CHILD", "ATOMIC"].includes(String(item.composition_kind)) ||
      !["PROPOSED", "APPROVED", "REJECTED", "APPLIED"].includes(String(item.status)) ||
      !["ndcg_gain", "cannibalization_variance_reduction", "correlation_rho", "disconnect_index", "confidence"].every((key) => finite(item[key])) ||
      !strings(item.member_cell_ids) || item.member_count !== item.member_cell_ids.length ||
      !strings(item.reasons) || !strings(item.warnings)) return false;
  if (!["parent_zone_id", "approved_by", "approved_at", "rejection_reason"].every((key) => item[key] == null || typeof item[key] === "string") ||
      (item.split_density_ratio != null && !finite(item.split_density_ratio))) return false;
  if (item.child_partitions !== undefined && (!Array.isArray(item.child_partitions) || !item.child_partitions.every(strings))) return false;
  if (item.child_zone_ids !== undefined && !strings(item.child_zone_ids)) return false;
  if (item.composition_kind === "SPLIT_CHILD") {
    if (!Array.isArray(item.child_partitions) || item.child_partitions.length < 2 ||
        !strings(item.child_zone_ids) || item.child_zone_ids.length !== item.child_partitions.length ||
        item.child_partitions.some((part) => !part.length)) return false;
    const cells = (item.child_partitions as string[][]).flat();
    if (new Set(cells).size !== cells.length || cells.length !== item.member_cell_ids.length ||
        !cells.every((cell) => (item.member_cell_ids as string[]).includes(cell))) return false;
  }
  return true;
}

export function buildHeatZoneCompositionClient(
  activeRoleId: OperatorRoleId = "expansion-manager",
): HeatZoneCompositionClient {
  const headers = {
    ...operatorSecurityHeaders(activeRoleId),
    "Content-Type": "application/json",
  };

  return {
    async fetchProposals(status?: string): Promise<HeatZoneProposal[]> {
      const url = status && status !== "ALL"
        ? `/api/v1/heatzones/merge-split/proposals?status=${encodeURIComponent(status)}`
        : "/api/v1/heatzones/merge-split/proposals";
      const response = await fetch(url, {
        cache: "no-store",
        headers: {
          ...headers,
          "X-Correlation-Id": `corr-hz006-proposals-list-${Date.now()}`,
        },
      });
      if (!response.ok) {
        // A denied/failed read is not an authoritative empty list.
        throw new Error(`提案清單讀取失敗（HTTP ${response.status}）`);
      }
      const data = await response.json();
      if (!data || !Array.isArray(data.items) || !data.items.every(isProposal) ||
          new Set(data.items.map((item: HeatZoneProposal) => item.proposal_id)).size !== data.items.length) {
        throw new Error("提案清單回應格式不正確");
      }
      return data.items as HeatZoneProposal[];
    },

    async getProposal(proposalId: string): Promise<HeatZoneProposal | null> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}`,
        {
          cache: "no-store",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-get-${proposalId}`,
          },
        },
      );
      if (!response.ok) {
        return null;
      }
      const data: unknown = await response.json();
      if (!isProposal(data) || data.proposal_id !== proposalId) throw new Error("提案回應格式不正確");
      return data;
    },

    async previewProposal(proposalId: string): Promise<ProposalPreviewData | null> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}/preview`,
        {
          method: "POST",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-preview-${proposalId}`,
          },
        },
      );
      if (!response.ok) {
        return null;
      }
      const data = await response.json();
      if (!data || !isProposal(data.proposal) || data.proposal.proposal_id !== proposalId ||
          !Array.isArray(data.current_active_compositions) ||
          !Array.isArray(data.proposed_member_cells) || !data.proposed_member_cells.every((cell: unknown) => typeof cell === "string") ||
          typeof data.proposed_zone_id !== "string" || typeof data.proposed_kind !== "string" ||
          !["expected_ndcg_gain", "expected_cannibalization_variance_reduction", "correlation_rho", "disconnect_index", "confidence"].every((key) => typeof data[key] === "number" && Number.isFinite(data[key]))) {
        throw new Error("提案預覽回應格式不正確");
      }
      return data as ProposalPreviewData;
    },

    async approveProposal(proposalId: string, notes?: string): Promise<boolean> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}/approve`,
        {
          method: "POST",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-approve-${proposalId}`,
            "Idempotency-Key": `idemp-approve-${proposalId}`,
          },
          body: JSON.stringify({ notes: notes || undefined }),
        },
      );
      return response.ok;
    },

    async rejectProposal(proposalId: string, reason: string): Promise<boolean> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}/reject`,
        {
          method: "POST",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-reject-${proposalId}`,
            "Idempotency-Key": `idemp-reject-${proposalId}`,
          },
          body: JSON.stringify({ reason }),
        },
      );
      return response.ok;
    },

    async fetchZoneLineage(zoneId: string): Promise<Record<string, unknown> | null> {
      const response = await fetch(
        `/api/v1/heatzones/zones/${encodeURIComponent(zoneId)}/lineage`,
        {
          cache: "no-store",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-zone-lineage-${zoneId}`,
          },
        },
      );
      if (!response.ok) {
        return null;
      }
      return (await response.json()) as Record<string, unknown>;
    },
  };
}
