"""Focused contract tests for the Supervisor Runtime Release lease bridge."""

from __future__ import annotations

import base64
import copy
import json
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import release_lease_integration as bridge
import supervisor
from common import validate_config

from delivery_toolchain.release.release_lease import (
    LeaseStateError,
    LeaseStateStore,
    build_lease,
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
    cand_sha = manifest.get("candidate_sha") or CANDIDATE_SHA
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": cand_sha,
            "manifest_digest": manifest["manifest_digest"],
            "stage": "candidate-built",
            "environment": "dev",
            "admission_target": "dev",
            "decision": "go",
        },
        "candidate_rebind": {
            "to_candidate_sha": cand_sha,
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
                "release_sha": cand_sha,
                "stage": "candidate-built",
                "environment": "dev",
                "admission_target": "dev",
                "receipts": [
                    {
                        "receipt_id": f"receipt-{index}",
                        "release_sha": cand_sha,
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


def _run(harness: dict, dispatch, *, loader=None, public_loader=None, ref_resolver=None, now=NOW, clock=None) -> bool:
    return bridge.process_release_lease_issuance(
        harness["config"],
        commit_status=harness["commit"],
        private_key_loader=loader or (lambda _: harness["private_key"]),
        public_key_loader=public_loader or (lambda *_: harness["private_key"].public_key()),
        dispatch=dispatch,
        ref_resolver=ref_resolver
        or (lambda root, ref, *, repository=None: harness["request"]["candidate_sha"]),
        now=now,
        clock=clock,
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
            "required dependency expected 'done'",
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
        settings={"github_repository": "example/odayplus", "dispatch_ref": "dev"},
    )
    command = captured["command"]
    assert command[-2:] == ["--input", "-"]
    assert command[4] == "repos/example/odayplus/actions/workflows/deploy-dev.yml/dispatches"
    assert "release_lease" not in " ".join(command)
    assert "stdin" not in captured["kwargs"]
    payload = json.loads(captured["kwargs"]["input"].decode("utf-8"))
    assert payload["ref"] == "dev"
    assert payload["inputs"]["phase"] == "deploy"
    assert payload["inputs"]["environment"] == "dev"
    assert payload["inputs"]["release_sha"] == CANDIDATE_SHA
    assert payload["inputs"]["task_id"] == TASK_ID
    assert payload["inputs"]["manifest_run_id"] == RUN_ID
    assert payload["inputs"]["manifest_digest"] == manifest["manifest_digest"]
    for comp in ("api", "web", "worker", "scheduler"):
        assert f"{comp}_image" in payload["inputs"]
        assert payload["inputs"][f"{comp}_image"] == manifest["components"][comp]["image"]
    assert json.loads(base64.b64decode(payload["inputs"]["release_lease"])) == lease


def test_dispatch_ref_raw_sha_is_rejected_in_settings_and_never_dispatches(harness: dict) -> None:
    harness["config"]["release_lease_issuer"]["dispatch_ref"] = "a" * 40
    settings, errors = bridge.issuer_settings(harness["config"])
    assert settings is None
    assert any("not a commit SHA" in err for err in errors)

    assert not _run(
        harness,
        lambda **_: pytest.fail("raw SHA dispatch_ref must not dispatch"),
        loader=lambda _: pytest.fail("raw SHA dispatch_ref must not load key"),
    )
    activity = harness["activity_path"].read_text(encoding="utf-8")
    assert "release_lease_issuer_configuration_blocked" in activity


def test_dispatch_ref_invalid_characters_rejected(harness: dict) -> None:
    harness["config"]["release_lease_issuer"]["dispatch_ref"] = "bad ref with spaces"
    settings, errors = bridge.issuer_settings(harness["config"])
    assert settings is None
    assert any("not a valid git branch or tag reference" in err for err in errors)


def test_dispatch_ref_custom_branch_is_used_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
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
        settings={"github_repository": "example/odayplus", "dispatch_ref": "release/v1.0"},
    )
    payload = json.loads(captured["kwargs"]["input"].decode("utf-8"))
    assert payload["ref"] == "release/v1.0"


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")
    return repo, run_git


def test_dispatch_ref_ancestry_real_git_evidence_only_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, run_git = make_repo(tmp_path)
    (repo / "docs/evidence/gates").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("print('app')\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate base commit")
    cand_sha = run_git("rev-parse", "HEAD")

    manifest = _manifest()
    manifest["candidate_sha"] = cand_sha
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    registry = _registry(manifest)
    (repo / "docs/evidence/gates/RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json").write_text(json.dumps(registry), encoding="utf-8")
    run_git("add", ".")
    run_git("commit", "-m", "record evidence for candidate")
    dev_sha = run_git("rev-parse", "HEAD")

    request = _request(manifest)
    request["candidate_sha"] = cand_sha
    request["manifest_digest"] = manifest["manifest_digest"]
    status = _status(request)
    status_path = repo / "ai-status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    activity_path = repo / "ai-activity-log.jsonl"
    state_dir = repo / "durable-state"
    state_dir.mkdir()
    store = LeaseStateStore(state_dir, require_existing=True)
    private_pem, _ = generate_keypair()
    key_path = repo / "test-key.pem"
    key_path.write_bytes(private_pem)
    private_key = load_private_key(key_path=key_path)
    key_path.unlink()

    monkeypatch.setattr(bridge, "LeaseStateStore", lambda uri, *, require_existing: store)

    config = {
        "paths": {
            "status_file": str(status_path),
            "activity_log": str(activity_path),
        },
        "release_lease_issuer": {
            "enabled": True,
            "secret_reference": "projects/999999999999/secrets/test-release-lease-key",
            "state_uri": "gs://unit-test-existing-bucket/release-leases",
            "github_repository": "example/odayplus",
            "workflow": ".github/workflows/deploy-dev.yml",
            "ttl_seconds": 300,
            "dispatch_ref": "dev",
        },
    }

    dispatched: list[dict] = []
    assert bridge.process_release_lease_issuance(
        config,
        commit_status=lambda _, cand: status_path.write_text(json.dumps(cand), encoding="utf-8") or True,
        private_key_loader=lambda _: private_key,
        dispatch=lambda **kwargs: dispatched.append(kwargs),
        ref_resolver=lambda root, ref, *, repository=None: dev_sha,
        now=NOW,
    )
    assert len(dispatched) == 1
    assert dispatched[0]["lease"]["candidate_sha"] == cand_sha

    record = json.loads(status_path.read_text(encoding="utf-8"))["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "dispatched"
    assert record["dispatch_ref"] == "dev"
    assert record["dispatch_ref_sha"] == dev_sha
    assert record["receipt"]["dispatch_ref"] == "dev"
    assert record["receipt"]["dispatch_ref_sha"] == dev_sha


# ---------------------------------------------------------------------------
# ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001: `resolve_ref_sha` reads one
# remote and nothing else.
#
# These exercise real `git ls-remote` against a real repository. The configured
# repository's HTTPS URL is redirected with git's own `url.<base>.insteadOf`, so
# production code builds and runs exactly the command it runs in the Supervisor
# -- no network, no patched `subprocess`, and no test-only branch inside
# `resolve_ref_sha` for the tests to accidentally certify.

CONFIGURED_REPOSITORY = "example/odayplus"
CONFIGURED_REMOTE_URL = f"https://github.com/{CONFIGURED_REPOSITORY}.git"


def make_repo_with_remote(tmp_path: Path, *, remote_path: Path | None = None):
    """A work repo whose configured-repository URL points at a local bare repo."""

    repo, run_git = make_repo(tmp_path)
    remote = remote_path if remote_path is not None else tmp_path / "configured-remote.git"
    if remote_path is None:
        subprocess.run(
            ["git", "init", "--bare", str(remote)], capture_output=True, text=True, check=True
        )
    run_git("config", f"url.{remote}.insteadOf", CONFIGURED_REMOTE_URL)
    run_git("remote", "add", "configured", str(remote))
    return repo, run_git, remote


def test_resolve_ref_sha_reads_the_configured_remote_not_the_local_tip(tmp_path: Path) -> None:
    """A local branch that has drifted from the remote is not the answer."""

    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("remote tip\n")
    run_git("add", ".")
    run_git("commit", "-m", "remote tip")
    run_git("branch", "-M", "dev")
    run_git("push", "configured", "dev")
    remote_sha = run_git("rev-parse", "HEAD")

    # Local `dev` and the local `origin/dev` mirror both move ahead of the
    # remote. Before this fix either one would have been signed for.
    (repo / "file.txt").write_text("local only\n")
    run_git("add", ".")
    run_git("commit", "-m", "local drift the remote never saw")
    local_sha = run_git("rev-parse", "HEAD")
    run_git("update-ref", "refs/remotes/origin/dev", local_sha)
    assert local_sha != remote_sha

    assert (
        bridge.resolve_ref_sha(repo, "dev", repository=CONFIGURED_REPOSITORY) == remote_sha
    )


def test_resolve_ref_sha_refuses_when_the_configured_remote_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable remote is unknown, and unknown is not "use whatever is local"."""

    repo, run_git, _remote = make_repo_with_remote(
        tmp_path, remote_path=tmp_path / "this-remote-does-not-exist.git"
    )
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    run_git("branch", "-M", "dev")
    local_sha = run_git("rev-parse", "HEAD")
    run_git("update-ref", "refs/remotes/origin/dev", local_sha)

    assert bridge.resolve_ref_sha(repo, "dev", repository=CONFIGURED_REPOSITORY) is None


def test_resolve_ref_sha_refuses_an_unknown_ref_on_a_readable_remote(tmp_path: Path) -> None:
    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    run_git("branch", "-M", "dev")
    run_git("push", "configured", "dev")

    assert bridge.resolve_ref_sha(repo, "no-such-branch", repository=CONFIGURED_REPOSITORY) is None


def test_resolve_ref_sha_refuses_without_a_configured_repository(tmp_path: Path) -> None:
    """No repository means no remote to be definitive about; local is not a substitute."""

    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    run_git("branch", "-M", "dev")
    run_git("push", "configured", "dev")

    assert bridge.resolve_ref_sha(repo, "dev") is None
    assert bridge.resolve_ref_sha(repo, "dev", repository="") is None
    assert bridge.resolve_ref_sha(repo, "dev", repository="not-an-owner-slash-repo") is None


def test_resolve_ref_sha_peels_an_annotated_tag_to_its_commit(tmp_path: Path) -> None:
    """GitHub runs the commit a tag points at, never the tag object."""

    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    commit_sha = run_git("rev-parse", "HEAD")
    run_git("tag", "-a", "release-1", "-m", "annotated release tag")
    run_git("push", "configured", "release-1")
    tag_object_sha = run_git("rev-parse", "release-1")
    assert tag_object_sha != commit_sha

    resolved = bridge.resolve_ref_sha(repo, "release-1", repository=CONFIGURED_REPOSITORY)
    assert resolved == commit_sha


def test_resolve_ref_sha_resolves_a_lightweight_tag(tmp_path: Path) -> None:
    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    commit_sha = run_git("rev-parse", "HEAD")
    run_git("tag", "release-2")
    run_git("push", "configured", "release-2")

    assert bridge.resolve_ref_sha(repo, "release-2", repository=CONFIGURED_REPOSITORY) == commit_sha


def test_resolve_ref_sha_refuses_a_branch_and_tag_of_the_same_name(tmp_path: Path) -> None:
    """`workflow_dispatch` takes a bare name; a collision has no single answer."""

    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("one\n")
    run_git("add", ".")
    run_git("commit", "-m", "one")
    run_git("branch", "-M", "dev")
    run_git("tag", "shared-name")
    run_git("push", "configured", "shared-name")

    (repo / "file.txt").write_text("two\n")
    run_git("add", ".")
    run_git("commit", "-m", "two")
    run_git("branch", "shared-name")
    run_git("push", "configured", "refs/heads/shared-name:refs/heads/shared-name")

    assert bridge.resolve_ref_sha(repo, "shared-name", repository=CONFIGURED_REPOSITORY) is None


def test_resolve_ref_sha_accepts_a_branch_and_tag_that_agree(tmp_path: Path) -> None:
    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("one\n")
    run_git("add", ".")
    run_git("commit", "-m", "one")
    run_git("branch", "-M", "agreeing-name")
    run_git("tag", "agreeing-name")
    commit_sha = run_git("rev-parse", "HEAD")
    run_git("push", "configured", "refs/heads/agreeing-name:refs/heads/agreeing-name")
    run_git("push", "configured", "refs/tags/agreeing-name:refs/tags/agreeing-name")

    assert (
        bridge.resolve_ref_sha(repo, "agreeing-name", repository=CONFIGURED_REPOSITORY)
        == commit_sha
    )


def test_resolve_ref_sha_refuses_a_raw_sha_as_a_ref(tmp_path: Path) -> None:
    repo, run_git, _remote = make_repo_with_remote(tmp_path)
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    head_sha = run_git("rev-parse", "HEAD")

    assert bridge.resolve_ref_sha(repo, head_sha, repository=CONFIGURED_REPOSITORY) is None


def test_unreadable_remote_blocks_issuance_even_with_a_matching_local_ref(
    tmp_path: Path,
) -> None:
    """The end of the chain: an unknown remote blocks, it does not sign locally."""

    repo, run_git, _remote = make_repo_with_remote(
        tmp_path, remote_path=tmp_path / "unreachable.git"
    )
    (repo / "file.txt").write_text("hello\n")
    run_git("add", ".")
    run_git("commit", "-m", "init")
    run_git("branch", "-M", "dev")
    candidate_sha = run_git("rev-parse", "HEAD")

    settings = {
        "dispatch_ref": "dev",
        "github_repository": CONFIGURED_REPOSITORY,
    }
    ref_sha, errors = bridge.check_dispatch_ref_errors(settings, candidate_sha, repo)
    assert ref_sha is None
    assert errors
    assert any("does not resolve to exactly one commit" in error for error in errors)
    assert any(CONFIGURED_REPOSITORY in error for error in errors)


def test_dispatch_ref_ancestry_real_git_non_evidence_drift_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, run_git = make_repo(tmp_path)
    (repo / "docs/evidence/gates").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("print('app')\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate base commit")
    cand_sha = run_git("rev-parse", "HEAD")

    manifest = _manifest()
    manifest["candidate_sha"] = cand_sha
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    registry = _registry(manifest)
    (repo / "docs/evidence/gates/RELEASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    (repo / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json").write_text(json.dumps(registry), encoding="utf-8")
    run_git("add", ".")
    run_git("commit", "-m", "record evidence for candidate")

    # Advance dev with product code changes (non-evidence)
    (repo / "product_feature.py").write_text("print('drift')\n")
    run_git("add", ".")
    run_git("commit", "-m", "product code drift")
    dev_sha = run_git("rev-parse", "HEAD")

    request = _request(manifest)
    request["candidate_sha"] = cand_sha
    request["manifest_digest"] = manifest["manifest_digest"]
    status = _status(request)
    status_path = repo / "ai-status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    activity_path = repo / "ai-activity-log.jsonl"
    state_dir = repo / "durable-state"
    state_dir.mkdir()
    store = LeaseStateStore(state_dir, require_existing=True)

    monkeypatch.setattr(bridge, "LeaseStateStore", lambda uri, *, require_existing: store)

    config = {
        "paths": {
            "status_file": str(status_path),
            "activity_log": str(activity_path),
        },
        "release_lease_issuer": {
            "enabled": True,
            "secret_reference": "projects/999999999999/secrets/test-release-lease-key",
            "state_uri": "gs://unit-test-existing-bucket/release-leases",
            "github_repository": "example/odayplus",
            "workflow": ".github/workflows/deploy-dev.yml",
            "ttl_seconds": 300,
            "dispatch_ref": "dev",
        },
    }

    dispatched: list[dict] = []
    assert bridge.process_release_lease_issuance(
        config,
        commit_status=lambda _, cand: status_path.write_text(json.dumps(cand), encoding="utf-8") or True,
        private_key_loader=lambda _: pytest.fail("non-evidence drift must not load signing key"),
        dispatch=lambda **kwargs: dispatched.append(kwargs),
        ref_resolver=lambda root, ref, *, repository=None: dev_sha,
        now=NOW,
    )
    assert dispatched == []
    record = json.loads(status_path.read_text(encoding="utf-8"))["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("release manifest contains non-evidence paths" in error for error in record["receipt"]["errors"])



def test_dispatch_ref_non_ancestor_blocks(harness: dict) -> None:
    dispatched: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatched.append(kwargs),
        ref_resolver=lambda root, ref, *, repository=None: "f" * 40,
        loader=lambda _: pytest.fail("non-ancestor ref must stop before Secret Manager"),
    )
    assert dispatched == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("not an ancestor" in error for error in record["receipt"]["errors"])


def test_dispatch_ref_unresolvable_blocks(harness: dict) -> None:
    dispatched: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatched.append(kwargs),
        ref_resolver=lambda root, ref, *, repository=None: None,
        loader=lambda _: pytest.fail("unresolvable ref must stop before Secret Manager"),
    )
    assert dispatched == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any(
        "does not resolve to exactly one commit" in error
        for error in record["receipt"]["errors"]
    )


def test_runtime_release_inputs_missing_components_raises_dispatch_error() -> None:
    manifest = _manifest()
    request = _request(manifest)
    lease = {
        "candidate_sha": CANDIDATE_SHA,
        "manifest_digest": manifest["manifest_digest"],
        "task_id": TASK_ID,
    }
    broken_manifest = dict(manifest)
    broken_manifest["components"] = {"api": {"image": "invalid-image-without-digest"}}

    with pytest.raises(bridge.RuntimeReleaseDispatchError, match="every immutable Runtime Release image"):
        bridge.dispatch_runtime_release(
            lease=lease,
            request=request,
            manifest=broken_manifest,
            settings={"github_repository": "example/odayplus", "dispatch_ref": "dev"},
        )


def test_public_example_stays_disabled_and_workflow_has_no_issuer_secret() -> None:
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / ".orchestrator/config.example.json").read_text(encoding="utf-8"))
    assert config["release_lease_issuer"]["enabled"] is False
    assert config["release_lease_issuer"]["secret_reference"] == bridge.DEFAULT_SECRET_REFERENCE
    assert config["release_lease_issuer"]["dispatch_ref"] == "dev"
    assert validate_config(config, source="focused test public example") == config
    workflow = (root / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    assert bridge.DEFAULT_SECRET_REFERENCE not in workflow
    assert "odp-release-lease-private-key" not in workflow


@pytest.mark.parametrize("callback_kind,revision_sync", [
    ("legacy", False),
    ("current", False),
    ("current", True),
])
@pytest.mark.parametrize("dispatch_fails", [False, True])
def test_release_terminal_receipt_survives_commit_reload(
    harness: dict, monkeypatch: pytest.MonkeyPatch, callback_kind: str, revision_sync: bool, dispatch_fails: bool
) -> None:
    """Exercise the actual bridge/CAS callback, with local-only fixture lease store."""
    sync_calls = []

    def local_sync(config):
        sync_calls.append(config)
        if revision_sync:
            snapshot = json.loads(harness["status_path"].read_text())
            snapshot[supervisor.STATUS_WRITE_REVISION_FIELD] = uuid.uuid4().hex
            harness["status_path"].write_text(json.dumps(snapshot))
        return True

    monkeypatch.setattr(supervisor, "sync_status_pipeline", local_sync)

    def legacy_commit(config, status):
        return supervisor.write_status_snapshot_if_current(config, status) and supervisor.sync_status_pipeline(config)

    harness["commit"] = legacy_commit if callback_kind == "legacy" else supervisor.commit_canonical_task_transition
    dispatch_attempts = []

    def dispatch(**kwargs):
        dispatch_attempts.append(True)
        if dispatch_fails:
            raise bridge.RuntimeReleaseDispatchError("isolated unconfirmed dispatch")

    assert _run(harness, dispatch)
    assert len(dispatch_attempts) == 1
    assert len(sync_calls) == 3
    expected = "dispatch_unknown" if dispatch_fails else "dispatched"
    snapshot = json.loads(harness["status_path"].read_text())
    record = snapshot["tasks"][1][bridge.ISSUANCE_FIELD]
    activity = harness["activity_path"].read_text()
    event_type = "release_lease_dispatch_unknown" if dispatch_fails else "release_lease_runtime_release_dispatched"
    assert event_type in activity
    assert record["state"] == expected, (
        f"canonical state {record['state']!r} disagrees with emitted {event_type!r}; "
        f"callback={callback_kind}, revision_sync={revision_sync}"
    )


def test_two_release_requests_preserve_history_and_nonce_audit(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two valid release requests in sequence preserve prior issuance history on the second task."""
    second_id = "ODP-RELEASE-DEPLOY-002"
    old_nonce = "synthetic-prior-approval-nonce"
    old_record = {
        "state": "dispatched",
        "request_fingerprint": "synthetic-prior-fingerprint",
        "approval_id": "synthetic-prior-approval",
        "approval_nonce_digest": bridge._safe_digest(old_nonce),
        "receipt": {"lease_id": "lease-" + "1" * 32},
    }

    status = _read_status(harness)
    status[supervisor.STATUS_WRITE_REVISION_FIELD] = "initial-revision"
    second = copy.deepcopy(status["tasks"][1])
    second["id"] = second_id
    second[bridge.REQUEST_FIELD].update({
        "task_id": second_id,
        "nonce": "synthetic-new-second-approval",
        "approval_id": "synthetic-new-second-approval-id",
    })
    second[bridge.ISSUANCE_FIELD] = copy.deepcopy(old_record)
    status["tasks"].append(second)
    harness["status_path"].write_text(json.dumps(status))

    def local_sync(config):
        snapshot = _read_status(harness)
        snapshot[supervisor.STATUS_WRITE_REVISION_FIELD] = uuid.uuid4().hex
        harness["status_path"].write_text(json.dumps(snapshot))
        return True

    monkeypatch.setattr(supervisor, "sync_status_pipeline", local_sync)
    harness["commit"] = supervisor.commit_canonical_task_transition

    dispatches = []
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs["request"]["task_id"]))
    assert dispatches == ["ODP-RELEASE-DEPLOY-001", second_id]

    snapshot = _read_status(harness)
    second_live = next(t for t in snapshot["tasks"] if t["id"] == second_id)
    history = second_live.get(bridge.ISSUANCE_HISTORY_FIELD, [])
    assert len(history) == 1
    assert history[0] == old_record
    assert second_live.get(bridge.ISSUANCE_FIELD, {}).get("state") == "dispatched"

    # Nonce reuse of the old nonce must be rejected
    reuse_errors = bridge._nonce_reuse_errors(
        snapshot,
        "ODP-RELEASE-DEPLOY-003",
        "different-fingerprint",
        bridge._safe_digest(old_nonce),
        archive_dir=harness["root"] / "ai-task-archive/tasks",
        config=harness["config"],
    )
    assert reuse_errors == ["release_lease_request nonce was already used by a different issuance"]


@pytest.mark.parametrize("callback_kind", ["legacy", "current"])
@pytest.mark.parametrize("dispatch_fails", [False, True])
@pytest.mark.parametrize("external_change", ["receipt", "replacement_request", "request_only", "removed_task", "history_only"])
def test_terminal_publication_preserves_external_issuance_after_refresh(
    harness: dict, monkeypatch: pytest.MonkeyPatch,
    callback_kind: str, dispatch_fails: bool, external_change: str,
) -> None:
    """An actual canonical CAS reload must not authorize a stale release result."""
    sync_states = []
    expected_task = None
    external_history = {"state": "dispatch_unknown", "operator_audit": "concurrent-history"}

    def local_sync(config):
        nonlocal expected_task
        snapshot = _read_status(harness)
        task = next(t for t in snapshot["tasks"] if t["id"] == TASK_ID)
        sync_states.append(task[bridge.ISSUANCE_FIELD]["state"])
        if len(sync_states) == 2:
            assert sync_states[-1] == "issued"
            task.setdefault(bridge.ISSUANCE_HISTORY_FIELD, []).append(external_history)
            task["external_writer_marker"] = "must-survive"
            if external_change in {"receipt", "replacement_request"}:
                task[bridge.ISSUANCE_FIELD].update(
                    state="dispatch_unknown", dispatch="not_confirmed",
                    operator_receipt={"id": "external-writer-reconciliation"},
                )
            if external_change in {"replacement_request", "request_only"}:
                task[bridge.REQUEST_FIELD].update(
                    approval_id="external-approval", nonce="external-nonce",
                )
            if external_change == "replacement_request":
                task[bridge.ISSUANCE_FIELD].update(
                    request_fingerprint=bridge.request_fingerprint(TASK_ID, task[bridge.REQUEST_FIELD]),
                    approval_id="external-approval",
                    approval_nonce_digest=bridge._safe_digest("external-nonce"),
                )
            expected_task = copy.deepcopy(task)
            if external_change == "removed_task":
                snapshot["tasks"].remove(task)
        snapshot[supervisor.STATUS_WRITE_REVISION_FIELD] = uuid.uuid4().hex
        harness["status_path"].write_text(json.dumps(snapshot))
        return True

    monkeypatch.setattr(supervisor, "sync_status_pipeline", local_sync)

    def legacy_commit(config, status):
        return supervisor.write_status_snapshot_if_current(config, status) and supervisor.sync_status_pipeline(config)

    harness["commit"] = legacy_commit if callback_kind == "legacy" else supervisor.commit_canonical_task_transition
    dispatch_attempts = []

    def dispatch(**kwargs):
        dispatch_attempts.append(True)
        if dispatch_fails:
            raise bridge.RuntimeReleaseDispatchError("fixture dispatch outcome unknown")

    assert _run(harness, dispatch)
    dispatch_expected = external_change == "history_only"
    assert dispatch_attempts == ([True] if dispatch_expected else [])
    assert expected_task is not None
    snapshot = _read_status(harness)
    task = next((t for t in snapshot["tasks"] if t["id"] == TASK_ID), None)
    if external_change == "removed_task":
        assert task is None
    else:
        assert task[bridge.ISSUANCE_HISTORY_FIELD] == expected_task[bridge.ISSUANCE_HISTORY_FIELD]
        assert task["external_writer_marker"] == "must-survive"
        assert task[bridge.REQUEST_FIELD] == expected_task[bridge.REQUEST_FIELD]
        if dispatch_expected:
            assert task[bridge.ISSUANCE_FIELD]["state"] == ("dispatch_unknown" if dispatch_fails else "dispatched")
        else:
            assert task[bridge.ISSUANCE_FIELD] == expected_task[bridge.ISSUANCE_FIELD]
    activity = harness["activity_path"].read_text()
    terminal_event = '"type": "release_lease_dispatch_unknown"' if dispatch_fails else '"type": "release_lease_runtime_release_dispatched"'
    assert (terminal_event in activity) is dispatch_expected


# ---------------------------------------------------------------------------
# ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001: stale-CAS safe recovery tests
# ---------------------------------------------------------------------------


def test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When GCS write succeeded but task CAS failed, bounded retry reconciles and dispatches in the same cycle."""
    first_commit = True
    key_loader_calls = []

    def failing_commit(config, candidate):
        nonlocal first_commit
        task = candidate["tasks"][1]
        issuance = task.get(bridge.ISSUANCE_FIELD)
        if issuance and issuance.get("state") == "issued" and first_commit:
            first_commit = False
            # Simulate stale CAS rejection: task on disk stays in 'issuing' reservation
            return False
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        return True

    def tracking_loader(ref):
        key_loader_calls.append(ref)
        return harness["private_key"]

    harness["commit"] = failing_commit
    dispatches: list[dict] = []

    # Single cycle: GCS lease is minted, first 'issued' CAS fails, bounded
    # retry re-reads status and succeeds, then dispatches within the same cycle.
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), loader=tracking_loader)
    assert len(dispatches) == 1
    assert len(key_loader_calls) == 1

    # Confirm GCS state store has exactly one unconsumed issued lease
    leases = harness["store"].find_leases_for_task(TASK_ID)
    assert len(leases) == 1
    assert leases[0]["state"] == "issued"
    lease_id = leases[0]["lease_id"]
    assert dispatches[0]["lease"]["lease_id"] == lease_id
    assert dispatches[0]["lease"]["candidate_sha"] == CANDIDATE_SHA

    status_after = _read_status(harness)
    record = status_after["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "dispatched"
    assert record["dispatch"] == "accepted"
    assert record["receipt"]["lease_id"] == lease_id
    assert harness["request"]["nonce"] not in json.dumps(record)

    activity = harness["activity_path"].read_text(encoding="utf-8")
    assert "release_lease_issued" in activity
    assert "release_lease_runtime_release_dispatched" in activity
    assert harness["request"]["nonce"] not in activity


def test_stale_cas_issuing_recovery_with_expired_lease_revokes_and_blocks(harness: dict) -> None:
    """When an orphan lease in GCS has expired, recovery revokes it and marks task blocked."""
    # Pre-populate an issued lease in state store with expired timestamp
    expired_time = datetime(2026, 9, 4, 11, 0, 0, tzinfo=UTC)
    expired_lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=60,
        issued_at=expired_time,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    harness["store"].record_issued(expired_lease)

    # Set task to issuing state with current request fingerprint
    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("expired lease recovery must not load signing key"),
    )
    assert dispatches == []

    # Verify lease was revoked in GCS
    record = harness["store"].get(expired_lease["lease_id"])
    assert record["state"] == "revoked"
    assert "orphan lease expired" in (record.get("revoked_reason") or "")

    # Verify task board state is blocked
    task_after = _read_status(harness)["tasks"][1]
    assert task_after[bridge.ISSUANCE_FIELD]["state"] == "blocked"
    assert any("expired" in err for err in task_after[bridge.ISSUANCE_FIELD]["receipt"]["errors"])


def test_stale_cas_issuing_recovery_with_mismatched_payload_leaves_unrelated_lease_and_blocks(harness: dict) -> None:
    """When an orphan lease in GCS has mismatched candidate_sha, recovery does not revoke unrelated lease and blocks."""
    mismatched_lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha="f" * 40,  # Different SHA
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint="sha256:" + "f" * 64,
        approval_id="other-approval",
        approval_nonce_digest="sha256:" + "0" * 64,
    )
    harness["store"].record_issued(mismatched_lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("mismatched lease recovery must not load signing key"),
    )
    assert dispatches == []

    # Verify unrelated lease was NOT revoked in GCS (exact ownership rule)
    record = harness["store"].get(mismatched_lease["lease_id"])
    assert record["state"] == "issued"

    # Verify task board state is blocked
    task_after = _read_status(harness)["tasks"][1]
    assert task_after[bridge.ISSUANCE_FIELD]["state"] == "blocked"
    assert any("does not match current request" in err for err in task_after[bridge.ISSUANCE_FIELD]["receipt"]["errors"])


def test_stale_cas_issuing_recovery_with_multiple_issued_leases_revokes_matching_and_blocks(harness: dict) -> None:
    """When multiple issued leases exist in GCS for a task, recovery revokes matching leases and blocks."""
    fp = bridge.request_fingerprint(TASK_ID, harness["request"])
    app_id = str(harness["request"].get("approval_id") or "")
    nonce_digest = bridge._safe_digest(harness["request"].get("nonce"))
    lease1 = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=fp,
        approval_id=app_id,
        approval_nonce_digest=nonce_digest,
    )
    lease2 = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=fp,
        approval_id=app_id,
        approval_nonce_digest=nonce_digest,
    )
    harness["store"].record_issued(lease1)
    harness["store"].record_issued(lease2)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("multiple lease recovery must not load signing key"),
    )
    assert dispatches == []

    assert harness["store"].get(lease1["lease_id"])["state"] == "revoked"
    assert harness["store"].get(lease2["lease_id"])["state"] == "revoked"
    task_after = _read_status(harness)["tasks"][1]
    assert task_after[bridge.ISSUANCE_FIELD]["state"] == "blocked"
    assert any("multiple conflicting" in err for err in task_after[bridge.ISSUANCE_FIELD]["receipt"]["errors"])


def test_stale_cas_issuing_recovery_with_failed_preconditions_revokes_and_blocks(harness: dict) -> None:
    """When an issuing task's dependencies become incomplete, recovery revokes GCS lease and blocks."""
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    harness["store"].record_issued(lease)

    # Set dependency to in_progress (not done)
    _set_task_status(harness, DEPENDENCY_ID, "in_progress")
    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("failed precondition recovery must not load signing key"),
    )
    assert dispatches == []

    assert harness["store"].get(lease["lease_id"])["state"] == "revoked"
    task_after = _read_status(harness)["tasks"][1]
    assert task_after[bridge.ISSUANCE_FIELD]["state"] == "blocked"
    assert any("required dependency expected 'done'" in err for err in task_after[bridge.ISSUANCE_FIELD]["receipt"]["errors"])


