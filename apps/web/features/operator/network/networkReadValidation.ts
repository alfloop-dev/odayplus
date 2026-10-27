import type { CandidateReviewSubmissionReference } from "./networkReviewSubmissionTypes";
// Validate only DTO fields consumed by Network renderers. This does not derive
// scores, eligibility, workflow state or access rights, and never repairs a row.
type Row = Record<string, unknown>;
export const readObject = (value: unknown): value is Row => typeof value === "object" && value !== null && !Array.isArray(value);
export const readId = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const text = (value: unknown): value is string => typeof value === "string";
const number = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullableNumber = (value: unknown) => value === null || number(value);
const optionalNumber = (value: unknown) => value === undefined || nullableNumber(value);
const nullableText = (value: unknown) => value === null || text(value);
const optionalText = (value: unknown) => value === undefined || nullableText(value);
const displayNumber = (value: unknown) => text(value) || number(value);
const optionalDisplayNumber = (value: unknown) => value === undefined || value === null || displayNumber(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const rows = (value: unknown, valid: (row: Row) => boolean): boolean => Array.isArray(value)
  && value.every((row) => readObject(row) && readId(row.id) && valid(row));
const oneOf = (value: unknown, values: readonly string[]) => text(value) && values.includes(value);
const recommendation = (value: unknown) => oneOf(value, ["GO", "WAIT", "REJECT", "INVESTIGATE"]);
const nullableRecommendation = (value: unknown) => value === null || recommendation(value);

// Listing.listing_status and CandidateSite.site_status are source strings.
// Retain safely rendered unknown/terminal states without inventing membership
// in the legacy UI status unions or deriving an eligibility decision.
export function hasNetworkListingsRenderShape(value: unknown): boolean {
  if (!readObject(value)) return false;
  const availability = value.heatZoneAvailability;
  if (availability !== undefined && availability !== null && (!readObject(availability)
    || !oneOf(availability.state, ["available", "unavailable"])
    || !optionalText(availability.message))) return false;
  return rows(value.heatZones, (zone) => text(zone.label) && nullableNumber(zone.rank)
      && (zone.centroid === null || Array.isArray(zone.centroid) && zone.centroid.length === 2 && zone.centroid.every(number))
      && nullableNumber(zone.demandGap) && nullableNumber(zone.competitionIndex)
      && (zone.cannibalizationRisk === null || oneOf(zone.cannibalizationRisk, ["low", "medium", "high", "critical"]))
      && text(zone.rentBand) && optionalNumber(zone.confidence)
      && strings(zone.reasons) && strings(zone.risks) && text(zone.nextStep))
    && rows(value.listingSources, (source) => text(source.name)
      && text(source.status)
      && optionalText(source.complianceNote))
    && rows(value.listings, (listing) => optionalText(listing.sourceId) && optionalText(listing.address)
      && optionalText(listing.heatZoneId) && text(listing.status)
      && optionalNumber(listing.rentPerMonth) && optionalNumber(listing.areaPing)
      && optionalNumber(listing.geocodeConfidence)
      && (listing.hardRuleFailures === undefined || listing.hardRuleFailures === null || strings(listing.hardRuleFailures))
      && optionalText(listing.duplicateOfId) && optionalText(listing.candidateId)
      && optionalText(listing.floor) && optionalText(listing.sourceUrl) && optionalText(listing.archivedReason)
      && optionalText(listing.mergeReason) && optionalText(listing.mergedIntoId) && optionalText(listing.hardRuleSummary)
      && optionalNumber(listing.fitScore) && optionalNumber(listing.frontageMeters)
      && (listing.sourceEvidence === undefined || listing.sourceEvidence === null || strings(listing.sourceEvidence)))
    && rows(value.candidates, (candidate) => text(candidate.heatZoneId) && text(candidate.title)
      && nullableText(candidate.address) && text(candidate.status)
      && nullableNumber(candidate.score) && nullableRecommendation(candidate.recommendation)
      && nullableText(candidate.modelVersion) && nullableText(candidate.datasetSnapshotId)
      && strings(candidate.missingData) && optionalText(candidate.listingId)
      && optionalText(candidate.reviewId) && optionalNumber(candidate.confidence)
      && optionalNumber(candidate.latitude) && optionalNumber(candidate.longitude)
      && optionalNumber(candidate.geocodeConfidence))
    && rows(value.siteReviews, (review) => readId(review.candidateId)
      && text(review.status)
      && strings(review.reviewerRoleIds) && text(review.requestedAt)
      && optionalText(review.decidedAt) && optionalText(review.reason));
}

function validGate(value: unknown): boolean {
  return readObject(value) && text(value.state) && typeof value.passed === "boolean"
    && strings(value.missing) && strings(value.otherMissing) && text(value.blockNote)
    && Array.isArray(value.checks) && value.checks.every((check) => readObject(check)
      && readId(check.key) && text(check.label) && oneOf(check.state, ["ok", "warn", "fail"]) && text(check.note))
    && number(value.okCount) && number(value.totalCount);
}
function validRevenue(value: unknown): boolean {
  return nullableNumber(value) || readObject(value) && number(value.p10) && number(value.p50) && number(value.p90);
}
function validScorecard(card: Row): boolean {
  return text(card.title) && text(card.zoneLabel) && text(card.heatZoneId)
    && nullableNumber(card.score) && recommendation(card.recommendation)
    && text(card.modelVersion) && text(card.datasetSnapshotId) && text(card.generatedAt)
    && (card.confidence === null || displayNumber(card.confidence))
    && optionalDisplayNumber(card.payback) && optionalText(card.capex) && optionalText(card.rentAssumption)
    && readObject(card.revenuePath) && ["m1", "m3", "m6", "m12"].every((key) => validRevenue((card.revenuePath as Row)[key]))
    && readObject(card.band) && ["p10", "p50", "p90"].every((key) => displayNumber((card.band as Row)[key]))
    && readObject(card.subScores) && ["rentReasonableness", "cannibalization", "competition", "demand", "poiFit", "access"]
      .every((key) => optionalDisplayNumber((card.subScores as Row)[key]))
    && [card.drivers, card.reasons, card.risks, card.conditions].every(strings)
    && text(card.conditionTitle);
}
function validRecommendationCard(value: unknown): boolean {
  return readObject(value) && readId(value.id) && text(value.title) && recommendation(value.recommendation)
    && nullableNumber(value.score) && text(value.text) && (value.why === undefined || strings(value.why));
}
function validCompare(value: unknown): boolean {
  if (!readObject(value) || typeof value.empty !== "boolean") return false;
  if (!rows(value.columns, (column) => text(column.title) && nullableText(column.priority)
    && recommendation(column.recommendation) && nullableNumber(column.score) && typeof column.isBest === "boolean")) return false;
  if (!Array.isArray(value.metrics) || !value.metrics.every((metric) => readObject(metric)
    && readId(metric.key) && text(metric.label) && rows(metric.values, (item) => text(item.text) && typeof item.isBest === "boolean"))) return false;
  if (value.recommendation === null) return true;
  const rec = value.recommendation;
  return readObject(rec) && validRecommendationCard(rec.primary)
    && (rec.alternate === null || validRecommendationCard(rec.alternate))
    && (rec.avoid === null || validRecommendationCard(rec.avoid))
    && rows(rec.priorityList, (item) => text(item.priority) && text(item.title)
      && nullableNumber(item.score) && recommendation(item.recommendation));
}
function validReadEvidence(value: unknown): boolean {
  if (value === undefined) return true;
  if (!readObject(value) || value.source !== "canonical" || !readId(value.candidateId)
    || !readId(value.listingId) || !readId(value.addressId) || !nullableText(value.datasetSnapshotId)
    || !readObject(value.fields)) return false;
  return Object.values(value.fields).every((field) => readObject(field)
    && (field.value === null || displayNumber(field.value) || strings(field.value))
    && nullableText(field.unit) && nullableText(field.reason)
    && oneOf(field.availability, ["recorded", "unmeasured", "denied", "invalid"])
    && readObject(field.provenance) && text(field.provenance.recordType)
    && readId(field.provenance.recordId) && text(field.provenance.fieldPath)
    && nullableText(field.provenance.snapshotId) && optionalText(field.provenance.measurementStatus));
}
function validPipelineEvidence(value: unknown): boolean {
  if (value === undefined) return true;
  return readObject(value) && Object.values(value).every((read) => readObject(read)
    && read.source === "canonical" && readId(read.candidateId)
    && oneOf(read.availability, ["recorded", "unavailable", "denied", "conflict"])
    && (read.matches === null || typeof read.matches === "boolean")
    && text(read.producer) && nullableText(read.recordId) && nullableText(read.status) && nullableText(read.reason));
}
function validCurrentAdmission(value: unknown): boolean {
  if (value === undefined) return true;
  return readObject(value) && readId(value.candidateId) && readId(value.listingId) && readId(value.addressId)
    && nullableText(value.checkedAt) && ["currentInputs", "source", "pit", "model"].every((key) => {
      const fact = value[key];
      return readObject(fact) && oneOf(fact.state, ["verified", "blocked", "unverified"]) && nullableText(fact.reasonCode);
    });
}
export function hasVerifiedCurrentScoreAdmission(candidate: unknown): boolean {
  if (!readObject(candidate) || !validCurrentAdmission(candidate.currentAdmission)
    || !readObject(candidate.currentAdmission) || !readObject(candidate.readEvidence)
    || !readObject(candidate.gate) || !validGate(candidate.gate) || candidate.gate.passed !== true
    || !oneOf(candidate.gate.state, ["ready", "warn"])) return false;
  const admission = candidate.currentAdmission;
  return admission.candidateId === candidate.id && admission.listingId === candidate.listingId
    && candidate.readEvidence.source === "canonical" && candidate.readEvidence.candidateId === candidate.id
    && candidate.readEvidence.listingId === admission.listingId && candidate.readEvidence.addressId === admission.addressId
    && readId(admission.checkedAt) && Number.isFinite(Date.parse(admission.checkedAt))
    && ["currentInputs", "source", "pit", "model"].every((key) => readObject(admission[key]) && admission[key].state === "verified");
}
function validTargetFormat(value: unknown): boolean {
  if (value === undefined || value === null) return true; // retained legacy envelopes
  const nonnegative = (x: unknown) => number(x) && x >= 0;
  const positive = (x: unknown) => number(x) && x > 0;
  const count = (x: unknown) => positive(x) && Number.isSafeInteger(x);
  const ratio = (x: unknown) => nonnegative(x) && (x as number) <= 1;
  const unknownAmount = (x: unknown) => x === null || nonnegative(x);
  if (!readObject(value) || value.format_code !== "ODAY_G2" || value.format_version !== "2.0.0"
    || !readId(value.format_name) || !nonnegative(value.package_total_untaxed)
    || !nonnegative(value.unallocated_package_cost) || !positive(value.daily_operating_hours)
    || (value.daily_operating_hours as number) > 24
    || !readObject(value.machine_mix) || !Array.isArray(value.machine_mix.items) || !value.machine_mix.items.length
    || !Array.isArray(value.capital_assets) || !value.capital_assets.length
    || !readObject(value.fitout_spec) || !nonnegative(value.fitout_spec.base_fitout_cost)
    || !count(value.fitout_spec.fitout_useful_life_months)
    || !["utilities_spec", "maintenance_spec", "financing_spec", "tax_spec", "residual_spec", "ramp_spec", "seasonality_spec"]
      .every((key) => value[key] === null)) return false;
  const items = value.machine_mix.items;
  if (!items.every((item) => readObject(item) && count(item.quantity) && readObject(item.machine_model)
    && readId(item.machine_model.model_id) && item.machine_model.machine_class === "COMBO"
    && positive(item.machine_model.capacity_kg) && nonnegative(item.machine_model.unit_capex)
    && nonnegative(item.machine_model.base_cycle_price) && positive(item.machine_model.cycle_minutes)
    && count(item.machine_model.useful_life_months) && ratio(item.machine_model.residual_value_ratio)
    && ["baseline_turns_per_day", "water_liters_per_cycle", "electricity_kwh_per_cycle", "gas_kg_per_cycle",
      "detergent_cost_per_cycle", "monthly_maintenance_per_unit"].every((key) => unknownAmount((item.machine_model as Row)[key])))) return false;
  return new Set(items.map((item) => item.machine_model.model_id)).size === items.length
    && value.capital_assets.every((asset) => readObject(asset) && readId(asset.asset_id)
      && oneOf(asset.asset_kind, ["PAYMENT_SYSTEM", "KIOSK", "VENDING", "CAMERA_SYSTEM", "FURNITURE"])
      && count(asset.quantity) && nonnegative(asset.unit_capex) && count(asset.useful_life_months)
      && ratio(asset.residual_value_ratio) && unknownAmount(asset.monthly_fixed_operating_cost))
    && new Set(value.capital_assets.map((asset) => asset.asset_id)).size === value.capital_assets.length;
}

export function hasNetworkScoringRenderShape(value: unknown): boolean {
  if (!readObject(value)) return false;
  return optionalText(value.modelVersion) && validTargetFormat(value.targetFormat)
    && (value.targetFormat == null || value.source === "canonical")
    && rows(value.candidates, (candidate) => text(candidate.title) && text(candidate.zoneLabel)
      && text(candidate.heatZoneId) && nullableText(candidate.address)
      && nullableText(candidate.modelVersion) && text(candidate.datasetSnapshotId)
      && text(candidate.stage) && optionalText(candidate.canonicalSiteStatus) && validGate(candidate.gate) && typeof candidate.scored === "boolean"
      && nullableNumber(candidate.score) && nullableRecommendation(candidate.recommendation)
      && typeof candidate.inCompare === "boolean" && optionalText(candidate.listingId) && optionalText(candidate.reviewId)
      && validReadEvidence(candidate.readEvidence) && validPipelineEvidence(candidate.pipelineEvidence)
      && validCurrentAdmission(candidate.currentAdmission))
    && rows(value.scorecards, validScorecard) && validCompare(value.compare)
    && Array.isArray(value.compareSet) && value.compareSet.every(readId);
}

const version = (value: unknown): value is number => Number.isSafeInteger(value) && typeof value === "number" && value > 0;
export function hasScoreExecutionContext(value: unknown): value is import("./networkScoringTypes").ScoreExecutionContext {
  return readObject(value) && readId(value.tenantId) && readId(value.candidateSiteId) && readId(value.reportId)
    && version(value.reportVersion) && version(value.jobVersion)
    && readId(value.originalJobId) && readId(value.predecessorJobId)
    && (value.activeExecutionRequest === undefined || value.activeExecutionRequest === null
      || hasScoreExecutionReceipt(value.activeExecutionRequest) && value.activeExecutionRequest.candidateSiteId === value.candidateSiteId);
}
export function hasScoreExecutionReceipt(value: unknown): value is import("./networkScoringTypes").ScoreExecutionReceipt {
  if (!readObject(value) || value.source !== "canonical" || typeof value.created !== "boolean"
    || !readId(value.executionRequestId) || !readId(value.candidateSiteId) || !readId(value.jobId)
    || !oneOf(value.status, ["QUEUED", "RUNNING", "RETRYING", "SUCCEEDED", "FAILED", "DEAD_LETTER", "CANCELLED"])
    || !version(value.actualVersion) || !(value.jobVersion === null || version(value.jobVersion))
    || !readId(value.baseReportId) || !version(value.baseReportVersion) || !readId(value.correlationId)) return false;
  const result = value.result;
  if (!(result === null || readObject(result) && readId(result.report_id) && version(result.report_version)
    && readId(result.sitescore_run_id) && readId(result.prediction_run_id) && readId(result.model_version))) return false;
  if (value.status === "SUCCEEDED" && result === null) return false;
  return value.lastError === null || readObject(value.lastError) && text(value.lastError.code) && typeof value.lastError.retryable === "boolean";
}


/** Current sealed/server-owned session readback; persona storage is not this authority. */
export { hasCanonicalScoreSession } from "../operatorSessionValidation";

export function hasScoreExecutionCommandReceipt(value: unknown): value is import("./networkScoringTypes").ScoreExecutionCommandReceipt {
  return readObject(value) && oneOf(value.operation, ["create", "retry"])
    && [value.actorSubjectId, value.tenantId, value.idempotencyKey, value.correlationId,
      value.executionRequestId, value.jobId].every(readId);
}

export function scoreExecutionCommandReceiptMatches(
  value: unknown,
  intent: import("./networkScoringTypes").ScoreExecutionCommandIntent,
  request: Pick<import("./networkScoringTypes").ScoreExecutionReceipt, "executionRequestId" | "jobId">,
): boolean {
  return hasScoreExecutionCommandReceipt(value) && value.operation === intent.operation
    && value.actorSubjectId === intent.actorSubjectId && value.tenantId === intent.tenantId
    && value.idempotencyKey === intent.idempotencyKey && value.correlationId === intent.correlationId
    && value.executionRequestId === request.executionRequestId && value.jobId === request.jobId;
}

// Typed pending namespace is emitted only after the backend checks the physical
// review pointer; full canonical Decision/Approval joins remain required.
export function hasCandidateReviewSubmission(value: unknown): value is CandidateReviewSubmissionReference {
  return readObject(value) && ["decisionId", "approvalId", "reportId", "tenantId", "submittedBy", "submissionSummary", "reviewerGroupId"].every((key) => readId(value[key]))
    && value.decisionId !== value.approvalId && typeof value.reportVersion === "number" && Number.isSafeInteger(value.reportVersion) && value.reportVersion > 0 && typeof value.decisionVersion === "number" && Number.isSafeInteger(value.decisionVersion) && value.decisionVersion > 0
    && readId(value.originalCreatorSubjectId) && typeof value.originalCreatorVerified === "boolean"
    && readId(value.latestReportId) && typeof value.latestReportVersion === "number" && Number.isSafeInteger(value.latestReportVersion) && value.latestReportVersion > 0;
}
