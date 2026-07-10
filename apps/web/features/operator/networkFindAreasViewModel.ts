import type {
  Candidate,
  HeatZoneLens,
  Listing,
  ListingSource,
  OperatorHeatZone,
  OperatorRoleId,
  RebalanceStore,
  RiskLevel,
  SiteReview,
} from "./types";

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

export type NetworkTone = "good" | "watch" | "risk";

export type ListingRadarRow = {
  id: string;
  sourceId: string;
  sourceName: string;
  sourceStatus: ListingSource["status"];
  complianceNote: string;
  heatZoneId: string;
  zoneLabel: string;
  address: string;
  status: Listing["status"];
  statusLabel: string;
  rentLabel: string;
  areaPing: number;
  geocodeConfidence: number;
  geocodeConfidenceLabel: string;
  isDuplicate: boolean;
  duplicateOfId?: string;
  hardRuleFailures: string[];
  candidateId?: string;
  tone: NetworkTone;
};

export type CandidatePipelineRow = {
  id: string;
  heatZoneId: string;
  zoneLabel: string;
  title: string;
  address: string;
  status: Candidate["status"];
  statusLabel: string;
  score: number;
  scoreMeter: number;
  recommendation: Candidate["recommendation"];
  modelVersion: string;
  datasetSnapshotId: string;
  missingData: string[];
  reviewId?: string;
  listingId?: string;
  isBestInZone: boolean;
  tone: NetworkTone;
};

export type SiteScoreLabRow = {
  id: string;
  title: string;
  zoneLabel: string;
  score: number;
  scoreMeter: number;
  recommendation: Candidate["recommendation"];
  band: string;
  tone: NetworkTone;
  modelVersion: string;
  datasetSnapshotId: string;
  missingData: string[];
  evidenceReady: boolean;
  decisionReady: boolean;
  gateLabel: string;
};

export type CompareMetricRow = {
  key: string;
  label: string;
  values: Array<{ zoneId: string; value: number; label: string; isLeader: boolean }>;
};

export type CompareColumn = {
  zoneId: string;
  label: string;
  rank: number;
  lensLabel: string;
};

export type NetworkCompareViewModel = {
  columns: CompareColumn[];
  metrics: CompareMetricRow[];
};

export type ReviewQueueRow = {
  id: string;
  candidateId: string;
  candidateTitle: string;
  zoneLabel: string;
  status: SiteReview["status"];
  statusLabel: string;
  requestedByLabel: string;
  reviewerLabels: string[];
  requestedAt: string;
  decidedAt?: string;
  reasonRequired: boolean;
  reason?: string;
  score?: number;
  recommendation?: Candidate["recommendation"];
  tone: NetworkTone;
};

export type RebalanceQueueRow = {
  id: string;
  storeId: string;
  storeName: string;
  status: RebalanceStore["status"];
  statusLabel: string;
  avmRequestId?: string;
  netPlanOptionId?: string;
  relatedApprovalId?: string;
  summary: string;
  tone: NetworkTone;
};

export type NetworkFindAreasViewModel = {
  lenses: LensDefinition[];
  activeLens: NetworkFindAreasLens;
  selectedZone: NetworkFindAreasZoneViewModel | null;
  zones: NetworkFindAreasZoneViewModel[];
  rankedZones: NetworkFindAreasZoneViewModel[];
  mapPoints: NetworkFindAreasMapPoint[];
  listingRadar: ListingRadarRow[];
  candidatePipeline: CandidatePipelineRow[];
  siteScoreLab: SiteScoreLabRow[];
  compare: NetworkCompareViewModel;
  reviewQueue: ReviewQueueRow[];
  rebalanceQueue: RebalanceQueueRow[];
  totals: {
    heatZones: number;
    listings: number;
    candidates: number;
    averageConfidence: string;
    reviews: number;
    rebalances: number;
  };
};

