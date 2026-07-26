"use client";

import type { AssistedIntake, AuditReference, FieldValue } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { policyLabel, policyTone } from "./intakeTypes";

export type EvidencePanelProps = {
  record: AssistedIntake;
  fields?: FieldValue[];
  auditReferences?: AuditReference[];
  onOpenFix?: (fieldKey: string) => void;
  maskedView?: boolean;
  testId?: string;
  section?: "source" | "lineage";
};

type LineageRow = {
  key: string;
  label: string;
  parsed: unknown;
  normalized: unknown;
  corrected: unknown;
  correctionReason: string | null;
  lowConfidence: boolean;
  missing: boolean;
  masked: boolean;
  classification: string;
  group: "identity" | "location" | "commercial" | "property" | "provenance";
};

const UNAVAILABLE = "UNAVAILABLE";

export function EvidencePanel({
  record,
  fields,
  onOpenFix,
  maskedView = false,
  testId = "intake-evidence-panel",
  section = "source",
}: EvidencePanelProps) {
  const lineageRows = buildLineageRows(record, fields);
  const raw = record as AssistedIntake & Record<string, unknown>;

  if (section === "source") {
    const evidenceRows = [
      ["原始 URL", record.originalUrl],
      ["Canonical URL", record.canonicalUrl],
      ["Captured At", record.capturedAt],
      ["Observed At", raw.observedAt ?? raw.observed_at],
      ["Snapshot ID", record.snapshotId],
      ["Snapshot Hash", raw.snapshotHash ?? raw.snapshot_hash ?? raw.snapshotSha256],
      ["Parser Version", record.parserVersion],
      ["Correlation ID", record.correlationId],
      ["Policy Reason", record.policyReason],
      ["Policy Version", raw.policyVersion ?? raw.policy_version],
      ["Policy Expiry", raw.policyExpiresAt ?? raw.policy_expires_at],
      ["WORM State", raw.wormState ?? raw.worm_state],
      ["Purpose Binding", raw.purposeBinding ?? raw.purpose_binding],
      ["Retention / Legal Hold", raw.retentionState ?? raw.retention_state ?? raw.legalHold],
      ["Masking / Export", raw.maskingState ?? raw.masking_state ?? raw.exportState],
      ["Evidence Receipt", raw.evidenceReceiptId ?? raw.evidence_receipt_id],
    ] as const;

    return (
      <section className={styles.sectionBox} data-testid={testId}>
        <div className={styles.sectionHead}>來源政策與證據 SOURCE POLICY & EVIDENCE</div>
        <div className={styles.metaGrid}>
          <div>
            <span className={styles.metaCaption}>Policy Decision</span>
            <div className={styles.metaValue}>
              <span className={styles.chip} data-tone={policyTone(record.policy)}>{policyLabel(record.policy)}</span>
            </div>
          </div>
          {evidenceRows.map(([label, value]) => (
            <div key={label} data-testid={`evidence-${slug(label)}`}>
              <span className={styles.metaCaption}>{label}</span>
              <div className={styles.metaValue}>{available(value)}</div>
            </div>
          ))}
        </div>
      </section>
    );
  }

  const groups = ["identity", "location", "commercial", "property", "provenance"] as const;
  return (
    <section className={styles.sectionBox} data-testid={`${testId}-lineage`}>
      <div className={styles.sectionHead}>解析、正規化與修正值 PARSED DATA LINEAGE</div>
      {groups.map((group) => {
        const rows = lineageRows.filter((row) => row.group === group);
        if (!rows.length) return null;
        return (
          <div data-testid={`lineage-group-${group}`} key={group}>
            <h4 className={styles.lineageGroupTitle}>{group.toUpperCase()}</h4>
            <table className={styles.lineageGrid} aria-label={`${group} field lineage`}>
              <thead>
                <tr>
                  <th className={styles.fieldsHeadCell} scope="col">欄位</th>
                  <th className={styles.fieldsHeadCell} scope="col">Parsed</th>
                  <th className={styles.fieldsHeadCell} scope="col">Normalized</th>
                  <th className={styles.fieldsHeadCell} scope="col">Corrected / State</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const masked = maskedView || row.masked;
                  const correctedAvailable = hasValue(row.corrected);
                  return (
                    <tr data-testid={`lineage-row-${row.key}`} key={row.key}>
                      <th className={styles.fieldCell} scope="row">
                        <strong>{row.label}</strong>
                        <span className={styles.metaSub}>{row.key} · classification {row.classification}</span>
                      </th>
                      <td className={styles.fieldCell}>{masked ? "•••• [MASKED]" : available(row.parsed)}</td>
                      <td className={styles.fieldCell}>{masked ? "•••• [MASKED]" : available(row.normalized)}</td>
                      <td className={styles.fieldCell}>
                        {masked ? "•••• [MASKED]" : correctedAvailable ? String(row.corrected) : row.missing ? "? [MISSING]" : "未人工修正"}
                        {row.correctionReason ? <span className={styles.metaSub}>原因：{row.correctionReason}</span> : null}
                        {row.lowConfidence ? <span className={styles.lowChip}>⚠ [LOW_CONFIDENCE]</span> : null}
                        {onOpenFix ? (
                          <button className={styles.fixButton} data-testid={`fix-field-${row.key}`} onClick={() => onOpenFix(row.key)} type="button">
                            修正 {row.label}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })}
      {!lineageRows.length ? <div className={styles.emptyState}>? [MISSING] API 未提供解析欄位</div> : null}
    </section>
  );
}

function buildLineageRows(record: AssistedIntake, fields?: FieldValue[]): LineageRow[] {
  if (fields) {
    return fields.map((field) => ({
      key: field.field_path,
      label: field.field_path,
      parsed: field.parsed,
      normalized: field.normalized,
      corrected: (field as FieldValue & { corrected?: unknown }).corrected,
      correctionReason: (field as FieldValue & { correction_reason?: string | null }).correction_reason ?? null,
      lowConfidence: field.confidence !== undefined && field.confidence !== null && field.confidence < 0.5,
      missing: !hasValue(field.parsed) && !hasValue(field.normalized),
      masked: field.masked === true,
      classification: field.classification ?? UNAVAILABLE,
      group: groupFor(field.field_path, false),
    }));
  }
  return Object.values(record.parsedFields ?? {}).map((field) => ({
    key: field.key,
    label: field.label,
    parsed: field.sourceValue,
    normalized: field.normalizedValue,
    corrected: field.correctedValue,
    correctionReason: field.correctionReason,
    lowConfidence: field.lowConfidence,
    missing: !hasValue(field.sourceValue) && !hasValue(field.normalizedValue) && !hasValue(field.correctedValue),
    masked: field.masked === true,
    classification: (field as typeof field & { classification?: string }).classification ?? UNAVAILABLE,
    group: groupFor(field.key, field.identity),
  }));
}

function groupFor(key: string, identity: boolean): LineageRow["group"] {
  const normalized = key.toLowerCase();
  if (identity) return "identity";
  if (normalized.includes("address") || normalized.includes("district") || normalized.includes("city")) return "location";
  if (normalized.includes("rent") || normalized.includes("price") || normalized.includes("deposit")) return "commercial";
  if (normalized.includes("area") || normalized.includes("floor") || normalized.includes("type")) return "property";
  return "provenance";
}

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

function available(value: unknown): string {
  return hasValue(value) ? String(value) : UNAVAILABLE;
}

function slug(label: string): string {
  return label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-").replaceAll(/^-|-$/g, "");
}
