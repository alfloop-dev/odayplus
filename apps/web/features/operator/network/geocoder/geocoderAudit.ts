// Governed geocoder address search — audit events (ODP-CAP-GEOCODER-SEARCH-001).
//
// Owned layer  : the shape of the audit record the console emits when an
//                operator selects a geocode candidate, overrides a
//                low-confidence one, or rejects a search outright.
// Not changing : where the event is persisted. The caller supplies the sink,
//                because the intake and candidate-site surfaces each write to
//                their own governed endpoint; this module only guarantees that
//                what they write is complete and matches what was on screen.
// Composes with: geocoderPolicy (the verdict and risk copy being recorded) and
//                the AuditMeta envelope in @oday-plus/domain-types.
//
// The event captures the risk summary VERBATIM as it was displayed. It is not
// rebuilt at submit time: an audit reader needs to know what the operator was
// actually shown, not what the current build would show them.

import type {
  CandidateAssessment,
  GeocodeAuditEvent,
  GeocodeCandidate,
} from "./geocoderTypes";
import { requiresExplicitReview } from "./geocoderPolicy";

/**
 * Build the audit event for an accepted candidate.
 *
 * `action` distinguishes a clean selection from an override so a governance
 * query can count overrides without re-deriving the policy: any candidate that
 * needed explicit review and was accepted anyway is a `low_confidence_override`.
 */
export function buildSelectionAuditEvent(input: {
  candidate: GeocodeCandidate;
  assessment: CandidateAssessment;
  actorRoleId: string;
  correlationId: string;
  riskSummary: string;
  reviewReason: string;
  reviewAcknowledged: boolean;
  occurredAt?: string;
}): GeocodeAuditEvent {
  const override = requiresExplicitReview(input.assessment);
  const reason = input.reviewReason.trim();

  return {
    action: override ? "low_confidence_override" : "candidate_selected",
    actorRoleId: input.actorRoleId,
    occurredAt: input.occurredAt ?? new Date().toISOString(),
    correlationId: input.correlationId,
    addressRaw: input.candidate.addressRaw,
    candidateId: input.candidate.candidateId,
    selected: {
      latitude: input.candidate.latitude,
      longitude: input.candidate.longitude,
      precision: String(input.candidate.precision ?? ""),
      confidence: input.candidate.confidence,
      provider: input.candidate.provider,
      providerRequestId: input.candidate.providerRequestId,
      formattedAddress: input.candidate.formattedAddress,
    },
    flags: [...input.assessment.flags],
    requirement: input.assessment.requirement,
    // Null rather than "" when no review was required, so an audit reader can
    // tell "not applicable" from "left blank".
    reviewReason: override ? reason : null,
    reviewAcknowledged: input.reviewAcknowledged,
    riskSummary: input.riskSummary,
  };
}

/**
 * Build the audit event for a search the operator declined to accept.
 *
 * Recorded because "the geocoder offered nothing usable" is itself governance
 * evidence: it explains why a downstream record has no coordinate, and it must
 * not be indistinguishable from a search that never happened.
 */
export function buildRejectionAuditEvent(input: {
  addressRaw: string;
  actorRoleId: string;
  correlationId: string;
  reason: string;
  riskSummary: string;
  occurredAt?: string;
}): GeocodeAuditEvent {
  return {
    action: "search_rejected",
    actorRoleId: input.actorRoleId,
    occurredAt: input.occurredAt ?? new Date().toISOString(),
    correlationId: input.correlationId,
    addressRaw: input.addressRaw,
    candidateId: null,
    selected: null,
    flags: [],
    requirement: "explicit_review_required",
    reviewReason: input.reason.trim(),
    reviewAcknowledged: true,
    riskSummary: input.riskSummary,
  };
}
