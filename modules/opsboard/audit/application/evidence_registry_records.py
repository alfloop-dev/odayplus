"""Actual registry facts, not invented export-program/purpose approvals.

The shipped policy-kind CHECK and native parameter contracts do not define a
SiteScore evidence program, field policy or export-retention record. This
read-only adapter validates existing PostgreSQL registry/account facts without
repurposing intake_audit_retention or treating an approver's name as authority.
Its result is deliberately not a Stage1 read_authorized binding.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from modules.opsboard.audit.application.evidence_admission import CanonicalRecordReference
from modules.opsboard.audit.application.sitescore_evidence import SiteScoreEvidenceUnavailable
from shared.audit.integrity import sha256_hex
from shared.auth import Role, Scope
from shared.governance import DecisionPolicy
from shared.identity.store import SqlIdentityStore
from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
from shared.infrastructure.persistence.postgresql import PostgresEngine


def _unavailable() -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:actual_applicable_registry_record")


def _scope_covers(grant: Scope, requested: Scope) -> bool:
    if grant.tenant_id != requested.tenant_id or not grant.permits_classification(requested.clearance):
        return False
    for axis in ("brand_ids", "region_ids", "store_ids", "assigned_area_ids", "heat_zone_ids", "modules"):
        allowed, selected = getattr(grant, axis), getattr(requested, axis)
        if allowed and (not selected or not selected <= allowed):
            return False
    return True


@dataclass(frozen=True)
class RegistryPolicyFacts:
    reference: CanonicalRecordReference
    content_sha256: str
    policy: DecisionPolicy
    approval_scope: Scope
    export_admitted: bool = field(default=False, init=False)


class SqlEvidenceRegistryRecordReader:
    """Read a real selected version and its currently active approval identity.

    This is a concrete SQL reader, not a policy installer or a new policy-kind
    namespace. Missing/inapplicable data fails closed. A separate, defined
    native export schema still must bind program/purpose/fields/destination and
    its effective retention semantics before it can implement Stage1's ports.
    """

    def __init__(self, engine: PostgresEngine) -> None:
        if not isinstance(engine, PostgresEngine):
            raise _unavailable()
        self.engine = engine
        self.policies = SqlDecisionPolicyRepository(engine)
        self.identities = SqlIdentityStore(connection_factory=self._connection)

    @contextmanager
    def _connection(self):
        connection = getattr(self.engine._local, "connection", None)
        if connection is None:
            raise _unavailable()
        # Borrow the original transaction's connection; never commit/close it.
        yield connection

    def read_current(self, reference: CanonicalRecordReference, *, expected_kind: str,
                     scope: Scope, at: datetime) -> RegistryPolicyFacts:
        try:
            if (not isinstance(reference, CanonicalRecordReference)
                    or reference.owner != "workflow.decision_policies"
                    or not isinstance(expected_kind, str) or not expected_kind.strip()
                    or not isinstance(scope, Scope) or not scope.tenant_id
                    or not isinstance(at, datetime) or at.utcoffset() is None
                    or getattr(self.engine._local, "connection", None) is None):
                raise _unavailable()
            row = self.policies.find_version(reference.record_id)
            effective = self.policies.find_effective(policy_kind=expected_kind, tenant_id=scope.tenant_id, at=at)
            if (not isinstance(row, DecisionPolicy) or effective != row
                    or row.policy_version != reference.version or row.policy_kind != expected_kind
                    or row.tenant_id != scope.tenant_id or row.effective_from.utcoffset() is None
                    or (row.effective_to is not None and row.effective_to.utcoffset() is None)
                    or not row.covers(at) or row.approved_at is None or row.approved_at.utcoffset() is None
                    or row.approved_at > at):
                raise _unavailable()
            account = self.identities.find_account_by_id(row.approved_by)
            if account is None or not account.is_active or str(account.tenant_id) != scope.tenant_id:
                raise _unavailable()
            roles = self.identities.get_account_roles(account.account_id)
            approved_scope = self.identities.get_account_scope(account.account_id, require_explicit=True)
            if Role(row.owner_role) not in roles or not _scope_covers(approved_scope, scope):
                raise _unavailable()
            content: dict[str, Any] = {**row.to_dict(), "approved_at": row.approved_at.isoformat()}
            # No adapter branch interprets arbitrary parameters as export grant.
            return RegistryPolicyFacts(reference, sha256_hex(content), row, approved_scope)
        except SiteScoreEvidenceUnavailable:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, LookupError, PermissionError) as exc:
            raise _unavailable() from exc
