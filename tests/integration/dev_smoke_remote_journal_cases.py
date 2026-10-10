"""Cases imported by the exact declared test_dev_smoke_provisioning selection.

Actual production router/PG/auth, memory BFF and mocked GitHub/gate: NOT live
consent, secret staging, required CI, source approval or deployment evidence.
"""
from __future__ import annotations

import json
from typing import Any

import pytest


def _recorded_authorization(plan: Any) -> Any:
    from pathlib import Path
    from delivery_toolchain.release.provision_dev_smoke import RecordedUserAuthorization
    record = json.loads(Path(
        "support/handoffs/dev-smoke-principal-isolation-20261009/USER-AUTHORIZATION-20261010.json"
    ).read_text())
    # Explicit OFFLINE projection, not approval for these test IDs.
    record["approved_scope"]["tenant_id"] = plan["tenant_id"]
    record["approved_scope"]["preserve_existing_account_id"] = plan["actor_account_id"]
    return RecordedUserAuthorization(authorization=record, approved_plan=plan)


@pytest.mark.parametrize("changed", [None, "email", "execution_id", "recipient_custodian", "username"])
def test_existing_recorded_consent_needs_no_new_github_comment(
    foreground_plan_input: Any, changed: str | None,
) -> None:
    from copy import deepcopy
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    plan, context = foreground_plan_input
    consent = _recorded_authorization(plan)
    submitted = deepcopy(plan)
    if changed:
        submitted[changed] = "changed-private-input"
        with pytest.raises(ProvisioningRefused) as error:
            consent.observe(submitted, now=context["now"])
        assert "private" not in str(error.value)
    else:
        consent.observe(submitted, now=context["now"])
    assert not hasattr(consent, "_client")  # No social approval/SMTP ceremony.


@pytest.fixture
def remote_binding(encrypted_binding: Any, monkeypatch: Any) -> Any:
    import delivery_toolchain.release.provision_dev_smoke as module
    s, web, local, old, github, private, plan, args = encrypted_binding
    monkeypatch.setenv("ODP_DEPLOY_ENV", "dev")
    monkeypatch.setenv("ODP_RELEASE_PROFILE", "dev-admin")
    monkeypatch.setenv("ODAY_RELEASE_SHA", plan["release_sha"])
    monkeypatch.setenv("ODP_RELEASE_MANIFEST_DIGEST", plan["manifest_digest"])
    journal = module.RemoteProvisioningJournal(web=web, web_origin="https://web.example.invalid")
    lifecycle = module.WebInvitationExecutor(
        web=web, web_origin="https://web.example.invalid", journal=journal,
        admission=old._lifecycle._admission, source=old._lifecycle._source,
        custody=_recorded_authorization(plan),
    )
    binding = module.DevCredentialBundleExecutor(lifecycle=lifecycle, store=old._store)
    assert not hasattr(journal, "_engine") and not hasattr(journal, "_audit")
    return s, web, local, binding, github, private, plan, args


