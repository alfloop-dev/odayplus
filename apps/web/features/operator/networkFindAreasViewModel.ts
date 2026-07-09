import type { Candidate, HeatZoneLens, Listing, OperatorHeatZone, RiskLevel } from "./types";

export type NetworkFindAreasLens = HeatZoneLens | "life";

export type LensDefinition = {
  id: NetworkFindAreasLens;
  label: string;
  shortLabel: string;
  description: string;
};

export type NetworkFindAreasZoneViewModel = {
  zone: OperatorHeatZone;
  id: string;
  label: string;
  rank: number;
  centroidLabel: string;
  demandGap: number;
  demandLabel: string;
  fitScore: number;
  fitLabel: string;
  competitionIndex: number;
  competitionLabel: string;
  cannibalizationRisk: RiskLevel;
  cannibalizationLabel: string;
  cannibalizationScore: number;
  rentBand: string;
  rentScore: number;
  confidence: number;
  confidenceLabel: string;
  trafficScore: number;
  trafficLabel: string;
  unmetScore: number;
  unmetLabel: string;
  lifeScore: number;
  lifeLabel: string;
  lensScore: number;
  lensLabel: string;
  listingCount: number;
  candidateCount: number;
  candidateSummary: string;
  bestCandidate?: Candidate;
  listings: Listing[];
  candidates: Candidate[];
  reasons: string[];
  risks: string[];
  nextStep: string;
  mapX: number;
  mapY: number;
  mapSize: number;
  mapTone: "good" | "watch" | "risk";
};

export type NetworkFindAreasMapPoint = {
  id: string;
  type: "listing" | "candidate";
  label: string;
  heatZoneId: string;
  x: number;
  y: number;
  status: string;
};

export type NetworkFindAreasViewModel = {
  lenses: LensDefinition[];
  activeLens: NetworkFindAreasLens;
  selectedZone: NetworkFindAreasZoneViewModel | null;
  zones: NetworkFindAreasZoneViewModel[];
  rankedZones: NetworkFindAreasZoneViewModel[];
  mapPoints: NetworkFindAreasMapPoint[];
  totals: {
    heatZones: number;
    listings: number;
    candidates: number;
    averageConfidence: string;
  };
};

export type BuildNetworkFindAreasViewModelInput = {
  heatZones: OperatorHeatZone[];
  listings: Listing[];
  candidates: Candidate[];
  selectedHeatZoneId?: string;
  activeLens?: NetworkFindAreasLens;
};

export const NETWORK_FIND_AREAS_LENSES: LensDefinition[] = [
  {
    id: "demand",
    label: "Demand Gap",
    shortLabel: "Demand",
    description: "Unserved demand intensity",
  },
  {
    id: "fit",
    label: "Brand Fit",
    shortLabel: "Fit",
    description: "Composite demand and operating fit",
  },
  {
    id: "competition",
    label: "Competition",
    shortLabel: "Comp",
    description: "Lower direct competition scores higher",
  },
  {
    id: "cannibalization",
    label: "Cannibalization",
    shortLabel: "Cann",
    description: "Lower overlap risk scores higher",
  },
  {
    id: "rent",
    label: "Rent Band",
    shortLabel: "Rent",
    description: "Lease affordability within current area supply",
  },
  {
    id: "life",
    label: "Life Signal",
    shortLabel: "Life",
    description: "Local activity and neighborhood mix fallback",
  },
  {
    id: "traffic",
    label: "Traffic",
    shortLabel: "Traffic",
    description: "Footfall and transit proxy",
  },
  {
    id: "unmet",
    label: "Unmet Demand",
    shortLabel: "Unmet",
    description: "Demand gap after competition pressure",
  },
  {
    id: "confidence",
    label: "Confidence",
    shortLabel: "Conf",
    description: "Model confidence and evidence coverage",
  },
];

const RISK_WEIGHT: Record<RiskLevel, number> = {
  low: 0.18,
  medium: 0.48,
  high: 0.78,
  critical: 0.95,
};

const RISK_LABEL: Record<RiskLevel, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

