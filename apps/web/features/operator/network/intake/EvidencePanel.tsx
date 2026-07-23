"use client";

import type {
  AssistedIntake,
  AuditReference,
  FieldValue,
  SourcePolicyState,
} from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import type {
  AuthoritativeEvidenceVerification,
  AuthoritativeExportReceipt,
  AuthoritativeHumanDecisionEvidence,
  AuthoritativeSensitiveEvidenceAccess,
  AuthoritativeSourceEvidence,
} from "./evidenceContracts";
import { matchLabel, matchTone, policyLabel, policyTone } from "./intakeTypes";
import { formatIntakeDateTime } from "./types";

type EvidenceField = {
  field_path: string;
  parsed?: unknown;
  normalized?: unknown;
  corrected?: unknown;
  effective?: unknown;
  confidence?: number | null;
  classification?: string | null;
  masked?: boolean;
  mask_reason_code?: string | null;
  low_confidence?: boolean;
};

export type EvidencePanelProps = {
  record: AssistedIntake;
  fields?: FieldValue[];
  sourceEvidence?: AuthoritativeSourceEvidence | null;
  access?: AuthoritativeSensitiveEvidenceAccess | null;
  humanDecision?: AuthoritativeHumanDecisionEvidence | null;
  verification?: AuthoritativeEvidenceVerification | null;
  exportReceipt?: AuthoritativeExportReceipt | null;
  auditReferences?: AuditReference[];
  etag?: string | null;
  onOpenFix?: (fieldKey: string) => void;
  maskedView?: boolean;
  testId?: string;
};

function SourceValue({
  label,
  value,
  testId,
  temporal = false,
}: {
  label: string;
  value: unknown;
  testId?: string;
  temporal?: boolean;
}) {
  const unavailable = value === null || value === undefined || value === "";
  const formatted =
    temporal && typeof value === "string"
      ? formatIntakeDateTime(value)
      : null;
  return (
    <div className={styles.receiptValue}>
      <dt>{label}</dt>
      <dd
        data-authoritative={unavailable ? "missing" : "present"}
        data-testid={testId}
      >
        {formatted ? (
          <time dateTime={String(value)} title={formatted.title}>
            {formatted.text}
          </time>
        ) : unavailable ? (
          "API 未回傳"
        ) : (
          String(value)
        )}
      </dd>
    </div>
  );
}

function displayFieldValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function legacyFields(record: AssistedIntake): EvidenceField[] {
  return Object.values(record.parsedFields ?? {}).map((field) => ({
    field_path: field.key,
    parsed: field.sourceValue,
    normalized: field.normalizedValue,
    corrected: field.correctedValue,
    masked: field.masked,
    mask_reason_code: field.mask_reason_code,
    low_confidence: field.lowConfidence,
  }));
}

function isSourcePolicyState(value: string): value is SourcePolicyState {
  return [
    "APPROVED_RETRIEVAL",
    "ASSISTED_ENTRY_ONLY",
    "AUTH_REQUIRED",
    "SOURCE_BLOCKED",
    "POLICY_UNKNOWN",
  ].includes(value);
}