def test_issuing_without_gcs_lease_terminates_blocked_without_loading_key(harness: dict) -> None:
    """When a task is in issuing state but no GCS lease exists, recovery terminates blocked without re-signing."""
    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("absent GCS lease recovery must NOT load signing key"),
    )
    assert dispatches == []

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("issuing reservation has no durable lease" in err for err in record["receipt"]["errors"])


def test_issuing_with_consumed_or_revoked_gcs_lease_terminates_blocked_without_loading_key(harness: dict) -> None:
    """When a task is in issuing state but the durable lease is consumed or revoked, recovery blocks without re-signing."""
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    harness["store"].record_issued(lease)
    harness["store"].consume(lease, consumed_by="previous_runner")

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("consumed GCS lease recovery must NOT load signing key"),
    )
    assert dispatches == []

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("issuing reservation has no active issued lease" in err for err in record["receipt"]["errors"])


def test_issuing_recovery_with_invalid_signature_blocks_and_does_not_dispatch(harness: dict) -> None:
    """When durable lease in GCS has an invalid signature, verify_lease catches it, revokes lease, and blocks."""
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    # Corrupt signature value
    lease["signature"]["value"] = "0" * 128
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("invalid signature recovery must NOT load signing key"),
    )
    assert dispatches == []

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("signature" in err for err in record["receipt"]["errors"])