export function buildNetworkFindAreasViewModel({
  activeLens = "demand",
  candidates,
  heatZones,
  listings,
  selectedHeatZoneId,
}: BuildNetworkFindAreasViewModelInput): NetworkFindAreasViewModel {
  const zones = [...heatZones].sort((left, right) => left.rank - right.rank);
  const rents = zones.map((zone) => rentMidpoint(zone.rentBand)).filter((value) => value > 0);
  const minRent = rents.length ? Math.min(...rents) : 0;
  const maxRent = rents.length ? Math.max(...rents) : 0;
  const bounds = coordinateBounds(zones);

  const zoneModels = zones.map((zone) =>
    buildZoneViewModel({
      activeLens,
      bounds,
      candidates: candidates.filter((candidate) => candidate.heatZoneId === zone.id),
      listings: listings.filter((listing) => listing.heatZoneId === zone.id),
      maxRent,
      minRent,
      zone,
    }),
  );

  const selectedId =
    zoneModels.find((zone) => zone.id === selectedHeatZoneId)?.id ??
    zoneModels.find((zone) => zone.id === "HZ-01")?.id ??
    zoneModels[0]?.id;
  const selectedZone = zoneModels.find((zone) => zone.id === selectedId) ?? null;
  const rankedZones = [...zoneModels].sort((left, right) => {
    if (right.lensScore !== left.lensScore) {
      return right.lensScore - left.lensScore;
    }
    return left.rank - right.rank;
  });

  return {
    activeLens,
    lenses: NETWORK_FIND_AREAS_LENSES,
    mapPoints: buildMapPoints(zoneModels),
    rankedZones,
    selectedZone,
    totals: {
      averageConfidence: formatPercent(average(zoneModels.map((zone) => zone.confidence))),
      candidates: candidates.length,
      heatZones: zoneModels.length,
      listings: listings.length,
    },
    zones: zoneModels,
  };
}

function buildZoneViewModel({
  activeLens,
  bounds,
  candidates,
  listings,
  maxRent,
  minRent,
  zone,
}: {
  activeLens: NetworkFindAreasLens;
  bounds: ReturnType<typeof coordinateBounds>;
  candidates: Candidate[];
  listings: Listing[];
  maxRent: number;
  minRent: number;
  zone: OperatorHeatZone;
}): NetworkFindAreasZoneViewModel {
  const demandGap = clamp01(zone.demandGap);
  const competitionIndex = clamp01(zone.competitionIndex);
  const cannibalizationWeight = RISK_WEIGHT[zone.cannibalizationRisk] ?? RISK_WEIGHT.medium;
  const cannibalizationScore = clamp01(1 - cannibalizationWeight);
  const rentScore = rentOpportunityScore(zone.rentBand, minRent, maxRent);
  const confidence = clamp01(zone.confidence);
  const trafficScore = deterministicScore(`${zone.id}:traffic`, 0.52, 0.95);
  const lifeScore = deterministicScore(`${zone.id}:life`, 0.48, 0.9);
  const unmetScore = clamp01(demandGap * (1 - competitionIndex * 0.42));
  const fitScore = clamp01(
    demandGap * 0.32 +
      (1 - competitionIndex) * 0.18 +
      cannibalizationScore * 0.16 +
      rentScore * 0.12 +
      trafficScore * 0.12 +
      confidence * 0.1,
  );
  const lensScore = lensValue(activeLens, {
    cannibalizationScore,
    competitionScore: 1 - competitionIndex,
    confidence,
    demandGap,
    fitScore,
    lifeScore,
    rentScore,
    trafficScore,
    unmetScore,
  });
  const mapPosition = coordinatePosition(zone, bounds);
  const bestCandidate = [...candidates].sort((left, right) => right.score - left.score)[0];
  const mapTone = zone.cannibalizationRisk === "high" || zone.cannibalizationRisk === "critical" ? "risk" : lensScore >= 0.72 ? "good" : "watch";

  return {
    bestCandidate,
    candidates,
    candidateCount: candidates.length,
    candidateSummary: bestCandidate ? `${bestCandidate.id} ${bestCandidate.recommendation} · ${bestCandidate.score}` : "No candidate",
    cannibalizationLabel: RISK_LABEL[zone.cannibalizationRisk] ?? zone.cannibalizationRisk,
    cannibalizationRisk: zone.cannibalizationRisk,
    cannibalizationScore,
    centroidLabel: `${zone.centroid[1].toFixed(4)}, ${zone.centroid[0].toFixed(4)}`,
    competitionIndex,
    competitionLabel: formatPercent(competitionIndex),
    confidence,
    confidenceLabel: formatPercent(confidence),
    demandGap,
    demandLabel: formatPercent(demandGap),
    fitLabel: formatPercent(fitScore),
    fitScore,
    id: zone.id,
    label: zone.label,
    lensLabel: formatPercent(lensScore),
    lensScore,
    lifeLabel: formatPercent(lifeScore),
    lifeScore,
    listingCount: listings.length,
    listings,
    mapSize: Math.round(34 + lensScore * 36),
    mapTone,
    mapX: mapPosition.x,
    mapY: mapPosition.y,
    nextStep: zone.nextStep,
    rank: zone.rank,
    reasons: zone.reasons.length ? zone.reasons : ["Fixture fallback generated from demand and traffic signals."],
    rentBand: zone.rentBand,
    rentScore,
    risks: zone.risks.length ? zone.risks : ["Risk review pending."],
    trafficLabel: formatPercent(trafficScore),
    trafficScore,
    unmetLabel: formatPercent(unmetScore),
    unmetScore,
    zone,
  };
}

