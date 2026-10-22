import "@testing-library/jest-dom/vitest";
import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetworkFindAreasWorkspace as GrantedReadNetworkWorkspace, inspectNetworkListingsSnapshot, inspectNetworkScoringSnapshot } from "../../NetworkFindAreasWorkspace";

// Explicit read-grant fixture: shell projects listing and SiteScore read families.
// This is a renderer premise, not a real Principal/production permission grant.
const NETWORK_READ_FIXTURE_TABS = ["areas", "radar", "candidates", "score", "compare", "review"] as const;
function NetworkFindAreasWorkspace(props: import("../../NetworkFindAreasWorkspace").NetworkFindAreasWorkspaceProps) {
  return createElement(GrantedReadNetworkWorkspace, { allowedTabIds: NETWORK_READ_FIXTURE_TABS, ...props });
}


vi.mock("next/navigation", () => ({
  usePathname: () => "/operator", useSearchParams: () => new URLSearchParams("ws=network&tab=sitescore"),
  useRouter: () => ({ push: vi.fn() }),
}));
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

// Actual application-service JSON captured from cd84 + the frozen 001/008 inputs.
// NetworkListingService.snapshot(), NetworkScoringService.snapshot(), and the
// existing canonical report projection fixture build these DTOs. These are local
// contract fixtures; they do not certify a production model or live inference.
const listingProducer = {
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
    }
  ],
  "listingSources": [
    {
      "id": "SRC-591",
      "name": "591 licensed broker intake",
      "status": "connected",
      "complianceNote": "R4 proof uses licensed/manual intake fields and preserves source evidence refs.",
      "lastSyncedAt": "2026-07-14T06:10:00Z"
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
      "status": "new",
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
      "sourceUrl": "https://example.invalid/listings/L-2024"
    }
  ],
  "candidates": [],
  "siteReviews": [],
  "assistedIntakes": []
};
const scoringProducer = {
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
};
const canonicalProducer = {
  "source": "canonical",
  "modelVersion": "actual-report-model",
  "candidates": [
    {
      "id": "candidate-A",
      "listingId": "listing-candidate-A",
      "heatZoneId": "synthetic-zone",
      "title": "Synthetic candidate-A",
      "zoneLabel": "synthetic-zone",
      "address": "Synthetic candidate-A",
      "district": "",
      "modelVersion": "actual-report-model",
      "datasetSnapshotId": "current-input-candidate-A",
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
            "note": ""
          },
          {
            "key": "geocode",
            "label": "Geocode ≥ 0.80",
            "state": "ok",
            "note": ""
          },
          {
            "key": "rent",
            "label": "租金",
            "state": "ok",
            "note": ""
          },
          {
            "key": "area",
            "label": "坪數",
            "state": "ok",
            "note": ""
          },
          {
            "key": "floor",
            "label": "樓層",
            "state": "ok",
            "note": ""
          },
          {
            "key": "hardRule",
            "label": "Hard rule",
            "state": "ok",
            "note": ""
          }
        ],
        "okCount": 6,
        "totalCount": 6
      },
      "scored": true,
      "score": null,
      "recommendation": "INVESTIGATE",
      "reviewId": null,
      "inCompare": false
    },
    {
      "id": "candidate-B",
      "listingId": "listing-candidate-B",
      "heatZoneId": "synthetic-zone",
      "title": "Synthetic candidate-B",
      "zoneLabel": "synthetic-zone",
      "address": "Synthetic candidate-B",
      "district": "",
      "modelVersion": null,
      "datasetSnapshotId": "current-input-candidate-B",
      "stage": "ready",
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
            "note": ""
          },
          {
            "key": "geocode",
            "label": "Geocode ≥ 0.80",
            "state": "ok",
            "note": ""
          },
          {
            "key": "rent",
            "label": "租金",
            "state": "ok",
            "note": ""
          },
          {
            "key": "area",
            "label": "坪數",
            "state": "ok",
            "note": ""
          },
          {
            "key": "floor",
            "label": "樓層",
            "state": "ok",
            "note": ""
          },
          {
            "key": "hardRule",
            "label": "Hard rule",
            "state": "ok",
            "note": ""
          }
        ],
        "okCount": 6,
        "totalCount": 6
      },
      "scored": false,
      "score": null,
      "recommendation": null,
      "reviewId": null,
      "inCompare": false
    }
  ],
  "scorecards": [
    {
      "id": "candidate-A",
      "title": "candidate-A",
      "zoneLabel": "",
      "heatZoneId": "",
      "score": null,
      "recommendation": "INVESTIGATE",
      "modelVersion": "actual-report-model",
      "datasetSnapshotId": "historical-report-input-candidate-A",
      "generatedAt": "2026-10-13T00:00:00+00:00",
      "confidence": 0.91,
      "confidenceStatus": "measured",
      "payback": 36,
      "revenuePath": {
        "m1": {
          "p10": 100000,
          "p50": 150000,
          "p90": 200000
        },
        "m3": {
          "p10": 100000,
          "p50": 150000,
          "p90": 200000
        },
        "m6": {
          "p10": 100000,
          "p50": 150000,
          "p90": 200000
        },
        "m12": {
          "p10": 100000,
          "p50": 150000,
          "p90": 200000
        }
      },
      "band": {
        "p10": 100000,
        "p50": 150000,
        "p90": 200000
      },
      "subScores": {
        "rentReasonableness": 0.6,
        "cannibalization": 0.2
      },
      "capex": null,
      "rentAssumption": null,
      "drivers": [],
      "reasons": [],
      "risks": [],
      "conditions": [],
      "conditionTitle": "",
      "reviewId": null,
      "reportId": "sitescore-report-b121d87c-5e3b-44d4-ad10-7666715f6f5f",
      "reportVersion": 1,
      "sitescoreRunId": "run-candidate-A-1"
    }
  ],
  "batchResults": [
    {
      "rank": null,
      "priority": null,
      "id": "candidate-A",
      "title": "candidate-A",
      "score": null,
      "recommendation": "INVESTIGATE",
      "m12P50": 150000,
      "payback": 36,
      "cannibalization": 0.2,
      "inCompare": false
    }
  ],
  "compare": {
    "columns": [],
    "metrics": [
      {
        "key": "sitescore",
        "label": "SiteScore",
        "values": []
      },
      {
        "key": "m12",
        "label": "M12 P50",
        "values": []
      },
      {
        "key": "payback",
        "label": "回本期",
        "values": []
      },
      {
        "key": "cannibalization",
        "label": "自家稀釋",
        "values": []
      },
      {
        "key": "competition",
        "label": "競店壓力",
        "values": []
      },
      {
        "key": "rent",
        "label": "租金合理性",
        "values": []
      },
      {
        "key": "confidence",
        "label": "資料信心",
        "values": []
      }
    ],
    "recommendation": null,
    "empty": true
  },
  "compareSet": [],
  "auditEvents": [],
  "correlationId": null,
  "counts": {
    "candidates": 2,
    "scored": 1,
    "gateBlocked": 0
  }
};

