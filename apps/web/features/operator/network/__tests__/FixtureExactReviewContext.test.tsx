import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewPanel } from "../ReviewPanel";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace } from "../../NetworkFindAreasWorkspace";
import type { ReviewItem } from "../networkReviewTypes";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return <GrantedReadNetworkWorkspace allowedTabIds={NETWORK_READ_FIXTURE_TABS} {...props} />;
}

const nav=vi.hoisted(()=>({pathname:"/operator",search:"",push:vi.fn()}));
vi.mock("next/navigation",()=>({usePathname:()=>nav.pathname,useSearchParams:()=>new URLSearchParams(nav.search),useRouter:()=>({push:nav.push})}));
// Actual local application fixture JSON. Network candidates were prepared using
// the original E2E's fixture convert setup; reports retain their original scalar
// and absent immutable lineage. This is not production authorization evidence.
const producer={
  "network": {
    "source": "api",
    "heatZones": [
      {
        "id": "HZ-01",
        "label": "信義松仁生活圈",
        "rank": 1,
        "centroid": [
          121.5668,
          25.033
        ],
        "demandGap": 0.86,
        "competitionIndex": 0.34,
        "cannibalizationRisk": "medium",
        "rentBand": "NT$52k-88k/month",
        "confidence": 0.93,
        "recommendedLens": "demand",
        "reasons": [
          "Office-lunch and late-night demand gap remains high.",
          "Transit and residential POI mix supports ODAY_G2 format.",
          "L-2024 has clean geocode, floor, area, and source evidence."
        ],
        "risks": [
          "Rent negotiation pressure",
          "Nearby competitor 220m away"
        ],
        "nextStep": "Convert L-2024 into CS-1001 and continue SiteScore evidence review."
      },
      {
        "id": "HZ-02",
        "label": "板橋府中生活圈",
        "rank": 2,
        "centroid": [
          121.4575,
          25.01
        ],
        "demandGap": 0.78,
        "competitionIndex": 0.43,
        "cannibalizationRisk": "medium",
        "rentBand": "NT$48k-72k/month",
        "confidence": 0.88,
        "recommendedLens": "fit",
        "reasons": [
          "Weekend service demand is under-realized.",
          "L-2025 has duplicate-source coverage after merge."
        ],
        "risks": [
          "Duplicate broker postings require evidence merge before review"
        ],
        "nextStep": "Keep L-2025 active after merging L-2029 duplicate source evidence."
      }
    ],
    "listingSources": [
      {
        "id": "SRC-591",
        "name": "591 licensed broker intake",
        "status": "connected",
        "complianceNote": "R4 proof uses licensed/manual intake fields and preserves source evidence refs.",
        "lastSyncedAt": "2026-07-14T06:10:00Z"
      },
      {
        "id": "SRC-BROKER",
        "name": "Broker confirmation",
        "status": "manualOnly",
        "complianceNote": "Broker notes are retained as source evidence before any merge/archive action.",
        "lastSyncedAt": "2026-07-14T06:12:00Z"
      }
    ],
    "listings": [
      {
        "id": "L-2024",
        "tenantId": "tenant-a",
        "sourceId": "SRC-591",
        "sourceListingId": "s591-2024",
        "heatZoneId": "HZ-01",
        "address": "台北市信義區松仁路 96 號 1F",
        "status": "candidate",
        "rentPerMonth": 58000,
        "areaPing": 18,
        "floor": "1F 臨路",
        "frontageMeters": 6,
        "geocodeConfidence": 0.94,
        "hardRuleFailures": [],
        "hardRuleSummary": "3/3 pass: area, floor, permitted use",
        "sourceEvidence": [
          "EV-L-2024-RAW-591",
          "EV-L-2024-GEOCODE",
          "EV-L-2024-BROKER-CALL"
        ],
        "fitScore": 72,
        "firstSeenAt": "2026-07-14T06:10:00Z",
        "sourceUrl": "https://example.invalid/listings/L-2024",
        "candidateId": "CS-1001",
        "convertedAt": "2026-10-13T12:31:52Z"
      },
      {
        "id": "L-2025",
        "tenantId": "tenant-a",
        "sourceId": "SRC-BROKER",
        "sourceListingId": "broker-2025",
        "heatZoneId": "HZ-02",
        "address": "新北市板橋區府中路 52 號 1F",
        "status": "candidate",
        "rentPerMonth": 53000,
        "areaPing": 22,
        "floor": "1F",
        "frontageMeters": 5,
        "geocodeConfidence": 0.93,
        "hardRuleFailures": [],
        "hardRuleSummary": "3/3 pass: area, floor, permitted use",
        "sourceEvidence": [
          "EV-L-2025-BROKER",
          "EV-L-2025-GEOCODE"
        ],
        "fitScore": 70,
        "firstSeenAt": "2026-07-13T09:25:00Z",
        "sourceUrl": "https://example.invalid/listings/L-2025",
        "candidateId": "CS-1002",
        "convertedAt": "2026-10-13T12:31:52Z"
      },
      {
        "id": "L-2029",
        "tenantId": "tenant-a",
        "sourceId": "SRC-591",
        "sourceListingId": "s591-2029",
        "heatZoneId": "HZ-02",
        "address": "新北市板橋區府中路 52 號 1F",
        "status": "duplicate",
        "rentPerMonth": 53000,
        "areaPing": 22,
        "floor": "1F",
        "frontageMeters": 5,
        "geocodeConfidence": 0.93,
        "duplicateOfId": "L-2025",
        "hardRuleFailures": [],
        "hardRuleSummary": "3/3 pass, duplicate same-address source",
        "sourceEvidence": [
          "EV-L-2029-RAW-591",
          "EV-L-2029-ADDRESS-MATCH",
          "EV-L-2029-RENT-MATCH"
        ],
        "fitScore": 70,
        "firstSeenAt": "2026-07-14T06:10:00Z",
        "sourceUrl": "https://example.invalid/listings/L-2029"
      },
      {
        "id": "L-2030",
        "tenantId": "tenant-a",
        "sourceId": "SRC-591",
        "sourceListingId": "s591-2030",
        "heatZoneId": "HZ-01",
        "address": "台北市信義區松仁路 110 號 1-2F",
        "status": "hardfail",
        "rentPerMonth": 128000,
        "areaPing": 40,
        "floor": "1-2F",
        "frontageMeters": 8,
        "geocodeConfidence": 0.95,
        "hardRuleFailures": [
          "area_above_format_maximum",
          "floor_not_ground_level"
        ],
        "hardRuleSummary": "2/3 blocked: area above 30 ping and second-floor operations required",
        "sourceEvidence": [
          "EV-L-2030-RAW-591",
          "EV-L-2030-HARD-RULES"
        ],
        "fitScore": 44,
        "firstSeenAt": "2026-07-14T06:10:00Z",
        "sourceUrl": "https://example.invalid/listings/L-2030"
      }
    ],
    "candidates": [
      {
        "id": "CS-1001",
        "listingId": "L-2024",
        "heatZoneId": "HZ-01",
        "title": "信義松仁候選點",
        "address": "台北市信義區松仁路 96 號 1F",
        "status": "ready",
        "score": 82,
        "recommendation": "GO",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "missingData": [],
        "reviewId": "RV-1001",
        "feasibilityFlags": [],
        "prior90dCellNetRevenue": null,
        "prior90dCellTransactionCount": null,
        "prior90dCellStoreCount": null,
        "featureSnapshotTime": null
      },
      {
        "id": "CS-1002",
        "listingId": "L-2025",
        "heatZoneId": "HZ-02",
        "title": "L-2025 候選點",
        "address": "新北市板橋區府中路 52 號 1F",
        "status": "ready",
        "score": 68,
        "recommendation": "WAIT",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "missingData": [],
        "reviewId": null,
        "feasibilityFlags": [],
        "prior90dCellNetRevenue": null,
        "prior90dCellTransactionCount": null,
        "prior90dCellStoreCount": null,
        "featureSnapshotTime": null
      }
    ],
    "siteReviews": [
      {
        "id": "RV-1001",
        "candidateId": "CS-1001",
        "status": "pending",
        "requestedByRoleId": "expansionManager",
        "reviewerRoleIds": [
          "opsLead",
          "auditPm"
        ],
        "requestedAt": "2026-07-14T06:30:00Z",
        "reasonRequired": true
      }
    ],
    "assistedIntakes": [],
    "expansionSteps": [
      {
        "id": "find",
        "label": "Find Area",
        "tabIndex": 0,
        "state": "completed",
        "entityId": "HZ-01",
        "summary": "HZ-01 selected and synchronized to the map."
      },
      {
        "id": "radar",
        "label": "Listing Radar",
        "tabIndex": 1,
        "state": "completed",
        "entityId": "L-2024",
        "summary": "L-2024 clean; duplicate and hard-rule rows visible."
      },
      {
        "id": "candidate",
        "label": "Candidate",
        "tabIndex": 2,
        "state": "current",
        "entityId": "CS-1001",
        "summary": "CS-1001 created once from L-2024."
      },
      {
        "id": "sitescore",
        "label": "SiteScore",
        "tabIndex": 3,
        "state": "next",
        "entityId": "CS-1001",
        "summary": "Ready for GO 82 evidence review."
      },
      {
        "id": "compare",
        "label": "Compare",
        "tabIndex": 4,
        "state": "next",
        "entityId": "CS-1001",
        "summary": "Compare HZ-01 candidate against HZ-02 pipeline."
      },
      {
        "id": "review",
        "label": "Review",
        "tabIndex": 5,
        "state": "next",
        "entityId": "RV-1001",
        "summary": "Reasoned review is available after scoring gate."
      }
    ],
    "selectedHeatZoneId": "HZ-01",
    "selectedLens": "demand",
    "auditEvents": [
      {
        "id": "AUD-NET-1c3128a5b6",
        "occurredAt": "2026-10-13T12:31:52Z",
        "actorRoleId": "expansion-manager",
        "actorName": "synthetic-local-setup",
        "category": "workflow",
        "action": "listing.convert",
        "targetType": "listing",
        "targetId": "L-2025",
        "message": "listing.convert recorded for L-2025",
        "correlationId": "synthetic-fixture-readback",
        "metadata": {
          "candidateId": "CS-1002",
          "created": true
        }
      },
      {
        "id": "AUD-NET-421e8c86b2",
        "occurredAt": "2026-10-13T12:31:52Z",
        "actorRoleId": "expansion-manager",
        "actorName": "synthetic-local-setup",
        "category": "workflow",
        "action": "listing.convert",
        "targetType": "listing",
        "targetId": "L-2024",
        "message": "listing.convert recorded for L-2024",
        "correlationId": "synthetic-fixture-readback",
        "metadata": {
          "candidateId": "CS-1001",
          "created": true
        }
      }
    ],
    "correlationId": null,
    "counts": {
      "heatZones": 2,
      "listings": 4,
      "candidates": 2,
      "siteReviews": 1,
      "assistedIntakes": 0
    }
  },
  "scoring": {
    "source": "api",
    "modelVersion": "SiteScore v2.3",
    "candidates": [
      {
        "id": "CS-1001",
        "listingId": "L-2024",
        "heatZoneId": "HZ-01",
        "title": "信義松仁候選點",
        "zoneLabel": "信義松仁 86",
        "address": "台北市信義區松仁路 96 號 1F",
        "district": "台北市信義區",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "stage": "evaluated",
        "gate": {
          "state": "ready",
          "passed": true,
          "missing": [],
          "otherMissing": [],
          "blockNote": "",
          "checks": [
            {
              "key": "address",
              "label": "地址定位",
              "state": "ok",
              "note": "台北市信義區松仁路 96 號 1F"
            },
            {
              "key": "geocode",
              "label": "Geocode ≥ 0.80",
              "state": "ok",
              "note": "0.94（高）"
            },
            {
              "key": "rent",
              "label": "租金",
              "state": "ok",
              "note": "NT$58,000／月（區間 P45）"
            },
            {
              "key": "area",
              "label": "坪數",
              "state": "ok",
              "note": "18 坪"
            },
            {
              "key": "floor",
              "label": "樓層",
              "state": "ok",
              "note": "1F 臨路"
            },
            {
              "key": "hardRule",
              "label": "Hard rule",
              "state": "ok",
              "note": "3/3 通過（面積／樓層／用途）"
            }
          ],
          "okCount": 6,
          "totalCount": 6
        },
        "scored": true,
        "score": 82,
        "recommendation": "GO",
        "reviewId": null,
        "inCompare": true
      },
      {
        "id": "CS-1002",
        "listingId": "L-2025",
        "heatZoneId": "HZ-02",
        "title": "板橋府中候選點",
        "zoneLabel": "板橋府中 78",
        "address": "新北市板橋區府中路 XX 號 1F",
        "district": "新北市板橋區",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260703-0600",
        "stage": "pendingreview",
        "gate": {
          "state": "ready",
          "passed": true,
          "missing": [],
          "otherMissing": [],
          "blockNote": "",
          "checks": [
            {
              "key": "address",
              "label": "地址定位",
              "state": "ok",
              "note": "新北市板橋區府中路 XX 號 1F"
            },
            {
              "key": "geocode",
              "label": "Geocode ≥ 0.80",
              "state": "ok",
              "note": "0.92（高）"
            },
            {
              "key": "rent",
              "label": "租金",
              "state": "ok",
              "note": "NT$52,000／月（區間 P70）"
            },
            {
              "key": "area",
              "label": "坪數",
              "state": "ok",
              "note": "22 坪"
            },
            {
              "key": "floor",
              "label": "樓層",
              "state": "ok",
              "note": "1F 近捷運出口"
            },
            {
              "key": "hardRule",
              "label": "Hard rule",
              "state": "ok",
              "note": "3/3 通過"
            }
          ],
          "okCount": 6,
          "totalCount": 6
        },
        "scored": true,
        "score": 76,
        "recommendation": "WAIT",
        "reviewId": "RV-701",
        "inCompare": true
      },
      {
        "id": "CS-1003",
        "listingId": "L-2026",
        "heatZoneId": "HZ-05",
        "title": "中壢中原候選點",
        "zoneLabel": "中壢中原 69",
        "address": "中壢區中北路 XX 號 1F",
        "district": "桃園市中壢區",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "stage": "needdata",
        "gate": {
          "state": "geo",
          "passed": false,
          "missing": [
            "Geocode ≥ 0.80",
            "平日人流樣本",
            "房東租期意願",
            "地址人工確認"
          ],
          "otherMissing": [
            "平日人流樣本",
            "房東租期意願",
            "地址人工確認"
          ],
          "blockNote": "地址／Geocode 未確認 — 需人工確認地址後才能評分",
          "checks": [
            {
              "key": "address",
              "label": "地址定位",
              "state": "ok",
              "note": "中壢區中北路 XX 號 1F（待人工確認）"
            },
            {
              "key": "geocode",
              "label": "Geocode ≥ 0.80",
              "state": "fail",
              "note": "0.71（中）— 待人工確認"
            },
            {
              "key": "rent",
              "label": "租金",
              "state": "ok",
              "note": "NT$36,000／月（區間 P25）"
            },
            {
              "key": "area",
              "label": "坪數",
              "state": "ok",
              "note": "16 坪"
            },
            {
              "key": "floor",
              "label": "樓層",
              "state": "ok",
              "note": "1F"
            },
            {
              "key": "hardRule",
              "label": "Hard rule",
              "state": "ok",
              "note": "3/3 通過"
            }
          ],
          "okCount": 5,
          "totalCount": 6
        },
        "scored": false,
        "score": null,
        "recommendation": null,
        "reviewId": null,
        "inCompare": false
      },
      {
        "id": "CS-1004",
        "listingId": "L-2027",
        "heatZoneId": "HZ-03",
        "title": "大安和平候選點",
        "zoneLabel": "大安和平 74",
        "address": "大安區和平東路二段 XX 號 1F",
        "district": "台北市大安區",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260630-0600",
        "stage": "rejected",
        "gate": {
          "state": "ready",
          "passed": true,
          "missing": [],
          "otherMissing": [],
          "blockNote": "",
          "checks": [
            {
              "key": "address",
              "label": "地址定位",
              "state": "ok",
              "note": "大安區和平東路二段 XX 號 1F"
            },
            {
              "key": "geocode",
              "label": "Geocode ≥ 0.80",
              "state": "ok",
              "note": "0.90（高）"
            },
            {
              "key": "rent",
              "label": "租金",
              "state": "ok",
              "note": "NT$64,000／月（區間 P90）"
            },
            {
              "key": "area",
              "label": "坪數",
              "state": "ok",
              "note": "20 坪"
            },
            {
              "key": "floor",
              "label": "樓層",
              "state": "ok",
              "note": "1F"
            },
            {
              "key": "hardRule",
              "label": "Hard rule",
              "state": "ok",
              "note": "3/3 通過"
            }
          ],
          "okCount": 6,
          "totalCount": 6
        },
        "scored": true,
        "score": 49,
        "recommendation": "REJECT",
        "reviewId": "RV-698",
        "inCompare": true
      }
    ],
    "scorecards": [
      {
        "id": "CS-1001",
        "title": "信義松仁候選點",
        "zoneLabel": "信義松仁 86",
        "heatZoneId": "HZ-01",
        "score": 82,
        "recommendation": "GO",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "generatedAt": "2026-07-04 06:10",
        "confidence": "中高",
        "payback": "22 個月",
        "revenuePath": {
          "m1": 182,
          "m3": 268,
          "m6": 342,
          "m12": 428
        },
        "band": {
          "p10": "NT$356K",
          "p50": "NT$428K",
          "p90": "NT$512K"
        },
        "subScores": {
          "rentReasonableness": "合理（區間 P45）",
          "cannibalization": "4%（低）",
          "competition": "中（無 24h 競店）",
          "demand": "強（夜間人流 84）",
          "poiFit": "高",
          "access": "中（週末停車不易）"
        },
        "capex": "NT$1.6M（14 台配置）",
        "rentAssumption": "月租 NT$58,000（區間 P45）",
        "drivers": [
          "夜間人流指數 84",
          "千戶級社區 6 個",
          "無 24h 競店"
        ],
        "reasons": [
          "住宅＋商辦混合，夜間洗烘需求強",
          "既有店 650m，稀釋僅 4%",
          "降租後租金落於區間 P45，仍可接受"
        ],
        "risks": [
          "週末停車不易",
          "租約含 3% 年調條款",
          "晚間人流資料信心中等"
        ],
        "conditions": [],
        "conditionTitle": "",
        "reviewId": null
      },
      {
        "id": "CS-1002",
        "title": "板橋府中候選點",
        "zoneLabel": "板橋府中 78",
        "heatZoneId": "HZ-02",
        "score": 76,
        "recommendation": "WAIT",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260703-0600",
        "generatedAt": "2026-07-13 16:42",
        "confidence": "中",
        "payback": "27 個月",
        "revenuePath": {
          "m1": 142,
          "m3": 221,
          "m6": 289,
          "m12": 372
        },
        "band": {
          "p10": "NT$308K",
          "p50": "NT$372K",
          "p90": "NT$431K"
        },
        "subScores": {
          "rentReasonableness": "偏高（區間 P70）",
          "cannibalization": "11%（中）",
          "competition": "高（150m 強競店）",
          "demand": "中高（通勤指數 82）",
          "poiFit": "高（捷運＋學校＋超商 5）",
          "access": "中（站前施工圍籬）"
        },
        "capex": "NT$1.8M（16 台配置）",
        "rentAssumption": "月租 NT$52,000（區間 P70）",
        "drivers": [
          "捷運通勤人流",
          "舊公寓無烘衣機比例高"
        ],
        "reasons": [
          "人流量體大",
          "夜間治安佳",
          "距捷運出口 80m，通勤動線佳"
        ],
        "risks": [
          "站前施工至 12 月",
          "與府中店重疊 11%",
          "晚間人流資料信心中等"
        ],
        "conditions": [
          "站前施工影響需於 Q4 前複評",
          "租金議價至 NT$48,000 以下",
          "補充晚間人流資料"
        ],
        "conditionTitle": "通過條件 — 符合後可重評為 GO",
        "reviewId": "RV-701"
      },
      {
        "id": "CS-1004",
        "title": "大安和平候選點",
        "zoneLabel": "大安和平 74",
        "heatZoneId": "HZ-03",
        "score": 49,
        "recommendation": "REJECT",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260630-0600",
        "generatedAt": "2026-06-30 14:20",
        "confidence": "中高",
        "payback": "41 個月",
        "revenuePath": {
          "m1": 102,
          "m3": 156,
          "m6": 203,
          "m12": 268
        },
        "band": {
          "p10": "NT$214K",
          "p50": "NT$268K",
          "p90": "NT$322K"
        },
        "subScores": {
          "rentReasonableness": "過高（區間 P90）",
          "cannibalization": "12%（中高）",
          "competition": "中",
          "demand": "中（學區穩定）",
          "poiFit": "中（學校 3 · 傳統市場）",
          "access": "低（巷弄面寬不足）"
        },
        "capex": "NT$1.7M（14 台配置）",
        "rentAssumption": "月租 NT$64,000（區間 P90）",
        "drivers": [
          "學區穩定需求"
        ],
        "reasons": [
          "住宅密度高",
          "高齡住戶代收送潛力"
        ],
        "risks": [
          "租金 P90，回本期 41 個月",
          "與大安和平店重疊 12%",
          "停車與面寬條件弱"
        ],
        "conditions": [
          "租金過高（P90），回本期 41 個月超出品牌上限 30 個月",
          "與大安和平店服務圈重疊 12%，自家稀釋高",
          "面寬與停車條件不足，大件洗客群流失"
        ],
        "conditionTitle": "拒絕原因",
        "reviewId": "RV-698"
      }
    ],
    "batchResults": [
      {
        "rank": 1,
        "priority": "P1",
        "id": "CS-1001",
        "title": "信義松仁候選點",
        "score": 82,
        "recommendation": "GO",
        "m12P50": "NT$428K",
        "payback": "22 個月",
        "cannibalization": "4%（低）",
        "inCompare": true
      },
      {
        "rank": 2,
        "priority": "P2",
        "id": "CS-1002",
        "title": "板橋府中候選點",
        "score": 76,
        "recommendation": "WAIT",
        "m12P50": "NT$372K",
        "payback": "27 個月",
        "cannibalization": "11%（中）",
        "inCompare": true
      },
      {
        "rank": 3,
        "priority": "P3",
        "id": "CS-1004",
        "title": "大安和平候選點",
        "score": 49,
        "recommendation": "REJECT",
        "m12P50": "NT$268K",
        "payback": "41 個月",
        "cannibalization": "12%（中高）",
        "inCompare": true
      }
    ],
    "compare": {
      "columns": [
        {
          "id": "CS-1001",
          "title": "信義松仁候選點",
          "priority": "P1",
          "recommendation": "GO",
          "score": 82,
          "isBest": true
        },
        {
          "id": "CS-1002",
          "title": "板橋府中候選點",
          "priority": "P2",
          "recommendation": "WAIT",
          "score": 76,
          "isBest": false
        },
        {
          "id": "CS-1004",
          "title": "大安和平候選點",
          "priority": "P3",
          "recommendation": "REJECT",
          "score": 49,
          "isBest": false
        }
      ],
      "metrics": [
        {
          "key": "sitescore",
          "label": "SiteScore",
          "values": [
            {
              "id": "CS-1001",
              "text": "82 GO",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "76 WAIT",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "49 REJECT",
              "isBest": false
            }
          ]
        },
        {
          "key": "m12",
          "label": "M12 P50",
          "values": [
            {
              "id": "CS-1001",
              "text": "NT$428K",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "NT$372K",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "NT$268K",
              "isBest": false
            }
          ]
        },
        {
          "key": "payback",
          "label": "回本期",
          "values": [
            {
              "id": "CS-1001",
              "text": "22 個月",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "27 個月",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "41 個月",
              "isBest": false
            }
          ]
        },
        {
          "key": "cannibalization",
          "label": "自家稀釋",
          "values": [
            {
              "id": "CS-1001",
              "text": "4%（低）",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "11%（中）",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "12%（中高）",
              "isBest": false
            }
          ]
        },
        {
          "key": "competition",
          "label": "競店壓力",
          "values": [
            {
              "id": "CS-1001",
              "text": "中（無 24h 競店）",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "高（150m 強競店）",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "中",
              "isBest": false
            }
          ]
        },
        {
          "key": "rent",
          "label": "租金合理性",
          "values": [
            {
              "id": "CS-1001",
              "text": "合理（區間 P45）",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "偏高（區間 P70）",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "過高（區間 P90）",
              "isBest": false
            }
          ]
        },
        {
          "key": "confidence",
          "label": "資料信心",
          "values": [
            {
              "id": "CS-1001",
              "text": "中高",
              "isBest": true
            },
            {
              "id": "CS-1002",
              "text": "中",
              "isBest": false
            },
            {
              "id": "CS-1004",
              "text": "中高",
              "isBest": false
            }
          ]
        }
      ],
      "recommendation": {
        "primary": {
          "id": "CS-1001",
          "title": "信義松仁候選點",
          "recommendation": "GO",
          "score": 82,
          "text": "推薦：信義松仁候選點（GO 82，回本 22 個月）",
          "why": [
            "SiteScore 82，為目前最高分",
            "M12 P50 NT$428K／月",
            "回本期 22 個月",
            "自家稀釋 4%（低）",
            "租金可行性：合理（區間 P45）"
          ]
        },
        "alternate": {
          "id": "CS-1002",
          "title": "板橋府中候選點",
          "recommendation": "WAIT",
          "score": 76,
          "text": "備選：板橋府中候選點（WAIT 76）　— 條件改善後重評"
        },
        "avoid": {
          "id": "CS-1004",
          "title": "大安和平候選點",
          "recommendation": "REJECT",
          "score": 49,
          "text": "不建議：大安和平候選點 — 租金過高（P90），回本期 41 個月超出品牌上限 30 個月"
        },
        "priorityList": [
          {
            "priority": "P1",
            "id": "CS-1001",
            "title": "信義松仁候選點",
            "score": 82,
            "recommendation": "GO"
          },
          {
            "priority": "P2",
            "id": "CS-1002",
            "title": "板橋府中候選點",
            "score": 76,
            "recommendation": "WAIT"
          },
          {
            "priority": "P3",
            "id": "CS-1004",
            "title": "大安和平候選點",
            "score": 49,
            "recommendation": "REJECT"
          }
        ]
      },
      "empty": false
    },
    "compareSet": [
      "CS-1001",
      "CS-1002",
      "CS-1004"
    ],
    "auditEvents": [],
    "correlationId": null,
    "counts": {
      "candidates": 4,
      "scored": 3,
      "gateBlocked": 1
    }
  },
  "reviews": {
    "source": "api",
    "reviews": [
      {
        "id": "RV-698",
        "candidateId": "CS-1004",
        "candidateTitle": "大安和平候選點",
        "zoneLabel": "大安和平 74",
        "recommendation": "REJECT",
        "score": 49,
        "risk": "租金 P90 · 回本 41 個月超品牌上限",
        "requestedBy": "王若寧（拓展）",
        "reviewerRole": "選址審核 / Site Reviewer",
        "submittedAt": "2026-06-30 14:20",
        "dueAt": "2026-07-15 18:00",
        "payback": "41 個月",
        "m12P50": "NT$268K",
        "rentReasonableness": "過高（區間 P90）",
        "cannibalization": "12%（中高）",
        "sourceListingId": "L-2027",
        "fieldVisit": "已完成（6/24 現勘）",
        "brokerContact": "已聯絡",
        "notes": "租金過高、回本期 41 個月超出品牌上限 30 個月，與大安和平店重疊 12%。",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260630-0600",
        "compareText": "不建議（REJECT 49，租金 P90 回本 41 個月）",
        "candidateStatus": "pendingreview",
        "eventChips": [
          "SiteScore v2.3",
          "Dataset FS-20260630-0600",
          "現勘完成",
          "比較 不建議"
        ],
        "history": [
          {
            "t": "2026-06-30 14:20",
            "v": "SiteScore 完成 REJECT 49"
          },
          {
            "t": "2026-06-30 14:20",
            "v": "王若寧送審（拓展）"
          }
        ],
        "status": "pending",
        "statusLabel": "待審核",
        "decision": null,
        "candidateStatusLabel": "待審核",
        "candidateMissingData": [],
        "pending": true,
        "decided": false,
        "overrideAvailable": true
      },
      {
        "id": "RV-702",
        "candidateId": "CS-1001",
        "candidateTitle": "信義松仁候選點",
        "zoneLabel": "信義松仁 86",
        "recommendation": "GO",
        "score": 82,
        "risk": "回本 22 個月 · 週末停車不易",
        "requestedBy": "王若寧（拓展）",
        "reviewerRole": "選址審核 / Site Reviewer",
        "submittedAt": "2026-07-13 09:20",
        "dueAt": "2026-07-16 18:00",
        "payback": "22 個月",
        "m12P50": "NT$428K",
        "rentReasonableness": "合理（區間 P45）",
        "cannibalization": "4%（低）",
        "sourceListingId": "L-2024",
        "fieldVisit": "已完成（現勘照片 6 張）",
        "brokerContact": "已聯絡（王仲介）",
        "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260704-0600",
        "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
        "candidateStatus": "pendingreview",
        "eventChips": [
          "SiteScore v2.3",
          "Dataset FS-20260704-0600",
          "現勘完成",
          "比較 P1"
        ],
        "history": [
          {
            "t": "2026-07-04 06:10",
            "v": "SiteScore 完成 GO 82"
          },
          {
            "t": "2026-07-13 09:20",
            "v": "王若寧送審（拓展）"
          }
        ],
        "status": "pending",
        "statusLabel": "待審核",
        "decision": null,
        "candidateStatusLabel": "待審核",
        "candidateMissingData": [],
        "pending": true,
        "decided": false,
        "overrideAvailable": true
      },
      {
        "id": "RV-701",
        "candidateId": "CS-1002",
        "candidateTitle": "板橋府中候選點",
        "zoneLabel": "板橋府中 78",
        "recommendation": "WAIT",
        "score": 76,
        "risk": "站前施工至 12 月 · 與府中店重疊 11%",
        "requestedBy": "王若寧（拓展）",
        "reviewerRole": "選址審核 / Site Reviewer",
        "submittedAt": "2026-07-13 16:42",
        "dueAt": "2026-07-17 18:00",
        "payback": "27 個月",
        "m12P50": "NT$372K",
        "rentReasonableness": "偏高（區間 P70）",
        "cannibalization": "11%（中）",
        "sourceListingId": "L-2025",
        "fieldVisit": "已完成（6/28 現勘）",
        "brokerContact": "已聯絡（王仲介）",
        "notes": "捷運通勤人流大，惟站前施工圍籬與府中店重疊需以條件管理。",
        "modelVersion": "SiteScore v2.3",
        "datasetSnapshotId": "FS-20260703-0600",
        "compareText": "P2 備選（WAIT 76，條件改善後可重評）",
        "candidateStatus": "pendingreview",
        "eventChips": [
          "SiteScore v2.3",
          "Dataset FS-20260703-0600",
          "現勘完成",
          "比較 P2"
        ],
        "history": [
          {
            "t": "2026-07-13 16:42",
            "v": "王若寧送審（拓展）"
          }
        ],
        "status": "pending",
        "statusLabel": "待審核",
        "decision": null,
        "candidateStatusLabel": "待審核",
        "candidateMissingData": [],
        "pending": true,
        "decided": false,
        "overrideAvailable": true
      }
    ],
    "candidates": [
      {
        "id": "CS-1001",
        "title": "信義松仁候選點",
        "zoneLabel": "信義松仁 86",
        "status": "pendingreview",
        "statusLabel": "待審核",
        "recommendation": "GO",
        "score": 82,
        "reviewId": "RV-702",
        "missingData": []
      },
      {
        "id": "CS-1002",
        "title": "板橋府中候選點",
        "zoneLabel": "板橋府中 78",
        "status": "pendingreview",
        "statusLabel": "待審核",
        "recommendation": "WAIT",
        "score": 76,
        "reviewId": "RV-701",
        "missingData": []
      },
      {
        "id": "CS-1004",
        "title": "大安和平候選點",
        "zoneLabel": "大安和平 74",
        "status": "pendingreview",
        "statusLabel": "待審核",
        "recommendation": "REJECT",
        "score": 49,
        "reviewId": "RV-698",
        "missingData": []
      }
    ],
    "approvals": [
      {
        "id": "AP-RV-702",
        "reviewId": "RV-702",
        "candidateId": "CS-1001",
        "title": "信義松仁候選點",
        "systemRecommendation": "GO",
        "risk": "回本 22 個月 · 週末停車不易",
        "status": "pending",
        "submittedAt": "2026-07-13 09:20",
        "decidedAt": null,
        "decidedBy": null
      },
      {
        "id": "AP-RV-701",
        "reviewId": "RV-701",
        "candidateId": "CS-1002",
        "title": "板橋府中候選點",
        "systemRecommendation": "WAIT",
        "risk": "站前施工至 12 月 · 與府中店重疊 11%",
        "status": "pending",
        "submittedAt": "2026-07-13 16:42",
        "decidedAt": null,
        "decidedBy": null
      },
      {
        "id": "AP-RV-698",
        "reviewId": "RV-698",
        "candidateId": "CS-1004",
        "title": "大安和平候選點",
        "systemRecommendation": "REJECT",
        "risk": "租金 P90 · 回本 41 個月超品牌上限",
        "status": "pending",
        "submittedAt": "2026-06-30 14:20",
        "decidedAt": null,
        "decidedBy": null
      }
    ],
    "decisions": [],
    "auditEvents": [],
    "decisionMapping": {
      "GO": "Approved",
      "WAIT": "On Hold",
      "RETURN": "Need Data",
      "REJECT": "Rejected"
    },
    "counts": {
      "reviews": 3,
      "pending": 3,
      "decided": 0
    },
    "correlationId": null
  }
};
const approvedReviews={
  "source": "api",
  "reviews": [
    {
      "id": "RV-698",
      "candidateId": "CS-1004",
      "candidateTitle": "大安和平候選點",
      "zoneLabel": "大安和平 74",
      "recommendation": "REJECT",
      "score": 49,
      "risk": "租金 P90 · 回本 41 個月超品牌上限",
      "requestedBy": "王若寧（拓展）",
      "reviewerRole": "選址審核 / Site Reviewer",
      "submittedAt": "2026-06-30 14:20",
      "dueAt": "2026-07-15 18:00",
      "payback": "41 個月",
      "m12P50": "NT$268K",
      "rentReasonableness": "過高（區間 P90）",
      "cannibalization": "12%（中高）",
      "sourceListingId": "L-2027",
      "fieldVisit": "已完成（6/24 現勘）",
      "brokerContact": "已聯絡",
      "notes": "租金過高、回本期 41 個月超出品牌上限 30 個月，與大安和平店重疊 12%。",
      "modelVersion": "SiteScore v2.3",
      "datasetSnapshotId": "FS-20260630-0600",
      "compareText": "不建議（REJECT 49，租金 P90 回本 41 個月）",
      "candidateStatus": "pendingreview",
      "eventChips": [
        "SiteScore v2.3",
        "Dataset FS-20260630-0600",
        "現勘完成",
        "比較 不建議"
      ],
      "history": [
        {
          "t": "2026-06-30 14:20",
          "v": "SiteScore 完成 REJECT 49"
        },
        {
          "t": "2026-06-30 14:20",
          "v": "王若寧送審（拓展）"
        }
      ],
      "status": "pending",
      "statusLabel": "待審核",
      "decision": null,
      "candidateStatusLabel": "待審核",
      "candidateMissingData": [],
      "pending": true,
      "decided": false,
      "overrideAvailable": true
    },
    {
      "id": "RV-701",
      "candidateId": "CS-1002",
      "candidateTitle": "板橋府中候選點",
      "zoneLabel": "板橋府中 78",
      "recommendation": "WAIT",
      "score": 76,
      "risk": "站前施工至 12 月 · 與府中店重疊 11%",
      "requestedBy": "王若寧（拓展）",
      "reviewerRole": "選址審核 / Site Reviewer",
      "submittedAt": "2026-07-13 16:42",
      "dueAt": "2026-07-17 18:00",
      "payback": "27 個月",
      "m12P50": "NT$372K",
      "rentReasonableness": "偏高（區間 P70）",
      "cannibalization": "11%（中）",
      "sourceListingId": "L-2025",
      "fieldVisit": "已完成（6/28 現勘）",
      "brokerContact": "已聯絡（王仲介）",
      "notes": "捷運通勤人流大，惟站前施工圍籬與府中店重疊需以條件管理。",
      "modelVersion": "SiteScore v2.3",
      "datasetSnapshotId": "FS-20260703-0600",
      "compareText": "P2 備選（WAIT 76，條件改善後可重評）",
      "candidateStatus": "pendingreview",
      "eventChips": [
        "SiteScore v2.3",
        "Dataset FS-20260703-0600",
        "現勘完成",
        "比較 P2"
      ],
      "history": [
        {
          "t": "2026-07-13 16:42",
          "v": "王若寧送審（拓展）"
        }
      ],
      "status": "pending",
      "statusLabel": "待審核",
      "decision": null,
      "candidateStatusLabel": "待審核",
      "candidateMissingData": [],
      "pending": true,
      "decided": false,
      "overrideAvailable": true
    },
    {
      "id": "RV-702",
      "candidateId": "CS-1001",
      "candidateTitle": "信義松仁候選點",
      "zoneLabel": "信義松仁 86",
      "recommendation": "GO",
      "score": 82,
      "risk": "回本 22 個月 · 週末停車不易",
      "requestedBy": "王若寧（拓展）",
      "reviewerRole": "選址審核 / Site Reviewer",
      "submittedAt": "2026-07-13 09:20",
      "dueAt": "2026-07-16 18:00",
      "payback": "22 個月",
      "m12P50": "NT$428K",
      "rentReasonableness": "合理（區間 P45）",
      "cannibalization": "4%（低）",
      "sourceListingId": "L-2024",
      "fieldVisit": "已完成（現勘照片 6 張）",
      "brokerContact": "已聯絡（王仲介）",
      "notes": "住宅＋商辦混合，夜間洗烘需求強；既有店 650m 稀釋僅 4%。",
      "modelVersion": "SiteScore v2.3",
      "datasetSnapshotId": "FS-20260704-0600",
      "compareText": "P1 主推（GO 82，領先板橋府中 6 分）",
      "candidateStatus": "approved",
      "eventChips": [
        "SiteScore v2.3",
        "Dataset FS-20260704-0600",
        "現勘完成",
        "比較 P1"
      ],
      "history": [
        {
          "t": "2026-10-13T12:38:50Z",
          "v": "選址審核 / Site Reviewer決策 GO → Approved"
        },
        {
          "t": "2026-07-04 06:10",
          "v": "SiteScore 完成 GO 82"
        },
        {
          "t": "2026-07-13 09:20",
          "v": "王若寧送審（拓展）"
        }
      ],
      "status": "approved",
      "statusLabel": "已核准 GO",
      "decision": {
        "decision": "GO",
        "finalLabel": "Approved",
        "mappedStatus": "approved",
        "reason": "Review approved based on excellent SiteScore and fit metrics.",
        "conditions": "",
        "requiredData": [],
        "override": false,
        "decidedAt": "2026-10-13T12:38:50Z",
        "decidedBy": "synthetic-local-fixture-reviewer",
        "actorRoleId": "siteReviewer",
        "decisionId": "DEC-REVIEW-c44810ff",
        "approvalId": "AP-RV-702",
        "auditId": "AUD-REVIEW-53c380acf2"
      },
      "candidateStatusLabel": "已核准 GO",
      "candidateMissingData": [],
      "pending": false,
      "decided": true,
      "overrideAvailable": false
    }
  ],
  "candidates": [
    {
      "id": "CS-1001",
      "title": "信義松仁候選點",
      "zoneLabel": "信義松仁 86",
      "status": "approved",
      "statusLabel": "已核准 GO",
      "recommendation": "GO",
      "score": 82,
      "reviewId": "RV-702",
      "missingData": []
    },
    {
      "id": "CS-1002",
      "title": "板橋府中候選點",
      "zoneLabel": "板橋府中 78",
      "status": "pendingreview",
      "statusLabel": "待審核",
      "recommendation": "WAIT",
      "score": 76,
      "reviewId": "RV-701",
      "missingData": []
    },
    {
      "id": "CS-1004",
      "title": "大安和平候選點",
      "zoneLabel": "大安和平 74",
      "status": "pendingreview",
      "statusLabel": "待審核",
      "recommendation": "REJECT",
      "score": 49,
      "reviewId": "RV-698",
      "missingData": []
    }
  ],
  "approvals": [
    {
      "id": "AP-RV-702",
      "reviewId": "RV-702",
      "candidateId": "CS-1001",
      "title": "信義松仁候選點",
      "systemRecommendation": "GO",
      "risk": "回本 22 個月 · 週末停車不易",
      "status": "approved",
      "submittedAt": "2026-07-13 09:20",
      "decidedAt": "2026-10-13T12:38:50Z",
      "decidedBy": "synthetic-local-fixture-reviewer"
    },
    {
      "id": "AP-RV-701",
      "reviewId": "RV-701",
      "candidateId": "CS-1002",
      "title": "板橋府中候選點",
      "systemRecommendation": "WAIT",
      "risk": "站前施工至 12 月 · 與府中店重疊 11%",
      "status": "pending",
      "submittedAt": "2026-07-13 16:42",
      "decidedAt": null,
      "decidedBy": null
    },
    {
      "id": "AP-RV-698",
      "reviewId": "RV-698",
      "candidateId": "CS-1004",
      "title": "大安和平候選點",
      "systemRecommendation": "REJECT",
      "risk": "租金 P90 · 回本 41 個月超品牌上限",
      "status": "pending",
      "submittedAt": "2026-06-30 14:20",
      "decidedAt": null,
      "decidedBy": null
    }
  ],
  "decisions": [
    {
      "id": "DEC-REVIEW-c44810ff",
      "reviewId": "RV-702",
      "candidateId": "CS-1001",
      "module": "Network 選址審核",
      "item": "信義松仁候選點",
      "systemRecommendation": "GO",
      "decision": "GO",
      "finalDecision": "Approved",
      "mappedStatus": "approved",
      "reason": "Review approved based on excellent SiteScore and fit metrics.",
      "conditions": "",
      "requiredData": [],
      "override": false,
      "actor": "synthetic-local-fixture-reviewer",
      "actorRoleId": "siteReviewer",
      "decidedAt": "2026-10-13T12:38:50Z",
      "modelVersion": "SiteScore v2.3",
      "datasetSnapshot": "FS-20260704-0600",
      "approvalId": "AP-RV-702",
      "correlationId": "fixture-only-control"
    }
  ],
  "auditEvents": [
    {
      "id": "AUD-REVIEW-53c380acf2",
      "occurredAt": "2026-10-13T12:38:50Z",
      "actorRoleId": "siteReviewer",
      "actorName": "synthetic-local-fixture-reviewer",
      "category": "governance",
      "action": "review.decision",
      "targetType": "review",
      "targetId": "RV-702",
      "message": "RV-702 GO → Approved for CS-1001",
      "correlationId": "fixture-only-control",
      "metadata": {
        "decision": "GO",
        "finalLabel": "Approved",
        "mappedStatus": "approved",
        "candidateId": "CS-1001",
        "approvalId": "AP-RV-702",
        "decisionId": "DEC-REVIEW-c44810ff",
        "override": false,
        "systemRecommendation": "GO"
      }
    }
  ],
  "decisionMapping": {
    "GO": "Approved",
    "WAIT": "On Hold",
    "RETURN": "Need Data",
    "REJECT": "Rejected"
  },
  "counts": {
    "reviews": 3,
    "pending": 2,
    "decided": 1
  },
  "correlationId": null
};
const reviews=producer.reviews.reviews as ReviewItem[];
const row=(id="CS-1002")=>structuredClone(reviews.find(item=>item.candidateId===id)!);
beforeEach(()=>{nav.search="ws=network&tab=review&candidate=CS-1002";nav.push.mockReset();window.history.replaceState(null,"","/operator?"+nav.search);window.sessionStorage.clear();vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE","false");});
afterEach(()=>{cleanup();vi.unstubAllEnvs();vi.unstubAllGlobals();vi.restoreAllMocks();});
const cases=[{candidateId:"CS-1001",reviewId:"RV-702",action:"go"},{candidateId:"CS-1002",reviewId:"RV-701",action:"wait"}];
function reads(){let decided=false;const mock=vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{const url=String(input);
  if(init?.method==="POST"&&url.endsWith("/decide")){decided=true;return new Response("{}",{status:200,headers:{"Content-Type":"application/json"}});}
  const data=url.includes("network-listings?")?producer.network:url.includes("network-scoring")?producer.scoring:url.includes("network-reviews")?(decided?approvedReviews:producer.reviews):{source:"api",stores:[]};return new Response(JSON.stringify(data),{status:200,headers:{"Content-Type":"application/json"}});
});vi.stubGlobal("fetch",mock);return mock;}
function followPushedRoute(view:ReturnType<typeof render>){const href=String(nav.push.mock.calls.at(-1)![0]);nav.search=new URL(href,"https://example.invalid").searchParams.toString();window.history.replaceState(null,"",href);view.rerender(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"/>);return new URL(href,"https://example.invalid");}
describe("explicit fixture-only exact candidate Review compatibility",()=>{
  it.each(cases)("selects the unique actual $candidateId legacy review without borrowing the first row",({candidateId,reviewId,action})=>{
    render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId={candidateId} expectedReport={null}/>);
    expect(screen.getByTestId(`review-card-${reviewId}`)).toHaveAttribute("data-active","true");
    expect(screen.getByTestId("review-card-RV-698")).not.toHaveAttribute("data-active","true");
    expect(screen.getByTestId(`review-btn-${action}-${reviewId}`)).toBeInTheDocument();
  });
  it.each(cases)("retains explicit $candidateId fixture queue selection and its original dialog",({candidateId,reviewId,action})=>{
    render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId={candidateId} expectedReport={null}/>);
    fireEvent.click(screen.getByTestId(`review-card-${reviewId}`));fireEvent.click(screen.getByTestId(`review-btn-${action}-${reviewId}`));
    expect(screen.getByTestId("review-decision-dialog")).toHaveTextContent(reviewId);
  });
  it.each(cases)("preserves actual Workspace Candidate→Score→Compare→Review context for $candidateId",async({candidateId,reviewId,action})=>{
    nav.search=`ws=network&tab=candidates&candidate=${candidateId}`;window.history.replaceState(null,"","/operator?"+nav.search);reads();const view=render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"/>);
    await waitFor(()=>expect(screen.getByTestId(`candidate-row-${candidateId}`)).toHaveAttribute("data-active","true"));
    for(const tab of [3,4,5]){fireEvent.click(screen.getByTestId(`network-tab-${tab}`));expect(followPushedRoute(view).searchParams.get("candidate")).toBe(candidateId);}
    await screen.findByTestId(`review-card-${reviewId}`);fireEvent.click(screen.getByTestId(`review-card-${reviewId}`));
    expect(screen.getByTestId(`review-metrics-${reviewId}`)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId(`review-btn-${action}-${reviewId}`));expect(screen.getByTestId("review-decision-dialog")).toHaveTextContent(reviewId);
  });
  it("retains the original fixture CS-1001 GO reason gate and recorded Approved readback",async()=>{
    nav.search="ws=network&tab=review&candidate=CS-1001";window.history.replaceState(null,"","/operator?"+nav.search);const mock=reads();render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"/>);
    await screen.findByTestId("review-card-RV-702");fireEvent.click(screen.getByTestId("review-card-RV-702"));fireEvent.click(screen.getByTestId("review-btn-go-RV-702"));
    fireEvent.change(screen.getByTestId("review-decision-reason"),{target:{value:"Short"}});fireEvent.click(screen.getByTestId("review-decision-submit"));
    expect(screen.getByTestId("review-decision-error")).toHaveTextContent("決策原因");
    expect(mock.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(0);
    fireEvent.change(screen.getByTestId("review-decision-reason"),{target:{value:"Review approved based on excellent SiteScore and fit metrics."}});fireEvent.click(screen.getByTestId("review-decision-submit"));
    await waitFor(()=>expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument());
    await waitFor(()=>expect(screen.getByTestId("review-decided-RV-702")).toHaveTextContent("Approved"));
    const writes=mock.mock.calls.filter(([,init])=>init?.method==="POST");expect(writes).toHaveLength(1);expect(new URL(String(writes[0][0]),"https://example.invalid").pathname).toBe("/api/v1/operator/network-reviews/RV-702/decide");expect(JSON.parse(String(writes[0][1]!.body))).toMatchObject({decision:"GO",reason:"Review approved based on excellent SiteScore and fit metrics."});
  });
  it("never enables this compatibility path without its explicit fixture opt-in",()=>{
    render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide workflowCandidateId="CS-1002" expectedReport={null}/>);
    expect(screen.getByTestId("review-card-RV-701")).not.toHaveAttribute("data-active","true");fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("keeps production Workspace strict even when its API rows happen to be legacy-shaped",async()=>{
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE","true");reads();render(<NetworkFindAreasWorkspace activeRoleId="expansion-manager"/>);await screen.findByTestId("review-card-RV-701");fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it.each(["DRAFT","SYSTEM_RECOMMENDED","PENDING_REVIEW","APPROVED","REJECTED"] as const)("does not relax canonical $0 without exact report context",status=>{
    const canonical={...row(),canonicalDecisionStatus:status};render(<ReviewPanel reviews={[canonical]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it.each(["reportId","reportVersion","sitescoreRunId"])("does not treat legacy %s lineage as an unqualified fixture",key=>{
    const linked:any=row();linked[key]=key==="reportVersion"?1:"recorded-lineage";render(<ReviewPanel reviews={[linked]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("does not fall back to fixture matching when a different expected report was specified",()=>{
    render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={{reportId:"different-report",reportVersion:7}}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("refuses duplicate exact candidate reviews even after explicit queue selection",()=>{
    const duplicate={...row(),id:"RV-701-duplicate"};render(<ReviewPanel reviews={[row(),duplicate]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    expect(screen.getByTestId("review-card-RV-701")).not.toHaveAttribute("data-active","true");fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("retains foreign candidate history without substituting it for the requested context",()=>{
    render(<ReviewPanel reviews={[row("CS-1001")]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-702"));expect(screen.queryByTestId("review-btn-go-RV-702")).not.toBeInTheDocument();
  });
  it("preserves the existing exact canonical report behavior with fixture opt-in present",()=>{
    const canonical:ReviewItem={...row(),score:null,canonicalDecisionStatus:"PENDING_REVIEW",reportAvailability:"available",reportId:"retained-report",reportVersion:4};
    render(<ReviewPanel reviews={[canonical]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={{reportId:"retained-report",reportVersion:4}}/>);
    expect(screen.getByTestId("review-card-RV-701")).toHaveAttribute("data-active","true");expect(screen.getByTestId("review-btn-wait-RV-701")).toBeInTheDocument();
  });
  it.each(["reportId","reportVersion","sitescoreRunId","canonicalDecisionStatus"])("does not qualify an explicitly null %s marker as absent fixture lineage",key=>{
    const linked:any=row();linked[key]=null;render(<ReviewPanel reviews={[linked]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("does not choose a legacy record when the exact candidate also has canonical history",()=>{
    const canonical:ReviewItem={...row(),id:"canonical-history",canonicalDecisionStatus:"PENDING_REVIEW",reportId:"retained-report",reportVersion:4};
    render(<ReviewPanel reviews={[row(),canonical]} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("never grants decision authority merely because fixture candidate matching succeeds",()=>{
    render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide={false} fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    expect(screen.getByTestId("review-card-RV-701")).toHaveAttribute("data-active","true");expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });
  it("closes an existing local dialog when its fixture opt-in is removed",()=>{
    const view=render(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide fixtureCandidateContext workflowCandidateId="CS-1002" expectedReport={null}/>);
    fireEvent.click(screen.getByTestId("review-card-RV-701"));fireEvent.click(screen.getByTestId("review-btn-wait-RV-701"));expect(screen.getByTestId("review-decision-dialog")).toBeInTheDocument();
    view.rerender(<ReviewPanel reviews={reviews} fallbackRows={[]} canDecide fixtureCandidateContext={false} workflowCandidateId="CS-1002" expectedReport={null}/>);
    expect(screen.queryByTestId("review-decision-dialog")).not.toBeInTheDocument();expect(screen.queryByTestId("review-btn-wait-RV-701")).not.toBeInTheDocument();
  });

});
