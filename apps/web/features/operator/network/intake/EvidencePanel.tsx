"use client";

import type { AssistedIntake, AuditReference, FieldValue } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { matchLabel, matchTone, policyLabel, policyTone } from "./intakeTypes";

export type EvidencePanelProps = {
  record: AssistedIntake;
  fields?: FieldValue[];
  auditReferences?: AuditReference[];
  onOpenFix?: (fieldKey: string) => void;
  maskedView?: boolean;
  testId?: string;
};

export function EvidencePanel({
  record,
  fields,
  auditReferences = [],
  onOpenFix,
  maskedView = false,
  testId = "intake-evidence-panel",
}: EvidencePanelProps) {
  const parsedFieldList: FieldValue[] = fields ?? Object.values(record.parsedFields ?? {}).map((f) => ({
    field_path: f.key,
    parsed: (f as any).raw ?? f.sourceValue,
    normalized: (f as any).value ?? f.normalizedValue,
    confidence: (f as any).confidence ?? (f.lowConfidence ? 0.4 : 0.95),
    classification: (f.key.toLowerCase().includes("owner") || f.key.toLowerCase().includes("contact") ? "CONFIDENTIAL" : "PUBLIC") as FieldValue["classification"],
    masked: false,
  }));

  const urlDiffers = record.originalUrl && record.canonicalUrl && record.originalUrl !== record.canonicalUrl;
  const matchResult = record.matchResult;
  const humanDecision = (record as any).decision;

  return (
    <div className={styles.sectionBox} data-testid={testId} style={{ border: "1px solid #eef1f6", borderRadius: "10px", padding: "14px", background: "#ffffff", marginBottom: "16px" }}>
      <h4 style={{ margin: "0 0 12px 0", fontSize: "13px", fontWeight: 700, color: "#1e293b", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>解析證據與比對對照 EVIDENCE & MATCH PANEL</span>
        <span style={{ fontSize: "11px", fontWeight: 600, color: "#64748b" }}>
          ETag: <code style={{ background: "#f1f5f9", padding: "2px 4px", borderRadius: "4px" }}>W/"v{record.version}-{record.id}"</code>
        </span>
      </h4>

      {/* 1. URL & Ingestion Source Evidence */}
      <div
        data-testid="evidence-source-section"
        style={{
          background: "#f8fafc",
          border: "1px solid #e2e8f0",
          borderRadius: "8px",
          padding: "12px",
          marginBottom: "14px",
        }}
      >
        <div style={{ fontSize: "11px", fontWeight: 700, color: "#334155", marginBottom: "8px" }}>
          🌐 來源網址與 Capture Lineage
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "11px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
            <span style={{ width: "80px", color: "#64748b", fontWeight: 600, flexShrink: 0 }}>原始 URL:</span>
            <span style={{ fontFamily: "monospace", color: "#2563eb", wordBreak: "break-all" }} data-testid="evidence-original-url">
              {record.originalUrl ?? "—"}
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
            <span style={{ width: "80px", color: "#64748b", fontWeight: 600, flexShrink: 0 }}>规范 URL:</span>
            <span
              style={{
                fontFamily: "monospace",
                color: urlDiffers ? "#d97706" : "#2563eb",
                fontWeight: urlDiffers ? 700 : 400,
                wordBreak: "break-all",
              }}
              data-testid="evidence-canonical-url"
            >
              {record.canonicalUrl ?? record.originalUrl ?? "—"}
              {urlDiffers && <span style={{ marginLeft: "6px", fontSize: "10px", color: "#d97706" }}>(已規範化轉址)</span>}
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "8px", marginTop: "6px", paddingTop: "6px", borderTop: "1px solid #e2e8f0" }}>
            <div>
              <span style={{ color: "#64748b" }}>Policy 狀態: </span>
              <span className={styles.chip} data-tone={policyTone(record.policy)}>
                {policyLabel(record.policy)}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b" }}>Snapshot ID: </span>
              <span style={{ fontFamily: "monospace" }}>{record.snapshotId ?? (record as any).sourceSnapshotId ?? record.sourceId ?? "—"}</span>
            </div>
            <div>
              <span style={{ color: "#64748b" }}>Parser Run ID: </span>
              <span style={{ fontFamily: "monospace" }}>{record.parserVersion ?? (record as any).parserRunId ?? "PR-RUN-88412"}</span>
            </div>
          </div>
        </div>
      </div>

      {/* 2. Match Recommendation vs Human Operator Decision */}
      <div
        data-testid="evidence-match-comparison"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
          gap: "12px",
          marginBottom: "14px",
        }}
      >
        {/* Model Match Recommendation */}
        <div style={{ background: "#f0f9ff", border: "1px solid #bae6fd", borderRadius: "8px", padding: "12px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: "#0369a1", marginBottom: "6px" }}>
            🤖 模型推薦 (Model Recommendation)
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "11px" }}>
            <div>
              <span style={{ color: "#64748b" }}>比對結果: </span>
              <span className={styles.chip} data-tone={matchResult ? matchTone(matchResult.outcome) : "neutral"}>
                {matchResult ? matchLabel(matchResult.outcome) : "尚未比對"}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b" }}>相似度得分: </span>
              <span style={{ fontWeight: 700 }}>
                {matchResult ? `${Math.round((matchResult.confidence ?? (matchResult as any).score ?? 0) * 100)}%` : "—"}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b" }}>候選標的物件 ID: </span>
              <span style={{ fontFamily: "monospace" }}>{matchResult?.targetListingId ?? (matchResult as any)?.matchedCandidateId ?? "—"}</span>
            </div>
          </div>
        </div>

        {/* Human Decision Override */}
        <div style={{ background: "#f8fafc", border: "1px solid #cbd5e1", borderRadius: "8px", padding: "12px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
            👤 人工覆核與決策 (Human Operator Decision)
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "11px" }}>
            <div>
              <span style={{ color: "#64748b" }}>決策類型: </span>
              <span style={{ fontWeight: 700, color: humanDecision ? "#15803d" : "#64748b" }}>
                {humanDecision ? humanDecision.kind.toUpperCase() : "未決策 (Pending)"}
              </span>
            </div>
            <div>
              <span style={{ color: "#64748b" }}>覆核人員: </span>
              <span>{humanDecision?.by ?? record.owner ?? "—"}</span>
            </div>
            {humanDecision?.reason && (
              <div>
                <span style={{ color: "#64748b" }}>決策理由: </span>
                <span style={{ fontStyle: "italic" }}>{humanDecision.reason}</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 3. Parsed Fields Table */}
      <div data-testid="evidence-fields-table">
        <div style={{ fontSize: "11px", fontWeight: 700, color: "#334155", marginBottom: "8px" }}>
          📋 解析欄位與 Lineage (Parsed Fields & Confidence)
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "11px" }}>
            <thead>
              <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", textAlign: "left" }}>
                <th style={{ padding: "6px 8px", color: "#475569" }}>欄位 Path</th>
                <th style={{ padding: "6px 8px", color: "#475569" }}>解析值 (Parsed)</th>
                <th style={{ padding: "6px 8px", color: "#475569" }}>規範值 (Effective)</th>
                <th style={{ padding: "6px 8px", color: "#475569" }}>信心度</th>
                <th style={{ padding: "6px 8px", color: "#475569" }}>密級</th>
                <th style={{ padding: "6px 8px", color: "#475569", textAlign: "right" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {parsedFieldList.map((f, idx) => {
                const conf = f.confidence !== undefined && f.confidence !== null ? Math.round(f.confidence * 100) : 100;
                const isMasked = maskedView || f.masked || f.classification === "RESTRICTED";

                return (
                  <tr key={f.field_path ?? idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                    <td style={{ padding: "6px 8px", fontFamily: "monospace", fontWeight: 600 }}>{f.field_path}</td>
                    <td style={{ padding: "6px 8px", color: "#475569" }}>
                      {isMasked ? "•••••••• (Masked)" : String(f.parsed ?? "—")}
                    </td>
                    <td style={{ padding: "6px 8px", color: "#1e293b", fontWeight: 600 }}>
                      {isMasked ? "•••••••• (Masked)" : String(f.normalized ?? f.parsed ?? "—")}
                    </td>
                    <td style={{ padding: "6px 8px" }}>
                      <span
                        style={{
                          padding: "1px 6px",
                          borderRadius: "4px",
                          fontSize: "10px",
                          fontWeight: 700,
                          background: conf >= 80 ? "#dcfce7" : conf >= 50 ? "#fef9c3" : "#fee2e2",
                          color: conf >= 80 ? "#15803d" : conf >= 50 ? "#a16207" : "#b91c1c",
                        }}
                      >
                        {conf}%
                      </span>
                    </td>
                    <td style={{ padding: "6px 8px" }}>
                      <span
                        style={{
                          fontSize: "9.5px",
                          fontWeight: 700,
                          padding: "1px 4px",
                          borderRadius: "3px",
                          background: f.classification === "CONFIDENTIAL" || f.classification === "RESTRICTED" ? "#fed7aa" : "#e2e8f0",
                          color: f.classification === "CONFIDENTIAL" || f.classification === "RESTRICTED" ? "#c2410c" : "#475569",
                        }}
                      >
                        {f.classification}
                      </span>
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "right" }}>
                      {onOpenFix && (
                        <button
                          type="button"
                          onClick={() => onOpenFix(f.field_path)}
                          className={styles.secondaryButton}
                          style={{ padding: "2px 6px", fontSize: "10px" }}
                          data-testid={`fix-field-${f.field_path}`}
                        >
                          修正
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* 4. Audit References */}
      {auditReferences.length > 0 && (
        <div data-testid="evidence-audit-references" style={{ marginTop: "12px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, color: "#334155", marginBottom: "6px" }}>
            🛡️ 稽核事件紀錄 AUDIT EVENT REFERENCES
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "10.5px" }}>
            {auditReferences.map((aud, i) => (
              <div key={aud.audit_event_id ?? i} style={{ display: "flex", justifyContent: "space-between", background: "#f8fafc", padding: "4px 8px", borderRadius: "4px" }}>
                <span><code style={{ fontSize: "10px" }}>{aud.audit_event_id}</code> - {aud.action}</span>
                <span style={{ color: aud.result === "ALLOWED" || aud.result === "SUCCEEDED" ? "#15803d" : "#b91c1c", fontWeight: 600 }}>{aud.result}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
