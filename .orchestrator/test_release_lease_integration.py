"""Focused contract tests for the Supervisor Runtime Release lease bridge."""

from __future__ import annotations

import base64
import json
import subprocess
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


def _run(harness: dict, dispatch, *, loader=None, ref_resolver=None) -> bool:
    return bridge.process_release_lease_issuance(
        harness["config"],
        commit_status=harness["commit"],
        private_key_loader=loader or (lambda _: harness["private_key"]),
        dispatch=dispatch,
        ref_resolver=ref_resolver
        or (lambda root, ref, *, repository=None: harness["request"]["candidate_sha"]),
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
    assert any("non-evidence paths" in error for error in record["receipt"]["errors"])



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