function lensValue(
  lens: NetworkFindAreasLens,
  values: {
    cannibalizationScore: number;
    competitionScore: number;
    confidence: number;
    demandGap: number;
    fitScore: number;
    lifeScore: number;
    rentScore: number;
    trafficScore: number;
    unmetScore: number;
  },
) {
  switch (lens) {
    case "cannibalization":
      return values.cannibalizationScore;
    case "competition":
      return values.competitionScore;
    case "confidence":
      return values.confidence;
    case "fit":
      return values.fitScore;
    case "life":
      return values.lifeScore;
    case "rent":
      return values.rentScore;
    case "traffic":
      return values.trafficScore;
    case "unmet":
      return values.unmetScore;
    case "demand":
    default:
      return values.demandGap;
  }
}

function buildMapPoints(zones: NetworkFindAreasZoneViewModel[]): NetworkFindAreasMapPoint[] {
  return zones.flatMap((zone) => [
    ...zone.listings.map((listing, index) => {
      const point = offsetPoint(zone.mapX, zone.mapY, `${listing.id}:listing`, index, 5);
      return {
        heatZoneId: zone.id,
        id: listing.id,
        label: listing.address,
        status: listing.status,
        type: "listing" as const,
        x: point.x,
        y: point.y,
      };
    }),
    ...zone.candidates.map((candidate, index) => {
      const point = offsetPoint(zone.mapX, zone.mapY, `${candidate.id}:candidate`, index, 8);
      return {
        heatZoneId: zone.id,
        id: candidate.id,
        label: candidate.title,
        status: candidate.recommendation,
        type: "candidate" as const,
        x: point.x,
        y: point.y,
      };
    }),
  ]);
}

function coordinateBounds(zones: OperatorHeatZone[]) {
  if (!zones.length) {
    return { maxLat: 0, maxLng: 0, minLat: 0, minLng: 0 };
  }

  const lngs = zones.map((zone) => zone.centroid[0]);
  const lats = zones.map((zone) => zone.centroid[1]);
  return {
    maxLat: Math.max(...lats),
    maxLng: Math.max(...lngs),
    minLat: Math.min(...lats),
    minLng: Math.min(...lngs),
  };
}

function coordinatePosition(zone: OperatorHeatZone, bounds: ReturnType<typeof coordinateBounds>) {
  const [lng, lat] = zone.centroid;
  const lngRange = bounds.maxLng - bounds.minLng;
  const latRange = bounds.maxLat - bounds.minLat;
  const fallbackX = deterministicScore(`${zone.id}:x`, 0.22, 0.78) * 100;
  const fallbackY = deterministicScore(`${zone.id}:y`, 0.2, 0.76) * 100;
  const x = lngRange > 0 ? 16 + ((lng - bounds.minLng) / lngRange) * 68 : fallbackX;
  const y = latRange > 0 ? 82 - ((lat - bounds.minLat) / latRange) * 64 : fallbackY;
  return { x: clamp(x, 12, 88), y: clamp(y, 14, 86) };
}

function offsetPoint(baseX: number, baseY: number, seed: string, index: number, radius: number) {
  const angle = deterministicScore(seed, 0, 1) * Math.PI * 2;
  const distance = radius + (index % 3) * 3;
  return {
    x: clamp(baseX + Math.cos(angle) * distance, 7, 93),
    y: clamp(baseY + Math.sin(angle) * distance, 9, 91),
  };
}

function rentOpportunityScore(rentBand: string, minRent: number, maxRent: number) {
  const rent = rentMidpoint(rentBand);
  if (!rent || maxRent <= minRent) {
    return 0.64;
  }
  return clamp01(1 - (rent - minRent) / (maxRent - minRent));
}

function rentMidpoint(rentBand: string) {
  const numbers = rentBand.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? [];
  if (!numbers.length) {
    return 0;
  }
  return average(numbers);
}

function deterministicScore(seed: string, min: number, max: number) {
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) % 100000;
  }
  const normalized = (hash % 1000) / 999;
  return min + normalized * (max - min);
}

function average(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatPercent(value: number) {
  return `${Math.round(clamp01(value) * 100)}%`;
}

function clamp01(value: number) {
  return clamp(value, 0, 1);
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
