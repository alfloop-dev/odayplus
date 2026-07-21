"use client";

import { useMemo } from "react";
import type { AssistedIntake, MatchOutcome } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { matchLabel, matchTone } from "./intakeTypes";

export type TargetListingData = {
  id?: string;
  sourceId?: string;
  canonicalUrl?: string;
  address?: string;
  area?: string | number;
  floor?: string | number;
  listingType?: string;
  rent?: string | number;
  status?: string;
};

export type ListingCompareRow = {
  key: string;
  label: string;
  targetValue: string;
  submissionValue: string;
  changed: boolean;
  contradiction: boolean;
  detail: string;
};

export function ListingCompareTable({
  record,
  targetListing,
  className,
}: {
  record: AssistedIntake;
  targetListing?: TargetListingData | null;
  className?: string;
}) {
  const match = record.matchResult;
  const outcome: MatchOutcome | undefined = match?.outcome;
  const targetId = targetListing?.id || match?.targetListingId || "";

  // Extract parsed fields for easy lookup
  const parsedMap = useMemo(() => {
    const map = new Map<string, string>();
    if (record.parsedFields) {
      for (const field of Object.values(record.parsedFields)) {
        map.set(field.key, field.correctedValue || field.normalizedValue || field.sourceValue || "—");
      }
    }
    return map;
  }, [record.parsedFields]);

  // Construct standard compare rows as per task brief:
  // source ID, canonical URL, address, area, floor, listing type, rent/price, status, confidence, contradictions
  const compareRows = useMemo<ListingCompareRow[]>(() => {
    const agreeingKeys = new Set((match?.agreeingSignals ?? []).map((s) => s.key));
    const contradictingMap = new Map((match?.contradictingSignals ?? []).map((s) => [s.key, s.detail]));

    const fields: Array<{
      key: string;
      label: string;
      targetVal: string | number | undefined;
      subVal: string | number | undefined;
    }> = [
      {
        key: "sourceId",
        label: "來源 ID (Source ID)",
        targetVal: targetListing?.sourceId ?? (targetId ? `SRC-${targetId.slice(-6)}` : undefined),
        subVal: record.sourceId,
      },
      {
        key: "canonicalUrl",
        label: "規範網址 (Canonical URL)",
        targetVal: targetListing?.canonicalUrl,
        subVal: record.canonicalUrl,
      },
      {
        key: "address",
        label: "地址 (Address)",
        targetVal: targetListing?.address,
        subVal: parsedMap.get("address") ?? parsedMap.get("normalized_address"),
      },
      {
        key: "area",
        label: "坪數/面積 (Area)",
        targetVal: targetListing?.area,
        subVal: parsedMap.get("area") ?? parsedMap.get("ping"),
      },
      {
        key: "floor",
        label: "樓層 (Floor)",
        targetVal: targetListing?.floor,
        subVal: parsedMap.get("floor"),
      },
      {
        key: "listingType",
        label: "物件類型 (Listing Type)",
        targetVal: targetListing?.listingType,
        subVal: parsedMap.get("listingType") ?? parsedMap.get("listing_type"),
      },
      {
        key: "rent",
        label: "租金/價格 (Rent/Price)",
        targetVal: targetListing?.rent,
        subVal: parsedMap.get("rent") ?? parsedMap.get("price"),
      },
      {
        key: "status",
        label: "處理狀態 (Status)",
        targetVal: targetListing?.status ?? (targetId ? "ACTIVE" : undefined),
        subVal: record.stage,
      },
    ];

    return fields.map((field) => {
      const targetStr = field.targetVal !== undefined && field.targetVal !== null ? String(field.targetVal) : "—";
      const subStr = field.subVal !== undefined && field.subVal !== null ? String(field.subVal) : "—";
      const isContradiction = contradictingMap.has(field.key);
      const isAgreeing = agreeingKeys.has(field.key);
      const isChanged = targetStr !== subStr || isContradiction;
      let detail = "—";

      if (isContradiction) {
        detail = contradictingMap.get(field.key) || "資料不符，存在矛盾訊號";
      } else if (isAgreeing) {
        detail = "資訊一致";
      } else if (isChanged) {
        detail = "數值變更";
      } else {
        detail = "數值相同";
      }

      return {
        key: field.key,
        label: field.label,
        targetValue: targetStr,
        submissionValue: subStr,
        changed: isChanged,
        contradiction: isContradiction,
        detail,
      };
    });
  }, [record, targetListing, targetId, match, parsedMap]);

  // Screen-reader change summary narrative
  const changeSummaryText = useMemo(() => {
    if (!match) {
      return `收件 ${record.id} 尚無比對結果。`;
    }
    const targetText = targetId ? `目標物件 ${targetId}` : "尚無對應目標物件";
    const outcomeText = outcome ? matchLabel(outcome) : "未知";
    const agreeCount = match.agreeingSignals?.length ?? 0;
    const conCount = match.contradictingSignals?.length ?? 0;
    const conList = (match.contradictingSignals ?? []).map((s) => s.label).join("、");
    const conText = conCount > 0 ? `，主要矛盾欄位包含：${conList}` : "，無重大矛盾。";

    return (
      `比對結果為 ${outcomeText} (${outcome || "N/A"})，對象為 ${targetText}。` +
      `信心得分 ${(match.confidence * 100).toFixed(0)}%。` +
      `包含 ${agreeCount} 項一致訊號，${conCount} 項矛盾訊號${conText}`
    );
  }, [record.id, targetId, outcome, match]);

  return (
    <div
      aria-label="物件差異比對表格"
      className={`${styles.sectionBox} ${className || ""}`}
      data-testid="listing-compare-table"
    >
      <div className={styles.sectionHead}>
        <span>物件欄位並列比對 LISTING COMPARE TABLE</span>
        {outcome ? (
          <span className={styles.chip} data-testid="compare-outcome-badge" data-tone={matchTone(outcome)}>
            {outcome} · {matchLabel(outcome)}
          </span>
        ) : null}
        {targetId ? <span className={styles.metaSub}>目標對象：{targetId}</span> : null}
      </div>

      {/* Screen-reader-readable change summary */}
      <div className={styles.srSummary} data-testid="intake-change-summary" role="region" aria-live="polite">
        <strong>變更摘要 (Screen-Reader Summary)：</strong> {changeSummaryText}
      </div>

      <div className={styles.desktopOnlyNote} data-testid="intake-desktop-required">
        並列比對與詳細差異分析需要較寬畫面 — 在行動裝置下請至桌面端檢視 complete side-by-side comparison。
      </div>

      <div className={styles.compareGrid} data-testid="compare-table-grid">
        <div className={styles.fieldsHeadCell}>比對欄位</div>
        <div className={styles.fieldsHeadCell}>既有目標物件 ({targetId || "無"})</div>
        <div className={styles.fieldsHeadCell}>本次收件內容 ({record.id})</div>
        <div className={styles.fieldsHeadCell}>比對判定與說明</div>

        {compareRows.map((row) => (
          <div
            className={row.contradiction ? styles.compareRowChanged : undefined}
            data-testid={`compare-row-${row.key}`}
            key={row.key}
            style={{ display: "contents" }}
          >
            <div className={styles.fieldCell} data-label="欄位">
              <span className={styles.fieldLabelText}>{row.label}</span>
            </div>
            <div className={`${styles.fieldCell} ${styles.sourceValue}`} data-label="既有目標">
              <span>{row.targetValue}</span>
            </div>
            <div className={styles.fieldCell} data-label="本次收件">
              <span className={row.changed ? styles.correctedValue : undefined}>{row.submissionValue}</span>
            </div>
            <div className={styles.fieldCell} data-label="判定說明">
              {row.contradiction ? (
                <span className={styles.changeChip} data-testid={`signal-con-${row.key}`}>
                  ▲ 矛盾 (Contradiction)
                </span>
              ) : row.changed ? (
                <span className={styles.chip} data-tone="watch" data-testid={`signal-changed-${row.key}`}>
                  ~ 變動 (Changed)
                </span>
              ) : (
                <span className={styles.chip} data-tone="good" data-testid={`signal-match-${row.key}`}>
                  ✓ 一致 (Matched)
                </span>
              )}
              <span className={styles.metaSub} style={{ marginLeft: "6px" }}>
                {row.detail}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Summary metrics bar */}
      <div className={styles.metaGrid} style={{ marginTop: "12px", paddingTop: "8px", borderTop: "1px solid #eef1f6" }}>
        <div>
          <span className={styles.metaCaption}>比對信心度 Confidence</span>
          <div className={styles.metaValue} data-testid="compare-confidence-val">
            {match ? `${(match.confidence * 100).toFixed(1)}% (${match.confidence.toFixed(2)})` : "—"}
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>一致訊號 Agreeing</span>
          <div className={styles.metaValue} data-testid="compare-agree-count">
            {match?.agreeingSignals?.length ?? 0} 項
          </div>
        </div>
        <div>
          <span className={styles.metaCaption}>矛盾訊號 Contradicting</span>
          <div className={styles.metaValue} data-testid="compare-con-count">
            {match?.contradictingSignals?.length ?? 0} 項
          </div>
        </div>
      </div>
    </div>
  );
}
