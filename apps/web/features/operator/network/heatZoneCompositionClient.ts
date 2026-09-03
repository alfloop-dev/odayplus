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
  approveProposal: (
    proposalId: string,
    decidedBy: string,
    notes?: string,
  ) => Promise<boolean>;
  rejectProposal: (
    proposalId: string,
    rejectedBy: string,
    reason: string,
  ) => Promise<boolean>;
  fetchZoneLineage: (zoneId: string) => Promise<Record<string, unknown> | null>;
};

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
        return [];
      }
      const data = await response.json();
      return (data.items || []) as HeatZoneProposal[];
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
      return (await response.json()) as HeatZoneProposal;
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
      return (await response.json()) as ProposalPreviewData;
    },

    async approveProposal(
      proposalId: string,
      decidedBy: string,
      notes?: string,
    ): Promise<boolean> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}/approve`,
        {
          method: "POST",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-approve-${proposalId}`,
            "Idempotency-Key": `idemp-approve-${proposalId}`,
          },
          body: JSON.stringify({
            decided_by: decidedBy,
            notes: notes || undefined,
          }),
        },
      );
      return response.ok;
    },

    async rejectProposal(
      proposalId: string,
      rejectedBy: string,
      reason: string,
    ): Promise<boolean> {
      const response = await fetch(
        `/api/v1/heatzones/merge-split/proposals/${encodeURIComponent(proposalId)}/reject`,
        {
          method: "POST",
          headers: {
            ...headers,
            "X-Correlation-Id": `corr-hz006-proposal-reject-${proposalId}`,
            "Idempotency-Key": `idemp-reject-${proposalId}`,
          },
          body: JSON.stringify({
            rejected_by: rejectedBy,
            reason,
          }),
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
