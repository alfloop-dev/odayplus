"use client";

import type { AssistedIntake, AuditReference, CanonicalIntakeDetail, CanonicalIntakeEvidencePage, FieldValue } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { policyLabel, policyTone } from "./intakeTypes";
import { readCanonicalIntakeEvidence, type CanonicalIntakeReadContext } from "./canonicalIntakeReadClient";
import { CanonicalIntakeFailure } from "./CanonicalIdentityDecisionPanel";
import { useCanonicalIntakeReadPage, type CanonicalIntakePageLoader } from "./useCanonicalIntakeReadPage";

type AssistedEvidencePanelProps = {
  record: AssistedIntake;
  fields?: FieldValue[];
  auditReferences?: AuditReference[];
  onOpenFix?: (fieldKey: string) => void;
  maskedView?: boolean;
  testId?: string;
  section?: "source" | "lineage";
};
type NativeEvidencePanelProps = { canonical: CanonicalIntakeDetail; context: CanonicalIntakeReadContext };
export type EvidencePanelProps = AssistedEvidencePanelProps | NativeEvidencePanelProps;

export function EvidencePanel(props: EvidencePanelProps) {
  return "canonical" in props ? <NativeEvidencePanel {...props} /> : <AssistedEvidencePanel {...props} />;
}

const loadEvidencePage: CanonicalIntakePageLoader<CanonicalIntakeEvidencePage> = (context, parent, cursor, signal, initial) =>
  readCanonicalIntakeEvidence(context, parent, cursor, initial.through_at, signal);

function NativeEvidencePanel({ canonical, context }: NativeEvidencePanelProps) {
  const { page, busy, error, selectCursor } = useCanonicalIntakeReadPage(context, canonical,
    canonical.processing_evidence ?? null, loadEvidencePage);
  const source = canonical.source_info;
  const sourceRows = source ? [
    ...source.url_fields.map((field) => [field.field_path === "originalUrl" ? "原始 URL" : "Canonical URL",
      field.masked ? "無法檢視" : field.effective] as const),
    ["來源識別碼", source.source_id], ["來源政策（記錄值）", source.recorded_policy_state],
    ["最後轉移時間", source.last_transition_at], ["完成時間（已記錄）", source.completed_at],
  ] : [];
  return <section className={styles.sectionBox} data-testid="native-intake-evidence-panel">
    <div className={styles.sectionHead}>來源資訊 SOURCE</div>
    {source ? <div className={styles.metaGrid}>{sourceRows.map(([label, value]) => <div key={label}>
      <span className={styles.metaCaption}>{label}</span><div className={styles.metaValue}>{available(value)}</div>
    </div>)}</div> : <p>尚未取得可讀取的來源資訊。</p>}
    <h4>快照與解析紀錄</h4>
    {busy ? <p role="status">正在讀取快照與解析紀錄…</p>
      : error ? <CanonicalIntakeFailure error={error} />
      : page ? <>
        <p>讀取於 {page.observed_at} · 收錄時間上界 {page.through_at} · 收件版本 {page.intake_version}</p>
        {page.items.length ? page.items.map((item) => {
          const m = item.metadata;
          const rows = item.kind === "SNAPSHOT" ? [
            ["來源識別碼", item.metadata.source_id], ["已記錄 SHA-256", item.metadata.content_sha256],
            ["內容類型", item.metadata.media_type], ["位元組數", item.metadata.byte_length],
            ["擷取時間", item.metadata.captured_at], ["觀察時間", item.metadata.observed_at],
            ["擷取方式", item.metadata.capture_method], ["紀錄版本", item.metadata.version],
            ["保留類別", item.metadata.retention_class], ["已記錄清除期限", item.metadata.purge_after],
            ["Legal hold（記錄值）", item.metadata.legal_hold], ["已記錄物件版本", item.metadata.object_generation],
            ["資料落地設定", item.metadata.residency_mode],
          ] : [
            ["已記錄狀態", item.metadata.status], ["來源快照", item.metadata.source_snapshot_id],
            ["解析發布識別碼", item.metadata.parser_release_id], ["解析版本", item.metadata.semantic_version],
            ["解析套件", item.metadata.package_name], ["輸出格式版本", item.metadata.output_schema_version],
            ["紀錄版本", item.metadata.version], ["開始時間", item.metadata.started_at],
            ["結束時間", item.metadata.completed_at], ["查詢編號 Correlation ID", item.metadata.correlation_id],
          ];
          const reference = item.kind === "SNAPSHOT" ? item.metadata.source_snapshot_id : item.metadata.parser_run_id;
          return <article key={item.kind + ":" + reference} className={styles.noteBox}>
            <h4>{item.kind === "SNAPSHOT" ? "來源快照" : "解析紀錄"} <span className={styles.summaryValue}>{reference}</span></h4>
            <p>保存時間 {item.recorded_at} · 來源 {m.source_id}</p>
            <div className={styles.metaGrid}>{rows.map(([label, value]) => <div key={String(label)}>
              <span className={styles.metaCaption}>{label}</span><div className={styles.metaValue}>{available(value)}</div>
            </div>)}</div>
          </article>;
        }) : <p>目前沒有已保存的快照或解析紀錄。</p>}
        <div className={styles.timelineActions}>
          <button className={styles.secondaryButton} disabled={!page.previous_cursor} type="button"
            onClick={() => selectCursor(page.previous_cursor)}>較早的快照與解析紀錄</button>
          <button className={styles.secondaryButton} disabled={!page.next_cursor} type="button"
            onClick={() => selectCursor(page.next_cursor)}>較後的快照與解析紀錄</button>
        </div>
      </> : <p>尚未取得快照與解析紀錄，請重新整理詳情。</p>}
  </section>;
}

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

function AssistedEvidencePanel({
  record,
  fields,
  onOpenFix,
  maskedView = false,
  testId = "intake-evidence-panel",
  section = "source",
}: AssistedEvidencePanelProps) {
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
                      <th className={styles.fieldCell} data-label="欄位" scope="row">
                        <div className={styles.fieldCellStack}>
                          <strong>{row.label}</strong>
                          <span className={styles.metaSub}>{row.key} · classification {row.classification}</span>
                        </div>
                      </th>
                      <td className={styles.fieldCell} data-label="Parsed">
                        <div className={styles.fieldCellStack}>{masked ? "•••• [MASKED]" : available(row.parsed)}</div>
                      </td>
                      <td className={styles.fieldCell} data-label="Normalized">
                        <div className={styles.fieldCellStack}>{masked ? "•••• [MASKED]" : available(row.normalized)}</div>
                      </td>
                      <td className={styles.fieldCell} data-label="Corrected / State">
                        <div className={styles.fieldCellStack}>
                          {masked ? "•••• [MASKED]" : correctedAvailable ? String(row.corrected) : row.missing ? "? [MISSING]" : "未人工修正"}
                          {!masked && row.correctionReason ? <span className={styles.metaSub}>原因：{row.correctionReason}</span> : null}
                          {row.lowConfidence ? <span className={styles.lowChip}>⚠ [LOW_CONFIDENCE]</span> : null}
                          {onOpenFix ? (
                            <button className={styles.fixButton} data-testid={`fix-field-${row.key}`} onClick={() => onOpenFix(row.key)} type="button">
                              修正 {row.label}
                            </button>
                          ) : null}
                        </div>
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