def test_issuing_recovery_with_changed_approval_blocks_and_does_not_dispatch(harness: dict) -> None:
    """When a new approval ID/nonce is issued for the same deploy, recovery does not adopt or revoke the old lease."""
    old_request = copy.deepcopy(harness["request"])
    old_request["approval_id"] = "approval-old-001"
    old_request["nonce"] = "old-human-nonce-001"
    old_fp = bridge.request_fingerprint(TASK_ID, old_request)

    old_lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=old_fp,
        approval_id="approval-old-001",
        approval_nonce_digest=bridge._safe_digest("old-human-nonce-001"),
    )
    harness["store"].record_issued(old_lease)

    # New approval request on identical deployment fields (same candidate_sha, manifest, env, action)
    new_request = copy.deepcopy(harness["request"])
    new_request["approval_id"] = "approval-new-002"
    new_request["nonce"] = "new-human-nonce-002"
    new_fp = bridge.request_fingerprint(TASK_ID, new_request)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.REQUEST_FIELD] = new_request
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=new_request,
        fingerprint=new_fp,
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("changed approval must not load key for old lease"),
    )
    assert dispatches == []

    # Old lease must remain unmodified in store (not adopted, not revoked)
    stored_rec = harness["store"].get(old_lease["lease_id"])
    assert stored_rec["state"] == "issued"

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("does not match current request" in err for err in record["receipt"]["errors"])


