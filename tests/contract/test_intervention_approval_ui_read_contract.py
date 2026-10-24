"""Native read permissions and audited readback for the full UI owner.

These controls use owned SQLite and the existing local Principal test adapter;
they exercise actual route dependencies and commands, without browser/PG claims.
"""
from dataclasses import replace

import pytest

from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.auth import Role
from tests.contract.test_intervention_approval_authority import (
    CREATOR,
    REVIEWER,
    business_state,
    command,
    headers,
    pending,
    post,
    read,
)
from tests.contract.test_intervention_approval_authority import (
    owned as native_owned,
)


@pytest.fixture
def owned(tmp_path, monkeypatch):
    yield from native_owned.__wrapped__(tmp_path, monkeypatch)


def test_reviewer_read_permission_matches_actual_approve(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    before_state = business_state(engine, workflow, iid)
    snapshot = read(client, iid)
    assert snapshot['approval_permissions'] == {
        'approve': True, 'reject': True, 'approve_refusal': None, 'reject_refusal': None,
    }
    assert snapshot['feature_version'] == 'intervention-feature-v1'
    assert snapshot['read_at'] and snapshot['approval_audit'] is None
    assert business_state(engine, workflow, iid) == before_state
    body, hdr = command(client, iid)
    hdr['X-Correlation-Id'] = 'synthetic-ui-native-audit'
    response = post(client, iid, body, hdr)
    assert response.status_code == 200, response.text
    after = read(client, iid)
    proof = after['approval_audit']
    assert proof['event_id'] == response.json()['audit_event_id']
    assert proof['actor'] == REVIEWER and proof['reason'] == body['reason']
    assert proof['correlation_id'] == response.json()['correlation_id'] == hdr['X-Correlation-Id']
    assert after['approval']['approved_at'] == response.json()['approval']['approved_at']
    assert after['approval_permissions']['approve'] is False
    assert after['approval_permissions']['reject'] is False


def test_creator_cannot_approve_but_can_reject_matching_post(owned):
    _, client, _, _ = owned
    iid = pending(client)
    projection = read(client, iid, CREATOR)['approval_permissions']
    assert projection == {'approve': False, 'reject': True,
        'approve_refusal': 'CREATOR_APPROVAL_FORBIDDEN', 'reject_refusal': None}
    body, hdr = command(client, iid, CREATOR)
    assert post(client, iid, body, hdr).status_code == 403
    body['action'] = 'REJECT'
    response = post(client, iid, body, hdr)
    assert response.status_code == 200, response.text
    assert read(client, iid, CREATOR)['approval']['approved'] is False


def test_view_only_regional_principal_has_no_ui_command_grant(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    state = business_state(engine, workflow, iid)
    hdr = headers(**{'x-roles': Role.REGIONAL_SUPERVISOR.value})
    response = client.get(f'/api/v1/interventions/{iid}', headers=hdr)
    assert response.status_code == 200, response.text
    permissions = response.json()['approval_permissions']
    assert not permissions['approve'] and not permissions['reject']
    assert permissions['approve_refusal'] == 'APPROVAL_PERMISSION_REQUIRED'
    body, approved_headers = command(client, iid)
    approved_headers['x-roles'] = Role.REGIONAL_SUPERVISOR.value
    assert post(client, iid, body, approved_headers).status_code == 403
    assert business_state(engine, workflow, iid) == state


@pytest.mark.parametrize('role', [Role.MARKETING_MANAGER, Role.AUDITOR])
def test_unsupported_view_roles_receive_no_intervention_detail(role, owned):
    _, client, _, _ = owned
    iid = pending(client)
    assert client.get(f'/api/v1/interventions/{iid}', headers=headers(**{'x-roles': role.value})).status_code == 403


def test_conflict_blocks_approve_projection_and_post_but_not_reject(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    workflow.repository.save(replace(current, version=current.version+1,
        conflict=replace(current.conflict, has_conflict=True, resolved=False)))
    snapshot = read(client, iid)
    assert snapshot['approval_permissions'] == {'approve': False, 'reject': True,
        'approve_refusal': 'UNRESOLVED_CONFLICT', 'reject_refusal': None}
    before = business_state(engine, workflow, iid)
    body, hdr = command(client, iid)
    assert post(client, iid, body, hdr).status_code == 422
    assert business_state(engine, workflow, iid) == before
    body['action'] = 'REJECT'
    assert post(client, iid, body, hdr).status_code == 200


def test_today_native_owner_targets_do_not_grant_unrelated_personas(owned):
    _, client, workflow, _ = owned
    iid = pending(client)
    case = workflow.get(iid)
    repository = object.__new__(OperatorLiveRepository)
    for row in [repository._intervention_tasks([case])[0], repository._intervention_approvals([case])[0]]:
        assert row['target'] == {'workspace': 'today', 'entityId': iid, 'tab': 'interventions'}
        assert row['roles'] == ['ops-lead']


def test_read_capability_applies_exact_native_high_risk_session_requirement(owned, monkeypatch):
    from types import SimpleNamespace

    from apps.api.oday_api.security import dependencies
    engine, client, workflow, _ = owned
    iid = pending(client)
    state = business_state(engine, workflow, iid)
    # Declared Principal/session adapters isolate the existing route dependency;
    # this is not a signed production session or account grant.
    principal = dependencies._principal_from_trusted_headers(headers())
    monkeypatch.setattr(dependencies, 'principal_from_headers', lambda *args, **kwargs: principal)
    monkeypatch.setattr(dependencies, 'default_boundary', lambda: SimpleNamespace(
        _session_service=SimpleNamespace(validate_session=lambda sid: None)))
    snapshot = client.get(f'/api/v1/interventions/{iid}', headers=headers())
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()['approval_permissions'] == {
        'approve': False, 'reject': False,
        'approve_refusal': 'APPROVAL_SESSION_REQUIRED', 'reject_refusal': 'APPROVAL_SESSION_REQUIRED',
    }
    assert client.post(f'/api/v1/interventions/{iid}/approve', headers=headers(),
        json={'action': 'APPROVE'}).status_code == 401
    assert business_state(engine, workflow, iid) == state


def test_current_session_native_reviewer_permission_matches_successful_command(owned, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from apps.api.oday_api.security import dependencies
    _, client, _, _ = owned
    iid = pending(client)
    native = dependencies._principal_from_trusted_headers(headers())
    principal = replace(native, attributes={**native.attributes, 'sid': str(uuid4())})
    monkeypatch.setattr(dependencies, 'principal_from_headers', lambda *args, **kwargs: principal)
    active = {'valid': True}
    monkeypatch.setattr(dependencies, 'default_boundary', lambda: SimpleNamespace(
        _session_service=SimpleNamespace(validate_session=lambda sid: object() if active['valid'] else None)))
    snapshot = read(client, iid)
    assert snapshot['approval_permissions']['approve'] is True
    body, hdr = command(client, iid)
    assert post(client, iid, body, hdr).status_code == 200
    active['valid'] = False
    assert client.get(f'/api/v1/interventions/{iid}', headers=headers()).status_code == 401


def test_creator_verification_and_new_approval_projection_leave_legacy_policy_pending(owned):
    from datetime import UTC, datetime, timedelta

    from tests.contract.test_intervention_approval_authority import STORE
    _, client, workflow, _ = owned
    start = datetime.now(UTC)
    case = workflow.open_case(store_id=STORE, kind='PRICE_CHANGE', trigger_ref='synthetic-legacy',
        expected_outcome='synthetic legacy review', planned_start=start, planned_end=start+timedelta(days=7), created_by=CREATOR)
    workflow.check_eligibility(case.intervention_id, eligible=True, actor=CREATOR)
    workflow.propose_action(case.intervention_id, action_spec={}, actor=CREATOR)
    workflow.check_conflict(case.intervention_id, actor=CREATOR)
    workflow.submit_for_approval(case.intervention_id, actor=CREATOR)
    result = read(client, case.intervention_id)
    assert result['creator_verification'] == 'unverified_legacy_record'
    assert result['approval_permissions'] == {'approve': False, 'reject': True,
        'approve_refusal': 'CREATOR_VERIFICATION_REQUIRED', 'reject_refusal': None}
    body, hdr = command(client, case.intervention_id)
    assert post(client, case.intervention_id, body, hdr).status_code == 409
