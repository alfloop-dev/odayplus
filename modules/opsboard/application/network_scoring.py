"""Network SiteScore scoring service for Operator Console R4.

Owns the task-scoped scoring surface behind
``/api/v1/operator/network-scoring``:

- Candidate data-completeness **Gate**: address / geocode / rent / area /
  floor / hard-rule must all be present (geocode >= 0.80) before a candidate
  can be scored. Missing data blocks scoring *server-side* — the gate is
  enforced here, not only in the UI.
- Re-runnable SiteScore job: single-candidate and batch scoring persist
  deterministic scorecards and re-runs are idempotent.
- R4 scorecard: score, GO/WAIT/REJECT recommendation, M1/M3/M6/M12 revenue
  path, P10/P50/P90 band, six risk-breakdown sub-scores, support reasons,
  primary risks, and rec-specific conditions/reject reasons.
- Compare recommendation: primary (GO) / alternate / avoid (REJECT) derived
  consistently from the persisted, score-sorted results.

The service is deliberately in-memory for the Operator Console product slice.
It is deterministic and narrow enough to compose with the R4-005 network
listing intake and the SiteScore Review surface without owning those layers.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

MODEL_VERSION = "SiteScore v2.3"

# GO >= 80, WAIT 60-79, REJECT < 60. Recommendation is derived from the score
# so that batch results and Compare stay consistent with the scorecard.
GO_THRESHOLD = 80
WAIT_THRESHOLD = 60

# Geocode confidence floor for the data-completeness gate.
GEOCODE_MIN_CONFIDENCE = 0.80

# The six required data dimensions the gate enforces before scoring.
REQUIRED_DATA_DIMENSIONS = ("address", "geocode", "rent", "area", "floor", "hardRule")


class NetworkScoringNotFound(RuntimeError):
    """Raised when a candidate id is unknown."""


class NetworkScoringGateError(RuntimeError):
    """Raised when scoring is attempted on a data-incomplete candidate."""

    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _recommendation_for_score(score: int) -> str:
    if score >= GO_THRESHOLD:
        return "GO"
    if score >= WAIT_THRESHOLD:
        return "WAIT"
    return "REJECT"


def _seed_candidates() -> list[dict[str, Any]]:
    """Package-6 canonical Network R4 candidate set.

    CS-1001 信義松仁 (GO 82) is the golden-flow candidate derived from L-2024.
    CS-1002 板橋府中 (WAIT 76) exposes R4 conditions. CS-1004 大安和平
    (REJECT 49) exposes reject reasons. CS-1003 中壢中原 is gate-blocked:
    geocode 0.71 (< 0.80) plus missing field-survey/lease/address-confirm data
    so scoring is locked server-side.
    """

    return [
        {
            "id": "CS-1001",
            "listingId": "L-2024",
            "heatZoneId": "HZ-01",
            "title": "信義松仁候選點",
            "zoneLabel": "信義松仁 86",
            "address": "台北市信義區松仁路 96 號 1F",
            "district": "台北市信義區",
            "modelVersion": MODEL_VERSION,
            "datasetSnapshotId": "FS-20260704-0600",
            "generatedAt": "2026-07-04 06:10",
            "data": {
                "address": {"present": True, "note": "台北市信義區松仁路 96 號 1F"},
                "geocode": {"present": True, "confidence": 0.94, "note": "0.94（高）"},
                "rent": {"present": True, "note": "NT$58,000／月（區間 P45）"},
                "area": {"present": True, "note": "18 坪"},
                "floor": {"present": True, "note": "1F 臨路"},
                "hardRule": {"present": True, "pass": True, "note": "3/3 通過（面積／樓層／用途）"},
                "sourceConfidence": {"present": True, "note": "高"},
                "fieldSurvey": {"present": True, "note": "已完成（現勘照片 6 張）"},
                "brokerContact": {"present": True, "note": "已聯絡（王仲介）"},
            },
            "expected": {
                "score": 82,
                "payback": "22 個月",
                "confidence": "中高",
                "revenuePath": {"m1": 182, "m3": 268, "m6": 342, "m12": 428},
                "band": {"p10": "NT$356K", "p50": "NT$428K", "p90": "NT$512K"},
                "subScores": {
                    "rentReasonableness": "合理（區間 P45）",
                    "cannibalization": "4%（低）",
                    "competition": "中（無 24h 競店）",
                    "demand": "強（夜間人流 84）",
                    "poiFit": "高",
                    "access": "中（週末停車不易）",
                },
                "capex": "NT$1.6M（14 台配置）",
                "rentAssumption": "月租 NT$58,000（區間 P45）",
                "drivers": ["夜間人流指數 84", "千戶級社區 6 個", "無 24h 競店"],
                "reasons": [
                    "住宅＋商辦混合，夜間洗烘需求強",
                    "既有店 650m，稀釋僅 4%",
                    "降租後租金落於區間 P45，仍可接受",
                ],
                "risks": ["週末停車不易", "租約含 3% 年調條款", "晚間人流資料信心中等"],
                "conditions": [],
            },
        },
        {
            "id": "CS-1002",
            "listingId": "L-2025",
            "heatZoneId": "HZ-02",
            "title": "板橋府中候選點",
            "zoneLabel": "板橋府中 78",
            "address": "新北市板橋區府中路 XX 號 1F",
            "district": "新北市板橋區",
            "modelVersion": MODEL_VERSION,
            "datasetSnapshotId": "FS-20260703-0600",
            "generatedAt": "2026-07-13 16:42",
            "reviewId": "RV-701",
            "data": {
                "address": {"present": True, "note": "新北市板橋區府中路 XX 號 1F"},
                "geocode": {"present": True, "confidence": 0.92, "note": "0.92（高）"},
                "rent": {"present": True, "note": "NT$52,000／月（區間 P70）"},
                "area": {"present": True, "note": "22 坪"},
                "floor": {"present": True, "note": "1F 近捷運出口"},
                "hardRule": {"present": True, "pass": True, "note": "3/3 通過"},
                "sourceConfidence": {"present": True, "note": "高"},
                "fieldSurvey": {"present": True, "note": "已完成（6/28 現勘）"},
                "brokerContact": {"present": True, "note": "已聯絡（王仲介）"},
            },
            "expected": {
                "score": 76,
                "payback": "27 個月",
                "confidence": "中",
                "revenuePath": {"m1": 142, "m3": 221, "m6": 289, "m12": 372},
                "band": {"p10": "NT$308K", "p50": "NT$372K", "p90": "NT$431K"},
                "subScores": {
                    "rentReasonableness": "偏高（區間 P70）",
                    "cannibalization": "11%（中）",
                    "competition": "高（150m 強競店）",
                    "demand": "中高（通勤指數 82）",
                    "poiFit": "高（捷運＋學校＋超商 5）",
                    "access": "中（站前施工圍籬）",
                },
                "capex": "NT$1.8M（16 台配置）",
                "rentAssumption": "月租 NT$52,000（區間 P70）",
                "drivers": ["捷運通勤人流", "舊公寓無烘衣機比例高"],
                "reasons": ["人流量體大", "夜間治安佳", "距捷運出口 80m，通勤動線佳"],
                "risks": ["站前施工至 12 月", "與府中店重疊 11%", "晚間人流資料信心中等"],
                "conditions": [
                    "站前施工影響需於 Q4 前複評",
                    "租金議價至 NT$48,000 以下",
                    "補充晚間人流資料",
                ],
            },
        },
        {
            "id": "CS-1003",
            "listingId": "L-2026",
            "heatZoneId": "HZ-05",
            "title": "中壢中原候選點",
            "zoneLabel": "中壢中原 69",
            "address": "中壢區中北路 XX 號 1F",
            "district": "桃園市中壢區",
            "modelVersion": MODEL_VERSION,
            "datasetSnapshotId": "FS-20260704-0600",
            "generatedAt": "",
            # Gate-blocked: geocode below the 0.80 confidence floor plus three
            # missing evidence items. Scoring is locked server-side.
            "data": {
                "address": {"present": True, "note": "中壢區中北路 XX 號 1F（待人工確認）"},
                "geocode": {"present": False, "confidence": 0.71, "note": "0.71（中）— 待人工確認"},
                "rent": {"present": True, "note": "NT$36,000／月（區間 P25）"},
                "area": {"present": True, "note": "16 坪"},
                "floor": {"present": True, "note": "1F"},
                "hardRule": {"present": True, "pass": True, "note": "3/3 通過"},
                "sourceConfidence": {"present": True, "note": "中"},
                "fieldSurvey": {"present": False, "note": "未排定"},
                "brokerContact": {"present": False, "note": "未聯絡"},
            },
            "missingEvidence": ["平日人流樣本", "房東租期意願", "地址人工確認"],
            "expected": None,
        },
        {
            "id": "CS-1004",
            "listingId": "L-2027",
            "heatZoneId": "HZ-03",
            "title": "大安和平候選點",
            "zoneLabel": "大安和平 74",
            "address": "大安區和平東路二段 XX 號 1F",
            "district": "台北市大安區",
            "modelVersion": MODEL_VERSION,
            "datasetSnapshotId": "FS-20260630-0600",
            "generatedAt": "2026-06-30 14:20",
            "reviewId": "RV-698",
            "data": {
                "address": {"present": True, "note": "大安區和平東路二段 XX 號 1F"},
                "geocode": {"present": True, "confidence": 0.90, "note": "0.90（高）"},
                "rent": {"present": True, "note": "NT$64,000／月（區間 P90）"},
                "area": {"present": True, "note": "20 坪"},
                "floor": {"present": True, "note": "1F"},
                "hardRule": {"present": True, "pass": True, "note": "3/3 通過"},
                "sourceConfidence": {"present": True, "note": "高"},
                "fieldSurvey": {"present": True, "note": "已完成（6/24 現勘）"},
                "brokerContact": {"present": True, "note": "已聯絡"},
            },
            "expected": {
                "score": 49,
                "payback": "41 個月",
                "confidence": "中高",
                "revenuePath": {"m1": 102, "m3": 156, "m6": 203, "m12": 268},
                "band": {"p10": "NT$214K", "p50": "NT$268K", "p90": "NT$322K"},
                "subScores": {
                    "rentReasonableness": "過高（區間 P90）",
                    "cannibalization": "12%（中高）",
                    "competition": "中",
                    "demand": "中（學區穩定）",
                    "poiFit": "中（學校 3 · 傳統市場）",
                    "access": "低（巷弄面寬不足）",
                },
                "capex": "NT$1.7M（14 台配置）",
                "rentAssumption": "月租 NT$64,000（區間 P90）",
                "drivers": ["學區穩定需求"],
                "reasons": ["住宅密度高", "高齡住戶代收送潛力"],
                "risks": [
                    "租金 P90，回本期 41 個月",
                    "與大安和平店重疊 12%",
                    "停車與面寬條件弱",
                ],
                "conditions": [
                    "租金過高（P90），回本期 41 個月超出品牌上限 30 個月",
                    "與大安和平店服務圈重疊 12%，自家稀釋高",
                    "面寬與停車條件不足，大件洗客群流失",
                ],
            },
        },
    ]


# Candidates that begin already scored so the SiteScore Lab and Compare render
# a populated golden flow. CS-1003 stays unscored because its gate is blocked.
_INITIALLY_SCORED = ("CS-1001", "CS-1002", "CS-1004")

# Default compare basket — primary / alternate / avoid across the three scored
# recommendations (GO / WAIT / REJECT).
_DEFAULT_COMPARE_SET = ("CS-1001", "CS-1002", "CS-1004")


class NetworkScoringService:
    """Application service for R4 Candidate gate + SiteScore + Compare."""

    def __init__(self) -> None:
        self._candidates: list[dict[str, Any]] = _seed_candidates()
        self._scores: dict[str, dict[str, Any]] = {}
        self._audit_events: list[dict[str, Any]] = []
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._compare_set: list[str] = list(_DEFAULT_COMPARE_SET)
        for candidate in self._candidates:
            if candidate["id"] in _INITIALLY_SCORED:
                self._scores[candidate["id"]] = self._build_scorecard(candidate)

    # -- public API ----------------------------------------------------

    def reset(self) -> dict[str, Any]:
        self.__init__()
        return self.snapshot()

    def snapshot(self, *, correlation_id: str | None = None) -> dict[str, Any]:
        candidates = [self._candidate_view(candidate) for candidate in self._candidates]
        scorecards = self._sorted_scorecards()
        return {
            "source": "api",
            "modelVersion": MODEL_VERSION,
            "candidates": candidates,
            "scorecards": scorecards,
            "batchResults": self._batch_results(scorecards),
            "compare": self._compare(scorecards),
            "compareSet": list(self._compare_set),
            "auditEvents": _copy(self._audit_events),
            "correlationId": correlation_id,
            "counts": {
                "candidates": len(self._candidates),
                "scored": len(self._scores),
                "gateBlocked": sum(
                    1 for candidate in self._candidates if not self._gate(candidate)["passed"]
                ),
            },
        }

    def score_candidate(
        self,
        *,
        candidate_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        cache_key = ("score", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        candidate = self._candidate(candidate_id)
        gate = self._gate(candidate)
        if not gate["passed"]:
            raise NetworkScoringGateError(
                gate["blockNote"] or f"{candidate_id} is missing required data and cannot be scored",
                missing=gate["missing"],
            )

        scorecard = self._build_scorecard(candidate)
        self._scores[candidate_id] = scorecard
        audit = self._audit(
            action="sitescore.run",
            candidate_id=candidate_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "score": scorecard["score"],
                "recommendation": scorecard["recommendation"],
                "modelVersion": scorecard["modelVersion"],
                "datasetSnapshotId": scorecard["datasetSnapshotId"],
            },
        )
        result = {
            "candidate": self._candidate_view(candidate),
            "scorecard": _copy(scorecard),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def score_batch(
        self,
        *,
        actor_role_id: str,
        actor_name: str | None,
        candidate_ids: list[str] | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        cache_key = ("batch", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        targets = candidate_ids or [candidate["id"] for candidate in self._candidates]
        scored: list[str] = []
        skipped: list[dict[str, Any]] = []
        for candidate_id in targets:
            candidate = self._candidate(candidate_id)
            gate = self._gate(candidate)
            if not gate["passed"]:
                skipped.append(
                    {"candidateId": candidate_id, "reason": gate["blockNote"], "missing": gate["missing"]}
                )
                continue
            self._scores[candidate_id] = self._build_scorecard(candidate)
            scored.append(candidate_id)

        scorecards = self._sorted_scorecards()
        audit = self._audit(
            action="sitescore.batch",
            candidate_id="batch",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"scored": scored, "skipped": [item["candidateId"] for item in skipped]},
        )
        result = {
            "scoredCandidateIds": scored,
            "skipped": skipped,
            "batchResults": self._batch_results(scorecards),
            "scorecards": scorecards,
            "compare": self._compare(scorecards),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }
        if idempotency_key:
            self._idempotency_cache[cache_key] = _copy(result)
        return result

    def set_compare_set(
        self,
        *,
        candidate_ids: list[str],
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        ordered: list[str] = []
        for candidate_id in candidate_ids:
            candidate = self._candidate(candidate_id)
            if candidate["id"] not in ordered:
                ordered.append(candidate["id"])
        self._compare_set = ordered
        audit = self._audit(
            action="compare.set",
            candidate_id="compare",
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"compareSet": ordered},
        )
        scorecards = self._sorted_scorecards()
        return {
            "compareSet": ordered,
            "compare": self._compare(scorecards),
            "auditEvent": audit,
            "correlationId": correlation_id,
        }

    # -- internals -----------------------------------------------------

    def _candidate(self, candidate_id: str) -> dict[str, Any]:
        for candidate in self._candidates:
            if candidate["id"] == candidate_id:
                return candidate
        raise NetworkScoringNotFound(f"candidate {candidate_id} not found")

    def _gate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Evaluate the candidate data-completeness gate.

        Enforces the six required dimensions (address / geocode / rent / area /
        floor / hard-rule). Geocode also needs confidence >= 0.80. Returns a
        structured gate with per-dimension checks, an aggregate ``passed`` flag,
        the missing dimension labels, and the blocking note.
        """

        data = candidate.get("data", {})
        labels = {
            "address": "地址定位",
            "geocode": "Geocode ≥ 0.80",
            "rent": "租金",
            "area": "坪數",
            "floor": "樓層",
            "hardRule": "Hard rule",
        }
        checks: list[dict[str, Any]] = []
        missing: list[str] = []
        geocode_ok = True
        hard_rule_ok = True
        for dimension in REQUIRED_DATA_DIMENSIONS:
            field = data.get(dimension, {})
            present = bool(field.get("present"))
            if dimension == "geocode":
                confidence = float(field.get("confidence", 0.0))
                present = present and confidence >= GEOCODE_MIN_CONFIDENCE
                geocode_ok = present
            if dimension == "hardRule":
                present = present and bool(field.get("pass", True))
                hard_rule_ok = present
            state = "ok" if present else "fail"
            checks.append(
                {"key": dimension, "label": labels[dimension], "state": state, "note": field.get("note", "")}
            )
            if not present:
                missing.append(labels[dimension])

        other_missing = list(candidate.get("missingEvidence", []))
        passed = not missing
        if not geocode_ok:
            state = "geo"
            block_note = "地址／Geocode 未確認 — 需人工確認地址後才能評分"
        elif not hard_rule_ok:
            state = "hard"
            block_note = "硬規則未通過 — 建議標記不適合並退回物件"
        elif missing:
            state = "needdata"
            block_note = "缺必要資料：" + "、".join(missing)
        elif other_missing:
            # Non-blocking supplementary evidence still outstanding.
            state = "warn"
            block_note = "缺補充資料：" + "、".join(other_missing)
            passed = True
        else:
            state = "ready"
            block_note = ""

        return {
            "state": state,
            "passed": passed,
            "missing": missing + (other_missing if not passed else []),
            "otherMissing": other_missing,
            "blockNote": block_note,
            "checks": checks,
            "okCount": sum(1 for check in checks if check["state"] == "ok"),
            "totalCount": len(checks),
        }

    def _build_scorecard(self, candidate: dict[str, Any]) -> dict[str, Any]:
        expected = candidate.get("expected")
        if expected is None:
            raise NetworkScoringGateError(
                f"{candidate['id']} has no scoreable feature snapshot", missing=candidate.get("missingEvidence")
            )
        score = int(expected["score"])
        recommendation = _recommendation_for_score(score)
        conditions = list(expected.get("conditions", []))
        if recommendation == "GO":
            condition_title = ""
            conditions = []
        elif recommendation == "WAIT":
            condition_title = "通過條件 — 符合後可重評為 GO"
        else:
            condition_title = "拒絕原因"
        return {
            "id": candidate["id"],
            "title": candidate["title"],
            "zoneLabel": candidate["zoneLabel"],
            "heatZoneId": candidate["heatZoneId"],
            "score": score,
            "recommendation": recommendation,
            "modelVersion": candidate["modelVersion"],
            "datasetSnapshotId": candidate["datasetSnapshotId"],
            "generatedAt": candidate.get("generatedAt", ""),
            "confidence": expected.get("confidence", ""),
            "payback": expected.get("payback", ""),
            "revenuePath": _copy(expected.get("revenuePath", {})),
            "band": _copy(expected.get("band", {})),
            "subScores": _copy(expected.get("subScores", {})),
            "capex": expected.get("capex", ""),
            "rentAssumption": expected.get("rentAssumption", ""),
            "drivers": list(expected.get("drivers", [])),
            "reasons": list(expected.get("reasons", [])),
            "risks": list(expected.get("risks", [])),
            "conditions": conditions,
            "conditionTitle": condition_title,
            "reviewId": candidate.get("reviewId"),
        }

    def _sorted_scorecards(self) -> list[dict[str, Any]]:
        scored = [_copy(self._scores[cid]) for cid in self._scores]
        # Sort by score desc, then id asc for a deterministic tie-break.
        scored.sort(key=lambda card: (-card["score"], card["id"]))
        return scored

    def _candidate_view(self, candidate: dict[str, Any]) -> dict[str, Any]:
        gate = self._gate(candidate)
        scorecard = self._scores.get(candidate["id"])
        if scorecard is not None:
            stage = "rejected" if scorecard["recommendation"] == "REJECT" else "evaluated"
            if candidate.get("reviewId") and scorecard["recommendation"] != "REJECT":
                stage = "pendingreview"
        elif not gate["passed"]:
            stage = "needdata"
        else:
            stage = "ready"
        return {
            "id": candidate["id"],
            "listingId": candidate.get("listingId"),
            "heatZoneId": candidate["heatZoneId"],
            "title": candidate["title"],
            "zoneLabel": candidate["zoneLabel"],
            "address": candidate["address"],
            "district": candidate.get("district", ""),
            "modelVersion": candidate["modelVersion"],
            "datasetSnapshotId": candidate["datasetSnapshotId"],
            "stage": stage,
            "gate": gate,
            "scored": scorecard is not None,
            "score": scorecard["score"] if scorecard else None,
            "recommendation": scorecard["recommendation"] if scorecard else None,
            "reviewId": candidate.get("reviewId"),
            "inCompare": candidate["id"] in self._compare_set,
        }

    def _batch_results(self, scorecards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "rank": index + 1,
                "priority": f"P{index + 1}",
                "id": card["id"],
                "title": card["title"],
                "score": card["score"],
                "recommendation": card["recommendation"],
                "m12P50": card["band"].get("p50", ""),
                "payback": card["payback"],
                "cannibalization": card["subScores"].get("cannibalization", ""),
                "inCompare": card["id"] in self._compare_set,
            }
            for index, card in enumerate(scorecards)
        ]

    def _compare(self, scorecards: list[dict[str, Any]]) -> dict[str, Any]:
        by_id = {card["id"]: card for card in scorecards}
        selected = [by_id[cid] for cid in self._compare_set if cid in by_id]
        selected.sort(key=lambda card: (-card["score"], card["id"]))

        best = selected[0] if selected else None
        avoid = next(
            (card for card in selected if card["recommendation"] == "REJECT" and (not best or card["id"] != best["id"])),
            None,
        )
        alternate = next(
            (
                card
                for card in selected
                if (not best or card["id"] != best["id"]) and (not avoid or card["id"] != avoid["id"])
            ),
            None,
        )

        metric_defs = [
            ("sitescore", "SiteScore", lambda c: f"{c['score']} {c['recommendation']}"),
            ("m12", "M12 P50", lambda c: c["band"].get("p50", "")),
            ("payback", "回本期", lambda c: c["payback"]),
            ("cannibalization", "自家稀釋", lambda c: c["subScores"].get("cannibalization", "")),
            ("competition", "競店壓力", lambda c: c["subScores"].get("competition", "")),
            ("rent", "租金合理性", lambda c: c["subScores"].get("rentReasonableness", "")),
            ("confidence", "資料信心", lambda c: c["confidence"]),
        ]
        columns = [
            {
                "id": card["id"],
                "title": card["title"],
                "priority": f"P{index + 1}",
                "recommendation": card["recommendation"],
                "score": card["score"],
                "isBest": bool(best and card["id"] == best["id"]),
            }
            for index, card in enumerate(selected)
        ]
        metrics = [
            {
                "key": key,
                "label": label,
                "values": [
                    {"id": card["id"], "text": str(pick(card)), "isBest": bool(best and card["id"] == best["id"])}
                    for card in selected
                ],
            }
            for key, label, pick in metric_defs
        ]

        recommendation = None
        if best is not None:
            recommendation = {
                "primary": {
                    "id": best["id"],
                    "title": best["title"],
                    "recommendation": best["recommendation"],
                    "score": best["score"],
                    "text": (
                        f"推薦：{best['title']}（{best['recommendation']} {best['score']}，"
                        f"回本 {best['payback']}）"
                    ),
                    "why": [
                        f"SiteScore {best['score']}，為目前最高分",
                        f"M12 P50 {best['band'].get('p50', '')}／月",
                        f"回本期 {best['payback']}",
                        f"自家稀釋 {best['subScores'].get('cannibalization', '')}",
                        f"租金可行性：{best['subScores'].get('rentReasonableness', '')}",
                    ],
                },
                "alternate": (
                    {
                        "id": alternate["id"],
                        "title": alternate["title"],
                        "recommendation": alternate["recommendation"],
                        "score": alternate["score"],
                        "text": (
                            f"備選：{alternate['title']}（{alternate['recommendation']} {alternate['score']}）"
                            + ("　— 條件改善後重評" if alternate["recommendation"] == "WAIT" else "")
                        ),
                    }
                    if alternate is not None
                    else None
                ),
                "avoid": (
                    {
                        "id": avoid["id"],
                        "title": avoid["title"],
                        "recommendation": avoid["recommendation"],
                        "score": avoid["score"],
                        "text": (
                            f"不建議：{avoid['title']} — "
                            + (avoid["conditions"][0] if avoid.get("conditions") else (avoid["risks"][0] if avoid.get("risks") else "風險過高"))
                        ),
                    }
                    if avoid is not None
                    else None
                ),
                "priorityList": [
                    {"priority": f"P{index + 1}", "id": card["id"], "title": card["title"], "score": card["score"], "recommendation": card["recommendation"]}
                    for index, card in enumerate(selected)
                ],
            }

        return {
            "columns": columns,
            "metrics": metrics,
            "recommendation": recommendation,
            "empty": not selected,
        }

    def _audit(
        self,
        *,
        action: str,
        candidate_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": f"AUD-SCORE-{uuid.uuid4().hex[:10]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "category": "workflow",
            "action": action,
            "targetType": "candidate",
            "targetId": candidate_id,
            "message": f"{action} recorded for {candidate_id}",
            "correlationId": correlation_id,
            "metadata": metadata,
        }
        self._audit_events.insert(0, event)
        return _copy(event)


__all__ = [
    "MODEL_VERSION",
    "NetworkScoringGateError",
    "NetworkScoringNotFound",
    "NetworkScoringService",
]
