/** Unit HTTP transport values, never native producers or policy approvals. */
import type { CanonicalEvidenceSelection } from "../../governance/canonicalEvidenceApi";

export const digest = "a".repeat(64);
export const source = { decision_id: "sitescore-decision-engineering", decision_version: 3, candidate_id: "candidate-engineering",
  decided_at: "2026-10-15T08:10:00Z", period_start: "2026-10-15T08:00:00Z", period_end: "2026-10-15T08:10:00Z", source_digest: digest };
export const selection: CanonicalEvidenceSelection = { decision_id: source.decision_id, expected_decision_version: 3,
  sections: ["decision_log", "audit"], reason: "Inspect the actual recorded decision", from_time: "2026-10-15T00:00:00.000Z", to_time: "2026-10-15T23:59:59.999999Z", format: "json" };
export const document = { schema: "sitescore.decision-audit.references.v1", source_digest: digest, classification: "CONFIDENTIAL", masking_profile: "masked", actor_fields_masked: true,
  limits: { content: "recorded_decision_and_audit_references_only", source_content_included: false, model_readiness_claimed: false, subsidy_eligibility_claimed: false },
  sections: {
    decision_log: [{ decision_id: source.decision_id, decision_version: 3, candidate_id: source.candidate_id, report_id: "report-engineering", report_version: 1,
      report_sha256: digest, source_snapshot_ids: ["recorded-source-reference"], recorded_model_reference: "recorded-model-reference", recorded_feature_reference: "recorded-feature-reference",
      recorded_policy_reference: "recorded-policy-reference", previous_decision_id: null, approval_id: "real-engineering-signature", approval_status: "approved",
      decision_actor: "******", decided_at: source.decided_at, recorded_network_action: "GO", rationale_sha256: digest, source_digest: digest }],
    audit: [{ event_id: "actual-engineering-event", event_hash: digest, action: "approve", outcome: "success", actor: "******", correlation_id: "engineering-cid", occurred_at: source.decided_at,
      decision_version: 3, content_sha256: digest }],
  } };
export function previewValue() { return { document: structuredClone(document), artifact_sha256: digest, decision_id: source.decision_id, decision_version: 3, reason: selection.reason,
  classification: "CONFIDENTIAL", retention_class: "audit_5y", retention_days: 1826, formats: ["json", "csv", "pdf"], retained: false, external_acknowledged: false }; }
export function chainPreviewValue() {
  const value = previewValue();
  const interval = { p10: 1, p50: 2, p90: 3 };
  const chain = { schema: "sitescore.recorded-decision-chain.v1", source_digest: digest,
    prediction: { status: "recorded", report_id: "report-engineering", report_version: 1, report_sha256: digest,
      intervals: { m1: interval, m3: interval, m6: interval, m12: interval }, payback_period: interval, payback_p50_months: 2,
      confidence: null, confidence_status: "unmeasured", model_reference: "recorded-model-reference", feature_reference: "recorded-feature-reference",
      feature_snapshot_time: source.period_start, prediction_origin_time: source.period_start, scored_at: source.period_start, source_snapshot_ids: ["recorded-source-reference"] },
    recommendation: { status: "recorded", recorded_recommendation: "GO", recorded_network_action: "GO" },
    human_decision: { status: "recorded", decision_id: source.decision_id, decision_version: 3, approval_id: "real-engineering-signature", approval_status: "approved",
      actor: "******", decided_at: source.decided_at, policy_reference: "recorded-policy-reference", reason_masked: "****", reason_sha256: digest, actor_fields_masked: true,
      history: [{ version: 3, action: "approve", from_status: "PENDING_REVIEW", to_status: "APPROVED", actor: "******", reason_masked: "****",
        reason_sha256: digest, occurred_at: source.decided_at, approval_id: "real-engineering-signature", correlation_id: "engineering-cid" }] },
    execution: { status: "unavailable", record: null, reason: "CANONICAL_REALIZATION_OWNER_NOT_BOUND" },
    outcome: { status: "unavailable", record: null, reason: "NO_CANONICAL_DECISION_BOUND_SITESCORE_OUTCOME_OWNER" },
    coverage: { state: "degraded", complete: false, missing: ["outcome", "execution"] },
    limits: { model_readiness_claimed: false, source_content_included: false, physical_opening_or_revenue_outcome_claimed: false, actor_and_reason_masked_under_existing_shared_policy: true } };
  Object.assign(value.document, { schema: "sitescore.decision-audit.recorded-partial-chain.v2", recorded_chain: chain,
    limits: { ...value.document.limits, content: "recorded_decision_audit_and_partial_chain", full_QA03_complete: false } });
  return value;
}
export function reply(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
export function catalog() { return { sources: [source], supported_modules: ["SiteScore"], supported_sections: ["decision_log", "audit"], limits: "recorded_decision_and_audit_references_only" }; }