def test_issuing_recovery_with_ttl_delay_blocks_and_does_not_dispatch(harness: dict) -> None:
    """When TTL elapses between issuance and recovery, task is blocked without dispatch.

    Uses a 60-second lease issued at NOW. Recovery runs at NOW+120s (well past
    lease expiry). The pre-receipt-commit expiry recheck catches this and records
    a terminal blocked outcome. No signing key is loaded.
    """
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=60,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    later_now = NOW + timedelta(seconds=120)
    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("delayed TTL recovery must not load signing key"),
        now=later_now,
    )
    assert dispatches == []

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("expired" in err for err in record["receipt"]["errors"])
    # Matching lease was revoked
    assert harness["store"].get(lease["lease_id"])["state"] == "revoked"


def test_revision_checking_writer_preserves_newer_status_on_bounded_retry(harness: dict) -> None:
    """A genuine revision CAS reload preserves a concurrent update on retry."""
    initial_status = _read_status(harness)
    initial_status["_status_write_revision"] = "revision-before-race"
    harness["status_path"].write_text(json.dumps(initial_status), encoding="utf-8")
    issued_attempts = 0
    concurrent_writer_ran = False
    dispatches: list[dict] = []

    def revision_checking_commit(config, candidate):
        nonlocal issued_attempts, concurrent_writer_ran
        disk = _read_status(harness)
        # A real CAS writer never repairs or merges a stale candidate.
        if candidate.get("_status_write_revision") != disk.get("_status_write_revision"):
            return False
        candidate_task = next(task for task in candidate["tasks"] if task["id"] == TASK_ID)
        issuance = candidate_task.get(bridge.ISSUANCE_FIELD, {})
        if issuance.get("state") == "issued" and issued_attempts == 0:
            issued_attempts += 1
            disk["_status_write_revision"] = "revision-after-concurrent-update"
            disk["tasks"][0]["notes"] = ["concurrent writer updated dependency"]
            disk["updated_at"] = "2026-09-28T04:00:00Z"
            harness["status_path"].write_text(json.dumps(disk), encoding="utf-8")
            concurrent_writer_ran = True
            # The candidate revision is now stale; reject it without repair.
            return False
        next_revision = f"revision-{uuid.uuid4().hex}"
        candidate["_status_write_revision"] = next_revision
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        return True

    harness["commit"] = revision_checking_commit
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs))
    assert len(dispatches) == 1
    assert issued_attempts == 1
    assert concurrent_writer_ran

    status_final = _read_status(harness)
    assert status_final["tasks"][0]["notes"] == ["concurrent writer updated dependency"]
    assert status_final["tasks"][1][bridge.ISSUANCE_FIELD]["state"] == "dispatched"
    assert status_final["_status_write_revision"].startswith("revision-")


