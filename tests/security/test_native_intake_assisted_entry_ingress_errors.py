"""Classify bounded command conflicts without weakening input/storage errors."""

from datetime import UTC

import pytest
from fastapi import HTTPException
from psycopg import errors

from apps.api.app.routes.canonical_intakes import _assisted_errors, _recheck_errors
from apps.api.app.routes.canonical_match_cases import _http_errors
from modules.listing.application.native_intake_source_policy import NativeIntakeSourcePolicyConflict
from shared.audit.errors import CanonicalAuditUnavailable
from shared.jobs.queue import JobFenceRejectedError


@pytest.mark.parametrize("error", [NativeIntakeSourcePolicyConflict("NATIVE_SOURCE_POLICY_ADMISSION_DEADLINE_EXPIRED"),
    JobFenceRejectedError("synthetic fence conflict"), errors.SerializationFailure(), errors.DeadlockDetected(),
    errors.LockNotAvailable(), errors.QueryCanceled()])
def test_statement_and_between_statement_conflicts_both_return409(error):
    with pytest.raises(HTTPException) as raised, _http_errors(), _assisted_errors():
        raise error
    assert raised.value.status_code == 409


@pytest.mark.parametrize("explicit_cause", [False, True])
@pytest.mark.parametrize("driver_error", [errors.SerializationFailure, errors.QueryCanceled])
def test_audit_wrappers_preserve_both_explicit_and_implicit_sql_conflicts(explicit_cause, driver_error):
    with pytest.raises(HTTPException) as raised, _http_errors(), _assisted_errors():
        try:
            raise driver_error("synthetic SQL conflict")
        except Exception as error:
            if explicit_cause:
                raise CanonicalAuditUnavailable("synthetic unavailable") from error
            # Suppressed display still retains __context__ and no __cause__.
            raise CanonicalAuditUnavailable("synthetic unavailable") from None
    assert raised.value.status_code == 409


@pytest.mark.parametrize("error,status", [(errors.UndefinedTable(), 503), (errors.UndefinedColumn(), 503),
    (errors.InvalidSchemaName(), 503), (ValueError("INVALID_SYNTHETIC_INPUT"), 422),
    (PermissionError("synthetic denied"), 403), (CanonicalAuditUnavailable("synthetic actual outage"), 503)])
def test_actual_storage_authority_and_input_errors_keep_their_own_semantics(error, status):
    with pytest.raises(HTTPException) as raised, _http_errors(), _assisted_errors():
        raise error
    assert raised.value.status_code == status


def test_a_cyclic_context_does_not_hang_the_bounded_cause_walk():
    error = RuntimeError("synthetic unknown error")
    error.__context__ = error
    with pytest.raises(RuntimeError) as raised, _assisted_errors():
        raise error
    assert raised.value is error


@pytest.mark.parametrize("kind,operation,prefix", [
    ("ASSISTED_ENTRY", "withdrawCanonicalIntakeAssistedEntryRequest", "NATIVE_ASSISTED_"),
    ("SOURCE_POLICY_RECHECK", "withdrawCanonicalIntakeSourcePolicyRecheckRequest", "NATIVE_RECHECK_"),
])
@pytest.mark.parametrize("error_class,status,suffix", [
    (PermissionError, 403, "CURRENT_AUTHORITY_DENIED"),
    (JobFenceRejectedError, 409, "CURRENT_PROOF_CONFLICT"),
    (errors.SerializationFailure, 409, "CONCURRENT_CHANGE"),
])
def test_actual_withdrawal_route_uses_its_command_family_error_codes(monkeypatch, kind, operation, prefix,
        error_class, status, suffix):
    """Actual router mapping with a failing owner seam; no signed HTTP or SQL claim."""
    from types import SimpleNamespace
    from uuid import UUID

    from fastapi import Response

    from apps.api.app.routes import canonical_intakes as routes

    tenant = "11111111-1111-4111-8111-111111111111"
    actor = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(routes, "principal_from_headers", lambda _: SimpleNamespace(
        authenticated=True, tenant_id=tenant, subject_id=actor))
    engine = SimpleNamespace(dialect="postgresql")
    store = SimpleNamespace(_engine=engine)
    bundle = SimpleNamespace(engine=engine, assisted_intake_store=store,
        canonical_intakes=lambda: SimpleNamespace(graph=object()))
    request = SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace(persistence_bundle=bundle)))
    calls = []

    def cancel(*_, **kwargs):
        calls.append(kwargs["request_kind"])
        raise error_class("synthetic current owner denial")

    monkeypatch.setattr(routes, "NativeIntakeCommandWithdrawalOwner", lambda _: SimpleNamespace(cancel=cancel))
    router = routes.create_canonical_intakes_router(store)
    endpoint = next(route.endpoint for route in router.routes if route.operation_id == operation)
    with pytest.raises(HTTPException) as raised:
        endpoint(UUID(tenant), UUID(actor), SimpleNamespace(reason="Synthetic withdrawal"), request,
            Response(), "synthetic-withdrawal-key", 'W/"7"')
    assert calls == [kind]
    assert (raised.value.status_code, raised.value.detail) == (status, prefix + suffix)