def test_foreground_remote_journal_composes_actual_router_binding_same_pair_and_gate(
    remote_binding: Any, monkeypatch: Any,
) -> None:
    import base64
    import subprocess
    from nacl.public import SealedBox
    from delivery_toolchain.e2e import check_live_e2e_gate as gate
    from delivery_toolchain.release.provision_dev_smoke import DevSmokeBindingJournal
    from tests.integration.test_dev_smoke_provisioning import _snapshot, _q, _foreground_gate_template
    s, web, local, binding, github, private, plan, args = remote_binding
    before = _snapshot(s)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("offline executor launched process"))

    def evaluate(config: Any, **kwargs: Any) -> Any:
        bundle = json.loads(SealedBox(private).decrypt(base64.b64decode(github.uploads[0]["encrypted_value"])))
        assert config.dev_admin_username == bundle["username"] == "odp-dev-smoke"
        assert config.dev_admin_password == bundle["password"] == args["new_password"]
        assert config.dev_admin_initial_password == config.bootstrap_admin_password == ""
        assert config.dev_admin_bundle_account_id == bundle["account_id"]
        assert DevSmokeBindingJournal(journal=local).inspect()["stage"] == "binding-acknowledged"
        return [gate.CheckResult(True, "offline-composition-only", "not-live-evidence")], {
            "ok": True, "expected_release_sha": config.expected_sha, "expected_deployment": "dev",
        }

    monkeypatch.setattr(gate, "evaluate_gate", evaluate)
    result = binding.execute_and_check_gate(
        plan, gate_config=_foreground_gate_template(binding, args), worker_job="offline-worker",
        gcp_region="asia-east1", gcp_project="offline-project", **args,
    )
    receipt = result["provisioning"]
    assert receipt["binding_write_acknowledged"] is True and receipt["live_gate_passed"] is True
    assert receipt["deployment_success"] is False and receipt["credential_binding_verified"] is False
    assert not web.custody_evidence.calls
    assert len(github.uploads) == 1 and local.inspect().stage == "reserved"
    assert binding._journal._cookie == "" and binding._journal._depth == 0
    assert _snapshot(s)["password_credentials"] == before["password_credentials"]
    assert all(existing in _snapshot(s)["sessions"] for existing in before["sessions"])
    assert _q(s, "SELECT count(*) FROM identity.sessions WHERE revoked_at IS NOT NULL") == [(3,)]
    output = json.dumps(result) + json.dumps([e.metadata for e in s.audit.list_events()])
    assert all(secret not in output for secret in (args["admin_password"], args["new_password"], plan["email"]))
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("action", ["reserve", "binding-intent", "binding-acknowledged"])
def test_remote_journal_lost_committed_reply_never_retries_or_replaces(
    remote_binding: Any, monkeypatch: Any, action: str,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused, RemoteProvisioningJournal
    from tests.integration.test_dev_smoke_provisioning import _q
    s, web, local, binding, github, _, plan, args = remote_binding
    original = web.request
    attempted: list[str] = []

    def uncertain(method: str, path: str, **kwargs: Any) -> Any:
        result = original(method, path, **kwargs)
        if method == "POST" and path == RemoteProvisioningJournal.PATH:
            step = kwargs["body"]["action"]
            attempted.append(step)
            if step == action:
                raise RuntimeError(args["new_password"])
        return result

    monkeypatch.setattr(web, "request", uncertain)
    with pytest.raises(ProvisioningRefused) as error:
        binding.execute(plan, **args)
    assert args["new_password"] not in str(error.value) and error.value.__cause__ is None
    assert attempted.count(action) == 1
    assert local.inspect().stage == ("reserved" if action == "reserve" else "recovery-required")
    assert len(github.uploads) == int(action == "binding-acknowledged")
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1 if action == "reserve" else 2,)]
    with pytest.raises(ProvisioningRefused):
        binding.execute(plan, **args)
    assert len(github.uploads) == int(action == "binding-acknowledged")
    assert web.calls.count(("POST", "/api/v1/operator/users/invitations")) == int(action != "reserve")
    assert s.audit.verify_chain().ok


@pytest.mark.parametrize("mode", ["anonymous", "header-forgery", "wrong-role", "revoked", "must-change", "foreign-admin"])
def test_remote_journal_actual_auth_guard_precedes_any_ledger_write(
    remote_binding: Any, monkeypatch: Any, mode: str,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningJournal, RemoteProvisioningJournal
    from tests.integration.test_dev_smoke_provisioning import _headers, _q, TENANT, OTHER_TENANT
    s, _, local, _, _, _, plan, _ = remote_binding
    headers = _headers(s)
    if mode == "anonymous":
        headers = {}
    elif mode == "header-forgery":
        headers = {"x-subject-id": s.admin, "x-tenant-id": TENANT, "x-roles": "platform_admin"}
    elif mode == "wrong-role":
        s.engine.execute("DELETE FROM identity.account_roles WHERE account_id = ? AND role = 'platform_admin'", (s.admin,))
    elif mode == "revoked":
        s.engine.execute("UPDATE identity.sessions SET revoked_at = now() WHERE account_id = ?", (s.admin,))
    elif mode == "must-change":
        s.engine.execute("UPDATE identity.password_credentials SET must_change = true WHERE account_id = ?", (s.admin,))
    else:
        s.engine.execute("UPDATE identity.accounts SET tenant_id = ? WHERE account_id = ?", (OTHER_TENANT, s.admin))
    monkeypatch.setattr(ProvisioningJournal, "reserve", lambda *a, **k: pytest.fail("journal mutated"))
    response = s.client.post(RemoteProvisioningJournal.PATH, headers=headers, json={"action": "reserve", "plan": plan})
    assert response.status_code in {401, 403, 409}
    assert local.inspect() is None and _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]


