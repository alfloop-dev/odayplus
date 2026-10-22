/** Exact navigation identity only. The existing current read owner grants access. */
export const HEAT_ZONE_SELECTION_PARAM = "heatZone";

export type HeatZoneSelection = {
  readonly requested: boolean;
  readonly id: string | null;
  readonly invalid: boolean;
};

const NO_SELECTION: HeatZoneSelection = Object.freeze({ requested: false, id: null, invalid: false });
const INVALID_SELECTION: HeatZoneSelection = Object.freeze({ requested: true, id: null, invalid: true });

function oneExactValue(values: string[]): string | null {
  if (!values.length || !values[0].trim() || values.some((value) => value !== values[0])) return null;
  return values[0];
}

/** Only Find Areas consumes this identity; Candidates retains its separate zone scope. */
export function parseHeatZoneSelection(params: { toString(): string }, activeTabIndex: number): HeatZoneSelection {
  if (activeTabIndex !== 0) return NO_SELECTION;
  const query = new URLSearchParams(params.toString());
  const zones = query.getAll(HEAT_ZONE_SELECTION_PARAM);
  const entities = query.getAll("entity");
  if (!zones.length && !entities.length) return NO_SELECTION;
  const zoneId = oneExactValue(zones);
  const entityId = oneExactValue(entities);
  if (zoneId === null || entityId === null || zoneId !== entityId) return INVALID_SELECTION;
  return { requested: true, id: zoneId, invalid: false };
}

/** The Task Center owner must supply both exact IDs after current source admission. */
export function heatZoneTaskHref(target: unknown): string | null {
  if (!target || typeof target !== "object" || Array.isArray(target)) return null;
  const value = target as Record<string, unknown>;
  if (value.workspace !== "network" || value.tab !== "areas"
    || typeof value.entityId !== "string" || !value.entityId.trim()
    || typeof value.heatZoneId !== "string" || value.heatZoneId !== value.entityId) return null;
  const params = new URLSearchParams({ ws: "network", tab: "areas", entity: value.entityId,
    [HEAT_ZONE_SELECTION_PARAM]: value.heatZoneId });
  return `/operator?${params}`;
}
