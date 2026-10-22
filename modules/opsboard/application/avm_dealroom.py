"""Request-scoped consumer of the original AVM finance/DataRoom writers.

This is not an inference fallback or an attachment provider. The export is the
actual retained, masked DataRoom JSON; it never claims to contain source files.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from modules.avm.application.valuation import AVMError, AVMReviewRequiredFailure, AVMService
from modules.avm.domain import DataRoom, NormalizedMargin, ValuationCase, ValuationReport
from shared.audit.events import AuditEvent
from shared.audit.integrity import AuditIntegrityError
from shared.audit.policy import build_security_event
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Environment,
    ResourceDescriptor,
    evaluate_abac,
    rbac_allows,
)
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableAVMRepository


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(503, "AVM_CANONICAL_SOURCE_INVALID") from exc


def source_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class ScopedAVMDealRoom:
    _RECEIPTS = "avm.dealroom.command_receipts"
    _EXPORTS = "avm.dealroom.json_exports"

    def __init__(self, *, principal, repository, stores, brands, audit_log, queue, correlation_id,
                 production_executor=None, depreciation_version_pin=None, rollback_receipt=None,
                 force_production=False):
        self.principal = principal
        self.repository = repository
        self.documents = getattr(repository, "_store", None)
        self.engine = getattr(self.documents, "engine", None)
        self.stores, self.brands = stores, brands
        self.audit, self.queue = audit_log, queue
        self.correlation_id = correlation_id
        if not principal.authenticated or not principal.tenant_id:
            raise HTTPException(401, "AUTHENTICATED_ACTOR_REQUIRED")
        if (not isinstance(repository, DurableAVMRepository)
                or not isinstance(self.documents, TenantScopedDocumentStore)
                or self.documents.tenant_id != principal.tenant_id
                or self.engine is None or not isinstance(audit_log, DurableAuditLog)
                or audit_log._engine is not self.engine
                or not isinstance(queue, DurableJobQueue) or queue._engine is not self.engine
                or stores is None or brands is None):
            raise HTTPException(503, "AVM_DEALROOM_OWNER_UNAVAILABLE")
        self.native = AVMService(repository=repository, production_executor=production_executor,
            depreciation_version_pin=depreciation_version_pin, rollback_receipt=rollback_receipt)
        self.native.production_required = self.native.production_required or force_production
        self.formal_data_required = self.native.production_required

    def create_case(self, data, *, idempotency_key):
        """The original typed input writer, bound to the real server subject."""
        from modules.avm.domain import ValuationCase, build_valuation_view

        request_hash = source_hash(data)
        receipt_key = source_hash([self.principal.subject_id, "create", idempotency_key])
        # Tenant owner entry also covers a new row which does not exist yet.
        denial = None
        with self._transaction(None):
            previous = self.documents.get(self._RECEIPTS, receipt_key)
            if previous is not None:
                if previous["requestHash"] != request_hash:
                    raise HTTPException(409, "AVM_IDEMPOTENCY_CONFLICT")
                existing = self._raw_case(previous["caseId"])
                try:
                    self._authorize(existing, Action.CREATE)
                except HTTPException as exc:
                    denial = exc
                if denial is None:
                    return {"detail": self._projection(existing.case_id), "receipt": previous["receipt"], "idempotentReplay": True}
            else:
                try:
                    item = build_valuation_view(data)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(422, "AVM_VALUATION_INPUT_INVALID") from exc
                proposed = ValuationCase.create(item, created_by=self.principal.subject_id, correlation_id=self.correlation_id)
                try:
                    self._authorize(proposed, Action.CREATE, resource_id=f"stores/{item.store_id}")
                except HTTPException as exc:
                    denial = exc
                if denial is None:
                    created = self.native.create_case(item, created_by=self.principal.subject_id, correlation_id=self.correlation_id)
                    event = self.audit.record(AuditEvent(event_type="avm.case_created.v1", actor=self.principal.subject_id,
                        action="create_case", resource=f"avm/cases/{created.case_id}", outcome="created", correlation_id=self.correlation_id,
                        metadata={"tenant_id": self.principal.tenant_id, "store_id": created.store_id, "idempotency_key": idempotency_key,
                                  "input_sha256": request_hash, "authenticated_principal": self._current_credential_binding()}))
                    receipt = {"sourceId": created.case_id, "operation": "create", "actor": self.principal.subject_id,
                        "auditEventId": event.event_id, "correlationId": self.correlation_id, "artifact": None,
                        "deliveryStatus": "pending" if getattr(self.audit, "_worm_sink", None) is not None else "not_configured"}
                    if not self.documents.put_if_absent(self._RECEIPTS, receipt_key,
                            {"requestHash": request_hash, "receipt": receipt, "caseId": created.case_id}):
                        raise HTTPException(409, "AVM_IDEMPOTENCY_CONFLICT")
                    return {"detail": self._projection(created.case_id), "receipt": receipt, "idempotentReplay": False}
        raise denial

    def context(self):
        scope = asdict(self.principal.scope)
        # frozensets are encoded in a stable order; no token/session is exposed.
        scope = {key: sorted(value) if isinstance(value, (set, frozenset)) else
                 value.value if hasattr(value, "value") else value for key, value in scope.items()}
        return {"subjectId": self.principal.subject_id, "tenantId": self.principal.tenant_id,
                "canCreate": rbac_allows(self.principal, "avm", Action.CREATE),
                "contextKey": source_hash([self.principal.subject_id, sorted(role.value for role in self.principal.roles),
                                           scope, jsonable_encoder(self.principal.attributes)])}

    def _raw_case(self, case_id):
        row = self.documents.get(self.repository._CASES, case_id)
        if row is None:
            raise HTTPException(404, "AVM_CASE_NOT_FOUND")
        if not isinstance(row, ValuationCase) or row.case_id != case_id or row.valuation_input.store_id != row.store_id:
            raise HTTPException(503, "AVM_CANONICAL_SOURCE_INVALID")
        return row

    def _authorize(self, case, action, *, hide=False, resource_id=None):
        if not isinstance(case, ValuationCase):
            raise HTTPException(503, "AVM_CANONICAL_SOURCE_INVALID")
        store = self.stores.get_store(case.store_id)
        brand = self.brands.get_brand(store.brand_id) if store is not None else None
        resource = ResourceDescriptor(type="avm", resource_id=resource_id or case.case_id,
            tenant_id=getattr(store, "tenant_id", None), brand_id=getattr(store, "brand_id", None),
            region_id=getattr(store, "region_code", None) or None, store_id=case.store_id,
            module="avm", data_classification=DataClassification.CONFIDENTIAL)
        access = AccessRequest(principal=self.principal, action=action, resource=resource,
            environment=Environment(attributes={"correlation_id": self.correlation_id}))
        valid = (store is not None and store.is_current and store.tenant_id == self.principal.tenant_id
                 and brand is not None and brand.tenant_id == self.principal.tenant_id
                 and rbac_allows(self.principal, "avm", action)
                 and self.principal.scope.permits_assigned_area(None)
                 and self.principal.scope.permits_heat_zone(None))
        decision = evaluate_abac(access) if valid else Decision.deny("canonical AVM object outside scope", policy_id="avm.dealroom.scope")
        if decision.allowed and action == Action.APPROVE and case.created_by == self.principal.subject_id:
            decision = Decision.deny("case creator cannot approve their own valuation case", policy_id="avm.finance.segregation")
        self.audit.record(build_security_event(access, decision))
        if not decision.allowed:
            raise HTTPException(404 if hide else 403, "AVM_CASE_NOT_FOUND" if hide else "AVM_OBJECT_SCOPE_DENIED")

    @contextmanager
    def _transaction(self, case_id):
        completed_refusal = None
        with self.repository.case_transaction(case_id):
            try:
                sink = getattr(self.audit, "_worm_sink", None)
                if sink is None:
                    yield
                else:
                    from shared.audit.durable_delivery import DurableAuditDelivery
                    delivery = DurableAuditDelivery(self.engine, self.principal.tenant_id, self.queue, sink)
                    with self.audit.transactional_worm_sink(delivery):
                        yield
            except HTTPException as exc:
                if exc.status_code != 409 or type(exc.__cause__) is not AVMReviewRequiredFailure:
                    raise
                # Only the native named failure has completed its saved
                # REVIEW_REQUIRED transition. Do not turn it into a successful
                # command receipt or let any other refusal commit mutations.
                completed_refusal = exc
        if completed_refusal is not None:
            raise completed_refusal

    def _load(self, case_id):
        case = self.repository.get_case(case_id)
        reports = self.repository.report_history(case_id)
        margin = self.repository.get_margin(case_id)
        room = self.repository.get_dataroom(case_id)
        if not isinstance(case, ValuationCase) or case.case_id != case_id or case.valuation_input.store_id != case.store_id:
            raise HTTPException(503, "AVM_CANONICAL_SOURCE_INVALID")
        if (any(not isinstance(r, ValuationReport) or r.case_id != case_id or r.store_id != case.store_id
                or type(r.valuation_version) is not int or r.valuation_version < 1 for r in reports)
                or len({r.report_id for r in reports}) != len(reports)
                or margin is not None and (not isinstance(margin, NormalizedMargin) or margin.case_id != case_id or margin.store_id != case.store_id)
                or room is not None and (not isinstance(room, DataRoom) or room.case_id != case_id
                    or room.valuation_card.get("case_id") != case_id
                    or room.valuation_card.get("store_id") != case.store_id
                    or not reports or room.valuation_card.get("valuation_version") != reports[-1].valuation_version
                    or (room.valuation_card.get("finance_approval") or {}).get("decision_id") !=
                       (reports[-1].finance_approval.decision_id if reports[-1].finance_approval else None))):
            raise HTTPException(503, "AVM_CANONICAL_SOURCE_INVALID")
        value = {"case": case.to_dict(), "reports": [r.to_dict() for r in reports],
                 "normalizedMargin": margin.to_dict() if margin else None,
                 "dataRoom": room.to_dict() if room else None}
        return case, value

    def _current_credential_binding(self):
        # This reserved attribute comes from the actual AuthenticationBoundary
        # returned principal. The legacy header-trust stub cannot set it, and
        # these source fields are never accepted from a request body/header.
        source = self.principal.attributes.get("token_type")
        if source not in {"local", "oidc", "service"}:
            return None
        return {"subject_id": self.principal.subject_id, "tenant_id": self.principal.tenant_id,
                "token_type": source}

    def _native_credential_proof(self, event, actor):
        binding = event.metadata.get("authenticated_principal")
        return (isinstance(binding, dict) and binding.get("subject_id") == actor
                and binding.get("tenant_id") == self.principal.tenant_id
                and binding.get("token_type") in {"local", "oidc", "service"})

    def _creator_proof(self, case):
        """Use retained native creation + actual authenticated authorization.

        Older native HTTP accepted created_by in a body. Its string alone is
        not a verified initiator. A unique same-command Auth005 allow must bind
        the native case creation event to that authenticated actor and tenant.
        Missing evidence remains unavailable; no creator is backfilled.
        """
        history = case.status_history
        if not history or not isinstance(history[0], dict):
            return {"verified": False, "reasonCode": "AVM_CREATOR_AUTHORITY_UNAVAILABLE"}
        first = history[0]
        cid = first.get("correlation_id")
        if (not isinstance(cid, str) or not cid.strip() or first.get("actor") != case.created_by
                or first.get("timestamp") != case.created_at.isoformat()
                or first.get("to_status") != "DATA_READY"):
            return {"verified": False, "reasonCode": "AVM_CREATOR_AUTHORITY_UNAVAILABLE"}
        try:
            events = self.audit.list_events(correlation_id=cid)
        except AuditIntegrityError as exc:
            raise HTTPException(503, "AVM_CREATOR_AUDIT_INTEGRITY_UNAVAILABLE") from exc
        creation = [event for event in events if event.event_type == "avm.case_created.v1"
                    and event.resource == f"avm/cases/{case.case_id}" and event.action == "create_case"]
        if len(creation) != 1 or creation[0].actor != case.created_by or creation[0].outcome != "created" or creation[0].sequence is None:
            return {"verified": False, "reasonCode": "AVM_CREATOR_AUTHORITY_UNAVAILABLE"}
        authorization = [event for event in events if event.event_type == "security.authorization"
                         and event.action == Action.CREATE.value and event.resource == "avm"
                         and event.sequence is not None and event.sequence < creation[0].sequence]
        if not authorization or any(event.actor != case.created_by or event.outcome != "allow"
                or event.metadata.get("tenant_id") != self.principal.tenant_id for event in authorization):
            return {"verified": False, "reasonCode": "AVM_CREATOR_AUTHORITY_UNAVAILABLE"}
        if not self._native_credential_proof(creation[0], case.created_by):
            return {"verified": False, "reasonCode": "AVM_CREATOR_AUTHORITY_UNAVAILABLE"}
        return {"verified": True, "actor": case.created_by,
                "credentialSource": creation[0].metadata["authenticated_principal"]["token_type"],
                "authorizationEventId": authorization[0].event_id,
                "creationEventId": creation[0].event_id}

    def _finance_proof(self, case, report, creator_proof):
        absent = {"verified": False, "reasonCode": "AVM_FINANCE_AUTHORITY_UNAVAILABLE"}
        approval = report.get("finance_approval") if report else None
        if not creator_proof["verified"] or not isinstance(approval, dict) or approval.get("actor_id") == case.created_by:
            return absent
        cid = approval.get("correlation_id")
        if not isinstance(cid, str) or not cid.strip():
            return absent
        try:
            events = self.audit.list_events(correlation_id=cid)
        except AuditIntegrityError as exc:
            raise HTTPException(503, "AVM_FINANCE_AUDIT_INTEGRITY_UNAVAILABLE") from exc
        matches = [event for event in events if event.event_type == "avm.finance_approved.v1"
                   and event.resource in {f"avm/cases/{case.case_id}", f"avm/cases/{case.case_id}/finance-approval"}
                   and event.actor == approval.get("actor_id")
                   and (event.metadata.get("source_id") or event.metadata.get("decision_id")) == approval.get("decision_id")
                   and event.metadata.get("valuation_version") == report["valuation_version"]]
        if len(matches) != 1 or matches[0].sequence is None:
            return absent
        authorization = [event for event in events if event.event_type == "security.authorization"
                         and event.resource == "avm" and event.action == Action.APPROVE.value
                         and event.sequence is not None and event.sequence < matches[0].sequence]
        if not authorization or any(event.actor != approval.get("actor_id") or event.outcome != "allow"
                or event.metadata.get("tenant_id") != self.principal.tenant_id for event in authorization):
            return absent
        if not self._native_credential_proof(matches[0], approval.get("actor_id")):
            return absent
        return {"verified": True, "decisionId": approval["decision_id"],
                "credentialSource": matches[0].metadata["authenticated_principal"]["token_type"],
                "authorizationEventId": authorization[0].event_id, "financeEventId": matches[0].event_id}

    def _projection(self, case_id):
        case, value = self._load(case_id)
        version = source_hash(value)
        sensitive = rbac_allows(self.principal, "avm", Action.APPROVE) or rbac_allows(self.principal, "avm", Action.EXPORT)
        latest = value["reports"][-1] if value["reports"] else None
        def report_is_formal(report):
            return isinstance(report.get("execution_metadata"), dict) and report["execution_metadata"].get("mode") == "production_oss"

        verified_report = latest is not None and (not self.formal_data_required or report_is_formal(latest))
        report_availability = {"state": "available" if verified_report else "unavailable",
            "reasonCode": None if verified_report else "AVM_PRODUCTION_REPORT_NOT_VERIFIED" if latest else "AVM_REPORT_NOT_RECORDED"}
        # No free-form execution metadata or guessed artifact URL is exposed.
        report_fields = ("report_id", "valuation_version", "case_id", "store_id", "normalized_margin", "lenses", "fair_price",
                         "reserve_price", "asking_price", "confidence", "quality_score_status", "quality_disposition", "model_version",
                         "feature_version", "depreciation_version", "depreciation_applied", "prediction_origin_time", "valued_at", "finance_approval")
        def report_view(report):
            selected = {key: deepcopy(report.get(key)) for key in report_fields}
            # Native lenses may carry production artifact locations in their
            # execution metadata. Only the documented scalar source evidence
            # is a presentation contract; never expose arbitrary provider data.
            evidence_fields = {"multiple", "gm_ttm", "gm_fwd", "normalized_gm", "asset_book_value",
                "equipment_fair_value", "working_capital", "lease_liability", "source_snapshot_ids",
                "comparable_multiples", "liquidity_discount", "evidence_status", "included_lenses",
                "reserve_formula", "asking_formula", "mode"}
            for lens in selected["lenses"]:
                lens["evidence"] = {key: value for key, value in lens["evidence"].items() if key in evidence_fields}
            if not sensitive:
                selected["reserve_price"] = selected["asking_price"] = None
                if selected["finance_approval"]:
                    selected["finance_approval"].pop("reserve_price", None)
            return selected
        room = deepcopy(value["dataRoom"])
        if room is not None and not sensitive:
            room["valuation_card"].pop("reserve_price", None)
            room["valuation_card"].pop("asking_price", None)
            if room["valuation_card"].get("finance_approval"):
                room["valuation_card"]["finance_approval"].pop("reserve_price", None)
            room["valuation_card"].pop("legacy_finance_approval", None)
        creator_proof = self._creator_proof(case)
        finance_proof = self._finance_proof(case, latest, creator_proof) if verified_report else {"verified": False, "reasonCode": "AVM_REPORT_NOT_AVAILABLE"}
        approve = (verified_report and creator_proof["verified"] and rbac_allows(self.principal, "avm", Action.APPROVE) and case.created_by != self.principal.subject_id
                   and case.status.value == "REVIEW_REQUIRED" and latest is not None
                   and not value["reports"][-1].get("finance_approval")
                   and not self.repository.latest_report(case_id).is_legacy_quality_unknown)
        exports = [{key: row.get(key) for key in ("export_id", "actor", "reason", "exported_at", "correlation_id")}
                   for row in (room or {}).get("export_audit", [])]
        retained = {row["exportId"]: row for row in self.documents.list_by_group(self._EXPORTS, case_id)}
        for row in exports:
            artifact = retained.get(row["export_id"])
            row["artifact"] = ({"exportId": artifact["exportId"], "sha256": artifact["sha256"], "bytes": artifact["bytes"],
                                 "filename": artifact["filename"], "mediaType": "application/json"} if artifact else None)
        result = {"context": self.context(), "expectedVersion": version,
            "case": {key: value["case"][key] for key in ("case_id", "store_id", "status", "created_by", "created_at", "status_history")},
            "sourceSnapshotIds": list(case.valuation_input.source_snapshot_ids), "normalizedMargin": value["normalizedMargin"],
            "creatorVerification": creator_proof,
            "financeVerification": finance_proof,
            "report": report_view(latest) if verified_report else None,
            "reportHistory": [report_view(r) for r in value["reports"] if not self.formal_data_required or report_is_formal(r)],
            "reportAvailability": report_availability,
            "dataRoom": room if verified_report else None, "exports": exports if verified_report else [],
            "exportCount": len(exports) if verified_report else 0, "sensitiveValuesVisible": sensitive,
            "actions": {"approve": approve,
                "normalize": rbac_allows(self.principal, "avm", Action.CREATE) and case.status.value == "DATA_READY",
                "value": rbac_allows(self.principal, "avm", Action.CREATE) and case.status.value in {"DATA_READY", "REVIEW_REQUIRED"},
                "build": finance_proof["verified"] and rbac_allows(self.principal, "avm", Action.CREATE) and case.status.value in {"APPROVED", "DATAROOM_READY"},
                "export": finance_proof["verified"] and rbac_allows(self.principal, "avm", Action.EXPORT) and case.status.value == "DATAROOM_READY"},
            "attachmentProvider": {"available": False, "reasonCode": "AVM_SOURCE_DOCUMENT_BYTES_NOT_PROVIDED"}}
        canonical_json(result)
        return result

    def list_cases(self):
        visible = []
        # Authorize raw canonical parent first, before legacy read migrations.
        for raw in self.documents.list_all(self.repository._CASES):
            try:
                self._authorize(raw, Action.VIEW, hide=True)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise
            detail = self.read(raw.case_id)
            visible.append({key: detail[key] for key in ("case", "report", "dataRoom", "expectedVersion", "actions", "sensitiveValuesVisible", "reportAvailability", "exportCount")})
        return {"context": self.context(), "items": visible, "count": len(visible)}

    def read(self, case_id):
        self._authorize(self._raw_case(case_id), Action.VIEW, hide=True)
        denial = None
        with self._transaction(case_id):
            try:
                self._authorize(self._raw_case(case_id), Action.VIEW, hide=True)
            except HTTPException as exc:
                denial = exc
            if denial is None:
                return self._projection(case_id)
        raise denial

    def command(self, case_id, *, operation, expected_version, reason, reserve_price, idempotency_key):
        action = {"approve": Action.APPROVE, "build": Action.CREATE, "export": Action.EXPORT,
                  "normalize": Action.CREATE, "value": Action.CREATE}[operation]
        self._authorize(self._raw_case(case_id), action)
        request = {"caseId": case_id, "operation": operation, "expectedVersion": expected_version,
                   "reason": reason.strip(), "reservePrice": reserve_price}
        fingerprint = source_hash(request)
        key = source_hash([self.principal.subject_id, operation, idempotency_key])
        denial = None
        with self._transaction(case_id):
            try:
                self._authorize(self._raw_case(case_id), action)
            except HTTPException as exc:
                denial = exc
            if denial is None:
                before = self._projection(case_id)
                receipt = self.documents.get(self._RECEIPTS, key)
                if receipt is not None:
                    if receipt["requestHash"] != fingerprint:
                        raise HTTPException(409, "AVM_IDEMPOTENCY_CONFLICT")
                    return {"detail": before, "receipt": receipt["receipt"], "idempotentReplay": True}
                if before["expectedVersion"] != expected_version:
                    raise HTTPException(409, "AVM_VERSION_CONFLICT")
                if operation == "approve" and not before["creatorVerification"]["verified"]:
                    raise HTTPException(503, "AVM_CREATOR_AUTHORITY_UNAVAILABLE")
                if operation in {"approve", "build", "export"} and before["reportAvailability"]["state"] != "available":
                    raise HTTPException(503, "AVM_PRODUCTION_REPORT_UNAVAILABLE")
                if operation in {"build", "export"} and not before["financeVerification"]["verified"]:
                    raise HTTPException(503, "AVM_FINANCE_AUTHORITY_UNAVAILABLE")
                if operation == "value" and self.native.production_required and self.native.production_executor is None:
                    raise HTTPException(503, "AVM_PRODUCTION_MODEL_PROVIDER_UNAVAILABLE")
                try:
                    if operation == "normalize":
                        self.native.normalize(case_id, actor=self.principal.subject_id, correlation_id=self.correlation_id)
                        identity = case_id
                    elif operation == "value":
                        result = self.native.value(case_id, actor=self.principal.subject_id, correlation_id=self.correlation_id)
                        identity = result.report_id
                    elif operation == "approve":
                        result = self.native.approve_finance(case_id, actor=self.principal.subject_id, reason=reason.strip(),
                            reserve_price=reserve_price, correlation_id=self.correlation_id)
                        identity = result.finance_approval.decision_id
                    elif operation == "build":
                        result = self.native.build_dataroom(case_id, actor=self.principal.subject_id, correlation_id=self.correlation_id)
                        identity = result.dataroom_id
                    else:
                        result = self.native.export_dataroom(case_id, actor=self.principal.subject_id,
                            reason=reason.strip(), correlation_id=self.correlation_id)
                        identity = result.export_audit[-1]["export_id"]
                except AVMError as exc:
                    raise HTTPException(409, "AVM_NATIVE_WORKFLOW_REFUSED") from exc
                after = self._projection(case_id)
                artifact = None
                if operation == "export":
                    content = canonical_json({"format": "avm-dataroom-json-v1", "exportId": identity,
                        "case": after["case"], "report": after["report"], "dataRoom": after["dataRoom"],
                        "sourceSnapshotIds": after["sourceSnapshotIds"], "sourceDocumentBytesIncluded": False})
                    artifact = {"exportId": identity, "caseId": case_id, "content": content,
                        "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "filename": f"{identity}.json"}
                event_type = {"approve": "avm.finance_approved.v1", "build": "avm.dataroom_ready.v1", "export": "avm.dataroom_exported.v1",
                              "normalize": "avm.normalized.v1", "value": "avm.valued.v1"}[operation]
                event = self.audit.record(AuditEvent(event_type=event_type, actor=self.principal.subject_id,
                    action={"approve": "approve_finance", "build": "build_dataroom", "export": "export_dataroom",
                            "normalize": "normalize", "value": "value"}[operation],
                    resource=f"avm/cases/{case_id}", outcome="recorded", correlation_id=self.correlation_id,
                    metadata={"tenant_id": self.principal.tenant_id, "store_id": before["case"]["store_id"],
                        "reason": reason.strip(), "source_id": identity, "report_id": after["report"]["report_id"] if after["report"] else None,
                        "valuation_version": after["report"]["valuation_version"] if after["report"] else None,
                        "authenticated_principal": self._current_credential_binding(),
                        "before_version": before["expectedVersion"], "after_version": after["expectedVersion"],
                        "artifact_sha256": artifact["sha256"] if artifact else None}))
                if artifact is not None:
                    artifact["auditEventId"] = event.event_id
                    self.documents.put(self._EXPORTS, identity, artifact, group_key=case_id)
                receipt = {"sourceId": identity, "operation": operation, "actor": self.principal.subject_id,
                           "auditEventId": event.event_id, "correlationId": self.correlation_id,
                           "artifact": {k: artifact[k] for k in ("exportId", "sha256", "bytes", "filename")} if artifact else None,
                           "deliveryStatus": "pending" if getattr(self.audit, "_worm_sink", None) is not None else "not_configured"}
                if not self.documents.put_if_absent(self._RECEIPTS, key, {"requestHash": fingerprint, "receipt": receipt}):
                    raise HTTPException(409, "AVM_IDEMPOTENCY_CONFLICT")
                return {"detail": self._projection(case_id), "receipt": receipt, "idempotentReplay": False}
        raise denial

    def export_content(self, case_id, export_id):
        self._authorize(self._raw_case(case_id), Action.EXPORT, hide=True)
        denial = None
        with self._transaction(case_id):
            try:
                self._authorize(self._raw_case(case_id), Action.EXPORT, hide=True)
            except HTTPException as exc:
                denial = exc
            if denial is None:
                return self._retained_export(case_id, export_id)
        raise denial

    def _retained_export(self, case_id, export_id):
        # Called only inside the same case transaction after current scope
        # authorization; the exact native export and audit must both exist.
        item = self.documents.get(self._EXPORTS, export_id)
        if not isinstance(item, dict) or item.get("caseId") != case_id or item.get("exportId") != export_id:
            raise HTTPException(404, "AVM_EXPORT_NOT_FOUND")
        room = self.repository.get_dataroom(case_id)
        if room is None or sum(r.get("export_id") == export_id for r in room.export_audit) != 1:
            raise HTTPException(404, "AVM_EXPORT_NOT_FOUND")
        content = item.get("content")
        if (not isinstance(content, bytes) or len(content) != item.get("bytes")
                or hashlib.sha256(content).hexdigest() != item.get("sha256")):
            raise HTTPException(503, "AVM_EXPORT_INTEGRITY_UNAVAILABLE")
        if not isinstance(item.get("auditEventId"), str) or not item["auditEventId"].strip():
            raise HTTPException(503, "AVM_EXPORT_INTEGRITY_UNAVAILABLE")
        try:
            event = self.audit.get_event(item["auditEventId"], tenant_id=self.principal.tenant_id)
        except AuditIntegrityError as exc:
            raise HTTPException(503, "AVM_EXPORT_INTEGRITY_UNAVAILABLE") from exc
        if (event is None or event.event_type != "avm.dataroom_exported.v1"
                or event.resource != f"avm/cases/{case_id}"
                or event.metadata.get("source_id") != export_id
                or event.metadata.get("artifact_sha256") != item["sha256"]):
            raise HTTPException(503, "AVM_EXPORT_INTEGRITY_UNAVAILABLE")
        # The artifact's own immutable audit names the exact historical report.
        # A newer production report must never endorse an older POC artifact.
        reports = [report for report in self.repository.report_history(case_id)
                   if report.report_id == event.metadata.get("report_id")
                   and report.valuation_version == event.metadata.get("valuation_version")
                   and report.case_id == case_id and report.store_id == room.valuation_card.get("store_id")]
        if len(reports) != 1:
            raise HTTPException(503, "AVM_EXPORT_SOURCE_UNAVAILABLE")
        report = reports[0]
        if self.formal_data_required and (not isinstance(report.execution_metadata, dict)
                or report.execution_metadata.get("mode") != "production_oss"):
            raise HTTPException(503, "AVM_PRODUCTION_REPORT_UNAVAILABLE")
        case = self._raw_case(case_id)
        creator = self._creator_proof(case)
        if not creator["verified"] or not self._finance_proof(case, report.to_dict(), creator)["verified"]:
            raise HTTPException(503, "AVM_FINANCE_AUTHORITY_UNAVAILABLE")
        original_exports = [row for row in room.export_audit if row.get("export_id") == export_id]
        if (len(original_exports) != 1 or original_exports[0].get("actor") != event.actor
                or original_exports[0].get("correlation_id") != event.correlation_id):
            raise HTTPException(503, "AVM_EXPORT_SOURCE_UNAVAILABLE")
        return item
