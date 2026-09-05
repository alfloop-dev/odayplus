"""Focused contract tests for the Supervisor Runtime Release lease bridge."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import release_lease_integration as bridge
from common import validate_config

from delivery_toolchain.release.release_lease import (
    LeaseStateStore,
    generate_keypair,
    load_private_key,
)
from delivery_toolchain.release.release_manifest import compute_manifest_digest

NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=UTC)
CANDIDATE_SHA = "e" * 40
TASK_ID = "ODP-RELEASE-DEPLOY-001"
DEPENDENCY_ID = "ODP-RELEASE-MANIFEST-GATES-001"
RUN_ID = "33003734045"


def _manifest() -> dict:
    manifest = {
        "schema_version": 1,
        "release_id": "odp-20260904-001",
        "candidate_sha": CANDIDATE_SHA,
        "components": {
            component: {
                "image": f"ghcr.io/example/{component}@sha256:" + digest * 64,
            }
            for component, digest in {
                "api": "1",
                "web": "2",
                "worker": "3",
                "scheduler": "4",
            }.items()
        },
        "migration_digest": "sha256:" + "a" * 64,
        "data_contract_digest": "sha256:" + "b" * 64,
        "source_policy_digest": "sha256:" + "c" * 64,
        "external_sources_expected_enabled": [],
        "sbom_refs": ["oci://ghcr.io/example/sbom@sha256:" + "7" * 64],
        "signature_refs": ["oci://ghcr.io/example/sig@sha256:" + "8" * 64],
        "created_at": "2026-09-04T11:00:00+00:00",
        "created_by_workflow": "github://example/actions/runtime-release.yml/run-33003734045",
    }
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def _registry(manifest: dict) -> dict:
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": CANDIDATE_SHA,
            "manifest_digest": manifest["manifest_digest"],
            "stage": "candidate-built",
            "environment": "dev",
            "admission_target": "dev",
            "decision": "go",
        },
        "candidate_rebind": {
            "to_candidate_sha": CANDIDATE_SHA,
            "to_manifest_digest": manifest["manifest_digest"],
            "build_run": {
                "run_id": int(RUN_ID),
                "event": "workflow_dispatch",
                "phase": "build",
                "conclusion": "success",
            },
        },
        "gates": [
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": CANDIDATE_SHA,
                "stage": "candidate-built",
                "environment": "dev",
                "admission_target": "dev",
                "receipts": [
                    {
                        "receipt_id": f"receipt-{index}",
                        "release_sha": CANDIDATE_SHA,
                        "result": "pass",
                    }
                ],
            }
            for index in range(7)
        ],
    }


def _request(manifest: dict, *, nonce: str = "one-time-human-approval") -> dict:
    return {
        "kind": "runtime_release_deploy",
        "status": "approved",
        "task_id": TASK_ID,
        "approved_by": "Human/Ops",
        "approval_id": "approval-20260904-001",
        "nonce": nonce,
        "candidate_sha": CANDIDATE_SHA,
        "manifest_digest": manifest["manifest_digest"],
        "target_environment": "dev",
        "action": "deploy",
        "manifest_run_id": RUN_ID,
        "approved_at": "2026-09-04T11:59:00+00:00",
        "expires_at": "2026-09-04T12:05:00+00:00",
    }


def _status(request: dict) -> dict:
    return {
        "tasks": [
            {"id": DEPENDENCY_ID, "status": "done"},
            {
                "id": TASK_ID,
                "task_class": "runtime_release",
                "status": "in_progress",
                "depends_on": [DEPENDENCY_ID],
                bridge.REQUEST_FIELD: request,
            },
        ],
        "blockers": [],
    }


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    manifest = _manifest()
    registry = _registry(manifest)
    request = _request(manifest)
    status = _status(request)
    evidence = tmp_path / "docs/evidence/gates"
    evidence.mkdir(parents=True)
    (evidence / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (evidence / "RELEASE_GATE_REGISTRY.json").write_text(json.dumps(registry), encoding="utf-8")
    status_path = tmp_path / "ai-status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    activity_path = tmp_path / "ai-activity-log.jsonl"
    state_dir = tmp_path / "durable-state"
    state_dir.mkdir()
    store = LeaseStateStore(state_dir, require_existing=True)
    key_path = tmp_path / "test-only-private-key.pem"
    private_pem, _ = generate_keypair()
    key_path.write_bytes(private_pem)
    private_key = load_private_key(key_path=key_path)
    key_path.unlink()
    config = {
        "paths": {
            "status_file": str(status_path),
            "activity_log": str(activity_path),
        },
        "release_lease_issuer": {
            "enabled": True,
            # Deliberately not the production project: the reference is public
            # config and the validator must permit an authorised replacement.
            "secret_reference": "projects/999999999999/secrets/test-release-lease-key",
            "state_uri": "gs://unit-test-existing-bucket/release-leases",
            "github_repository": "example/odayplus",
            "workflow": ".github/workflows/deploy-dev.yml",
            "ttl_seconds": 300,
        },
    }

    def commit(_: dict, candidate: dict) -> bool:
        status_path.write_text(json.dumps(candidate), encoding="utf-8")
        return True

    monkeypatch.setattr(
        bridge,
        "LeaseStateStore",
        lambda uri, *, require_existing: store,
    )
    return {
        "root": tmp_path,
        "manifest": manifest,
        "registry": registry,
        "request": request,
        "status_path": status_path,
        "activity_path": activity_path,
        "store": store,
        "private_key": private_key,
        "config": config,
        "commit": commit,
    }


def _read_status(harness: dict) -> dict:
    return json.loads(harness["status_path"].read_text(encoding="utf-8"))


def _set_task_status(harness: dict, task_id: str, status: str) -> None:
    current = _read_status(harness)
    for task in current["tasks"]:
        if task["id"] == task_id:
            task["status"] = status
            harness["status_path"].write_text(json.dumps(current), encoding="utf-8")
            return
    raise AssertionError(f"missing task {task_id}")


def _run(harness: dict, dispatch, *, loader=None) -> bool:
    return bridge.process_release_lease_issuance(
        harness["config"],
        commit_status=harness["commit"],
        private_key_loader=loader or (lambda _: harness["private_key"]),
        dispatch=dispatch,
        now=NOW,
    )


def test_disabled_config_is_inert_without_secret_gcs_or_dispatch(harness: dict) -> None:
    harness["config"]["release_lease_issuer"]["enabled"] = False
    calls: list[str] = []

    assert not _run(
        harness,
        lambda **_: calls.append("dispatch"),
        loader=lambda _: pytest.fail("disabled issuer must not load a signing key"),
    )
    assert calls == []
    assert _read_status(harness)["tasks"][1].get(bridge.ISSUANCE_FIELD) is None


def test_issues_cas_receipt_then_dispatches_existing_runtime_release(harness: dict) -> None:
    calls: list[dict] = []

    assert _run(harness, lambda **kwargs: calls.append(kwargs))
    assert len(calls) == 1
    lease = calls[0]["lease"]
    assert lease["candidate_sha"] == CANDIDATE_SHA
    assert calls[0]["request"]["manifest_run_id"] == RUN_ID
    assert harness["store"].get(lease["lease_id"])["state"] == "issued"

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "dispatched"
    assert record["dispatch"] == "accepted"
    serialized = json.dumps(record, sort_keys=True)
    assert harness["request"]["nonce"] not in serialized
    assert lease["signature"]["value"] not in serialized
    assert record["receipt"]["lease_id"] == lease["lease_id"]
    assert record["receipt"]["nonce_digest"].startswith("sha256:")

    activity = harness["activity_path"].read_text(encoding="utf-8")
    assert harness["request"]["nonce"] not in activity
    assert lease["signature"]["value"] not in activity
    assert "release_lease_runtime_release_dispatched" in activity


@pytest.mark.parametrize(
    "mutate, loader_error, expected",
    [
        (
            lambda harness: harness["registry"]["candidate_rebind"]["build_run"].update({"run_id": 123}),
            False,
            "manifest_run_id does not match candidate_rebind.build_run.run_id",
        ),
        (
            lambda harness: _set_task_status(harness, DEPENDENCY_ID, "in_progress"),
            False,
            "expected 'done'",
        ),
        (lambda harness: None, True, "Secret Manager signing key is unavailable"),
    ],
)
def test_precondition_or_secret_failure_records_secret_free_block_and_never_dispatches(
    harness: dict, mutate, loader_error: bool, expected: str
) -> None:
    mutate(harness)
    if "run_id" in harness["registry"]["candidate_rebind"]["build_run"]:
        evidence = harness["root"] / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
        evidence.write_text(json.dumps(harness["registry"]), encoding="utf-8")

    dispatched: list[dict] = []
    loader = (lambda _: (_ for _ in ()).throw(RuntimeError("do not expose secrets"))) if loader_error else None
    assert _run(harness, lambda **kwargs: dispatched.append(kwargs), loader=loader)
    assert dispatched == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any(expected in error for error in record["receipt"]["errors"])
    serialized = json.dumps(record, sort_keys=True)
    assert harness["request"]["nonce"] not in serialized
    assert "do not expose secrets" not in serialized


def test_archived_nonce_replay_blocks_before_key_or_dispatch(harness: dict) -> None:
    archive = harness["root"] / "ai-task-archive/tasks"
    archive.mkdir(parents=True)
    reused = {
        "id": "ODP-ARCHIVED-RELEASE-001",
        bridge.ISSUANCE_FIELD: {
            "approval_nonce_digest": bridge._safe_digest(harness["request"]["nonce"]),
            "request_fingerprint": "sha256:" + "0" * 64,
        },
    }
    (archive / "ODP-ARCHIVED-RELEASE-001.json").write_text(
        json.dumps({"task": reused, "terminal_status": "done"}), encoding="utf-8"
    )
    dispatched: list[dict] = []

    assert _run(
        harness,
        lambda **kwargs: dispatched.append(kwargs),
        loader=lambda _: pytest.fail("archived nonce replay must stop before Secret Manager"),
    )
    assert dispatched == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("archived issuance" in error for error in record["receipt"]["errors"])


def test_same_fingerprint_block_is_not_written_or_logged_again(harness: dict) -> None:
    _set_task_status(harness, TASK_ID, "blocked")
    assert _run(harness, lambda **_: pytest.fail("blocked task cannot dispatch"))
    first_status = harness["status_path"].read_text(encoding="utf-8")
    first_activity = harness["activity_path"].read_text(encoding="utf-8")

    assert not _run(harness, lambda **_: pytest.fail("same blocked fingerprint cannot dispatch"))
    assert harness["status_path"].read_text(encoding="utf-8") == first_status
    assert harness["activity_path"].read_text(encoding="utf-8") == first_activity


def test_dispatch_failure_is_terminal_for_the_issued_nonce(harness: dict) -> None:
    key_loads: list[str] = []
    dispatches: list[str] = []

    def loader(reference: str):
        key_loads.append(reference)
        return harness["private_key"]

    def failing_dispatch(**_: object) -> None:
        dispatches.append("attempted")
        raise bridge.RuntimeReleaseDispatchError("unconfirmed")

    assert _run(harness, failing_dispatch, loader=loader)
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "dispatch_unknown"
    assert record["dispatch"] == "not_confirmed"
    assert len(key_loads) == 1
    assert dispatches == ["attempted"]
    assert harness["store"].get(record["receipt"]["lease_id"])["state"] == "issued"

    assert not _run(
        harness,
        lambda **_: pytest.fail("dispatch_unknown approval must never dispatch again"),
        loader=lambda _: pytest.fail("dispatch_unknown approval must never reload the key"),
    )
    assert len(key_loads) == 1
    assert dispatches == ["attempted"]


def test_malformed_archive_blocks_before_key_or_dispatch(harness: dict) -> None:
    archive = harness["root"] / "ai-task-archive/tasks"
    archive.mkdir(parents=True)
    (archive / "broken-snapshot.json").write_text("{not-json", encoding="utf-8")
    dispatched: list[dict] = []

    assert _run(
        harness,
        lambda **kwargs: dispatched.append(kwargs),
        loader=lambda _: pytest.fail("archive failure must stop before Secret Manager"),
    )
    assert dispatched == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("unreadable task snapshot" in error for error in record["receipt"]["errors"])


def test_gh_dispatch_uses_stdin_json_and_static_workflow_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    request = _request(manifest)
    lease = {
        "candidate_sha": CANDIDATE_SHA,
        "manifest_digest": manifest["manifest_digest"],
        "task_id": TASK_ID,
    }
    captured: dict = {}

    class Result:
        returncode = 0

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    bridge.dispatch_runtime_release(
        lease=lease,
        request=request,
        manifest=manifest,
        settings={"github_repository": "example/odayplus"},
    )
    command = captured["command"]
    assert command[-2:] == ["--input", "-"]
    assert command[4] == "repos/example/odayplus/actions/workflows/deploy-dev.yml/dispatches"
    assert "release_lease" not in " ".join(command)
    assert "stdin" not in captured["kwargs"]
    payload = json.loads(captured["kwargs"]["input"].decode("utf-8"))
    assert payload["ref"] == CANDIDATE_SHA
    assert payload["inputs"]["phase"] == "deploy"
    assert json.loads(base64.b64decode(payload["inputs"]["release_lease"])) == lease


def test_public_example_stays_disabled_and_workflow_has_no_issuer_secret() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / ".orchestrator/config.example.json").read_text(encoding="utf-8"))
    assert config["release_lease_issuer"]["enabled"] is False
    assert config["release_lease_issuer"]["secret_reference"] == bridge.DEFAULT_SECRET_REFERENCE
    assert validate_config(config, source="focused test public example") == config
    workflow = (root / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    assert bridge.DEFAULT_SECRET_REFERENCE not in workflow
    assert "odp-release-lease-private-key" not in workflow