def test_second_cas_rejection_during_recovery_does_not_dispatch(harness: dict) -> None:
    """When recovery encounters a second CAS rejection on status commit, dispatch is prevented."""
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    def rejecting_commit(config, candidate):
        return False

    harness["commit"] = rejecting_commit
    dispatches: list[dict] = []
    assert not _run(harness, lambda **kwargs: dispatches.append(kwargs))
    assert dispatches == []

    # Durable lease in store must stay issued for future cycle retry
    assert harness["store"].get(lease["lease_id"])["state"] == "issued"


def test_eligibility_revoked_during_storage_revokes_lease_and_blocks(harness: dict) -> None:
    """When task eligibility is revoked during storage/ref-resolution, lease is revoked and task blocked.

    A ref-resolver callback simulates a concurrent writer changing the request
    status to 'revoked' on disk during the ref lookup. The post-storage
    precondition recheck reads the updated status and catches the revocation.
    """
    fp = bridge.request_fingerprint(TASK_ID, harness["request"])
    app_id = str(harness["request"].get("approval_id") or "")
    nonce_digest = bridge._safe_digest(harness["request"].get("nonce"))

    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=fp,
        approval_id=app_id,
        approval_nonce_digest=nonce_digest,
    )
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=fp,
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    # Request status is 'approved' at start — valid
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    resolver_calls = 0
    revocation_happened = False

    def revoking_ref_resolver(root, ref, *, repository=None):
        nonlocal resolver_calls, revocation_happened
        resolver_calls += 1
        if resolver_calls == 2:
            # Revoke after the first admission check, during final ref resolution.
            disk = json.loads(harness["status_path"].read_text(encoding="utf-8"))
            disk["tasks"][1][bridge.REQUEST_FIELD]["status"] = "revoked"
            disk["_status_write_revision"] = "concurrent-revocation"
            disk["tasks"][0]["notes"] = ["preserve concurrent update"]
            harness["status_path"].write_text(json.dumps(disk), encoding="utf-8")
            revocation_happened = True
        return harness["request"]["candidate_sha"]

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        ref_resolver=revoking_ref_resolver,
    )
    assert dispatches == []
    assert revocation_happened
    assert resolver_calls >= 2

    final_status = _read_status(harness)
    assert final_status["tasks"][0]["notes"] == ["preserve concurrent update"]
    assert final_status["tasks"][1][bridge.REQUEST_FIELD]["status"] == "revoked"
    record = final_status["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert harness["store"].get(lease["lease_id"])["state"] == "revoked"


def test_cas_retry_revalidates_revoked_approval_and_preserves_newer_state(harness: dict) -> None:
    """A CAS rejection that concurrently revokes approval cannot reach retry dispatch."""
    initial = _read_status(harness)
    initial["_status_write_revision"] = "revision-before-race"
    harness["status_path"].write_text(json.dumps(initial), encoding="utf-8")
    issued_attempts = 0
    current_revision = {"value": "revision-before-race"}

    def revision_checking_commit(config, candidate):
        nonlocal issued_attempts
        disk = _read_status(harness)
        if candidate.get("_status_write_revision") != current_revision["value"]:
            return False
        candidate_task = next(task for task in candidate["tasks"] if task["id"] == TASK_ID)
        candidate_issuance = candidate_task.get(bridge.ISSUANCE_FIELD, {})
        if candidate_issuance.get("state") == "issued" and issued_attempts == 0:
            issued_attempts += 1
            disk["tasks"][1][bridge.REQUEST_FIELD]["status"] = "revoked"
            disk["tasks"][0]["notes"] = ["concurrent canonical update"]
            disk["_status_write_revision"] = "revision-after-revocation"
            harness["status_path"].write_text(json.dumps(disk), encoding="utf-8")
            current_revision["value"] = "revision-after-revocation"
            return False
        next_revision = f"revision-{uuid.uuid4().hex}"
        candidate["_status_write_revision"] = next_revision
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        current_revision["value"] = next_revision
        return True

    harness["commit"] = revision_checking_commit
    dispatches: list[dict] = []
    loader_calls: list[str] = []

    def loader(reference: str):
        loader_calls.append(reference)
        return harness["private_key"]

    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), loader=loader)
    final_status = _read_status(harness)
    record = final_status["tasks"][1][bridge.ISSUANCE_FIELD]
    assert dispatches == []
    assert issued_attempts == 1
    assert len(loader_calls) == 1
    assert final_status["tasks"][1][bridge.REQUEST_FIELD]["status"] == "revoked"
    assert final_status["tasks"][0]["notes"] == ["concurrent canonical update"]
    assert final_status["_status_write_revision"] == current_revision["value"]
    assert record["state"] == "blocked"
    leases = harness["store"].find_leases_for_task(TASK_ID)
    assert len(leases) == 1
    assert leases[0]["state"] == "revoked"