const canonicalNetworkProducer = {
  "source": "api",
  "heatZones": [],
  "listingSources": [],
  "listings": [
    {
      "id": "listing-candidate-A",
      "addressId": "address-candidate-A",
      "sourceId": "",
      "sourceListingId": "",
      "status": "active",
      "rentPerMonth": 40000,
      "areaPing": 24,
      "floor": "1F",
      "frontageMeters": 0.0,
      "listingConfidence": null,
      "listingConfidenceProvenance": "unmeasured",
      "geocodeConfidence": 0.9,
      "sourceUrl": "current-input-candidate-A",
      "snapshotId": "current-input-candidate-A",
      "hardRuleFailures": null,
      "tenantId": "synthetic-projection-tenant-a",
      "address": "Synthetic candidate-A",
      "latitude": 25.03,
      "longitude": 121.56,
      "h3Index": ""
    }
  ],
  "candidates": [
    {
      "id": "candidate-A",
      "listingId": "listing-candidate-A",
      "targetFormatCode": "synthetic-format",
      "heatZoneId": "synthetic-zone",
      "title": "listing-candidate-A 候選點",
      "address": "",
      "status": "new",
      "score": 99,
      "recommendation": "GO",
      "modelVersion": "stale-cached-model",
      "datasetSnapshotId": null,
      "reviewId": null,
      "missingData": [],
      "feasibilityFlags": [],
      "prior90dCellNetRevenue": null,
      "prior90dCellTransactionCount": null,
      "prior90dCellStoreCount": null,
      "featureSnapshotTime": null,
      "latitude": 25.03,
      "longitude": 121.56,
      "geocodeConfidence": 0.9,
      "h3Index": null
    }
  ],
  "siteReviews": [],
  "assistedIntakes": []
};

