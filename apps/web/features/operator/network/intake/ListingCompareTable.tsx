"use client";

import { useMemo } from "react";
import type { AssistedIntake, MatchOutcome } from "@oday-plus/openapi-client";
import styles from "./intake.module.css";
import { matchLabel, matchTone } from "./intakeTypes";

export type TargetListingData = {
  id?: string;
  sourceId?: string;
  sourceListingId?: string;
  sourceUrl?: string;
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
  unavailable: boolean;
  detail: string;
};

const SIGNAL_KEY_ALIASES: Readonly<Record<string, string>> = {
  sourceId: "sourceId",
  source_id: "sourceId",
  sourceUrl: "sourceUrl",
  source_url: "sourceUrl",
  canonicalUrl: "canonicalUrl",
  canonical_url: "canonicalUrl",
  providerListingId: "sourceListingId",
  provider_listing_id: "sourceListingId",
  sourceListingId: "sourceListingId",
  source_listing_id: "sourceListingId",
  normalizedAddress: "address",
  normalized_address: "address",
  address: "address",
  address_raw: "address",
  areaPing: "area",
  area_ping: "area",
  area: "area",
  ping: "area",
  floor: "floor",
  floor_number: "floor",
  listingType: "listingType",
  listing_type: "listingType",
  propertyType: "listingType",
  property_type: "listingType",
  rent: "rent",
  rent_amount: "rent",
  rentPerMonth: "rent",
  rent_per_month: "rent",
  asking_price: "rent",
  price: "rent",
  status: "status",
  listing_status: "status",
};

export function normalizeCompareSignalKey(key: string): string {
  return SIGNAL_KEY_ALIASES[key] ?? key;
}