export type BuildNetworkFindAreasViewModelInput = {
  heatZones: OperatorHeatZone[];
  listings: Listing[];
  candidates: Candidate[];
  listingSources?: ListingSource[];
  siteReviews?: SiteReview[];
  rebalanceStores?: RebalanceStore[];
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

const ROLE_LABEL: Record<OperatorRoleId, string> = {
  opsLead: "營運主管",
  supportLead: "客服主管",
  facilitiesLead: "工務主任",
  marketingManager: "行銷經理",
  expansionManager: "展店經理",
  auditPm: "PM／稽核",
};

const LISTING_STATUS_LABEL: Record<Listing["status"], string> = {
  new: "新進",
  parsed: "已解析",
  geocoded: "已定位",
  watching: "追蹤中",
  contacted: "已接觸",
  visit: "已勘查",
  candidate: "轉候選",
  scored: "已評分",
  duplicate: "重複",
  hardfail: "硬規則不符",
  archived: "封存",
  expired: "過期",
};

const CANDIDATE_STATUS_LABEL: Record<Candidate["status"], string> = {
  missingdata: "缺資料",
  scoring: "評分中",
  wait: "觀望",
  ready: "可審",
  pendingreview: "待審核",
  approved: "已核准",
  rejected: "已否決",
  blocked: "阻擋",
};

const SITE_REVIEW_STATUS_LABEL: Record<SiteReview["status"], string> = {
  pending: "待審",
  approved: "核准",
  returned: "退回",
  rejected: "否決",
};

const REBALANCE_STATUS_LABEL: Record<RebalanceStore["status"], string> = {
  watching: "追蹤中",
  avmrequested: "AVM 待估",
  avmready: "AVM 完成",
  netplanreview: "店網評估",
  pendingapproval: "待核准",
  approved: "已核准",
  closed: "結案",
};

function recommendationTone(recommendation: Candidate["recommendation"]): NetworkTone {
  if (recommendation === "GO") {
    return "good";
  }
  if (recommendation === "REJECT") {
    return "risk";
  }
  return "watch";
}

export function buildNetworkFindAreasViewModel({
  activeLens = "demand",
  candidates,
  heatZones,
  listings,
  listingSources = [],
  siteReviews = [],
  rebalanceStores = [],
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

  const zoneLabelById = new Map(zoneModels.map((zone) => [zone.id, zone.label] as const));
  const bestCandidateIds = new Set(
    zoneModels.map((zone) => zone.bestCandidate?.id).filter((id): id is string => Boolean(id)),
  );
  const candidateById = new Map(candidates.map((candidate) => [candidate.id, candidate] as const));

  const listingRadar = buildListingRadar(listings, listingSources, zoneLabelById);
  const candidatePipeline = buildCandidatePipeline(candidates, zoneLabelById, bestCandidateIds);
  const siteScoreLab = buildSiteScoreLab(candidates, zoneLabelById);
  const compare = buildCompareViewModel(rankedZones);
  const reviewQueue = buildReviewQueue(siteReviews, candidateById, zoneLabelById);
  const rebalanceQueue = buildRebalanceQueue(rebalanceStores);

  return {
    activeLens,
    candidatePipeline,
    compare,
    lenses: NETWORK_FIND_AREAS_LENSES,
    listingRadar,
    mapPoints: buildMapPoints(zoneModels),
    rankedZones,
    rebalanceQueue,
    reviewQueue,
    selectedZone,
    siteScoreLab,
    totals: {
      averageConfidence: formatPercent(average(zoneModels.map((zone) => zone.confidence))),
      candidates: candidates.length,
      heatZones: zoneModels.length,
      listings: listings.length,
      rebalances: rebalanceStores.length,
      reviews: siteReviews.length,
    },
    zones: zoneModels,
  };
}

function buildListingRadar(
  listings: Listing[],
  sources: ListingSource[],
  zoneLabelById: Map<string, string>,
): ListingRadarRow[] {
  const sourceById = new Map(sources.map((source) => [source.id, source] as const));
  return listings.map((listing) => {
    const source = sourceById.get(listing.sourceId);
    const isDuplicate = listing.status === "duplicate" || Boolean(listing.duplicateOfId);
    const tone: NetworkTone =
      listing.status === "hardfail" || listing.hardRuleFailures.length > 0
        ? "risk"
        : isDuplicate || listing.status === "expired"
          ? "watch"
          : "good";
    return {
      address: listing.address,
      areaPing: listing.areaPing,
      candidateId: listing.candidateId,
      complianceNote: source?.complianceNote ?? "Source metadata unavailable.",
      duplicateOfId: listing.duplicateOfId,
      geocodeConfidence: listing.geocodeConfidence,
      geocodeConfidenceLabel: formatPercent(listing.geocodeConfidence),
      hardRuleFailures: listing.hardRuleFailures,
      heatZoneId: listing.heatZoneId,
      id: listing.id,
      isDuplicate,
      rentLabel: formatCurrencyTwd(listing.rentPerMonth),
      sourceId: listing.sourceId,
      sourceName: source?.name ?? listing.sourceId,
      sourceStatus: source?.status ?? "manualOnly",
      status: listing.status,
      statusLabel: LISTING_STATUS_LABEL[listing.status] ?? listing.status,
      tone,
      zoneLabel: zoneLabelById.get(listing.heatZoneId) ?? listing.heatZoneId,
    };
  });
}

function buildCandidatePipeline(
  candidates: Candidate[],
  zoneLabelById: Map<string, string>,
  bestCandidateIds: Set<string>,
): CandidatePipelineRow[] {
  return [...candidates]
    .sort((left, right) => right.score - left.score)
    .map((candidate) => ({
      address: candidate.address,
      datasetSnapshotId: candidate.datasetSnapshotId,
      heatZoneId: candidate.heatZoneId,
      id: candidate.id,
      isBestInZone: bestCandidateIds.has(candidate.id),
      listingId: candidate.listingId,
      missingData: candidate.missingData,
      modelVersion: candidate.modelVersion,
      recommendation: candidate.recommendation,
      reviewId: candidate.reviewId,
      score: candidate.score,
      scoreMeter: clamp01(candidate.score / 100),
      status: candidate.status,
      statusLabel: CANDIDATE_STATUS_LABEL[candidate.status] ?? candidate.status,
      title: candidate.title,
      tone: recommendationTone(candidate.recommendation),
      zoneLabel: zoneLabelById.get(candidate.heatZoneId) ?? candidate.heatZoneId,
    }));
}

function buildSiteScoreLab(candidates: Candidate[], zoneLabelById: Map<string, string>): SiteScoreLabRow[] {
  return [...candidates]
    .sort((left, right) => right.score - left.score)
    .map((candidate) => {
      const evidenceReady = candidate.missingData.length === 0;
      const decisionReady = evidenceReady && candidate.recommendation === "GO";
      return {
        band:
          candidate.recommendation === "GO"
            ? "GO ≥ 80"
            : candidate.recommendation === "WAIT"
              ? "WAIT 60–79"
              : "REJECT < 60",
        datasetSnapshotId: candidate.datasetSnapshotId,
        decisionReady,
        evidenceReady,
        gateLabel: evidenceReady
          ? decisionReady
            ? "可送核准"
            : "證據齊備，建議續觀察"
          : `待補 ${candidate.missingData.length} 項證據`,
        id: candidate.id,
        missingData: candidate.missingData,
        modelVersion: candidate.modelVersion,
        recommendation: candidate.recommendation,
        score: candidate.score,
        scoreMeter: clamp01(candidate.score / 100),
        title: candidate.title,
        tone: recommendationTone(candidate.recommendation),
        zoneLabel: zoneLabelById.get(candidate.heatZoneId) ?? candidate.heatZoneId,
      };
    });
}

const COMPARE_METRICS: Array<{ key: string; label: string; pick: (zone: NetworkFindAreasZoneViewModel) => number }> = [
  { key: "demand", label: "Demand Gap", pick: (zone) => zone.demandGap },
  { key: "fit", label: "Brand Fit", pick: (zone) => zone.fitScore },
  { key: "competition", label: "Competition (low better)", pick: (zone) => 1 - zone.competitionIndex },
  { key: "cannibalization", label: "Cannibalization (low better)", pick: (zone) => zone.cannibalizationScore },
  { key: "rent", label: "Rent Opportunity", pick: (zone) => zone.rentScore },
  { key: "traffic", label: "Traffic", pick: (zone) => zone.trafficScore },
  { key: "unmet", label: "Unmet Demand", pick: (zone) => zone.unmetScore },
  { key: "confidence", label: "Confidence", pick: (zone) => zone.confidence },
];

function buildCompareViewModel(rankedZones: NetworkFindAreasZoneViewModel[]): NetworkCompareViewModel {
  const columns: CompareColumn[] = rankedZones.map((zone) => ({
    label: `${zone.id} · ${zone.label}`,
    lensLabel: zone.lensLabel,
    rank: zone.rank,
    zoneId: zone.id,
  }));

  const metrics: CompareMetricRow[] = COMPARE_METRICS.map((metric) => {
    const raw = rankedZones.map((zone) => ({ value: clamp01(metric.pick(zone)), zoneId: zone.id }));
    const maxValue = raw.reduce((max, entry) => Math.max(max, entry.value), 0);
    return {
      key: metric.key,
      label: metric.label,
      values: raw.map((entry) => ({
        isLeader: rankedZones.length > 1 && entry.value === maxValue && maxValue > 0,
        label: formatPercent(entry.value),
        value: entry.value,
        zoneId: entry.zoneId,
      })),
    };
  });

  return { columns, metrics };
}

function buildReviewQueue(
  siteReviews: SiteReview[],
  candidateById: Map<string, Candidate>,
  zoneLabelById: Map<string, string>,
): ReviewQueueRow[] {
  return siteReviews.map((review) => {
    const candidate = candidateById.get(review.candidateId);
    const tone: NetworkTone =
      review.status === "approved" ? "good" : review.status === "rejected" ? "risk" : "watch";
    return {
      candidateId: review.candidateId,
      candidateTitle: candidate?.title ?? review.candidateId,
      decidedAt: review.decidedAt,
      id: review.id,
      reason: review.reason,
      reasonRequired: review.reasonRequired,
      recommendation: candidate?.recommendation,
      requestedAt: review.requestedAt,
      requestedByLabel: ROLE_LABEL[review.requestedByRoleId] ?? review.requestedByRoleId,
      reviewerLabels: review.reviewerRoleIds.map((roleId) => ROLE_LABEL[roleId] ?? roleId),
      score: candidate?.score,
      status: review.status,
      statusLabel: SITE_REVIEW_STATUS_LABEL[review.status] ?? review.status,
      tone,
      zoneLabel: candidate ? zoneLabelById.get(candidate.heatZoneId) ?? candidate.heatZoneId : "—",
    };
  });
}

function buildRebalanceQueue(rebalanceStores: RebalanceStore[]): RebalanceQueueRow[] {
  return rebalanceStores.map((store) => ({
    avmRequestId: store.avmRequestId,
    id: store.id,
    netPlanOptionId: store.netPlanOptionId,
    relatedApprovalId: store.relatedApprovalId,
    status: store.status,
    statusLabel: REBALANCE_STATUS_LABEL[store.status] ?? store.status,
    storeId: store.storeId,
    storeName: store.storeName,
    summary: store.summary,
    tone: store.status === "approved" || store.status === "closed" ? "good" : store.status === "pendingapproval" ? "watch" : "watch",
  }));
}

function formatCurrencyTwd(value: number) {
  return new Intl.NumberFormat("en-US", {
    currency: "TWD",
    maximumFractionDigits: 0,
    style: "currency",
  }).format(value);
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