const clone = <T,>(value: T): T => structuredClone(value);
const listings = (value: unknown) => inspectNetworkListingsSnapshot(value as never);
const scoring = (value: unknown) => inspectNetworkScoringSnapshot(value as never);

describe("rendered Network read contracts", () => {
  it.each([false, 0, []])("refuses a malformed top-level DTO %j without throwing", (value) => {
    expect(() => listings(value)).not.toThrow(); expect(listings(value)).toBe("error");
    expect(() => scoring(value)).not.toThrow(); expect(scoring(value)).toBe("error");
  });
  it("accepts actual fixture-mode application DTOs and canonical nullable report DTOs", () => {
    expect(listings(listingProducer)).toBe("ready");
    expect(listings(canonicalNetworkProducer)).toBe("ready");
    expect(canonicalNetworkProducer.listings[0].status).toBe("active");
    expect(canonicalNetworkProducer.listings[0].hardRuleFailures).toBeNull();
    const nullableListingEvidence: any = clone(canonicalNetworkProducer);
    nullableListingEvidence.listings[0].sourceEvidence = null;
    expect(listings(nullableListingEvidence)).toBe("ready");
    expect(scoring(scoringProducer)).toBe("ready");
    expect(scoring(canonicalProducer)).toBe("ready");
    expect(canonicalProducer.candidates[1].modelVersion).toBeNull();
    expect(canonicalProducer.scorecards[0].score).toBeNull();
  });
  it("retains authorized empty, scoped-withheld aggregates, source and seed outcomes", () => {
    const emptyNetwork = { ...listingProducer, heatZones: [], listings: [], candidates: [] };
    expect(listings(emptyNetwork)).toBe("empty");
    expect(listings({ ...listingProducer, heatZones: [] })).toBe("ready");
    expect(listings({ ...listingProducer, source: undefined })).toBe("empty");
    expect(listings({ ...listingProducer, source: "fixture" })).toBe("seed");
    expect(scoring({ ...canonicalProducer, candidates: [], scorecards: [] })).toBe("empty");
    expect(scoring({ ...canonicalProducer, source: "fixture" })).toBe("seed");
  });
  it.each([
    ["null candidate collection", (x: any) => { x.candidates = null; }],
    ["listing collection object", (x: any) => { x.listings = {}; }],
    ["null candidate beside an authorized row", (x: any) => { x.candidates = [{ ...canonicalProducer.candidates[0], status: "scored", missingData: [] }, null]; }],
    ["missing candidate missingData array", (x: any) => { x.candidates = [{ ...canonicalProducer.candidates[0], status: "scored" }]; }],
    ["candidate missingData object", (x: any) => { x.candidates = [{ ...canonicalProducer.candidates[0], status: "scored", missingData: {} }]; }],
    ["object rendered as candidate title", (x: any) => { x.candidates = [{ ...canonicalProducer.candidates[0], title: {}, status: "scored", missingData: [] }]; }],
    ["null listing", (x: any) => { x.listings.push(null); }],
    ["null source", (x: any) => { x.listingSources.push(null); }],
    ["source name object", (x: any) => { x.listingSources[0].name = {}; }],
    ["null HeatZone", (x: any) => { x.heatZones.push(null); }],
    ["missing HeatZone centroid", (x: any) => { delete x.heatZones[0].centroid; }],
    ["nonfinite centroid", (x: any) => { x.heatZones[0].centroid[0] = NaN; }],
    ["HeatZone reason object", (x: any) => { x.heatZones[0].reasons = [{}]; }],
    ["null site review", (x: any) => { x.siteReviews = [null]; }],
    ["missing reviewer array", (x: any) => { x.siteReviews = [{ id: "review", candidateId: "candidate", status: "pending" }]; }],
    ["listing address React object", (x: any) => { x.listings[0].address = {}; }],
    ["listing floor React object", (x: any) => { x.listings[0].floor = {}; }],
    ["listing source URL React object", (x: any) => { x.listings[0].sourceUrl = {}; }],
    ["listing Fit React object", (x: any) => { x.listings[0].fitScore = {}; }],
    ["blank row identity", (x: any) => { x.listings[0].id = " "; }],
  ])("rejects the complete network read for %s", (_name, damage) => {
    const value: any = clone(listingProducer); damage(value);
    expect(() => listings(value)).not.toThrow();
    expect(listings(value)).toBe("error");
  });
  it.each([
    ["null candidate collection", (x: any) => { x.candidates = null; }],
    ["scorecard collection object", (x: any) => { x.scorecards = {}; }],
    ["null compare object", (x: any) => { x.compare = null; }],
    ["compare column collection object", (x: any) => { x.compare.columns = {}; }],
    ["null candidate", (x: any) => { x.candidates.push(null); }],
    ["null Gate", (x: any) => { x.candidates[0].gate = null; }],
    ["Gate missing list", (x: any) => { x.candidates[0].gate.missing = null; }],
    ["Gate check null", (x: any) => { x.candidates[0].gate.checks = [null]; }],
    ["Gate check unknown state", (x: any) => { x.candidates[0].gate.checks[0].state = "approved"; }],
    ["null report", (x: any) => { x.scorecards.push(null); }],
    ["blank report candidate identity", (x: any) => { x.scorecards[0].id = ""; }],
    ["null band", (x: any) => { x.scorecards[0].band = null; }],
    ["band React object", (x: any) => { x.scorecards[0].band.p10 = {}; }],
    ["nonfinite band", (x: any) => { x.scorecards[0].band.p10 = Infinity; }],
    ["null revenue path", (x: any) => { x.scorecards[0].revenuePath = null; }],
    ["invalid interval", (x: any) => { x.scorecards[0].revenuePath.m1 = { p10: 0, p50: null, p90: 2 }; }],
    ["missing drivers", (x: any) => { delete x.scorecards[0].drivers; }],
    ["reason object", (x: any) => { x.scorecards[0].reasons = [{}]; }],
    ["null subScores", (x: any) => { x.scorecards[0].subScores = null; }],
    ["unknown recommendation", (x: any) => { x.scorecards[0].recommendation = "APPROVED"; }],
    ["nonfinite confidence", (x: any) => { x.scorecards[0].confidence = NaN; }],
    ["null compare metric", (x: any) => { x.compare.metrics = [null]; }],
    ["null compare value", (x: any) => { x.compare.metrics[0].values.push(null); }],
    ["missing compare metric values", (x: any) => { delete x.compare.metrics[0].values; }],
    ["null comparison column", (x: any) => { x.compare.columns.push(null); }],
    ["null primary recommendation", (x: any) => { x.compare.recommendation.primary = null; }],
    ["malformed priority list", (x: any) => { x.compare.recommendation.priorityList = [null]; }],
    ["compare membership object", (x: any) => { x.compareSet.push({}); }],
  ])("rejects the complete scoring read for %s", (_name, damage) => {
    const value: any = clone(scoringProducer); damage(value);
    expect(() => scoring(value)).not.toThrow();
    expect(scoring(value)).toBe("error");
  });
  it("preserves measured zero and unmeasured nullable outcomes without filling values", () => {
    const value: any = clone(canonicalProducer);
    value.scorecards[0].confidence = 0; value.scorecards[0].payback = null;
    value.scorecards[0].revenuePath.m1 = null;
    expect(scoring(value)).toBe("ready");
    expect(value.scorecards[0].confidence).toBe(0);
    expect(value.scorecards[0].payback).toBeNull();
    expect(value.scorecards[0].revenuePath.m1).toBeNull();
  });
  it("accepts optional canonical read evidence with public address masking, measured zero, and nullable provenance", () => {
    const value: any = clone(canonicalProducer);
    const candidate = value.candidates[0]; candidate.address = null;
    candidate.readEvidence = { source: "canonical", candidateId: candidate.id, listingId: candidate.listingId,
      addressId: "recorded-address", datasetSnapshotId: null, fields: { sourceConfidence: {
        value: 0, unit: "ratio", availability: "recorded", reason: null,
        provenance: { recordType: "Listing", recordId: candidate.listingId, fieldPath: "sourceConfidence", snapshotId: null },
      } } };
    candidate.pipelineEvidence = { approved: { source: "canonical", candidateId: candidate.id,
      availability: "unavailable", matches: null, producer: "recorded-producer", recordId: null, status: null, reason: null } };
    expect(scoring(value)).toBe("ready");
    expect(candidate.address).toBeNull(); expect(candidate.readEvidence.fields.sourceConfidence.value).toBe(0);
  });
  it.each([
    ["read value React object", (x: any) => { x.readEvidence.fields.rent.value = {}; }],
    ["nonfinite read value", (x: any) => { x.readEvidence.fields.rent.value = NaN; }],
    ["read value array object", (x: any) => { x.readEvidence.fields.rent.value = [{}]; }],
    ["read provenance null", (x: any) => { x.readEvidence.fields.rent.provenance = null; }],
    ["read provenance ID object", (x: any) => { x.readEvidence.fields.rent.provenance.recordId = {}; }],
    ["read reason React object", (x: any) => { x.readEvidence.fields.rent.reason = {}; }],
    ["pipeline reason React object", (x: any) => { x.pipelineEvidence.approved.reason = {}; }],
    ["pipeline availability object", (x: any) => { x.pipelineEvidence.approved.availability = {}; }],
  ])("refuses malformed optional evidence for %s", (_name, damage) => {
    const value: any = clone(canonicalProducer); const candidate = value.candidates[0];
    candidate.readEvidence = { source: "canonical", candidateId: candidate.id, listingId: candidate.listingId,
      addressId: "recorded-address", datasetSnapshotId: null, fields: { rent: {
        value: null, unit: null, availability: "unmeasured", reason: null,
        provenance: { recordType: "Listing", recordId: candidate.listingId, fieldPath: "rent", snapshotId: null },
      } } };
    candidate.pipelineEvidence = { approved: { source: "canonical", candidateId: candidate.id,
      availability: "unavailable", matches: null, producer: "recorded-producer", recordId: null, status: null, reason: null } };
    damage(candidate); expect(() => scoring(value)).not.toThrow(); expect(scoring(value)).toBe("error");
  });

});

describe("successful HTTP response body shape", () => {
  it.each([null, false, 0, []])("shows a typed read failure for HTTP 200 body %j", async (body) => {
    vi.stubEnv("NEXT_PUBLIC_PRODUCTION_MODE", "true");
    window.history.replaceState(null, "", "/operator?ws=network&tab=sitescore");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), {
      status: 200, headers: { "Content-Type": "application/json", "X-Correlation-Id": "corr-malformed-body" },
    })));
    render(createElement(NetworkFindAreasWorkspace, { activeRoleId: "expansion-manager" }));
    const gate = await screen.findByTestId("operator-data-unavailable");
    await vi.waitFor(() => expect(gate).toHaveAttribute("data-status", "error"));
    expect(gate).not.toHaveAttribute("data-status", "empty");
    expect(screen.getByTestId("operator-data-unavailable-correlation")).toHaveTextContent("corr-malformed-body");
    expect(screen.getByTestId("operator-data-unavailable-technical")).toHaveTextContent("200");
    expect(screen.queryByTestId("sitescore-card-CS-1001")).not.toBeInTheDocument();
  });
});
