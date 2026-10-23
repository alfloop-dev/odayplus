"""Native match-case entry for an explicitly selected canonical Intake.

The application owns actor/role/scope authorization and HTTP idempotency. This
storage port never creates an Intake to satisfy a foreign key, supplies missing
confidence, or adopts a legacy Listing without a recorded native version.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.auth import Principal
from shared.domain.events import DomainEvent, validate_event


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


def validate_match_case_outcome(outcome: str, candidates) -> None:
    """Validate the same proposal shape at the API and storage boundaries."""
    if outcome not in {"NEW", "REVISION", "POSSIBLE_MATCH", "EXACT_DUPLICATE"}:
        raise ValueError("A supported human match proposal outcome is required")
    if outcome == "NEW":
        if candidates:
            raise ValueError("A NEW proposal cannot select an existing candidate")
    elif outcome in {"REVISION", "EXACT_DUPLICATE"}:
        if len(candidates) != 1 or candidates[0].listing_id is None:
            raise ValueError("A REVISION proposal requires exactly one recorded Listing")
    elif not candidates:
        raise ValueError("A POSSIBLE_MATCH proposal requires candidates")
    for candidate in candidates:
        if candidate.listing_id is not None:
            _version(candidate.listing_version)
        elif candidate.listing_version is not None:
            raise ValueError("Listing version requires a Listing identity")


class CanonicalMatchCaseRepository:
    def __init__(self, graph: Any, *, replay_store=None) -> None:
        self.graph, self.engine = graph, graph.engine
        self.replay_store = replay_store

    def property_options(self, tenant, *, scope, after, limit):
        """Read actual active roots for human case context, never match scores.

        Property has a region axis. Missing brand/store/area/HeatZone axes
        cannot borrow grants from the parent Intake. Filter before pagination;
        the HTTP owner still authorizes each returned target and lookahead.
        """
        representable = not any((scope.brand_ids, scope.store_ids, scope.assigned_area_ids, scope.heat_zone_ids))
        return self.engine.query("SELECT * FROM identity.properties WHERE tenant_id=? AND status='ACTIVE' "
            "AND current_redirect_property_id IS NULL AND ?::boolean "
            "AND (?::boolean OR region_id=ANY(?::uuid[])) "
            "AND (?::uuid IS NULL OR property_id>?::uuid) ORDER BY property_id LIMIT ?",
            (tenant, representable, not scope.region_ids, sorted(scope.region_ids), after, after, limit))

    def _matching_authority(self, tenant, intake, principal, policy_version_id, before, at):
        """Consume the existing registry's actual DATA_OWNER purpose approval."""
        from modules.listing.application.intake_registry_calendar import (
            RegistryIntakeWorkflowPolicy,
        )
        from modules.listing.application.native_intake_requester_authority import (
            current_native_identity_store,
        )
        from shared.auth import DataClassification, Role
        from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository

        identities = current_native_identity_store(self.engine, tenant, before=before)
        registry = RegistryIntakeWorkflowPolicy(SqlDecisionPolicyRepository(self.engine), identities)
        before()
        policy = registry._check_policy(registry._policies.find_version(policy_version_id),
            kind="native_matching_proposal_authority", scope=intake, at=at)
        p = policy.parameters
        required = {"schema_version", "enabled", "service_subjects", "source_ids", "corpus_source_ids", "max_corpus_rows"}
        if (policy.owner_role != Role.DATA_OWNER.value or set(p) != required
                or type(p["schema_version"]) is not int or p["schema_version"] != 1 or p["enabled"] is not True
                or not all(policy.reads(name) for name in ("scope", "service_principal", "source_id", "parser_result", "matching_corpus"))
                or type(p["max_corpus_rows"]) is not int or not 1 <= p["max_corpus_rows"] <= 1000):
            raise PermissionError("MATCHING_DATA_OWNER_PURPOSE_APPROVAL_REQUIRED")
        for name in ("service_subjects", "source_ids", "corpus_source_ids"):
            entries = p[name]
            if (not isinstance(entries, list) or any(not isinstance(x, str) or not 1 <= len(x) <= 256
                    or x != x.strip() or any(ord(c) < 32 for c in x) for x in entries)
                    or len(set(entries)) != len(entries) or (name != "corpus_source_ids" and not entries)):
                raise PermissionError("MATCHING_EXPLICIT_PURPOSE_SCOPE_REQUIRED")
        owner_scope = identities.get_account_scope(UUID(policy.approved_by), require_explicit=True)
        if (principal.subject_id not in p["service_subjects"] or intake["source_id"] not in p["source_ids"]
                or not registry._scope_allows(principal.scope, intake)
                or principal.scope.clearance < DataClassification.INTERNAL
                or owner_scope.clearance < DataClassification.INTERNAL):
            raise PermissionError("MATCHING_SERVICE_OR_SOURCE_SCOPE_DENIED")
        before.check_in_memory()
        return policy, owner_scope

    def _evaluate_parser_match(self, tenant, intake, policy, owner_scope, principal, *, before):
        """Read one complete authorized parser/corpus snapshot for both callers."""
        from modules.external_data.application.assisted_intake import (
            content_fingerprint,
            match_listing,
        )
        from modules.listing.application.intake_authorization import mask_intake
        from modules.listing.application.intake_correction_lineage import effective_value
        from modules.listing.application.intake_registry_calendar import (
            RegistryIntakeWorkflowPolicy,
        )
        from modules.listing.application.native_intake_assisted_entry_input import (
            NativeAssistedEntryFields,
        )
        from shared.infrastructure.persistence.identity_decisions_sql import (
            CanonicalIdentityDecisionRepository,
        )

        source = CanonicalIdentityDecisionRepository(self.graph)._listing_input_context(
            tenant, intake, before=before, require_proposed=True)
        permitted = replace(principal, scope=replace(principal.scope,
            clearance=min(principal.scope.clearance, owner_scope.clearance)))
        if any(row.get("masked") for row in mask_intake(permitted, {"fields": source["fields"]})["fields"]):
            raise PermissionError("FIELD_MASKED")
        # These are the proposed values a human could confirm, not
        # applied facts. Use the existing correction value priority
        # that also supplies the executor's effective revision values.
        values = NativeAssistedEntryFields.model_validate(
            {row["field_path"]: effective_value(row) for row in source["fields"]}).values()
        before()
        sources = self.engine.query("SELECT DISTINCT source_id FROM expansion.listings WHERE tenant_id=?", (tenant,))
        if any(row["source_id"] not in policy.parameters["corpus_source_ids"] for row in sources):
            raise PermissionError("MATCHING_CORPUS_SOURCE_COVERAGE_REQUIRED")
        before()
        if self.engine.query_one("SELECT source_id FROM intake.source_registry WHERE source_id=ANY(?::text[]) "
                "AND kill_switch LIMIT 1", (policy.parameters["corpus_source_ids"],)):
            raise PermissionError("MATCHING_CORPUS_SOURCE_DISABLED")
        before()
        rows = self.engine.query("SELECT l.*,r.material_fingerprint,r.listing_revision_id,"
            "jsonb_build_object('address',r.effective_values->'address','rent',r.effective_values->'rent',"
            "'areaPing',r.effective_values->'areaPing','floor',r.effective_values->'floor',"
            "'listingType',r.effective_values->'listingType','listingStatus',r.effective_values->'listingStatus') AS matching_values "
            "FROM expansion.listings l LEFT JOIN expansion.listing_revisions r "
            "ON r.tenant_id=l.tenant_id AND r.listing_id=l.listing_id AND r.listing_revision_id=l.current_revision_id "
            "WHERE l.tenant_id=? ORDER BY l.listing_id LIMIT ?", (tenant, policy.parameters["max_corpus_rows"] + 1))
        if len(rows) > policy.parameters["max_corpus_rows"]:
            raise IdentityConflictError("MATCHING_COMPLETE_CORPUS_BOUND_EXCEEDED")
        before()
        resolved = self.graph.resolve_listings(tenant, [r["listing_id"] for r in rows]) if rows else {}
        before()
        properties = self.engine.query("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=ANY(?::uuid[])",
            (tenant, sorted({r["effective_property_id"] for r in resolved.values()})))
        props = {r["property_id"]: r for r in properties}
        corpus = []
        for row in rows:
            bound = resolved[row["listing_id"]]
            prop = props[bound["effective_property_id"]]
            resource = {**row, "region_id": prop["region_id"]}
            if (not RegistryIntakeWorkflowPolicy._scope_allows(principal.scope, resource)
                    or not RegistryIntakeWorkflowPolicy._scope_allows(owner_scope, resource)):
                raise PermissionError("MATCHING_CORPUS_RESOURCE_SCOPE_DENIED")
            if row["listing_revision_id"] is None or row["legal_hold"] or prop["status"] != "ACTIVE":
                raise IdentityConflictError("MATCHING_CORPUS_CURRENT_EVIDENCE_REQUIRED")
            facts = json.loads(row["matching_values"]) if isinstance(row["matching_values"], str) else row["matching_values"]
            typed = NativeAssistedEntryFields.model_validate({k: v for k, v in facts.items() if v is not None}).values()
            corpus.append({**typed, "id": row["listing_id"], "sourceId": row["source_id"],
                "sourceListingId": row["source_listing_id"] or "", "canonicalUrl": row["canonical_url_sha256"],
                "rentPerMonth": typed["rent"], "contentFingerprint": row["material_fingerprint"].strip()})
        before()
        if self.engine.query_one("SELECT legal_hold_id FROM audit.legal_holds WHERE tenant_id=? AND released_at IS NULL "
                "AND ((subject_type='listing' AND subject_id=ANY(?::uuid[])) OR "
                "(subject_type='property' AND subject_id=ANY(?::uuid[]))) LIMIT 1",
                (tenant, list(resolved), list(props))):
            raise IdentityConflictError("MATCHING_CORPUS_HELD")
        before.check_in_memory()
        matched = match_listing(values=values, canonical_url=intake["canonical_url_sha256"],
            source_id=intake["source_id"], fingerprint=content_fingerprint(values), listings=corpus)
        confidence = Decimal(str(matched.confidence)).quantize(Decimal("0.0001"))
        candidate_ids = matched.candidate_listing_ids or (
            (matched.target_listing_id,) if matched.target_listing_id is not None else ())
        candidates = []
        for listing_id in candidate_ids:
            bound = resolved[listing_id]
            candidates.append(MatchCandidateEvidence(bound["effective_property_id"], bound["current_property_version"],
                confidence, {"shared_matcher_signals": [signal.to_dict() for signal in matched.signals]},
                listing_id, bound["listing_version"]))
        # Keep the joined matching projection separate from the actual native
        # Listing row consumed by the established exact graph proof.
        listings = {row["listing_id"]: {name: value for name, value in row.items()
            if name not in {"material_fingerprint", "listing_revision_id", "matching_values"}} for row in rows}
        return {"source": source, "values": values, "matched": matched, "resolved": resolved, "candidates": tuple(candidates),
            "confidence": confidence, "properties": props, "listings": listings}

    def match_partition(self, tenant, *, source_id, policy_version_id, boundary, credentials, limit):
        """Recover durable native parser results through the single case command.

        The selector grants no authority. Each command proves the full parser
        history and current service, DATA_OWNER, source and corpus permissions.
        Already recorded cases are excluded, so a new process resumes progress.
        """
        from modules.listing.application.native_intake_parser_policy import JOB_TYPE

        UUID(tenant)
        if type(limit) is not int or not 1 <= limit <= 100 or not source_id:
            raise ValueError("An explicit source partition and bounded limit are required")
        if boundary is None:
            raise PermissionError("MATCHING_AUTHENTICATION_BOUNDARY_REQUIRED")
        verified = boundary.authenticate(credentials)
        if not verified.authenticated or verified.token_type != "service" or verified.principal.tenant_id != tenant:
            raise PermissionError("MATCHING_VERIFIED_SERVICE_REQUIRED")
        with self.graph.transaction(tenant):
            rows = self.engine.query("SELECT i.intake_id,i.version FROM intake.intakes i "
                "WHERE i.tenant_id=? AND i.source_id=? AND i.processing_state='MATCHING' AND i.deleted_at IS NULL "
                "AND EXISTS (SELECT 1 FROM intake.parser_runs r JOIN workflow.jobs j "
                "ON j.tenant_id=r.tenant_id AND j.aggregate_id=r.parser_run_id "
                "WHERE r.tenant_id=i.tenant_id AND r.intake_id=i.intake_id AND r.status='SUCCEEDED' "
                "AND j.job_type=? AND j.aggregate_type='parser_run' AND j.status='SUCCEEDED' "
                "AND j.result->>'parser_run_id'=r.parser_run_id::text "
                "AND j.result->>'version'=i.version::text) "
                "AND NOT EXISTS (SELECT 1 FROM identity.match_cases c "
                "WHERE c.tenant_id=i.tenant_id AND c.intake_id=i.intake_id) "
                "ORDER BY i.submitted_at,i.intake_id LIMIT ?", (tenant, source_id, JOB_TYPE, limit))
        for row in rows:
            yield self.match_parser_result(tenant, row["intake_id"], expected_intake_version=row["version"],
                policy_version_id=policy_version_id, boundary=boundary, credentials=credentials)

    def match_parser_result(self, tenant, intake_id, *, expected_intake_version, policy_version_id, boundary, credentials):
        """One authorized, replayable proposal command using the shared matcher.

        Existing deployment admission can retry this short atomic command.
        Matching produces cases only; an exact proposal still needs the
        established identity service, and NEW/REVISION need human confirmation.
        """
        import hashlib

        from modules.listing.application.native_intake_parser_prerequisites import (
            NativeParserCommandBudget,
            NativeParserSQLClock,
        )
        from shared.audit.backlog import CanonicalAuditBacklog

        UUID(tenant)
        UUID(intake_id)
        _version(expected_intake_version)
        if boundary is None:
            raise PermissionError("MATCHING_AUTHENTICATION_BOUNDARY_REQUIRED")
        outcome = boundary.authenticate(credentials)
        if not outcome.authenticated or outcome.token_type != "service" or outcome.principal.tenant_id != tenant:
            raise PermissionError("MATCHING_VERIFIED_SERVICE_REQUIRED")
        principal = replace(outcome.principal, attributes={**outcome.principal.attributes, "token_type": "service"})
        store = self.replay_store
        if store is None or getattr(store, "_engine", None) is not self.engine:
            raise IdentityConflictError("MATCHING_EXISTING_DURABLE_REPLAY_STORE_REQUIRED")
        key = f"{tenant}:{principal.subject_id}:nativeMatchingProposal:{intake_id}:{expected_intake_version}"
        digest = hashlib.sha256(json.dumps({"policy_version_id": policy_version_id,
            "expected_intake_version": expected_intake_version}, sort_keys=True).encode()).hexdigest()
        with self.graph.transaction(tenant):
            store.refresh(tenant)
            with store.transaction():
                clock = NativeParserSQLClock(self.engine)
                before = NativeParserCommandBudget(clock)
                before()
                intake = self.graph._one("SELECT * FROM intake.intakes WHERE tenant_id=? AND intake_id=? FOR UPDATE", (tenant, intake_id))
                policy, owner_scope = self._matching_authority(tenant, intake, principal, policy_version_id, before, clock._clock())
                prior = store.replays.get(key)
                if prior is not None:
                    if prior[0] != digest:
                        raise IdentityConflictError("IDEMPOTENCY_KEY_REUSED")
                    return deepcopy(prior[1])
                if (intake["version"] != expected_intake_version or intake["processing_state"] != "MATCHING"
                        or intake["deleted_at"] or intake["resolved_listing_id"] is not None):
                    raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
                self.graph._no_hold(tenant, "intake", intake_id, held=intake["legal_hold"])
                evaluated = self._evaluate_parser_match(tenant, intake, policy, owner_scope, principal, before=before)
                source, matched, resolved = (evaluated[name] for name in ("source", "matched", "resolved"))
                candidates, confidence = evaluated["candidates"], evaluated["confidence"]
                before()
                CanonicalAuditBacklog(self.engine).require_available(tenant)
                receipt = self.create_case(tenant, intake_id, expected_intake_version=expected_intake_version,
                    actor_id=principal, confidence=confidence, candidates=tuple(candidates), outcome=matched.outcome,
                    supporting_signals=[{"kind": "NATIVE_SHARED_MATCHER_PROPOSAL", "policy_version_id": policy.policy_version_id,
                        "parser_job_id": source["parser_job_id"], "parser_run_id": source["parser_run_id"],
                        "original_input_sha256": source["original_input_sha256"],
                        "normalized_payload_sha256": source["normalized_payload_sha256"],
                        "corpus_versions": list(resolved.values())}],
                    contradictory_signals=[signal.to_dict() for signal in matched.signals if not signal.agrees], reason=matched.summary)
                before()
                store.replays[key] = (digest, receipt, 201)
                store.flush()
                before()
                now = clock._clock()
                if now >= datetime.fromisoformat(source["manual_entry_valid_until"]) or not policy.covers(now):
                    raise IdentityConflictError("MATCHING_SOURCE_OR_PURPOSE_APPROVAL_EXPIRED")
                before.check_in_memory()
                return receipt

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
            decisions = self.engine.query("SELECT match_decision_id FROM identity.match_decisions "
                "WHERE tenant_id=? AND match_case_id=? ORDER BY created_at,match_decision_id LIMIT 101",
                (tenant, case["match_case_id"]))
            if len(decisions) > 100:
                raise IdentityConflictError("CANONICAL_CASE_DECISIONS_TOO_LARGE")
            return {"match_case": case, "intake": intake, "candidates": candidates,
                    "identity_decision_ids": [row["match_decision_id"] for row in decisions]}

    def parser_input_transition(self, tenant, case, intake, *, before):
        """Prove this case's actual MATCHING→NEEDS_REVIEW successor.

        The parser result keeps its original version. A later review must prove
        the intervening transition, audit and event, rather than rewrite that
        result or treat an arbitrary current version as parser output.
        """
        from modules.listing.application.native_intake_retrieval import audited_value_matches

        def reject():
            raise IdentityConflictError("IDENTITY_LISTING_REVIEW_TRANSITION_REQUIRED")

        if (case["tenant_id"] != tenant or intake["tenant_id"] != tenant or case["intake_id"] != intake["intake_id"]
                or case["outcome"] != "POSSIBLE_MATCH" or intake["processing_state"] != "NEEDS_REVIEW"):
            reject()
        before()
        rows = self.engine.query("SELECT * FROM intake.intake_stage_transitions WHERE tenant_id=? "
            "AND intake_id=? AND match_case_id=? AND resulting_version=? LIMIT 2",
            (tenant, intake["intake_id"], case["match_case_id"], intake["version"]))
        if len(rows) != 1:
            reject()
        transition = rows[0]
        actor, service = transition["actor_subject_id"], transition["service_principal"]
        at = transition["occurred_at"]
        if (transition["from_state"] != "MATCHING" or transition["to_state"] != "NEEDS_REVIEW"
                or transition["expected_version"] + 1 != intake["version"]
                or transition["permission"] != "identity.review" or transition["reason_code"] != "POSSIBLE_MATCH"
                or (actor is None) == (service is None) or (actor or service) != case["proposed_by"]
                or transition["correlation_id"] != intake["correlation_id"]
                or transition["job_id"] is not None or transition["source_snapshot_id"] is not None):
            reject()
        before()
        audits = self.engine.query("SELECT audit_event_id FROM audit.audit_events WHERE tenant_id=? "
            "AND resource_type='intake' AND resource_id=? AND action='intake.review_required.v1' "
            "AND occurred_at=? AND result='SUCCEEDED' LIMIT 2", (tenant, intake["intake_id"], at))
        if len(audits) != 1:
            reject()
        before()
        audit = self.graph.audit_sink.writer.get_event(tenant, audits[0]["audit_event_id"])
        previous = {"intake_id": intake["intake_id"], "state": "MATCHING", "version": transition["expected_version"]}
        current = {"intake_id": intake["intake_id"], "state": "NEEDS_REVIEW", "version": intake["version"]}
        if (audit is None or audit["actor_subject_id"] != actor or audit["service_principal"] != service
                or audit["correlation_id"] != intake["correlation_id"] or audit["reason"] != transition["reason_text"]
                or audit["before_value"] != previous or not audited_value_matches(audit["after_value"], current)):
            reject()
        before()
        events = self.engine.query("SELECT * FROM workflow.outbox_events WHERE tenant_id=? "
            "AND aggregate_type='intake' AND aggregate_id=? AND aggregate_version=? "
            "AND event_type='intake.state_changed' LIMIT 2", (tenant, intake["intake_id"], intake["version"]))
        if len(events) != 1:
            reject()
        row = events[0]
        event = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        expected = {"tenant_id": tenant, "event_id": row["event_id"], "event_type": "intake.state_changed",
            "event_version": 1, "aggregate_type": "intake", "aggregate_id": intake["intake_id"],
            "aggregate_version": intake["version"], "partition_key": tenant + ":" + intake["intake_id"],
            "correlation_id": intake["correlation_id"], "actor_ref": case["proposed_by"],
            "producer": "listing_intake_service", "schema_ref": "#/payloads/IntakeStateChangedV1"}
        payload = {"intake_id": intake["intake_id"], "from_state": "MATCHING", "to_state": "NEEDS_REVIEW",
            "transition_id": transition["transition_id"], "reason_code": "POSSIBLE_MATCH",
            "version": intake["version"], "occurred_at": at}
        if (validate_event(event) or any(event.get(k) != v for k, v in expected.items())
                or any(row[k] != expected[k] for k in ("event_version", "partition_key", "correlation_id"))
                or event.get("occurred_at") != at or row["occurred_at"] != at
                or event.get("payload") != payload):
            reject()
        before()
        return transition

    def create_case(
        self, tenant: str, intake_id: str, *, expected_intake_version: int, actor_id: str | Principal,
        confidence: Decimal, candidates: tuple[MatchCandidateEvidence, ...],
        supporting_signals: list[dict], contradictory_signals: list[dict], reason: str,
        outcome: str = "POSSIBLE_MATCH",
        append_to_case: tuple[str, int] | None = None,
    ) -> dict:
        """Create or explicitly extend context through the existing case owner.

        NEW/REVISION remain PROPOSED and leave the Intake awaiting confirmation.
        POSSIBLE_MATCH retains its audited PROPOSED→PENDING_REVIEW and optional
        MATCHING→NEEDS_REVIEW transitions in this serializable transaction.
        The caller must finish its independent authorization/idempotency command
        in this same unit before allowing an HTTP receipt to escape.
        This entry authenticates no service and grants no approval or execution.
        An explicit append preserves all original matching facts, requires a
        pending unplanned case version, and uses the same candidate checks,
        ranked insertion and transaction as creation.
        """
        UUID(intake_id)
        audit_actor = actor_id
        service = isinstance(actor_id, Principal)
        if append_to_case is not None:
            UUID(append_to_case[0])
            _version(append_to_case[1])
            if service or outcome != "POSSIBLE_MATCH":
                raise PermissionError("MANUAL_CONTEXT_HUMAN_REVIEW_REQUIRED")
        if service:
            if (not actor_id.authenticated or actor_id.tenant_id != tenant
                    or actor_id.attributes.get("token_type") != "service"
                    or not isinstance(actor_id.subject_id, str) or not 1 <= len(actor_id.subject_id) <= 256):
                raise PermissionError("MATCH_CASE_VERIFIED_SERVICE_REQUIRED")
            actor_id = actor_id.subject_id
        else:
            UUID(actor_id)
            if outcome == "EXACT_DUPLICATE":
                raise ValueError("Exact duplicate proposals require the verified matching service")
        _version(expected_intake_version)
        numeric_confidence = _confidence(confidence)
        supporting_signals, contradictory_signals, candidates = deepcopy((supporting_signals, contradictory_signals, candidates))
        validate_match_case_outcome(outcome, candidates)
        if not supporting_signals or not reason.strip():
            raise ValueError("Supporting evidence and reason are required")
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
            if outcome != "POSSIBLE_MATCH" and intake["processing_state"] != "MATCHING":
                raise IdentityConflictError("INTAKE_ALREADY_REQUIRES_IDENTITY_REVIEW")
            existing = self.engine.query_one("SELECT * FROM identity.match_cases WHERE tenant_id=? AND intake_id=? FOR UPDATE",
                (tenant, intake_id))
            prior_candidates = []
            if append_to_case is not None:
                from shared.infrastructure.persistence.identity_decisions_sql import (
                    CanonicalIdentityDecisionRepository,
                )

                if existing is None or existing["match_case_id"] != append_to_case[0]:
                    raise IdentityNotFoundError("CANONICAL_MATCH_CASE_NOT_FOUND")
                CanonicalIdentityDecisionRepository(self.graph)._validate_context(
                    tenant, existing, intake, append_to_case[1])
                if self.engine.query_one("SELECT match_decision_id FROM identity.match_decisions "
                        "WHERE tenant_id=? AND match_case_id=? LIMIT 1", (tenant, existing["match_case_id"])):
                    raise IdentityConflictError("MATCH_CASE_PLAN_ALREADY_PROPOSED")
                prior_candidates = self.engine.query("SELECT * FROM identity.match_candidates "
                    "WHERE tenant_id=? AND match_case_id=? ORDER BY rank", (tenant, existing["match_case_id"]))
                if len(prior_candidates) + len(candidates) > 100:
                    raise IdentityConflictError("CANONICAL_CASE_CANDIDATES_TOO_LARGE")
            elif existing is not None:
                raise IdentityConflictError("CANONICAL_MATCH_CASE_ALREADY_EXISTS")
            else:
                self.graph._no_hold(tenant, "intake", intake_id, held=intake["legal_hold"])
            saved = {}
            seen_bindings = {(row["property_id"], row["listing_id"]) for row in prior_candidates}
            rank_offset = max((row["rank"] for row in prior_candidates), default=0)
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
                    if outcome in {"REVISION", "EXACT_DUPLICATE"} and (
                        not intake["source_id"] or listing["source_id"] != intake["source_id"]
                    ):
                        raise IdentityConflictError("MATCH_REVISION_SOURCE_CONFLICT")
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
                binding = (property_row["property_id"], normalized_listing_id)
                if binding in seen_bindings:
                    raise ValueError("Match candidates must have unique canonical identities")
                seen_bindings.add(binding)
                if append_to_case is not None:
                    evidence["manual_context"] = {"case_version": existing["version"] + 1, "added_by": actor_id}
                saved[rank + rank_offset] = (candidate, evidence, _confidence(candidate.confidence))
            case_id = existing["match_case_id"] if append_to_case is not None else str(uuid4())
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            if append_to_case is None:
                self.engine.execute(
                    "INSERT INTO identity.match_cases (match_case_id,tenant_id,intake_id,outcome,status,confidence,"
                    "supporting_signals,contradictory_signals,proposed_by) VALUES (?,?,?,?,'PROPOSED',?,?::jsonb,?::jsonb,?)",
                    (case_id, tenant, intake_id, outcome, numeric_confidence,
                     json.dumps(supporting_signals), json.dumps(contradictory_signals), actor_id),
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
            if append_to_case is not None:
                if self.engine.execute("UPDATE identity.match_cases SET version=version+1,updated_at=? "
                        "WHERE tenant_id=? AND match_case_id=? AND version=? AND status='PENDING_REVIEW'",
                        (at, tenant, case_id, existing["version"])).rowcount != 1:
                    raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
                previous = {key: existing[key] for key in ("match_case_id", "intake_id", "status", "version", "outcome", "confidence", "proposed_by")}
                previous["confidence"] = str(previous["confidence"])
                previous["candidate_ids"] = [row["match_candidate_id"] for row in prior_candidates]
                updated = {**previous, "version": existing["version"] + 1,
                    "candidate_ids": previous["candidate_ids"] + candidate_ids,
                    "manual_context": {"added_candidate_ids": candidate_ids, "confidence": str(numeric_confidence),
                        "supporting_signals": supporting_signals, "contradictory_signals": contradictory_signals}}
                audit_id = self._audit(tenant, intake, actor_id, case_id, previous, updated,
                    "match.manual_context_added.v1", reason, at)
                return {**updated, "candidate_ids": candidate_ids, "audit_event_id": audit_id,
                    "intake_version": intake["version"], "correlation_id": intake["correlation_id"]}
            proposed = {"match_case_id": case_id, "intake_id": intake_id, "status": "PROPOSED", "version": 1,
                        "outcome": outcome, "confidence": float(confidence), "candidate_ids": candidate_ids}
            created_audit_id = self._audit(tenant, intake, audit_actor, case_id, None, proposed, "match.case_created.v1", reason, at)
            if outcome != "POSSIBLE_MATCH":
                return {**proposed, "audit_event_id": created_audit_id,
                        "intake_version": intake["version"], "correlation_id": intake["correlation_id"]}
            if self.engine.execute(
                "UPDATE identity.match_cases SET status='PENDING_REVIEW',version=version+1,updated_at=? "
                "WHERE tenant_id=? AND match_case_id=? AND status='PROPOSED' AND version=1", (at, tenant, case_id),
            ).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            pending = {**proposed, "status": "PENDING_REVIEW", "version": 2}
            audit_id = self._audit(tenant, intake, audit_actor, case_id, proposed, pending, "match.review_required.v1", reason, at)
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
                    "from_state,to_state,actor_subject_id,service_principal,permission,reason_code,reason_text,expected_version,"
                    "resulting_version,match_case_id,correlation_id,occurred_at) "
                    "VALUES (?,?,?,?,'MATCHING','NEEDS_REVIEW',?,?,'identity.review','POSSIBLE_MATCH',?,?,?,?,?,?)",
                    (transition_id, tenant, intake_id, sequence, None if service else actor_id,
                     actor_id if service else None, reason, intake["version"],
                     updated["version"], case_id, intake["correlation_id"], at),
                )
                after = {"intake_id": intake_id, "state": "NEEDS_REVIEW", "version": updated["version"]}
                self._audit(tenant, intake, audit_actor, intake_id, before, after,
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
        actor_fields = {"service_principal": actor.subject_id} if isinstance(actor, Principal) else {"actor_subject_id": actor}
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": resource_type, "resource_id": case_id,
            "action": action, "result": "SUCCEEDED", **actor_fields, "correlation_id": intake["correlation_id"],
            "occurred_at": at, "before_value": before, "after_value": after, "reason": reason,
        }, {key: intake[key] for key in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})
        return audit_id


__all__ = ["CanonicalMatchCaseRepository", "MatchCandidateEvidence", "validate_match_case_outcome"]
