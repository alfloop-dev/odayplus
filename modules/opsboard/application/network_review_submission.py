"""Saved-report submission owner. No scoring, model admission or role grants."""
from __future__ import annotations

import copy
import hashlib
import json
import pickle
from dataclasses import replace
from typing import Any, Literal

from modules.opsboard.application.network_read_scope import linked_record_in_scope, record_in_scope
from modules.opsboard.application.network_reviews import (
    NetworkReviewConflict,
    NetworkReviewNotFound,
    NetworkReviewRoleError,
    NetworkReviewRuntimeUnavailable,
)
from shared.auth import Action, DataClassification
from shared.auth.rbac import ROLE_PERMISSIONS, rbac_allows
from shared.workflow.sitescore import DecisionStatus, SiteScoreDecisionError


def candidate_digest(candidate: Any) -> str:
    return hashlib.sha256(pickle.dumps(candidate, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()


def reviewer_groups() -> list[dict[str, str]]:
    return [{'id': role.value, 'label': role.name.replace('_', ' ').title(),
             'source': 'shared.auth.rbac.ROLE_PERMISSIONS'}
            for role, grants in sorted(ROLE_PERMISSIONS.items(), key=lambda item: item[0].value)
            if any(grant.matches('sitescore', Action.APPROVE) for grant in grants)]


class NetworkReviewSubmissionOwner:
    def __init__(self, service: Any) -> None:
        self.service = service

    def _candidate(self, candidate_id: str, principal: Any, *, action: Action = Action.EXECUTE,
                   module: Literal['operator_console', 'sitescore'] = 'operator_console'):
        s = self.service
        s._require_canonical_dependencies()
        # Read context checks the same actual bindings as the mutation owner;
        # constructing the transactional delivery adapter performs no delivery.
        s.transaction_scope(s._decision_repository.engine, s._decision_repository.tenant_id)
        c = s._candidate_workflow_service
        if c is None:
            raise NetworkReviewRuntimeUnavailable('canonical candidate owner is unavailable')
        if (principal is None or not principal.authenticated or not principal.subject_id
                or principal.tenant_id != s._decision_repository.tenant_id
                or not rbac_allows(principal, 'sitescore', action)):
            raise NetworkReviewRoleError('authenticated scoped SiteScore actor required')
        if not principal.scope.permits_classification(DataClassification.CONFIDENTIAL):
            raise NetworkReviewRoleError('candidate classification outside actor clearance')
        if module not in {'operator_console', 'sitescore'} or not principal.scope.permits_module(module):
            raise NetworkReviewRoleError('operator module outside actor scope' if module == 'operator_console'
                                         else 'sitescore module outside actor scope')
        repository = c._listing_repository
        candidate = repository._store.get(repository._CANDIDATES, candidate_id)
        if candidate is None:
            raise NetworkReviewNotFound('candidate not found')
        listing = repository.get_listing(candidate.listing.listing_id)
        if listing is None or candidate.candidate_site.listing_id != listing.listing_id:
            raise NetworkReviewConflict('candidate listing relationship is unavailable')
        # These are identity consistency checks, not fresh model/data gates.
        # A tenant partition does not erase contradictory known record tenants.
        actual_address = repository.get_address(listing.address_id)
        known_tenants = [getattr(record, "tenant_id", None) for record in
                         (candidate, candidate.candidate_site, candidate.listing, candidate.address, listing, actual_address)]
        if (actual_address is None or candidate.candidate_site.address_id != listing.address_id
                or candidate.address.address_id != listing.address_id
                or candidate.listing.address_id != listing.address_id
                or any(value is not None and str(value).strip() and value != principal.tenant_id for value in known_tenants)):
            raise NetworkReviewConflict("CURRENT_CANDIDATE_RELATIONSHIP_CONFLICT")
        parent = c._listing_to_dict(listing)
        child = c._candidate_to_dict(candidate)
        if not record_in_scope(principal, parent) or not linked_record_in_scope(principal, child, parent):
            raise NetworkReviewRoleError('candidate listing outside actor scope')
        return candidate

    def _target(self, candidate: Any, decision_id: str | None):
        s = self.service
        candidate_id = candidate.candidate_site.candidate_site_id
        latest = s._sitescore_repository.latest(candidate_id)
        if latest is None:
            raise NetworkReviewConflict('SAVED_REPORT_REQUIRED')
        decisions = [d for d in s._decision_repository.list_decisions() if d.candidate_site_id == candidate_id]
        pointer = candidate.review_id
        if pointer and decision_id and pointer != decision_id:
            raise NetworkReviewConflict('CANDIDATE_REVIEW_POINTER_CONFLICT')
        own_id = pointer or decision_id
        pending = [d for d in decisions if d.status == DecisionStatus.PENDING_REVIEW]
        if len(pending) > 1:
            raise NetworkReviewConflict('AMBIGUOUS_PENDING_DECISION')
        if pending and pending[0].decision_id != own_id:
            raise NetworkReviewConflict('CANDIDATE_REVIEW_POINTER_CONFLICT')
        if own_id:
            decision = s._decision_repository.get_decision(own_id)
            if (decision is None or decision.decision_id != own_id
                    or decision.candidate_site_id != candidate_id or decision.tenant_id != s._decision_repository.tenant_id):
                raise NetworkReviewConflict('OWN_DECISION_REQUIRED')
        else:
            if any(d.status in {DecisionStatus.APPROVED, DecisionStatus.REJECTED} for d in decisions):
                raise NetworkReviewConflict('TERMINAL_REVIEW_POLICY_UNRESOLVED')
            if any(getattr(d, 'network_action', None) is not None for d in decisions):
                raise NetworkReviewConflict('OWN_DECISION_REQUIRED')
            eligible = [d for d in decisions if d.report_id == latest.report_id
                        and d.report_version == latest.report_version]
            if len(eligible) > 1:
                raise NetworkReviewConflict('AMBIGUOUS_OWN_DECISION')
            decision = eligible[0] if eligible else None
        operational = candidate.candidate_site.site_status in {'approved', 'opened'}
        if candidate.candidate_site.site_status == 'rejected':
            raise NetworkReviewConflict('TERMINAL_CANDIDATE_POLICY_UNRESOLVED')
        if operational and decision is None:
            raise NetworkReviewConflict('OWN_DECISION_REQUIRED')
        if decision is not None:
            if decision.status not in {DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.DRAFT,
                                       DecisionStatus.PENDING_REVIEW, DecisionStatus.APPROVED, DecisionStatus.REJECTED}:
                raise NetworkReviewConflict('TERMINAL_REVIEW_POLICY_UNRESOLVED')
            if isinstance(decision.version, bool) or not isinstance(decision.version, int) or decision.version < 1:
                raise NetworkReviewConflict('ACTUAL_DECISION_VERSION_REQUIRED')
            report = s._decision_repository.get_report(decision.decision_id)
            if report is None or (report.candidate_site_id, report.report_id, report.report_version, report.model_version) != (
                    candidate_id, decision.report_id, decision.report_version, decision.model_version):
                raise NetworkReviewConflict('IMMUTABLE_RETAINED_REPORT_REQUIRED')
            original = s._sitescore_repository.get_report(report.report_id)
            if original != report:
                raise NetworkReviewConflict('IMMUTABLE_REPORT_BINDING_CONFLICT')
            if decision.status in {DecisionStatus.APPROVED, DecisionStatus.REJECTED}:
                if not operational:
                    raise NetworkReviewConflict('TERMINAL_REVIEW_POLICY_UNRESOLVED')
                if (type(latest.report_version) is not int or latest.report_version <= report.report_version
                        or latest.report_id == report.report_id):
                    raise NetworkReviewConflict('NEWER_SAVED_REPORT_REQUIRED')
                if any(d.report_id == latest.report_id for d in decisions):
                    raise NetworkReviewConflict('REPORT_ALREADY_HAS_DECISION')
                try:
                    s._decision_workflow._validate_previous_round(latest, decision.decision_id, decision.tenant_id)
                except SiteScoreDecisionError as exc:
                    raise NetworkReviewConflict(str(exc)) from exc
                # The CAS target keeps the old pointer/state/digest. Opening
                # the new round happens only after these are checked again.
                decision, report = None, latest
            elif operational:
                previous_id = getattr(decision, 'previous_decision_id', None)
                if previous_id is None:
                    raise NetworkReviewConflict('APPROVED_PREVIOUS_ROUND_REQUIRED')
                try:
                    s._decision_workflow._validate_previous_round(report, previous_id, decision.tenant_id)
                except SiteScoreDecisionError as exc:
                    raise NetworkReviewConflict(str(exc)) from exc
        else:
            report = latest
        if (not isinstance(report.report_id, str) or not report.report_id.strip() or isinstance(report.report_version, bool)
                or not isinstance(report.report_version, int) or report.report_version < 1
                or not isinstance(report.model_version, str) or not report.model_version.strip()):
            raise NetworkReviewConflict('SAVED_REPORT_IDENTITY_REQUIRED')
        return decision, report, latest

    def context(self, *, candidate_id: str, principal: Any, decision_id: str | None = None,
                module: Literal['operator_console', 'sitescore'] = 'operator_console') -> dict[str, Any]:
        candidate = self._candidate(candidate_id, principal, module=module)
        decision, report, latest = self._target(candidate, decision_id)
        previous_id = (getattr(decision, 'previous_decision_id', None) if decision is not None else
                       candidate.review_id if candidate.candidate_site.site_status in {'approved', 'opened'} else None)
        previous_ref = None
        if previous_id is not None:
            previous = self.service._decision_repository.get_decision(previous_id)
            approval = self.service._decision_workflow._approval_store.get(previous.approval_id)
            previous_ref = {'decisionId': previous.decision_id, 'decisionVersion': previous.version,
                'approvalId': previous.approval_id, 'approvalStatus': approval.approval_status,
                'reportId': previous.report_id, 'reportVersion': previous.report_version,
                'modelVersion': previous.model_version, 'tenantId': previous.tenant_id}
        pending = decision is not None and decision.status == DecisionStatus.PENDING_REVIEW
        pending_ref = None
        if pending:
            approval = self.service._decision_workflow._approval_store.get(decision.approval_id)
            if approval is None or approval.decision_id != decision.decision_id or approval.approval_status != 'pending':
                raise NetworkReviewConflict('PENDING_APPROVAL_REQUIRED')
            pending_ref = {'decisionId': decision.decision_id, 'decisionVersion': decision.version,
                'approvalId': decision.approval_id, 'reportId': decision.report_id, 'reportVersion': decision.report_version,
                'latestReportId': latest.report_id, 'latestReportVersion': latest.report_version,
                'originalCreatorSubjectId': decision.created_by,
                'originalCreatorVerified': getattr(decision, 'created_by_verified', False),
                'verifiedSubmitterSubjectId': getattr(decision, 'submitted_by', None) if getattr(decision, 'submission_actor_verified', False) else None,
                'submissionSummary': getattr(decision, 'submission_summary', None),
                'reviewerGroupId': getattr(decision, 'reviewer_group_id', None)}
        return {'source': 'canonical', 'candidateId': candidate_id, 'listingId': candidate.listing.listing_id,
                'addressId': candidate.address.address_id, 'tenantId': principal.tenant_id,
                'actorSubjectId': principal.subject_id, 'canSubmit': not pending,
                'blockCode': 'ALREADY_PENDING' if pending else None, 'reviewerGroups': reviewer_groups(),
                **({'previousReview': previous_ref} if previous_ref is not None else {}),
                'pendingReview': pending_ref,
                'target': {'expectedReportId': report.report_id, 'expectedReportVersion': report.report_version,
                    'expectedLatestReportId': latest.report_id, 'expectedLatestReportVersion': latest.report_version,
                    'decisionId': decision.decision_id if decision else None,
                    'expectedDecisionVersion': decision.version if decision else None,
                    'expectedCandidateReviewId': candidate.review_id,
                    'expectedCandidateState': candidate.candidate_site.site_status,
                    'expectedCandidateDigest': candidate_digest(candidate)}}

    def submit(self, *, candidate_id: str, principal: Any, target: dict[str, Any],
               idempotency_key: str | None, correlation_id: str | None,
               submission_summary: str, reviewer_group_id: str,
               module: Literal['operator_console', 'sitescore'] = 'operator_console') -> dict[str, Any]:
        s = self.service
        candidate = self._candidate(candidate_id, principal, module=module)  # before replay
        if not idempotency_key or not idempotency_key.strip():
            raise NetworkReviewConflict('IDEMPOTENCY_KEY_REQUIRED')
        if not isinstance(submission_summary, str) or not submission_summary.strip():
            raise NetworkReviewConflict('SUBMISSION_SUMMARY_REQUIRED')
        if reviewer_group_id not in {row['id'] for row in reviewer_groups()}:
            raise NetworkReviewConflict('SERVER_REVIEWER_GROUP_REQUIRED')
        request = {'target': target, 'submissionSummary': submission_summary, 'reviewerGroupId': reviewer_group_id}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        key = ('canonical-submit', principal.tenant_id, principal.subject_id, candidate_id, idempotency_key)
        cached = s._idempotency_cache.get(key)
        if cached:
            if cached['requestFingerprint'] != fingerprint:
                raise NetworkReviewConflict('IDEMPOTENCY_PAYLOAD_CONFLICT')
            return {**copy.deepcopy(cached['response']), 'idempotentReplay': True}
        repository = s._candidate_workflow_service._listing_repository
        # report -> candidate is the existing publisher order. No intake/job or
        # remote-model authority is acquired by this human submission owner.
        with s._sitescore_repository.report_guard(candidate_id), repository.candidate_guard(candidate_id):
            candidate = self._candidate(candidate_id, principal, module=module)
            current = self.context(candidate_id=candidate_id, principal=principal, decision_id=target.get('decisionId'), module=module)
            if current['target'] != target:
                raise NetworkReviewConflict('SUBMISSION_CONTEXT_CONFLICT')
            decision, report, _ = self._target(candidate, target.get('decisionId'))
            already_pending = decision is not None and decision.status == DecisionStatus.PENDING_REVIEW
            if already_pending:
                if (submission_summary != getattr(decision, 'submission_summary', None)
                        or reviewer_group_id != getattr(decision, 'reviewer_group_id', None)):
                    raise NetworkReviewConflict('PENDING_SUBMISSION_PAYLOAD_CONFLICT')
                approval = s._decision_workflow._approval_store.get(decision.approval_id)
                if approval is None or approval.decision_id != decision.decision_id or approval.approval_status != 'pending':
                    raise NetworkReviewConflict('PENDING_APPROVAL_REQUIRED')
            else:
                try:
                    if decision is None:
                        decision = s._decision_workflow.open_decision(report, created_by=principal.subject_id,
                            correlation_id=correlation_id or '', tenant_id=principal.tenant_id, verified_creator=True,
                            previous_decision_id=(candidate.review_id
                                if candidate.candidate_site.site_status in {'approved', 'opened'} else None))
                    decision = s._decision_workflow.submit_for_review(decision.decision_id,
                        submitted_by=principal.subject_id, correlation_id=correlation_id or '',
                        submission_summary=submission_summary, reviewer_group_id=reviewer_group_id, verified_submitter=True)
                except (SiteScoreDecisionError, ValueError) as exc:
                    raise NetworkReviewConflict(str(exc)) from exc
                if candidate.candidate_site.site_status in {'approved', 'opened'}:
                    next_candidate = copy.deepcopy(candidate)
                    next_candidate.review_id = decision.decision_id
                else:
                    next_candidate = replace(candidate, review_id=decision.decision_id)
                repository.save_review_candidate(next_candidate,
                    expected_review_id=candidate.review_id, expected_state=candidate.candidate_site.site_status)
                # Pending has a real persisted pointer and a distinct pending
                # relation; it is not counterfeit terminal reviewWorkflow data.
                c = s._candidate_workflow_service
                metadata = c._get_candidate_metadata(candidate_id)
                expected_owned = {key: copy.deepcopy(metadata[key]) for key in ('reviewWorkflow', 'reviewSubmission') if key in metadata}
                metadata['reviewSubmission'] = {'decisionId': decision.decision_id,
                    'approvalId': decision.approval_id, 'reportId': decision.report_id,
                    'reportVersion': decision.report_version, 'decisionVersion': decision.version,
                    'tenantId': decision.tenant_id, 'submittedBy': principal.subject_id,
                    'submissionSummary': submission_summary, 'reviewerGroupId': reviewer_group_id,
                    'originalCreatorSubjectId': decision.created_by, 'originalCreatorVerified': getattr(decision, 'created_by_verified', False)}
                if getattr(decision, 'previous_decision_id', None) is not None:
                    metadata['reviewSubmission']['previousDecisionId'] = decision.previous_decision_id
                c._intakes.save_review_metadata(candidate_id, metadata, expected_owned=expected_owned)
                s._reviews = [r for r in s._reviews if r['id'] != decision.decision_id] + [
                    {'id': decision.decision_id, 'candidateId': candidate_id, 'status': 'pending',
                     'approvalId': decision.approval_id, 'reportId': decision.report_id,
                     'reportVersion': decision.report_version, 'decisionVersion': decision.version,
                     'tenantId': decision.tenant_id, 'actor': principal.subject_id}]
            event = next((e for e in reversed(s._decision_workflow.audit_log.list_events())
                if e.resource == f'sitescore/decision/{decision.decision_id}' and e.action == 'submit'
                and e.metadata.get('version') == decision.version), None)
            if event is None:
                raise NetworkReviewRuntimeUnavailable('ACTUAL_SUBMIT_AUDIT_REQUIRED')
            result = {'source': 'canonical', 'acceptedNewSubmission': not already_pending, 'review': decision.to_dict(), 'records': {
                'candidateId': candidate_id, 'listingId': candidate.listing.listing_id,
                'reportId': decision.report_id, 'reportVersion': decision.report_version,
                'reviewId': decision.decision_id, 'decisionId': decision.decision_id,
                'decisionVersion': decision.version, 'approvalId': decision.approval_id,
                'auditId': event.event_id if event else None}, 'commandReceipt': {
                'operation': 'submit', 'actorSubjectId': principal.subject_id, 'tenantId': principal.tenant_id,
                'idempotencyKey': idempotency_key, 'correlationId': correlation_id,
                'decisionId': decision.decision_id, 'submissionSummary': submission_summary,
                'reviewerGroupId': reviewer_group_id, 'originalCreatorSubjectId': decision.created_by,
                'verifiedSubmitterSubjectId': getattr(decision, 'submitted_by', None),
                'originalCreatorVerified': getattr(decision, 'created_by_verified', False)}, 'idempotentReplay': False, 'correlationId': correlation_id}
            if getattr(decision, 'previous_decision_id', None) is not None:
                result['commandReceipt']['previousDecisionId'] = decision.previous_decision_id
                result['commandReceipt']['candidateState'] = candidate.candidate_site.site_status
            s._idempotency_cache[key] = {'requestFingerprint': fingerprint, 'response': copy.deepcopy(result)}
            return result