@pytest.mark.parametrize("fault", ["deployment", "profile", "source", "origin", "sha"])
def test_remote_gate_preflight_refuses_before_journal_session_login(
    remote_binding: Any, fault: str,
) -> None:
    from dataclasses import replace
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    from tests.integration.test_dev_smoke_provisioning import _foreground_gate_template
    _, web, local, binding, github, _, plan, args = remote_binding
    config = _foreground_gate_template(binding, args)
    fields = {"deployment": {"expected_deployment": "production"},
              "profile": {"release_profile": "full"},
              "source": {"external_provider_mode": "live"},
              "origin": {"web_url": "https://foreign.example.invalid"},
              "sha": {"expected_sha": "f" * 40}}
    with pytest.raises(ProvisioningRefused, match="PROVISIONING_GATE_CONFIG_INVALID"):
        binding.execute_and_check_gate(
            plan, gate_config=replace(config, **fields[fault]), worker_job="offline-worker",
            gcp_region="asia-east1", gcp_project="offline-project", **args,
        )
    assert not web.calls and not github.calls and local.inspect() is None


def test_remote_journal_server_audit_failure_rolls_back_reservation_without_issue(
    remote_binding: Any, monkeypatch: Any,
) -> None:
    from delivery_toolchain.release.provision_dev_smoke import ProvisioningRefused
    from tests.integration.test_dev_smoke_provisioning import _q
    s, web, local, binding, github, _, plan, args = remote_binding
    original = s.audit.record

    def fail(event: Any) -> Any:
        if event.event_type == "release.dev_smoke.reservation":
            raise RuntimeError(args["new_password"])
        return original(event)

    monkeypatch.setattr(s.audit, "record", fail)
    with pytest.raises(ProvisioningRefused) as error:
        binding.execute(plan, **args)
    assert args["new_password"] not in str(error.value)
    assert local.inspect() is None and not github.uploads
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
    assert not any(path == "/auth/invitations" for _, path in web.calls)


def test_remote_journal_reserve_uses_server_account_tuple_and_global_single_use(
    remote_binding: Any, monkeypatch: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from delivery_toolchain.release.provision_dev_smoke import RemoteProvisioningJournal
    from tests.integration.test_dev_smoke_provisioning import _headers, _q
    s, _, local, _, _, _, plan, _ = remote_binding
    headers = _headers(s)
    for body in ({"action": "reserve", "plan": plan, "original_account": {"password": "private-input"}},
                 {"action": "binding-intent", "account_id": s.admin,
                  "execution_id": plan["execution_id"], "plan_digest": "sha256:" + "b" * 64}):
        response = s.client.post(RemoteProvisioningJournal.PATH, headers=headers, json=body)
        assert response.status_code == 409 and "private" not in response.text
    monkeypatch.setenv("ODAY_RELEASE_SHA", "f" * 40)
    assert s.client.post(RemoteProvisioningJournal.PATH, headers=headers,
                         json={"action": "reserve", "plan": plan}).status_code == 409
    assert local.inspect() is None
    monkeypatch.setenv("ODAY_RELEASE_SHA", plan["release_sha"])

    def reserve(_: int) -> Any:
        return s.client.post(RemoteProvisioningJournal.PATH, headers=headers,
                             json={"action": "reserve", "plan": plan}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sorted(results) == [200, 409] and local.inspect().stage == "reserved"
    assert _q(s, "SELECT count(*) FROM identity.accounts") == [(1,)]
    assert _q(s, "SELECT count(*) FROM identity.invitations") == [(0,)]