export function ListingCompareTable({
  record,
  targetListing,
  className,
  detailHref,
}: {
  record: AssistedIntake;
  targetListing?: TargetListingData | null;
  className?: string;
  detailHref?: string;
}) {
  const match = record.matchResult;
  const outcome: MatchOutcome | undefined = match?.outcome;
  const targetId = targetListing?.id || match?.targetListingId || "";

  // Extract parsed fields for easy lookup
  const parsedMap = useMemo(() => {
    const map = new Map<string, string>();
    if (record.parsedFields) {
      for (const field of Object.values(record.parsedFields)) {
        const val = field.correctedValue ?? field.normalizedValue ?? field.sourceValue;
        map.set(field.key, val !== undefined && val !== null && val !== "" ? String(val) : "—");
      }
    }
    return map;
  }, [record.parsedFields]);

  // Construct standard compare rows as per task brief:
  // source ID, canonical URL, address, area, floor, listing type, rent/price, status, confidence, contradictions
  const compareRows = useMemo<ListingCompareRow[]>(() => {
    const agreeingKeys = new Set(
      (match?.agreeingSignals ?? []).map((signal) => normalizeCompareSignalKey(signal.key)),
    );
    const contradictingMap = new Map(
      (match?.contradictingSignals ?? []).map((signal) => [
        normalizeCompareSignalKey(signal.key),
        signal.detail,
      ]),
    );
    const parsedValue = (...keys: string[]) => keys.map((key) => parsedMap.get(key)).find((value) => value !== undefined);

    const fields: Array<{
      key: string;
      label: string;
      targetVal: string | number | undefined;
      subVal: string | number | undefined;
    }> = [
      {
        key: "sourceId",
        label: "來源 ID (Source ID)",
        targetVal: targetListing?.sourceId,
        subVal: record.sourceId,
      },
      {
        key: "sourceUrl",
        label: "來源網址 (Source URL)",
        targetVal: targetListing?.sourceUrl,
        subVal: record.originalUrl,
      },
      {
        key: "sourceListingId",
        label: "提供者物件 ID (Provider Listing ID)",
        targetVal: targetListing?.sourceListingId,
        subVal: parsedValue("providerListingId", "provider_listing_id", "sourceListingId", "source_listing_id"),
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
        subVal: parsedValue("address", "normalized_address", "address_raw"),
      },
      {
        key: "area",
        label: "坪數/面積 (Area)",
        targetVal: targetListing?.area,
        subVal: parsedValue("area", "area_ping", "ping"),
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
        subVal: parsedValue("listingType", "listing_type", "property_type"),
      },
      {
        key: "rent",
        label: "租金/價格 (Rent/Price)",
        targetVal: targetListing?.rent,
        subVal: parsedValue("rent", "rent_amount", "asking_price", "price"),
      },
      {
        key: "status",
        label: "物件狀態 (Listing Status)",
        targetVal: targetListing?.status,
        subVal: parsedValue("status", "listing_status"),
      },
    ];

    return fields.map((field) => {
      const targetStr = field.targetVal !== undefined && field.targetVal !== null ? String(field.targetVal) : "—";
      const subStr = field.subVal !== undefined && field.subVal !== null ? String(field.subVal) : "—";
      const isContradiction = contradictingMap.has(field.key);
      const isAgreeing = agreeingKeys.has(field.key);
      const valuesComparable = targetStr !== "—" && subStr !== "—";
      const unavailable = !valuesComparable;
      const isChanged = (valuesComparable && targetStr !== subStr) || isContradiction;
      let detail = "—";

      if (isContradiction) {
        detail = contradictingMap.get(field.key) || "資料不符，存在矛盾訊號";
      } else if (unavailable) {
        detail = "資料不可用，無法判定一致";
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
        unavailable,
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
      aria-label="既有物件與本次送件差異比對"
      className={`${styles.sectionBox} ${className || ""}`}
      data-testid="listing-compare-table"
    >
      <div className={styles.sectionHead}>
        <span>比對結果 MATCH REVIEW</span>
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

      {outcome === "POSSIBLE_MATCH" ? <div className={styles.desktopOnlyNote} data-testid="intake-desktop-required">
        <strong>DESKTOP_REQUIRED — POSSIBLE_MATCH 完整比對需在桌面完成</strong>
        <span>
          行動版可查看狀態與簡單確認；side-by-side 欄位比對與識別決策請改用桌面開啟。Deep link
          已保留：<a href={detailHref ?? `/intake/${record.id}`}>{detailHref ?? `/intake/${record.id}`}</a>，你的輸入不會遺失。
        </span>
        <span data-testid="intake-mobile-preserved-values">
          已保留輸入：
          {compareRows
            .filter((row) => row.submissionValue !== "—")
            .map((row) => `${row.label}=${row.submissionValue}`)
            .join("；") || "UNAVAILABLE"}
        </span>
      </div> : null}

      <table
        aria-label="Current and submitted values"
        className={styles.compareGrid}
        data-testid="compare-table-grid"
      >
        <thead>
          <tr>
            <th className={styles.fieldsHeadCell} scope="col">欄位</th>
            <th className={styles.fieldsHeadCell} scope="col">既有物件 ({targetId || "UNAVAILABLE"})</th>
            <th className={styles.fieldsHeadCell} scope="col">本次送件 ({record.id})</th>
          </tr>
        </thead>
        <tbody>
          {compareRows.map((row) => (
            <tr data-testid={`compare-row-${row.key}`} key={row.key}>
            <th
              className={`${styles.fieldCell} ${row.changed ? styles.compareCellChanged : ""}`}
              data-label="欄位"
              scope="row"
            >
              <div className={styles.fieldCellStack}>
                <span className={styles.fieldLabelText}>{row.label}</span>
                {row.contradiction ? (
                  <span className={styles.changeChip} data-testid={`signal-con-${row.key}`}>
                    ▲ 矛盾 (Contradiction)
                  </span>
                ) : row.unavailable ? (
                  <span className={styles.unavailableChip} data-testid={`signal-unavailable-${row.key}`}>
                    ? 不可用 [UNAVAILABLE]
                  </span>
                ) : row.changed ? (
                  <span className={styles.changeChip} data-testid={`signal-changed-${row.key}`}>
                    ▲ 變更 (Changed)
                  </span>
                ) : (
                  <span className={styles.chip} data-tone="good" data-testid={`signal-match-${row.key}`}>
                    ✓ 一致 (Matched)
                  </span>
                )}
                <span className={styles.metaSub}>{row.detail}</span>
              </div>
            </th>
            <td
              className={`${styles.fieldCell} ${styles.sourceValue} ${row.changed ? styles.compareCellChanged : ""}`}
              data-label="既有物件"
            >
              <div className={styles.fieldCellStack}>
                <span>{row.targetValue}</span>
              </div>
            </td>
            <td
              className={`${styles.fieldCell} ${row.changed ? styles.compareCellChanged : ""}`}
              data-label="本次送件"
            >
              <div className={styles.fieldCellStack}>
                <span className={row.changed ? styles.correctedValue : undefined}>{row.submissionValue}</span>
              </div>
            </td>
          </tr>
          ))}
        </tbody>
      </table>

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
