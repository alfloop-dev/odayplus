"""Synthetic registry rows and identities. Never installed by application code."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from modules.listing.application.intake_audit import AUDIT_RETENTION_KIND
from modules.listing.application.intake_registry_calendar import (
    CALENDAR_POLICY_KIND,
    WORKFLOW_POLICY_KIND,
    RegistryIntakeWorkflowPolicy,
)
from shared.auth import Role, Scope
from shared.governance import DecisionPolicy, InMemoryDecisionPolicyRepository
from shared.identity.store import Account, InMemoryIdentityStore

TENANT = "00000000-0000-0000-0000-000000000001"
APPROVER = "00000000-0000-0000-0000-000000000101"
MANAGER = "00000000-0000-0000-0000-000000000102"
OWNER = "00000000-0000-0000-0000-000000000103"
AT = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)  # Synthetic Friday 16:30 Taipei.
CALENDAR_ID = f"synthetic-calendar-v1:{TENANT}"
POLICY_ID = f"synthetic-intake-v1:{TENANT}"


def _audit_retention_row():
    return replace(_row("synthetic-audit-v1", AUDIT_RETENTION_KIND, {
        "schema_version": 1, "duration_basis": "UTC_ELAPSED", "retention_days": 90,
    }), owner_role=Role.RECORDS_MANAGER.value, declared_inputs=("scope", "occurred_at"))


def _row(label, kind, parameters):
    return DecisionPolicy(
        policy_version_id=f"{label}:{TENANT}",
        policy_label=label,
        policy_id=label,
        policy_version="1",
        policy_kind=kind,
        tenant_id=TENANT,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        parameters=parameters,
        declared_inputs=(
            "scope",
            "submitted_at",
            "actor_account",
            "target_account",
            "requested_due_at",
            "work_evidence",
        ),
        approved_by=APPROVER,
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        owner_role=Role.DATA_OWNER.value,
    )


def _calendar_row():
    return _row(
        "synthetic-calendar-v1",
        CALENDAR_POLICY_KIND,
        {
            "schema_version": 1,
            "duration_basis": "UTC_ELAPSED",
            "timezone": "Asia/Taipei",
            "coverage_from": "2026-01-01",
            "coverage_through": "2026-12-31",
            "weekly_windows": {
                str(day): [["09:00", "12:00"], ["13:00", "17:00"]] if day <= 5 else []
                for day in range(1, 8)
            },
            "date_overrides": {"2026-10-12": []},
            "ambiguous_time": "REJECT",
            "nonexistent_time": "REJECT",
        },
    )


def _workflow_row():
    return _row(
        "synthetic-intake-v1",
        WORKFLOW_POLICY_KIND,
        {
            "schema_version": 1,
            "calendar_version_id": CALENDAR_ID,
            "queue_key": "synthetic-queue",
            "warning_work_minutes": 60,
            "due_work_minutes": 120,
            "breach_work_minutes": 180,
            "assignee_role_bindings": {"expansion-staff": "expansion_user"},
            "deadline_override": {"mode": "KEEP_POLICY"},
            "approved_pause_reasons": ["Synthetic approved hold"],
            "completion_requirements": [
                {
                    "kind": "intake",
                    "allowed_states": ["READY", "CANCELLED"],
                    "minimum_count": 1,
                    "require_audit": True,
                }
            ],
        },
    )


def _provider(*, policy=None, calendar=None):
    identities = InMemoryIdentityStore()
    for account_id, roles in [
        (APPROVER, {Role.DATA_OWNER}),
        (MANAGER, {Role.SITE_REVIEWER}),
        (OWNER, {Role.EXPANSION_USER}),
    ]:
        identities.save_account(
            Account(
                account_id=UUID(account_id),
                tenant_id=UUID(TENANT),
                username=account_id,
                email="synthetic@example.invalid",
            )
        )
        identities.set_account_roles(account_id, roles)
        identities.set_account_scope(account_id, Scope(tenant_id=TENANT))
    repo = InMemoryDecisionPolicyRepository(
        [policy or _workflow_row(), calendar or _calendar_row()]
    )
    return RegistryIntakeWorkflowPolicy(repo, identities), identities, repo