export function EvidencePanel({
  record,
  fields,
  sourceEvidence,
  access,
  humanDecision,
  verification,
  exportReceipt,
  auditReferences = [],
  etag,
  onOpenFix,
  maskedView = false,
  testId = "intake-evidence-panel",
}: EvidencePanelProps) {
  const fieldList: EvidenceField[] = fields ?? legacyFields(record);
  const source: AuthoritativeSourceEvidence = {
    original_url: sourceEvidence?.original_url ?? record.originalUrl,
    canonical_url: sourceEvidence?.canonical_url ?? record.canonicalUrl,
    source_snapshot_id: sourceEvidence?.source_snapshot_id ?? record.snapshotId,
    parser_run_id: sourceEvidence?.parser_run_id,
    parser_version: sourceEvidence?.parser_version ?? record.parserVersion,
    observed_at: sourceEvidence?.observed_at,
    captured_at: sourceEvidence?.captured_at ?? record.capturedAt,
    policy_state: sourceEvidence?.policy_state ?? record.policy,
    policy_version: sourceEvidence?.policy_version,
    policy_expires_at: sourceEvidence?.policy_expires_at,
    correlation_id: sourceEvidence?.correlation_id ?? record.correlationId,
  };
  const urlDiffers = Boolean(
    source.original_url
      && source.canonical_url
      && source.original_url !== source.canonical_url,
  );
  const matchResult = record.matchResult;

  return (
    <section className={styles.sectionBox} data-testid={testId}>
      <div className={styles.receiptHeader}>
        <div>
          <div className={styles.sectionLabel}>解析與來源證據 EVIDENCE</div>
          <p className={styles.help}>
            所有識別碼、分類、目的、遮罩與驗證狀態均直接顯示後端回應。
          </p>
        </div>
        {etag ? <code data-testid="evidence-etag">{etag}</code> : null}
      </div>

      <section className={styles.evidenceSection} data-testid="evidence-source-section">
        <div className={styles.sectionLabel}>來源與擷取 LINEAGE</div>
        <dl className={styles.receiptList}>
          <SourceValue
            label="原始 URL"
            testId="evidence-original-url"
            value={source.original_url}
          />
          <SourceValue
            label={urlDiffers ? "Canonical URL（與原始網址不同）" : "Canonical URL"}
            testId="evidence-canonical-url"
            value={source.canonical_url}
          />
          <SourceValue label="Snapshot ID" value={source.source_snapshot_id} />
          <SourceValue
            label="Parser run ID"
            testId="evidence-parser-run-id"
            value={source.parser_run_id}
          />
          <SourceValue label="Parser version" value={source.parser_version} />
          <SourceValue label="Observed at" temporal value={source.observed_at} />
          <SourceValue label="Captured at" temporal value={source.captured_at} />
          <SourceValue label="Policy version" value={source.policy_version} />
          <SourceValue
            label="Policy expires at"
            temporal
            value={source.policy_expires_at}
          />
          <SourceValue label="Correlation ID" value={source.correlation_id} />
        </dl>
        {source.policy_state ? (
          <div>
            <span className={styles.metaLabel}>Policy state </span>
            <span
              className={styles.chip}
              data-tone={isSourcePolicyState(source.policy_state)
                ? policyTone(source.policy_state)
                : "neutral"}
              data-testid="evidence-policy-state"
            >
              {isSourcePolicyState(source.policy_state)
                ? policyLabel(source.policy_state)
                : source.policy_state}
              {" · "}
              {source.policy_state}
            </span>
          </div>
        ) : null}
      </section>

      <section className={styles.evidenceSection} data-testid="evidence-access-section">
        <div className={styles.sectionLabel}>敏感證據存取 PURPOSE BINDING</div>
        <dl className={styles.receiptList}>
          <SourceValue label="Purpose" value={access?.purpose} />
          <SourceValue label="Purpose binding ID" value={access?.purpose_binding_id} />
          <SourceValue label="Classification" value={access?.classification} />
          <SourceValue label="Access expires at" temporal value={access?.expires_at} />
          <SourceValue
            label="Masked"
            value={
              access?.masked === null || access?.masked === undefined
                ? undefined
                : String(access.masked)
            }
          />
          <SourceValue label="Mask reason code" value={access?.mask_reason_code} />
          <SourceValue label="Audit notice" value={access?.audit_notice} />
          <SourceValue label="Legal hold state" value={access?.legal_hold_state} />
          <SourceValue label="Legal hold ID" value={access?.legal_hold_id} />
          <SourceValue
            label="Legal hold expires at"
            temporal
            value={access?.legal_hold_expires_at}
          />
        </dl>
      </section>

      {(matchResult || humanDecision) ? (
        <div className={styles.grid2} data-testid="evidence-match-comparison">
          {matchResult ? (
            <section className={styles.evidenceSection}>
              <div className={styles.sectionLabel}>系統比對建議 MATCH RECOMMENDATION</div>
              <dl className={styles.receiptList}>
                <div className={styles.receiptValue}>
                  <dt>Outcome</dt>
                  <dd>
                    <span className={styles.chip} data-tone={matchTone(matchResult.outcome)}>
                      {matchLabel(matchResult.outcome)} · {matchResult.outcome}
                    </span>
                  </dd>
                </div>
                <SourceValue label="Confidence" value={matchResult.confidence} />
                <SourceValue label="Target listing ID" value={matchResult.targetListingId} />
                <SourceValue label="Summary" value={matchResult.summary} />
              </dl>
            </section>
          ) : null}

          {humanDecision ? (
            <section className={styles.evidenceSection} data-testid="evidence-human-decision">
              <div className={styles.sectionLabel}>人工決策 HUMAN DECISION</div>
              <dl className={styles.receiptList}>
                <SourceValue label="Decision ID" value={humanDecision.decision_id} />
                <SourceValue label="Decision type" value={humanDecision.decision_type} />
                <SourceValue label="Status" value={humanDecision.status} />
                <SourceValue label="Actor" value={humanDecision.actor_name} />
                <SourceValue label="Actor role" value={humanDecision.actor_role_id} />
                <SourceValue label="Reviewer" value={humanDecision.reviewer_subject_id} />
                <SourceValue label="Reason" value={humanDecision.reason} />
                <SourceValue label="Occurred at" temporal value={humanDecision.occurred_at} />
                <SourceValue label="Audit event" value={humanDecision.audit_event_id} />
                <SourceValue label="Correlation ID" value={humanDecision.correlation_id} />
              </dl>
            </section>
          ) : null}
        </div>
      ) : null}

      <section className={styles.evidenceSection} data-testid="evidence-fields-table">
        <div className={styles.sectionLabel}>解析欄位與遮罩 FIELD LINEAGE</div>
        {fieldList.length === 0 ? (
          <p className={styles.emptyState}>API 未回傳欄位證據。</p>
        ) : (
          <div className={styles.tableScroll}>
            <table className={styles.evidenceTable}>
              <thead>
                <tr>
                  <th scope="col">Field path</th>
                  <th scope="col">Parsed</th>
                  <th scope="col">Normalized</th>
                  <th scope="col">Corrected</th>
                  <th scope="col">Effective</th>
                  <th scope="col">Confidence</th>
                  <th scope="col">Classification / masking</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {fieldList.map((field) => {
                  const accessMasked = access?.masked === true;
                  const masked = Boolean(maskedView || accessMasked || field.masked);
                  const maskReasonCode = accessMasked
                    ? access.mask_reason_code
                    : field.mask_reason_code;
                  const confidence = typeof field.confidence === "number"
                    ? `${Math.round(field.confidence * 100)}%`
                    : field.low_confidence
                      ? "低信心"
                      : undefined;
                  const maskedValue = "•••••••• (Masked)";
                  return (
                    <tr key={field.field_path}>
                      <th scope="row">{field.field_path}</th>
                      <td>{masked ? maskedValue : displayFieldValue(field.parsed)}</td>
                      <td>{masked ? maskedValue : displayFieldValue(field.normalized)}</td>
                      <td>{masked ? maskedValue : displayFieldValue(field.corrected)}</td>
                      <td>{masked ? maskedValue : displayFieldValue(field.effective)}</td>
                      <td>{confidence ?? "—"}</td>
                      <td>
                        {field.classification ? <span>{field.classification}</span> : null}
                        {masked ? (
                          <span data-testid={`field-mask-${field.field_path}`}>
                            {field.classification ? " · " : ""}
                            MASKED
                            {maskReasonCode ? ` · ${maskReasonCode}` : ""}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        {onOpenFix ? (
                          <button
                            type="button"
                            onClick={() => onOpenFix(field.field_path)}
                            className={styles.secondaryButton}
                            data-testid={`fix-field-${field.field_path}`}
                          >
                            修正
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {verification ? (
        <section className={styles.evidenceSection} data-testid="evidence-verification">
          <div className={styles.sectionLabel}>證據驗證 VERIFICATION</div>
          <dl className={styles.receiptList}>
            <SourceValue
              label="Status"
              testId="evidence-verification-status"
              value={verification.status}
            />
            <SourceValue label="Verified at" temporal value={verification.verified_at} />
            <SourceValue label="Checksum algorithm" value={verification.checksum_algorithm} />
            <SourceValue
              label="Content checksum"
              testId="evidence-verification-checksum"
              value={verification.content_sha256}
            />
            <SourceValue label="Signature" value={verification.signature} />
            <SourceValue label="Signer key version" value={verification.signer_key_version} />
            <SourceValue label="WORM sink receipt" value={verification.worm_sink_id} />
            <SourceValue label="WORM checksum" value={verification.worm_checksum} />
            <SourceValue label="Evidence state" value={verification.evidence_state} />
            <SourceValue label="Audit event" value={verification.audit_event_id} />
            <SourceValue label="Correlation ID" value={verification.correlation_id} />
          </dl>
        </section>
      ) : (
        <UnavailableEvidenceSection
          label="證據驗證 VERIFICATION"
          testId="evidence-verification-unavailable"
        />
      )}

      {exportReceipt ? (
        <section className={styles.evidenceSection} data-testid="evidence-export-result">
          <div className={styles.sectionLabel}>匯出結果 EXPORT RESULT</div>
          <dl className={styles.receiptList}>
            <SourceValue label="Manifest ID" value={exportReceipt.export_manifest_id} />
            <SourceValue label="Requested by" value={exportReceipt.requested_by} />
            <SourceValue label="Approved by" value={exportReceipt.approved_by} />
            <SourceValue label="Purpose" value={exportReceipt.purpose} />
            <SourceValue label="Scope" value={JSON.stringify(exportReceipt.scope)} />
            <SourceValue label="Field mask" value={JSON.stringify(exportReceipt.field_mask)} />
            <SourceValue label="Object URI" value={exportReceipt.object_uri} />
            <SourceValue label="Content SHA-256" value={exportReceipt.content_sha256} />
            <SourceValue label="Watermark" value={exportReceipt.watermark} />
            <SourceValue label="Created at" temporal value={exportReceipt.created_at} />
            <SourceValue label="Expires at" temporal value={exportReceipt.expires_at} />
            <SourceValue label="Download evidence ID" value={exportReceipt.download_evidence_id} />
            <SourceValue label="Signer key version" value={exportReceipt.signer_key_version} />
            <SourceValue label="WORM sink receipt" value={exportReceipt.worm_sink_id} />
            <SourceValue label="WORM checksum" value={exportReceipt.worm_checksum} />
          </dl>
        </section>
      ) : (
        <UnavailableEvidenceSection
          label="匯出結果 EXPORT RESULT"
          testId="evidence-export-unavailable"
        />
      )}

      {auditReferences.length > 0 ? (
        <section
          className={styles.evidenceSection}
          data-testid="evidence-audit-references"
        >
          <div className={styles.sectionLabel}>稽核引用 AUDIT REFERENCES</div>
          <ul className={styles.auditReferenceList}>
            {auditReferences.map((audit) => (
              <li key={audit.audit_event_id}>
                <code>{audit.audit_event_id}</code>
                <span>{audit.action}</span>
                <span>{audit.result}</span>
                <EvidenceTime value={audit.occurred_at} />
                {audit.reason_code ? <code>{audit.reason_code}</code> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}

function UnavailableEvidenceSection({
  label,
  testId,
}: {
  label: string;
  testId: string;
}) {
  return (
    <section className={styles.evidenceSection} data-testid={testId}>
      <div className={styles.sectionLabel}>{label}</div>
      <p className={styles.emptyState}>
        API 未回傳 authoritative receipt；本頁不推算狀態或識別碼。
      </p>
    </section>
  );
}

function EvidenceTime({ value }: { value: string }) {
  const formatted = formatIntakeDateTime(value);
  return formatted ? (
    <time dateTime={value} title={formatted.title}>
      {formatted.text}
    </time>
  ) : (
    <span>API 未回傳有效時間</span>
  );
}
