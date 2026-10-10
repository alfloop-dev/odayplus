"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Listing, ListingSource } from "../types";
import type { ListingRadarRow } from "../networkFindAreasViewModel";
import type { OperatorRoleId } from "../navigation";
import styles from "../networkFindAreas.module.css";
import { AssistedIntakeSection } from "./intake/AssistedIntakeSection";
import { MERGE_DENIED_NOTE, canMergeListing } from "./listingPermissions";

type NetworkListingDetail = Listing & {
  archivedReason?: string;
  convertedAt?: string;
  duplicateOfId?: string;
  firstSeenAt?: string;
  fitScore?: number;
  floor?: string;
  frontageMeters?: number;
  hardRuleSummary?: string;
  mergeReason?: string;
  mergedIntoId?: string;
  sourceEvidence?: string[];
  sourceListingId?: string;
  sourceUrl?: string;
};

export function toTargetListingData(listing: NetworkListingDetail) {
  return {
    id: listing.id,
    sourceId: listing.sourceId,
    sourceListingId: listing.sourceListingId,
    sourceUrl: listing.sourceUrl,
    address: listing.address,
    area: listing.areaPing,
    floor: listing.floor,
    // The API-bound listing contract has no authoritative listing type.
    listingType: undefined,
    rent: listing.rentPerMonth,
    status: listing.status,
  };
}

