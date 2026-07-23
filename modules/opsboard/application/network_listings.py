"""Network listing intake service for Operator Console R4/R5.

Owns the Listing Radar state used by ``/api/v1/operator/network-listings``:

- R4 HeatZone/listing/candidate identifiers.
- Listing to candidate conversion.
- Duplicate merge while retaining source evidence.
- Hard-rule archive with reason.
- R5 human-assisted URL intake (submit/correct/decide/retry/promote).

Persistence is injected, not assumed. The service composes with two optional
repositories and never reaches into their internals:

- ``listing_repository`` for Listing Radar rows.
- ``intake_repository`` (:class:`AssistedIntakeRepository`) for assisted intake
  records and their idempotency cache.

When a repository is omitted the service falls back to a process-local
implementation (:class:`InMemoryAssistedIntakeRepository`), which keeps tests
and fixture-only runs self-contained. In the composed application both are
durable — see ``shared.infrastructure.persistence.operator_network_listings``
and the wiring in ``apps/api/app/routes/operator.py`` — so intake records and
replayed writes survive a restart.

Writes are deterministic and idempotent: a replayed ``(action, key)`` returns
the original response from the repository-backed idempotency cache rather than
applying the effect twice.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class IntakeIdempotencyRecord:
    """A replayable write response keyed by ``(action, key)``."""

    action: str
    key: str
    response: dict[str, Any]


class AssistedIntakeRepository(Protocol):
    """Public persistence contract for assisted listing intake.

    The service depends on this contract only, so a durable implementation can
    be substituted without the application layer reaching into a generic
    document store or any other backing detail.
    """

    def list_intakes(self) -> list[dict[str, Any]]: ...

    def save_intake(self, intake: dict[str, Any]) -> None: ...

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]: ...

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None: ...

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]: ...

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None: ...

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]: ...

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None: ...

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None: ...

    def save_promotion(self, promo: dict[str, Any]) -> None: ...

    def list_promotions(self) -> list[dict[str, Any]]: ...

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None: ...

    def save_assignment(self, assignment: dict[str, Any]) -> None: ...

    def list_assignments(self) -> list[dict[str, Any]]: ...

    def get_sla(self, sla_instance_id: str) -> dict[str, Any] | None: ...

    def save_sla(self, sla: dict[str, Any]) -> None: ...

    def list_slas(self) -> list[dict[str, Any]]: ...

    def save_saved_view(self, saved_view: dict[str, Any]) -> None: ...

    def list_saved_views(self) -> list[dict[str, Any]]: ...

    def get_api_replay(self, replay_key: str) -> dict[str, Any] | None: ...

    def save_api_replay(self, replay_key: str, replay: dict[str, Any]) -> None: ...

    def clear(self) -> None: ...


@dataclass
class InMemoryAssistedIntakeRepository:
    """Process-local implementation of :class:`AssistedIntakeRepository`.

    Used when the service runs without a durable backend; state is lost on
    restart, which is exactly what the durable implementation exists to fix.
    """

    intakes: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], IntakeIdempotencyRecord] = field(default_factory=dict)
    listing_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidate_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: dict[str, dict[str, Any]] = field(default_factory=dict)
    assignments: dict[str, dict[str, Any]] = field(default_factory=dict)
    slas: dict[str, dict[str, Any]] = field(default_factory=dict)
    saved_views: dict[str, dict[str, Any]] = field(default_factory=dict)
    api_replays: dict[str, dict[str, Any]] = field(default_factory=dict)

    def list_intakes(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.intakes.values()]

    def save_intake(self, intake: dict[str, Any]) -> None:
        self.intakes[intake["id"]] = copy.deepcopy(intake)

    def list_idempotency_records(self) -> list[IntakeIdempotencyRecord]:
        return list(self.idempotency.values())

    def save_idempotency_record(self, record: IntakeIdempotencyRecord) -> None:
        self.idempotency[(record.action, record.key)] = record

    def get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.listing_metadata.get(listing_id) or {})

    def save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self.listing_metadata[listing_id] = copy.deepcopy(metadata)

    def get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.candidate_metadata.get(candidate_id) or {})

    def save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self.candidate_metadata[candidate_id] = copy.deepcopy(metadata)

    def get_promotion(self, promo_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.promotions.get(promo_id))

    def save_promotion(self, promo: dict[str, Any]) -> None:
        self.promotions[promo["promotion_decision_id"]] = copy.deepcopy(promo)

    def list_promotions(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.promotions.values()]

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.assignments.get(assignment_id))

    def save_assignment(self, assignment: dict[str, Any]) -> None:
        self.assignments[assignment["assignment_id"]] = copy.deepcopy(assignment)

    def list_assignments(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.assignments.values()]

    def get_sla(self, sla_instance_id: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.slas.get(sla_instance_id))

    def save_sla(self, sla: dict[str, Any]) -> None:
        self.slas[sla["sla_instance_id"]] = copy.deepcopy(sla)

    def list_slas(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.slas.values()]

    def save_saved_view(self, saved_view: dict[str, Any]) -> None:
        self.saved_views[saved_view["saved_view_id"]] = copy.deepcopy(saved_view)

    def list_saved_views(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(item) for item in self.saved_views.values()]

    def get_api_replay(self, replay_key: str) -> dict[str, Any] | None:
        return copy.deepcopy(self.api_replays.get(replay_key))

    def save_api_replay(self, replay_key: str, replay: dict[str, Any]) -> None:
        self.api_replays[replay_key] = copy.deepcopy(replay)

    def clear(self) -> None:
        self.intakes.clear()
        self.idempotency.clear()
        self.listing_metadata.clear()
        self.candidate_metadata.clear()
        self.promotions.clear()
        self.assignments.clear()
        self.slas.clear()
        self.saved_views.clear()
        self.api_replays.clear()


class NetworkListingNotFound(RuntimeError):
    """Raised when a listing/candidate/zone id is unknown."""


class NetworkListingConflict(RuntimeError):
    """Raised when a requested mutation conflicts with current state."""


class NetworkListingPolicyError(RuntimeError):
    """Raised when a mutation violates the network intake policy."""


def _require_acknowledged_risk(
    *, risk_summary: str | None, risk_acknowledged: bool, action_label: str
) -> str:
    """Validate the caller-supplied risk disclosure for a high-impact write.

    The summary must come from the caller and be acknowledged there: a
    server-invented summary would record consent to text the operator never
    saw, which is exactly what the audit trail is supposed to evidence.
    """
    summary = (risk_summary or "").strip()
    if not summary:
        raise NetworkListingPolicyError(f"risk summary is required to {action_label}")
    if not risk_acknowledged:
        raise NetworkListingPolicyError(f"risk acknowledgement is required to {action_label}")
    return summary


def _require_governed_write_context(
    *,
    idempotency_key: str | None,
    correlation_id: str | None,
    action_label: str,
) -> str:
    key = (idempotency_key or "").strip()
    if not key:
        raise NetworkListingPolicyError(f"idempotency key is required to {action_label}")
    if not (correlation_id or "").strip():
        raise NetworkListingPolicyError(f"correlation id is required to {action_label}")
    return key


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value not in seen:
            output.append(value)
            seen.add(value)
    return output


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _uuid() -> str:
    return str(uuid.uuid4())


def _seed_state() -> dict[str, Any]:
    return {
        "heatZones": [
            {
                "id": "HZ-01",
                "label": "信義松仁生活圈",
                "rank": 1,
                "centroid": [121.5668, 25.0330],
                "demandGap": 0.86,
                "competitionIndex": 0.34,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$52k-88k/month",
                "confidence": 0.93,
                "recommendedLens": "demand",
                "reasons": [
                    "Office-lunch and late-night demand gap remains high.",
                    "Transit and residential POI mix supports ODAY_G2 format.",
                    "L-2024 has clean geocode, floor, area, and source evidence.",
                ],
                "risks": ["Rent negotiation pressure", "Nearby competitor 220m away"],
                "nextStep": "Convert L-2024 into CS-1001 and continue SiteScore evidence review.",
            },
            {
                "id": "HZ-02",
                "label": "板橋府中生活圈",
                "rank": 2,
                "centroid": [121.4575, 25.0100],
                "demandGap": 0.78,
                "competitionIndex": 0.43,
                "cannibalizationRisk": "medium",
                "rentBand": "NT$48k-72k/month",
                "confidence": 0.88,
                "recommendedLens": "fit",
                "reasons": [
                    "Weekend service demand is under-realized.",
                    "L-2025 has duplicate-source coverage after merge.",
                ],
                "risks": ["Duplicate broker postings require evidence merge before review"],
                "nextStep": "Keep L-2025 active after merging L-2029 duplicate source evidence.",
            },
        ],
        "listingSources": [
            {
                "id": "SRC-591",
                "name": "591 licensed broker intake",
                "status": "connected",
                "complianceNote": "R4 proof uses licensed/manual intake fields and preserves source evidence refs.",
                "lastSyncedAt": "2026-07-14T06:10:00Z",
            },
            {
                "id": "SRC-BROKER",
                "name": "Broker confirmation",
                "status": "manualOnly",
                "complianceNote": "Broker notes are retained as source evidence before any merge/archive action.",
                "lastSyncedAt": "2026-07-14T06:12:00Z",
            },
        ],
        "listings": [
            {
                "id": "L-2024",
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
                    "EV-L-2024-BROKER-CALL",
                ],
                "fitScore": 72,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2024",
            },
            {
                "id": "L-2025",
                "sourceId": "SRC-BROKER",
                "sourceListingId": "broker-2025",
                "heatZoneId": "HZ-02",
                "address": "新北市板橋區府中路 52 號 1F",
                "status": "watching",
                "rentPerMonth": 53000,
                "areaPing": 22,
                "floor": "1F",
                "frontageMeters": 5,
                "geocodeConfidence": 0.93,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": ["EV-L-2025-BROKER", "EV-L-2025-GEOCODE"],
                "fitScore": 70,
                "firstSeenAt": "2026-07-13T09:25:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2025",
            },
            {
                "id": "L-2029",
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
                    "EV-L-2029-RENT-MATCH",
                ],
                "fitScore": 70,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2029",
            },
            {
                "id": "L-2030",
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
                "hardRuleFailures": ["area_above_format_maximum", "floor_not_ground_level"],
                "hardRuleSummary": "2/3 blocked: area above 30 ping and second-floor operations required",
                "sourceEvidence": ["EV-L-2030-RAW-591", "EV-L-2030-HARD-RULES"],
                "fitScore": 44,
                "firstSeenAt": "2026-07-14T06:10:00Z",
                "sourceUrl": "https://example.invalid/listings/L-2030",
            },
        ],
        "candidates": [],
        "siteReviews": [],
        "auditEvents": [],
    }


class NetworkListingService:
    """Application service for R4 Listing Radar intake actions."""

    def __init__(
        self,
        listing_repository: Any | None = None,
        intake_repository: AssistedIntakeRepository | None = None,
    ) -> None:
        self._listing_repository = listing_repository
        self._intakes: AssistedIntakeRepository = (
            intake_repository
            if intake_repository is not None
            else InMemoryAssistedIntakeRepository()
        )
        self._state = _seed_state()
        self._idempotency_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_intakes()
        self._load_idempotency_cache()
        self._load_listings()
        self._load_candidates()

    def _load_listings(self) -> None:
        if self._listing_repository is not None:
            repo_listings = self._listing_repository.list_listings()
            if repo_listings:
                self._state["listings"] = []
                for lst in repo_listings:
                    self._state["listings"].append(self._listing_to_dict(lst))
            else:
                for lst_dict in self._state["listings"]:
                    lst_obj, addr_obj, key_obj = self._dict_to_listing(lst_dict)
                    self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

    def _load_candidates(self) -> None:
        if self._listing_repository is not None:
            repo_candidates = self._listing_repository.list_candidates()
            if repo_candidates:
                self._state["candidates"] = []
                for cand in repo_candidates:
                    self._state["candidates"].append(self._candidate_to_dict(cand))
            else:
                for cand_dict in self._state["candidates"]:
                    cand_obj = self._dict_to_candidate(cand_dict)
                    self._listing_repository.save_candidate(cand_obj)

    def _get_listing_metadata(self, listing_id: str) -> dict[str, Any]:
        return self._intakes.get_listing_metadata(listing_id)

    def _save_listing_metadata(self, listing_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_listing_metadata(listing_id, metadata)

    def _listing_runtime_metadata(self, listing_id: str) -> dict[str, Any]:
        metadata = self._get_listing_metadata(listing_id)
        metadata.setdefault("listingRevisions", [])
        metadata.setdefault("identityEdges", [])
        metadata.setdefault("identityDecisions", [])
        return metadata

    def _effective_listing(self, listing: dict[str, Any]) -> dict[str, Any]:
        """Project the current listing without rewriting its historical aggregate."""
        projected = _copy(listing)
        metadata = self._listing_runtime_metadata(listing["id"])
        revisions = [
            revision
            for revision in metadata["listingRevisions"]
            if revision.get("status") == "EFFECTIVE"
        ]
        if revisions:
            current = max(
                revisions,
                key=lambda item: (
                    int(item.get("sequence") or 0),
                    str(item.get("createdAt") or ""),
                    str(item.get("revisionId") or ""),
                ),
            )
            projected.update(_copy(current.get("effectiveValues") or {}))
            projected["currentRevisionId"] = current["revisionId"]
            projected["revisionSequence"] = current["sequence"]
        projected["listingRevisions"] = _copy(metadata["listingRevisions"])
        projected["identityEdges"] = _copy(metadata["identityEdges"])
        return projected

    def list_listing_revisions(self, listing_id: str) -> list[dict[str, Any]]:
        self._listing(listing_id)
        return _copy(self._listing_runtime_metadata(listing_id)["listingRevisions"])

    def list_identity_edges(
        self,
        *,
        listing_id: str | None = None,
        intake_id: str | None = None,
        include_superseded: bool = True,
    ) -> list[dict[str, Any]]:
        listing_ids = (
            [listing_id]
            if listing_id is not None
            else [listing["id"] for listing in self._state["listings"]]
        )
        edges: list[dict[str, Any]] = []
        for current_listing_id in listing_ids:
            self._listing(current_listing_id)
            listing_edges = self._listing_runtime_metadata(current_listing_id)["identityEdges"]
            superseded_edge_ids = {
                superseded_id
                for edge in listing_edges
                for superseded_id in edge.get("supersedesEdgeIds", [])
            }
            for edge in listing_edges:
                if intake_id is not None and edge.get("intakeId") != intake_id:
                    continue
                if not include_superseded and edge["edgeId"] in superseded_edge_ids:
                    continue
                edges.append(_copy(edge))
        return sorted(
            edges,
            key=lambda item: (str(item.get("createdAt") or ""), item["edgeId"]),
        )

    def _append_listing_revision(
        self,
        *,
        intake: dict[str, Any],
        listing_id: str,
        effective_values: dict[str, Any],
        actor_role_id: str,
        actor_name: str | None,
        reason: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        target = self._listing(listing_id)
        metadata = self._listing_runtime_metadata(listing_id)
        revisions = metadata["listingRevisions"]
        prior_projection = self._effective_listing(target)
        sequence = (
            max(
                (int(item.get("sequence") or 0) for item in revisions),
                default=0,
            )
            + 1
        )
        changed_values = {
            "rentPerMonth": effective_values.get("rent", prior_projection.get("rentPerMonth")),
            "areaPing": effective_values.get("areaPing", prior_projection.get("areaPing")),
            "floor": effective_values.get("floor", prior_projection.get("floor")),
        }
        before_values = {key: prior_projection.get(key) for key in changed_values}
        revision = {
            "revisionId": _uuid(),
            "listingId": listing_id,
            "intakeId": intake["id"],
            "sequence": sequence,
            "status": "EFFECTIVE",
            "supersedesRevisionId": (
                prior_projection.get("currentRevisionId") if revisions else None
            ),
            "beforeValues": before_values,
            "effectiveValues": changed_values,
            "reason": reason,
            "actor": actor_name or actor_role_id,
            "actorRoleId": actor_role_id,
            "sourceSnapshotId": intake.get("snapshotId"),
            "parserVersion": intake.get("parserVersion"),
            "correlationId": correlation_id,
            "createdAt": _now(),
            "evidenceState": (
                "COMPLETE"
                if intake.get("snapshotId") and intake.get("parserVersion")
                else "PARTIAL"
            ),
        }
        revisions.append(revision)
        metadata["currentRevisionId"] = revision["revisionId"]
        metadata["currentRevisionSequence"] = sequence
        self._save_listing_metadata(listing_id, metadata)
        return _copy(revision)

    def _append_identity_edge(
        self,
        *,
        intake: dict[str, Any],
        listing_id: str,
        relation: str,
        decision_id: str,
        actor_role_id: str,
        actor_name: str | None,
        reason: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        metadata = self._listing_runtime_metadata(listing_id)
        source_identity = {
            "sourceId": intake.get("sourceId"),
            "sourceListingId": (
                (intake.get("parsedFields") or {})
                .get("providerListingId", {})
                .get("correctedValue")
                or (intake.get("parsedFields") or {})
                .get("providerListingId", {})
                .get("normalizedValue")
                or (intake.get("parsedFields") or {})
                .get("providerListingId", {})
                .get("sourceValue")
            ),
            "canonicalUrl": intake.get("canonicalUrl"),
        }
        superseded_edge_ids = [
            edge["edgeId"]
            for edge in metadata["identityEdges"]
            if edge.get("sourceIdentity") == source_identity
            and edge["edgeId"]
            not in {
                superseded_id
                for candidate in metadata["identityEdges"]
                for superseded_id in candidate.get("supersedesEdgeIds", [])
            }
        ]
        edge = {
            "edgeId": _uuid(),
            "tenantId": intake.get("tenantId"),
            "intakeId": intake["id"],
            "listingId": listing_id,
            "propertyId": f"PROPERTY-{listing_id}",
            "relation": relation,
            "status": "EFFECTIVE",
            "sourceIdentity": source_identity,
            "decisionId": decision_id,
            "reason": reason,
            "actor": actor_name or actor_role_id,
            "actorRoleId": actor_role_id,
            "sourceSnapshotId": intake.get("snapshotId"),
            "parserVersion": intake.get("parserVersion"),
            "correlationId": correlation_id,
            "effectiveFrom": _now(),
            "effectiveTo": None,
            "supersedesEdgeIds": superseded_edge_ids,
            "createdAt": _now(),
            "evidenceState": (
                "COMPLETE"
                if intake.get("snapshotId") and intake.get("parserVersion")
                else "PARTIAL"
            ),
        }
        metadata["identityEdges"].append(edge)
        self._save_listing_metadata(listing_id, metadata)
        return _copy(edge)

    @staticmethod
    def _identity_graph_key(tenant_id: str) -> str:
        return f"__assisted_intake_identity_graph__:{tenant_id}"

    def _identity_graph(self, tenant_id: str) -> dict[str, Any]:
        graph = self._get_listing_metadata(self._identity_graph_key(tenant_id))
        graph.setdefault("decisions", {})
        graph.setdefault("edges", [])
        graph.setdefault("auditEvents", [])
        graph.setdefault("version", 0)
        return graph

    def _save_identity_graph(self, tenant_id: str, graph: dict[str, Any]) -> None:
        self._save_listing_metadata(self._identity_graph_key(tenant_id), graph)

    def get_identity_decision(self, *, tenant_id: str, decision_id: str) -> dict[str, Any] | None:
        decision = self._identity_graph(tenant_id)["decisions"].get(decision_id)
        return _copy(decision) if decision is not None else None

    def list_identity_decisions(
        self,
        *,
        tenant_id: str,
        intake_id: str | None = None,
        match_case_id: str | None = None,
    ) -> list[dict[str, Any]]:
        decisions = self._identity_graph(tenant_id)["decisions"].values()

        def related(decision: dict[str, Any]) -> bool:
            plan = decision.get("plan") or {}
            if intake_id is not None and plan.get("intakeId") != intake_id:
                return False
            if match_case_id is not None and plan.get("matchCaseId") != match_case_id:
                return False
            return True

        return sorted(
            (_copy(decision) for decision in decisions if related(decision)),
            key=lambda decision: (
                str(decision.get("createdAt") or ""),
                str(decision.get("decisionId") or ""),
            ),
        )

    def list_global_identity_edges(
        self,
        *,
        tenant_id: str,
        include_superseded: bool = True,
    ) -> list[dict[str, Any]]:
        edges = self._identity_graph(tenant_id)["edges"]
        superseded_ids = {
            edge_id for edge in edges for edge_id in edge.get("supersedesEdgeIds", [])
        }
        return [
            _copy(edge)
            for edge in edges
            if include_superseded or edge["edgeId"] not in superseded_ids
        ]

    @staticmethod
    def _identity_nodes_for_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nodes: dict[tuple[str, str], dict[str, Any]] = {}
        for edge in edges:
            for field, node_type in (
                ("sourcePropertyId", "PROPERTY"),
                ("targetPropertyId", "PROPERTY"),
                ("propertyId", "PROPERTY"),
                ("listingId", "LISTING"),
                ("intakeId", "INTAKE"),
            ):
                node_id = edge.get(field)
                if node_id:
                    nodes[(node_type, str(node_id))] = {
                        "nodeId": str(node_id),
                        "nodeType": node_type,
                        "status": "EFFECTIVE",
                    }
        return sorted(
            nodes.values(),
            key=lambda node: (node["nodeType"], node["nodeId"]),
        )

    def _identity_graph_snapshot(self, tenant_id: str) -> dict[str, Any]:
        graph = self._identity_graph(tenant_id)
        edges = self.list_global_identity_edges(
            tenant_id=tenant_id,
            include_superseded=True,
        )
        return {
            "version": int(graph.get("version") or 0),
            "nodes": self._identity_nodes_for_edges(edges),
            "edges": edges,
        }

    def _enrich_identity_graph_plan(
        self,
        *,
        tenant_id: str,
        action: str,
        plan: dict[str, Any],
        proposer_name: str,
        proposer_role_id: str,
    ) -> dict[str, Any]:
        """Return the complete, UI-binding graph plan without mutating the graph."""

        authoritative_match_plan = (
            plan.get("graphPlan") if action == "match_decision" else None
        )
        before = _copy(
            (authoritative_match_plan or {}).get("beforeGraph")
            or self._identity_graph_snapshot(tenant_id)
        )
        after_edges = _copy(
            (authoritative_match_plan or {}).get("afterGraph", {}).get("edges")
            or before["edges"]
        )
        redirects: list[dict[str, Any]] = _copy(
            (authoritative_match_plan or {}).get("redirects") or []
        )
        operations = list(
            _copy(
                (authoritative_match_plan or {}).get("operations")
                or plan.get("operations")
                or []
            )
        )

        def planned_edge(
            relation: str,
            source: str,
            target: str,
            *,
            supersedes: list[str] | None = None,
        ) -> None:
            edge = {
                "edgeId": f"planned:{_uuid()}",
                "tenantId": tenant_id,
                "decisionId": None,
                "relation": relation,
                "sourcePropertyId": source,
                "targetPropertyId": target,
                "status": "PROPOSED",
                "supersedesEdgeIds": list(supersedes or []),
            }
            after_edges.append(edge)
            operations.append(
                {
                    "operation": "APPEND_IDENTITY_EDGE",
                    "relation": relation,
                    "sourcePropertyId": source,
                    "targetPropertyId": target,
                    "supersedesEdgeIds": list(supersedes or []),
                }
            )

        if action == "merge":
            target = str(plan["targetPropertyId"])
            for source_value in plan["sourcePropertyIds"]:
                source = str(source_value)
                planned_edge("MERGED_INTO", source, target)
                redirects.append(
                    {
                        "fromPropertyId": source,
                        "toPropertyId": target,
                        "reason": "MERGE",
                        "status": "PROPOSED",
                    }
                )
        elif action == "split":
            source = str(plan["sourcePropertyId"])
            superseded = [str(value) for value in plan.get("sourceIdentityEdgeIds") or []]
            for partition in plan["partitions"]:
                planned_edge(
                    "SPLIT_TO",
                    source,
                    str(partition["targetPropertyId"]),
                    supersedes=superseded,
                )
        elif action in {"unmerge", "reversal"}:
            original_id = str(plan.get("originalDecisionId") or "")
            superseded = [
                str(edge["edgeId"])
                for edge in before["edges"]
                if edge.get("decisionId") == original_id
            ]
            for replacement in plan.get("replacementEdges") or []:
                target = str(replacement["targetPropertyId"])
                for source_edge_id in replacement.get("sourceIdentityEdgeIds") or []:
                    planned_edge(
                        "UNMERGED_TO",
                        str(source_edge_id),
                        target,
                        supersedes=superseded,
                    )

        original_decision_id = plan.get("originalDecisionId")
        original_decision = None
        if original_decision_id:
            original = self._identity_graph(tenant_id)["decisions"].get(
                original_decision_id
            )
            original_decision = {
                "decisionId": original_decision_id,
                "action": (original or {}).get("action"),
                "status": (original or {}).get("status"),
                "version": (original or {}).get("version"),
            }

        enriched = _copy(plan)
        planned_after = (
            _copy((authoritative_match_plan or {}).get("afterGraph"))
            if authoritative_match_plan
            else {
                "version": before["version"] + 1,
                "nodes": self._identity_nodes_for_edges(after_edges),
                "edges": after_edges,
            }
        )
        lineage_impact = _copy(
            (authoritative_match_plan or {}).get("lineageImpact")
            or {
                "appendOnly": True,
                "sourceEvidencePreserved": True,
                "supersededEdgeIds": sorted(
                    {
                        str(edge_id)
                        for edge in after_edges
                        for edge_id in edge.get("supersedesEdgeIds", [])
                    }
                ),
                "affectedDecisionIds": (
                    [str(original_decision_id)] if original_decision_id else []
                ),
                "summary": (
                    "The operation appends immutable identity edges and preserves "
                    "superseded lineage."
                ),
            }
        )
        enriched.update(
            {
                "planId": (
                    (authoritative_match_plan or {}).get("planId")
                    or plan.get("planId")
                    or _uuid()
                ),
                "planType": (
                    (authoritative_match_plan or {}).get("planType")
                    or action.upper()
                ),
                "status": "PROPOSED",
                "beforeGraph": before,
                "afterGraph": planned_after,
                "redirects": redirects,
                "candidateImpacts": _copy(
                    (authoritative_match_plan or {}).get("candidateImpacts")
                    or plan.get("candidateReassignments")
                    or plan.get("candidateImpacts")
                    or []
                ),
                "lineageImpact": lineage_impact,
                "proposer": {
                    "subjectId": proposer_name,
                    "roleId": proposer_role_id,
                },
                "reviewer": None,
                "expectedGraphVersion": before["version"],
                "originalDecision": original_decision,
                "operations": operations,
                "generatedAt": (
                    (authoritative_match_plan or {}).get("generatedAt")
                    or plan.get("generatedAt")
                    or _now()
                ),
            }
        )
        return enriched

    def propose_identity_decision(
        self,
        *,
        tenant_id: str,
        action: str,
        plan: dict[str, Any],
        actor_role_id: str,
        actor_name: str,
        reason: str,
        risk_acknowledged: bool,
        correlation_id: str | None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        if action not in {
            "merge",
            "split",
            "unmerge",
            "match_decision",
            "identity_correction",
        }:
            raise NetworkListingConflict(f"unsupported identity action {action}")
        if not reason.strip():
            raise NetworkListingConflict("identity decision reason is required")
        if action in {"merge", "split", "unmerge", "identity_correction"} and not risk_acknowledged:
            raise NetworkListingConflict(
                "risk acknowledgement is required for identity graph changes"
            )

        graph = self._identity_graph(tenant_id)
        decision_id = decision_id or _uuid()
        existing = graph["decisions"].get(decision_id)
        if existing is not None:
            return _copy(existing)
        timestamp = _now()
        audit_event_id = _uuid()
        graph_plan = self._enrich_identity_graph_plan(
            tenant_id=tenant_id,
            action=action,
            plan=plan,
            proposer_name=actor_name,
            proposer_role_id=actor_role_id,
        )
        decision = {
            "decisionId": decision_id,
            "tenantId": tenant_id,
            "action": action,
            "status": "PENDING_REVIEW",
            "plan": graph_plan,
            "reason": reason,
            "riskAcknowledged": risk_acknowledged,
            "proposer": actor_name,
            "proposerRoleId": actor_role_id,
            "reviewer": None,
            "reviewerRoleId": None,
            "version": 1,
            "correlationId": correlation_id,
            "auditEventId": audit_event_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "effectReceipt": None,
            "reversesDecisionId": None,
        }
        graph["decisions"][decision_id] = decision
        graph["version"] = int(graph.get("version") or 0) + 1
        graph["auditEvents"].append(
            {
                "id": audit_event_id,
                "occurredAt": timestamp,
                "actorRoleId": actor_role_id,
                "actorName": actor_name,
                "action": "identity.decision.proposed",
                "targetId": decision_id,
                "correlationId": correlation_id,
                "metadata": {
                    "before": None,
                    "after": {"status": "PENDING_REVIEW", "version": 1},
                    "reason": reason,
                    "sourceSnapshotId": graph_plan.get("sourceSnapshotId"),
                    "parserVersion": graph_plan.get("parserVersion"),
                    "relatedIds": _copy(graph_plan.get("relatedIds") or {}),
                    "evidenceState": graph_plan.get("evidenceState") or "PARTIAL",
                },
            }
        )
        self._save_identity_graph(tenant_id, graph)
        return _copy(decision)

    def record_lifecycle_receipt(
        self,
        *,
        intake_id: str,
        category: str,
        action: str,
        receipt: dict[str, Any],
        actor: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        """Append an idempotent external lifecycle receipt to the durable intake.

        Assignment, SLA and queue resources have their own versions. The intake
        keeps an immutable receipt stream plus the latest projection so a
        detail reload remains authoritative after process-local route state is
        gone.
        """

        intake = self._listing_intake(intake_id)
        resource_id = next(
            (
                receipt.get(key)
                for key in (
                    "assignment_id",
                    "sla_instance_id",
                    "job_id",
                    "promotion_decision_id",
                    "transition_id",
                    "receipt_id",
                )
                if receipt.get(key)
            ),
            None,
        )
        resource_version = receipt.get("version") or receipt.get("version_after")
        dedupe_key = (
            f"{category}:{action}:{resource_id or 'intake'}:"
            f"{resource_version or receipt.get('status') or receipt.get('state')}"
        )
        receipts = intake.setdefault("lifecycleReceipts", [])
        existing = next(
            (entry for entry in receipts if entry.get("dedupeKey") == dedupe_key),
            None,
        )
        if existing is not None:
            return _copy(existing)

        occurred_at = (
            receipt.get("occurred_at")
            or receipt.get("updated_at")
            or receipt.get("created_at")
            or _now()
        )
        entry = {
            "receiptId": _uuid(),
            "dedupeKey": dedupe_key,
            "category": category,
            "action": action,
            "resourceId": resource_id,
            "resourceVersion": resource_version,
            "status": receipt.get("status") or receipt.get("state") or receipt.get("to_state"),
            "actor": actor,
            "correlationId": correlation_id or receipt.get("correlation_id"),
            "occurredAt": occurred_at,
            "receipt": _copy(receipt),
        }
        receipts.append(entry)
        intake.setdefault("lifecycleProjections", {})[category] = _copy(receipt)
        # Intake transitions already increment the aggregate version in the
        # same command. Recording their durable receipt must not perform a
        # hidden second increment after the API has issued its ETag. External
        # assignment/SLA/job aggregates do increment the intake projection so
        # detail loaders observe their new receipt under a fresh ETag.
        if category != "intake":
            intake["version"] = int(intake.get("version") or 0) + 1
        intake["updatedAt"] = occurred_at
        self._save_intake(intake)
        return _copy(entry)

    def _match_graph_plan(
        self,
        *,
        tenant_id: str,
        intake_id: str,
        outcome: str,
        target_listing_id: str | None,
    ) -> dict[str, Any]:
        plan_id = _uuid()
        before = self._identity_graph_snapshot(tenant_id)
        submitted_node = {
            "nodeId": intake_id,
            "nodeType": "INTAKE",
            "status": "EFFECTIVE",
        }
        target_nodes = (
            [
                {
                    "nodeId": str(target_listing_id),
                    "nodeType": "LISTING",
                    "status": "EFFECTIVE",
                },
                {
                    "nodeId": f"PROPERTY-{target_listing_id}",
                    "nodeType": "PROPERTY",
                    "status": "EFFECTIVE",
                },
            ]
            if target_listing_id
            else []
        )
        before_nodes = {
            (node["nodeType"], node["nodeId"]): node
            for node in [*before["nodes"], submitted_node, *target_nodes]
        }
        proposed_edges: list[dict[str, Any]] = []
        if outcome == "NEW":
            plan_type = "CREATE_LISTING"
            operations = [
                {
                    "operation": "CREATE_LISTING",
                    "sourceIntakeId": intake_id,
                },
                {
                    "operation": "APPEND_IDENTITY_EDGE",
                    "relation": "SOURCE_OF",
                    "sourceIntakeId": intake_id,
                },
            ]
            permitted = ["CREATE", "QUARANTINE", "REJECT"]
            proposed_edges.append(
                {
                    "edgeId": f"planned:{plan_id}:source",
                    "relation": "SOURCE_OF",
                    "intakeId": intake_id,
                    "listingId": f"planned-listing:{plan_id}",
                    "propertyId": f"planned-property:{plan_id}",
                    "status": "PROPOSED",
                    "supersedesEdgeIds": [],
                }
            )
        elif outcome == "REVISION":
            plan_type = "APPEND_LISTING_REVISION"
            operations = [
                {
                    "operation": "APPEND_LISTING_REVISION",
                    "sourceIntakeId": intake_id,
                    "targetListingId": target_listing_id,
                },
                {
                    "operation": "APPEND_IDENTITY_EDGE",
                    "relation": "REVISION_OF",
                    "sourceIntakeId": intake_id,
                    "targetListingId": target_listing_id,
                },
            ]
            permitted = ["REVISE", "QUARANTINE", "REJECT"]
            proposed_edges.append(
                {
                    "edgeId": f"planned:{plan_id}:revision",
                    "relation": "REVISION_OF",
                    "intakeId": intake_id,
                    "listingId": target_listing_id,
                    "propertyId": f"PROPERTY-{target_listing_id}",
                    "status": "PROPOSED",
                    "supersedesEdgeIds": [],
                }
            )
        elif outcome == "EXACT_DUPLICATE":
            plan_type = "NO_GRAPH_MUTATION"
            operations = [
                {
                    "operation": "NAVIGATE_EXISTING_LISTING",
                    "targetListingId": target_listing_id,
                }
            ]
            permitted = []
        elif outcome == "POSSIBLE_MATCH":
            plan_type = "HUMAN_DECISION_REQUIRED"
            operations = []
            permitted = ["CREATE", "REVISE", "DUPLICATE", "QUARANTINE", "REJECT"]
        else:
            plan_type = "NO_GRAPH_MUTATION"
            operations = []
            permitted = ["REOPEN", "REJECT"] if outcome == "QUARANTINED" else []
        after_edges = [*_copy(before["edges"]), *proposed_edges]
        after_nodes = {
            **before_nodes,
            **{
                (node["nodeType"], node["nodeId"]): node
                for node in self._identity_nodes_for_edges(after_edges)
            },
        }
        return {
            "planId": plan_id,
            "planType": plan_type,
            "status": "PROPOSED" if permitted else "INFORMATIONAL",
            "operations": operations,
            "permittedDecisionTypes": permitted,
            "requiresHumanDecision": bool(permitted),
            "beforeGraph": {
                "version": before["version"],
                "nodes": sorted(
                    before_nodes.values(),
                    key=lambda node: (node["nodeType"], node["nodeId"]),
                ),
                "edges": _copy(before["edges"]),
            },
            "afterGraph": {
                "version": before["version"] + (1 if proposed_edges else 0),
                "nodes": sorted(
                    after_nodes.values(),
                    key=lambda node: (node["nodeType"], node["nodeId"]),
                ),
                "edges": after_edges,
            },
            "redirects": [],
            "candidateImpacts": [],
            "lineageImpact": {
                "appendOnly": True,
                "sourceEvidencePreserved": True,
                "supersededEdgeIds": [],
                "affectedDecisionIds": [],
                "summary": (
                    "The plan preserves source evidence and appends lineage only "
                    "after an authorized human decision."
                ),
            },
            "proposer": None,
            "reviewer": None,
            "expectedGraphVersion": before["version"],
            "originalDecision": None,
            "generatedAt": _now(),
        }

    def _record_match_case(
        self,
        *,
        intake: dict[str, Any],
        match_result: dict[str, Any],
        submitted_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        submitted_values = submitted_values or {}
        target_listing_id = match_result.get("targetListingId")
        target = (
            next(
                (
                    listing
                    for listing in self._get_match_listings()
                    if listing.get("id") == target_listing_id
                ),
                None,
            )
            if target_listing_id
            else None
        )
        submitted_by_signal = {
            "sourceListingId": submitted_values.get("providerListingId"),
            "canonicalUrl": intake.get("canonicalUrl"),
            "normalizedAddress": submitted_values.get("address"),
            "areaPing": submitted_values.get("areaPing"),
            "floor": submitted_values.get("floor"),
            "listingType": submitted_values.get("listingType"),
            "rent": submitted_values.get("rent"),
        }
        target_by_signal = {
            "sourceListingId": (target or {}).get("sourceListingId"),
            "canonicalUrl": (target or {}).get("canonicalUrl")
            or (target or {}).get("sourceUrl"),
            "normalizedAddress": (target or {}).get("address"),
            "areaPing": (target or {}).get("areaPing"),
            "floor": (target or {}).get("floor"),
            "listingType": (target or {}).get("listingType"),
            "rent": (target or {}).get("rentPerMonth"),
        }
        signals = list(match_result.get("agreeingSignals") or []) + list(
            match_result.get("contradictingSignals") or []
        )
        comparison_fields = [
            {
                "fieldPath": signal["key"],
                "label": signal.get("label") or signal["key"],
                "submittedValue": _copy(submitted_by_signal.get(signal["key"])),
                "existingValue": _copy(target_by_signal.get(signal["key"])),
                "agrees": bool(signal.get("agrees")),
                "detail": signal.get("detail"),
            }
            for signal in signals
        ]
        prior = intake.get("matchCase") or {}
        match_case = {
            "matchCaseId": intake.get("matchCaseId") or prior.get("matchCaseId") or _uuid(),
            "version": int(prior.get("version") or 0) + 1,
            "intakeId": intake["id"],
            "outcome": match_result["outcome"],
            "confidence": float(match_result.get("confidence") or 0),
            "targetListingId": target_listing_id,
            "summary": match_result.get("summary") or "",
            "comparisonFields": comparison_fields,
            "signals": _copy(signals),
            "graphPlan": self._match_graph_plan(
                tenant_id=intake["tenantId"],
                intake_id=intake["id"],
                outcome=match_result["outcome"],
                target_listing_id=target_listing_id,
            ),
            "sourceSnapshotId": intake.get("snapshotId"),
            "parserVersion": intake.get("parserVersion"),
            "createdAt": prior.get("createdAt") or _now(),
            "updatedAt": _now(),
        }
        intake["matchCaseId"] = match_case["matchCaseId"]
        intake["matchCaseVersion"] = match_case["version"]
        intake["matchCase"] = match_case
        return _copy(match_case)

    @staticmethod
    def _identity_graph_has_path(edges: list[dict[str, Any]], start: str, target: str) -> bool:
        adjacency: dict[str, set[str]] = {}
        superseded_ids = {
            edge_id for edge in edges for edge_id in edge.get("supersedesEdgeIds", [])
        }
        for edge in edges:
            if edge["edgeId"] in superseded_ids:
                continue
            if edge.get("relation") != "MERGED_INTO":
                continue
            adjacency.setdefault(edge["sourcePropertyId"], set()).add(edge["targetPropertyId"])
        pending = [start]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
        return False

    def _append_global_identity_edge(
        self,
        graph: dict[str, Any],
        *,
        tenant_id: str,
        decision_id: str,
        relation: str,
        source_property_id: str,
        target_property_id: str,
        actor_role_id: str,
        actor_name: str,
        reason: str,
        correlation_id: str | None,
        supersedes_edge_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        edge = {
            "edgeId": _uuid(),
            "tenantId": tenant_id,
            "decisionId": decision_id,
            "relation": relation,
            "sourcePropertyId": source_property_id,
            "targetPropertyId": target_property_id,
            "status": "EFFECTIVE",
            "supersedesEdgeIds": list(supersedes_edge_ids or []),
            "reason": reason,
            "actor": actor_name,
            "actorRoleId": actor_role_id,
            "correlationId": correlation_id,
            "effectiveFrom": _now(),
            "effectiveTo": None,
            "createdAt": _now(),
            "evidenceState": "COMPLETE",
        }
        graph["edges"].append(edge)
        return edge

    def review_identity_decision(
        self,
        *,
        tenant_id: str,
        decision_id: str,
        approve: bool,
        reviewer_role_id: str,
        reviewer_name: str,
        reason: str,
        risk_acknowledged: bool,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        graph = self._identity_graph(tenant_id)
        decision = graph["decisions"].get(decision_id)
        if decision is None:
            raise NetworkListingNotFound(f"identity decision {decision_id} was not found")
        if decision["status"] not in {"PENDING_REVIEW", "REVERSAL_PENDING"}:
            raise NetworkListingConflict(f"identity decision {decision_id} is {decision['status']}")
        if decision["proposer"] == reviewer_name:
            raise NetworkListingConflict("SELF_REVIEW_DENIED")
        if not reason.strip() or not risk_acknowledged:
            raise NetworkListingConflict("review reason and risk acknowledgement are required")

        before = {
            "status": decision["status"],
            "version": decision["version"],
        }
        edge_ids: list[str] = []
        runtime_receipt: dict[str, Any] | None = None
        if approve:
            plan = decision["plan"]
            action = decision["action"]
            if action == "match_decision":
                runtime_action = {
                    "CREATE": "create",
                    "REVISE": "revise",
                    "DUPLICATE": "duplicate",
                    "QUARANTINE": "quarantine",
                    "REJECT": "reject",
                }.get(plan["decisionType"])
                if runtime_action is None:
                    raise NetworkListingConflict(
                        f"unsupported match decision {plan['decisionType']}"
                    )
                updated_intake = self.decide_intake(
                    intake_id=plan["intakeId"],
                    action=runtime_action,
                    actor_role_id=reviewer_role_id,
                    actor_name=reviewer_name,
                    reason=reason,
                    risk_summary=decision["reason"],
                    risk_acknowledged=True,
                    target_listing_id=plan.get("targetListingId"),
                    idempotency_key=f"identity-decision:{decision_id}",
                    correlation_id=correlation_id,
                )
                runtime_receipt = _copy(updated_intake.get("latestDecisionReceipt"))
                if runtime_receipt and runtime_receipt.get("identityEdgeId"):
                    edge_ids.append(runtime_receipt["identityEdgeId"])
            elif action == "identity_correction":
                updated_intake = self.correct_intake(
                    intake_id=plan["intakeId"],
                    fields={plan["fieldPath"]: _copy(plan["correctedValue"])},
                    reason=reason,
                    risk_summary=decision["reason"],
                    risk_acknowledged=True,
                    actor_role_id=reviewer_role_id,
                    actor_name=reviewer_name,
                    idempotency_key=f"identity-correction:{decision_id}",
                    correlation_id=correlation_id,
                )
                for proposal in updated_intake.get("correctionProposals", []):
                    if proposal.get("correctionId") == plan["correctionId"]:
                        proposal["status"] = "APPLIED"
                        proposal["reviewer"] = reviewer_name
                        proposal["reviewerRoleId"] = reviewer_role_id
                        proposal["reviewReason"] = reason
                        proposal["reviewedAt"] = _now()
                        break
                self._save_intake(updated_intake)
                latest_audit = (updated_intake.get("auditEvents") or [])[-1]
                runtime_receipt = {
                    "receiptId": _uuid(),
                    "decisionId": decision_id,
                    "status": "EXECUTED",
                    "intakeId": plan["intakeId"],
                    "correctionId": plan["correctionId"],
                    "auditEventId": latest_audit["id"],
                    "correlationId": correlation_id,
                    "version": updated_intake["version"],
                    "issuedAt": _now(),
                    "evidenceState": latest_audit["metadata"]["evidenceState"],
                }
            elif action == "merge":
                target = plan["targetPropertyId"]
                for source in plan["sourcePropertyIds"]:
                    if source == target or self._identity_graph_has_path(
                        graph["edges"], target, source
                    ):
                        raise NetworkListingConflict("IDENTITY_CYCLE_DETECTED")
                    edge_ids.append(
                        self._append_global_identity_edge(
                            graph,
                            tenant_id=tenant_id,
                            decision_id=decision_id,
                            relation="MERGED_INTO",
                            source_property_id=source,
                            target_property_id=target,
                            actor_role_id=reviewer_role_id,
                            actor_name=reviewer_name,
                            reason=reason,
                            correlation_id=correlation_id,
                        )["edgeId"]
                    )
            elif action == "split":
                source = plan["sourcePropertyId"]
                superseded = list(plan.get("sourceIdentityEdgeIds") or [])
                for partition in plan["partitions"]:
                    target = partition["targetPropertyId"]
                    edge_ids.append(
                        self._append_global_identity_edge(
                            graph,
                            tenant_id=tenant_id,
                            decision_id=decision_id,
                            relation="SPLIT_TO",
                            source_property_id=source,
                            target_property_id=target,
                            actor_role_id=reviewer_role_id,
                            actor_name=reviewer_name,
                            reason=reason,
                            correlation_id=correlation_id,
                            supersedes_edge_ids=superseded,
                        )["edgeId"]
                    )
            elif action == "unmerge":
                original_id = plan["originalDecisionId"]
                superseded = [
                    edge["edgeId"]
                    for edge in graph["edges"]
                    if edge.get("decisionId") == original_id
                ]
                for replacement in plan["replacementEdges"]:
                    for source_edge_id in replacement["sourceIdentityEdgeIds"]:
                        edge_ids.append(
                            self._append_global_identity_edge(
                                graph,
                                tenant_id=tenant_id,
                                decision_id=decision_id,
                                relation="UNMERGED_TO",
                                source_property_id=source_edge_id,
                                target_property_id=replacement["targetPropertyId"],
                                actor_role_id=reviewer_role_id,
                                actor_name=reviewer_name,
                                reason=reason,
                                correlation_id=correlation_id,
                                supersedes_edge_ids=superseded,
                            )["edgeId"]
                        )
            elif action == "reversal":
                original_id = decision["reversesDecisionId"]
                superseded = [
                    edge["edgeId"]
                    for edge in graph["edges"]
                    if edge.get("decisionId") == original_id
                ]
                for edge_id in superseded:
                    edge_ids.append(
                        self._append_global_identity_edge(
                            graph,
                            tenant_id=tenant_id,
                            decision_id=decision_id,
                            relation="REVERSAL_OF",
                            source_property_id=edge_id,
                            target_property_id=original_id,
                            actor_role_id=reviewer_role_id,
                            actor_name=reviewer_name,
                            reason=reason,
                            correlation_id=correlation_id,
                            supersedes_edge_ids=[edge_id],
                        )["edgeId"]
                    )
            decision["status"] = "EXECUTED"
        else:
            decision["status"] = "REJECTED"
            if decision["action"] == "identity_correction":
                plan = decision["plan"]
                rejected_intake = self._listing_intake(plan["intakeId"])
                for proposal in rejected_intake.get("correctionProposals", []):
                    if proposal.get("correctionId") == plan["correctionId"]:
                        proposal["status"] = "REJECTED"
                        proposal["reviewer"] = reviewer_name
                        proposal["reviewerRoleId"] = reviewer_role_id
                        proposal["reviewReason"] = reason
                        proposal["reviewedAt"] = _now()
                        break
                rejected_intake["version"] = int(rejected_intake.get("version") or 0) + 1
                self._save_intake(rejected_intake)

        decision["reviewer"] = reviewer_name
        decision["reviewerRoleId"] = reviewer_role_id
        decision["plan"]["reviewer"] = {
            "subjectId": reviewer_name,
            "roleId": reviewer_role_id,
        }
        if approve:
            applied_edges = _copy(graph["edges"])
            decision["plan"]["afterGraph"] = {
                "version": int(graph.get("version") or 0) + 1,
                "nodes": self._identity_nodes_for_edges(applied_edges),
                "edges": applied_edges,
            }
        decision["version"] = int(decision["version"]) + 1
        decision["updatedAt"] = _now()
        audit_event_id = _uuid()
        decision["auditEventId"] = audit_event_id
        decision["correlationId"] = correlation_id
        decision["effectReceipt"] = {
            "receiptId": _uuid(),
            "decisionId": decision_id,
            "status": decision["status"],
            "identityEdgeIds": edge_ids,
            "runtimeReceipt": runtime_receipt,
            "auditEventId": audit_event_id,
            "correlationId": correlation_id,
            "version": decision["version"],
            "issuedAt": _now(),
            "evidenceState": "COMPLETE",
        }
        graph["version"] = int(graph.get("version") or 0) + 1
        graph["auditEvents"].append(
            {
                "id": audit_event_id,
                "occurredAt": decision["updatedAt"],
                "actorRoleId": reviewer_role_id,
                "actorName": reviewer_name,
                "action": "identity.decision.reviewed",
                "targetId": decision_id,
                "correlationId": correlation_id,
                "metadata": {
                    "before": before,
                    "after": {
                        "status": decision["status"],
                        "version": decision["version"],
                    },
                    "reason": reason,
                    "sourceSnapshotId": decision["plan"].get("sourceSnapshotId"),
                    "parserVersion": decision["plan"].get("parserVersion"),
                    "relatedIds": {
                        **_copy(decision["plan"].get("relatedIds") or {}),
                        "decisionId": decision_id,
                        "identityEdgeIds": edge_ids,
                    },
                    "evidenceState": "COMPLETE",
                },
            }
        )
        self._save_identity_graph(tenant_id, graph)
        return _copy(decision)

    def request_identity_reversal(
        self,
        *,
        tenant_id: str,
        original_decision_id: str,
        actor_role_id: str,
        actor_name: str,
        reason: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        graph = self._identity_graph(tenant_id)
        original = graph["decisions"].get(original_decision_id)
        if original is None or original["status"] != "EXECUTED":
            raise NetworkListingConflict("only an executed identity decision can be reversed")
        reversal = self.propose_identity_decision(
            tenant_id=tenant_id,
            action="unmerge",
            plan={
                "originalDecisionId": original_decision_id,
                "replacementEdges": [],
                "relatedIds": {"originalDecisionId": original_decision_id},
                "evidenceState": "COMPLETE",
            },
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            reason=reason,
            risk_acknowledged=True,
            correlation_id=correlation_id,
        )
        graph = self._identity_graph(tenant_id)
        persisted = graph["decisions"][reversal["decisionId"]]
        persisted["action"] = "reversal"
        persisted["status"] = "REVERSAL_PENDING"
        persisted["reversesDecisionId"] = original_decision_id
        persisted["plan"]["planType"] = "REVERSAL"
        persisted["plan"]["originalDecisionId"] = original_decision_id
        persisted["plan"]["originalDecision"] = {
            "decisionId": original_decision_id,
            "action": original.get("action"),
            "status": original.get("status"),
            "version": original.get("version"),
        }
        persisted["plan"]["relatedIds"] = {
            "originalDecisionId": original_decision_id
        }
        persisted["plan"]["evidenceState"] = "COMPLETE"
        self._save_identity_graph(tenant_id, graph)
        return _copy(persisted)

    def propose_quarantine_release(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str,
        reason: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        intake = self._listing_intake(intake_id)
        if intake["stage"] != "QUARANTINED":
            raise NetworkListingConflict(f"intake {intake_id} is not quarantined")
        if intake.get("pendingQuarantineRelease") is not None:
            raise NetworkListingConflict(f"intake {intake_id} already has a release proposal")

        proposal = {
            "proposalId": _uuid(),
            "proposer": actor_name,
            "proposerRoleId": actor_role_id,
            "reason": reason,
            "correlationId": correlation_id,
            "proposedAt": _now(),
            "status": "PENDING_REVIEW",
        }
        intake["pendingQuarantineRelease"] = proposal
        self._append_processing_transition(
            intake,
            to_stage="QUARANTINED",
            actor=actor_name,
            correlation_id=correlation_id,
            checkpoint="QUARANTINE_RELEASE_REVIEW",
            attempt=0,
            timeout_seconds=None,
            reason_code="SECOND_ACTOR_REQUIRED",
        )
        receipt = {
            "receiptId": proposal["proposalId"],
            "decision": "PROPOSE_QUARANTINE_RELEASE",
            "status": "PENDING_REVIEW",
            "intakeId": intake_id,
            "actor": actor_name,
            "actorRoleId": actor_role_id,
            "reason": reason,
            "correlationId": correlation_id,
            "version": intake["version"],
            "issuedAt": proposal["proposedAt"],
            "evidenceState": "COMPLETE",
        }
        intake.setdefault("decisionReceipts", []).append(receipt)
        intake["latestDecisionReceipt"] = receipt
        self._save_intake(intake)
        return _copy(intake)

    def release_quarantine(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str,
        reason: str,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        intake = self._listing_intake(intake_id)
        if intake["stage"] != "QUARANTINED":
            raise NetworkListingConflict(f"intake {intake_id} is not quarantined")
        proposal = intake.get("pendingQuarantineRelease")
        if proposal is None:
            raise NetworkListingConflict(
                f"intake {intake_id} requires a quarantine release proposal"
            )
        if proposal["proposer"] == actor_name:
            raise NetworkListingPolicyError("SELF_REVIEW_DENIED")
        proposal["status"] = "APPROVED"
        proposal["reviewer"] = actor_name
        proposal["reviewerRoleId"] = actor_role_id
        proposal["reviewReason"] = reason
        proposal["reviewedAt"] = _now()
        self._append_processing_transition(
            intake,
            to_stage="NEEDS_REVIEW",
            actor=actor_name,
            correlation_id=correlation_id,
            checkpoint="QUARANTINE_RELEASED",
            attempt=0,
            timeout_seconds=None,
            reason_code="QUARANTINE_RELEASED",
        )
        receipt = {
            "receiptId": _uuid(),
            "decision": "RELEASE_QUARANTINE",
            "status": "EXECUTED",
            "intakeId": intake_id,
            "actor": actor_name,
            "actorRoleId": actor_role_id,
            "reason": reason,
            "correlationId": correlation_id,
            "version": intake["version"],
            "issuedAt": _now(),
            "evidenceState": "COMPLETE",
        }
        intake.setdefault("decisionReceipts", []).append(receipt)
        intake["latestDecisionReceipt"] = receipt
        intake["lastQuarantineRelease"] = _copy(proposal)
        intake.pop("pendingQuarantineRelease", None)
        self._save_intake(intake)
        return _copy(intake)

    def cancel_intake(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str,
        reason: str,
        correlation_id: str | None,
        job_queue: Any | None = None,
    ) -> dict[str, Any]:
        intake = self._listing_intake(intake_id)
        if intake["stage"] not in {
            "SUBMITTED",
            "CHECKING_IDENTITY",
            "CHECKING_SOURCE_POLICY",
            "AWAITING_ASSISTED_ENTRY",
            "RETRIEVING",
            "PARSING",
            "MATCHING",
            "NEEDS_REVIEW",
        }:
            raise NetworkListingConflict(
                f"intake {intake_id} cannot be cancelled from {intake['stage']}"
            )
        job_id = intake.get("jobId")
        if job_queue is not None and job_id:
            job = job_queue.get(job_id)
            if job is not None:
                from shared.jobs.queue import JobStatus

                job_queue.update_status(
                    job_id,
                    JobStatus.CANCELLED,
                    expected_version=job.version,
                    fence_token=job.fence_token,
                )
        self._append_processing_transition(
            intake,
            to_stage="CANCELLED",
            actor=actor_name,
            correlation_id=correlation_id,
            checkpoint="CANCELLED",
            attempt=0,
            timeout_seconds=None,
            reason_code="USER_CANCELLED",
        )
        receipt = {
            "receiptId": _uuid(),
            "decision": "CANCEL",
            "status": "EXECUTED",
            "intakeId": intake_id,
            "actor": actor_name,
            "actorRoleId": actor_role_id,
            "reason": reason,
            "jobId": job_id,
            "correlationId": correlation_id,
            "version": intake["version"],
            "issuedAt": _now(),
            "evidenceState": "COMPLETE",
        }
        intake.setdefault("decisionReceipts", []).append(receipt)
        intake["latestDecisionReceipt"] = receipt
        self._save_intake(intake)
        return _copy(intake)

    def _append_processing_transition(
        self,
        intake: dict[str, Any],
        *,
        to_stage: str,
        actor: str,
        correlation_id: str | None,
        checkpoint: str | None = None,
        attempt: int | None = None,
        timeout_seconds: int | None = None,
        failure: dict[str, Any] | None = None,
        next_retry_at: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        from_stage = intake.get("stage")
        intake["stage"] = to_stage
        intake["version"] = int(intake.get("version") or 0) + 1
        transition = {
            "transitionId": _uuid(),
            "fromStage": from_stage,
            "toStage": to_stage,
            "occurredAt": _now(),
            "actor": actor,
            "correlationId": correlation_id,
            "checkpoint": checkpoint or to_stage,
            "attempt": attempt,
            "timeoutSeconds": timeout_seconds,
            "failure": _copy(failure),
            "nextRetryAt": next_retry_at,
            "reasonCode": reason_code,
            "versionAfter": intake["version"],
        }
        intake.setdefault("processingHistory", []).append(transition)
        intake.setdefault("auditEvents", []).append(
            {
                "id": _uuid(),
                "occurredAt": transition["occurredAt"],
                "actorRoleId": "system",
                "actorName": actor,
                "action": "intake.stage_transition",
                "targetId": intake["id"],
                "message": f"{from_stage or 'NONE'} -> {to_stage}",
                "correlationId": correlation_id,
                "metadata": {
                    "before": {"stage": from_stage},
                    "after": {
                        "stage": to_stage,
                        "version": intake["version"],
                    },
                    "reason": reason_code,
                    "sourceSnapshotId": intake.get("snapshotId"),
                    "parserVersion": intake.get("parserVersion"),
                    "relatedIds": {
                        "intakeId": intake["id"],
                        "jobId": intake.get("jobId"),
                    },
                    "checkpoint": checkpoint or to_stage,
                    "attempt": attempt,
                    "timeoutSeconds": timeout_seconds,
                    "failure": _copy(failure),
                    "nextRetryAt": next_retry_at,
                    "evidenceState": (
                        "COMPLETE"
                        if intake.get("snapshotId") and intake.get("parserVersion")
                        else "PARTIAL"
                    ),
                },
            }
        )
        self._save_intake(intake)
        return _copy(transition)

    def _get_candidate_metadata(self, candidate_id: str) -> dict[str, Any]:
        return self._intakes.get_candidate_metadata(candidate_id)

    def _save_candidate_metadata(self, candidate_id: str, metadata: dict[str, Any]) -> None:
        self._intakes.save_candidate_metadata(candidate_id, metadata)

    def _listing_to_dict(self, lst: Any) -> dict[str, Any]:
        res = {
            "id": lst.listing_id,
            "sourceId": lst.source_id,
            "sourceListingId": lst.source_listing_id,
            "status": lst.listing_status,
            "rentPerMonth": int(lst.rent_amount),
            "areaPing": lst.area_ping,
            "floor": lst.floor,
            "frontageMeters": int(lst.frontage_m) if lst.frontage_m else 0,
            "geocodeConfidence": lst.confidence,
            "sourceUrl": lst.snapshot_id,
        }
        meta = self._get_listing_metadata(lst.listing_id)
        if meta:
            res.update(meta)
        else:
            for item in _seed_state()["listings"]:
                if item["id"] == lst.listing_id:
                    for k, v in item.items():
                        if k not in res:
                            res[k] = v
                    break
        return res

    def _dict_to_listing(self, d: dict[str, Any]) -> tuple[Any, Any, Any]:
        from modules.listing.domain.models import ListingDedupKey
        from shared.domain.models import AddressLocation, Listing

        address_id = f"ADDR-{d['id']}"
        lst = Listing(
            listing_id=d["id"],
            source_listing_id=d["sourceListingId"],
            source_id=d["sourceId"],
            listing_status=d["status"],
            address_id=address_id,
            rent_amount=float(d["rentPerMonth"]),
            area_ping=float(d["areaPing"]),
            floor=d["floor"],
            frontage_m=float(d.get("frontageMeters") or 0),
            confidence=float(d.get("geocodeConfidence") or 1.0),
            snapshot_id=d.get("sourceUrl") or "",
        )
        addr = AddressLocation(
            address_id=address_id,
            raw_address=d.get("address") or "",
            normalized_address=d.get("address") or "",
            latitude=float(d.get("latitude") or d.get("lat") or 25.0339),
            longitude=float(d.get("longitude") or d.get("lng") or 121.5645),
            geocode_confidence=float(d.get("geocodeConfidence") or 1.0),
            h3_res_9=d.get("h3Index") or d.get("h3_index") or d.get("heatZoneId") or "",
        )
        key = ListingDedupKey(
            source_id=d["sourceId"],
            source_listing_id=d["sourceListingId"],
            normalized_address=d.get("address") or "",
            rent_amount=float(d["rentPerMonth"]),
            area_ping=float(d["areaPing"]),
        )
        return lst, addr, key

    def _candidate_to_dict(self, cand: Any) -> dict[str, Any]:
        res = {
            "id": cand.candidate_site.candidate_site_id,
            "listingId": cand.listing.listing_id,
            "heatZoneId": cand.heat_zone_id,
            "title": f"{cand.listing.listing_id} 候選點",
            "address": cand.address.raw_address,
            "status": cand.status.value if hasattr(cand.status, "value") else str(cand.status),
            "score": 68,
            "recommendation": "WAIT",
            "modelVersion": "SiteScore v2.3",
            "datasetSnapshotId": "FS-20260704-0600",
            "missingData": [],
        }
        meta = self._get_candidate_metadata(cand.candidate_site.candidate_site_id)
        if meta:
            res.update(meta)
        else:
            if res["id"] == "CS-1001":
                res["title"] = "信義松仁候選點"
                res["score"] = 82
                res["recommendation"] = "GO"
                res["reviewId"] = "RV-1001"
        return res

    def _dict_to_candidate(self, d: dict[str, Any]) -> Any:
        from modules.listing.domain.models import CandidateSiteDraft, ListingPipelineStatus
        from shared.domain.models import CandidateSite

        listing = self._listing(d["listingId"])
        lst_obj, addr_obj, _ = self._dict_to_listing(listing)

        cand_site = CandidateSite(
            candidate_site_id=d["id"],
            listing_id=d["listingId"],
            address_id=addr_obj.address_id,
            site_status=d["status"],
        )
        return CandidateSiteDraft(
            listing=lst_obj,
            address=addr_obj,
            candidate_site=cand_site,
            heat_zone_id=d["heatZoneId"],
            listing_source=listing.get("sourceId") or "",
            status=ListingPipelineStatus.CANDIDATE,
        )

    def _sync_listing_to_repo(self, listing_id: str) -> None:
        if self._listing_repository is not None:
            listing = self._listing(listing_id)
            lst_obj, addr_obj, key_obj = self._dict_to_listing(listing)
            self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

            meta = {
                "heatZoneId": listing.get("heatZoneId"),
                "hardRuleFailures": listing.get("hardRuleFailures"),
                "hardRuleSummary": listing.get("hardRuleSummary"),
                "sourceEvidence": listing.get("sourceEvidence"),
                "fitScore": listing.get("fitScore"),
                "firstSeenAt": listing.get("firstSeenAt"),
                # The domain Listing carries only `status`, which cannot tell an
                # already-merged duplicate from a merge-eligible one. Without
                # these, a restart drops the terminal marker and merge_listing
                # would accept a second request for the same source.
                "duplicateOfId": listing.get("duplicateOfId"),
                "mergedIntoId": listing.get("mergedIntoId"),
                "mergedAt": listing.get("mergedAt"),
                "mergeReason": listing.get("mergeReason"),
                "mergedSourceListingIds": listing.get("mergedSourceListingIds"),
            }
            self._save_listing_metadata(listing_id, meta)

    def _sync_candidate_to_repo(self, candidate_id: str) -> None:
        if self._listing_repository is not None:
            candidate = None
            for cand in self._state["candidates"]:
                if cand["id"] == candidate_id:
                    candidate = cand
                    break
            if candidate:
                cand_obj = self._dict_to_candidate(candidate)
                self._listing_repository.save_candidate(cand_obj)

                meta = {
                    "title": candidate.get("title"),
                    "score": candidate.get("score"),
                    "recommendation": candidate.get("recommendation"),
                    "modelVersion": candidate.get("modelVersion"),
                    "datasetSnapshotId": candidate.get("datasetSnapshotId"),
                    "missingData": candidate.get("missingData"),
                    "reviewId": candidate.get("reviewId"),
                }
                self._save_candidate_metadata(candidate_id, meta)

    def _load_intakes(self) -> None:
        self._state["assistedIntakes"] = self._intakes.list_intakes()

    def _load_idempotency_cache(self) -> None:
        self._idempotency_cache = {
            (record.action, record.key): record.response
            for record in self._intakes.list_idempotency_records()
        }

    def _save_idempotency(self, action: str, key: str, response: dict[str, Any]) -> None:
        self._idempotency_cache[(action, key)] = _copy(response)
        self._intakes.save_idempotency_record(
            IntakeIdempotencyRecord(action=action, key=key, response=_copy(response))
        )

    def _save_intake(self, intake: dict[str, Any]) -> None:
        self._state.setdefault("assistedIntakes", [])
        found = False
        for idx, item in enumerate(self._state["assistedIntakes"]):
            if item["id"] == intake["id"]:
                self._state["assistedIntakes"][idx] = intake
                found = True
                break
        if not found:
            self._state["assistedIntakes"].append(intake)

        self._intakes.save_intake(intake)

    def reset(self) -> dict[str, Any]:
        self._state = _seed_state()
        self._idempotency_cache = {}
        self._intakes.clear()
        self._state["assistedIntakes"] = []

        if self._listing_repository is not None:
            self._listing_repository.clear()
            for lst_dict in self._state["listings"]:
                lst_obj, addr_obj, key_obj = self._dict_to_listing(lst_dict)
                self._listing_repository.save_listing(lst_obj, addr_obj, key_obj)

        return self.snapshot()

    def snapshot(
        self,
        *,
        selected_heat_zone_id: str | None = None,
        lens: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        selected_id = selected_heat_zone_id or "HZ-01"
        active_lens = lens or "demand"
        self._state.setdefault("assistedIntakes", [])
        return {
            "source": "api",
            "heatZones": _copy(self._state["heatZones"]),
            "listingSources": _copy(self._state["listingSources"]),
            "listings": [self._effective_listing(listing) for listing in self._state["listings"]],
            "candidates": _copy(self._state["candidates"]),
            "siteReviews": _copy(self._state["siteReviews"]),
            "assistedIntakes": _copy(self._state["assistedIntakes"]),
            "expansionSteps": self._expansion_steps(selected_id=selected_id),
            "selectedHeatZoneId": selected_id,
            "selectedLens": active_lens,
            "auditEvents": _copy(self._state["auditEvents"]),
            "correlationId": correlation_id,
            "counts": {
                "heatZones": len(self._state["heatZones"]),
                "listings": len(self._state["listings"]),
                "candidates": len(self._state["candidates"]),
                "siteReviews": len(self._state["siteReviews"]),
                "assistedIntakes": len(self._state["assistedIntakes"]),
            },
        }

    def convert_listing(
        self,
        *,
        listing_id: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to convert listing"
            )

        cache_key = ("convert", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        listing = self._listing(listing_id)
        if listing.get("hardRuleFailures"):
            raise NetworkListingPolicyError(
                f"{listing_id} cannot convert until hard-rule failures are resolved"
            )
        if listing.get("status") in {"duplicate", "archived", "expired"}:
            raise NetworkListingConflict(
                f"{listing_id} is {listing.get('status')} and cannot convert"
            )

        existing = self._candidate_for_listing(listing_id)
        created = existing is None
        if existing is None:
            candidate_id = (
                "CS-1001"
                if listing_id == "L-2024"
                else f"CS-{1000 + len(self._state['candidates']) + 1}"
            )
            existing = {
                "id": candidate_id,
                "listingId": listing_id,
                "heatZoneId": listing["heatZoneId"],
                "title": "信義松仁候選點" if candidate_id == "CS-1001" else f"{listing_id} 候選點",
                "address": listing["address"],
                "status": "ready",
                "score": 82 if candidate_id == "CS-1001" else 68,
                "recommendation": "GO" if candidate_id == "CS-1001" else "WAIT",
                "modelVersion": "SiteScore v2.3",
                "datasetSnapshotId": "FS-20260704-0600",
                "missingData": [],
                "reviewId": "RV-1001" if candidate_id == "CS-1001" else None,
            }
            self._state["candidates"].append(existing)
            if existing.get("reviewId"):
                self._state["siteReviews"].append(
                    {
                        "id": existing["reviewId"],
                        "candidateId": existing["id"],
                        "status": "pending",
                        "requestedByRoleId": "expansionManager",
                        "reviewerRoleIds": ["opsLead", "auditPm"],
                        "requestedAt": "2026-07-14T06:30:00Z",
                        "reasonRequired": True,
                    }
                )

        listing["status"] = "candidate"
        listing["candidateId"] = existing["id"]
        listing["convertedAt"] = listing.get("convertedAt") or _now()
        self._sync_listing_to_repo(listing_id)
        self._sync_candidate_to_repo(existing["id"])

        audit = self._audit(
            action="listing.convert",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={"candidateId": existing["id"], "created": created},
        )
        result = {
            "listing": _copy(listing),
            "candidate": _copy(existing),
            "created": created,
            "auditEvent": audit,
            "candidateCount": len(
                [item for item in self._state["candidates"] if item.get("listingId") == listing_id]
            ),
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        if idempotency_key:
            self._save_idempotency("convert", idempotency_key, result)
        return result

    def merge_listing(
        self,
        *,
        source_listing_id: str,
        target_listing_id: str,
        reason: str,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to merge listing"
            )

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="merge listing",
        )

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("merge reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="merge listing",
        )

        cache_key = ("merge", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        source = self._listing(source_listing_id)
        target = self._listing(target_listing_id)
        if source_listing_id == target_listing_id:
            raise NetworkListingConflict("source and target listing must be different")
        # Merge is terminal for the source. Replaying the SAME idempotency key is
        # served from the cache above; reaching here with an already-merged source
        # means a genuinely new request, which would append a second listing.merge
        # audit event and overwrite the first merge's reason.
        merged_into = source.get("mergedIntoId")
        if merged_into:
            raise NetworkListingConflict(
                f"{source_listing_id} is already merged into {merged_into} and cannot be merged again"
            )

        source_before_status = source.get("status")
        target_before_evidence = list(target.get("sourceEvidence", []))

        source_evidence = list(source.get("sourceEvidence", []))
        target["sourceEvidence"] = _dedupe(list(target.get("sourceEvidence", [])) + source_evidence)
        target["mergedSourceListingIds"] = _dedupe(
            list(target.get("mergedSourceListingIds", [])) + [source_listing_id]
        )
        target["mergeReason"] = reason
        target["mergedAt"] = target.get("mergedAt") or _now()
        source["status"] = "duplicate"
        source["duplicateOfId"] = target_listing_id
        source["mergedIntoId"] = target_listing_id
        source["mergedAt"] = source.get("mergedAt") or _now()
        source["mergeReason"] = reason
        self._sync_listing_to_repo(source_listing_id)
        self._sync_listing_to_repo(target_listing_id)

        audit = self._audit(
            action="listing.merge",
            target_id=target_listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "sourceListingId": source_listing_id,
                # The operator's own words, recorded alongside the disclosure
                # they acknowledged; archive does the same.
                "reason": reason,
                "sourceEvidenceRetained": len(source_evidence),
                "targetEvidenceCount": len(target["sourceEvidence"]),
                "before": {
                    "sourceStatus": source_before_status,
                    "targetEvidenceCount": len(target_before_evidence),
                },
                "after": {
                    "sourceStatus": "duplicate",
                    "targetEvidenceCount": len(target["sourceEvidence"]),
                },
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": (
                    f"Merge source {source_listing_id} into target {target_listing_id}."
                ),
            },
        )
        result = {
            "source": _copy(source),
            "target": _copy(target),
            "sourceEvidenceRetained": _copy(source_evidence),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=target["heatZoneId"]),
        }
        self._save_idempotency("merge", governed_key, result)
        return result

    def archive_listing(
        self,
        *,
        listing_id: str,
        reason: str,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {"expansionManager", "expansion-manager", "siteReviewer", "site_reviewer"}
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to archive listing"
            )

        reason = reason.strip()
        if not reason:
            raise NetworkListingPolicyError("archive reason is required")

        cache_key = ("archive", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        listing = self._listing(listing_id)
        before_status = listing.get("status")

        listing["status"] = "archived"
        listing["archivedReason"] = reason
        listing["archivedAt"] = listing.get("archivedAt") or _now()
        self._sync_listing_to_repo(listing_id)

        audit = self._audit(
            action="listing.archive",
            target_id=listing_id,
            actor_role_id=actor_role_id,
            actor_name=actor_name,
            correlation_id=correlation_id,
            metadata={
                "reason": reason,
                "hardRuleFailures": list(listing.get("hardRuleFailures", [])),
                "sourceEvidenceCount": len(listing.get("sourceEvidence", [])),
                "before": {
                    "status": before_status,
                },
                "after": {
                    "status": "archived",
                },
                "effectSummary": f"Archive listing {listing_id}. Reason: {reason}",
            },
        )
        result = {
            "listing": _copy(listing),
            "auditEvent": audit,
            "correlationId": correlation_id,
            "expansionSteps": self._expansion_steps(selected_id=listing["heatZoneId"]),
        }
        if idempotency_key:
            self._save_idempotency("archive", idempotency_key, result)
        return result

    def submit_intake(
        self,
        *,
        url: str,
        heat_zone_id: str | None,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        async_intake: bool = False,
        tenant_id: str | None = None,
        intake_id: str | None = None,
        scope_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cache_key = ("submit_intake", idempotency_key or "")
        if idempotency_key and cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        from modules.external_data.application.assisted_intake import (
            PARSER_VERSION,
            normalize_url,
            resolve_source_policy,
            validate_url,
        )

        url = url.strip()
        validate_url(url)
        canon_url = normalize_url(url)

        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake.get("canonicalUrl") == canon_url:
                if intake.get("stage") in {
                    "NEEDS_REVIEW",
                    "READY",
                    "QUARANTINED",
                    "FAILED",
                    "AWAITING_ASSISTED_ENTRY",
                }:
                    duplicate = _copy(intake)
                    target_listing_id = (duplicate.get("matchResult") or {}).get("targetListingId")
                    duplicate["submissionReceipt"] = {
                        "receiptId": _uuid(),
                        "receiptType": "EXACT_SOURCE_IDENTITY",
                        "intakeId": intake["id"],
                        "state": intake["stage"],
                        "existingListingId": target_listing_id,
                        "navigationTarget": (
                            f"/w/expansion/listings/{target_listing_id}"
                            if target_listing_id
                            else f"/w/expansion/listings/intake/{intake['id']}"
                        ),
                        "correlationId": correlation_id,
                        "issuedAt": _now(),
                        "evidenceState": ("COMPLETE" if target_listing_id else "PARTIAL"),
                    }
                    return duplicate
                else:
                    raise NetworkListingConflict(
                        f"URL {url} is already being processed (intake {intake['id']})"
                    )

        policy = resolve_source_policy(url)
        intake_id = intake_id or f"IN-{3001 + len(self._state['assistedIntakes'])}"

        intake = {
            "id": intake_id,
            "tenantId": tenant_id or "tenant-a",
            "scope": _copy(
                scope_context
                or {
                    "tenant_id": tenant_id or "tenant-a",
                    "heat_zone_id": heat_zone_id,
                }
            ),
            "originalUrl": url,
            "canonicalUrl": canon_url,
            "submitter": actor_name or "林曉青（展店）",
            "owner": actor_name or "林曉青",
            "heatZoneId": heat_zone_id,
            "intakeMethod": "URL",
            "stage": "SUBMITTED",
            "sourceId": policy.source_id,
            "policy": policy.policy,
            "policyLabel": policy.policy_label,
            "policyReason": policy.policy_reason,
            "rawSnapshot": None,
            "snapshotId": None,
            "capturedAt": None,
            "parserVersion": PARSER_VERSION,
            "correlationId": correlation_id,
            "parsedFields": {},
            "matchResult": None,
            "matchCaseId": _uuid(),
            "matchCaseVersion": 0,
            "matchCase": None,
            "auditEvents": [],
            "processingHistory": [
                {
                    "transitionId": _uuid(),
                    "fromStage": None,
                    "toStage": "SUBMITTED",
                    "occurredAt": _now(),
                    "actor": actor_name or actor_role_id,
                    "correlationId": correlation_id,
                    "checkpoint": "SUBMITTED",
                    "attempt": 0,
                    "timeoutSeconds": None,
                    "failure": None,
                    "nextRetryAt": None,
                    "reasonCode": None,
                    "versionAfter": 1,
                }
            ],
            "decisionReceipts": [],
            "idempotencyKey": idempotency_key,
            "version": 1,
        }

        if async_intake:
            if job_queue is None:
                raise NetworkListingPolicyError(
                    "asynchronous intake requires the durable assisted-intake queue"
                )
            from shared.jobs.queue import JobRequest

            payload_to_send = {
                "intake_id": intake_id,
                "url": url,
                "heat_zone_id": heat_zone_id,
                "actor_role_id": actor_role_id,
                "actor_name": actor_name,
                "tenant_id": tenant_id,
            }
            job, _created = job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload=payload_to_send,
                    idempotency_key=idempotency_key,
                ),
                correlation_id=correlation_id or "system",
            )
            intake["jobId"] = job.job_id
            intake["jobReceipt"] = {
                "jobId": job.job_id,
                "status": str(getattr(job.status, "value", job.status)),
                "checkpoint": "CHECKING_IDENTITY",
                "attempt": int(getattr(job, "attempt", 0) or 0),
                "correlationId": correlation_id,
                "issuedAt": _now(),
            }
        elif policy.quarantines or policy.policy in {"POLICY_UNKNOWN", "SOURCE_BLOCKED"}:
            intake["stage"] = "QUARANTINED"
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": f"依來源政策 {policy.policy} 予以隔離：{policy.policy_reason}",
            }
            self._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
            )
        elif policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            intake["stage"] = "AWAITING_ASSISTED_ENTRY"
        elif policy.policy == "APPROVED_RETRIEVAL":
            raise NetworkListingPolicyError(
                "approved retrieval requires the durable assisted-intake queue"
            )

        audit_evt = {
            "id": _uuid(),
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.create",
            "targetId": intake_id,
            "message": f"Intake record {intake_id} created for {url}.",
            "correlationId": correlation_id,
            "metadata": {
                "policy": policy.policy,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                "before": None,
                "after": {
                    "stage": intake["stage"],
                    "version": intake["version"],
                },
                "reason": "User-submitted listing intake",
                "sourceSnapshotId": intake.get("snapshotId"),
                "parserVersion": intake.get("parserVersion"),
                "relatedIds": {
                    "intakeId": intake_id,
                    "jobId": intake.get("jobId"),
                },
                "evidenceState": (
                    "COMPLETE"
                    if intake.get("snapshotId") and intake.get("parserVersion")
                    else "PARTIAL"
                ),
            },
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)

        if idempotency_key:
            self._save_idempotency("submit_intake", idempotency_key, intake)
        return _copy(intake)

    def submit_structured_intake(
        self,
        *,
        method: str,
        fields: dict[str, Any],
        source_id: str,
        original_url: str | None,
        heat_zone_id: str | None,
        actor_role_id: str,
        actor_name: str,
        idempotency_key: str,
        correlation_id: str,
        tenant_id: str,
        intake_id: str,
        scope_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist manual, CSV, and approved-feed rows in the canonical runtime."""
        cache_key = ("submit_structured_intake", idempotency_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        from modules.external_data.application.assisted_intake import (
            ASSISTED_ENTRY_REQUIRED_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
            normalize_address,
            normalize_floor,
            normalize_url,
        )

        def cell(
            key: str,
            label: str,
            source_value: Any,
            normalized_value: Any,
            *,
            identity: bool = False,
        ) -> dict[str, Any]:
            return {
                "key": key,
                "label": label,
                "sourceValue": source_value,
                "normalizedValue": normalized_value,
                "correctedValue": None,
                "correctionReason": None,
                "identity": identity,
                "lowConfidence": normalized_value in {None, ""},
                "sourceSnapshotId": None,
                "parserVersion": "structured-intake-v1",
            }

        canonical_url = normalize_url(original_url) if original_url else None
        parsed_fields = {
            "providerListingId": cell(
                "providerListingId",
                "提供者物件 ID",
                fields.get("source_listing_id"),
                str(fields.get("source_listing_id") or "").strip(),
                identity=True,
            ),
            "address": cell(
                "address",
                "地址",
                fields.get("address"),
                normalize_address(str(fields.get("address") or "")),
                identity=True,
            ),
            "rent": cell(
                "rent", "租金", fields.get("rent"), fields.get("rent"), identity=True
            ),
            "areaPing": cell(
                "areaPing",
                "坪數",
                fields.get("area_ping"),
                fields.get("area_ping"),
                identity=True,
            ),
            "floor": cell(
                "floor",
                "樓層",
                fields.get("floor"),
                normalize_floor(str(fields.get("floor") or "")),
            ),
            "listingType": cell(
                "listingType", "型態／用途", "店面", "店面"
            ),
            "listingStatus": cell(
                "listingStatus", "來源狀態", "active", "active"
            ),
            "currency": cell(
                "currency",
                "幣別",
                fields.get("currency") or "TWD",
                str(fields.get("currency") or "TWD").upper(),
            ),
        }
        created_at = _now()
        policy = (
            "APPROVED_RETRIEVAL"
            if method == "APPROVED_FEED"
            else "ASSISTED_ENTRY_ONLY"
        )
        intake = {
            "id": intake_id,
            "tenantId": tenant_id,
            "scope": _copy(scope_context),
            "originalUrl": original_url,
            "canonicalUrl": canonical_url,
            "submitter": actor_name,
            "owner": actor_name,
            "heatZoneId": heat_zone_id,
            "intakeMethod": method,
            "stage": "SUBMITTED",
            "sourceId": source_id,
            "policy": policy,
            "policyLabel": policy,
            "policyReason": "Structured data was supplied without page retrieval.",
            "rawSnapshot": None,
            "snapshotId": None,
            "capturedAt": created_at,
            "parserVersion": "structured-intake-v1",
            "parserRunId": None,
            "correlationId": correlation_id,
            "parsedFields": parsed_fields,
            "matchResult": None,
            "matchCaseId": _uuid(),
            "matchCaseVersion": 0,
            "matchCase": None,
            "auditEvents": [],
            "processingHistory": [
                {
                    "transitionId": _uuid(),
                    "fromStage": None,
                    "toStage": "SUBMITTED",
                    "occurredAt": created_at,
                    "actor": actor_name,
                    "correlationId": correlation_id,
                    "checkpoint": "SUBMITTED",
                    "attempt": 0,
                    "timeoutSeconds": None,
                    "failure": None,
                    "nextRetryAt": None,
                    "reasonCode": None,
                    "versionAfter": 1,
                }
            ],
            "decisionReceipts": [],
            "idempotencyKey": idempotency_key,
            "version": 1,
        }
        intake["auditEvents"].append(
            {
                "id": _uuid(),
                "occurredAt": created_at,
                "actorRoleId": actor_role_id,
                "actorName": actor_name,
                "action": "intake.create_structured",
                "targetId": intake_id,
                "message": f"{method} intake {intake_id} persisted.",
                "correlationId": correlation_id,
                "metadata": {
                    "method": method,
                    "before": None,
                    "after": {"stage": "SUBMITTED", "version": 1},
                    "reason": "Structured intake submission",
                    "sourceSnapshotId": None,
                    "parserVersion": "structured-intake-v1",
                    "relatedIds": {"intakeId": intake_id},
                    "evidenceState": "PARTIAL",
                },
            }
        )
        self._save_intake(intake)

        values = effective_fields(parsed_fields)
        complete = all(
            values.get(name) not in {None, ""}
            and (name not in {"rent", "areaPing"} or float(values[name]) > 0)
            for name in ASSISTED_ENTRY_REQUIRED_FIELDS
        )
        if not complete:
            self._append_processing_transition(
                intake,
                to_stage="AWAITING_ASSISTED_ENTRY",
                actor=actor_name,
                correlation_id=correlation_id,
                checkpoint="ASSISTED_ENTRY",
                attempt=0,
                reason_code="REQUIRED_FIELDS_MISSING",
            )
        else:
            self._append_processing_transition(
                intake,
                to_stage="MATCHING",
                actor=actor_name,
                correlation_id=correlation_id,
                checkpoint="MATCHING",
                attempt=0,
                timeout_seconds=120,
            )
            match = match_listing(
                values=values,
                canonical_url=canonical_url or "",
                source_id=source_id,
                fingerprint=content_fingerprint(values),
                listings=self._get_match_listings(),
            )
            intake["matchResult"] = match.to_dict()
            self._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
                submitted_values=values,
            )
            self._append_processing_transition(
                intake,
                to_stage=(
                    "NEEDS_REVIEW"
                    if match.outcome == "POSSIBLE_MATCH"
                    else "READY"
                ),
                actor=actor_name,
                correlation_id=correlation_id,
                checkpoint="MATCHING",
                attempt=0,
                timeout_seconds=120,
            )
        intake["submissionReceipt"] = {
            "receiptId": _uuid(),
            "receiptType": f"{method}_INTAKE",
            "intakeId": intake_id,
            "state": intake["stage"],
            "existingListingId": (intake.get("matchResult") or {}).get(
                "targetListingId"
            ),
            "navigationTarget": f"/w/expansion/listings/intake/{intake_id}",
            "correlationId": correlation_id,
            "issuedAt": _now(),
            "evidenceState": "PARTIAL",
        }
        self._save_intake(intake)
        self._save_idempotency("submit_structured_intake", idempotency_key, intake)
        return _copy(intake)

    def list_intakes(self, selected_heat_zone_id: str | None = None) -> list[dict[str, Any]]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        intakes = self._state["assistedIntakes"]
        if selected_heat_zone_id is not None:
            intakes = [item for item in intakes if item.get("heatZoneId") == selected_heat_zone_id]
        return _copy(intakes)

    def get_intake(self, intake_id: str) -> dict[str, Any]:
        self._load_intakes()
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return _copy(intake)
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

    def correct_intake(
        self,
        *,
        intake_id: str,
        fields: dict[str, Any],
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {
            "expansionStaff",
            "expansion_user",
            "expansionManager",
            "expansion-manager",
            "dataSteward",
            "data_owner",
        }
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to correct intake"
            )

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="correct intake",
        )
        cache_key = ("correct_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="correct intake",
        )

        intake = self._listing_intake(intake_id)

        from modules.external_data.application.assisted_intake import (
            IDENTITY_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
        )

        requires_reason = False
        for key in fields:
            if key in IDENTITY_FIELDS:
                requires_reason = True
                break
        if requires_reason and (not reason or not reason.strip()):
            raise NetworkListingPolicyError(
                "reason is required for modifying identity-affecting fields"
            )

        before_stage = intake["stage"]
        before_version = int(intake.get("version") or 0)
        corrected_at = _now()
        corrections_made = []
        before_after_changes = []
        for key, val in fields.items():
            if key not in intake["parsedFields"]:
                intake["parsedFields"][key] = {
                    "key": key,
                    "label": key,
                    "sourceValue": None,
                    "normalizedValue": None,
                    "correctedValue": None,
                    "correctionReason": None,
                    "identity": key in IDENTITY_FIELDS,
                    "lowConfidence": False,
                }

            before_val = intake["parsedFields"][key].get("correctedValue") or intake[
                "parsedFields"
            ][key].get("normalizedValue")
            intake["parsedFields"][key]["correctedValue"] = val
            intake["parsedFields"][key]["correctionReason"] = reason
            intake["parsedFields"][key]["correctionActor"] = actor_name or actor_role_id
            intake["parsedFields"][key]["correctionActorRoleId"] = actor_role_id
            intake["parsedFields"][key]["correctedAt"] = corrected_at
            intake["parsedFields"][key]["sourceSnapshotId"] = intake.get("snapshotId")
            intake["parsedFields"][key]["parserVersion"] = intake.get("parserVersion")
            after_val = val
            corrections_made.append(f"'{key}' corrected from '{before_val}' to '{after_val}'")
            before_after_changes.append(
                {
                    "field": key,
                    "before": before_val,
                    "after": val,
                }
            )

        effective_vals = effective_fields(intake["parsedFields"])

        from modules.external_data.application.assisted_intake import ASSISTED_ENTRY_REQUIRED_FIELDS

        has_all_required = True
        for rf in ASSISTED_ENTRY_REQUIRED_FIELDS:
            val = effective_vals.get(rf)
            if val in (None, ""):
                has_all_required = False
                break
            if rf in ("rent", "areaPing"):
                try:
                    if float(val) <= 0:
                        has_all_required = False
                        break
                except (ValueError, TypeError):
                    has_all_required = False
                    break

        if has_all_required:
            fingerprint = content_fingerprint(effective_vals)
            match_res = match_listing(
                values=effective_vals,
                canonical_url=intake["canonicalUrl"],
                source_id=intake["sourceId"],
                fingerprint=fingerprint,
                listings=self._get_match_listings(),
            )
            intake["matchResult"] = match_res.to_dict()
            self._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
                submitted_values=effective_vals,
            )
            if match_res.outcome == "POSSIBLE_MATCH":
                target_stage = "NEEDS_REVIEW"
            else:
                target_stage = "READY"
        else:
            target_stage = "AWAITING_ASSISTED_ENTRY"

        if has_all_required and before_stage == "AWAITING_ASSISTED_ENTRY":
            intake["stage"] = before_stage
            intake["version"] = before_version
            self._append_processing_transition(
                intake,
                to_stage="PARSING",
                actor=actor_name or actor_role_id,
                correlation_id=correlation_id,
                checkpoint="ASSISTED_ENTRY",
                attempt=0,
                timeout_seconds=120,
                reason_code="ASSISTED_ENTRY_COMPLETE",
            )
            self._append_processing_transition(
                intake,
                to_stage="MATCHING",
                actor=actor_name or actor_role_id,
                correlation_id=correlation_id,
                checkpoint="MATCHING",
                attempt=0,
                timeout_seconds=120,
                reason_code="ASSISTED_ENTRY_PARSED",
            )
            self._append_processing_transition(
                intake,
                to_stage=target_stage,
                actor=actor_name or actor_role_id,
                correlation_id=correlation_id,
                checkpoint="MATCHING",
                attempt=0,
                timeout_seconds=120,
                reason_code="MATCHING_COMPLETED",
            )
        elif target_stage != before_stage:
            intake["stage"] = before_stage
            intake["version"] = before_version
            self._append_processing_transition(
                intake,
                to_stage=target_stage,
                actor=actor_name or actor_role_id,
                correlation_id=correlation_id,
                checkpoint="CORRECTION",
                attempt=0,
                timeout_seconds=None,
                reason_code="CORRECTION_APPLIED",
            )
        else:
            intake["version"] = before_version + 1

        audit_event_id = _uuid()
        audit_evt = {
            "id": audit_event_id,
            "occurredAt": corrected_at,
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": "intake.correct",
            "targetId": intake_id,
            "message": f"Fields corrected: {'; '.join(corrections_made)}. Reason: {reason}",
            "correlationId": correlation_id,
            "metadata": {
                "fields": list(fields.keys()),
                "reason": reason,
                "stage": intake["stage"],
                "matchOutcome": intake["matchResult"]["outcome"] if intake["matchResult"] else None,
                "beforeAfter": before_after_changes,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "before": {
                    "stage": before_stage,
                    "version": before_version,
                    "fields": _copy(before_after_changes),
                },
                "after": {
                    "stage": intake["stage"],
                    "version": intake["version"],
                    "fields": _copy(before_after_changes),
                },
                "sourceSnapshotId": intake.get("snapshotId"),
                "parserVersion": intake.get("parserVersion"),
                "relatedIds": {
                    "intakeId": intake_id,
                    "listingId": (intake.get("matchResult") or {}).get("targetListingId"),
                    "auditEventId": audit_event_id,
                },
                "evidenceState": (
                    "COMPLETE"
                    if intake.get("snapshotId") and intake.get("parserVersion")
                    else "PARTIAL"
                ),
            },
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        self._save_idempotency("correct_intake", governed_key, intake)
        return _copy(intake)

    def decide_intake(
        self,
        *,
        intake_id: str,
        action: str,
        reason: str | None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        target_listing_id: str | None = None,
    ) -> dict[str, Any]:
        # Server-side role check
        allowed_roles = {
            "expansionManager",
            "expansion-manager",
            "siteReviewer",
            "site_reviewer",
            "dataSteward",
            "data_owner",
        }
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to decide intake"
            )

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="decide intake",
        )
        cache_key = ("decide_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("decision reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="decide intake",
        )

        intake = self._listing_intake(intake_id)
        action = action.strip().lower()

        if action not in {"create", "revise", "duplicate", "quarantine", "reject"}:
            raise NetworkListingPolicyError(f"invalid action: {action}")

        from modules.external_data.application.assisted_intake import effective_fields

        effective_vals = effective_fields(intake["parsedFields"])

        before_stage = intake["stage"]
        before_after = {"stage": {"before": before_stage}}
        effect_summary = f"Manual decision '{action}' recorded for intake {intake_id}."
        decision_id = _uuid()
        revision: dict[str, Any] | None = None
        identity_edge: dict[str, Any] | None = None

        if action == "create":
            new_id = f"L-{2031 + len(self._state['listings'])}"
            new_listing = {
                "id": new_id,
                "sourceId": intake["sourceId"],
                "sourceListingId": effective_vals.get("providerListingId", ""),
                "heatZoneId": intake["heatZoneId"] or "HZ-01",
                "address": effective_vals.get("address", ""),
                "status": "new",
                "rentPerMonth": effective_vals.get("rent", 0),
                "areaPing": effective_vals.get("areaPing", 0),
                "floor": effective_vals.get("floor", ""),
                "frontageMeters": 5,
                "geocodeConfidence": 0.9,
                "hardRuleFailures": [],
                "hardRuleSummary": "3/3 pass: area, floor, permitted use",
                "sourceEvidence": [f"EV-{new_id}-RAW", f"EV-{new_id}-GEOCODE"],
                "fitScore": 70,
                "firstSeenAt": _now(),
                "sourceUrl": intake["originalUrl"],
            }
            self._state["listings"].append(new_listing)
            self._sync_listing_to_repo(new_id)
            intake["stage"] = "READY"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "NEW",
                    "outcomeLabel": "新物件",
                    "confidence": 1.0,
                    "targetListingId": new_id,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動建立為新物件 {new_id}。",
                }
            else:
                intake["matchResult"]["targetListingId"] = new_id
                intake["matchResult"]["outcome"] = "NEW"
                intake["matchResult"]["outcomeLabel"] = "新物件"
                intake["matchResult"]["summary"] = f"已手動建立為新物件 {new_id}。"

            identity_edge = self._append_identity_edge(
                intake=intake,
                listing_id=new_id,
                relation="SOURCE_OF",
                decision_id=decision_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                reason=reason_text,
                correlation_id=correlation_id,
            )
            before_after["stage"]["after"] = "READY"
            before_after["listings_count"] = {
                "before": len(self._state["listings"]) - 1,
                "after": len(self._state["listings"]),
            }
            effect_summary = f"Created new listing {new_id} from intake {intake_id}."

        elif action == "revise":
            target_id = (
                target_listing_id
                or (intake.get("matchResult") or {}).get("targetListingId")
                or (self._state["listings"][0]["id"] if self._state.get("listings") else None)
            )
            if not target_id:
                raise NetworkListingConflict("no target listing found for revision")
            target = self._effective_listing(self._listing(target_id))
            revision = self._append_listing_revision(
                intake=intake,
                listing_id=target_id,
                effective_values=effective_vals,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                reason=reason_text,
                correlation_id=correlation_id,
            )
            identity_edge = self._append_identity_edge(
                intake=intake,
                listing_id=target_id,
                relation="REVISION_OF",
                decision_id=decision_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                reason=reason_text,
                correlation_id=correlation_id,
            )
            intake["stage"] = "READY"
            if not intake.get("matchResult"):
                intake["matchResult"] = {"targetListingId": target_id, "confidence": 0.9}
            else:
                intake["matchResult"]["targetListingId"] = target_id
            intake["matchResult"]["summary"] = f"已手動將版本更新至既有物件 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_rent"] = {
                "before": revision["beforeValues"]["rentPerMonth"],
                "after": revision["effectiveValues"]["rentPerMonth"],
            }
            before_after["target_area"] = {
                "before": revision["beforeValues"]["areaPing"],
                "after": revision["effectiveValues"]["areaPing"],
            }
            before_after["target_floor"] = {
                "before": revision["beforeValues"]["floor"],
                "after": revision["effectiveValues"]["floor"],
            }
            effect_summary = (
                f"Appended immutable revision {revision['revisionId']} to "
                f"listing {target_id} from intake {intake_id}."
            )

        elif action == "duplicate":
            target_id = intake["matchResult"].get("targetListingId")
            if not target_id:
                raise NetworkListingConflict("no target listing found for duplicate merge")
            target = self._effective_listing(self._listing(target_id))
            before_evidence_count = len(target.get("identityEdges", []))
            identity_edge = self._append_identity_edge(
                intake=intake,
                listing_id=target_id,
                relation="DUPLICATE_OF",
                decision_id=decision_id,
                actor_role_id=actor_role_id,
                actor_name=actor_name,
                reason=reason_text,
                correlation_id=correlation_id,
            )
            intake["stage"] = "READY"
            intake["matchResult"]["summary"] = f"已手動標記為重複並合併至 {target_id}。"

            before_after["stage"]["after"] = "READY"
            before_after["target_evidence_count"] = {
                "before": before_evidence_count,
                "after": before_evidence_count + 1,
            }
            effect_summary = (
                f"Bound duplicate intake {intake_id} to listing {target_id} "
                f"with identity edge {identity_edge['edgeId']}."
            )

        elif action == "quarantine":
            intake["stage"] = "QUARANTINED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已手動送交隔離。原因：{reason_text}",
                }
            else:
                intake["matchResult"]["outcome"] = "QUARANTINED"
                intake["matchResult"]["summary"] = f"已手動送交隔離。原因：{reason_text}"

            before_after["stage"]["after"] = "QUARANTINED"
            effect_summary = f"Quarantined intake {intake_id}."

        elif action == "reject":
            intake["stage"] = "FAILED"
            if not intake.get("matchResult"):
                intake["matchResult"] = {
                    "outcome": "QUARANTINED",
                    "outcomeLabel": "已隔離",
                    "confidence": 0.0,
                    "targetListingId": None,
                    "agreeingSignals": [],
                    "contradictingSignals": [],
                    "summary": f"已拒絕此送件。原因：{reason_text}",
                }
            else:
                intake["matchResult"]["summary"] = f"已拒絕此送件。原因：{reason_text}"

            before_after["stage"]["after"] = "FAILED"
            effect_summary = f"Rejected intake {intake_id}."

        if intake["stage"] != before_stage:
            target_stage = intake["stage"]
            intake["stage"] = before_stage
            self._append_processing_transition(
                intake,
                to_stage=target_stage,
                actor=actor_name or actor_role_id,
                correlation_id=correlation_id,
                checkpoint="HUMAN_DECISION",
                attempt=0,
                timeout_seconds=None,
                reason_code=f"DECISION_{action.upper()}",
            )
        else:
            intake["version"] = int(intake.get("version") or 0) + 1

        audit_event_id = _uuid()
        audit_evt = {
            "id": audit_event_id,
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "action": f"intake.decide.{action}",
            "targetId": intake_id,
            "message": f"Intake decision '{action}' recorded. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "decision": action,
                "reason": reason_text,
                "stage": intake["stage"],
                "targetListingId": intake["matchResult"].get("targetListingId"),
                "beforeAfter": before_after,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
                "effectSummary": effect_summary,
                "before": _copy(before_after),
                "after": {
                    "stage": intake["stage"],
                    "version": intake["version"],
                },
                "sourceSnapshotId": intake.get("snapshotId"),
                "parserVersion": intake.get("parserVersion"),
                "relatedIds": {
                    "intakeId": intake_id,
                    "listingId": (intake.get("matchResult") or {}).get("targetListingId"),
                    "listingRevisionId": (revision.get("revisionId") if revision else None),
                    "identityEdgeId": (identity_edge.get("edgeId") if identity_edge else None),
                    "decisionId": decision_id,
                },
                "evidenceState": (
                    "COMPLETE"
                    if intake.get("snapshotId") and intake.get("parserVersion")
                    else "PARTIAL"
                ),
            },
        }
        receipt = {
            "receiptId": _uuid(),
            "decisionId": decision_id,
            "decision": action.upper(),
            "status": "EXECUTED",
            "intakeId": intake_id,
            "listingId": (intake.get("matchResult") or {}).get("targetListingId"),
            "listingRevisionId": (revision.get("revisionId") if revision else None),
            "identityEdgeId": (identity_edge.get("edgeId") if identity_edge else None),
            "auditEventId": audit_event_id,
            "correlationId": correlation_id,
            "version": intake["version"],
            "issuedAt": _now(),
            "evidenceState": audit_evt["metadata"]["evidenceState"],
        }
        intake.setdefault("decisionReceipts", []).append(receipt)
        intake["latestDecisionReceipt"] = receipt
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)
        self._save_idempotency("decide_intake", governed_key, intake)
        return _copy(intake)

    def process_queued_intake(
        self,
        *,
        intake_id: str,
        retrieval_provider: Any,
        actor_name: str = "Assisted Intake Worker",
        correlation_id: str | None = None,
        attempt: int = 1,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Execute one queued intake from an approved retrieval adapter.

        The provider is mandatory so production cannot silently fall back to the
        deterministic corpus. It receives ``(canonical_url, policy=decision)``
        and must return ``RetrievalResult`` after the approved network and
        snapshot boundary has completed.
        """
        if retrieval_provider is None:
            raise NetworkListingPolicyError(
                "an approved retrieval provider is required for queued processing"
            )

        from modules.external_data.application.assisted_intake import (
            ASSISTED_ENTRY_REQUIRED_FIELDS,
            content_fingerprint,
            effective_fields,
            match_listing,
            parse_snapshot,
            resolve_source_policy,
        )

        intake = self._listing_intake(intake_id)
        correlation = correlation_id or intake.get("correlationId")
        if intake["stage"] not in {
            "SUBMITTED",
            "FAILED",
            "CHECKING_IDENTITY",
            "CHECKING_SOURCE_POLICY",
            "RETRIEVING",
            "PARSING",
            "MATCHING",
        }:
            raise NetworkListingConflict(
                f"intake {intake_id} is in stage {intake['stage']} and cannot be processed"
            )

        self._append_processing_transition(
            intake,
            to_stage="CHECKING_IDENTITY",
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="CHECKING_IDENTITY",
            attempt=attempt,
            timeout_seconds=30,
        )
        canonical_url = intake["canonicalUrl"]
        exact_listing = next(
            (
                listing
                for listing in self._state["listings"]
                if listing.get("sourceUrl") == canonical_url
            ),
            None,
        )
        if exact_listing is not None:
            intake["matchResult"] = {
                "outcome": "EXACT_DUPLICATE",
                "outcomeLabel": "完全重複",
                "confidence": 1.0,
                "targetListingId": exact_listing["id"],
                "agreeingSignals": [
                    {
                        "key": "canonicalUrl",
                        "label": "Canonical URL",
                        "agrees": True,
                        "detail": canonical_url,
                    }
                ],
                "contradictingSignals": [],
                "summary": f"Canonical source identity already belongs to {exact_listing['id']}.",
            }
            self._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
            )
            self._append_processing_transition(
                intake,
                to_stage="READY",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="CHECKING_IDENTITY",
                attempt=attempt,
                timeout_seconds=30,
                reason_code="EXACT_SOURCE_IDENTITY",
            )
            intake["submissionReceipt"] = {
                "receiptId": _uuid(),
                "receiptType": "EXACT_SOURCE_IDENTITY",
                "intakeId": intake_id,
                "state": "READY",
                "existingListingId": exact_listing["id"],
                "navigationTarget": (f"/w/expansion/listings/{exact_listing['id']}"),
                "correlationId": correlation,
                "issuedAt": _now(),
                "evidenceState": "COMPLETE",
            }
            self._save_intake(intake)
            return _copy(intake)

        self._append_processing_transition(
            intake,
            to_stage="CHECKING_SOURCE_POLICY",
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="CHECKING_SOURCE_POLICY",
            attempt=attempt,
            timeout_seconds=15,
        )
        policy = resolve_source_policy(intake["originalUrl"])
        intake["sourceId"] = policy.source_id
        intake["policy"] = policy.policy
        intake["policyLabel"] = policy.policy_label
        intake["policyReason"] = policy.policy_reason

        if policy.quarantines or policy.policy in {
            "POLICY_UNKNOWN",
            "SOURCE_BLOCKED",
        }:
            intake["matchResult"] = {
                "outcome": "QUARANTINED",
                "outcomeLabel": "已隔離",
                "confidence": 0.0,
                "targetListingId": None,
                "agreeingSignals": [],
                "contradictingSignals": [],
                "summary": policy.policy_reason,
            }
            self._record_match_case(
                intake=intake,
                match_result=intake["matchResult"],
            )
            self._append_processing_transition(
                intake,
                to_stage="QUARANTINED",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="CHECKING_SOURCE_POLICY",
                attempt=attempt,
                timeout_seconds=15,
                reason_code=policy.policy,
            )
            return _copy(intake)

        if policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            required_cells = {
                "address": ("地址", True),
                "rent": ("租金", True),
                "areaPing": ("坪數", True),
            }
            for field_key, (label, identity) in required_cells.items():
                intake.setdefault("parsedFields", {}).setdefault(
                    field_key,
                    {
                        "key": field_key,
                        "label": label,
                        "sourceValue": None,
                        "normalizedValue": None,
                        "correctedValue": None,
                        "correctionReason": None,
                        "identity": identity,
                        "lowConfidence": True,
                        "sourceSnapshotId": None,
                        "parserVersion": None,
                    },
                )
            self._append_processing_transition(
                intake,
                to_stage="AWAITING_ASSISTED_ENTRY",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="CHECKING_SOURCE_POLICY",
                attempt=attempt,
                timeout_seconds=15,
                reason_code=policy.policy,
            )
            return _copy(intake)

        self._append_processing_transition(
            intake,
            to_stage="RETRIEVING",
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="RETRIEVING",
            attempt=attempt,
            timeout_seconds=timeout_seconds,
        )
        try:
            retrieval = retrieval_provider(canonical_url, policy=policy)
        except Exception as exc:
            failure = {
                "code": "RETRIEVAL_PROVIDER_FAILED",
                "summary": str(exc),
                "nextAction": "Retry from the persisted retrieval checkpoint.",
                "retryable": True,
            }
            intake["failure"] = failure
            self._append_processing_transition(
                intake,
                to_stage="FAILED",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="RETRIEVING",
                attempt=attempt,
                timeout_seconds=timeout_seconds,
                failure=failure,
                reason_code=failure["code"],
            )
            return _copy(intake)

        if not retrieval.ok:
            retrieval_failure = retrieval.failure
            failure = {
                "code": (
                    retrieval_failure.code if retrieval_failure is not None else "RETRIEVAL_FAILED"
                ),
                "summary": (
                    retrieval_failure.summary
                    if retrieval_failure is not None
                    else "Approved retrieval failed."
                ),
                "nextAction": (
                    retrieval_failure.next_action
                    if retrieval_failure is not None
                    else "Review source evidence."
                ),
                "retryable": bool(
                    retrieval_failure.retryable if retrieval_failure is not None else False
                ),
            }
            intake["failure"] = failure
            self._append_processing_transition(
                intake,
                to_stage="FAILED",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="RETRIEVING",
                attempt=attempt,
                timeout_seconds=timeout_seconds,
                failure=failure,
                reason_code=failure["code"],
            )
            return _copy(intake)

        intake["rawSnapshot"] = _copy(retrieval.raw)
        intake["snapshotId"] = retrieval.snapshot_id
        intake["capturedAt"] = retrieval.captured_at
        intake.pop("failure", None)
        self._append_processing_transition(
            intake,
            to_stage="PARSING",
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="PARSING",
            attempt=attempt,
            timeout_seconds=300,
        )
        try:
            intake["parsedFields"] = parse_snapshot(retrieval)
        except Exception as exc:
            failure = {
                "code": "PARSER_FAILED",
                "summary": str(exc),
                "nextAction": "Retry parsing from the persisted source snapshot.",
                "retryable": True,
            }
            intake["failure"] = failure
            self._append_processing_transition(
                intake,
                to_stage="FAILED",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="PARSING",
                attempt=attempt,
                timeout_seconds=300,
                failure=failure,
                reason_code=failure["code"],
            )
            return _copy(intake)

        values = effective_fields(intake["parsedFields"])
        has_required_fields = all(
            values.get(field_name) not in {None, ""}
            and (field_name not in {"rent", "areaPing"} or float(values[field_name]) > 0)
            for field_name in ASSISTED_ENTRY_REQUIRED_FIELDS
        )
        if not has_required_fields:
            self._append_processing_transition(
                intake,
                to_stage="AWAITING_ASSISTED_ENTRY",
                actor=actor_name,
                correlation_id=correlation,
                checkpoint="PARSING",
                attempt=attempt,
                timeout_seconds=300,
                reason_code="REQUIRED_FIELDS_MISSING",
            )
            return _copy(intake)

        self._append_processing_transition(
            intake,
            to_stage="MATCHING",
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="MATCHING",
            attempt=attempt,
            timeout_seconds=120,
        )
        match = match_listing(
            values=values,
            canonical_url=canonical_url,
            source_id=policy.source_id,
            fingerprint=content_fingerprint(values),
            listings=self._get_match_listings(),
        )
        intake["matchResult"] = match.to_dict()
        self._record_match_case(
            intake=intake,
            match_result=intake["matchResult"],
            submitted_values=values,
        )
        self._append_processing_transition(
            intake,
            to_stage=("NEEDS_REVIEW" if match.outcome == "POSSIBLE_MATCH" else "READY"),
            actor=actor_name,
            correlation_id=correlation,
            checkpoint="MATCHING",
            attempt=attempt,
            timeout_seconds=120,
        )
        return _copy(intake)

    def retry_intake(
        self,
        *,
        intake_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        job_queue: Any | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        intake = self._listing_intake(intake_id)
        if intake["stage"] not in {
            "FAILED",
            "READY",
            "NEEDS_REVIEW",
            "AWAITING_ASSISTED_ENTRY",
        }:
            raise NetworkListingConflict(
                f"intake {intake_id} is in stage {intake['stage']} and cannot be retried"
            )
        if job_queue is None:
            raise NetworkListingPolicyError("retry requires the durable assisted-intake queue")

        from modules.external_data.application.assisted_intake import (
            resolve_source_policy,
        )
        from shared.jobs.queue import JobRequest

        policy = resolve_source_policy(intake["originalUrl"])
        if policy.quarantines or policy.policy in {
            "POLICY_UNKNOWN",
            "SOURCE_BLOCKED",
        }:
            raise NetworkListingPolicyError(
                f"source policy {policy.policy} does not permit retrieval retry"
            )
        if policy.policy in {"ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"}:
            raise NetworkListingPolicyError(
                f"source policy {policy.policy} requires assisted entry"
            )

        original_key = intake.get("idempotencyKey")
        existing_job = job_queue.get_by_idempotency_key(original_key) if original_key else None
        if existing_job is not None:
            job = job_queue.replay(existing_job.job_id)
        else:
            retry_key = f"{original_key or intake_id}:retry:{int(intake.get('version') or 1) + 1}"
            job, _created = job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={
                        "intake_id": intake_id,
                        "url": intake["originalUrl"],
                        "heat_zone_id": intake.get("heatZoneId"),
                        "actor_role_id": actor_role_id,
                        "actor_name": actor_name,
                        "tenant_id": tenant_id or intake.get("tenantId"),
                    },
                    idempotency_key=retry_key,
                ),
                correlation_id=correlation_id or intake.get("correlationId") or "system",
            )

        intake["jobId"] = job.job_id
        intake["jobReceipt"] = {
            "jobId": job.job_id,
            "status": str(getattr(job.status, "value", job.status)),
            "checkpoint": "CHECKING_IDENTITY",
            "attempt": int(getattr(job, "attempts", 0) or 0),
            "correlationId": correlation_id or intake.get("correlationId"),
            "issuedAt": _now(),
        }
        self._append_processing_transition(
            intake,
            to_stage="SUBMITTED",
            actor=actor_name or actor_role_id,
            correlation_id=correlation_id or intake.get("correlationId"),
            checkpoint="CHECKING_IDENTITY",
            attempt=int(getattr(job, "attempts", 0) or 0),
            timeout_seconds=30,
            reason_code="RETRY_QUEUED",
        )
        return _copy(intake)

    # Adapter methods for PromotionService
    def get_listing(self, listing_id: str) -> dict[str, Any] | None:
        try:
            return self._listing(listing_id)
        except NetworkListingNotFound:
            return None

    def save_listing(self, listing: dict[str, Any], address: Any = None, key: Any = None) -> None:
        for i, lst in enumerate(self._state["listings"]):
            if lst["id"] == listing["id"]:
                self._state["listings"][i] = listing
                break
        self._sync_listing_to_repo(listing["id"])

    def list_candidates(self) -> list[dict[str, Any]]:
        cands = list(self._state["candidates"])
        if hasattr(self, "_listing_repository") and self._listing_repository:
            for draft in self._listing_repository.list_candidates():
                # Handle draft being either a CandidateSiteDraft or a dictionary
                if hasattr(draft, "candidate_site"):
                    listing_id = draft.candidate_site.listing_id
                    c_id = draft.candidate_site.candidate_site_id
                    status = draft.candidate_site.site_status
                else:
                    listing_id = draft.get("listingId") or draft.get("listing_id")
                    c_id = draft.get("candidateSiteId") or draft.get("id")
                    status = draft.get("status")
                if not any(
                    c.get("listingId") == listing_id or c.get("listing_id") == listing_id
                    for c in cands
                ):
                    cands.append(
                        {
                            "id": c_id,
                            "listingId": listing_id,
                            "status": status,
                        }
                    )
        return cands

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for cand in self._state["candidates"]:
            if cand["id"] == candidate_id:
                return cand
        return None

    def save_candidate(self, candidate: dict[str, Any]) -> None:
        for i, cand in enumerate(self._state["candidates"]):
            if cand["id"] == candidate["id"]:
                self._state["candidates"][i] = candidate
                self._sync_candidate_to_repo(candidate["id"])
                return
        self._state["candidates"].append(candidate)
        self._sync_candidate_to_repo(candidate["id"])

    def get_listing_intake(self, intake_id: str) -> dict[str, Any] | None:
        try:
            return self._listing_intake(intake_id)
        except NetworkListingNotFound:
            return None

    def save_intake(self, intake: dict[str, Any]) -> None:
        self._save_intake(intake)

    def promote_intake(
        self,
        *,
        intake_id: str,
        reason: str | None = None,
        risk_summary: str | None = None,
        risk_acknowledged: bool = False,
        actor_role_id: str,
        actor_name: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        allowed_roles = {
            "expansionManager",
            "expansion-manager",
            "siteReviewer",
            "site_reviewer",
            "expansionUser",
            "expansion-user",
            "expansion_user",
        }
        if actor_role_id not in allowed_roles:
            raise NetworkListingPolicyError(
                f"role {actor_role_id!r} is not allowed to promote intake"
            )

        governed_key = _require_governed_write_context(
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            action_label="promote intake",
        )
        cache_key = ("promote_intake", governed_key)
        if cache_key in self._idempotency_cache:
            return _copy(self._idempotency_cache[cache_key])

        reason_text = (reason or "").strip()
        if not reason_text:
            raise NetworkListingPolicyError("promotion reason is required")

        risk_summary_text = _require_acknowledged_risk(
            risk_summary=risk_summary,
            risk_acknowledged=risk_acknowledged,
            action_label="promote intake",
        )

        intake = self._listing_intake(intake_id)
        target_listing_id = intake["matchResult"].get("targetListingId")
        if not target_listing_id:
            raise NetworkListingConflict("intake must be resolved to a listing before promotion")

        listing = self._listing(target_listing_id)

        # Enforce segregation of duties & run reviewed promotion saga
        proposer_id = actor_name or intake.get("submitter") or "operator-expansion-staff"

        import hashlib

        gate_snap = str(listing.get("fitScore", 0))
        gate_snapshot_sha256 = hashlib.sha256(gate_snap.encode()).hexdigest()

        from modules.listing.application.promotion import PromotionService
        from modules.listing.domain.intake_states import Actor, PrincipalRole, TransitionContext

        promo_service = PromotionService(
            promotion_repository=self._intakes,
            listing_repository=self,
            intake_repository=self,
            outbox_repository=getattr(self._listing_repository, "outbox_repository", None),
        )

        # 1. Propose
        proposer_actor = Actor(
            actor_id=proposer_id,
            role=PrincipalRole.EXPANSION_STAFF,
            tenant_id=listing.get("tenantId") or "tenant-a",
        )
        proposer_context = TransitionContext(
            actor=proposer_actor,
            idempotency_key=governed_key,
            correlation_id=correlation_id,
            risk_acknowledged=risk_acknowledged,
            reason=reason_text,
        )

        try:
            promo_record = promo_service.request_promotion(
                intake_id=intake_id,
                target_format_code="FORMAT-A",
                reason=reason_text,
                gate_snapshot_sha256=gate_snapshot_sha256,
                context=proposer_context,
            )
        except Exception as exc:
            if "DUPLICATE_CANDIDATE" in str(exc) or "DEPENDENCY_CONFLICT" in str(exc):
                raise NetworkListingConflict(str(exc)) from exc
            raise NetworkListingPolicyError(str(exc)) from exc

        audit_evt = {
            "id": _uuid(),
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": proposer_id,
            "action": "intake.promote_request",
            "targetId": intake_id,
            "message": f"Requested promotion of target listing {target_listing_id}. Reason: {reason_text}",
            "correlationId": correlation_id,
            "metadata": {
                "targetListingId": target_listing_id,
                "reason": reason_text,
                "riskSummary": risk_summary_text,
                "riskAcknowledged": True,
            },
        }
        intake["auditEvents"].append(audit_evt)
        self._save_intake(intake)

        result = {
            "promotion_decision_id": promo_record["promotion_decision_id"],
            "intake_id": promo_record["intake_id"],
            "listing_id": promo_record["listing_id"],
            "status": promo_record["status"],
            "version": promo_record["version"],
            "created": False,
        }

        self._save_idempotency("promote_intake", governed_key, result)
        return result

    def _get_match_listings(self) -> list[dict[str, Any]]:
        res = []
        for lst in self._state["listings"]:
            source_id = lst.get("sourceId", "")
            source_listing_id = lst.get("sourceListingId", "")

            fp = lst.get("contentFingerprint")
            if not fp:
                from modules.external_data.application.assisted_intake import content_fingerprint

                fields_for_fp = {
                    "address": lst.get("address", ""),
                    "floor": lst.get("floor", ""),
                    "areaPing": lst.get("areaPing"),
                    "rent": lst.get("rentPerMonth"),
                    "listingType": lst.get("listingType", "店面"),
                    "listingStatus": lst.get("listingStatus", "active"),
                }
                fp = content_fingerprint(fields_for_fp)
                lst["contentFingerprint"] = fp

            res.append(
                {
                    "id": lst["id"],
                    "sourceId": source_id,
                    "sourceListingId": source_listing_id,
                    "canonicalUrl": lst.get("sourceUrl", ""),
                    "contentFingerprint": fp,
                    "address": lst.get("address", ""),
                    "floor": lst.get("floor", ""),
                    "areaPing": lst.get("areaPing"),
                    "rentPerMonth": lst.get("rentPerMonth"),
                    "listingType": lst.get("listingType"),
                }
            )
        return res

    def _listing_intake(self, intake_id: str) -> dict[str, Any]:
        self._state.setdefault("assistedIntakes", [])
        for intake in self._state["assistedIntakes"]:
            if intake["id"] == intake_id:
                return intake
        raise NetworkListingNotFound(f"assisted intake record {intake_id} not found")

    def _listing(self, listing_id: str) -> dict[str, Any]:
        for listing in self._state["listings"]:
            if listing.get("id") == listing_id:
                return listing
        raise NetworkListingNotFound(f"listing {listing_id} not found")

    def _candidate_for_listing(self, listing_id: str) -> dict[str, Any] | None:
        return next(
            (
                candidate
                for candidate in self._state["candidates"]
                if candidate.get("listingId") == listing_id
            ),
            None,
        )

    def _get_snapshot_service(self) -> Any:
        import os

        from modules.external_data.application.source_snapshots import SourceSnapshotService
        from shared.infrastructure.object_store.client import GcsObjectStore, InMemoryObjectStore

        doc_store = None
        db_conn = None
        if hasattr(self._intakes, "_store"):
            doc_store = self._intakes._store
            if hasattr(doc_store, "_engine"):
                db_conn = doc_store._engine

        def residency_resolver(tenant_id: str) -> str:
            if doc_store:
                tenant_meta = doc_store.get("operator.tenant_metadata", tenant_id)
                if tenant_meta:
                    return tenant_meta.get("residency_mode", "TW_ONLY")
            return "TW_ONLY"

        if os.environ.get("ODP_OBJECT_STORE") == "gcs":
            object_store = GcsObjectStore(tenant_residency_resolver=residency_resolver)
        else:
            object_store = InMemoryObjectStore(tenant_residency_resolver=residency_resolver)

        return SourceSnapshotService(
            db_conn=db_conn,
            object_store=object_store,
            document_store=doc_store,
        )

    def _get_security_gate(self, snapshot_service: Any) -> Any:
        from modules.external_data.security.assisted_listing_retrieval import RetrievalSecurityGate

        return RetrievalSecurityGate(source_snapshot_service=snapshot_service)

    def _audit(
        self,
        *,
        action: str,
        target_id: str,
        actor_role_id: str,
        actor_name: str | None,
        correlation_id: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "id": f"AUD-NET-{uuid.uuid4().hex[:10]}",
            "occurredAt": _now(),
            "actorRoleId": actor_role_id,
            "actorName": actor_name or "Expansion Manager",
            "category": "workflow",
            "action": action,
            "targetType": "listing",
            "targetId": target_id,
            "message": f"{action} recorded for {target_id}",
            "correlationId": correlation_id,
            "metadata": metadata,
        }
        self._state["auditEvents"].insert(0, event)
        return _copy(event)

    def _expansion_steps(self, *, selected_id: str) -> list[dict[str, Any]]:
        has_selected_zone = any(zone["id"] == selected_id for zone in self._state["heatZones"])
        candidate = next(
            (item for item in self._state["candidates"] if item.get("id") == "CS-1001"),
            None,
        )
        listing = self._listing("L-2024")
        candidate_created = candidate is not None
        return [
            {
                "id": "find",
                "label": "Find Area",
                "tabIndex": 0,
                "state": "completed" if has_selected_zone else "current",
                "entityId": selected_id,
                "summary": f"{selected_id} selected and synchronized to the map.",
            },
            {
                "id": "radar",
                "label": "Listing Radar",
                "tabIndex": 1,
                "state": "completed" if candidate_created else "current",
                "entityId": "L-2024",
                "summary": f"{listing['id']} clean; duplicate and hard-rule rows visible.",
            },
            {
                "id": "candidate",
                "label": "Candidate",
                "tabIndex": 2,
                "state": "current" if candidate_created else "next",
                "entityId": "CS-1001" if candidate_created else "L-2024",
                "summary": "CS-1001 created once from L-2024."
                if candidate_created
                else "Convert L-2024 to create CS-1001.",
            },
            {
                "id": "sitescore",
                "label": "SiteScore",
                "tabIndex": 3,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Ready for GO 82 evidence review."
                if candidate_created
                else "Blocked until candidate exists.",
            },
            {
                "id": "compare",
                "label": "Compare",
                "tabIndex": 4,
                "state": "next" if candidate_created else "blocked",
                "entityId": "CS-1001",
                "summary": "Compare HZ-01 candidate against HZ-02 pipeline."
                if candidate_created
                else "Blocked by missing candidate.",
            },
            {
                "id": "review",
                "label": "Review",
                "tabIndex": 5,
                "state": "blocked" if not candidate_created else "next",
                "entityId": "RV-1001" if candidate_created else None,
                "summary": "Reasoned review is available after scoring gate."
                if candidate_created
                else "No candidate review packet yet.",
            },
        ]


__all__ = [
    "NetworkListingConflict",
    "NetworkListingNotFound",
    "NetworkListingPolicyError",
    "NetworkListingService",
]
