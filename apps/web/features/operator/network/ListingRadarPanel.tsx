"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Listing, ListingSource } from "../types";
import type { ListingRadarRow } from "../networkFindAreasViewModel";
import type { OperatorRoleId } from "../navigation";
import type { OperatorLoadFailure } from "../operatorDataMode";
import { OperatorDataUnavailableGate } from "../OperatorDataUnavailableGate";
import styles from "../networkFindAreas.module.css";
import { AssistedIntakeSection } from "./intake/AssistedIntakeSection";
import { CanonicalIntakeCasePicker } from "./intake/CanonicalIntakeCasePicker";
import type { CanonicalIntakeReadContext } from "./intake/canonicalIntakeReadClient";
import { MERGE_DENIED_NOTE, canMergeListing } from "./listingPermissions";
import { resolveListingAction, type ListingWriteContext } from "./listingActions";
import { resolveListingNavigation, type ListingNavigation, type ListingNavigationTarget } from "./listingNavigation";

type NetworkListingDetail = Listing & {
  archivedReason?: string;
  convertedAt?: string;
  duplicateOfId?: string;
  firstSeenAt?: string;
  fitScore?: number | null;
  floor?: string;
  frontageMeters?: number | null;
  hardRuleSummary?: string;
  mergeReason?: string;
  mergedIntoId?: string | null;
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
  canonicalIntakeReadContext,
  intakeDetailOpen = false,
  listings,
  listingsReadFailure = null,
  onArchive,
  navigationByListing,
  navigationError,
  onNavigate,
  onMerge,
  rows,
  selectedHeatZoneId,
  selectedZoneLabel,
  sources,
  writeContext,
}: {
  activeRoleId: OperatorRoleId;
  busyListingId?: string | null;
  canonicalIntakeReadContext?: CanonicalIntakeReadContext;
  /**
   * True while the canonical intake detail owns the page. Package 10 puts the
   * detail immediately below the global topbar, so the Network compliance strip
   * — like the Network heading, KPI strip, stepper and tab strip above this
   * panel — must not precede it (ADD-006 §3.1). Source cards and the radar stay
   * below the detail exactly as in the canonical layout.
   */
  intakeDetailOpen?: boolean;
  listings: NetworkListingDetail[];
  /**
   * Set when the listing collection itself was refused or unreachable. The
   * source cards, counts and inbox are then replaced by that typed failure:
   * an unread collection is not "0 筆" and not "no listings match".
   */
  listingsReadFailure?: OperatorLoadFailure | null;
  onArchive?: (listingId: string) => void;
  navigationByListing?: ReadonlyMap<string, ListingNavigation>;
  navigationError?: string | null;
  onNavigate?: (listingId: string, target: ListingNavigationTarget) => void;
  onMerge?: (sourceListingId: string, targetListingId: string) => void;
  rows: ListingRadarRow[];
  selectedHeatZoneId?: string;
  selectedZoneLabel?: string;
  sources: ListingSource[];
  writeContext?: ListingWriteContext;
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
    sourceFilteredRows[0] ??
    visibleRows.find((row) => row.id === selectedListingId) ??
    visibleRows[0] ??
    rows[0];
  const selectedListing = selectedRow ? listingById.get(selectedRow.id) : undefined;
  const navigationFor = (id: string) => navigationByListing?.get(id) ?? resolveListingNavigation(id, null, false);
  const detailNavigation = selectedRow ? navigationFor(selectedRow.id) : null;
  // `mergedIntoId` is the durable terminal marker: a merged source keeps
  // isDuplicate/status "duplicate", so only this field distinguishes "can still
  // be merged" from "already merged". A second click would mint a fresh
  // idempotency key, which the server now refuses with 409.
  const detailMergedIntoId = selectedListing?.mergedIntoId ?? undefined;
  const detailIsTerminal = selectedRow?.status === "archived" || Boolean(detailMergedIntoId);
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
  const detailAction = selectedRow ? resolveListingAction(selectedRow.id, listings, activeRoleId, writeContext) : null;
  const detailLegacyAction = detailAction?.kind ?? null;
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

      {!intakeDetailOpen && canonicalIntakeReadContext ? <CanonicalIntakeCasePicker context={canonicalIntakeReadContext} /> : null}

      {navigationError ? <p role="alert">{navigationError}</p> : null}
      {listingsReadFailure ? (
        <OperatorDataUnavailableGate
          failure={listingsReadFailure}
          onRetry={() => window.location.reload()}
          status="error"
        />
      ) : (
        <>
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
                <button aria-pressed={false} type="button">地圖</button>
              </div>
            </div>
            {sourceFilteredRows.length ? (
              <div className={styles.radarRows} data-testid="network-listing-table">
                {sourceFilteredRows.map((row) => {
                  const listing = listingById.get(row.id);
                  const evidence = listing?.sourceEvidence ?? [];
                  const isBusy = busyListingId === row.id;
                  const isTerminal = row.status === "archived" || Boolean(listing?.mergedIntoId);
                  const mergeTarget = listing?.duplicateOfId ?? row.duplicateOfId;
                  const navigation = navigationFor(row.id);
                  // Merge needs listing:UPDATE plus the service's actor allowlist;
                  // hiding it for roles that cannot clear both keeps the console
                  // from offering a button that is guaranteed to 403/422. Once
                  // `mergedIntoId` is set the merge is terminal, so the entry
                  // point must retire rather than mint a second request.
                  const action = resolveListingAction(row.id, listings, activeRoleId, writeContext);
                  const canMerge = action?.kind === "merge";
                  const canArchive = action?.kind === "archive";

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
                        <strong>{row.id} · {listingTitle(row)}</strong>
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
                        {row.hardRuleStatus === "unmeasured" ? <span className={styles.muted}>硬規則尚未檢核</span> : !row.isDuplicate && !row.hardRuleFailures.length ? <span className={styles.muted}>Clean</span> : null}
                        {listing?.mergedIntoId ? <small>merged into {listing.mergedIntoId}</small> : null}
                        {listing?.archivedReason ? <small>{listing.archivedReason}</small> : null}
                        <small data-testid={`listing-evidence-${row.id}`}>
                          {evidence.length} evidence refs{evidence.length ? ` · ${evidence.join(", ")}` : ""}
                        </small>
                      </div>
                      <div className={styles.rowActions}>
                        <button
                          data-testid={`listing-navigate-${row.id}`}
                          disabled={isTerminal || isBusy || !onNavigate || (!navigation.primary && navigation.state !== "multiple")}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (isTerminal) return;
                            setSelectedListingId(row.id);
                            if (navigation.primary) onNavigate?.(row.id, navigation.primary);
                          }}
                          type="button"
                        >
                          {isTerminal ? detailPrimaryLabel(row, canMergeListing(activeRoleId), listing?.mergedIntoId ?? undefined) : navigation.label}
                        </button>
                        {canMerge && mergeTarget ? (
                          <button
                            data-testid={`merge-${row.id}`}
                            disabled={isBusy}
                            onClick={(event) => {
                              event.stopPropagation();
                              if (action?.targetListingId) onMerge?.(row.id, action.targetListingId);
                            }}
                            type="button"
                          >
                            標記重複
                          </button>
                        ) : null}
                        {canArchive ? (
                          <button
                            data-testid={`archive-${row.id}`}
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
                        {!canMerge && !canArchive ? (
                          <span className={styles.muted}>{isBusy ? "處理中…" : "查看詳情"}</span>
                        ) : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className={styles.emptyState}>No listings match the selected filter</div>
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
                  <DetailRow label="樓層／面寬">{selectedListing?.floor ?? "—"} · {selectedListing?.frontageMeters ? `${selectedListing.frontageMeters}m` : "—"}</DetailRow>
                  <DetailRow label="首見">{selectedListing?.firstSeenAt ?? "—"}</DetailRow>
                  <DetailRow label="Geocode">{selectedRow.geocodeConfidenceLabel}</DetailRow>
                  <DetailRow label="重複檢查">{selectedRow.isDuplicate ? `重複 ${selectedRow.duplicateOfId ?? ""}` : "唯一物件"}</DetailRow>
                  <DetailRow label="硬規則">{selectedRow.hardRuleStatus === "unmeasured" ? "硬規則尚未檢核" : selectedListing?.hardRuleSummary ?? (selectedRow.hardRuleFailures.length ? selectedRow.hardRuleFailures.join("; ") : "3/3 通過")}</DetailRow>
                  <DetailRow label="HeatZone">{selectedRow.zoneLabel} · 適配 {selectedListing?.fitScore ?? "—"}</DetailRow>
                  <DetailRow label="候選點">{detailNavigation?.primary?.kind === "candidate" ? detailNavigation.primary.id : "—"}</DetailRow>
                  <DetailRow label="Evidence">{(selectedListing?.sourceEvidence ?? []).join(", ") || "—"}</DetailRow>
                </dl>
                <button
                  className={styles.detailPrimaryButton}
                  data-testid="listing-detail-primary"
                  disabled={detailIsTerminal || busyListingId === selectedRow.id || (detailNavigation?.primary ? !onNavigate : !detailLegacyAction)}
                  onClick={() => {
                    if (detailIsTerminal) return;
                    if (detailNavigation?.primary) {
                      onNavigate?.(selectedRow.id, detailNavigation.primary);
                    } else if (detailLegacyAction === "merge") {
                      if (detailAction?.targetListingId) onMerge?.(selectedRow.id, detailAction.targetListingId);
                    } else if (detailLegacyAction === "archive") {
                      onArchive?.(selectedRow.id);
                    }
                  }}
                  type="button"
                >
                  {detailIsTerminal
                    ? detailPrimaryLabel(selectedRow, canMergeListing(activeRoleId), detailMergedIntoId)
                    : detailNavigation?.primary || !detailLegacyAction ? detailNavigation?.label : detailPrimaryLabel(selectedRow, canMergeListing(activeRoleId), detailMergedIntoId)}
                </button>
                {detailNavigation && detailNavigation.intakes.length > 0 ? (
                  <div aria-label="關聯收件" data-testid="listing-related-intakes">
                    {detailNavigation.intakes.map((intake) => (
                      <button
                        key={intake.id}
                        disabled={busyListingId === selectedRow.id || !onNavigate}
                        onClick={() => onNavigate?.(selectedRow.id, { kind: "intake", id: intake.id })}
                        type="button"
                      >
                        查看收件 {intake.id} · {intake.stage}
                      </button>
                    ))}
                  </div>
                ) : null}
                {detailMergeDenied ? (
                  <p className={styles.muted} data-testid="listing-detail-merge-denied">
                    {MERGE_DENIED_NOTE}
                  </p>
                ) : null}
              </>
            ) : (
              <div className={styles.emptyState}>No listing selected</div>
            )}
          </aside>
        </div>
        </>
      )}
    </div>
  );
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
  if (row.hardRuleStatus === "unmeasured") return "硬規則尚未檢核";
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
  if (row.status === "archived") return "已封存";
  if (mergedIntoId) return `已標記重複至 ${mergedIntoId}`;
  if (row.status === "candidate") return `前往候選點 ${row.candidateId ?? ""}`;
  if (row.isDuplicate) return canMerge ? "標記重複（保留目標物件）" : "無標記重複權限";
  if (row.hardRuleStatus === "unmeasured") return "硬規則尚未檢核";
  if (row.hardRuleFailures.length) return "封存（硬規則未過）";
  return "轉為候選點";
}
