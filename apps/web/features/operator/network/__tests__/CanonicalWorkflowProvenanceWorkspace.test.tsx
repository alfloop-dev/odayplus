import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { writeFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";
import { ExpansionStepper } from "../ExpansionStepper";
import type { NetworkShell } from "../NetworkShell";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}


const nav = vi.hoisted(() => ({ search: "ws=network&tab=candidates&candidate=ui03-second", push: vi.fn() }));
const capture = vi.hoisted(() => ({ props: null as ComponentProps<typeof NetworkShell> | null }));
vi.mock("next/navigation", () => ({ usePathname: () => "/operator", useSearchParams: () => new URLSearchParams(nav.search), useRouter: () => ({ push: nav.push }) }));
vi.mock("../NetworkShell", async (original) => {
  const actual = await original<typeof import("../NetworkShell")>();
  return { ...actual, NetworkShell: (props: ComponentProps<typeof NetworkShell>) => {
    capture.props = props;
    return <actual.NetworkShell {...props} />;
  } };
});
vi.mock("../intake/AssistedIntakeSection", async (original) => ({ ...await original<typeof import("../intake/AssistedIntakeSection")>(), AssistedIntakeSection: () => <div /> }));

// Captured unmodified actual ba1 producer outputs from a fresh, isolated durable
// SQLite store and actual scope projection. No inference or actual role grant.
// Export recipe and exact hashes are retained in the UI03 evidence handoff.
const producer = {"canonical":{"network":{"assistedIntakes":[],"auditEvents":[],"candidates":[{"address":"","datasetSnapshotId":null,"feasibilityFlags":[],"featureSnapshotTime":null,"geocodeConfidence":0.9,"h3Index":null,"heatZoneId":"synthetic-zone","id":"ui03-first","latitude":25.03,"listingId":"listing-ui03-first","longitude":121.56,"missingData":[],"modelVersion":"stale-cached-model","prior90dCellNetRevenue":null,"prior90dCellStoreCount":null,"prior90dCellTransactionCount":null,"recommendation":"GO","reviewId":null,"score":99,"status":"new","targetFormatCode":"synthetic-format","title":"listing-ui03-first 候選點"},{"address":"","datasetSnapshotId":null,"feasibilityFlags":[],"featureSnapshotTime":null,"geocodeConfidence":0.9,"h3Index":null,"heatZoneId":"synthetic-zone","id":"ui03-second","latitude":25.03,"listingId":"listing-ui03-second","longitude":121.56,"missingData":[],"modelVersion":"stale-cached-model","prior90dCellNetRevenue":null,"prior90dCellStoreCount":null,"prior90dCellTransactionCount":null,"recommendation":"GO","reviewId":null,"score":99,"status":"new","targetFormatCode":"synthetic-format","title":"listing-ui03-second 候選點"}],"correlationId":null,"counts":{"assistedIntakes":0,"candidates":2,"heatZones":0,"listings":2,"siteReviews":0},"expansionSteps":[{"entityId":null,"id":"find","label":"Find Area","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":0},{"entityId":null,"id":"radar","label":"Listing Radar","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":1},{"entityId":null,"id":"candidate","label":"Candidate","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":2},{"entityId":null,"id":"sitescore","label":"SiteScore","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":3},{"entityId":null,"id":"compare","label":"Compare","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":4},{"entityId":null,"id":"review","label":"Review","state":"blocked","summary":"Select an exact candidate context; progress requires current authorized listing, report and review reads.","tabIndex":5}],"heatZones":[],"listingSources":[],"listings":[{"address":"Synthetic ui03-first","addressId":"address-ui03-first","areaPing":24,"floor":"1F","frontageMeters":0.0,"geocodeConfidence":0.9,"h3Index":"","hardRuleFailures":null,"id":"listing-ui03-first","latitude":25.03,"listingConfidence":null,"listingConfidenceProvenance":"unmeasured","longitude":121.56,"rentPerMonth":40000,"snapshotId":"current-input-ui03-first","sourceId":"","sourceListingId":"","sourceUrl":"current-input-ui03-first","status":"active","tenantId":"ui03-local-synthetic-tenant"},{"address":"Synthetic ui03-second","addressId":"address-ui03-second","areaPing":24,"floor":"1F","frontageMeters":0.0,"geocodeConfidence":0.9,"h3Index":"","hardRuleFailures":null,"id":"listing-ui03-second","latitude":25.03,"listingConfidence":null,"listingConfidenceProvenance":"unmeasured","longitude":121.56,"rentPerMonth":40000,"snapshotId":"current-input-ui03-second","sourceId":"","sourceListingId":"","sourceUrl":"current-input-ui03-second","status":"active","tenantId":"ui03-local-synthetic-tenant"}],"selectedHeatZoneId":null,"selectedLens":"demand","siteReviews":[],"source":"api"},"scopedNetwork":{"assistedIntakes":[],"auditEvents":[],"candidates":[{"address":"","datasetSnapshotId":null,"feasibilityFlags":[],"featureSnapshotTime":null,"geocodeConfidence":0.9,"h3Index":null,"heatZoneId":"synthetic-zone","id":"ui03-first","latitude":25.03,"listingId":"listing-ui03-first","longitude":121.56,"missingData":[],"modelVersion":"stale-cached-model","prior90dCellNetRevenue":null,"prior90dCellStoreCount":null,"prior90dCellTransactionCount":null,"recommendation":"GO","reviewId":null,"score":99,"status":"new","targetFormatCode":"synthetic-format","title":"listing-ui03-first 候選點"},{"address":"","datasetSnapshotId":null,"feasibilityFlags":[],"featureSnapshotTime":null,"geocodeConfidence":0.9,"h3Index":null,"heatZoneId":"synthetic-zone","id":"ui03-second","latitude":25.03,"listingId":"listing-ui03-second","longitude":121.56,"missingData":[],"modelVersion":"stale-cached-model","prior90dCellNetRevenue":null,"prior90dCellStoreCount":null,"prior90dCellTransactionCount":null,"recommendation":"GO","reviewId":null,"score":99,"status":"new","targetFormatCode":"synthetic-format","title":"listing-ui03-second 候選點"}],"correlationId":null,"counts":{"assistedIntakes":0,"candidates":2,"heatZones":0,"listings":2,"siteReviews":0},"expansionSteps":[],"heatZones":[],"listingSources":[],"listings":[{"address":"Synthetic ui03-first","areaPing":24,"floor":"1F","frontageMeters":0.0,"geocodeConfidence":0.9,"h3Index":"","hardRuleFailures":null,"id":"listing-ui03-first","latitude":25.03,"listingConfidence":null,"listingConfidenceProvenance":"unmeasured","longitude":121.56,"rentPerMonth":40000,"sourceId":"","sourceListingId":"","sourceUrl":"current-input-ui03-first","status":"active","tenantId":"ui03-local-synthetic-tenant"},{"address":"Synthetic ui03-second","areaPing":24,"floor":"1F","frontageMeters":0.0,"geocodeConfidence":0.9,"h3Index":"","hardRuleFailures":null,"id":"listing-ui03-second","latitude":25.03,"listingConfidence":null,"listingConfidenceProvenance":"unmeasured","longitude":121.56,"rentPerMonth":40000,"sourceId":"","sourceListingId":"","sourceUrl":"current-input-ui03-second","status":"active","tenantId":"ui03-local-synthetic-tenant"}],"selectedHeatZoneId":null,"selectedLens":"demand","siteReviews":[],"source":"api"},"scoring":{"auditEvents":[],"batchResults":[{"cannibalization":0.2,"id":"ui03-first","inCompare":false,"m12P50":150000,"payback":36,"priority":null,"rank":null,"recommendation":"INVESTIGATE","score":null,"title":"ui03-first"},{"cannibalization":0.2,"id":"ui03-second","inCompare":false,"m12P50":150000,"payback":36,"priority":null,"rank":null,"recommendation":"INVESTIGATE","score":null,"title":"ui03-second"}],"candidates":[{"address":"Synthetic ui03-first","currentAdmission":{"addressId":"address-ui03-first","candidateId":"ui03-first","checkedAt":null,"currentInputs":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"},"listingId":"listing-ui03-first","model":{"reasonCode":"SITESCORE_CURRENT_MODEL_ADMISSION_UNVERIFIED","state":"unverified"},"pit":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"},"source":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"}},"datasetSnapshotId":"current-input-ui03-first","district":"","gate":{"blockNote":"硬規則判定未取得或無法驗證 — 需補充現行已記錄判定後才能評分","checks":[{"key":"address","label":"地址定位","note":"","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"","state":"ok"},{"key":"rent","label":"租金","note":"","state":"ok"},{"key":"area","label":"坪數","note":"","state":"ok"},{"key":"floor","label":"樓層","note":"","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"未記錄硬規則判定","state":"fail"}],"missing":["Hard rule"],"okCount":5,"otherMissing":[],"passed":false,"state":"hard","totalCount":6},"heatZoneId":"synthetic-zone","id":"ui03-first","inCompare":false,"listingId":"listing-ui03-first","modelVersion":"synthetic-retained-model-ui03-first","readEvidence":{"addressId":"address-ui03-first","candidateId":"ui03-first","datasetSnapshotId":"current-input-ui03-first","fields":{"address":{"availability":"recorded","provenance":{"fieldPath":"normalized_address","recordId":"address-ui03-first","recordType":"AddressLocation","snapshotId":null},"reason":null,"unit":null,"value":"Synthetic ui03-first"},"area":{"availability":"recorded","provenance":{"fieldPath":"area_ping","recordId":"listing-ui03-first","recordType":"Listing","snapshotId":"current-input-ui03-first"},"reason":null,"unit":"ping","value":24},"floor":{"availability":"recorded","provenance":{"fieldPath":"floor","recordId":"listing-ui03-first","recordType":"Listing","snapshotId":"current-input-ui03-first"},"reason":null,"unit":null,"value":"1F"},"geocode":{"availability":"recorded","provenance":{"fieldPath":"geocode_confidence","recordId":"address-ui03-first","recordType":"AddressLocation","snapshotId":null},"reason":null,"unit":"ratio","value":0.9},"hardRule":{"availability":"unmeasured","provenance":{"fieldPath":"hard_rule_failures","recordId":"listing-ui03-first","recordType":"Listing","snapshotId":"current-input-ui03-first"},"reason":"UNMEASURED","unit":null,"value":null},"rent":{"availability":"recorded","provenance":{"fieldPath":"rent_amount","recordId":"listing-ui03-first","recordType":"Listing","snapshotId":"current-input-ui03-first"},"reason":null,"unit":"TWD","value":40000},"sourceConfidence":{"availability":"unmeasured","provenance":{"fieldPath":"confidence","measurementStatus":"unmeasured","recordId":"listing-ui03-first","recordType":"Listing","snapshotId":"current-input-ui03-first"},"reason":"UNMEASURED","unit":"ratio","value":null}},"listingId":"listing-ui03-first","source":"canonical"},"recommendation":"INVESTIGATE","reviewId":null,"score":null,"scored":true,"stage":"evaluated","title":"Synthetic ui03-first","zoneLabel":"synthetic-zone"},{"address":"Synthetic ui03-second","currentAdmission":{"addressId":"address-ui03-second","candidateId":"ui03-second","checkedAt":null,"currentInputs":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"},"listingId":"listing-ui03-second","model":{"reasonCode":"SITESCORE_CURRENT_MODEL_ADMISSION_UNVERIFIED","state":"unverified"},"pit":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"},"source":{"reasonCode":"SITESCORE_CURRENT_CONTEXT_UNAVAILABLE","state":"unverified"}},"datasetSnapshotId":"current-input-ui03-second","district":"","gate":{"blockNote":"硬規則判定未取得或無法驗證 — 需補充現行已記錄判定後才能評分","checks":[{"key":"address","label":"地址定位","note":"","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"","state":"ok"},{"key":"rent","label":"租金","note":"","state":"ok"},{"key":"area","label":"坪數","note":"","state":"ok"},{"key":"floor","label":"樓層","note":"","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"未記錄硬規則判定","state":"fail"}],"missing":["Hard rule"],"okCount":5,"otherMissing":[],"passed":false,"state":"hard","totalCount":6},"heatZoneId":"synthetic-zone","id":"ui03-second","inCompare":false,"listingId":"listing-ui03-second","modelVersion":"synthetic-retained-model-ui03-second","readEvidence":{"addressId":"address-ui03-second","candidateId":"ui03-second","datasetSnapshotId":"current-input-ui03-second","fields":{"address":{"availability":"recorded","provenance":{"fieldPath":"normalized_address","recordId":"address-ui03-second","recordType":"AddressLocation","snapshotId":null},"reason":null,"unit":null,"value":"Synthetic ui03-second"},"area":{"availability":"recorded","provenance":{"fieldPath":"area_ping","recordId":"listing-ui03-second","recordType":"Listing","snapshotId":"current-input-ui03-second"},"reason":null,"unit":"ping","value":24},"floor":{"availability":"recorded","provenance":{"fieldPath":"floor","recordId":"listing-ui03-second","recordType":"Listing","snapshotId":"current-input-ui03-second"},"reason":null,"unit":null,"value":"1F"},"geocode":{"availability":"recorded","provenance":{"fieldPath":"geocode_confidence","recordId":"address-ui03-second","recordType":"AddressLocation","snapshotId":null},"reason":null,"unit":"ratio","value":0.9},"hardRule":{"availability":"unmeasured","provenance":{"fieldPath":"hard_rule_failures","recordId":"listing-ui03-second","recordType":"Listing","snapshotId":"current-input-ui03-second"},"reason":"UNMEASURED","unit":null,"value":null},"rent":{"availability":"recorded","provenance":{"fieldPath":"rent_amount","recordId":"listing-ui03-second","recordType":"Listing","snapshotId":"current-input-ui03-second"},"reason":null,"unit":"TWD","value":40000},"sourceConfidence":{"availability":"unmeasured","provenance":{"fieldPath":"confidence","measurementStatus":"unmeasured","recordId":"listing-ui03-second","recordType":"Listing","snapshotId":"current-input-ui03-second"},"reason":"UNMEASURED","unit":"ratio","value":null}},"listingId":"listing-ui03-second","source":"canonical"},"recommendation":"INVESTIGATE","reviewId":null,"score":null,"scored":true,"stage":"evaluated","title":"Synthetic ui03-second","zoneLabel":"synthetic-zone"}],"compare":{"columns":[],"empty":true,"metrics":[{"key":"sitescore","label":"SiteScore","values":[]},{"key":"m12","label":"M12 P50","values":[]},{"key":"payback","label":"回本期","values":[]},{"key":"cannibalization","label":"自家稀釋","values":[]},{"key":"competition","label":"競店壓力","values":[]},{"key":"rent","label":"租金合理性","values":[]},{"key":"confidence","label":"資料信心","values":[]}],"recommendation":null},"compareSet":[],"correlationId":null,"counts":{"candidates":2,"gateBlocked":2,"scored":2},"modelVersion":"synthetic-retained-model-ui03-first","scorecards":[{"band":{"p10":100000,"p50":150000,"p90":200000},"capex":null,"conditionTitle":"","conditions":[],"confidence":0.91,"confidenceStatus":"measured","datasetSnapshotId":"historical-report-input-ui03-first","drivers":[],"generatedAt":"2026-10-13T00:00:00+00:00","heatZoneId":"","id":"ui03-first","modelVersion":"synthetic-retained-model-ui03-first","payback":36,"reasons":[],"recommendation":"INVESTIGATE","rentAssumption":null,"reportId":"sitescore-report-da9eacac-f17c-4027-9f4b-adf3d3194a8b","reportVersion":1,"revenuePath":{"m1":{"p10":100000,"p50":150000,"p90":200000},"m12":{"p10":100000,"p50":150000,"p90":200000},"m3":{"p10":100000,"p50":150000,"p90":200000},"m6":{"p10":100000,"p50":150000,"p90":200000}},"reviewId":null,"risks":[],"score":null,"sitescoreRunId":"run-ui03-first-1","subScores":{"cannibalization":0.2,"rentReasonableness":0.6},"title":"ui03-first","zoneLabel":""},{"band":{"p10":100000,"p50":150000,"p90":200000},"capex":null,"conditionTitle":"","conditions":[],"confidence":0.91,"confidenceStatus":"measured","datasetSnapshotId":"historical-report-input-ui03-second","drivers":[],"generatedAt":"2026-10-13T00:00:00+00:00","heatZoneId":"","id":"ui03-second","modelVersion":"synthetic-retained-model-ui03-second","payback":36,"reasons":[],"recommendation":"INVESTIGATE","rentAssumption":null,"reportId":"sitescore-report-ebacd4f5-1f7f-44f5-8338-b2cfbb686bc9","reportVersion":1,"revenuePath":{"m1":{"p10":100000,"p50":150000,"p90":200000},"m12":{"p10":100000,"p50":150000,"p90":200000},"m3":{"p10":100000,"p50":150000,"p90":200000},"m6":{"p10":100000,"p50":150000,"p90":200000}},"reviewId":null,"risks":[],"score":null,"sitescoreRunId":"run-ui03-second-1","subScores":{"cannibalization":0.2,"rentReasonableness":0.6},"title":"ui03-second","zoneLabel":""}],"source":"canonical"}},"legacy":{"network":{"assistedIntakes":[],"auditEvents":[],"candidates":[],"correlationId":null,"counts":{"assistedIntakes":0,"candidates":0,"heatZones":2,"listings":4,"siteReviews":0},"expansionSteps":[{"entityId":"HZ-01","id":"find","label":"Find Area","state":"completed","summary":"HZ-01 selected and synchronized to the map.","tabIndex":0},{"entityId":"L-2024","id":"radar","label":"Listing Radar","state":"current","summary":"L-2024 clean; duplicate and hard-rule rows visible.","tabIndex":1},{"entityId":"L-2024","id":"candidate","label":"Candidate","state":"next","summary":"Convert L-2024 to create CS-1001.","tabIndex":2},{"entityId":"CS-1001","id":"sitescore","label":"SiteScore","state":"blocked","summary":"Blocked until candidate exists.","tabIndex":3},{"entityId":"CS-1001","id":"compare","label":"Compare","state":"blocked","summary":"Blocked by missing candidate.","tabIndex":4},{"entityId":null,"id":"review","label":"Review","state":"blocked","summary":"No candidate review packet yet.","tabIndex":5}],"heatZones":[{"cannibalizationRisk":"medium","centroid":[121.5668,25.033],"competitionIndex":0.34,"confidence":0.93,"demandGap":0.86,"id":"HZ-01","label":"信義松仁生活圈","nextStep":"Convert L-2024 into CS-1001 and continue SiteScore evidence review.","rank":1,"reasons":["Office-lunch and late-night demand gap remains high.","Transit and residential POI mix supports ODAY_G2 format.","L-2024 has clean geocode, floor, area, and source evidence."],"recommendedLens":"demand","rentBand":"NT$52k-88k/month","risks":["Rent negotiation pressure","Nearby competitor 220m away"]},{"cannibalizationRisk":"medium","centroid":[121.4575,25.01],"competitionIndex":0.43,"confidence":0.88,"demandGap":0.78,"id":"HZ-02","label":"板橋府中生活圈","nextStep":"Keep L-2025 active after merging L-2029 duplicate source evidence.","rank":2,"reasons":["Weekend service demand is under-realized.","L-2025 has duplicate-source coverage after merge."],"recommendedLens":"fit","rentBand":"NT$48k-72k/month","risks":["Duplicate broker postings require evidence merge before review"]}],"listingSources":[{"complianceNote":"R4 proof uses licensed/manual intake fields and preserves source evidence refs.","id":"SRC-591","lastSyncedAt":"2026-07-14T06:10:00Z","name":"591 licensed broker intake","status":"connected"},{"complianceNote":"Broker notes are retained as source evidence before any merge/archive action.","id":"SRC-BROKER","lastSyncedAt":"2026-07-14T06:12:00Z","name":"Broker confirmation","status":"manualOnly"}],"listings":[{"address":"台北市信義區松仁路 96 號 1F","areaPing":18,"firstSeenAt":"2026-07-14T06:10:00Z","fitScore":72,"floor":"1F 臨路","frontageMeters":6,"geocodeConfidence":0.94,"hardRuleFailures":[],"hardRuleSummary":"3/3 pass: area, floor, permitted use","heatZoneId":"HZ-01","id":"L-2024","rentPerMonth":58000,"sourceEvidence":["EV-L-2024-RAW-591","EV-L-2024-GEOCODE","EV-L-2024-BROKER-CALL"],"sourceId":"SRC-591","sourceListingId":"s591-2024","sourceUrl":"https://example.invalid/listings/L-2024","status":"new","tenantId":"tenant-a"},{"address":"新北市板橋區府中路 52 號 1F","areaPing":22,"firstSeenAt":"2026-07-13T09:25:00Z","fitScore":70,"floor":"1F","frontageMeters":5,"geocodeConfidence":0.93,"hardRuleFailures":[],"hardRuleSummary":"3/3 pass: area, floor, permitted use","heatZoneId":"HZ-02","id":"L-2025","rentPerMonth":53000,"sourceEvidence":["EV-L-2025-BROKER","EV-L-2025-GEOCODE"],"sourceId":"SRC-BROKER","sourceListingId":"broker-2025","sourceUrl":"https://example.invalid/listings/L-2025","status":"watching","tenantId":"tenant-a"},{"address":"新北市板橋區府中路 52 號 1F","areaPing":22,"duplicateOfId":"L-2025","firstSeenAt":"2026-07-14T06:10:00Z","fitScore":70,"floor":"1F","frontageMeters":5,"geocodeConfidence":0.93,"hardRuleFailures":[],"hardRuleSummary":"3/3 pass, duplicate same-address source","heatZoneId":"HZ-02","id":"L-2029","rentPerMonth":53000,"sourceEvidence":["EV-L-2029-RAW-591","EV-L-2029-ADDRESS-MATCH","EV-L-2029-RENT-MATCH"],"sourceId":"SRC-591","sourceListingId":"s591-2029","sourceUrl":"https://example.invalid/listings/L-2029","status":"duplicate","tenantId":"tenant-a"},{"address":"台北市信義區松仁路 110 號 1-2F","areaPing":40,"firstSeenAt":"2026-07-14T06:10:00Z","fitScore":44,"floor":"1-2F","frontageMeters":8,"geocodeConfidence":0.95,"hardRuleFailures":["area_above_format_maximum","floor_not_ground_level"],"hardRuleSummary":"2/3 blocked: area above 30 ping and second-floor operations required","heatZoneId":"HZ-01","id":"L-2030","rentPerMonth":128000,"sourceEvidence":["EV-L-2030-RAW-591","EV-L-2030-HARD-RULES"],"sourceId":"SRC-591","sourceListingId":"s591-2030","sourceUrl":"https://example.invalid/listings/L-2030","status":"hardfail","tenantId":"tenant-a"}],"selectedHeatZoneId":"HZ-01","selectedLens":"demand","siteReviews":[],"source":"api"},"scoring":{"auditEvents":[],"batchResults":[{"cannibalization":"4%（低）","id":"CS-1001","inCompare":true,"m12P50":"NT$428K","payback":"22 個月","priority":"P1","rank":1,"recommendation":"GO","score":82,"title":"信義松仁候選點"},{"cannibalization":"11%（中）","id":"CS-1002","inCompare":true,"m12P50":"NT$372K","payback":"27 個月","priority":"P2","rank":2,"recommendation":"WAIT","score":76,"title":"板橋府中候選點"},{"cannibalization":"12%（中高）","id":"CS-1004","inCompare":true,"m12P50":"NT$268K","payback":"41 個月","priority":"P3","rank":3,"recommendation":"REJECT","score":49,"title":"大安和平候選點"}],"candidates":[{"address":"台北市信義區松仁路 96 號 1F","datasetSnapshotId":"FS-20260704-0600","district":"台北市信義區","gate":{"blockNote":"","checks":[{"key":"address","label":"地址定位","note":"台北市信義區松仁路 96 號 1F","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"0.94（高）","state":"ok"},{"key":"rent","label":"租金","note":"NT$58,000／月（區間 P45）","state":"ok"},{"key":"area","label":"坪數","note":"18 坪","state":"ok"},{"key":"floor","label":"樓層","note":"1F 臨路","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"3/3 通過（面積／樓層／用途）","state":"ok"}],"missing":[],"okCount":6,"otherMissing":[],"passed":true,"state":"ready","totalCount":6},"heatZoneId":"HZ-01","id":"CS-1001","inCompare":true,"listingId":"L-2024","modelVersion":"SiteScore v2.3","recommendation":"GO","reviewId":null,"score":82,"scored":true,"stage":"evaluated","title":"信義松仁候選點","zoneLabel":"信義松仁 86"},{"address":"新北市板橋區府中路 XX 號 1F","datasetSnapshotId":"FS-20260703-0600","district":"新北市板橋區","gate":{"blockNote":"","checks":[{"key":"address","label":"地址定位","note":"新北市板橋區府中路 XX 號 1F","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"0.92（高）","state":"ok"},{"key":"rent","label":"租金","note":"NT$52,000／月（區間 P70）","state":"ok"},{"key":"area","label":"坪數","note":"22 坪","state":"ok"},{"key":"floor","label":"樓層","note":"1F 近捷運出口","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"3/3 通過","state":"ok"}],"missing":[],"okCount":6,"otherMissing":[],"passed":true,"state":"ready","totalCount":6},"heatZoneId":"HZ-02","id":"CS-1002","inCompare":true,"listingId":"L-2025","modelVersion":"SiteScore v2.3","recommendation":"WAIT","reviewId":"RV-701","score":76,"scored":true,"stage":"pendingreview","title":"板橋府中候選點","zoneLabel":"板橋府中 78"},{"address":"中壢區中北路 XX 號 1F","datasetSnapshotId":"FS-20260704-0600","district":"桃園市中壢區","gate":{"blockNote":"地址／Geocode 未確認 — 需人工確認地址後才能評分","checks":[{"key":"address","label":"地址定位","note":"中壢區中北路 XX 號 1F（待人工確認）","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"0.71（中）— 待人工確認","state":"fail"},{"key":"rent","label":"租金","note":"NT$36,000／月（區間 P25）","state":"ok"},{"key":"area","label":"坪數","note":"16 坪","state":"ok"},{"key":"floor","label":"樓層","note":"1F","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"3/3 通過","state":"ok"}],"missing":["Geocode ≥ 0.80","平日人流樣本","房東租期意願","地址人工確認"],"okCount":5,"otherMissing":["平日人流樣本","房東租期意願","地址人工確認"],"passed":false,"state":"geo","totalCount":6},"heatZoneId":"HZ-05","id":"CS-1003","inCompare":false,"listingId":"L-2026","modelVersion":"SiteScore v2.3","recommendation":null,"reviewId":null,"score":null,"scored":false,"stage":"needdata","title":"中壢中原候選點","zoneLabel":"中壢中原 69"},{"address":"大安區和平東路二段 XX 號 1F","datasetSnapshotId":"FS-20260630-0600","district":"台北市大安區","gate":{"blockNote":"","checks":[{"key":"address","label":"地址定位","note":"大安區和平東路二段 XX 號 1F","state":"ok"},{"key":"geocode","label":"Geocode ≥ 0.80","note":"0.90（高）","state":"ok"},{"key":"rent","label":"租金","note":"NT$64,000／月（區間 P90）","state":"ok"},{"key":"area","label":"坪數","note":"20 坪","state":"ok"},{"key":"floor","label":"樓層","note":"1F","state":"ok"},{"key":"hardRule","label":"Hard rule","note":"3/3 通過","state":"ok"}],"missing":[],"okCount":6,"otherMissing":[],"passed":true,"state":"ready","totalCount":6},"heatZoneId":"HZ-03","id":"CS-1004","inCompare":true,"listingId":"L-2027","modelVersion":"SiteScore v2.3","recommendation":"REJECT","reviewId":"RV-698","score":49,"scored":true,"stage":"rejected","title":"大安和平候選點","zoneLabel":"大安和平 74"}],"compare":{"columns":[{"id":"CS-1001","isBest":true,"priority":"P1","recommendation":"GO","score":82,"title":"信義松仁候選點"},{"id":"CS-1002","isBest":false,"priority":"P2","recommendation":"WAIT","score":76,"title":"板橋府中候選點"},{"id":"CS-1004","isBest":false,"priority":"P3","recommendation":"REJECT","score":49,"title":"大安和平候選點"}],"empty":false,"metrics":[{"key":"sitescore","label":"SiteScore","values":[{"id":"CS-1001","isBest":true,"text":"82 GO"},{"id":"CS-1002","isBest":false,"text":"76 WAIT"},{"id":"CS-1004","isBest":false,"text":"49 REJECT"}]},{"key":"m12","label":"M12 P50","values":[{"id":"CS-1001","isBest":true,"text":"NT$428K"},{"id":"CS-1002","isBest":false,"text":"NT$372K"},{"id":"CS-1004","isBest":false,"text":"NT$268K"}]},{"key":"payback","label":"回本期","values":[{"id":"CS-1001","isBest":true,"text":"22 個月"},{"id":"CS-1002","isBest":false,"text":"27 個月"},{"id":"CS-1004","isBest":false,"text":"41 個月"}]},{"key":"cannibalization","label":"自家稀釋","values":[{"id":"CS-1001","isBest":true,"text":"4%（低）"},{"id":"CS-1002","isBest":false,"text":"11%（中）"},{"id":"CS-1004","isBest":false,"text":"12%（中高）"}]},{"key":"competition","label":"競店壓力","values":[{"id":"CS-1001","isBest":true,"text":"中（無 24h 競店）"},{"id":"CS-1002","isBest":false,"text":"高（150m 強競店）"},{"id":"CS-1004","isBest":false,"text":"中"}]},{"key":"rent","label":"租金合理性","values":[{"id":"CS-1001","isBest":true,"text":"合理（區間 P45）"},{"id":"CS-1002","isBest":false,"text":"偏高（區間 P70）"},{"id":"CS-1004","isBest":false,"text":"過高（區間 P90）"}]},{"key":"confidence","label":"資料信心","values":[{"id":"CS-1001","isBest":true,"text":"中高"},{"id":"CS-1002","isBest":false,"text":"中"},{"id":"CS-1004","isBest":false,"text":"中高"}]}],"recommendation":{"alternate":{"id":"CS-1002","recommendation":"WAIT","score":76,"text":"備選：板橋府中候選點（WAIT 76）　— 條件改善後重評","title":"板橋府中候選點"},"avoid":{"id":"CS-1004","recommendation":"REJECT","score":49,"text":"不建議：大安和平候選點 — 租金過高（P90），回本期 41 個月超出品牌上限 30 個月","title":"大安和平候選點"},"primary":{"id":"CS-1001","recommendation":"GO","score":82,"text":"推薦：信義松仁候選點（GO 82，回本 22 個月）","title":"信義松仁候選點","why":["SiteScore 82，為目前最高分","M12 P50 NT$428K／月","回本期 22 個月","自家稀釋 4%（低）","租金可行性：合理（區間 P45）"]},"priorityList":[{"id":"CS-1001","priority":"P1","recommendation":"GO","score":82,"title":"信義松仁候選點"},{"id":"CS-1002","priority":"P2","recommendation":"WAIT","score":76,"title":"板橋府中候選點"},{"id":"CS-1004","priority":"P3","recommendation":"REJECT","score":49,"title":"大安和平候選點"}]}},"compareSet":["CS-1001","CS-1002","CS-1004"],"correlationId":null,"counts":{"candidates":4,"gateBlocked":1,"scored":3},"modelVersion":"SiteScore v2.3","scorecards":[{"band":{"p10":"NT$356K","p50":"NT$428K","p90":"NT$512K"},"capex":"NT$1.6M（14 台配置）","conditionTitle":"","conditions":[],"confidence":"中高","datasetSnapshotId":"FS-20260704-0600","drivers":["夜間人流指數 84","千戶級社區 6 個","無 24h 競店"],"generatedAt":"2026-07-04 06:10","heatZoneId":"HZ-01","id":"CS-1001","modelVersion":"SiteScore v2.3","payback":"22 個月","reasons":["住宅＋商辦混合，夜間洗烘需求強","既有店 650m，稀釋僅 4%","降租後租金落於區間 P45，仍可接受"],"recommendation":"GO","rentAssumption":"月租 NT$58,000（區間 P45）","revenuePath":{"m1":182,"m12":428,"m3":268,"m6":342},"reviewId":null,"risks":["週末停車不易","租約含 3% 年調條款","晚間人流資料信心中等"],"score":82,"subScores":{"access":"中（週末停車不易）","cannibalization":"4%（低）","competition":"中（無 24h 競店）","demand":"強（夜間人流 84）","poiFit":"高","rentReasonableness":"合理（區間 P45）"},"title":"信義松仁候選點","zoneLabel":"信義松仁 86"},{"band":{"p10":"NT$308K","p50":"NT$372K","p90":"NT$431K"},"capex":"NT$1.8M（16 台配置）","conditionTitle":"通過條件 — 符合後可重評為 GO","conditions":["站前施工影響需於 Q4 前複評","租金議價至 NT$48,000 以下","補充晚間人流資料"],"confidence":"中","datasetSnapshotId":"FS-20260703-0600","drivers":["捷運通勤人流","舊公寓無烘衣機比例高"],"generatedAt":"2026-07-13 16:42","heatZoneId":"HZ-02","id":"CS-1002","modelVersion":"SiteScore v2.3","payback":"27 個月","reasons":["人流量體大","夜間治安佳","距捷運出口 80m，通勤動線佳"],"recommendation":"WAIT","rentAssumption":"月租 NT$52,000（區間 P70）","revenuePath":{"m1":142,"m12":372,"m3":221,"m6":289},"reviewId":"RV-701","risks":["站前施工至 12 月","與府中店重疊 11%","晚間人流資料信心中等"],"score":76,"subScores":{"access":"中（站前施工圍籬）","cannibalization":"11%（中）","competition":"高（150m 強競店）","demand":"中高（通勤指數 82）","poiFit":"高（捷運＋學校＋超商 5）","rentReasonableness":"偏高（區間 P70）"},"title":"板橋府中候選點","zoneLabel":"板橋府中 78"},{"band":{"p10":"NT$214K","p50":"NT$268K","p90":"NT$322K"},"capex":"NT$1.7M（14 台配置）","conditionTitle":"拒絕原因","conditions":["租金過高（P90），回本期 41 個月超出品牌上限 30 個月","與大安和平店服務圈重疊 12%，自家稀釋高","面寬與停車條件不足，大件洗客群流失"],"confidence":"中高","datasetSnapshotId":"FS-20260630-0600","drivers":["學區穩定需求"],"generatedAt":"2026-06-30 14:20","heatZoneId":"HZ-03","id":"CS-1004","modelVersion":"SiteScore v2.3","payback":"41 個月","reasons":["住宅密度高","高齡住戶代收送潛力"],"recommendation":"REJECT","rentAssumption":"月租 NT$64,000（區間 P90）","revenuePath":{"m1":102,"m12":268,"m3":156,"m6":203},"reviewId":"RV-698","risks":["租金 P90，回本期 41 個月","與大安和平店重疊 12%","停車與面寬條件弱"],"score":49,"subScores":{"access":"低（巷弄面寬不足）","cannibalization":"12%（中高）","competition":"中","demand":"中（學區穩定）","poiFit":"中（學校 3 · 傳統市場）","rentReasonableness":"過高（區間 P90）"},"title":"大安和平候選點","zoneLabel":"大安和平 74"}],"source":"api"}}};
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }); }
function reads(scoring: unknown = producer.canonical.scoring, network: unknown = producer.canonical.network) {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => String(input).includes("network-listings?")
    ? json(network) : String(input).includes("network-scoring") ? json(scoring) : json({ detail: "unavailable synthetic read" }, 403));
  vi.stubGlobal("fetch", mock);
  return mock;
}
beforeEach(() => {
  nav.search = "ws=network&tab=candidates&candidate=ui03-second"; nav.push.mockReset(); capture.props = null;
  window.history.replaceState(null, "", `/operator?${nav.search}`); window.sessionStorage.clear();
  vi.stubEnv("ODP_PRODUCT_MODE", "poc"); vi.stubEnv("NEXT_PUBLIC_ODP_PRODUCT_MODE", "poc");
  vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "false");
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("actual producer workflow provenance in the fixture-permitted lane", () => {
  it.each(["network", "scopedNetwork"] as const)("cold-opens the exact second retained report through actual %s reads and native SSR", async (networkKey) => {
    const fetchMock = reads(producer.canonical.scoring, producer.canonical[networkKey]);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-ui03-second");
    await waitFor(() => expect(capture.props).not.toBeNull());
    const props = capture.props!;
    const html = renderToStaticMarkup(<ExpansionStepper activeTab={props.activeTab} onStepSelect={() => undefined}
      steps={props.steps} workflowNotice={props.workflowNotice} workflowNextAction={props.workflowNextAction} />);
    if (process.env.UI03_EVIDENCE_DIR) writeFileSync(`${process.env.UI03_EVIDENCE_DIR}/cold-${networkKey}-native-ssr.html`, html);
    const detached = document.createElement("div"); detached.innerHTML = html;
    expect(detached.querySelector('[data-testid="network-step-sitescore"]')).toHaveAttribute("data-state", "completed");
    expect(detached.querySelector('[data-testid="network-step-sitescore"]')).toHaveAttribute("title", expect.stringContaining(producer.canonical.scoring.scorecards.find((row) => row.id === "ui03-second")!.reportId));
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("ui03-second");
    expect(screen.getByTestId("network-workflow-context-notice")).toHaveTextContent("再評分資格");
    expect(screen.queryByText("下一步：執行 SiteScore")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  });

  it("keeps the identical actual canonical producer strict in production", async () => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true"); reads();
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("ui03-second");
  });

  it("uses canonical native projection in the explicit isolated E2E lane of a production-mode bundle", async () => {
    vi.stubEnv("NODE_ENV", "production"); vi.stubEnv("ODP_DEPLOY_ENV", "e2e"); vi.stubEnv("ODP_E2E_MODE", "true");
    vi.stubEnv("ODP_REQUIRE_LIVE_DATA", "false"); reads();
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("ui03-second");
  });

  it("keeps canonical producer compatibility when optional evidence fields are absent", async () => {
    const candidates = producer.canonical.scoring.candidates.map(({ readEvidence: _readEvidence, currentAdmission: _currentAdmission, ...row }) => row);
    reads({ ...producer.canonical.scoring, candidates });
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("network-step-sitescore")).toHaveTextContent("ui03-second");
  });

  it.each(["evidence-candidate", "evidence-listing", "admission-candidate", "admission-listing", "admission-address", "admission-null"])("keeps supplied canonical metadata with %s association unknown", async (change) => {
    const candidates = producer.canonical.scoring.candidates.map((row) => row.id !== "ui03-second" ? row : {
      ...row,
      readEvidence: { ...row.readEvidence,
        candidateId: change === "evidence-candidate" ? "ui03-first" : row.readEvidence.candidateId,
        listingId: change === "evidence-listing" ? "listing-ui03-first" : row.readEvidence.listingId },
      currentAdmission: change === "admission-null" ? null : { ...row.currentAdmission,
        candidateId: change === "admission-candidate" ? "ui03-first" : row.currentAdmission.candidateId,
        listingId: change === "admission-listing" ? "listing-ui03-first" : row.currentAdmission.listingId,
        addressId: change === "admission-address" ? "address-ui03-first" : row.currentAdmission.addressId },
    });
    reads({ ...producer.canonical.scoring, candidates }, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
  });

  it.each(["readEvidence", "currentAdmission", "reportId", "reportVersion", "sitescoreRunId"])("does not interpret explicit null %s as a genuine legacy fixture", async (field) => {
    const legacy = producer.legacy.scoring;
    const candidates = ["readEvidence", "currentAdmission"].includes(field)
      ? legacy.candidates.map((row) => ({ ...row, [field]: null })) : legacy.candidates;
    const scorecards = ["reportId", "reportVersion", "sitescoreRunId"].includes(field)
      ? legacy.scorecards.map((row) => ({ ...row, [field]: null })) : legacy.scorecards;
    nav.search = "ws=network&tab=candidates&candidate=CS-1001";
    reads({ ...legacy, candidates, scorecards }, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
  });

  it("does not treat API source with canonical lineage as verified canonical or legacy fixture progress", async () => {
    reads({ ...producer.canonical.scoring, source: "api" }, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
  });

  it("keeps a current candidate with a missing own retained report separate from another candidate's report", async () => {
    reads({ ...producer.canonical.scoring, scorecards: producer.canonical.scoring.scorecards.filter((row) => row.id === "ui03-first") });
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-ui03-second");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("title", expect.stringContaining(producer.canonical.scoring.scorecards[0].reportId));
  });

  it("keeps URL cold reload, report tab and history navigation attached to the selected immutable report", async () => {
    reads(); const first = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-ui03-second"); first.unmount();
    nav.search = "ws=network&tab=score&candidate=ui03-second";
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("sitescore-card-ui03-second")).toBeVisible();
    expect(screen.getByTestId("sitescore-card-ui03-first")).not.toBeVisible();
    nav.search = "ws=network&tab=score&candidate=ui03-first";
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    expect(screen.getByTestId("sitescore-card-ui03-first")).toBeVisible();
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("title", expect.stringContaining(producer.canonical.scoring.scorecards.find((row) => row.id === "ui03-first")!.reportId));
  });

  it.each([0, true, null])("does not claim a retained report with malformed version %s", async (reportVersion) => {
    reads({ ...producer.canonical.scoring, scorecards: producer.canonical.scoring.scorecards.map((row) => row.id === "ui03-second" ? { ...row, reportVersion } : row) });
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-ui03-second");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
  });

  it.each([403, 500])("does not assume the legacy fixture workflow before any %s scoring admission", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings?")
      ? json(producer.legacy.network) : json({ detail: "unverified initial read" }, status)));
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
  });

  it("keeps the historical report available with an actual candidate failure status without claiming retry admission", async () => {
    const network = { ...producer.canonical.network, candidates: producer.canonical.network.candidates.map((row) => ({ ...row, status: "SCORING_FAILED" })) };
    reads(producer.canonical.scoring, network); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "completed"));
    expect(screen.getByTestId("network-workflow-context-notice")).toHaveTextContent("不代表目前工作成功或可以重試");
  });

  it("preserves all six original steps from the genuine legacy fixture producer", async () => {
    nav.search = "ws=network&tab=candidates&candidate=CS-1001";
    reads(producer.legacy.scoring, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-CS-1001");
    expect(capture.props!.steps).toEqual(producer.legacy.network.expansionSteps);
    expect(screen.queryByTestId("network-workflow-context-notice")).not.toBeInTheDocument();
  });

  it("cannot carry old legacy fixture steps across a new role's failed scoring read", async () => {
    nav.search = "ws=network&tab=candidates&candidate=CS-1001";
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings?")
      ? json(producer.legacy.network) : String(input).includes("network-scoring")
        ? ++calls === 1 ? json(producer.legacy.scoring) : json({ detail: "new role denied" }, 403)
        : json({ detail: "unavailable" }, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("candidate-detail-score-CS-1001");
    expect(capture.props!.steps).toEqual(producer.legacy.network.expansionSteps);
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(calls).toBe(2));
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
  });

  it.each(["ws=network&tab=candidates", "ws=network&tab=score&candidate=ui03-first&candidate=ui03-second", "ws=network&tab=candidates&candidate=unrelated"])("does not substitute the first report for ambiguous selection %s", async (search) => {
    nav.search = search; reads(); render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-sitescore")).not.toHaveAttribute("data-state", "completed");
    expect(screen.getByTestId("network-step-radar")).toBeEnabled();
    expect(screen.getByTestId("network-tab-0")).toBeEnabled();
  });

  it("keeps a malformed canonical response unknown instead of falling back to fixture progress", async () => {
    reads({ ...producer.canonical.scoring, candidates: [null] }, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
  });

  it("keeps empty canonical reads neutral while preserving ordinary tab navigation", async () => {
    reads({ ...producer.canonical.scoring, candidates: [], scorecards: [] }, producer.legacy.network);
    render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await screen.findByTestId("network-workflow-context-notice");
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
    fireEvent.click(screen.getByTestId("network-tab-1")); expect(nav.push).toHaveBeenCalled();
  });

  it("does not reuse canonical progress or legacy placeholder progress after a role read is denied", async () => {
    let scoringCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings?")
      ? json(producer.legacy.network) : String(input).includes("network-scoring")
        ? ++scoringCalls === 1 ? json(producer.canonical.scoring) : json({ detail: "new scope denied" }, 403)
        : json({ detail: "unavailable" }, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(screen.getByTestId("network-workflow-context-notice")).toBeInTheDocument());
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await waitFor(() => expect(scoringCalls).toBe(2));
    expect(screen.getByTestId("network-step-sitescore")).toHaveAttribute("data-state", "blocked");
    expect(screen.getByTestId("network-step-candidate")).toHaveAttribute("data-state", "blocked");
  });

  it("ignores an abandoned role's late canonical response after the new generation reads genuine legacy fixtures", async () => {
    nav.search = "ws=network&tab=candidates&candidate=CS-1001";
    let calls = 0, finish!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).includes("network-listings?")
      ? json(producer.legacy.network) : String(input).includes("network-scoring")
        ? ++calls === 1 ? new Promise<Response>((resolve) => { finish = resolve; }) : json(producer.legacy.scoring)
        : json({ detail: "unavailable" }, 403)));
    const view = render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager" />);
    await waitFor(() => expect(calls).toBe(1));
    view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-staff" />);
    await screen.findByTestId("candidate-detail-score-CS-1001");
    await act(async () => finish(json(producer.canonical.scoring)));
    expect(capture.props!.steps).toEqual(producer.legacy.network.expansionSteps);
    expect(screen.queryByTestId("network-workflow-context-notice")).not.toBeInTheDocument();
  });
});
