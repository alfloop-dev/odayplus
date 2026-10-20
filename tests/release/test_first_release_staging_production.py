"""First staging/production release without a predecessor (fail closed).

ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-001. The rollout plan's recovery steps
assume production already holds an approved release to switch back to. The
first production release has none, and staging shares one manifest with
production, so before this change neither could be admitted without
fabricating a rollback target.

The chosen policy extends ``initial_release_recovery`` and does **not** relax
the rollback binding:

* staging may seal a first-release record (read back against this release's
  release-scoped staging names); production never is a build target and only
  receives the staging-sealed manifest;
* every admission of such a manifest needs a readback of *that* environment
  taken during the admission, bound to the manifest digest and fresh; and
* staging and production admission are refused once production has consumed a
  lease for any other manifest -- the record is usable once per lineage.

Every refusal happens before the single-use lease is consumed.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from delivery_toolchain.release import check_runtime_admission
from delivery_toolchain.release.build_release_handoff import HandoffError, build_handoff
from delivery_toolchain.release.check_runtime_admission import main as admission_main
from delivery_toolchain.release.probe_release_target_absence import (
    ProbeError,
    probe_target_absence,
    verify_manifest_recovery,
)
from delivery_toolchain.release.probe_release_target_absence import (
    main as probe_main,
)
from delivery_toolchain.release.release_lease import (
    STATE_CONSUMED,
    STATE_ISSUED,
    LeaseStateStore,
    build_lease,
    generate_keypair,
)
from delivery_toolchain.release.release_manifest import (
    INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS,
    INITIAL_RELEASE_ADMISSION_READBACK_MAX_AGE_SECONDS,
    INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS,
    INITIAL_RELEASE_PROBE_COMMAND,
    INITIAL_RELEASE_READBACK_KIND,
    INITIAL_RELEASE_TARGET_INVENTORY,
    INITIAL_RELEASE_VERIFICATION_KIND,
    compute_manifest_digest,
    initial_release_lineage_errors,
    initial_release_verification_errors,
    release_candidate_job_name,
    staging_release_target_names,
    validate_manifest,
    validate_release_admission,
)
from product_ops.deployment.staging_lifecycle import get_ephemeral_resource_names

ROOT = Path(__file__).resolve().parents[2]
SHA = "596b9c9a1788d952811a2bf8d4bba8a4e4d76b12"
RELEASE_ID = f"odp-{SHA[:12]}"
TASK_ID = "ODP-CORE-OPS-FIRST-RELEASE-ROLLBACK-TEST"
CREATED_AT = "2026-10-20T08:00:00+00:00"
REPO = "asia-east1-docker.pkg.dev/odayplus/oday-plus"
NOW = datetime(2026, 10, 20, 9, 0, 0, tzinfo=UTC)
CORE_OPS = "core-ops"

PRODUCTION_TARGETS = {
    component: f"oday-plus-{component}"
    for component, _kind in INITIAL_RELEASE_TARGET_INVENTORY
}

# Answers `gcloud run services|jobs list` from FAKE_PRESENT / FAKE_UNREADABLE.
FAKE_GCLOUD = '''#!/usr/bin/env python3
import json
import os
import sys

present = set(json.loads(os.environ.get("FAKE_PRESENT", "[]")))
if json.loads(os.environ.get("FAKE_UNREADABLE", "false")):
    print("PERMISSION_DENIED", file=sys.stderr)
    raise SystemExit(1)
name = ""
for argument in sys.argv[1:]:
    if argument.startswith("--filter=metadata.name="):
        name = argument.split("=", 2)[2]
if sys.argv[1:3] == ["run", "jobs"]:
    for resource in sorted(present):
        print(resource)
elif name in present:
    print(name)
raise SystemExit(0)
'''


@pytest.fixture(autouse=True)
def resolved_sources_off_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODP_CLOUD_RUN_VPC_EGRESS", "ALL_TRAFFIC")
    monkeypatch.setenv("FAKE_PRESENT", "[]")
    monkeypatch.setenv("FAKE_UNREADABLE", "false")


@pytest.fixture
def fake_gcloud(tmp_path: Path) -> Path:
    path = tmp_path / "fake-gcloud"
    path.write_text(FAKE_GCLOUD, encoding="utf-8")
    path.chmod(0o755)
    return path


def ref(name: str, fill: str) -> str:
    return f"{REPO}/{name}@sha256:{fill * 64}"


def readback(environment: str, *, release_id: str = RELEASE_ID, present=()) -> dict:
    """A readback as the probe writes it, for staging or a long-lived target."""

    if environment == "staging":
        names = staging_release_target_names(release_id)
    else:
        names = {
            component: (
                release_candidate_job_name(PRODUCTION_TARGETS[component], SHA)
                if kind == "cloud-run-job"
                else PRODUCTION_TARGETS[component]
            )
            for component, kind in INITIAL_RELEASE_TARGET_INVENTORY
        }
    return {
        "kind": INITIAL_RELEASE_READBACK_KIND,
        "target_environment": environment,
        "project": f"odayplus-{environment}",
        "region": "asia-east1",
        "probe_command": INITIAL_RELEASE_PROBE_COMMAND,
        "targets": [
            {
                "component": component,
                "resource_kind": kind,
                "resource_name": names[component],
                "exists": component in present,
                "serving_traffic": component in present,
            }
            for component, kind in sorted(INITIAL_RELEASE_TARGET_INVENTORY)
        ],
    }


def first_release(**overrides) -> dict:
    kwargs = {
        "release_sha": SHA,
        "components": {
            "api": ref("api", "1"),
            "web": ref("web", "2"),
            "worker": ref("worker", "3"),
            "scheduler": ref("scheduler", "4"),
        },
        "sbom_refs": [ref("api", "5")],
        "signature_refs": [ref("api", "6")],
        "initial_release_readback": readback("staging"),
        "target_environment": "staging",
        "release_profile": CORE_OPS,
        "created_at": CREATED_AT,
        "created_by_workflow": (
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@" + SHA
        ),
        "root": ROOT,
    }
    kwargs.update(overrides)
    _, manifest = build_handoff(**kwargs)
    return manifest


def admission_receipt(
    manifest: dict,
    environment: str,
    *,
    observed_at: datetime = NOW - timedelta(seconds=30),
    fresh: dict | None = None,
) -> dict:
    recovery = manifest["initial_release_recovery"]
    return {
        "schema_version": 1,
        "kind": INITIAL_RELEASE_VERIFICATION_KIND,
        "environment": environment,
        "observed_at": observed_at.isoformat(),
        "release_id": manifest["release_id"],
        "candidate_sha": manifest["candidate_sha"],
        "manifest_digest": manifest["manifest_digest"],
        "target_environment": recovery["target_environment"],
        "recovery_method": recovery["recovery_method"],
        "recovery_actions": list(recovery["recovery_actions"]),
        "rollback_target_available": False,
        "binding_digest": recovery["binding_digest"],
        "verified_absent": True,
        "absence_readback": fresh if fresh is not None else readback(environment),
    }


@pytest.fixture(scope="module")
def staging_manifest() -> dict:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ODP_CLOUD_RUN_VPC_EGRESS", "ALL_TRAFFIC")
        return first_release()


# --------------------------------------------------------------------------
# Policy shape
# --------------------------------------------------------------------------


def test_production_is_reached_only_through_the_staging_seal() -> None:
    assert INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS == ("dev", "staging")
    assert INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS == {
        "dev": ("dev",),
        "staging": ("staging", "production"),
    }


def test_staging_names_mirror_the_staging_lifecycle() -> None:
    """The probe reads the names staging_lifecycle/Terraform will create."""

    for release_id in (RELEASE_ID, "REL_1.0", "odp-" + "x" * 60, "rel.1.0"):
        lifecycle = get_ephemeral_resource_names(release_id)
        assert staging_release_target_names(release_id) == {
            "api": lifecycle["cloud_run_api"],
            "web": lifecycle["cloud_run_web"],
            "migration": lifecycle["cloud_run_migration_job"],
            "scheduler": lifecycle["cloud_run_scheduler_job"],
            "worker": lifecycle["cloud_run_worker_job"],
        }


# --------------------------------------------------------------------------
# Build: sealing a staging first release
# --------------------------------------------------------------------------


def test_a_staging_first_release_seals_an_admissible_core_ops_manifest(
    staging_manifest: dict,
) -> None:
    manifest = staging_manifest
    recovery = manifest["initial_release_recovery"]

    assert "rollback_release" not in manifest
    assert recovery["target_environment"] == "staging"
    assert recovery["rollback_target_available"] is False
    assert manifest["release_profile"]["name"] == CORE_OPS
    assert validate_manifest(manifest, expected_candidate_sha=SHA) == []
    for environment in ("staging", "production"):
        assert validate_release_admission(manifest, environment=environment) == []
    assert any(
        "admissible only into ['staging', 'production']" in error
        for error in validate_release_admission(manifest, environment="dev")
    )


def test_a_full_profile_staging_first_release_takes_the_same_path() -> None:
    manifest = first_release(release_profile="full")

    for environment in ("staging", "production"):
        assert validate_release_admission(manifest, environment=environment) == []


def test_a_dev_sealed_first_release_never_reaches_staging_or_production() -> None:
    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    for environment in ("staging", "production"):
        errors = validate_release_admission(manifest, environment=environment)
        assert any("admissible only into ['dev']" in error for error in errors)


def test_a_staging_readback_of_another_release_namespace_is_refused() -> None:
    with pytest.raises(HandoffError) as excinfo:
        first_release(initial_release_readback=readback("staging", release_id="odp-other"))
    assert any("release-scoped staging name" in e for e in excinfo.value.errors)


def test_a_staging_target_that_is_not_empty_is_refused() -> None:
    with pytest.raises(HandoffError) as excinfo:
        first_release(initial_release_readback=readback("staging", present=("api",)))
    assert any("exists must be False" in e for e in excinfo.value.errors)


def test_an_enabled_source_keeps_the_rollback_and_snapshot_requirement() -> None:
    with pytest.raises(HandoffError) as excinfo:
        first_release(external_sources_expected_enabled=["moi_real_price"])
    assert any("啟用外部資料來源的 release 不得走" in e for e in excinfo.value.errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["initial_release_recovery"].update(target_environment="production"),
        lambda m: m["initial_release_recovery"]["absence_readback"].update(
            target_environment="production"
        ),
        lambda m: m["initial_release_recovery"].update(rollback_target_available=True),
        lambda m: m.update(release_id="odp-other-release"),
    ],
)
def test_resealing_a_tampered_staging_record_is_still_refused(
    staging_manifest: dict, mutate
) -> None:
    manifest = copy.deepcopy(staging_manifest)
    mutate(manifest)
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    assert validate_release_admission(manifest, environment="production") != []


# --------------------------------------------------------------------------
# Probe: staging by release id, production only at admission
# --------------------------------------------------------------------------


def _probe_args(fake_gcloud: Path, environment: str, *extra: str) -> list[str]:
    return [
        "--environment", environment,
        "--project", f"odayplus-{environment}",
        "--region", "asia-east1",
        "--candidate-sha", SHA,
        "--gcloud", str(fake_gcloud),
        *extra,
    ]


def test_the_staging_build_readback_is_derived_from_the_release_id(
    fake_gcloud: Path, tmp_path: Path
) -> None:
    output = tmp_path / "readback.json"
    assert probe_main(
        _probe_args(fake_gcloud, "staging", "--staging-release-id", RELEASE_ID,
                    "--output", str(output))
    ) == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == readback("staging")
    assert first_release(initial_release_readback=written)["initial_release_recovery"]


def test_a_staging_probe_refuses_long_lived_names_and_a_missing_release_id(
    fake_gcloud: Path, tmp_path: Path
) -> None:
    targets = [f"--target={c}={n}" for c, n in PRODUCTION_TARGETS.items()]
    assert probe_main(
        _probe_args(fake_gcloud, "staging", "--staging-release-id", RELEASE_ID,
                    *targets, "--output", str(tmp_path / "a.json"))
    ) == 1
    assert probe_main(
        _probe_args(fake_gcloud, "staging", "--output", str(tmp_path / "b.json"))
    ) == 1
    assert not (tmp_path / "a.json").exists()
    assert not (tmp_path / "b.json").exists()


def test_production_is_never_read_back_for_a_build(
    fake_gcloud: Path, tmp_path: Path, capsys
) -> None:
    targets = [f"--target={c}={n}" for c, n in PRODUCTION_TARGETS.items()]
    output = tmp_path / "readback.json"

    assert probe_main(
        _probe_args(fake_gcloud, "production", *targets, "--output", str(output))
    ) == 1
    assert not output.exists()
    assert "不是首次 release 的建置目標" in capsys.readouterr().err


def test_production_admission_reads_production_and_writes_a_bound_receipt(
    fake_gcloud: Path, staging_manifest: dict
) -> None:
    receipt = verify_manifest_recovery(
        staging_manifest,
        environment="production",
        project="odayplus-production",
        region="asia-east1",
        targets=dict(PRODUCTION_TARGETS),
        gcloud=str(fake_gcloud),
        now=NOW,
    )

    assert receipt["environment"] == "production"
    assert receipt["observed_at"] == NOW.isoformat()
    assert receipt["manifest_digest"] == staging_manifest["manifest_digest"]
    assert receipt["absence_readback"] == readback("production")
    assert initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment="production", now=NOW
    ) == []


def test_production_admission_refuses_a_production_that_already_runs(
    fake_gcloud: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PRESENT", json.dumps([PRODUCTION_TARGETS["api"]]))

    with pytest.raises(ProbeError) as excinfo:
        verify_manifest_recovery(
            staging_manifest,
            environment="production",
            project="odayplus-production",
            region="asia-east1",
            targets=dict(PRODUCTION_TARGETS),
            gcloud=str(fake_gcloud),
            now=NOW,
        )
    assert any("已經有既有資源" in e for e in excinfo.value.errors)


def test_a_production_that_cannot_be_read_is_not_an_empty_production(
    fake_gcloud: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UNREADABLE", "true")

    with pytest.raises(ProbeError) as excinfo:
        verify_manifest_recovery(
            staging_manifest,
            environment="production",
            project="odayplus-production",
            region="asia-east1",
            targets=dict(PRODUCTION_TARGETS),
            gcloud=str(fake_gcloud),
            now=NOW,
        )
    assert any("讀不到不等於不存在" in e for e in excinfo.value.errors)


def test_staging_admission_re_reads_the_release_namespace(
    fake_gcloud: Path, staging_manifest: dict
) -> None:
    receipt = verify_manifest_recovery(
        staging_manifest,
        environment="staging",
        project="odayplus-staging",
        region="asia-east1",
        targets={},
        gcloud=str(fake_gcloud),
        now=NOW,
    )
    assert receipt["absence_readback"] == readback("staging")

    with pytest.raises(ProbeError):
        verify_manifest_recovery(
            staging_manifest,
            environment="staging",
            project="odayplus-staging",
            region="asia-east1",
            targets={},
            gcloud=str(fake_gcloud),
            staging_release_id="odp-other-release",
            now=NOW,
        )


def test_a_dev_sealed_manifest_cannot_be_re_read_into_production(
    fake_gcloud: Path,
) -> None:
    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    with pytest.raises(ProbeError):
        verify_manifest_recovery(
            manifest,
            environment="production",
            project="odayplus-production",
            region="asia-east1",
            targets=dict(PRODUCTION_TARGETS),
            gcloud=str(fake_gcloud),
            now=NOW,
        )


def test_probe_target_absence_rejects_a_staging_release_id_elsewhere(
    fake_gcloud: Path,
) -> None:
    with pytest.raises(ProbeError):
        probe_target_absence(
            target_environment="production",
            project="odayplus-production",
            region="asia-east1",
            targets=dict(PRODUCTION_TARGETS),
            candidate_sha=SHA,
            gcloud=str(fake_gcloud),
            staging_release_id=RELEASE_ID,
        )


# --------------------------------------------------------------------------
# Admission readback receipt: missing, stale, mis-targeted, mis-bound, occupied
# --------------------------------------------------------------------------


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_fresh_bound_empty_readback_is_accepted(
    staging_manifest: dict, environment: str
) -> None:
    receipt = admission_receipt(staging_manifest, environment)
    assert initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment=environment, now=NOW
    ) == []


def _mutations() -> list[tuple[str, object, str]]:
    stale = NOW - timedelta(seconds=INITIAL_RELEASE_ADMISSION_READBACK_MAX_AGE_SECONDS + 1)
    return [
        ("missing", None, "is missing"),
        ("stale", {"observed_at": stale.isoformat()}, "is stale"),
        ("future", {"observed_at": (NOW + timedelta(seconds=5)).isoformat()}, "in the future"),
        ("naive-time", {"observed_at": "2026-10-20T08:59:30"}, "timezone-aware"),
        ("no-time", {"observed_at": None}, "timezone-aware"),
        ("environment", {"environment": "staging"}, ".environment is 'staging'"),
        ("digest", {"manifest_digest": "sha256:" + "0" * 64}, ".manifest_digest does not match"),
        ("candidate", {"candidate_sha": "0" * 40}, ".candidate_sha does not match"),
        ("release", {"release_id": "odp-other"}, ".release_id does not match"),
        ("binding", {"binding_digest": "sha256:" + "0" * 64}, ".binding_digest does not match"),
        ("not-absent", {"verified_absent": False}, "verified_absent must be True"),
        ("rollback-claim", {"rollback_target_available": True}, "rollback_target_available"),
        ("kind", {"kind": "something-else"}, ".kind must be"),
        ("extra", {"note": "trust me"}, "unexpected fields"),
        (
            "occupied",
            {"absence_readback": readback("production", present=("web",))},
            "already exists in the target",
        ),
        (
            "other-target",
            {"absence_readback": readback("staging")},
            ".target_environment is 'staging'",
        ),
    ]


@pytest.mark.parametrize(
    ("name", "change", "fragment"), _mutations(), ids=[m[0] for m in _mutations()]
)
def test_every_defective_production_readback_is_refused(
    staging_manifest: dict, name: str, change, fragment: str
) -> None:
    if change is None:
        receipt = None
    else:
        receipt = admission_receipt(staging_manifest, "production")
        receipt.update(change)

    errors = initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment="production", now=NOW
    )
    assert any(fragment in error for error in errors), errors


def test_a_staging_readback_that_drifted_from_the_seal_is_refused(
    staging_manifest: dict,
) -> None:
    drifted = readback("staging")
    drifted["project"] = "odayplus-elsewhere"
    receipt = admission_receipt(staging_manifest, "staging", fresh=drifted)

    errors = initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment="staging", now=NOW
    )
    assert any("does not match the readback sealed" in error for error in errors)


def test_the_freshness_window_is_ten_minutes_in_literal_seconds(
    staging_manifest: dict,
) -> None:
    """Pinned with literals: widening the constant must fail this test, not move it."""

    def errors_at(age_seconds: int) -> list[str]:
        receipt = admission_receipt(
            staging_manifest, "production",
            observed_at=NOW - timedelta(seconds=age_seconds),
        )
        return initial_release_verification_errors(
            receipt, manifest=staging_manifest, environment="production", now=NOW
        )

    assert errors_at(600) == []
    assert any("is stale" in error for error in errors_at(601))
    assert any("is stale" in error for error in errors_at(24 * 3600))


def test_the_freshness_window_is_inclusive_at_its_edge(staging_manifest: dict) -> None:
    edge = NOW - timedelta(seconds=INITIAL_RELEASE_ADMISSION_READBACK_MAX_AGE_SECONDS)
    receipt = admission_receipt(staging_manifest, "production", observed_at=edge)
    assert initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment="production", now=NOW
    ) == []


# --------------------------------------------------------------------------
# Durable lineage: once per lineage
# --------------------------------------------------------------------------


def _record(state: str, environment: str, digest: str) -> dict:
    return {
        "state": state,
        "lease": {
            "lease_id": "lease-x",
            "release_id": "odp-older",
            "target_environment": environment,
            "manifest_digest": digest,
        },
    }


OTHER = "sha256:" + "9" * 64
THIS = "sha256:" + "1" * 64


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_production_that_admitted_another_release_closes_the_branch(
    environment: str,
) -> None:
    errors = initial_release_lineage_errors(
        [_record(STATE_CONSUMED, "production", OTHER)],
        environment=environment,
        manifest_digest=THIS,
    )
    assert any("rollback_release" in error for error in errors)


@pytest.mark.parametrize(
    "records",
    [
        [],
        [_record(STATE_CONSUMED, "production", THIS)],
        [_record(STATE_ISSUED, "production", OTHER)],
        [_record(STATE_CONSUMED, "staging", OTHER)],
        [_record(STATE_CONSUMED, "dev", OTHER)],
    ],
    ids=["empty", "same-manifest-retry", "issued-only", "staging-attempt", "dev"],
)
def test_history_that_is_not_a_production_admission_does_not_count(records) -> None:
    for environment in ("staging", "production"):
        assert initial_release_lineage_errors(
            records, environment=environment, manifest_digest=THIS
        ) == []


@pytest.mark.parametrize("records", [None, [{"state": "consumed"}], ["garbage"]])
def test_unreadable_or_unclassifiable_history_is_refused(records) -> None:
    assert initial_release_lineage_errors(
        records, environment="production", manifest_digest=THIS
    ) != []


def test_dev_keeps_its_live_readback_semantics() -> None:
    assert initial_release_lineage_errors(
        [_record(STATE_CONSUMED, "production", OTHER)],
        environment="dev",
        manifest_digest=THIS,
    ) == []


# --------------------------------------------------------------------------
# End to end through check_runtime_admission: refused with the lease unspent
# --------------------------------------------------------------------------


def _registry(manifest_digest: str, environment: str) -> dict:
    stage, registry_env = {
        "staging": ("dev-verified", "dev"),
        "production": ("staging-verified", "staging"),
    }[environment]
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": SHA,
            "candidate_ref": "origin/dev",
            "manifest_ref": "docs/evidence/gates/RELEASE_MANIFEST.json",
            "manifest_digest": manifest_digest,
            "stage": stage,
            "environment": registry_env,
            "admission_target": environment,
            "decision": "go",
        },
        "gates": [
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": SHA,
                "stage": stage,
                "environment": registry_env,
                "admission_target": environment,
                "receipts": [
                    {"receipt_id": f"r-{index}", "release_sha": SHA, "result": "pass"}
                ],
            }
            for index in range(7)
        ],
    }


def _admit(
    tmp_path: Path,
    manifest: dict,
    *,
    environment: str,
    receipt: dict | None,
    prior_production_digest: str | None = None,
    now: datetime | None = None,
) -> tuple[int, dict, dict, LeaseStateStore]:
    private_pem, public_pem = generate_keypair()
    private_key = load_pem_private_key(private_pem, password=None)
    (tmp_path / "lease.pub").write_bytes(public_pem)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps(_registry(manifest["manifest_digest"], environment)), encoding="utf-8"
    )
    store = LeaseStateStore(tmp_path / "leases")
    if prior_production_digest is not None:
        prior = build_lease(
            task_id=TASK_ID,
            release_id="odp-older-release",
            candidate_sha="0" * 40,
            manifest_digest=prior_production_digest,
            target_environment="production",
            allowed_action="deploy",
            private_key=private_key,
        )
        store.record_issued(prior)
        store.consume(prior, consumed_by="earlier-production-admission")
    lease = build_lease(
        task_id=TASK_ID,
        release_id=manifest["release_id"],
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment=environment,
        allowed_action="deploy",
        private_key=private_key,
    )
    store.record_issued(lease)
    (tmp_path / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
    argv = [
        "--sha", SHA,
        "--environment", environment,
        "--task-id", TASK_ID,
        "--lease-file", str(tmp_path / "lease.json"),
        "--lease-state-dir", str(tmp_path / "leases"),
        "--public-key-file", str(tmp_path / "lease.pub"),
        "--registry", str(tmp_path / "registry.json"),
        "--manifest", str(tmp_path / "manifest.json"),
        "--manifest-digest", manifest["manifest_digest"],
        "--require-manifest-digest",
        "--receipt", str(tmp_path / "receipt.json"),
    ]
    if receipt is not None:
        (tmp_path / "initial.json").write_text(json.dumps(receipt), encoding="utf-8")
        argv += ["--initial-release-receipt", str(tmp_path / "initial.json")]
    code = admission_main(argv)
    result = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    return code, result, lease, store


def _fresh(manifest: dict, environment: str) -> dict:
    return admission_receipt(
        manifest, environment, observed_at=datetime.now(UTC) - timedelta(seconds=5)
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_first_release_is_admitted_and_the_lease_consumed(
    tmp_path: Path, staging_manifest: dict, environment: str
) -> None:
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment=environment,
        receipt=_fresh(staging_manifest, environment),
    )

    assert code == 0, result.get("errors")
    assert result["admitted"] is True
    assert result["release_profile"] == CORE_OPS
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


def test_a_retry_of_the_same_first_production_release_is_admitted(
    tmp_path: Path, staging_manifest: dict
) -> None:
    code, result, _, _ = _admit(
        tmp_path, staging_manifest, environment="production",
        receipt=_fresh(staging_manifest, "production"),
        prior_production_digest=staging_manifest["manifest_digest"],
    )
    assert code == 0, result.get("errors")


def _refusals(manifest: dict) -> list[tuple[str, dict]]:
    stale = _fresh(manifest, "production")
    stale["observed_at"] = (
        datetime.now(UTC)
        - timedelta(seconds=INITIAL_RELEASE_ADMISSION_READBACK_MAX_AGE_SECONDS + 60)
    ).isoformat()
    wrong_env = _fresh(manifest, "production")
    wrong_env["environment"] = "staging"
    wrong_digest = _fresh(manifest, "production")
    wrong_digest["manifest_digest"] = OTHER
    occupied = _fresh(manifest, "production")
    occupied["absence_readback"] = readback("production", present=("api", "web"))
    return [
        ("missing", {"receipt": None}),
        ("stale", {"receipt": stale}),
        ("environment", {"receipt": wrong_env}),
        ("digest", {"receipt": wrong_digest}),
        ("occupied", {"receipt": occupied}),
        (
            "lineage",
            {"receipt": _fresh(manifest, "production"), "prior_production_digest": OTHER},
        ),
    ]


@pytest.mark.parametrize("case", range(6), ids=[
    "missing", "stale", "environment", "digest", "occupied", "lineage",
])
def test_every_refusal_leaves_the_production_lease_unspent(
    tmp_path: Path, staging_manifest: dict, case: int
) -> None:
    _name, kwargs = _refusals(staging_manifest)[case]
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="production", **kwargs
    )

    assert code != 0
    assert result["admitted"] is False
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_a_dev_sealed_first_release_is_refused_by_production_admission(
    tmp_path: Path,
) -> None:
    """Even with a valid production readback, a dev seal never admits production."""

    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    code, result, lease, store = _admit(
        tmp_path, manifest, environment="production",
        receipt=_fresh(manifest, "production"),
    )

    assert code != 0
    assert any("admissible only into ['dev']" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_the_readback_guard_alone_refuses_a_dev_seal_in_production() -> None:
    """Defence in depth: the receipt check does not lean on the caller's env binding."""

    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    errors = initial_release_verification_errors(
        admission_receipt(manifest, "production"),
        manifest=manifest, environment="production", now=NOW,
    )
    assert any("admissible only into ['dev']" in e for e in errors)


