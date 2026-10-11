/**
 * Offline schematic basemap for the HeatZone map.
 *
 * Package 10 draws HeatZones over a real OSM/CARTO basemap. No tile provider
 * is configured for local, POC or E2E builds (NEXT_PUBLIC_ODP_MAP_TILE_URL is
 * unset), and those builds must not reach the network, so the map previously
 * rendered on a flat background with nothing to orient the zones against.
 *
 * This is a hand-simplified outline of the main Taipei rivers and trunk roads,
 * bundled with the app and drawn by MapLibre with no network request. It is a
 * schematic for orientation only — the on-map caption says so — and is
 * replaced by the live raster tiles whenever a tile URL is configured.
 * Coordinates are [lng, lat].
 */

type Line = GeoJSON.Feature<GeoJSON.LineString, { kind: "river" | "highway" | "arterial"; name: string }>;

function line(kind: Line["properties"]["kind"], name: string, coordinates: Array<[number, number]>): Line {
  return { type: "Feature", properties: { kind, name }, geometry: { type: "LineString", coordinates } };
}

export const SCHEMATIC_BASEMAP: GeoJSON.FeatureCollection<GeoJSON.LineString, Line["properties"]> = {
  type: "FeatureCollection",
  features: [
    line("river", "淡水河", [
      [121.405, 25.18], [121.43, 25.16], [121.455, 25.135], [121.465, 25.118], [121.478, 25.1],
      [121.492, 25.085], [121.505, 25.068], [121.503, 25.05], [121.495, 25.035], [121.49, 25.022],
    ]),
    line("river", "大漢溪", [
      [121.49, 25.022], [121.47, 25.012], [121.45, 24.995], [121.43, 24.975], [121.4, 24.955], [121.37, 24.935],
    ]),
    line("river", "新店溪", [
      [121.49, 25.022], [121.505, 25.012], [121.52, 25.008], [121.533, 25.005], [121.54, 24.99],
      [121.543, 24.97], [121.54, 24.955],
    ]),
    line("river", "基隆河", [
      [121.465, 25.118], [121.485, 25.108], [121.505, 25.098], [121.52, 25.088], [121.535, 25.082],
      [121.552, 25.077], [121.57, 25.072], [121.59, 25.066], [121.612, 25.058], [121.64, 25.062], [121.665, 25.068],
    ]),
    line("highway", "國道一號", [
      [121.66, 25.067], [121.61, 25.063], [121.57, 25.07], [121.54, 25.076], [121.515, 25.072],
      [121.49, 25.065], [121.46, 25.058], [121.43, 25.05], [121.4, 25.045],
    ]),
    line("highway", "國道三號", [
      [121.66, 25.0], [121.59, 24.985], [121.545, 24.975], [121.5, 24.985], [121.455, 24.985],
      [121.42, 24.98], [121.385, 24.975],
    ]),
    line("arterial", "忠孝東西路", [
      [121.495, 25.046], [121.52, 25.045], [121.545, 25.042], [121.565, 25.041], [121.59, 25.05], [121.615, 25.052],
    ]),
    line("arterial", "新生高架", [[121.532, 25.075], [121.533, 25.05], [121.534, 25.025]]),
    line("arterial", "縣民大道", [[121.495, 25.035], [121.465, 25.015], [121.44, 25.005]]),
  ],
};
