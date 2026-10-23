"""Native identity proposals and independent reviews, before job admission.

The caller authorizes the fresh actor, Intake and every affected resource in
this serializable unit. Approval records a human decision; admission and the
existing executor separately own queue reservation and graph mutation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.domain.events import DomainEvent, validate_event
from shared.infrastructure.persistence.identity_cases_sql import (
    CanonicalMatchCaseRepository,
    _version,
)


def _uuid(value: str) -> str:
    if not isinstance(value, (str, UUID)):
        raise ValueError("An explicit canonical UUID is required")
    return str(UUID(str(value)))


def _versions(values: dict) -> dict[str, int]:
    if not isinstance(values, dict):
        raise ValueError("Explicit native resource versions are required")
    result = {_uuid(key): _version(value) for key, value in values.items()}
    if len(result) != len(values):
        raise ValueError("Resource version identities must be unique")
    return result


def _identities(values: list) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError("Explicit native resource identities are required")
    result = [_uuid(value) for value in values]
    if len(set(result)) != len(result):
        raise ValueError("Resource identities must be unique")
    return result


def canonical_identity_plan(kind: str, raw: dict) -> dict:
    """Whitelist the exact existing executor input; never infer a version."""
    common = {"expected_property_versions", "expected_listing_versions", "expected_edge_versions",
              "candidate_reassignment_plan"}
    specific = {"CREATE": {"expected_intake_version", "input_binding"},
                "REVISE": {"expected_intake_version", "input_binding"},
                "DUPLICATE": {"expected_intake_version", "input_binding"},
                "MERGE": {"source_property_ids", "target_property_id"},
                "SPLIT": {"source_property_id", "partitions"}}
    if kind == "DUPLICATE" and isinstance(raw, dict) and "exact_authority" in raw:
        specific[kind] = specific[kind] | {"exact_authority"}
    if not isinstance(kind, str) or kind not in specific or not isinstance(raw, dict) or set(raw) != common | specific[kind]:
        raise ValueError("A complete supported identity plan is required")
    plan = {name: _versions(raw[name]) for name in common if name != "candidate_reassignment_plan"}
    if kind in {"CREATE", "REVISE", "DUPLICATE"}:
        binding = raw["input_binding"]
        identities = {"parser_job_id", "parser_run_id", "source_snapshot_id"}
        hashes = {"original_input_sha256", "normalized_payload_sha256"}
        if (not isinstance(binding, dict)
                or set(binding) != identities | hashes | {"parser_run_version", "expected_correction_versions"}
                or any(not isinstance(binding[name], str) or len(binding[name]) != 64
                    or any(c not in "0123456789abcdef" for c in binding[name]) for name in hashes)):
            raise ValueError("The observed parser and original input bindings are required")
        corrections = _versions(binding["expected_correction_versions"])
        if (not corrections or raw["candidate_reassignment_plan"] != []
                or (kind == "REVISE" and (len(plan["expected_listing_versions"]) != 1
                    or len(plan["expected_property_versions"]) != 1))
                or (kind == "DUPLICATE" and (len(plan["expected_listing_versions"]) != 1
                    or not plan["expected_property_versions"]
                    or ("exact_authority" not in raw and len(plan["expected_property_versions"]) != 1)))):
            raise ValueError("The routine plan must bind the exact case resources and field proposals")
        exact = {}
        if kind == "DUPLICATE" and "exact_authority" in raw:
            authority = raw["exact_authority"]
            if (not isinstance(authority, dict) or set(authority) != {"policy_version_id", "identity_policy_sha256", "proposal_audit_event_id"}
                    or not isinstance(authority["policy_version_id"], str) or not authority["policy_version_id"].strip()
                    or not isinstance(authority["identity_policy_sha256"], str) or len(authority["identity_policy_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in authority["identity_policy_sha256"])):
                raise ValueError("Explicit current service and matching authority bindings are required")
            exact["exact_authority"] = {**authority, "proposal_audit_event_id": _uuid(authority["proposal_audit_event_id"])}
        return {**plan, **exact, "candidate_reassignment_plan": [], "expected_intake_version": _version(raw["expected_intake_version"]),
            "input_binding": {**{name: _uuid(binding[name]) for name in identities},
                **{name: binding[name] for name in hashes}, "parser_run_version": _version(binding["parser_run_version"]),
                "expected_correction_versions": corrections}}
    if kind == "MERGE":
        sources, target = _identities(raw["source_property_ids"]), _uuid(raw["target_property_id"])
        if target in sources:
            raise ValueError("An identity cannot be merged into itself")
        targets = {target}
        plan.update(source_property_ids=sources, target_property_id=target)
    else:
        sources = [_uuid(raw["source_property_id"])]
        if not isinstance(raw["partitions"], list) or len(raw["partitions"]) < 2:
            raise ValueError("At least two distinct split targets are required")
        partitions, targets, assigned = [], set(), set()
        for part in raw["partitions"]:
            if not isinstance(part, dict) or set(part) != {"target_property_id", "source_identity_edge_ids"}:
                raise ValueError("Explicit split partitions are required")
            target, edges = _uuid(part["target_property_id"]), _identities(part["source_identity_edge_ids"])
            if target in targets or target in sources or assigned.intersection(edges):
                raise ValueError("Split partitions require distinct targets and disjoint source edges")
            targets.add(target)
            assigned.update(edges)
            partitions.append({"target_property_id": target, "source_identity_edge_ids": edges})
        if assigned != set(plan["expected_edge_versions"]):
            raise ValueError("The split must explicitly assign every selected source edge")
        plan.update(source_property_id=sources[0], partitions=partitions)
    if set(plan["expected_property_versions"]) != set(sources) | targets:
        raise ValueError("The plan must bind every affected property version exactly")
    if not isinstance(raw["candidate_reassignment_plan"], list):
        raise ValueError("Explicit candidate dispositions are required")
    candidates, seen = [], set()
    for item in raw["candidate_reassignment_plan"]:
        if (not isinstance(item, dict) or not {"candidate_site_id", "expected_version", "disposition"} <= set(item)
                or set(item) - {"candidate_site_id", "expected_version", "disposition", "target_property_id"}):
            raise ValueError("Candidate identity, version and disposition are required")
        key = _uuid(item["candidate_site_id"])
        disposition = item["disposition"]
        target = _uuid(item["target_property_id"]) if item.get("target_property_id") is not None else None
        if (key in seen or not isinstance(disposition, str) or disposition not in {"REASSIGN", "KEEP_HISTORICAL", "REQUIRE_REVIEW"}
                or (target not in targets if disposition == "REASSIGN" else target is not None)):
            raise ValueError("The candidate disposition must name an eligible target exactly once")
        seen.add(key)
        candidates.append({"candidate_site_id": key, "expected_version": _version(item["expected_version"]),
                           "disposition": disposition, "target_property_id": target})
    plan["candidate_reassignment_plan"] = candidates
    return plan


class CanonicalIdentityDecisionRepository:
    def __init__(self, graph: Any) -> None:
        self.graph, self.engine = graph, graph.engine

    def _audit(self, tenant, intake, actor, key, before, after, action, reason, at, *, audit_id, service_principal=None):
        if service_principal is not None and (actor is not None or service_principal != "native-intake-identity"):
            raise IdentityConflictError("IDENTITY_SERVICE_AUDIT_ACTOR_CONFLICT")
        self.graph.audit_sink.persist({
            "audit_event_id": audit_id, "tenant_id": tenant, "resource_type": "identity_decision", "resource_id": key,
            "action": action, "result": "SUCCEEDED", "actor_subject_id": actor,
            "correlation_id": intake["correlation_id"], "occurred_at": at,
            "before_value": before, "after_value": after, "reason": reason,
            **({"service_principal": service_principal, "actor_role": "SVC_INTAKE"} if service_principal else {}),
        }, {name: intake[name] for name in ("tenant_id", "brand_id", "region_id", "assigned_area_id", "heat_zone_id")})

    def get(self, tenant: str, decision_id: str) -> dict:
        with self.graph.transaction(tenant):
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
                (tenant, _uuid(decision_id)))
            case, intake = self.graph._case(tenant, decision["match_case_id"])
            return {"decision": decision, "match_case": case, "intake": intake}

    def require_approval(self, tenant, decision, *, error_code="IDENTITY_DECISION_NOT_APPROVED"):
        """Check recorded approval at each existing execution trust boundary.

        Routine confirmation is an actual human decision, with its immutable
        approval audit. MERGE/SPLIT keep their independent reviewer. This is
        recorded evidence only; positive execution separately rechecks current
        roles, source authority, input and resource versions in _snapshot.
        """
        service = decision.get("proposer_service_principal") is not None
        if service and decision["decision_type"] != "DUPLICATE":
            raise IdentityConflictError(error_code)
        if ((decision["risk_acknowledged"] if service else not decision["risk_acknowledged"])
                or not decision["reviewed_at"]):
            raise IdentityConflictError(error_code)
        if decision["decision_type"] not in {"CREATE", "REVISE", "DUPLICATE"}:
            if (decision["decision_type"] not in {"MERGE", "SPLIT"} or not decision["reviewer_subject_id"]
                    or decision["reviewer_subject_id"] == decision["proposer_subject_id"]):
                raise IdentityConflictError(error_code)
            return
        if decision["decision_type"] == "DUPLICATE" and not service:
            try:
                review = decision["after_graph"]["review"]
                plan = decision["after_graph"]["plan"]
                audit = self.graph.audit_sink.writer.get_event(tenant, review["audit_event_id"])
                reviewer, proposer = decision["reviewer_subject_id"], decision["proposer_subject_id"]
                if (not proposer or not reviewer or reviewer == proposer or "exact_authority" in plan
                        or review["reviewer_subject_id"] != reviewer or review["status"] != "APPROVED"
                        or review["risk_acknowledged"] is not True
                        or datetime.fromisoformat(review["reviewed_at"]) != datetime.fromisoformat(decision["reviewed_at"])
                        or audit is None or audit["actor_subject_id"] != reviewer or audit["service_principal"] is not None
                        or audit["action"] != "identity.decision_approved.v1" or audit["result"] != "SUCCEEDED"
                        or audit["resource_id"] != decision["match_decision_id"] or audit["reason"] != review["reason"]
                        or audit["before_value"]["plan"] != plan or audit["before_value"]["status"] != "PENDING_REVIEW"
                        or audit["after_value"]["reviewer_subject_id"] != reviewer
                        or audit["after_value"]["decision_id"] != decision["match_decision_id"]
                        or audit["after_value"]["status"] != "APPROVED"
                        or audit["after_value"]["risk_acknowledged"] is not True):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError) as error:
                raise IdentityConflictError("IDENTITY_CONFIRMATION_PROOF_REQUIRED") from error
            return
        try:
            confirmation = decision["after_graph"]["confirmation"]
            audit = self.graph.audit_sink.writer.get_event(tenant, confirmation["audit_event_id"])
            if service:
                actor_valid = ("exact_authority" in decision["after_graph"]["plan"]
                    and decision["proposer_subject_id"] is None
                    and decision.get("proposer_service_principal") == "native-intake-identity"
                    and confirmation.get("confirmed_service_principal") == "native-intake-identity"
                    and confirmation.get("service_subject_id") == "service:native-intake-identity"
                    and audit is not None and audit["actor_subject_id"] is None
                    and audit["service_principal"] == "native-intake-identity" and audit["actor_role"] == "SVC_INTAKE")
            else:
                actor_valid = (confirmation["confirmed_by"] == decision["proposer_subject_id"]
                    and audit is not None and audit["actor_subject_id"] == decision["proposer_subject_id"])
            if (decision["reviewer_subject_id"] is not None
                    or not actor_valid
                    or datetime.fromisoformat(confirmation["confirmed_at"]) != datetime.fromisoformat(decision["reviewed_at"])
                    or audit is None or audit["action"] != "identity.decision_approved.v1"
                    or audit["resource_id"] != decision["match_decision_id"]
                    or audit["result"] != "SUCCEEDED"
                    or audit["before_value"]["plan"] != decision["after_graph"]["plan"]
                    or audit["after_value"]["decision_id"] != decision["match_decision_id"]
                    or audit["after_value"]["status"] != "APPROVED"):
                raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise IdentityConflictError("IDENTITY_CONFIRMATION_PROOF_REQUIRED") from error

    def _validate_context(self, tenant, case, intake, expected_version, *, kind=None, exact_service=False):
        if case["version"] != _version(expected_version):
            raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
        if kind in {"CREATE", "REVISE"} or (kind == "DUPLICATE" and exact_service):
            valid = (case["status"] == "PROPOSED"
                and case["outcome"] == {"CREATE": "NEW", "REVISE": "REVISION", "DUPLICATE": "EXACT_DUPLICATE"}[kind]
                and intake["processing_state"] == "MATCHING")
            if kind in {"CREATE", "REVISE"}:
                valid = valid or (case["status"] == "PENDING_REVIEW" and case["outcome"] == "POSSIBLE_MATCH"
                    and intake["processing_state"] == "NEEDS_REVIEW")
        elif kind == "DUPLICATE":
            valid = (case["status"] == "PENDING_REVIEW" and case["outcome"] == "POSSIBLE_MATCH"
                and intake["processing_state"] == "NEEDS_REVIEW")
        else:
            valid = (case["status"] == "PENDING_REVIEW"
                and case["outcome"] in {"POSSIBLE_MATCH", "QUARANTINED"}
                and intake["processing_state"] == "NEEDS_REVIEW")
        if (not valid and not exact_service and kind in {"CREATE", "REVISE", "DUPLICATE"}
                and case["status"] == "EXECUTED" and case["outcome"] == "POSSIBLE_MATCH"
                and intake["processing_state"] == "NEEDS_REVIEW" and intake["resolved_listing_id"] is None):
            self._completed_graph(tenant, case, intake)
            valid = True
        if not valid or intake["deleted_at"]:
            raise IdentityConflictError("IDENTITY_CASE_NOT_AWAITING_REVIEW")
        self.graph._no_hold(tenant, "intake", intake["intake_id"], held=intake["legal_hold"])
        return case, intake

    def _completed_graph(self, tenant, case, intake):
        """Prove the current case version finished graph repair, not ingestion.

        Keep that decision terminal. Only a fresh human confirmation may consume
        the unchanged original parser proposals through the existing executor.
        """
        from shared.jobs.queue import JobStatus

        def reject():
            raise IdentityConflictError("IDENTITY_GRAPH_CONTINUATION_PROOF_REQUIRED")

        rows = self.engine.query("SELECT * FROM workflow.outbox_events WHERE tenant_id=? "
            "AND aggregate_type='match_case' AND aggregate_id=? AND aggregate_version=? "
            "AND event_type='match.decided' LIMIT 2", (tenant, case["match_case_id"], case["version"]))
        if len(rows) != 1:
            reject()
        row = rows[0]
        try:
            event = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? "
                "AND match_decision_id=? FOR UPDATE", (tenant, _uuid(event["payload"]["match_decision_id"])))
            expected = {"tenant_id": tenant, "event_id": row["event_id"], "event_type": "match.decided",
                "event_version": 1, "aggregate_type": "match_case", "aggregate_id": case["match_case_id"],
                "aggregate_version": case["version"], "partition_key": tenant + ":" + case["match_case_id"],
                "correlation_id": intake["correlation_id"], "actor_ref": None,
                "producer": "identity_resolution_service", "schema_ref": "#/payloads/MatchDecidedV1"}
            payload = {"match_decision_id": decision["match_decision_id"], "match_case_id": case["match_case_id"],
                "decision_type": decision["decision_type"], "status": "EXECUTED",
                "proposer_subject_id": decision["proposer_subject_id"], "reviewer_subject_id": decision["reviewer_subject_id"]}
            if (decision["match_case_id"] != case["match_case_id"] or decision["decision_type"] not in {"MERGE", "SPLIT"}
                    or decision.get("proposer_service_principal") is not None or validate_event(event)
                    or any(event.get(k) != v for k, v in expected.items()) or event["payload"] != payload
                    or any(row[k] != expected[k] for k in ("event_version", "partition_key", "correlation_id"))
                    or event["occurred_at"] != decision["executed_at"] or row["occurred_at"] != decision["executed_at"]):
                reject()
            self.require_approval(tenant, decision)
            job = self.graph._one("SELECT * FROM workflow.jobs WHERE tenant_id=? AND job_type=? "
                "AND aggregate_type='identity_decision' AND aggregate_id=? FOR UPDATE",
                (tenant, self.graph.job_type, decision["match_decision_id"]))
            result = self.graph.execution_receipt(tenant, job, decision)
            delivery = self.graph.queue.get(job["job_id"])
            if (job["payload"]["match_case_version"] + 1 != case["version"]
                    or decision["after_graph"]["intake_version"] != intake["version"]
                    or job["correlation_id"] != intake["correlation_id"]
                    or delivery is None or delivery.status != JobStatus.SUCCEEDED
                    or delivery.job_type != self.graph.job_type
                    or delivery.fence_token != job["payload"]["delivery_fence"]):
                reject()
        except (KeyError, TypeError, ValueError, AttributeError, IdentityNotFoundError) as error:
            raise IdentityConflictError("IDENTITY_GRAPH_CONTINUATION_PROOF_REQUIRED") from error
        return result

    def _context(self, tenant: str, case_id: str, expected_version: int) -> tuple[dict, dict]:
        case, intake = self.graph._case(tenant, _uuid(case_id))
        return self._validate_context(tenant, case, intake, expected_version)

    def _listing_input_context(self, tenant, intake, *, before=None, require_proposed=False, review_case=None):
        """Read proven proposals for the caller's authorized, masked preview.

        A later confirmation/execution must repeat these existing readers.
        These references neither approve the original field proposals nor
        turn historical parser success into a present source or account grant.
        """
        from modules.listing.application.native_intake_assisted_entry_policy import (
            NativeAssistedEntryConflict,
            NativeAssistedEntryUnavailable,
        )
        from modules.listing.application.native_intake_parser_history import (
            NativeParserTerminalHistory,
        )
        from modules.listing.application.native_intake_parser_input import (
            NativeParserManualInputReader,
        )
        from modules.listing.application.native_intake_parser_job_proof import (
            parser_delivery_identity,
        )
        from modules.listing.application.native_intake_parser_policy import (
            JOB_TYPE,
            NativeParserConflict,
            NativeParserUnavailable,
        )
        from modules.listing.application.native_intake_parser_prerequisites import (
            NativeParserCommandBudget,
            NativeParserSQLClock,
        )
        from modules.listing.application.native_intake_requester_authority import (
            NativeIntakeRequesterAuthorityUnavailable,
        )
        from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
        from modules.listing.application.native_intake_source_policy import (
            NativeIntakeSourcePolicyConflict,
            NativeIntakeSourcePolicyUnavailable,
        )

        clock = NativeParserSQLClock(self.engine)
        before = before if before is not None else NativeParserCommandBudget(clock)
        try:
            parser_version = intake["version"]
            review_transition = None
            if review_case is not None:
                review_transition = CanonicalMatchCaseRepository(self.graph).parser_input_transition(
                    tenant, review_case, intake, before=before)
                parser_version = review_transition["expected_version"]
            before()
            jobs = self.engine.query(
                "SELECT j.job_id FROM workflow.jobs j JOIN intake.parser_runs r "
                "ON r.tenant_id=j.tenant_id AND r.parser_run_id=j.aggregate_id "
                "WHERE j.tenant_id=? AND r.intake_id=? AND j.job_type=? "
                "AND j.aggregate_type='parser_run' AND j.status='SUCCEEDED' AND r.status='SUCCEEDED' "
                "AND j.result->>'version'=? LIMIT 2", (tenant, intake["intake_id"], JOB_TYPE, str(parser_version)))
            before.check_in_memory()
            if len(jobs) != 1:
                raise IdentityConflictError("IDENTITY_LISTING_CURRENT_PARSER_RESULT_REQUIRED")
            before()
            delivery = self.graph.queue.get(jobs[0]["job_id"])
            before.check_in_memory()
            if delivery is None:
                raise IdentityConflictError("IDENTITY_LISTING_CURRENT_PARSER_RESULT_REQUIRED")
            delivery_tenant, generation = parser_delivery_identity(delivery)
            if delivery_tenant != tenant:
                raise IdentityConflictError("IDENTITY_LISTING_PARSER_TENANT_CONFLICT")
            result, original = NativeParserTerminalHistory(self.graph).read_positive_with_parent_input(
                delivery, tenant, generation, before=before)
            if (result["intake_id"] != intake["intake_id"] or result["version"] != parser_version
                    or result["processing_state"] != "MATCHING" or result["parser_status"] != "SUCCEEDED"):
                raise IdentityConflictError("IDENTITY_LISTING_CURRENT_PARSER_RESULT_REQUIRED")
            if review_transition is not None and (
                    review_transition["sequence_no"] != result["transition_sequence"] + 1
                    or datetime.fromisoformat(review_transition["occurred_at"]) < datetime.fromisoformat(result["settled_at"])):
                raise IdentityConflictError("IDENTITY_LISTING_REVIEW_TRANSITION_REQUIRED")
            _, _, classified = NativeParserManualInputReader(self.graph).current_authority(intake, original, before=before)
            before()
            run = self.graph._one("SELECT * FROM intake.parser_runs WHERE tenant_id=? AND parser_run_id=? FOR UPDATE",
                (tenant, result["parser_run_id"]))
            normalized = json.loads(run["normalized_payload"]) if isinstance(run["normalized_payload"], str) else run["normalized_payload"]
            parsed = json.loads(run["parsed_payload"]) if isinstance(run["parsed_payload"], str) else run["parsed_payload"]
            before()
            corrections = self.engine.query(
                "SELECT correction_id,field_path,field_classification,status,version,identity_affecting,proposed_by,"
                "corrected_value::text AS corrected_json FROM intake.human_corrections "
                "WHERE tenant_id=? AND intake_id=? AND correction_id=ANY(?::uuid[]) ORDER BY field_path",
                (tenant, intake["intake_id"], [identity for _, identity in original.lineage.correction_ids]))
            # The full history reader above proved these exact rows and their
            # immutable values in this same serializable snapshot. Their live
            # status/version remain visible; parser success never applies them.
            fields = [{"correction_id": row["correction_id"], "field_path": row["field_path"],
                "classification": row["field_classification"], "status": row["status"], "version": row["version"],
                "proposed_by": row["proposed_by"],
                "identity_affecting": row["identity_affecting"], "parsed": parsed.get(row["field_path"]),
                "normalized": normalized.get(row["field_path"]), "corrected": json.loads(row["corrected_json"])}
                for row in corrections]
            if require_proposed and any(row["status"] != "PROPOSED" for row in fields):
                raise IdentityConflictError("IDENTITY_LISTING_INPUT_VERSION_CONFLICT")
            reference = {"parser_job_id": delivery.job_id, "parser_run_id": run["parser_run_id"],
                "parser_run_version": run["version"], "source_snapshot_id": run["source_snapshot_id"],
                "original_input_sha256": original.sha256, "normalized_payload_sha256": canonical_sha256(normalized),
                "proposed_correction_ids": [identity for _, identity in original.lineage.correction_ids],
                "manual_entry_valid_until": classified.valid_until.isoformat(), "fields": fields}
            before()
            if clock._clock() >= classified.valid_until:
                raise IdentityConflictError("IDENTITY_LISTING_SOURCE_INPUT_EXPIRED")
            before.check_in_memory()
            return reference
        except (NativeParserConflict, NativeAssistedEntryConflict, NativeIntakeSourcePolicyConflict) as error:
            raise IdentityConflictError(str(error)) from error
        except (NativeParserUnavailable, NativeAssistedEntryUnavailable,
                NativeIntakeRequesterAuthorityUnavailable, NativeIntakeSourcePolicyUnavailable, PermissionError) as error:
            raise IdentityConflictError("IDENTITY_LISTING_CURRENT_INPUT_UNAVAILABLE") from error

    def planning_context(self, tenant: str, case_id: str, *, expected_case_version: int,
                         include_listing_input: bool = False) -> dict:
        """Read a complete bounded native closure; the caller authorizes every row.

        The surrounding route holds this same serializable transaction while
        authorizing/projecting the rows. No plan, disposition or eligibility is
        inferred, and no missing version is synthesized from matching evidence.
        """
        if type(include_listing_input) is not bool:
            raise ValueError("An explicit listing input projection is required")
        with self.graph.transaction(tenant):
            case, intake = self.graph._case(tenant, _uuid(case_id))
            kind = {"NEW": "CREATE", "REVISION": "REVISE"}.get(case["outcome"])
            self._validate_context(tenant, case, intake, expected_case_version,
                kind="CREATE" if include_listing_input and case["outcome"] == "POSSIBLE_MATCH" else kind)
            listing_input = self._listing_input_context(tenant, intake,
                review_case=case if kind is None else None) if kind or include_listing_input else None
            anchors = self.engine.query(
                "SELECT match_candidate_id,property_id,listing_id FROM identity.match_candidates "
                "WHERE tenant_id=? AND match_case_id=? ORDER BY rank,match_candidate_id LIMIT 101",
                (tenant, case["match_case_id"]),
            )
            if len(anchors) > 100:
                raise IdentityConflictError("IDENTITY_PLANNING_CONTEXT_TOO_LARGE")
            if kind == "CREATE":
                if anchors:
                    raise IdentityConflictError("IDENTITY_LISTING_CASE_SHAPE_CONFLICT")
                return {"match_case": case, "intake": intake, "anchors": [], "properties": [], "listings": [],
                        "source_identity_edges": [], "candidate_sites": [], "anchor_listings": [],
                        "listing_input": listing_input}
            if kind == "REVISE" and (len(anchors) != 1 or anchors[0]["listing_id"] is None):
                raise IdentityConflictError("IDENTITY_LISTING_CASE_SHAPE_CONFLICT")
            if not anchors:
                raise IdentityConflictError("IDENTITY_PLANNING_ANCHORS_REQUIRED")
            bindings, anchor_listings = [], {}
            for row in anchors:
                property_id = row["property_id"]
                listing_version = None
                if row["listing_id"] is not None:
                    listing = self.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=?",
                        (tenant, row["listing_id"]))
                    anchor_listings[listing["listing_id"]] = listing
                    if kind == "REVISE" and (not intake["source_id"] or listing["source_id"] != intake["source_id"]):
                        raise IdentityConflictError("MATCH_REVISION_SOURCE_CONFLICT")
                    resolved = self.graph.resolve_listing(tenant, row["listing_id"])
                    property_id, listing_version = resolved["effective_property_id"], resolved["listing_version"]
                if property_id is None:
                    raise IdentityConflictError("MATCH_CANDIDATE_IDENTITY_UNAVAILABLE")
                if row["listing_id"] is None:
                    property_id = self.graph.resolve_properties(tenant, [property_id])[property_id]["effective_property_id"]
                bindings.append({"match_candidate_id": row["match_candidate_id"],
                    "effective_property_id": _uuid(property_id), "listing_id": row["listing_id"],
                    "listing_version": listing_version})
            property_ids = sorted({row["effective_property_id"] for row in bindings})
            properties = self.engine.query(
                "SELECT * FROM identity.properties WHERE tenant_id=? "
                "AND property_id=ANY(?::uuid[]) ORDER BY property_id LIMIT 101", (tenant, property_ids),
            )
            if {row["property_id"] for row in properties} != set(property_ids):
                raise IdentityNotFoundError("CANONICAL_IDENTITY_RESOURCE_NOT_FOUND")
            # Identical closure predicate to _resources: immutable Listing
            # parents and current source edges both bind actual Listing versions.
            listings = self.engine.query(
                "SELECT * FROM expansion.listings WHERE tenant_id=? AND (property_id=ANY(?::uuid[]) "
                "OR listing_id IN (SELECT listing_id FROM identity.source_identity_edges WHERE tenant_id=? "
                "AND property_id=ANY(?::uuid[]) AND effective_to IS NULL)) "
                "ORDER BY listing_id LIMIT 1001", (tenant, property_ids, tenant, property_ids),
            )
            edges = self.engine.query(
                "SELECT edge_id,property_id,listing_id,edge_version FROM identity.source_identity_edges "
                "WHERE tenant_id=? AND property_id=ANY(?::uuid[]) AND effective_to IS NULL "
                "ORDER BY edge_id LIMIT 1001", (tenant, property_ids),
            )
            candidates = self.engine.query(
                "SELECT * FROM expansion.candidate_sites WHERE tenant_id=? "
                "AND property_id=ANY(?::uuid[]) ORDER BY candidate_site_id LIMIT 1001", (tenant, property_ids),
            )
            if any(len(rows) > 1000 for rows in (listings, edges, candidates)):
                raise IdentityConflictError("IDENTITY_PLANNING_CONTEXT_TOO_LARGE")
            listing_ids = {row["listing_id"] for row in listings}
            if any(row["listing_id"] is not None and row["listing_id"] not in listing_ids
                   for row in edges):
                raise IdentityConflictError("IDENTITY_PLANNING_CONTEXT_INCOMPLETE")
            return {"match_case": case, "intake": intake, "anchors": bindings,
                    "properties": properties, "listings": listings, "source_identity_edges": edges,
                    "candidate_sites": candidates, "anchor_listings": list(anchor_listings.values()),
                    **({"listing_input": listing_input} if listing_input is not None else {})}

    def _snapshot(self, tenant: str, case: dict, intake: dict, kind: str, plan: dict,
                  *, confirmation_actor_id=None, reviewer_actor_id=None, reason=None,
                  include_listing_values=False, before=None) -> dict:
        if kind == "DUPLICATE" and "exact_authority" in plan:
            from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner

            return NativeIntakeIdentityOwner(self.graph).exact_snapshot(tenant, case, intake,
                policy_version_id=plan["exact_authority"]["policy_version_id"], plan=plan, before=before)
        if kind in {"CREATE", "REVISE", "DUPLICATE"}:
            return self._listing_confirmation_snapshot(tenant, case, intake, kind, plan,
                actor_id=confirmation_actor_id, reviewer_id=reviewer_actor_id,
                reason=reason, include_values=include_listing_values)
        properties = self.graph._resources(tenant, plan, intake)
        sources = plan["source_property_ids"] if kind == "MERGE" else [plan["source_property_id"]]
        edges = self.graph._edges(tenant, sources, plan["expected_edge_versions"])
        anchors = self.engine.query("SELECT property_id,listing_id FROM identity.match_candidates "
            "WHERE tenant_id=? AND match_case_id=? ORDER BY rank", (tenant, case["match_case_id"]))
        anchored = set()
        for row in anchors:
            if row["listing_id"]:
                anchored.add(self.graph.resolve_listing(tenant, row["listing_id"])["effective_property_id"])
            elif row["property_id"]:
                anchored.add(self.graph.resolve_properties(tenant, [row["property_id"]])[row["property_id"]]["effective_property_id"])
        if not set(properties) <= anchored:
            raise IdentityConflictError("IDENTITY_PLAN_OUTSIDE_MATCH_CASE")
        candidates = self.engine.query("SELECT * FROM expansion.candidate_sites WHERE tenant_id=? "
            "AND property_id=ANY(?::uuid[]) ORDER BY candidate_site_id FOR UPDATE", (tenant, sources))
        dispositions = {row["candidate_site_id"]: row for row in plan["candidate_reassignment_plan"]}
        if {row["candidate_site_id"] for row in candidates} != set(dispositions):
            raise IdentityConflictError("CANDIDATE_DISPOSITION_REQUIRED")
        reassigned = set()
        for row in candidates:
            item = dispositions[row["candidate_site_id"]]
            if row["version"] != item["expected_version"]:
                raise IdentityConflictError("CANDIDATE_VERSION_CONFLICT")
            self.graph._no_hold(tenant, "candidate_site", row["candidate_site_id"])
            if item["disposition"] != "REASSIGN" or row["status"] in {"REJECTED", "OPENED"}:
                continue
            binding = (item["target_property_id"], row["target_format_code"])
            if binding in reassigned or self.engine.query_one("SELECT candidate_site_id FROM expansion.candidate_sites "
                "WHERE tenant_id=? AND property_id=? AND target_format_code=? AND status NOT IN ('REJECTED','OPENED') "
                "AND candidate_site_id<>? FOR UPDATE", (tenant, *binding, row["candidate_site_id"])):
                raise IdentityConflictError("CANDIDATE_PLAN_CONFLICT")
            reassigned.add(binding)
        listings = self.engine.query("SELECT * FROM expansion.listings WHERE tenant_id=? "
            "AND listing_id=ANY(?::uuid[]) ORDER BY listing_id", (tenant, list(plan["expected_listing_versions"])))
        return {"properties": list(properties.values()), "listings": listings, "source_edges": edges,
                "candidates": candidates, "match_case_version": case["version"], "intake_version": intake["version"]}

    def confirmation_principal(self, case, intake, *, kind, actor_id, reason, before=None,
                               proposer_id=None, approve=True):
        """The same current SQL confirmation permission also protects replays."""
        from modules.listing.application.native_intake_assisted_entry_request import (
            human_transition_role,
        )
        from modules.listing.application.native_intake_parser_prerequisites import (
            NativeParserCommandBudget,
            NativeParserSQLClock,
        )
        from modules.listing.application.native_intake_requester_authority import (
            current_native_identity_principal,
        )
        from modules.listing.domain.intake_states import (
            Actor,
            DenialCode,
            DomainValidationError,
            IdentityDecisionAggregate,
            IdentityDecisionStateMachine,
            IdentityGraphState,
            PrincipalRole,
            TransitionContext,
        )

        if kind not in {"CREATE", "REVISE", "DUPLICATE"} or type(approve) is not bool:
            raise ValueError("An explicit human identity decision is required")
        actor_id = _uuid(actor_id)
        before = before if before is not None else NativeParserCommandBudget(NativeParserSQLClock(self.engine))
        principal = current_native_identity_principal(self.engine, actor_id, intake,
            action="decide" if approve else "reject_identity", risk_summary=reason,
            risk_acknowledged=approve, before=before)
        if kind == "DUPLICATE" and proposer_id is None:
            return principal  # A proposal records no human approval.
        role = human_transition_role(principal)
        if not approve:
            return principal
        if kind == "DUPLICATE" and role != PrincipalRole.EXPANSION_MANAGER:
            raise DomainValidationError(DenialCode.ROLE_DENIED, "Ordinary identity approval requires a manager.")
        aggregate = IdentityDecisionAggregate(case["match_case_id"], intake["tenant_id"],
            IdentityGraphState.PENDING_REVIEW if kind == "DUPLICATE" else IdentityGraphState.PROPOSED,
            1, proposer_id=_uuid(proposer_id) if proposer_id is not None else actor_id, decision_type=kind)
        IdentityDecisionStateMachine.transition(aggregate, IdentityGraphState.APPROVED,
            TransitionContext(actor=Actor(actor_id, role, intake["tenant_id"]), reason=reason, risk_acknowledged=True))
        before.check_in_memory()
        return principal

    def _listing_confirmation_snapshot(self, tenant, case, intake, kind, plan, *, actor_id, reviewer_id,
                                       reason, include_values):
        """Prove the human's exact observed proposals; never apply their values."""
        from modules.listing.application.intake_authorization import (
            authorize_intake_action,
            mask_intake,
        )
        from modules.listing.application.intake_correction_lineage import effective_value
        from modules.listing.application.native_intake_parser_prerequisites import (
            NativeParserCommandBudget,
            NativeParserSQLClock,
        )

        actor_id = _uuid(actor_id)
        review = case["outcome"] == "POSSIBLE_MATCH"
        if (plan["expected_intake_version"] != intake["version"]
                or intake["processing_state"] != ("NEEDS_REVIEW" if review else "MATCHING")
                or intake["deleted_at"] or intake["resolved_listing_id"] is not None
                or (kind == "DUPLICATE" and not review)
                or (not review and case["outcome"] != {"CREATE": "NEW", "REVISE": "REVISION"}[kind])):
            raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
        self.graph._no_hold(tenant, "intake", intake["intake_id"], held=intake["legal_hold"])
        clock = NativeParserSQLClock(self.engine)
        before = NativeParserCommandBudget(clock)
        current = self._listing_input_context(tenant, intake, before=before, require_proposed=True,
            review_case=case if review else None)
        binding = plan["input_binding"]
        if (any(binding[name] != current[name] for name in binding if name != "expected_correction_versions")
                or binding["expected_correction_versions"] != {row["correction_id"]: row["version"] for row in current["fields"]}):
            raise IdentityConflictError("IDENTITY_LISTING_INPUT_VERSION_CONFLICT")
        principal = self.confirmation_principal(case, intake, kind=kind, actor_id=actor_id, reason=reason, before=before)
        principals = [principal]
        if kind == "DUPLICATE" and reviewer_id is not None:
            principals.append(self.confirmation_principal(case, intake, kind=kind, actor_id=reviewer_id,
                proposer_id=actor_id, reason=reason, before=before))
        for observer in principals:
            if any(row.get("masked") for row in mask_intake(observer, {"fields": current["fields"]})["fields"]):
                raise PermissionError("FIELD_MASKED")
        if kind != "DUPLICATE" and any(row["identity_affecting"] and row["proposed_by"] == actor_id for row in current["fields"]):
            raise IdentityConflictError("SECOND_ACTOR_REQUIRED")
        before()
        anchors = self.engine.query("SELECT property_id,listing_id FROM identity.match_candidates "
            "WHERE tenant_id=? AND match_case_id=? ORDER BY rank", (tenant, case["match_case_id"]))
        resources = {"properties": [], "listings": [], "source_edges": []}
        if kind == "CREATE":
            if not review and (anchors or any(plan[name] for name in (
                    "expected_property_versions", "expected_listing_versions", "expected_edge_versions"))):
                raise IdentityConflictError("IDENTITY_LISTING_CASE_SHAPE_CONFLICT")
            if review:
                # The human rejects these observed matches, not their versions.
                # Bind the same complete graph closure exposed by planning_context.
                anchored = set()
                for anchor in anchors:
                    before()
                    anchored.add(self.graph.resolve_listing(tenant, anchor["listing_id"])["effective_property_id"]
                        if anchor["listing_id"] else self.graph.resolve_properties(
                            tenant, [anchor["property_id"]])[anchor["property_id"]]["effective_property_id"])
                if not anchored or anchored != set(plan["expected_property_versions"]):
                    raise IdentityConflictError("PROPERTY_VERSION_SET_CONFLICT")
                before()
                properties = self.graph._resources(tenant, plan, intake)
                before()
                edges = self.graph._edges(tenant, sorted(properties), plan["expected_edge_versions"])
                before()
                listings = self.engine.query("SELECT * FROM expansion.listings WHERE tenant_id=? "
                    "AND listing_id=ANY(?::uuid[]) ORDER BY listing_id", (tenant, list(plan["expected_listing_versions"])))
                for resource in (*properties.values(), *listings):
                    before()
                    authorize_intake_action(principal, "view", resource=resource)
                resources = {"properties": list(properties.values()), "listings": listings, "source_edges": edges}
        else:
            selected = [anchor for anchor in anchors if anchor["listing_id"] in plan["expected_listing_versions"]]
            if (len(selected) != 1 or (not review and len(anchors) != 1)):
                raise IdentityConflictError("IDENTITY_LISTING_CASE_SHAPE_CONFLICT")
            before()
            listing = self.graph._one("SELECT * FROM expansion.listings WHERE tenant_id=? AND listing_id=? FOR UPDATE",
                (tenant, selected[0]["listing_id"]))
            if (listing["version"] != plan["expected_listing_versions"][listing["listing_id"]]
                    or not intake["source_id"] or listing["source_id"] != intake["source_id"]
                    or (kind == "DUPLICATE" and listing["current_revision_id"] is None)
                    or listing["lifecycle_state"] != "ACTIVE"):
                raise IdentityConflictError("IDENTITY_LISTING_TARGET_CONFLICT")
            self.graph._no_hold(tenant, "listing", listing["listing_id"], held=listing["legal_hold"])
            before()
            resolved = self.graph.resolve_listing(tenant, listing["listing_id"])
            property_id = resolved["effective_property_id"]
            if property_id not in plan["expected_property_versions"]:
                raise IdentityConflictError("PROPERTY_VERSION_SET_CONFLICT")
            before()
            prop = self.graph._one("SELECT * FROM identity.properties WHERE tenant_id=? AND property_id=? FOR UPDATE",
                (tenant, property_id))
            if prop["version"] != plan["expected_property_versions"][property_id] or prop["status"] != "ACTIVE":
                raise IdentityConflictError("VERSION_CONFLICT")
            self.graph._no_hold(tenant, "property", property_id)
            for observer in principals:
                for resource in (listing, prop):
                    authorize_intake_action(observer, "view", resource=resource)
            before()
            edges = self.engine.query("SELECT * FROM identity.source_identity_edges WHERE tenant_id=? "
                "AND listing_id=? AND effective_to IS NULL ORDER BY edge_id", (tenant, listing["listing_id"]))
            if ({row["edge_id"]: row["edge_version"] for row in edges} != plan["expected_edge_versions"]
                    or any(row["property_id"] != property_id for row in edges)):
                raise IdentityConflictError("SOURCE_EDGE_VERSION_CONFLICT")
            resources = {"properties": [prop], "listings": [listing], "source_edges": edges}
        before()
        if clock._clock() >= datetime.fromisoformat(current["manual_entry_valid_until"]):
            raise IdentityConflictError("IDENTITY_LISTING_SOURCE_INPUT_EXPIRED")
        before.check_in_memory()
        snapshot = {**resources, "input_binding": binding, "intake_version": intake["version"],
            "match_case_version": case["version"], "confirmed_by": actor_id,
            "manual_entry_valid_until": current["manual_entry_valid_until"],
            "field_proposals": [] if kind == "DUPLICATE" else [
                {name: row[name] for name in ("correction_id", "version", "status", "identity_affecting", "proposed_by")}
                for row in current["fields"]]}
        if kind == "DUPLICATE":
            snapshot.pop("confirmed_by")
            snapshot.update(proposer_subject_id=actor_id, reviewer_subject_id=reviewer_id)
        if include_values and kind != "DUPLICATE":
            snapshot["listing_values"] = {name: {row["field_path"]: row[name] for row in current["fields"]
                if row[name] is not None} for name in ("parsed", "normalized", "corrected")}
            snapshot["listing_values"]["effective"] = {row["field_path"]: effective_value(row) for row in current["fields"]}
        return snapshot

    def approve_exact(self, tenant, case_id, *, expected_case_version, policy_version_id, before):
        """The existing exact service owns approval; public human proposals cannot invoke it."""
        from modules.listing.application.native_intake_identity import NativeIntakeIdentityOwner

        with self.graph.transaction(tenant):
            case, intake = self.graph._case(tenant, _uuid(case_id))
            self._validate_context(tenant, case, intake, expected_case_version, kind="DUPLICATE", exact_service=True)
            snapshot = NativeIntakeIdentityOwner(self.graph).exact_snapshot(tenant, case, intake,
                policy_version_id=policy_version_id, before=before)
            plan = canonical_identity_plan("DUPLICATE", snapshot["plan"])
            key, audit_id = str(uuid4()), str(uuid4())
            at = self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            reason = "Resolve the currently proven unambiguous exact source identity."
            confirmation = {"confirmed_service_principal": "native-intake-identity",
                "service_subject_id": snapshot["service_subject_id"], "confirmed_at": at, "audit_event_id": audit_id}
            after = {"plan": plan, "match_case_version": case["version"] + 1, "intake_version": intake["version"],
                "confirmation": confirmation}
            self.engine.execute("INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,decision_type,"
                "status,proposer_subject_id,proposer_service_principal,reason,risk_acknowledged,before_graph,after_graph,reviewed_at) "
                "VALUES (?,?,?,'DUPLICATE','APPROVED',NULL,'native-intake-identity',?,false,?::jsonb,?::jsonb,?)",
                (key, tenant, case_id, reason, json.dumps(snapshot), json.dumps(after), at))
            if self.engine.execute("UPDATE identity.match_cases SET status='APPROVED',version=version+1,updated_at=? "
                "WHERE tenant_id=? AND match_case_id=? AND version=? AND status='PROPOSED'",
                (at, tenant, case_id, expected_case_version)).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            receipt = {"decision_id": key, "status": "APPROVED", "version": 1,
                "source_input_valid_until": snapshot["manual_entry_valid_until"], "audit_event_id": audit_id}
            self._audit(tenant, intake, None, key, {"plan": plan}, receipt, "identity.decision_approved.v1",
                reason, at, audit_id=audit_id, service_principal="native-intake-identity")
            self.graph._match_event(tenant, {"match_decision_id": key, "match_case_id": case_id, "decision_type": "DUPLICATE",
                "proposer_subject_id": None, "proposer_service_principal": "native-intake-identity", "reviewer_subject_id": None},
                status="APPROVED", case_version=case["version"] + 1, correlation_id=intake["correlation_id"], at=at)
            return receipt

    def propose(self, tenant: str, case_id: str, *, kind: str, plan: dict, expected_case_version: int,
                actor_id: str, reason: str, risk_acknowledged: bool) -> dict:
        actor_id, case_id = _uuid(actor_id), _uuid(case_id)
        if kind == "DUPLICATE" and isinstance(plan, dict) and "exact_authority" in plan:
            raise PermissionError("IDENTITY_EXACT_SERVICE_REQUIRED")
        routine = kind in {"CREATE", "REVISE"}
        if risk_acknowledged is not True or len(reason.strip()) < (3 if routine else 20):
            raise ValueError("Explicit identity risk acknowledgement and reason are required")
        plan = canonical_identity_plan(kind, deepcopy(plan))
        with self.graph.transaction(tenant):
            case, intake = self.graph._case(tenant, case_id)
            self._validate_context(tenant, case, intake, expected_case_version, kind=kind)
            before = self._snapshot(tenant, case, intake, kind, plan, confirmation_actor_id=actor_id, reason=reason)
            key, at = str(uuid4()), self.engine.query_one("SELECT clock_timestamp() AS now")["now"]
            audit_id = str(uuid4())
            after = {"plan": plan, "match_case_version": case["version"] + 1,
                     "intake_version": intake["version"], "proposal_audit_event_id": audit_id}
            self.engine.execute("INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,"
                "decision_type,status,proposer_subject_id,reason,risk_acknowledged,before_graph,after_graph) "
                "VALUES (?,?,?,?,'DRAFT',?,?,true,?::jsonb,?::jsonb)",
                (key, tenant, case_id, kind, actor_id, reason, json.dumps(before), json.dumps(after)))
            draft = {"decision_id": key, "match_case_id": case_id, "intake_id": intake["intake_id"],
                     "match_case_version": case["version"], "status": "DRAFT", "version": 1, "plan": plan}
            self._audit(tenant, intake, actor_id, key, None, draft,
                "identity.decision_created.v1", reason, at, audit_id=str(uuid4()))
            if routine:
                confirmation = {"confirmed_by": actor_id, "confirmed_at": at, "audit_event_id": audit_id}
                after = {**after, "confirmation": confirmation}
                if self.engine.execute("UPDATE identity.match_decisions SET status='APPROVED',reviewed_at=?,version=2,"
                    "after_graph=?::jsonb WHERE tenant_id=? AND match_decision_id=? AND status='DRAFT' AND version=1",
                    (at, json.dumps(after), tenant, key)).rowcount != 1:
                    raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
                if self.engine.execute("UPDATE identity.match_cases SET status='APPROVED',version=version+1,updated_at=? "
                    "WHERE tenant_id=? AND match_case_id=? AND status=? AND version=?",
                    (at, tenant, case_id, case["status"], case["version"])).rowcount != 1:
                    raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
                receipt = {"decision_id": key, "match_case_id": case_id, "match_case_version": case["version"]+1,
                    "status": "APPROVED", "version": 2, "job_id": None, "audit_event_id": audit_id,
                    "correlation_id": intake["correlation_id"]}
                self._audit(tenant, intake, actor_id, key, draft, receipt,
                    "identity.decision_approved.v1", reason, at, audit_id=audit_id)
                self.graph._match_event(tenant, {"match_decision_id": key, "match_case_id": case_id,
                    "decision_type": kind, "proposer_subject_id": actor_id, "reviewer_subject_id": None},
                    status="APPROVED", case_version=case["version"]+1, correlation_id=intake["correlation_id"], at=at)
                return receipt
            if self.engine.execute("UPDATE identity.match_decisions SET status='PENDING_REVIEW',version=2 "
                "WHERE tenant_id=? AND match_decision_id=? AND status='DRAFT' AND version=1", (tenant, key)).rowcount != 1:
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if self.engine.execute("UPDATE identity.match_cases SET status='PENDING_REVIEW',version=version+1,updated_at=? WHERE tenant_id=? "
                "AND match_case_id=? AND status=? AND version=?", (at, tenant, case_id, case["status"], case["version"])).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            pending = {**draft, "status": "PENDING_REVIEW", "version": 2, "match_case_version": case["version"]+1,
                       "audit_event_id": audit_id}
            self._audit(tenant, intake, actor_id, key, draft, pending,
                "identity.decision_proposed.v1", reason, at, audit_id=audit_id)
            candidate_ids = [row["match_candidate_id"] for row in self.engine.query(
                "SELECT match_candidate_id FROM identity.match_candidates WHERE tenant_id=? AND match_case_id=? ORDER BY rank",
                (tenant, case_id))]
            contradictory = case["contradictory_signals"]
            self.graph._outbox(DomainEvent(event_type="match.review_required", tenant_id=tenant, aggregate_type="match_case",
                aggregate_id=case_id, aggregate_version=case["version"]+1, partition_key=tenant+":"+case_id,
                correlation_id=intake["correlation_id"], actor_ref=actor_id, producer="matcher_service",
                occurred_at=datetime.fromisoformat(at), schema_ref="#/payloads/MatchReviewRequiredV1",
                payload={"match_case_id": case_id, "intake_id": intake["intake_id"], "outcome": case["outcome"],
                         "confidence": float(case["confidence"]), "candidate_ids": candidate_ids,
                         "contradictory_signals": json.loads(contradictory) if isinstance(contradictory, str) else contradictory}))
            return {"decision_id": key, "status": "PENDING_REVIEW", "version": 2, "job_id": None,
                    "audit_event_id": audit_id, "correlation_id": intake["correlation_id"],
                    "match_case_id": case_id, "match_case_version": case["version"]+1}


    def review(self, tenant: str, decision_id: str, *, expected_decision_version: int,
               expected_case_version: int, reviewer_id: str, approve: bool, reason: str,
               risk_acknowledged: bool) -> dict:
        reviewer_id, decision_id = _uuid(reviewer_id), _uuid(decision_id)
        if (type(approve) is not bool or type(risk_acknowledged) is not bool or len(reason.strip()) < 3
                or (approve and risk_acknowledged is not True)):
            raise ValueError("An explicit review and reason/risk acknowledgement are required")
        with self.graph.transaction(tenant):
            decision = self.graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? "
                "AND match_decision_id=? FOR UPDATE", (tenant, decision_id))
            if decision.get("proposer_service_principal") is not None:
                raise IdentityConflictError("IDENTITY_SERVICE_DECISION_NOT_HUMAN_REVIEWABLE")
            if decision["version"] != _version(expected_decision_version) or decision["status"] != "PENDING_REVIEW":
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if reviewer_id == decision["proposer_subject_id"]:
                raise IdentityConflictError("SECOND_ACTOR_REQUIRED")
            case, intake = self._context(tenant, decision["match_case_id"], expected_case_version)
            if decision["after_graph"].get("match_case_version") != case["version"]:
                raise IdentityConflictError("IDENTITY_PROPOSAL_CASE_BINDING_CONFLICT")
            if approve and decision["after_graph"].get("intake_version") != intake["version"]:
                raise IdentityConflictError("INTAKE_VERSION_CONFLICT")
            plan = canonical_identity_plan(decision["decision_type"], decision["after_graph"]["plan"])
            if decision["decision_type"] == "DUPLICATE":
                self.confirmation_principal(case, intake, kind="DUPLICATE", actor_id=reviewer_id,
                    proposer_id=decision["proposer_subject_id"], reason=reason, approve=approve)
            snapshot = self._snapshot(tenant, case, intake, decision["decision_type"], plan,
                confirmation_actor_id=decision["proposer_subject_id"], reviewer_actor_id=reviewer_id,
                reason=decision["reason"]) if approve else None
            at, audit_id = self.engine.query_one("SELECT clock_timestamp() AS now")["now"], str(uuid4())
            status = "APPROVED" if approve else "REJECTED"
            review = {"reviewer_subject_id": reviewer_id, "status": status, "reason": reason,
                      "risk_acknowledged": risk_acknowledged, "reviewed_at": at, "audit_event_id": audit_id}
            after = {**decision["after_graph"], "review": review, "match_case_version": case["version"]+1,
                     "review_snapshot": snapshot}
            if self.engine.execute("UPDATE identity.match_decisions SET status=?,reviewer_subject_id=?,reviewed_at=?,"
                "version=version+1,after_graph=?::jsonb WHERE tenant_id=? AND match_decision_id=? "
                "AND status='PENDING_REVIEW' AND version=?", (status, reviewer_id, at, json.dumps(after),
                 tenant, decision_id, decision["version"])).rowcount != 1:
                raise IdentityConflictError("IDENTITY_DECISION_VERSION_CONFLICT")
            if self.engine.execute("UPDATE identity.match_cases SET status=?,version=version+1,updated_at=? "
                "WHERE tenant_id=? AND match_case_id=? AND status='PENDING_REVIEW' AND version=?",
                (status, at, tenant, case["match_case_id"], case["version"])).rowcount != 1:
                raise IdentityConflictError("MATCH_CASE_VERSION_CONFLICT")
            before_value = {"decision_id": decision_id, "status": "PENDING_REVIEW", "version": decision["version"],
                            "plan": plan, "match_case_id": case["match_case_id"], "match_case_version": case["version"]}
            after_value = {**before_value, **review, "version": decision["version"]+1, "match_case_version": case["version"]+1}
            self._audit(tenant, intake, reviewer_id, decision_id, before_value, after_value,
                "identity.decision_approved.v1" if approve else "identity.decision_rejected.v1", reason, at, audit_id=audit_id)
            self.graph._match_event(tenant, {**decision, "reviewer_subject_id": reviewer_id}, status=status,
                case_version=case["version"]+1, correlation_id=intake["correlation_id"], at=at)
            return {"decision_id": decision_id, "status": status, "version": decision["version"]+1,
                    "match_case_id": case["match_case_id"], "match_case_version": case["version"]+1,
                    "job_id": None, "audit_event_id": audit_id, "correlation_id": intake["correlation_id"]}


__all__ = ["CanonicalIdentityDecisionRepository", "canonical_identity_plan"]