@pytest.mark.parametrize("driver_error", [errors.SerializationFailure, errors.DeadlockDetected,
    errors.LockNotAvailable, errors.QueryCanceled])
@pytest.mark.parametrize("wrapper", ["direct", "implicit", "explicit"])
def test_recheck_pending_guard_conflicts_preserve409_through_each_audit_wrapper(driver_error, wrapper):
    with pytest.raises(HTTPException) as raised, _http_errors(), _recheck_errors():
        try:
            raise driver_error("synthetic pending-guard conflict")
        except Exception as error:
            if wrapper == "direct":
                raise
            if wrapper == "explicit":
                raise CanonicalAuditUnavailable("synthetic wrapper") from error
            raise CanonicalAuditUnavailable("synthetic wrapper") from None
    assert raised.value.status_code == 409


def test_recheck_cyclic_context_preserves_the_unknown_failure_without_hanging():
    error = RuntimeError("synthetic unknown recheck failure")
    error.__context__ = error
    with pytest.raises(RuntimeError) as raised, _recheck_errors():
        raise error
    assert raised.value is error


def test_memory_proof_checks_share_the_original_sql_deadline_without_sql_roundtrips(monkeypatch):
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from modules.listing.application import native_intake_pending_commands as pending

    ticks, sql_boundaries = [10.0], []
    observed = datetime(2026, 10, 16, tzinfo=UTC)
    monkeypatch.setattr(pending, "time", SimpleNamespace(monotonic=lambda: ticks[0]))

    def clock():
        ticks[0] += 0.050  # The initial database clock query consumes the budget.
        return observed

    budget = pending.NativeIntakeCommandBudget(SimpleNamespace(
        _clock=clock, _admission_budget=sql_boundaries.append))
    assert budget.deadline <= observed + timedelta(milliseconds=700)
    budget()
    ticks[0] = 10.4
    for _ in range(123):
        budget.check_in_memory()
    assert sql_boundaries == [budget.deadline]
    budget()
    assert sql_boundaries == [budget.deadline, budget.deadline]
    ticks[0] = 10.751
    with pytest.raises(pending.NativeAssistedEntryConflict, match="ROOT_DEADLINE_EXPIRED"):
        budget.check_in_memory()
    with pytest.raises(pending.NativeAssistedEntryConflict, match="ROOT_DEADLINE_EXPIRED"):
        budget()
    assert sql_boundaries == [budget.deadline, budget.deadline]


@pytest.mark.parametrize("phase", ["initial_clock", "between_callbacks", "during_sql"])
def test_command_budget_expires_across_initial_clock_and_sql_boundaries(monkeypatch, phase):
    from datetime import datetime
    from types import SimpleNamespace

    from modules.listing.application import native_intake_pending_commands as pending

    ticks = [0.0]
    monkeypatch.setattr(pending, "time", SimpleNamespace(monotonic=lambda: ticks[0]))

    def clock():
        if phase == "initial_clock":
            ticks[0] = 0.751
        return datetime(2026, 10, 16, tzinfo=UTC)

    def sql_boundary(deadline):
        if phase == "during_sql":
            ticks[0] = 0.751

    source = SimpleNamespace(_clock=clock, _admission_budget=sql_boundary)
    with pytest.raises(pending.NativeAssistedEntryConflict, match="ROOT_DEADLINE_EXPIRED"):
        budget = pending.NativeIntakeCommandBudget(source)
        if phase == "between_callbacks":
            budget()
            ticks[0] = 0.751
        budget()