def test_state_store_list_or_read_failure_records_blocked_without_crashing_or_signing(
    harness: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When state store list/read raises LeaseStateError, process catches it and marks task blocked."""
    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    def failing_find(task_id):
        raise LeaseStateError("GCS list_blobs permission denied")

    monkeypatch.setattr(harness["store"], "find_leases_for_task", failing_find)

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("storage lookup failure must not load signing key"),
    )
    assert dispatches == []

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("durable lease state lookup failed" in err for err in record["receipt"]["errors"])


def test_malformed_verifier_errors_with_bearer_sentinels_never_leak_secrets(harness: dict) -> None:
    """Malformed verifier errors containing bearer sentinels are sanitized and never leak into status or activity log."""
    sentinel_algo = "BEARER_ALGO_TOKEN_SECRET_12345"
    sentinel_key = "BEARER_KEY_ID_SECRET_67890"

    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    # Inject bearer sentinels into signature block
    lease["signature"]["algorithm"] = sentinel_algo
    lease["signature"]["key_id"] = sentinel_key
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs))
    assert dispatches == []

    status_text = harness["status_path"].read_text(encoding="utf-8")
    activity_text = harness["activity_path"].read_text(encoding="utf-8")

    assert sentinel_algo not in status_text
    assert sentinel_algo not in activity_text
    assert sentinel_key not in status_text
    assert sentinel_key not in activity_text


def test_no_duplicate_sign_or_dispatch_on_recovery(harness: dict) -> None:
    """Reconciliation after a stale CAS never duplicates private key signing or dispatches twice."""
    key_loader_calls: list[str] = []
    dispatches: list[dict] = []
    attempt = 0

    def flaky_commit(config, candidate):
        nonlocal attempt
        attempt += 1
        if attempt == 2:  # attempt 1 is 'issuing' reservation, attempt 2 is 'issued'
            return False
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        return True

    harness["commit"] = flaky_commit

    def loader(ref):
        key_loader_calls.append(ref)
        return harness["private_key"]

    # Single cycle: signs key once, writes GCS, first 'issued' CAS fails,
    # bounded retry succeeds, dispatches within the same cycle.
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), loader=loader)
    assert len(key_loader_calls) == 1
    assert len(dispatches) == 1


def test_no_secret_or_bearer_material_in_logs_or_status(harness: dict) -> None:
    """Neither private key material nor raw bearer tokens/signatures appear in issuance receipts or logs."""
    dispatches: list[dict] = []
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs))
    assert len(dispatches) == 1

    lease = dispatches[0]["lease"]
    sig_value = lease["signature"]["value"]
    raw_nonce = harness["request"]["nonce"]

    issuance_record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    serialized_issuance = json.dumps(issuance_record, sort_keys=True)
    activity_text = harness["activity_path"].read_text(encoding="utf-8")

    # Raw signature value must never appear in issuance record or activity log
    assert sig_value not in serialized_issuance
    assert sig_value not in activity_text

    # Raw human nonce must not appear in issuance record or activity log (only digest)
    assert raw_nonce not in serialized_issuance
    assert raw_nonce not in activity_text

    # Private key markers must never appear in status or activity log
    status_text = harness["status_path"].read_text(encoding="utf-8")
    assert "BEGIN PRIVATE KEY" not in status_text
    assert "BEGIN PRIVATE KEY" not in activity_text
    assert "BEGIN ED25519 PRIVATE KEY" not in status_text
    assert "BEGIN ED25519 PRIVATE KEY" not in activity_text



def test_revision_checking_writer_recovers_after_two_issued_cas_rejections(
    harness: dict,
) -> None:
    """Two true CAS rejections preserve newer data; recovery never re-signs."""
    key_loader_calls: list[str] = []
    dispatches: list[dict] = []
    issued_attempts = 0

    initial_status = _read_status(harness)
    initial_status["_status_write_revision"] = "revision-initial"
    harness["status_path"].write_text(json.dumps(initial_status), encoding="utf-8")

    def revision_checking_commit(config, candidate):
        nonlocal issued_attempts
        disk = _read_status(harness)
        # The writer accepts only the exact revision read by the caller and
        # never repairs or merges a stale candidate snapshot.
        if candidate.get("_status_write_revision") != disk.get("_status_write_revision"):
            return False
        candidate_task = next(task for task in candidate["tasks"] if task["id"] == TASK_ID)
        issuance = candidate_task.get(bridge.ISSUANCE_FIELD, {})
        if issuance.get("state") == "issued" and issued_attempts < 2:
            issued_attempts += 1
            disk["_status_write_revision"] = f"revision-concurrent-{issued_attempts}"
            disk["tasks"][0]["notes"] = [f"concurrent update {issued_attempts}"]
            disk["updated_at"] = f"2026-09-28T04:00:0{issued_attempts}Z"
            harness["status_path"].write_text(json.dumps(disk), encoding="utf-8")
            # This is the concurrent writer's commit. The candidate passed
            # the earlier compare but is now stale, so reject it.
            return False
        candidate["_status_write_revision"] = f"revision-accepted-{uuid.uuid4().hex}"
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        return True

    harness["commit"] = revision_checking_commit

    def tracking_loader(ref):
        key_loader_calls.append(ref)
        return harness["private_key"]

    # Cycle 1: one signature is durable, while both receipt CAS attempts lose
    # to newer canonical revisions.
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), loader=tracking_loader)
    assert len(key_loader_calls) == 1
    assert dispatches == []
    assert issued_attempts == 2

    leases = harness["store"].find_leases_for_task(TASK_ID)
    assert len(leases) == 1
    assert leases[0]["state"] == "issued"
    lease_id = leases[0]["lease_id"]

    # Cycle 2 proves exact-lease recovery without loading the signing key again.
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("recovery must not reload the signing key"),
    )
    assert len(dispatches) == 1
    assert len(key_loader_calls) == 1
    assert dispatches[0]["lease"]["lease_id"] == lease_id

    status_final = _read_status(harness)
    record = status_final["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "dispatched"
    assert record["dispatch"] == "accepted"
    assert status_final["tasks"][0]["notes"] == ["concurrent update 2"]
    assert status_final["_status_write_revision"].startswith("revision-accepted-")

    serialized_record = json.dumps(record)
    activity_text = harness["activity_path"].read_text(encoding="utf-8")
    status_text = harness["status_path"].read_text(encoding="utf-8")
    assert harness["request"]["nonce"] not in serialized_record
    assert harness["request"]["nonce"] not in activity_text
    assert "BEGIN PRIVATE KEY" not in status_text
    assert "BEGIN PRIVATE KEY" not in activity_text


@pytest.mark.parametrize("phase", ["cas", "post_sync"])
@pytest.mark.parametrize("input_change", ["no_go", "manifest", "build_binding"])
def test_release_inputs_are_reloaded_after_cas_and_sync_callbacks(
    harness: dict, phase: str, input_change: str
) -> None:
    """Fresh gate, manifest and build inputs revoke stale retry/dispatch admission."""
    initial_status = _read_status(harness)
    initial_status["_status_write_revision"] = "revision-before-input-change"
    harness["status_path"].write_text(json.dumps(initial_status), encoding="utf-8")
    issued_attempts = 0
    dispatches: list[dict] = []
    key_loader_calls: list[str] = []

    def mutate_release_inputs() -> None:
        registry_path = harness["root"] / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
        manifest_path = harness["root"] / "docs/evidence/gates/RELEASE_MANIFEST.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if input_change == "no_go":
            registry["release"]["decision"] = "no-go"
        elif input_change == "manifest":
            manifest["components"]["api"]["image"] = "ghcr.io/example/api@sha256:" + "f" * 64
            manifest["manifest_digest"] = compute_manifest_digest(manifest)
        else:
            registry["candidate_rebind"]["build_run"]["run_id"] = int(RUN_ID) + 1
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def revision_checking_commit(config, candidate):
        nonlocal issued_attempts
        disk = _read_status(harness)
        if candidate.get("_status_write_revision") != disk.get("_status_write_revision"):
            return False
        candidate_task = next(task for task in candidate["tasks"] if task["id"] == TASK_ID)
        issuance = candidate_task.get(bridge.ISSUANCE_FIELD, {})
        if issuance.get("state") == "issued":
            issued_attempts += 1
            if phase == "cas" and issued_attempts == 1:
                disk["_status_write_revision"] = "revision-after-issued-cas-race"
                disk["tasks"][0]["notes"] = ["preserve concurrent writer"]
                harness["status_path"].write_text(json.dumps(disk), encoding="utf-8")
                mutate_release_inputs()
                return False
        candidate["_status_write_revision"] = f"revision-accepted-{uuid.uuid4().hex}"
        harness["status_path"].write_text(json.dumps(candidate), encoding="utf-8")
        if phase == "post_sync" and issuance.get("state") == "issued" and issued_attempts == 1:
            mutate_release_inputs()
        return True

    harness["commit"] = revision_checking_commit

    def tracking_loader(ref):
        key_loader_calls.append(ref)
        return harness["private_key"]

    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), loader=tracking_loader)
    assert dispatches == []
    assert len(key_loader_calls) == 1
    assert issued_attempts == 1

    final_status = _read_status(harness)
    record = final_status["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert harness["store"].find_leases_for_task(TASK_ID)[0]["state"] == "revoked"
    if phase == "cas":
        assert final_status["tasks"][0]["notes"] == ["preserve concurrent writer"]
        assert final_status["_status_write_revision"].startswith("revision-accepted-")
    else:
        assert "_status_write_revision" in final_status


def test_request_expiry_during_final_ref_validation_blocks_without_dispatch(
    harness: dict,
) -> None:
    """A live clock catches request expiry caused by the final ref callback."""
    fingerprint = bridge.request_fingerprint(TASK_ID, harness["request"])
    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=fingerprint,
        approval_id=str(harness["request"]["approval_id"]),
        approval_nonce_digest=bridge._safe_digest(harness["request"]["nonce"]),
    )
    harness["store"].record_issued(lease)
    status = _read_status(harness)
    status["tasks"][1][bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=fingerprint,
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(status), encoding="utf-8")

    clock_now = [NOW]
    resolver_calls = 0

    def advancing_ref_resolver(root, ref, *, repository=None):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls == 2:
            clock_now[0] = NOW + timedelta(minutes=5, seconds=1)
        return CANDIDATE_SHA

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        ref_resolver=advancing_ref_resolver,
        clock=lambda: clock_now[0],
    )
    assert resolver_calls >= 2
    assert dispatches == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    assert any("expired" in error for error in record["receipt"]["errors"])
    assert harness["store"].get(lease["lease_id"])["state"] == "revoked"


def test_request_expiry_during_commit_sync_blocks_dispatch(harness: dict) -> None:
    """A slow canonical sync that crosses request expiry cannot dispatch."""
    clock_now = [NOW]
    base_commit = harness["commit"]

    def expiring_commit(config, candidate):
        result = base_commit(config, candidate)
        task = next(task for task in candidate["tasks"] if task["id"] == TASK_ID)
        issuance = task.get(bridge.ISSUANCE_FIELD, {})
        if issuance.get("state") == "issued" and result:
            clock_now[0] = NOW + timedelta(minutes=5, seconds=1)
        return result

    harness["commit"] = expiring_commit
    dispatches: list[dict] = []
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs), clock=lambda: clock_now[0])
    assert dispatches == []
    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "expired_before_dispatch"
    assert harness["store"].get(record["receipt"]["lease_id"])["state"] == "revoked"


def test_unknown_diagnostic_maps_to_fixed_safe_message() -> None:
    """Unrecognized non-hex bearer text is never copied or truncated into a receipt."""
    sentinel = "Bearer.NonHexSecret!_short"
    sanitized = bridge._sanitize_errors([f"provider response contained {sentinel}"])
    assert sanitized == ["lease validation failed"]
    assert sentinel not in json.dumps(bridge._receipt(None, errors=[f"error {sentinel}"], issued_at=NOW))


def test_schema_version_bearing_signature_value_is_sanitized(
    harness: dict,
) -> None:
    """A stored lease with schema_version set to a 128-char hex signature value has its error sanitized.

    The raw hex value must never appear in status or activity logs.
    """
    fp = bridge.request_fingerprint(TASK_ID, harness["request"])
    fake_sig_value = "a1" * 64  # 128-char hex, looks like a signature value

    lease = build_lease(
        task_id=TASK_ID,
        release_id=str(harness["manifest"]["release_id"]),
        candidate_sha=CANDIDATE_SHA,
        manifest_digest=harness["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=harness["private_key"],
        ttl_seconds=300,
        issued_at=NOW,
        request_fingerprint=fp,
        approval_id=str(harness["request"].get("approval_id") or ""),
        approval_nonce_digest=bridge._safe_digest(harness["request"].get("nonce")),
    )
    # Tamper: set schema_version to the signature value
    lease["schema_version"] = fake_sig_value
    harness["store"].record_issued(lease)

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=fp,
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    dispatches: list[dict] = []
    assert _run(harness, lambda **kwargs: dispatches.append(kwargs))
    assert dispatches == []

    status_text = harness["status_path"].read_text(encoding="utf-8")
    activity_text = harness["activity_path"].read_text(encoding="utf-8")

    # The 128-char hex signature value must not appear in status or activity
    assert fake_sig_value not in status_text
    assert fake_sig_value not in activity_text

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"


def test_durable_state_lookup_error_uses_safe_sentinel_not_raw_exception(
    harness: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _state_errors catches a LeaseStateError, the error message is a safe sentinel.

    The raw exception text (which could contain storage paths, credentials, or
    infrastructure details) must never appear in the receipt errors.
    """
    secret_path = "gs://secret-bucket-name/secret-prefix/lease-data"
    raw_exception_text = f"Permission denied reading {secret_path}: IAM role missing"

    current_status = _read_status(harness)
    task = current_status["tasks"][1]
    task[bridge.ISSUANCE_FIELD] = bridge._issuance_record(
        state="issuing",
        task_id=TASK_ID,
        request=harness["request"],
        fingerprint=bridge.request_fingerprint(TASK_ID, harness["request"]),
        settings=harness["config"]["release_lease_issuer"],
        receipt=bridge._receipt(None, errors=[], issued_at=NOW),
        updated_at=NOW,
    )
    harness["status_path"].write_text(json.dumps(current_status), encoding="utf-8")

    def failing_find(task_id):
        raise LeaseStateError(raw_exception_text)

    monkeypatch.setattr(harness["store"], "find_leases_for_task", failing_find)

    dispatches: list[dict] = []
    assert _run(
        harness,
        lambda **kwargs: dispatches.append(kwargs),
        loader=lambda _: pytest.fail("storage failure must not load signing key"),
    )
    assert dispatches == []

    status_text = harness["status_path"].read_text(encoding="utf-8")
    activity_text = harness["activity_path"].read_text(encoding="utf-8")

    # Raw exception details must not appear
    assert secret_path not in status_text
    assert secret_path not in activity_text
    assert "IAM role missing" not in status_text
    assert "IAM role missing" not in activity_text

    record = _read_status(harness)["tasks"][1][bridge.ISSUANCE_FIELD]
    assert record["state"] == "blocked"
    # Should use safe sentinel message
    assert any("durable lease state lookup failed" in err for err in record["receipt"]["errors"])


def test_sanitize_errors_unknown_text_maps_to_fixed_message() -> None:
    """Unknown diagnostics are replaced, not truncated or redacted in place."""
    fake_sig = "abcdef0123456789" * 8
    sanitized = bridge._sanitize_errors([f"verification failed for value {fake_sig} in store"])
    assert sanitized == ["lease validation failed"]
    assert fake_sig not in sanitized[0]


def test_sanitize_errors_state_store_sentinel_mapping() -> None:
    """_sanitize_errors maps known diagnostic categories to safe sentinel codes."""
    test_cases = [
        ("BEGIN PRIVATE KEY\nMIIEvAIBAD...", "private key error"),
        ("algorithm Ed25519 does not match signature expectation", "lease signature algorithm is invalid"),
        ("key_id abc123 does not match expected signature key", "lease signature key_id does not match configured verification key"),
        ("InvalidSignature: hash mismatch", "lease signature does not verify against the configured public key"),
        ("lease.schema_version must be 1; actual value does not match", "lease schema_version does not match expected version"),
        ("durable lease state lookup failed", "durable lease state lookup failed"),
        ("permission denied reading gs://bucket/prefix", "lease state store access denied"),
    ]
    for raw, expected_sentinel in test_cases:
        result = bridge._sanitize_errors([raw])
        assert len(result) == 1, f"Expected 1 result for {raw!r}, got {result}"
        assert result[0] == expected_sentinel, f"For {raw!r}: expected {expected_sentinel!r}, got {result[0]!r}"
