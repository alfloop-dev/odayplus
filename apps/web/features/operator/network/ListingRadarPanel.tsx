"use client";

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { Listing, ListingSource } from "../types";
import type { ListingRadarRow } from "../networkFindAreasViewModel";
import styles from "../networkFindAreas.module.css";

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

export function ListingRadarPanel({
  busyListingId,
  listings,
  onArchive,
  onConvert,
  onMerge,
  rows,
  selectedHeatZoneId,
  selectedZoneLabel,
  sources,
}: {
  busyListingId?: string | null;
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
  const listingById = useMemo(() => new Map(listings.map((listing) => [listing.id, listing])), [listings]);
  const visibleRows =
    filterMode === "selected" && selectedHeatZoneId
      ? rows.filter((row) => row.heatZoneId === selectedHeatZoneId)
      : rows;

  return (
    <div className={styles.tabPanel} data-testid="network-panel-listings" role="tabpanel">
      <div className={styles.panelHeader}>
        <h3>物件雷達 / Listing Radar</h3>
        <span>{visibleRows.length} visible listings</span>
      </div>

      <div className={styles.radarToolbar} aria-label="Listing Radar filters">
        <span className={styles.zoneFilterChip} data-testid="listing-zone-filter-chip">
          {selectedHeatZoneId ?? "ALL"} · {selectedZoneLabel ?? "All zones"}
        </span>
        <div className={styles.segmentedControl}>
          <button
            aria-pressed={filterMode === "selected"}
            onClick={() => setFilterMode("selected")}
            type="button"
          >
            Selected zone
          </button>
          <button
            aria-pressed={filterMode === "all"}
            data-testid="listing-filter-all"
            onClick={() => setFilterMode("all")}
            type="button"
          >
            All listings
          </button>
        </div>
      </div>

      {visibleRows.length ? (
        <div className={styles.tableWrap}>
          <table className={styles.dataTable} data-testid="network-listing-table">
            <thead>
              <tr>
                <th>Listing</th>
                <th>HeatZone</th>
                <th>Status</th>
                <th>Rent / area</th>
                <th>Geocode</th>
                <th>Evidence and rules</th>
                <th>Candidate</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => {
                const listing = listingById.get(row.id);
                const evidence = listing?.sourceEvidence ?? [];
                const isBusy = busyListingId === row.id;
                const mergeTarget = listing?.duplicateOfId ?? row.duplicateOfId;
                const canConvert =
                  row.id === "L-2024" &&
                  !row.candidateId &&
                  !row.isDuplicate &&
                  row.hardRuleFailures.length === 0 &&
                  row.status !== "archived";
                const canMerge = row.id === "L-2029" && Boolean(mergeTarget);
                const canArchive =
                  row.id === "L-2030" &&
                  row.status !== "archived" &&
                  (row.status === "hardfail" || row.hardRuleFailures.length > 0);

                return (
                  <tr key={row.id} data-tone={row.tone} data-testid={`listing-row-${row.id}`}>
                    <td>
                      <strong>{row.id}</strong>
                      <small>{row.address}</small>
                      <small>{row.sourceName}</small>
                      {listing?.sourceListingId ? <small>{listing.sourceListingId}</small> : null}
                    </td>
                    <td>
                      {row.zoneLabel}
                      <small>{row.heatZoneId}</small>
                    </td>
                    <td>
                      <ToneBadge tone={row.tone}>{row.statusLabel}</ToneBadge>
                      {listing?.mergedIntoId ? <small>merged into {listing.mergedIntoId}</small> : null}
                      {listing?.archivedReason ? <small>{listing.archivedReason}</small> : null}
                    </td>
                    <td>
                      {row.rentLabel}
                      <small>
                        {row.areaPing} ping
                        {listing?.floor ? ` · ${listing.floor}` : ""}
                      </small>
                    </td>
                    <td>{row.geocodeConfidenceLabel}</td>
                    <td>
                      {row.isDuplicate ? <span className={styles.flag}>Dup {mergeTarget ?? ""}</span> : null}
                      {row.hardRuleFailures.length ? (
                        <span className={styles.flagRisk}>{row.hardRuleFailures.join("; ")}</span>
                      ) : null}
                      {!row.isDuplicate && !row.hardRuleFailures.length ? <span className={styles.muted}>Clean</span> : null}
                      <small data-testid={`listing-evidence-${row.id}`}>
                        {evidence.length} evidence refs{evidence.length ? ` · ${evidence.join(", ")}` : ""}
                      </small>
                      {listing?.hardRuleSummary ? <small>{listing.hardRuleSummary}</small> : null}
                    </td>
                    <td>{row.candidateId ?? "—"}</td>
                    <td>
                      <div className={styles.rowActions}>
                        {canConvert ? (
                          <button
                            data-testid="convert-L-2024"
                            disabled={isBusy}
                            onClick={() => onConvert?.(row.id)}
                            type="button"
                          >
                            Convert
                          </button>
                        ) : null}
                        {canMerge && mergeTarget ? (
                          <button
                            data-testid="merge-L-2029"
                            disabled={isBusy}
                            onClick={() => onMerge?.(row.id, mergeTarget)}
                            type="button"
                          >
                            Merge
                          </button>
                        ) : null}
                        {canArchive ? (
                          <button
                            data-testid="archive-L-2030"
                            disabled={isBusy}
                            onClick={() => onArchive?.(row.id)}
                            type="button"
                          >
                            Archive
                          </button>
                        ) : null}
                        {!canConvert && !canMerge && !canArchive ? (
                          <span className={styles.muted}>{isBusy ? "Saving..." : "No action"}</span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className={styles.emptyState}>No listings match the selected filter</div>
      )}
      <div className={styles.cardRow} aria-label="Listing sources">
        {sources.map((source) => (
          <article className={styles.sourceCard} key={source.id}>
            <div className={styles.sourceCardHead}>
              <strong>{source.name}</strong>
              <span className={styles.muted}>{source.status}</span>
            </div>
            <p>{source.complianceNote}</p>
            {source.lastSyncedAt ? <small className={styles.muted}>Synced {source.lastSyncedAt}</small> : null}
          </article>
        ))}
      </div>
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
