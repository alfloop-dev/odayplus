"""Owned SQLite form owner + current principal/store controls; not email/human UAT."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from apps.api.oday_api.security.dependencies import operator_permission_decision
from modules.opsboard.application.operator_franchisee_reports import admit_supervisor_reports
from modules.opsboard.application.operator_live_repository import OperatorLiveRepositoryError
from modules.opsboard.application.shell import (
    AUDIT_EVENTS,
    FRANCHISEE_REPORTS,
    ShellNotFound,
    ShellService,
)
from shared.auth import Action, DataClassification, Principal, ResourceDescriptor, Role, Scope
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_shell import DurableShellRepository

TENANT = "fran004-owned-tenant"
STORE = "fran004-owned-store"


class CurrentState:
    def __init__(self, principal=None):
        self.principal = principal or Principal("actual-supervisor", frozenset({Role.REGIONAL_SUPERVISOR}), Scope(tenant_id=TENANT, store_ids=frozenset({STORE})))
    def resolve_role(self, **_):
        return {"id": "field-lead", "label": "Display label does not grant access", "allowedWorkspaces": ["today", "store", "govern"]}
    def get_today(self, **_):
        return {"notifications": []}
    def admit_franchisee_reports(self, reports, audit_events):
        principal = self.principal
        stores = SimpleNamespace(get_store=lambda identity: SimpleNamespace(store_id=identity, is_current=True,
            tenant_id=TENANT, brand_id="actual-brand", region_code="actual-region") if identity == STORE else None)
        brands = SimpleNamespace(get_brand=lambda _: SimpleNamespace(tenant_id=TENANT))
        return admit_supervisor_reports(reports, audit_events, principal=principal, store_repository=stores,
            brand_repository=brands, can_read=lambda resource: operator_permission_decision(principal,
                ResourceDescriptor(type=resource, tenant_id=principal.tenant_id, module="operator_console", data_classification=DataClassification.CONFIDENTIAL), Action.VIEW).allowed)


def services(path, principal=None):
    engine = SqliteEngine(path)
    repository = DurableShellRepository(TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT))
    state = CurrentState(principal)
    return engine, repository, state, ShellService(state, repository=repository)


def save_form(service):
    return service.franchisee_report(category="equipment", message="Actual saved machine repair result",
        subject_id="actual-franchisee", store_id=STORE, correlation_id="real-form-correlation", idempotency_key="actual-report-key")


def test_saved_form_produces_current_scoped_supervisor_inbox_and_exact_source_after_restart(tmp_path):
    path = tmp_path / "owned-fran004.sqlite"
    engine, _, _, service = services(path)
    saved = save_form(service)
    repeated = save_form(service)
    assert repeated["idempotentReplay"] is True
    assert repeated["report"]["reportId"] == saved["report"]["reportId"]
    inbox = service.get_notifications(role_id="field-lead", subject_id="actual-supervisor")
    assert len(inbox["items"]) == 1
    notice = inbox["items"][0]
    assert notice["notificationId"] == f"franchisee-report:{saved['report']['reportId']}"
    assert notice["sourceEntity"] == {"kind": "franchisee.report", "id": saved["report"]["reportId"]}
    assert notice["occurredAt"] == saved["report"]["createdAt"]
    assert notice["sourceHref"] == f"/notifications/reports/{saved['report']['reportId']}"
    assert notice["acknowledged"] is False
    source = service.get_supervisor_report(saved["report"]["reportId"])
    assert source["report"] == saved["report"]
    assert source["delivery"]["email"] == "not_evidenced"
    engine.close()
    restarted, _, _, second = services(path)
    try:
        after = second.get_notifications(role_id="field-lead", subject_id="actual-supervisor")
        assert after["items"][0]["notificationId"] == notice["notificationId"]
        assert second.get_supervisor_report(saved["report"]["reportId"])["report"] == source["report"]
    finally:
        restarted.close()


@pytest.mark.parametrize("denial", ["role", "tenant", "store", "brand", "region", "anonymous"])
def test_display_supervisor_label_cannot_replace_actual_role_tenant_or_current_store_scope(tmp_path, denial):
    principal = Principal("actual-supervisor", frozenset({Role.REGIONAL_SUPERVISOR}), Scope(tenant_id=TENANT, store_ids=frozenset({STORE})))
    if denial == "role":
        principal = replace(principal, roles=frozenset({Role.OPERATIONS_MANAGER}))
    elif denial == "tenant":
        principal = replace(principal, scope=Scope(tenant_id="foreign-tenant"))
    elif denial == "store":
        principal = replace(principal, scope=Scope(tenant_id=TENANT, store_ids=frozenset({"different-store"})))
    elif denial == "brand":
        principal = replace(principal, scope=Scope(tenant_id=TENANT, brand_ids=frozenset({"different-brand"})))
    elif denial == "region":
        principal = replace(principal, scope=Scope(tenant_id=TENANT, region_ids=frozenset({"different-region"})))
    else:
        principal = Principal("anonymous", authenticated=False)
    engine, _, _, service = services(tmp_path / "denied.sqlite", principal)
    try:
        report = save_form(service)["report"]
        assert service.get_notifications(role_id="field-lead", subject_id="actual-supervisor")["items"] == []
        with pytest.raises(ShellNotFound):
            service.get_supervisor_report(report["reportId"])
        with pytest.raises(ShellNotFound):
            service.get_supervisor_report("missing-report")
    finally:
        engine.close()


@pytest.mark.parametrize("gap", ["audit missing", "actor mismatch", "invalid time", "unknown actor"])
def test_unproved_saved_form_never_becomes_a_fabricated_supervisor_notice(tmp_path, gap):
    engine, repo, state, service = services(tmp_path / "unproved.sqlite")
    try:
        saved = save_form(service)["report"]
        reports = repo.list_records(FRANCHISEE_REPORTS)
        events = repo.list_records(AUDIT_EVENTS)
        before = deepcopy((reports, events))
        if gap == "audit missing":
            events = []
        elif gap == "actor mismatch":
            reports[0]["subjectId"] = "unrelated-actor"
        elif gap == "invalid time":
            reports[0]["createdAt"] = "not-a-time"
        else:
            reports[0]["subjectId"] = "franchisee-unknown"
        result = state.admit_franchisee_reports(reports, events)
        assert result["records"] == []
        assert result["coverage"]["state"] == "partial"
        assert repo.list_records(FRANCHISEE_REPORTS) == before[0]
        assert repo.list_records(AUDIT_EVENTS) == before[1]
        assert saved["reportId"]
    finally:
        engine.close()


def test_missing_request_admission_is_unavailable_not_a_persona_authority_fallback(tmp_path):
    engine, _, _, service = services(tmp_path / "no-admission.sqlite")
    try:
        report = save_form(service)["report"]
        class Unbound:
            def resolve_role(self, **_):
                return {"id": "field-lead", "label": "Supervisor display", "allowedWorkspaces": ["store"]}
            def get_today(self, **_):
                return {"notifications": []}
        service._state = Unbound()  # exact missing-port negative; not a production owner substitution
        value = service.get_notifications(role_id="field-lead", subject_id="actual-supervisor")
        assert value["items"] == []
        assert value["meta"]["supervisorReportCoverage"]["state"] == "unavailable"
        with pytest.raises(OperatorLiveRepositoryError):
            service.get_supervisor_report(report["reportId"])
    finally:
        engine.close()