export function ListingRadarPanel({
  activeRoleId,
  busyListingId,
  intakeDetailOpen = false,
  listings,
  onArchive,
  onConvert,
  onMerge,
  rows,
  selectedHeatZoneId,
  selectedZoneLabel,
  sources,
}: {
  activeRoleId: OperatorRoleId;
  busyListingId?: string | null;
  /**
   * True while the canonical intake detail owns the page. Package 10 puts the
   * detail immediately below the global topbar, so the Network compliance strip
   * — like the Network heading, KPI strip, stepper and tab strip above this
   * panel — must not precede it (ADD-006 §3.1). Source cards and the radar stay
   * below the detail exactly as in the canonical layout.
   */
  intakeDetailOpen?: boolean;
  listings: NetworkListingDetail[];
  onArchive?: (listingId: string) => void;
  onConvert?: (listingId: string) => void;
  onMerge?: (sourceListingId: string, targetListingId: string) => void;
  rows: ListingRadarRow[];
  selectedHeatZoneId?: string;
  selectedZoneLabel?: string;
  sources: ListingSource[];
}) {
  const [filterMode, setFilterMode] = useState<"selected" | "all">("selected");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [selectedListingId, setSelectedListingId] = useState("");
  const listingById = useMemo(() => new Map(listings.map((listing) => [listing.id, listing])), [listings]);
  const visibleRows =
    filterMode === "selected" && selectedHeatZoneId
      ? rows.filter((row) => row.heatZoneId === selectedHeatZoneId)
      : rows;
  const sourceFilteredRows =
    sourceFilter === "all" ? visibleRows : visibleRows.filter((row) => row.sourceId === sourceFilter);
  const selectedRow =
    sourceFilteredRows.find((row) => row.id === selectedListingId) ??
    sourceFilteredRows[0];
  const selectedListing = selectedRow ? listingById.get(selectedRow.id) : undefined;
  // `mergedIntoId` is the durable terminal marker: a merged source keeps
  // isDuplicate/status "duplicate", so only this field distinguishes "can still
  // be merged" from "already merged". A second click would mint a fresh
  // idempotency key, which the server now refuses with 409.
  const detailMergedIntoId = selectedListing?.mergedIntoId;
  // The detail pane's primary action doubles as the merge entry point, so it
  // needs the SAME gates as the row-level button — otherwise an unauthorized
  // role is still offered a merge whose handler silently does nothing.
  // Terminal state outranks permission: an already-merged listing is not a
  // merge the role is missing, so the denial note must not claim it is.
  const detailMergeDenied = Boolean(
    selectedRow?.isDuplicate &&
      !detailMergedIntoId &&
      selectedRow.status !== "archived" &&
      selectedRow.status !== "candidate" &&
      !canMergeListing(activeRoleId),
  );
  const detailAction = selectedRow ? listingAction(selectedRow, selectedListing, activeRoleId) : null;
  const detailActionAvailable =
    detailAction === "convert" ? Boolean(onConvert) :
    detailAction === "merge" ? Boolean(onMerge) :
    detailAction === "archive" ? Boolean(onArchive) : false;
  const visibleListingCount = rows.filter((row) => row.status !== "archived").length;
  const sourceFilterOptions = [
    { id: "all", label: `全部來源 ${visibleListingCount}` },
    ...sources.map((source) => ({
      id: source.id,
      label: `${sourceShortLabel(source.name)} ${rows.filter((row) => row.sourceId === source.id && row.status !== "archived").length}`,
    })),
  ];

  return (
    <div className={styles.tabPanel} data-screen-label="Network 物件雷達" data-testid="network-panel-listings" role="tabpanel">
      {intakeDetailOpen ? null : (
        <div className={styles.complianceBanner} data-testid="network-compliance-banner">
          <span>COMPLIANCE</span>
          正式上線前需確認來源授權、服務條款、robots 規則與資料使用範圍。系統支援合作 feed、人工匯入與合規 connector，不實作繞過限制的爬取。
        </div>
      )}

      {/*
        "Network URL 收件佇列" sits directly under the compliance banner and
        above the source cards, per the Package 10 layout. It owns its own API
        binding. The radar below is API-bound in production and may use fixtures
        only in local/POC mode.
      */}
      <AssistedIntakeSection
        activeRoleId={activeRoleId}
        selectedHeatZoneId={selectedHeatZoneId}
        targetListings={listings.map(toTargetListingData)}
      />

      <div className={styles.sourceSummaryGrid} aria-label="Listing sources">
        {sources.map((source) => (
          <article className={styles.sourceCard} key={source.id}>
            <div className={styles.sourceCardHead}>
              <strong>{source.name}</strong>
              <span className={styles.toneBadge} data-tone={source.status === "connected" ? "good" : "watch"}>
                {sourceStatusLabel(source.status)}
              </span>
            </div>
            <small className={styles.muted}>{source.lastSyncedAt ? `最近收件 ${source.lastSyncedAt}` : "人工匯入"}</small>
            <p>{source.complianceNote}</p>
            <small className={styles.muted}>新增 {rows.filter((row) => row.sourceId === source.id).length} · 合規模式</small>
          </article>
        ))}
      </div>

      <div className={styles.radarLayout}>
        <aside className={styles.sourceFilterPanel} aria-label="來源篩選">
          <div className={styles.filterTitle}>來源篩選</div>
          <div className={styles.sourceFilterList}>
            {sourceFilterOptions.map((option) => (
              <button
                aria-pressed={sourceFilter === option.id}
                key={option.id}
                onClick={() => setSourceFilter(option.id)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            className={styles.zoneFilterChip}
            data-testid="listing-zone-filter-chip"
            onClick={() => setFilterMode(filterMode === "selected" ? "all" : "selected")}
            type="button"
          >
            {filterMode === "selected" ? `${selectedHeatZoneId ?? "ALL"} · ${selectedZoneLabel ?? "All zones"}` : "全部區域"}
          </button>
          <button
            className={styles.filterClearButton}
            data-testid="listing-filter-all"
            onClick={() => setFilterMode("all")}
            type="button"
          >
            顯示全部物件
          </button>
        </aside>

        <section className={styles.radarInbox}>
          <div className={styles.radarInboxHeader}>
            <div>
              <h3>物件收件匣</h3>
              <span>{sourceFilteredRows.length} 筆</span>
            </div>
            <div className={styles.radarViewToggle} aria-label="Radar view">
              <button aria-pressed type="button">清單</button>
              <button disabled title="尚未提供物件地圖檢視" type="button">地圖</button>
            </div>
          </div>
          {sourceFilteredRows.length ? (
            <div className={styles.radarRows} data-testid="network-listing-table">
              {sourceFilteredRows.map((row) => {
                const listing = listingById.get(row.id);
                const evidence = listing?.sourceEvidence ?? [];
                const isBusy = busyListingId === row.id;
                const mergeTarget = listing?.duplicateOfId ?? row.duplicateOfId;
                // Row and detail share the same terminal/permission/hard-rule
                // gates, and never advertise a write without its real handler.
                const action = listingAction(row, listing, activeRoleId);
                const canConvert = action === "convert" && Boolean(onConvert);
                const canMerge = action === "merge" && Boolean(onMerge);
                const canArchive = action === "archive" && Boolean(onArchive);

                return (
                  <article
                    className={styles.radarRow}
                    data-active={selectedRow?.id === row.id ? "true" : undefined}
                    data-testid={`listing-row-${row.id}`}
                    data-tone={row.tone}
                    key={row.id}
                    onClick={() => setSelectedListingId(row.id)}
                  >
                    <div className={styles.radarRowHead}>
                      <span>{sourceShortLabel(row.sourceName)}</span>
                      <button
                        aria-label={`查看 ${row.id} 物件詳情`}
                        aria-pressed={selectedRow?.id === row.id}
                        className={styles.radarSelectionButton}
                        onClick={() => setSelectedListingId(row.id)}
                        type="button"
                      >
                        <strong>{row.id} · {listingTitle(row)}</strong>
                      </button>
                      <ToneBadge tone={row.tone}>{row.statusLabel}</ToneBadge>
                    </div>
                    <div className={styles.radarRowMeta}>
                      <span>{row.address}</span>
                      <span>{row.rentLabel} · {row.areaPing} ping</span>
                      <span className={styles.zoneMini}>{row.zoneLabel} {row.heatZoneId}</span>
                      <span>Fit {listing?.fitScore ?? "—"}</span>
                      <span>{rowRecommendation(row, mergeTarget)}</span>
                    </div>
                    <div className={styles.radarEvidence}>
                      {row.isDuplicate ? <span className={styles.flag}>Dup {mergeTarget ?? ""}</span> : null}
                      {row.hardRuleFailures.length ? (
                        <span className={styles.flagRisk}>{row.hardRuleFailures.join("; ")}</span>
                      ) : null}
                      {!row.isDuplicate && !row.hardRuleFailures.length ? <span className={styles.muted}>Clean</span> : null}
                      {listing?.mergedIntoId ? <small>merged into {listing.mergedIntoId}</small> : null}
                      {listing?.archivedReason ? <small>{listing.archivedReason}</small> : null}
                      <small data-testid={`listing-evidence-${row.id}`}>
                        {evidence.length} evidence refs{evidence.length ? ` · ${evidence.join(", ")}` : ""}
                      </small>
                    </div>
                    <div className={styles.rowActions}>
                      {canConvert ? (
                        <button
                          data-testid="convert-L-2024"
                          disabled={isBusy}
                          onClick={(event) => {
                            event.stopPropagation();
                            onConvert?.(row.id);
                          }}
                          type="button"
                        >
                          轉為候選點
                        </button>
                      ) : null}
                      {canMerge && mergeTarget ? (
                        <button
                          data-testid="merge-L-2029"
                          disabled={isBusy}
                          onClick={(event) => {
                            event.stopPropagation();
                            onMerge?.(row.id, mergeTarget);
                          }}
                          type="button"
                        >
                          標記重複
                        </button>
                      ) : null}
                      {canArchive ? (
                        <button
                          data-testid="archive-L-2030"
                          disabled={isBusy}
                          onClick={(event) => {
                            event.stopPropagation();
                            onArchive?.(row.id);
                          }}
                          type="button"
                        >
                          封存
                        </button>
                      ) : null}
                      {!canConvert && !canMerge && !canArchive ? (
                        <span className={styles.muted}>{isBusy ? "寫入中…" : "查看詳情"}</span>
                      ) : null}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className={styles.emptyState}>此篩選下沒有物件。</div>
          )}
        </section>

        <aside className={styles.listingDetailPanel} aria-label="Listing detail">
          {selectedRow ? (
            <>
              <div>
                <div className={styles.detailIdLine}>
                  <span>{selectedRow.id}</span>
                  <ToneBadge tone={selectedRow.tone}>{selectedRow.statusLabel}</ToneBadge>
                </div>
                <h3>{listingTitle(selectedRow)}</h3>
                <p>{selectedRow.sourceName} · {selectedListing?.sourceUrl ?? "source evidence retained"}</p>
              </div>
              <div className={styles.listingPhotoPlaceholder}>物件照片 placeholder</div>
              <dl className={styles.listingDetailRows}>
                <DetailRow label="正規化地址">{selectedRow.address}</DetailRow>
                <DetailRow label="租金／坪數">{selectedRow.rentLabel} · {selectedRow.areaPing} ping</DetailRow>
                <DetailRow label="樓層／面寬">{selectedListing?.floor ?? "—"} · {selectedListing?.frontageMeters != null ? `${selectedListing.frontageMeters}m` : "—"}</DetailRow>
                <DetailRow label="首見">{selectedListing?.firstSeenAt ?? "—"}</DetailRow>
                <DetailRow label="Geocode">{selectedRow.geocodeConfidenceLabel}</DetailRow>
                <DetailRow label="重複檢查">{selectedRow.isDuplicate ? `重複 ${selectedRow.duplicateOfId ?? ""}` : "唯一物件"}</DetailRow>
                <DetailRow label="硬規則">{selectedListing?.hardRuleSummary ?? (selectedRow.hardRuleFailures.length ? selectedRow.hardRuleFailures.join("; ") : "未提供檢查結果")}</DetailRow>
                <DetailRow label="HeatZone">{selectedRow.zoneLabel} · 適配 {selectedListing?.fitScore ?? "—"}</DetailRow>
                <DetailRow label="候選點">{selectedRow.candidateId ?? "—"}</DetailRow>
                <DetailRow label="Evidence">{(selectedListing?.sourceEvidence ?? []).join(", ") || "—"}</DetailRow>
              </dl>
              <button
                className={styles.detailPrimaryButton}
                data-testid="listing-detail-primary"
                disabled={
                  !detailActionAvailable || busyListingId === selectedRow.id
                }
                onClick={() => {
                  if (!detailActionAvailable || busyListingId === selectedRow.id) return;
                  const mergeTarget = selectedListing?.duplicateOfId ?? selectedRow.duplicateOfId;
                  if (detailAction === "convert") onConvert?.(selectedRow.id);
                  else if (detailAction === "merge" && mergeTarget) onMerge?.(selectedRow.id, mergeTarget);
                  else if (detailAction === "archive") onArchive?.(selectedRow.id);
                }}
                type="button"
              >
                {detailPrimaryLabel(selectedRow, canMergeListing(activeRoleId), detailMergedIntoId)}
              </button>
              <div className={styles.radarSecondaryActions} aria-label="物件次要操作">
                <button disabled type="button">加入 Watchlist</button>
                <button disabled type="button">聯絡仲介</button>
                <button disabled type="button">直接送 SiteScore（資料足夠）</button>
                <button
                  disabled={detailAction !== "archive" || !onArchive || busyListingId === selectedRow.id}
                  onClick={() => onArchive?.(selectedRow.id)}
                  type="button"
                >標記不適合／封存</button>
                <p>地圖、Watchlist、仲介聯絡與直接評分尚未提供；封存須符合硬規則與服務條件。</p>
              </div>
              {!detailActionAvailable && !detailMergeDenied ? (
                <p className={styles.muted}>此物件目前沒有可執行操作；候選點請至候選點分頁查看。</p>
              ) : null}
              {detailMergeDenied ? (
                <p className={styles.muted} data-testid="listing-detail-merge-denied">
                  {MERGE_DENIED_NOTE}
                </p>
              ) : null}
            </>
          ) : (
            <div className={styles.emptyState}>此篩選下沒有物件，請調整來源或區域。</div>
          )}
        </aside>
      </div>
    </div>
  );
}

// Preserve the service's existing bounded demo action IDs and role gates.
// An absence of failures is not a fabricated hard-rule success receipt.
function listingAction(row: ListingRadarRow, listing: NetworkListingDetail | undefined, role: OperatorRoleId) {
  if (row.status === "archived" || row.status === "candidate" || row.candidateId || listing?.mergedIntoId) return null;
  if (row.id === "L-2024" && !row.isDuplicate && row.hardRuleFailures.length === 0) return "convert";
  if (row.id === "L-2029" && (listing?.duplicateOfId ?? row.duplicateOfId) && canMergeListing(role)) return "merge";
  if (row.id === "L-2030" && (row.status === "hardfail" || row.hardRuleFailures.length > 0)) return "archive";
  return null;
}

function ToneBadge({ children, tone }: { children: ReactNode; tone: "good" | "watch" | "risk" }) {
  return (
    <span className={styles.toneBadge} data-tone={tone}>
      {children}
    </span>
  );
}

function DetailRow({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function sourceShortLabel(name: string) {
  if (name.includes("591")) return "591";
  if (/broker/i.test(name) || name.includes("仲介")) return "仲介";
  return name.split(" ")[0] || name;
}

function sourceStatusLabel(status: ListingSource["status"]) {
  if (status === "connected") return "已連接";
  if (status === "paused") return "已暫停";
  return "僅人工";
}

function listingTitle(row: ListingRadarRow) {
  return row.address.replace(/^台北市|^新北市/, "");
}

function rowRecommendation(row: ListingRadarRow, mergeTarget?: string) {
  if (row.status === "candidate") return "已轉候選";
  if (row.status === "archived") return "已封存";
  if (row.isDuplicate) return `標記重複${mergeTarget ? ` ${mergeTarget}` : ""}`;
  if (row.hardRuleFailures.length) return "封存";
  return "轉為候選點";
}

/**
 * `canMerge` must be threaded in: offering "標記重複（保留目標物件）" to a role
 * that cannot merge advertises an action the server would refuse, and the
 * handler would silently no-op. The permission state is shown instead.
 *
 * `mergedIntoId` outranks both: a merged source stays isDuplicate, so without
 * it this still labels a completed merge as an available one.
 */
function detailPrimaryLabel(row: ListingRadarRow, canMerge: boolean, mergedIntoId?: string) {
  if (row.status === "candidate") return `前往候選點 ${row.candidateId ?? ""}`;
  if (row.status === "archived") return "已封存";
  if (mergedIntoId) return `已標記重複至 ${mergedIntoId}`;
  if (row.isDuplicate) return canMerge ? "標記重複（保留目標物件）" : "無標記重複權限";
  if (row.hardRuleFailures.length) return "封存（硬規則未過）";
  return "轉為候選點";
}