def test_the_env_bound_manifest_check_alone_refuses_a_dev_seal_in_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verifier binds validate_release_admission to --environment by itself."""

    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    monkeypatch.setattr(
        check_runtime_admission, "initial_release_verification_errors",
        lambda *args, **kwargs: [],
    )
    code, result, lease, store = _admit(
        tmp_path, manifest, environment="production", receipt=None
    )

    assert code != 0
    assert any("admissible only into ['dev']" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_staging_admission_is_also_closed_by_a_production_admission(
    tmp_path: Path, staging_manifest: dict
) -> None:
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"),
        prior_production_digest=OTHER,
    )
    assert code != 0
    assert any("rollback_release" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_an_ordinary_release_is_not_asked_for_a_readback(tmp_path: Path) -> None:
    """The branch adds nothing to a release that binds its predecessor."""

    assert check_runtime_admission.initial_release_admission_errors(
        {"rollback_release": {"release_id": "x"}},
        environment="production",
        receipt=None,
        state_store=LeaseStateStore(tmp_path / "leases"),
        now=NOW,
    ) == []


# --------------------------------------------------------------------------
# Mutation: each guard is what stands between a defect and an admission
# --------------------------------------------------------------------------


def test_mutation_without_the_readback_guard_a_missing_receipt_would_admit(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "guarded").mkdir()
    (tmp_path / "mutant").mkdir()
    code, _, _, _ = _admit(
        tmp_path / "guarded", staging_manifest, environment="production", receipt=None
    )
    assert code != 0

    monkeypatch.setattr(
        check_runtime_admission, "initial_release_verification_errors",
        lambda *args, **kwargs: [],
    )
    code, result, _, _ = _admit(
        tmp_path / "mutant", staging_manifest, environment="production", receipt=None
    )
    assert code == 0, result.get("errors")


def test_mutation_without_the_lineage_guard_a_second_first_release_would_admit(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "guarded").mkdir()
    (tmp_path / "mutant").mkdir()
    receipt = _fresh(staging_manifest, "production")
    code, _, _, _ = _admit(
        tmp_path / "guarded", staging_manifest, environment="production",
        receipt=receipt, prior_production_digest=OTHER,
    )
    assert code != 0

    monkeypatch.setattr(
        check_runtime_admission, "initial_release_lineage_errors",
        lambda *args, **kwargs: [],
    )
    code, result, _, _ = _admit(
        tmp_path / "mutant", staging_manifest, environment="production",
        receipt=receipt, prior_production_digest=OTHER,
    )
    assert code == 0, result.get("errors")


def test_mutation_widening_the_admission_path_to_dev_sealed_records_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If dev records were admissible into production, this suite must notice."""

    from delivery_toolchain.release import release_manifest

    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    assert validate_release_admission(manifest, environment="production") != []

    monkeypatch.setitem(
        release_manifest.INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS,
        "dev", ("dev", "production"),
    )
    assert validate_release_admission(manifest, environment="production") == []


def test_the_workflow_passes_the_admission_readback_to_the_verifier() -> None:
    workflow = (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")

    assert "--initial-release-receipt" in workflow
    assert '--staging-release-id "odp-${ODAY_RELEASE_SHA:0:12}"' in workflow
    assert os.path.basename(
        "${RELEASE_RECEIPT_DIR}/initial-release-recovery-receipt.json"
    ) in workflow