@pytest.mark.parametrize("errors_context", [_assisted_errors, _recheck_errors])
def test_the_shared_root_budget_conflict_keeps_both_command_endpoints_at409(errors_context):
    from modules.listing.application.native_intake_assisted_entry_policy import (
        NativeAssistedEntryConflict,
    )

    with pytest.raises(HTTPException) as raised, _http_errors(), errors_context():
        raise NativeAssistedEntryConflict("NATIVE_COMMAND_ROOT_DEADLINE_EXPIRED")
    assert raised.value.status_code == 409


@pytest.mark.parametrize("defect", ["tenant_nil", "job_id_mismatch", "extra_key", "zero_generation",
    "leading_zero", "large_generation", "foreign_type"])
def test_malformed_negative_delivery_never_opens_a_sql_transaction(defect):
    from types import SimpleNamespace

    from modules.listing.application.native_intake_recheck_settlement import NativeRecheckSettlement

    job_id = "65bdce0e-40ae-4c81-8aef-f633746e31b7"
    data = {"job_id": job_id, "tenant_id": "a510c8a7-7a93-4d80-8ae9-25a405c65ec1", "attempt_token": "1." + "a" * 64}
    delivery = SimpleNamespace(job_id=job_id, job_type="native-intake-source-policy-recheck", payload=data)
    if defect == "tenant_nil":
        data["tenant_id"] = "00000000-0000-0000-0000-000000000000"
    elif defect == "job_id_mismatch":
        data["job_id"] = "5cd51516-20f8-4c69-838e-1936589756ba"
    elif defect == "extra_key":
        data["actor_role"] = "SVC_INTAKE"
    elif defect == "foreign_type":
        delivery.job_type = "native-intake-source-policy"
    else:
        prefix = {"zero_generation": "0", "leading_zero": "01", "large_generation": str(2**63)}[defect]
        data["attempt_token"] = prefix + "." + "a" * 64

    def transaction(*args, **kwargs):
        raise AssertionError("Malformed routing fields must fail before opening an SQL root")

    owner = SimpleNamespace(graph=SimpleNamespace(transaction=transaction),
        engine=SimpleNamespace(), queue=SimpleNamespace(), source=SimpleNamespace())
    with pytest.raises(JobFenceRejectedError, match="NEGATIVE_IDENTITY_REJECTED"):
        NativeRecheckSettlement(owner).settle(delivery, RuntimeError("synthetic invalid delivery"))


@pytest.mark.parametrize("authenticated,expected_denial", [(False, 401), (True, 403)])
def test_actual_role_authorizer_denial_is_not_a_retry_grant(authenticated, expected_denial):
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from modules.listing.application.intake_authorization import authorize_intake_action
    from modules.listing.application.native_intake_recheck_settlement import NativeRecheckSettlement
    from modules.listing.application.native_intake_source_policy_recheck import (
        NativeIntakeSourcePolicyRecheckOwner,
    )
    from shared.auth import Principal, Role, Scope

    tenant, subject = "a510c8a7-7a93-4d80-8ae9-25a405c65ec1", "65bdce0e-40ae-4c81-8aef-f633746e31b7"
    now = datetime(2026, 10, 16, tzinfo=UTC)
    intake = {"tenant_id": tenant, "processing_state": "AWAITING_SOURCE_AUTHORIZATION",
        "source_policy_state": "AUTH_REQUIRED", "version": 3, "deleted_at": None,
        "resolved_listing_id": None, "completed_at": None, "submitter": subject}
    payload = {"expected_intake_version": 3, "requested_by": subject, "input_sha256": "synthetic-original-input",
        "policy": {"backoff_seconds": [1]}}
    observed = []

    def authority(requester, resource, before):
        assert requester == subject
        principal = Principal(subject_id=subject,
            roles=frozenset({Role.EXPANSION_USER if authenticated else Role.DATA_OWNER}),
            scope=Scope(tenant_id=tenant, modules=frozenset({"listing"})), authenticated=authenticated)
        try:
            authorize_intake_action(principal, "recheck_source_policy", resource=resource)
        except HTTPException as denial:
            observed.append(denial.status_code)
            raise

    owner = SimpleNamespace(graph=SimpleNamespace(), engine=SimpleNamespace(), queue=SimpleNamespace(),
        source=SimpleNamespace(_input_digest=lambda resource: "synthetic-original-input"),
        _waiting_context=NativeIntakeSourcePolicyRecheckOwner._waiting_context, _authority=authority)
    due = NativeRecheckSettlement(owner)._retry_due(intake, payload,
        {"timeout_at": now + timedelta(seconds=10)}, 1, now, lambda: None)
    assert due is None and observed == [expected_denial]
