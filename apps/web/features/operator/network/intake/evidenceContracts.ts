import type { IntakeFieldValue } from "@oday-plus/openapi-client";

export type EvidenceVerificationStatus =
  | "VERIFIED"
  | "PENDING"
  | "FAILED"
  | "TAMPERED";

/**
 * Values in this contract are rendered verbatim from an API response. The UI
 * must never calculate a checksum, infer WORM persistence, or upgrade a missing
 * verification result to VERIFIED.
 */
export type AuthoritativeEvidenceVerification = {
  status: EvidenceVerificationStatus;
  verified_at?: string | null;
  checksum_algorithm?: string | null;
  content_sha256?: string | null;
  signature?: string | null;
  signer_key_version?: string | null;
  worm_sink_id?: string | null;
  worm_checksum?: string | null;
  evidence_state?: string | null;
  audit_event_id?: string | null;
  correlation_id?: string | null;
};

export type AuthoritativeSourceEvidence = {
  original_url?: string | null;
  canonical_url?: string | null;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  parser_version?: string | null;
  observed_at?: string | null;
  captured_at?: string | null;
  policy_state?: string | null;
  policy_version?: string | null;
  policy_expires_at?: string | null;
  correlation_id?: string | null;
};

export type AuthoritativeSensitiveEvidenceAccess = {
  purpose?: string | null;
  purpose_binding_id?: string | null;
  classification?: string | null;
  expires_at?: string | null;
  masked?: boolean | null;
  mask_reason_code?: string | null;
  audit_notice?: string | null;
  legal_hold_state?: string | null;
  legal_hold_id?: string | null;
  legal_hold_expires_at?: string | null;
};

export type AuthoritativeEvidenceReceipt = {
  evidence_receipt_id: string;
  status: string;
  created_at?: string | null;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  audit_event_id?: string | null;
  correlation_id?: string | null;
  version?: number | null;
  verification?: AuthoritativeEvidenceVerification | null;
};

export type AuthoritativeExportReceipt = {
  export_manifest_id: string;
  requested_by: string;
  approved_by: string;
  purpose: string;
  scope: Record<string, unknown>;
  field_mask: Record<string, unknown>;
  source_snapshot_ids: string[];
  audit_event_ids: string[];
  object_uri: string;
  content_sha256: string;
  watermark: string;
  expires_at: string;
  created_at: string;
  download_evidence_id?: string | null;
  signer_key_version?: string | null;
  worm_sink_id?: string | null;
  worm_checksum?: string | null;
};

export type AuthoritativeIdentityReceipt = {
  identity_receipt_id: string;
  operation: string;
  status: string;
  decision_id?: string | null;
  identity_edge_ids?: string[];
  resource_versions?: Record<string, number>;
  audit_event_id?: string | null;
  correlation_id?: string | null;
  occurred_at?: string | null;
};

export type AuthoritativeHumanDecisionEvidence = {
  decision_id: string;
  decision_type: string;
  status: string;
  actor_name?: string | null;
  actor_role_id?: string | null;
  reviewer_subject_id?: string | null;
  reason?: string | null;
  occurred_at?: string | null;
  audit_event_id?: string | null;
  correlation_id?: string | null;
};

export type StructuredAuditBeforeAfter = Record<
  string,
  {
    before?: IntakeFieldValue;
    after?: IntakeFieldValue;
  }
>;

export type StructuredAuditEvent = {
  id: string;
  occurred_at: string;
  actor_name?: string | null;
  actor_role_id?: string | null;
  action: string;
  result?: string | null;
  reason?: string | null;
  reason_code?: string | null;
  before_after?: StructuredAuditBeforeAfter;
  source_snapshot_id?: string | null;
  parser_run_id?: string | null;
  parser_version?: string | null;
  related_ids?: Record<string, string | null | undefined>;
  correlation_id?: string | null;
  version?: number | null;
  evidence_state?: string | null;
  audit_event_id?: string | null;
  message?: string | null;
};

export type AuthoritativeRecoveryContext = {
  operation?: string | null;
  current_state?: string | null;
  current_version?: number | string | null;
  server_value?: unknown;
  preserved_input?: Record<string, unknown> | null;
};
