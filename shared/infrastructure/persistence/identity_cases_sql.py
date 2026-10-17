"""Native match-case entry for an explicitly selected canonical Intake.

The application owns actor/role/scope authorization and HTTP idempotency. This
storage port never creates an Intake to satisfy a foreign key, supplies missing
confidence, or adopts a legacy Listing without a recorded native version.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.domain.events import DomainEvent


@dataclass(frozen=True)
class MatchCandidateEvidence:
    property_id: str
    property_version: int
    confidence: Decimal
    evidence: dict[str, Any]
    listing_id: str | None = None
    listing_version: int | None = None


def _confidence(value: Decimal) -> str:
    if (not isinstance(value, Decimal) or not value.is_finite() or not 0 <= value <= 1
            or value.quantize(Decimal("0.0001")) != value):
        raise ValueError("Explicit confidence with at most four decimals is required")
    return str(value)


def _version(value: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("A recorded positive native version is required")
    return value


class CanonicalMatchCaseRepository:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine = graph, graph.engine

    def get_for_intake(self, tenant: str, intake_id: str) -> dict:
        """Select the unique case of this Intake, never another available case."""
        UUID(intake_id)
        with self.graph.transaction(tenant):
            self.graph._one("SELECT intake_id FROM intake.intakes WHERE tenant_id=? AND intake_id=?", (tenant, intake_id))
            row = self.engine.query_one(
                "SELECT match_case_id FROM identity.match_cases WHERE tenant_id=? AND intake_id=?",
                (tenant, intake_id),
            )
            if row is None:
                raise IdentityNotFoundError("CANONICAL_MATCH_CASE_NOT_FOUND")
            case, intake = self.graph._case(tenant, row["match_case_id"])
            candidates = self.engine.query(
                "SELECT * FROM identity.match_candidates WHERE tenant_id=? AND match_case_id=? ORDER BY rank",
                (tenant, row["match_case_id"]),
            )
            for name in ("supporting_signals", "contradictory_signals"):
                if isinstance(case[name], str):
                    case[name] = json.loads(case[name])
            for candidate in candidates:
                if isinstance(candidate["evidence"], str):
                    candidate["evidence"] = json.loads(candidate["evidence"])
            return {"match_case": case, "intake": intake, "candidates": candidates}

    def create_review_case(
        self, tenant: str, intake_id: str, *, expected_intake_version: int, actor_id: str,
        confidence: Decimal, candidates: tuple[MatchCandidateEvidence, ...],
        supporting_signals: list[dict], contradictory_signals: list[dict], reason: str,
    ) -> dict:
        """Persist real matching evidence and route an open Intake to review.

        Initial PROPOSED→PENDING_REVIEW and optional MATCHING→NEEDS_REVIEW are
        separate audited transitions committed in one serializable transaction.
        The caller must finish its independent authorization/idempotency command
        in this same unit before allowing an HTTP receipt to escape.
        """
        UUID(intake_id)
        UUID(actor_id)
        _version(expected_intake_version)
        numeric_confidence = _confidence(confidence)
        supporting_signals, contradictory_signals, candidates = deepcopy((supporting_signals, contradictory_signals, candidates))
        if not candidates or not supporting_signals or not reason.strip():
            raise ValueError("Match candidates, supporting evidence and reason are required")
        if len({(c.property_id, c.listing_id) for c in candidates}) != len(candidates):
            raise ValueError("Match candidates must be unique")
        # Fail before SQL if evidence cannot be represented by canonical JSON.
        json.dumps([supporting_signals, contradictory_signals, [c.evidence for c in candidates]], allow_nan=False)
        with self.graph.transaction(tenant):
            intake = self.graph._one(
                "SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, intake_id),
            )
            if intake["version"] != expected_intake_version:
                raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
            if intake["processing_state"] not in {"MATCHING", "NEEDS_REVIEW"} or intake["deleted_at"]:
                raise IdentityConflictError("INTAKE_NOT_AWAITING_IDENTITY_REVIEW")
            self.graph._no_hold(tenant, "intake", intake_id, held=intake["legal_hold"])
            if self.engine.query_one("SELECT match_case_id FROM identity.match_cases WHERE tenant_id=? AND intake_id=?", (tenant, intake_id)):
                raise IdentityConflictError("CANONICAL_MATCH_CASE_ALREADY_EXISTS")
            saved = {}
            seen_bindings = set()
            for rank, candidate in sorted(enumerate(candidates, 1), key=lambda item: (item[1].property_id, item[1].listing_id or "")):
                UUID(candidate.property_id)
                property_row = self.graph._one(
                    "SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=? FOR UPDATE",
                    (tenant, candidate.property_id),
                )
                if property_row["version"] != _version(candidate.property_version):
                    raise IdentityConflictError("PROPERTY_VERSION_CONFLICT")
                if property_row["status"] != "ACTIVE" or property_row["current_redirect_property_id"]:
                    raise IdentityConflictError("PROPERTY_TERMINAL")
                self.graph._no_hold(tenant, "property", candidate.property_id)
                evidence = {"reported_evidence": candidate.evidence, "property_version": property_row["version"]}
                normalized_listing_id = None
                if candidate.listing_id is not None:
                    UUID(candidate.listing_id)
                    listing = self.graph._one(
                        "SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=? FOR UPDATE",
                        (tenant, candidate.listing_id),
                    )
                    if listing["version"] != _version(candidate.listing_version):
                        raise IdentityConflictError("LISTING_VERSION_CONFLICT")
                    self.graph._no_hold(tenant, "listing", candidate.listing_id, held=listing["legal_hold"])
                    if listing["lifecycle_state"] in {"ARCHIVED", "QUARANTINED"}:
                        raise IdentityConflictError("LISTING_TERMINAL")
                    # Resolve the actual current edge, preserving the immutable
                    # Listing reference when an earlier decision rebound it.
                    resolved = self.graph.resolve_listing(tenant, candidate.listing_id)
                    if resolved["effective_property_id"] != property_row["property_id"]:
                        raise IdentityConflictError("MATCH_CANDIDATE_LINEAGE_CONFLICT")
                    evidence["listing_version"] = listing["version"]
                    normalized_listing_id = listing["listing_id"]
                elif candidate.listing_version is not None:
                    raise ValueError("Listing version requires a Listing identity")
                binding = (property_row["property_id"], normalized_listing_id)
                if binding in seen_bindings:
                    raise ValueError("Match candidates must have unique canonical identities")
                seen_bindings.add(binding)
                saved[rank] = (candidate, evidence, _confidence(candidate.confidence))
            case_id = str(uuid4())
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            self.engine.execute(
                "INSERT INTO identity.match_cases (match_case_id,tenant_id,intake_id,outcome,status,confidence,"
                "supporting_signals,contradictory_signals,proposed_by) VALUES (?,?,?,'POSSIBLE_MATCH','PROPOSED',?,?::jsonb,?::jsonb,?)",
                (case_id, tenant, intake_id, numeric_confidence, json.dumps(supporting_signals), json.dumps(contradictory_signals), actor_id),
            )
            candidate_ids = []
            for rank, (candidate, evidence, numeric) in sorted(saved.items()):
                candidate_id = str(uuid4())
                self.engine.execute(
                    "INSERT INTO identity.match_candidates (match_candidate_id,tenant_id,match_case_id,property_id,"
                    "listing_id,rank,confidence,evidence) VALUES (?,?,?,?,?,?,?,?::jsonb)",
                    (candidate_id, tenant, case_id, candidate.property_id, candidate.listing_id, rank, numeric, json.dumps(evidence)),
                )
                candidate_ids.append(candidate_id)
            proposed = {"match_case_id": case_id, "intake_id": intake_id, "status": "PROPOSED", "version": 1,
                        "confidence": float(confidence), "candidate_ids": candidate_ids}
            self._audit(tenant, intake, actor_id, case_id, None, proposed, "match.case_created.v1", reason, at)
            if self.engine.execute(
                "UPDATE identity.match_cases SET status='PENDING_REVIEW',version=version+1,updated_at=? "
                "WHERE tenant_id=? AND match_case_id=? AND status='PROPOSED' AND version=1", (at, tenant, case_id),
            ).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            pending = {**proposed, "status": "PENDING_REVIEW", "version": 2}
            audit_id = self._audit(tenant, intake, actor_id, case_id, proposed, pending, "match.review_required.v1", reason, at)
            if intake["processing_state"] == "MATCHING":
                before = {"intake_id": intake_id, "state": "MATCHING", "version": intake["version"]}
                updated = self.engine.query_one(
                    "UPDATE intake.intakes SET processing_state='NEEDS_REVIEW',version=version+1,last_transition_at=? "
                    "WHERE tenant_id=? AND intake_id=? AND version=? AND processing_state='MATCHING' RETURNING version",
                    (at, tenant, intake_id, intake["version"]),
                )
                if updated is None:
                    raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
                sequence = self.engine.query_one(
                    "SELECT COALESCE(MAX(sequence_no),0)+1 AS next FROM intake.intake_stage_transitions "
                    "WHERE tenant_id=? AND intake_id=?", (tenant, intake_id),
                )["next"]
                transition_id = str(uuid4())
                self.engine.execute(
                    "INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                    "from_state,to_state,actor_subject_id,permission,reason_code,reason_text,expected_version,"
                    "resulting_version,match_case_id,correlation_id,occurred_at) "
                    "VALUES (?,?,?,?,'MATCHING','NEEDS_REVIEW',?,'identity.review','POSSIBLE_MATCH',?,?,?,?,?,?)",
                    (transition_id, tenant, intake_id, sequence, actor_id, reason, intake["version"],
                     updated["version"], case_id, intake["correlation_id"], at),
                )
                after = {"intake_id": intake_id, "state": "NEEDS_REVIEW", "version": updated["version"]}
                self._audit(tenant, intake, actor_id, intake_id, before, after,
                            "intake.review_required.v1", reason, at, resource_type="intake")
                intake["version"] = updated["version"]
                self.graph._outbox(DomainEvent(
                    event_type="intake.state_changed", tenant_id=tenant, aggregate_type="intake", aggregate_id=intake_id,
                    aggregate_version=intake["version"], partition_key=tenant + ":" + intake_id,
                    correlation_id=intake["correlation_id"], producer="listing_intake_service", occurred_at=datetime.fromisoformat(at),
                    schema_ref="#/payloads/IntakeStateChangedV1", actor_ref=actor_id,
                    payload={"intake_id": intake_id, "from_state": "MATCHING", "to_state": "NEEDS_REVIEW",
                             "transition_id": transition_id, "reason_code": "POSSIBLE_MATCH", "version": intake["version"],
                             "occurred_at": at},
                ))
            self.graph._outbox(DomainEvent(
                event_type="match.review_required", tenant_id=tenant, aggregate_type="match_case", aggregate_id=case_id,
                aggregate_version=2, partition_key=tenant + ":" + case_id, correlation_id=intake["correlation_id"],
                producer="matcher_service", schema_ref="#/payloads/MatchReviewRequiredV1",
                occurred_at=datetime.fromisoformat(at), actor_ref=actor_id,
                payload={"match_case_id": case_id, "intake_id": intake_id, "outcome": "POSSIBLE_MATCH",
                         "confidence": float(confidence), "candidate_ids": candidate_ids,
                         "contradictory_signals": contradictory_signals},
            ))
            return {**pending, "audit_event_id": audit_id, "intake_version": intake["version"],
                    "correlation_id": intake["correlation_id"]}

    def _audit(self, tenant, intake, actor, case_id, before, after, action, reason, at, *, resource_type="match_case"):
        audit_id = str(uuid4())
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": resource_type, "resource_id": case_id,
            "action": action, "result": "SUCCEEDED", "actor_subject_id": actor, "correlation_id": intake["correlation_id"],
            "occurred_at": at, "before_value": before, "after_value": after, "reason": reason,
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        return audit_id


__all__ = ["CanonicalMatchCaseRepository", "MatchCandidateEvidence"]
