import type { OperatorDataAvailability, OperatorLoadFailure } from "../operatorDataMode";
import { HEAT_ZONE_SELECTION_PARAM, type HeatZoneSelection } from "./heatZoneNavigation";

/**
 * Find Areas consumer for the frozen Root HeatZone URL identity.
 *
 * The URL only names the requested HeatZone; it never grants read authority.
 * The original guarded `network-listings` read stays the sole authority, and a
 * requested target is shown only after a fresh read in the current access
 * generation and location confirms it exactly.
 */

/**
 * The `selectedHeatZoneId` API argument implied by the URL.
 *
 * - `undefined`: no explicit intent; the original selection behaviour applies.
 * - `null`: explicit intent that is refused (malformed, or conflicting with a
 *   controlled `selectedHeatZoneId` prop). No zone may be chosen for it.
 * - `string`: the exact opaque requested HeatZone ID.
 */
export function heatZoneRequestArgument(
  selection: HeatZoneSelection,
  controlledSelectedId: string | undefined,
): string | null | undefined {
  if (!selection.requested) return undefined;
  if (selection.invalid || selection.id === null) return null;
  if (controlledSelectedId !== undefined && controlledSelectedId !== selection.id) return null;
  return selection.id;
}

/**
 * Read-admission receipt: which access generation, URL and location epoch a
 * snapshot answered.
 *
 * The URL string alone is not a freshness identity: A -> B -> Back to the
 * byte-identical A is a new location intent. `locationEpoch` is the monotonic
 * location version captured when the read started, so an earlier A snapshot
 * can never equal the current A receipt again; only a fresh current read can.
 */
export function heatZoneSnapshotReceipt(generation: number, locationKey: string, locationEpoch: number): string {
  return JSON.stringify([generation, locationEpoch, locationKey]);
}

type RequestedSnapshot = {
  source?: unknown;
  selectedHeatZoneId?: unknown;
  heatZones?: ReadonlyArray<{ id?: unknown }> | null;
  heatZoneAvailability?: { state?: unknown } | null;
} | null;

export type HeatZoneUnavailableReason = "read" | "source" | "mismatch" | "missing" | "duplicate";

export type HeatZoneRequestedTarget =
  | { readonly state: "none" }
  | { readonly state: "invalid" }
  | { readonly state: "conflict"; readonly id: string }
  | { readonly state: "pending"; readonly id: string }
  | { readonly state: "unavailable"; readonly id: string; readonly reason: HeatZoneUnavailableReason }
  | { readonly state: "confirmed"; readonly id: string };

export type HeatZoneRequestedTargetInput = {
  selection: HeatZoneSelection;
  controlledSelectedId: string | undefined;
  /** The Network snapshot already masked to the current access generation. */
  snapshot: RequestedSnapshot;
  loadState: OperatorDataAvailability;
  /** Receipt recorded when the snapshot (or its refusal) was admitted. */
  snapshotReceipt: string | null;
  /** Receipt of the current access generation, URL and location epoch. */
  currentReceipt: string;
};

/**
 * Confirms an explicit request only against a fresh authenticated snapshot:
 * same generation+epoch+URL receipt, API source, available HeatZone aggregates,
 * `selectedHeatZoneId` equal to the requested ID and exactly one matching row.
 * Everything else stays pending or unavailable; nothing falls back to another
 * zone, the first row or fixtures.
 */
export function resolveHeatZoneRequestedTarget({
  selection,
  controlledSelectedId,
  snapshot,
  loadState,
  snapshotReceipt,
  currentReceipt,
}: HeatZoneRequestedTargetInput): HeatZoneRequestedTarget {
  if (!selection.requested) return { state: "none" };
  if (selection.invalid || selection.id === null) return { state: "invalid" };
  const id = selection.id;
  if (controlledSelectedId !== undefined && controlledSelectedId !== id) return { state: "conflict", id };
  if (snapshotReceipt !== currentReceipt || loadState === "loading") return { state: "pending", id };
  if (loadState === "empty") return { state: "unavailable", id, reason: "missing" };
  if (loadState !== "ready" || !snapshot) return { state: "unavailable", id, reason: "read" };
  if (snapshot.source !== "api") return { state: "unavailable", id, reason: "source" };
  if (snapshot.heatZoneAvailability?.state === "unavailable") return { state: "unavailable", id, reason: "read" };
  if (snapshot.selectedHeatZoneId !== id) return { state: "unavailable", id, reason: "mismatch" };
  const matches = Array.isArray(snapshot.heatZones) ? snapshot.heatZones.filter((zone) => zone?.id === id).length : 0;
  if (matches === 0) return { state: "unavailable", id, reason: "missing" };
  if (matches > 1) return { state: "unavailable", id, reason: "duplicate" };
  return { state: "confirmed", id };
}

const UNAVAILABLE_DETAIL: Record<HeatZoneUnavailableReason, string> = {
  read: "目前讀取未能核對指定熱區；未改選其他熱區。",
  source: "指定熱區的讀取來源不是正式資料；未改選其他熱區或示範資料。",
  mismatch: "目前讀取選定的熱區與連結指定的熱區不一致；未改選其他熱區。",
  missing: "目前身分與範圍內找不到連結指定的熱區；未改選其他熱區。",
  duplicate: "目前讀取包含多筆相同 ID 的熱區，無法唯一核對；未改選其他熱區。",
};

export type HeatZoneTargetGate = {
  status: "loading" | "error";
  loadingDetail?: string;
  failure?: OperatorLoadFailure;
  retry: boolean;
};

/** Presentation for the existing OperatorDataUnavailableGate; null when the panel may render. */
export function heatZoneTargetGate(target: HeatZoneRequestedTarget): HeatZoneTargetGate | null {
  switch (target.state) {
    case "none":
    case "confirmed":
      return null;
    case "pending":
      return { status: "loading", loadingDetail: "正在以目前身分核對連結指定的熱區。", retry: false };
    case "invalid":
      return { status: "error", retry: false, failure: { kind: "unknown",
        technicalDetail: "熱區連結的 entity 與 heatZone 缺漏、空白或不一致；未改選其他熱區。" } };
    case "conflict":
      return { status: "error", retry: false, failure: { kind: "unknown",
        technicalDetail: "熱區連結與目前工作台指定的熱區不一致；未改選任一熱區。" } };
    case "unavailable":
      return { status: "error", retry: true, failure: { kind: "unknown", technicalDetail: UNAVAILABLE_DETAIL[target.reason] } };
  }
}

/** Extra params for the existing tab navigation helper: both identities move together. */
export function heatZoneSelectionParams(id: string): Record<string, string> {
  return { entity: id, [HEAT_ZONE_SELECTION_PARAM]: id };
}
