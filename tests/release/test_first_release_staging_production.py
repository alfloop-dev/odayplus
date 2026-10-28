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
  lease for any other manifest -- the record is usable once per lineage. That
  history lives in *production's* lease store, so a staging admission reads it
  across environments and is refused without it; a record that cannot be
  classified is refused, never skipped; and
* the record states each environment's real failure path: ephemeral staging
  holds its release-scoped namespace for forensics, production deletes the
  candidate and holds zero traffic.

Every refusal happens before the single-use lease is consumed.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from delivery_toolchain.release import check_runtime_admission, release_manifest
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
    LEASE_STATES,
    STATE_CONSUMED,
    STATE_ISSUED,
    LeaseStateError,
    LeaseStateStore,
    build_lease,
    generate_keypair,
)
from delivery_toolchain.release.release_manifest import (
    INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS,
    INITIAL_RELEASE_ADMISSION_READBACK_MAX_AGE_SECONDS,
    INITIAL_RELEASE_ELIGIBLE_ENVIRONMENTS,
    INITIAL_RELEASE_ENVIRONMENT_RECOVERY,
    INITIAL_RELEASE_LINEAGE_LEASE_STATES,
    INITIAL_RELEASE_PROBE_COMMAND,
    INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD,
    INITIAL_RELEASE_READBACK_KIND,
    INITIAL_RELEASE_RECOVERY_ACTIONS,
    INITIAL_RELEASE_RECOVERY_METHOD,
    INITIAL_RELEASE_STAGING_RECOVERY_ACTIONS,
    INITIAL_RELEASE_STAGING_RECOVERY_METHOD,
    INITIAL_RELEASE_TARGET_INVENTORY,
    INITIAL_RELEASE_VERIFICATION_KIND,
    compute_initial_release_binding_digest,
    compute_manifest_digest,
    initial_release_admission_recovery,
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
    method, actions = initial_release_admission_recovery(recovery, environment) or (
        recovery["recovery_method"], recovery["recovery_actions"]
    )
    return {
        "schema_version": 1,
        "kind": INITIAL_RELEASE_VERIFICATION_KIND,
        "environment": environment,
        "observed_at": observed_at.isoformat(),
        "release_id": manifest["release_id"],
        "candidate_sha": manifest["candidate_sha"],
        "manifest_digest": manifest["manifest_digest"],
        "target_environment": recovery["target_environment"],
        "recovery_method": method,
        "recovery_actions": list(actions),
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
        # Staging claiming the delete-candidate cleanup it does not perform.
        lambda m: m["initial_release_recovery"].update(
            recovery_method=INITIAL_RELEASE_RECOVERY_METHOD,
            recovery_actions=list(INITIAL_RELEASE_RECOVERY_ACTIONS),
        ),
        lambda m: m["initial_release_recovery"].pop(INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD),
        lambda m: m["initial_release_recovery"][
            INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD
        ].update(recovery_method=INITIAL_RELEASE_STAGING_RECOVERY_METHOD),
    ],
)
def test_resealing_a_tampered_staging_record_is_still_refused(
    staging_manifest: dict, mutate
) -> None:
    manifest = copy.deepcopy(staging_manifest)
    mutate(manifest)
    manifest["manifest_digest"] = compute_manifest_digest(manifest)

    for environment in ("staging", "production"):
        assert validate_release_admission(manifest, environment=environment) != []


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
    assert receipt["recovery_method"] == INITIAL_RELEASE_RECOVERY_METHOD
    assert receipt["recovery_actions"] == list(INITIAL_RELEASE_RECOVERY_ACTIONS)
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
    assert receipt["recovery_method"] == INITIAL_RELEASE_STAGING_RECOVERY_METHOD
    assert receipt["recovery_actions"] == list(INITIAL_RELEASE_STAGING_RECOVERY_ACTIONS)

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
        "lease_id": "lease-x",
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


def _unclassifiable() -> list[tuple[str, object]]:
    def record(**changes) -> dict:
        value = _record(STATE_CONSUMED, "production", OTHER)
        lease_changes = changes.pop("lease", {})
        value.update(changes)
        value["lease"].update(lease_changes)
        return value

    no_env = record()
    del no_env["lease"]["target_environment"]
    return [
        ("not-a-list", None),
        ("no-lease", [{"state": "consumed"}]),
        ("garbage", ["garbage"]),
        # The reviewer's probes: each of these used to be skipped as unrelated.
        ("empty-lease", [{"state": "consumed", "lease": {}}]),
        ("env-null", [record(lease={"target_environment": None})]),
        ("env-missing", [no_env]),
        ("env-unknown", [record(lease={"target_environment": "prod"})]),
        ("state-unknown", [record(state="unknown")]),
        ("state-missing", [{k: v for k, v in record().items() if k != "state"}]),
        ("lease-id-mismatch", [record(lease_id="lease-y")]),
        ("lease-id-missing", [record(lease={"lease_id": None}, lease_id=None)]),
        ("production-digest-missing", [record(lease={"manifest_digest": None})]),
        ("production-digest-malformed", [record(lease={"manifest_digest": "sha256:x"})]),
        # Corruption is not diluted by valid neighbours.
        ("one-bad-among-good", [
            _record(STATE_CONSUMED, "production", THIS),
            record(state="unknown"),
        ]),
    ]


@pytest.mark.parametrize(
    "records", [case for _, case in _unclassifiable()],
    ids=[name for name, _ in _unclassifiable()],
)
def test_unreadable_or_unclassifiable_history_is_refused(records) -> None:
    for environment in ("staging", "production"):
        assert initial_release_lineage_errors(
            records, environment=environment, manifest_digest=THIS
        ) != []


def test_the_lineage_states_mirror_the_lease_store() -> None:
    assert INITIAL_RELEASE_LINEAGE_LEASE_STATES == LEASE_STATES


def test_a_store_read_as_production_lineage_must_hold_only_production_leases() -> None:
    """Staging's own store, handed over as production's, is recognised as such."""

    staging_store_records = [_record(STATE_ISSUED, "staging", THIS)]
    assert initial_release_lineage_errors(
        staging_store_records, environment="staging", manifest_digest=THIS
    ) == []
    errors = initial_release_lineage_errors(
        staging_store_records, environment="staging", manifest_digest=THIS,
        lineage_target_store=True,
    )
    assert any("is not production's lease store" in e for e in errors)


@pytest.mark.parametrize("name", ["state-unknown", "env-unknown"])
def test_mutation_the_old_shape_check_would_skip_unclassifiable_history(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    records = dict(_unclassifiable())[name]
    assert initial_release_lineage_errors(
        records, environment="production", manifest_digest=THIS
    ) != []

    def old_shape_check(record):
        lease = record.get("lease") if isinstance(record, dict) else None
        if not isinstance(lease, dict) or not isinstance(record.get("state"), str):
            return "cannot be classified"
        return None

    monkeypatch.setattr(release_manifest, "_lineage_record_problem", old_shape_check)
    assert initial_release_lineage_errors(
        records, environment="production", manifest_digest=THIS
    ) == []


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
    lineage: str | None = "production",
    production_records: list[dict] | None = None,
    component_images: dict[str, str] | None = None,
    target_registry: str | None = None,
) -> tuple[int, dict, dict, LeaseStateStore]:
    """Admit *manifest* into *environment* with separate per-environment stores.

    As deployed, staging and production each have their own lease store
    (``vars.ODP_RELEASE_LEASE_STATE_URI`` per environment). Production's
    history -- ``prior_production_digest`` and ``production_records`` -- is
    written to production's store; a staging admission sees it only through
    ``--lineage-state-uri``. ``lineage`` is ``"production"`` (pass production's
    store to a staging admission), ``None`` (pass nothing), or a literal value.
    """

    private_pem, public_pem = generate_keypair()
    private_key = load_pem_private_key(private_pem, password=None)
    (tmp_path / "lease.pub").write_bytes(public_pem)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "registry.json").write_text(
        json.dumps(_registry(manifest["manifest_digest"], environment)), encoding="utf-8"
    )
    store = LeaseStateStore(tmp_path / "leases")
    production_dir = (
        tmp_path / "leases" if environment == "production" else tmp_path / "production-leases"
    )
    production_store = LeaseStateStore(production_dir)
    for index, raw in enumerate(production_records or []):
        (production_dir / f"lease-{index:032x}.json").write_text(
            json.dumps(raw), encoding="utf-8"
        )
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
        production_store.record_issued(prior)
        production_store.consume(prior, consumed_by="earlier-production-admission")
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
    if lineage == "production":
        if environment == "staging":
            argv += ["--lineage-state-uri", str(production_dir)]
    elif lineage is not None:
        argv += ["--lineage-state-uri", lineage]
    for name, image in (component_images or {}).items():
        argv += ["--component-image", f"{name}={image}"]
    if target_registry is not None:
        # `=` form: an unresolved registry starts with "-".
        argv += [f"--target-registry={target_registry}"]
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


def test_staging_reads_production_history_from_production_s_store(
    tmp_path: Path, staging_manifest: dict
) -> None:
    """The reviewer's case: production admitted A in its own store; B into staging.

    Staging's own store holds nothing but B's staging lease, so it alone would
    say "production never admitted anything".
    """

    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"),
        prior_production_digest=OTHER,
    )
    assert code != 0
    assert any("production already admitted" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED
    assert initial_release_lineage_errors(
        store.list_records(), environment="staging",
        manifest_digest=staging_manifest["manifest_digest"],
    ) == []


def _staging_lineage_refusals(tmp_path: Path) -> list[tuple[str, dict, str]]:
    return [
        ("not-supplied", {"lineage": None}, "needs production's lease store"),
        ("own-store", {"lineage": str(tmp_path / "leases")}, "the staging lease store itself"),
        ("absent", {"lineage": str(tmp_path / "no-such-store")}, "cannot open production"),
        (
            "not-production-store",
            {"production_records": [_record(STATE_ISSUED, "staging", OTHER)]},
            "is not production's lease store",
        ),
        (
            "unclassifiable",
            {"production_records": [{"state": "consumed", "lease": {}}]},
            "refusing an initial-release admission rather than guessing",
        ),
    ]


@pytest.mark.parametrize("case", range(5), ids=[
    "not-supplied", "own-store", "absent", "not-production-store", "unclassifiable",
])
def test_staging_without_trustworthy_production_history_is_refused_unspent(
    tmp_path: Path, staging_manifest: dict, case: int
) -> None:
    _name, kwargs, expected = _staging_lineage_refusals(tmp_path)[case]
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"), **kwargs,
    )

    assert code != 0
    assert result["admitted"] is False
    assert any(expected in e for e in result["errors"]), result["errors"]
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_unclassifiable_production_history_leaves_the_lease_unspent(
    tmp_path: Path, staging_manifest: dict, environment: str
) -> None:
    bad = _record(STATE_CONSUMED, "production", OTHER)
    bad["state"] = "unknown"
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment=environment,
        receipt=_fresh(staging_manifest, environment), production_records=[bad],
    )

    assert code != 0
    assert any("has state 'unknown'" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


# A history record that exists but is not a JSON object is damage, not
# absence. The directory backend used to return it as-is and list_records then
# dropped ``null``, so a corrupted production history read as an empty one.
NON_OBJECT_RECORDS = [None, [], ["garbage"], "garbage", 0, True]
NON_OBJECT_IDS = ["null", "empty-list", "list", "string", "number", "bool"]


@pytest.mark.parametrize("payload", NON_OBJECT_RECORDS, ids=NON_OBJECT_IDS)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_directory_store_refuses_a_history_record_that_is_not_an_object(
    tmp_path: Path, environment: str, payload: object
) -> None:
    store = LeaseStateStore(tmp_path / f"{environment}-leases")
    lease_id = "lease-" + "0" * 32
    (tmp_path / f"{environment}-leases" / f"{lease_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(LeaseStateError, match="is malformed"):
        store.get(lease_id)
    with pytest.raises(LeaseStateError, match="is malformed"):
        store.list_records()
    with pytest.raises(LeaseStateError, match="is malformed"):
        store.find_leases_for_task(TASK_ID)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_lease_record_overwritten_with_null_is_not_transitioned(
    tmp_path: Path, environment: str
) -> None:
    private_pem, _ = generate_keypair()
    lease = build_lease(
        task_id=TASK_ID, release_id="odp-null-record", candidate_sha=SHA,
        manifest_digest=THIS, target_environment=environment,
        allowed_action="deploy",
        private_key=load_pem_private_key(private_pem, password=None),
    )
    store = LeaseStateStore(tmp_path / "leases")
    store.record_issued(lease)
    path = tmp_path / "leases" / f"{lease['lease_id']}.json"
    path.write_text("null", encoding="utf-8")

    with pytest.raises(LeaseStateError, match="is malformed"):
        store.consume(lease, consumed_by="admission")
    assert path.read_text(encoding="utf-8") == "null"


@pytest.mark.parametrize("payload", NON_OBJECT_RECORDS, ids=NON_OBJECT_IDS)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_non_object_production_record_refuses_admission_unspent(
    tmp_path: Path, staging_manifest: dict, environment: str, payload: object
) -> None:
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment=environment,
        receipt=_fresh(staging_manifest, environment), production_records=[payload],
    )

    assert code != 0
    assert result["admitted"] is False
    assert any(
        "cannot read production lease history" in e and "is malformed" in e
        for e in result["errors"]
    ), result["errors"]
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_mutation_returning_a_null_record_would_admit_over_a_corrupt_history(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    """Without the object check, ``null`` is skipped and the lease is spent."""

    def old_get(self, lease_id):
        path = self._path(lease_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(LeaseStateStore, "get", old_get)
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment=environment,
        receipt=_fresh(staging_manifest, environment), production_records=[None],
    )

    assert code == 0, result.get("errors")
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


# Bytes that are not UTF-8 are damage of the same kind. ``read_text`` raises
# UnicodeDecodeError, which is neither OSError nor JSONDecodeError; unwrapped it
# escaped every caller that catches LeaseStateError and the verifier exited
# with a traceback and no admission receipt.
NOT_UTF8 = b"\xff\xfe{}"


def test_the_directory_store_refuses_a_record_that_is_not_utf8(tmp_path: Path) -> None:
    store = LeaseStateStore(tmp_path / "leases")
    lease_id = "lease-" + "0" * 32
    (tmp_path / "leases" / f"{lease_id}.json").write_bytes(NOT_UTF8)

    with pytest.raises(LeaseStateError, match="is unreadable"):
        store.get(lease_id)
    with pytest.raises(LeaseStateError, match="is unreadable"):
        store.list_records()
    with pytest.raises(LeaseStateError, match="is unreadable"):
        store.find_leases_for_task(TASK_ID)


def test_a_lease_record_overwritten_with_invalid_utf8_is_not_transitioned(
    tmp_path: Path,
) -> None:
    private_pem, _ = generate_keypair()
    lease = build_lease(
        task_id=TASK_ID, release_id="odp-bad-bytes", candidate_sha=SHA,
        manifest_digest=THIS, target_environment="production",
        allowed_action="deploy",
        private_key=load_pem_private_key(private_pem, password=None),
    )
    store = LeaseStateStore(tmp_path / "leases")
    store.record_issued(lease)
    path = tmp_path / "leases" / f"{lease['lease_id']}.json"
    path.write_bytes(NOT_UTF8)

    with pytest.raises(LeaseStateError, match="is unreadable"):
        store.consume(lease, consumed_by="admission")
    with pytest.raises(LeaseStateError, match="is unreadable"):
        store.revoke(lease, reason="operator")
    assert path.read_bytes() == NOT_UTF8


def _plant_not_utf8_production_record(tmp_path: Path, environment: str) -> None:
    # Where _admit keeps production's store for each admission environment.
    production_dir = tmp_path / ("leases" if environment == "production" else "production-leases")
    production_dir.mkdir()
    (production_dir / f"lease-{'f' * 32}.json").write_bytes(NOT_UTF8)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_an_invalid_utf8_production_record_refuses_admission_with_a_receipt(
    tmp_path: Path, staging_manifest: dict, environment: str
) -> None:
    _plant_not_utf8_production_record(tmp_path, environment)
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment=environment,
        receipt=_fresh(staging_manifest, environment),
    )

    assert code != 0
    assert result["admitted"] is False
    assert any(
        "cannot read production lease history" in e and "is unreadable" in e
        for e in result["errors"]
    ), result["errors"]
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_mutation_without_the_utf8_wrap_the_verifier_dies_without_a_receipt(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    """The refusal above is the wrap's doing: unwrapped, nothing is recorded."""

    def old_get(self, lease_id):
        path = self._path(lease_id)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LeaseStateError(f"lease state record {path} is unreadable: {exc}") from exc
        if not isinstance(record, dict):
            raise LeaseStateError(f"lease state record {path} is malformed")
        return record

    _plant_not_utf8_production_record(tmp_path, environment)
    monkeypatch.setattr(LeaseStateStore, "get", old_get)
    with pytest.raises(UnicodeDecodeError):
        _admit(
            tmp_path, staging_manifest, environment=environment,
            receipt=_fresh(staging_manifest, environment),
        )
    assert not (tmp_path / "receipt.json").exists()


def test_production_refuses_a_lineage_store_other_than_its_own(
    tmp_path: Path, staging_manifest: dict
) -> None:
    (tmp_path / "elsewhere").mkdir()
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="production",
        receipt=_fresh(staging_manifest, "production"),
        lineage=str(tmp_path / "elsewhere"),
    )

    assert code != 0
    assert any("reads its lineage from its own lease store" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_an_ordinary_staging_release_never_opens_production_s_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--lineage-state-uri is ignored unless the manifest is a first release."""

    opened: list[str] = []
    real = check_runtime_admission.LeaseStateStore

    def recording(path, **kwargs):
        opened.append(str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(check_runtime_admission, "LeaseStateStore", recording)
    (tmp_path / "leases").mkdir()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"rollback_release": {"release_id": "x"}}), encoding="utf-8"
    )
    check_runtime_admission.main([
        "--sha", SHA, "--environment", "staging", "--task-id", TASK_ID,
        "--lease-file", str(tmp_path / "missing-lease.json"),
        "--lease-state-dir", str(tmp_path / "leases"),
        "--manifest", str(tmp_path / "manifest.json"),
        "--lineage-state-uri", str(tmp_path / "production-leases"),
        "--receipt", str(tmp_path / "receipt.json"),
    ])
    assert str(tmp_path / "production-leases") not in opened


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


def test_mutation_reading_staging_s_own_store_would_admit_after_production(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reverting to the staging store as lineage source re-opens the bypass."""

    (tmp_path / "guarded").mkdir()
    (tmp_path / "mutant").mkdir()
    receipt = _fresh(staging_manifest, "staging")
    code, _, _, _ = _admit(
        tmp_path / "guarded", staging_manifest, environment="staging",
        receipt=receipt, prior_production_digest=OTHER,
    )
    assert code != 0

    # The pre-fix verifier: staging answers the lineage question from the
    # store it was admitted against, i.e. its own.
    monkeypatch.setattr(check_runtime_admission, "INITIAL_RELEASE_LINEAGE_TARGET", "staging")
    code, result, lease, store = _admit(
        tmp_path / "mutant", staging_manifest, environment="staging",
        receipt=receipt, prior_production_digest=OTHER, lineage=None,
    )
    assert code == 0, result.get("errors")
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


def test_mutation_widening_the_admission_path_to_dev_sealed_records_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If dev records were admissible into production, this suite must notice.

    Two guards stand in the way: the admission path itself, and the missing
    production ``promotion_recovery`` a dev seal never carries.
    """

    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    assert validate_release_admission(manifest, environment="production") != []

    monkeypatch.setitem(
        release_manifest.INITIAL_RELEASE_ADMISSIBLE_ENVIRONMENTS,
        "dev", ("dev", "production"),
    )
    widened = validate_release_admission(manifest, environment="production")
    assert widened and all(
        INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD in error for error in widened
    ), widened

    monkeypatch.setattr(release_manifest, "expected_promotion_recovery", lambda _env: None)
    assert validate_release_admission(manifest, environment="production") == []


# --------------------------------------------------------------------------
# Recovery semantics: each environment's record says what its failure path does
# --------------------------------------------------------------------------


def test_staging_seals_its_forensic_hold_and_production_s_cleanup(
    staging_manifest: dict,
) -> None:
    recovery = staging_manifest["initial_release_recovery"]

    assert recovery["recovery_method"] == INITIAL_RELEASE_STAGING_RECOVERY_METHOD
    assert recovery["recovery_actions"] == list(INITIAL_RELEASE_STAGING_RECOVERY_ACTIONS)
    assert recovery[INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD] == {
        "environment": "production",
        "recovery_method": INITIAL_RELEASE_RECOVERY_METHOD,
        "recovery_actions": list(INITIAL_RELEASE_RECOVERY_ACTIONS),
    }
    assert initial_release_admission_recovery(recovery, "staging") == (
        INITIAL_RELEASE_STAGING_RECOVERY_METHOD,
        list(INITIAL_RELEASE_STAGING_RECOVERY_ACTIONS),
    )
    assert initial_release_admission_recovery(recovery, "production") == (
        INITIAL_RELEASE_RECOVERY_METHOD,
        list(INITIAL_RELEASE_RECOVERY_ACTIONS),
    )
    assert initial_release_admission_recovery(recovery, "dev") is None


def test_the_staging_recovery_promises_no_deletion_and_no_traffic_change() -> None:
    method, actions = INITIAL_RELEASE_ENVIRONMENT_RECOVERY["staging"]
    for word in ("delete", "zero-traffic"):
        assert word not in method
        assert not any(word in action for action in actions)
    assert INITIAL_RELEASE_ENVIRONMENT_RECOVERY["dev"] == (
        INITIAL_RELEASE_RECOVERY_METHOD, INITIAL_RELEASE_RECOVERY_ACTIONS
    )
    assert INITIAL_RELEASE_ENVIRONMENT_RECOVERY["production"] == (
        INITIAL_RELEASE_RECOVERY_METHOD, INITIAL_RELEASE_RECOVERY_ACTIONS
    )


def test_the_staging_record_matches_the_workflow_s_staging_failure_path() -> None:
    """Anti-drift: staging failure holds; it neither deletes nor moves traffic."""

    workflow = (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    start = workflow.index("- name: Hold ephemeral staging resources on failure")
    end = workflow.index("- name:", start + 1)
    step = workflow[start:end]

    assert "failure() && inputs.environment == 'staging'" in step
    assert "staging_lifecycle.py hold" in step
    for forbidden in ("delete", "update-traffic", "cleanup"):
        assert forbidden not in step


def test_a_dev_seal_is_unchanged_and_carries_no_promotion() -> None:
    manifest = first_release(
        initial_release_readback=readback("dev"), target_environment="dev",
        release_profile="full",
    )
    recovery = manifest["initial_release_recovery"]

    assert INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD not in recovery
    assert recovery["recovery_method"] == INITIAL_RELEASE_RECOVERY_METHOD
    # A dev record's binding digest is computed exactly as before this change.
    assert recovery["binding_digest"] == compute_initial_release_binding_digest(
        candidate_sha=SHA,
        components=manifest["components"],
        target_environment="dev",
        recovery_method=INITIAL_RELEASE_RECOVERY_METHOD,
        readback=recovery["absence_readback"],
    )

    forged = copy.deepcopy(manifest)
    forged["initial_release_recovery"][INITIAL_RELEASE_PROMOTION_RECOVERY_FIELD] = {
        "environment": "production",
        "recovery_method": INITIAL_RELEASE_RECOVERY_METHOD,
        "recovery_actions": list(INITIAL_RELEASE_RECOVERY_ACTIONS),
    }
    forged["manifest_digest"] = compute_manifest_digest(forged)
    assert any(
        "is not allowed for a record sealed for 'dev'" in error
        for error in validate_release_admission(forged, environment="dev")
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_a_receipt_stating_the_other_environment_s_recovery_is_refused(
    staging_manifest: dict, environment: str
) -> None:
    other = "production" if environment == "staging" else "staging"
    receipt = admission_receipt(staging_manifest, environment)
    method, actions = initial_release_admission_recovery(
        staging_manifest["initial_release_recovery"], other
    )
    receipt.update(recovery_method=method, recovery_actions=list(actions))

    errors = initial_release_verification_errors(
        receipt, manifest=staging_manifest, environment=environment, now=NOW
    )
    assert any("recovery_method does not match" in e for e in errors)
    assert any("recovery_actions does not match" in e for e in errors)


def test_the_workflow_passes_the_admission_readback_to_the_verifier() -> None:
    workflow = (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")

    assert "--initial-release-receipt" in workflow
    assert "--lineage-state-uri" in workflow
    assert "vars.ODP_PRODUCTION_RELEASE_LEASE_STATE_URI" in workflow
    assert '--staging-release-id "odp-${ODAY_RELEASE_SHA:0:12}"' in workflow
    assert os.path.basename(
        "${RELEASE_RECEIPT_DIR}/initial-release-recovery-receipt.json"
    ) in workflow


def _lease_store_validation_step() -> dict:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["admission"]["steps"]
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    # Refused before the admission step that consumes the lease.
    assert names.index("Validate shared lease store configuration") < names.index(
        "Validate supervisor release admission"
    )
    return next(
        step for step in steps
        if isinstance(step, dict) and step.get("name") == "Validate shared lease store configuration"
    )


def _run_lease_store_validation(environment: str, production_uri: str | None) -> subprocess.CompletedProcess:
    step = _lease_store_validation_step()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RELEASE_ENVIRONMENT": environment,
        "RELEASE_LEASE_STATE_URI": "gs://odayplus-runtime-20260825-release-leases/leases",
    }
    if production_uri is not None:
        env["PRODUCTION_LEASE_STATE_URI"] = production_uri
    return subprocess.run(
        ["bash", "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )


def test_the_lineage_store_is_validated_from_the_staging_environment_s_variable() -> None:
    step = _lease_store_validation_step()
    assert step["env"]["RELEASE_ENVIRONMENT"] == "${{ inputs.environment }}"
    assert step["env"]["PRODUCTION_LEASE_STATE_URI"] == (
        "${{ vars.ODP_PRODUCTION_RELEASE_LEASE_STATE_URI }}"
    )


@pytest.mark.parametrize(
    "production_uri",
    [".", "/home/runner/production-leases", "gs://odayplus-prod-20260826-release-leases",
     "gs://odayplus-prod-20260826-release-leases/", "gs:///leases",
     "file:///production-leases", " "],
    ids=["cwd", "local-dir", "bare-bucket", "empty-prefix", "no-bucket", "file-uri", "blank"],
)
def test_staging_refuses_a_production_lineage_store_that_is_not_shared(
    production_uri: str,
) -> None:
    result = _run_lease_store_validation("staging", production_uri)

    assert result.returncode == 1
    assert "ODP_PRODUCTION_RELEASE_LEASE_STATE_URI must be production's shared" in result.stderr


@pytest.mark.parametrize(
    "environment,production_uri",
    [
        ("staging", "gs://odayplus-prod-20260826-release-leases/leases"),
        # Unset: not passed; the verifier refuses a staging first release itself
        # (test_staging_without_trustworthy_production_history_is_refused_unspent).
        ("staging", None),
        ("staging", ""),
        # Only staging hands this store to the verifier.
        ("production", "."),
        ("dev", "."),
    ],
    ids=["staging-gs", "staging-unset", "staging-empty", "production-ignores", "dev-ignores"],
)
def test_the_lineage_shape_check_admits_a_shared_store_or_none(
    environment: str, production_uri: str | None
) -> None:
    result = _run_lease_store_validation(environment, production_uri)

    assert result.returncode == 0, result.stderr


def test_production_admissions_are_serialised_by_the_workflow_concurrency_group() -> None:
    """The premise that makes the non-atomic lineage read and consume safe.

    Two production deploy runs share one group and never interleave, so the
    later one reads production's lineage only after the earlier one consumed
    its lease, and is refused (design doc §5.7).
    """

    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8")
    )
    concurrency = workflow["concurrency"]
    assert concurrency["group"] == (
        "runtime-release-${{ inputs.environment }}-"
        "${{ inputs.phase == 'build' && 'build' || 'deploy' }}"
    )
    assert concurrency["cancel-in-progress"] is False
    # The lease-consuming jobs do not opt out into a group of their own.
    for job in ("admission", "automatic_admission", "deploy"):
        assert "concurrency" not in workflow["jobs"][job]


# --------------------------------------------------------------------------
# ODP-PROD-IMAGE-PROMOTION-001: production deploys from its own registry
# --------------------------------------------------------------------------

PRODUCTION_REGISTRY = "asia-east1-docker.pkg.dev/odayplus-prod/oday-plus"


def _manifest_images(manifest: dict) -> dict[str, str]:
    return {name: component["image"] for name, component in manifest["components"].items()}


def _promoted_images(manifest: dict, registry: str = PRODUCTION_REGISTRY) -> dict[str, str]:
    return {
        name: release_manifest.promoted_image_ref(image, registry)
        for name, image in _manifest_images(manifest).items()
    }


def test_the_promoted_reference_replaces_only_the_repository_root() -> None:
    image = f"{REPO}/oday-staging-api@sha256:{'1' * 64}"
    assert release_manifest.promoted_image_ref(image, PRODUCTION_REGISTRY) == (
        f"{PRODUCTION_REGISTRY}/oday-staging-api@sha256:{'1' * 64}"
    )
    nested = f"{REPO}/team/api@sha256:{'1' * 64}"
    assert release_manifest.promoted_image_ref(nested, PRODUCTION_REGISTRY) == (
        f"{PRODUCTION_REGISTRY}/team/api@sha256:{'1' * 64}"
    )
    for not_promotable in (
        f"{REPO}/api:release-{SHA}",
        f"ghcr.io/alfloop-dev/api@sha256:{'1' * 64}",
        f"{REPO}@sha256:{'1' * 64}",
        f"{REPO}/API@sha256:{'1' * 64}",
        None,
    ):
        assert release_manifest.promoted_image_ref(not_promotable, PRODUCTION_REGISTRY) is None
    for registry in ("-docker.pkg.dev//", PRODUCTION_REGISTRY + "/", "asia-east1-docker.pkg.dev/odayplus-prod", "ghcr.io/a/b", None):
        assert release_manifest.promoted_image_ref(image, registry) is None


@pytest.mark.parametrize(
    "handoff,target_registry",
    [("manifest", None), ("manifest", PRODUCTION_REGISTRY), ("promoted", PRODUCTION_REGISTRY)],
    ids=["manifest-images", "manifest-images-with-registry", "promoted-images"],
)
def test_production_admits_the_manifest_images_or_their_promoted_copies(
    tmp_path: Path, staging_manifest: dict, handoff: str, target_registry: str | None
) -> None:
    images = (
        _manifest_images(staging_manifest)
        if handoff == "manifest"
        else _promoted_images(staging_manifest)
    )
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="production",
        receipt=_fresh(staging_manifest, "production"),
        component_images=images, target_registry=target_registry,
    )

    assert code == 0, result.get("errors")
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED
    assert result["component_images"] == dict(sorted(images.items()))
    assert result["target_registry"] == target_registry
    # The lease stays bound to the unchanged manifest.
    assert result["manifest_digest"] == staging_manifest["manifest_digest"]


def _refused_handoffs(manifest: dict) -> list[tuple[str, dict[str, str], str | None]]:
    promoted = _promoted_images(manifest)
    api_digest = promoted["api"].split("@", 1)[1]

    def with_api(image: str) -> dict[str, str]:
        return {**promoted, "api": image}

    return [
        ("other-digest", with_api(f"{PRODUCTION_REGISTRY}/api@sha256:{'e' * 64}"), PRODUCTION_REGISTRY),
        ("other-name", with_api(f"{PRODUCTION_REGISTRY}/web@{api_digest}"), PRODUCTION_REGISTRY),
        ("nested-name", with_api(f"{PRODUCTION_REGISTRY}/x/api@{api_digest}"), PRODUCTION_REGISTRY),
        (
            "third-registry",
            with_api(f"asia-east1-docker.pkg.dev/someone-else/oday-plus/api@{api_digest}"),
            PRODUCTION_REGISTRY,
        ),
        ("other-region", with_api(f"us-central1-docker.pkg.dev/odayplus-prod/oday-plus/api@{api_digest}"), PRODUCTION_REGISTRY),
        ("tag", with_api(f"{PRODUCTION_REGISTRY}/api:release-{SHA}"), PRODUCTION_REGISTRY),
        # Without the target registry production is held to the exact manifest refs.
        ("promoted-without-registry", promoted, None),
        # The registry named to admission is the only extra one accepted.
        ("promoted-into-another-registry", _promoted_images(manifest, "asia-east1-docker.pkg.dev/odayplus-other/oday-plus"), PRODUCTION_REGISTRY),
        ("unresolved-variables", _manifest_images(manifest), "-docker.pkg.dev//"),
        ("registry-without-repository", promoted, "asia-east1-docker.pkg.dev/odayplus-prod"),
    ]


@pytest.mark.parametrize("case", range(10), ids=[
    "other-digest", "other-name", "nested-name", "third-registry", "other-region", "tag",
    "promoted-without-registry", "promoted-into-another-registry", "unresolved-variables",
    "registry-without-repository",
])
def test_production_refuses_any_other_handoff_with_the_lease_unspent(
    tmp_path: Path, staging_manifest: dict, case: int
) -> None:
    _name, images, target_registry = _refused_handoffs(staging_manifest)[case]
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="production",
        receipt=_fresh(staging_manifest, "production"),
        component_images=images, target_registry=target_registry,
    )

    assert code != 0
    assert result["admitted"] is False
    assert any("handoff image" in e or "target registry" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


@pytest.mark.parametrize("handoff", ["manifest", "promoted"])
def test_staging_is_refused_a_target_registry_with_the_lease_unspent(
    tmp_path: Path, staging_manifest: dict, handoff: str
) -> None:
    """dev and staging deploy what the manifest records; nothing widens that."""

    images = (
        _manifest_images(staging_manifest)
        if handoff == "manifest"
        else _promoted_images(staging_manifest)
    )
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"),
        component_images=images, target_registry=PRODUCTION_REGISTRY,
    )

    assert code != 0
    assert any("--target-registry is accepted only for ['production']" in e for e in result["errors"])
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


def test_staging_still_admits_exactly_the_manifest_images(
    tmp_path: Path, staging_manifest: dict
) -> None:
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"),
        component_images=_manifest_images(staging_manifest),
    )

    assert code == 0, result.get("errors")
    assert result["target_registry"] is None
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


def test_mutation_binding_on_digest_alone_would_admit_another_image_name(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name and registry checks, not the digest, are what refuse these."""

    def digest_only(manifest, images, *, target_registry=None):
        recorded = {
            name: component["image"].split("@", 1)[1]
            for name, component in manifest["components"].items()
        }
        return [
            f"handoff image for {name!r} has another digest"
            for name, image in images.items()
            if image.split("@", 1)[-1] != recorded.get(name)
        ]

    for case in ("other-name", "third-registry"):
        index = [name for name, _, _ in _refused_handoffs(staging_manifest)].index(case)
        _name, images, target_registry = _refused_handoffs(staging_manifest)[index]
        real_dir = tmp_path / f"real-{case}"
        real_dir.mkdir()
        code, _, _, _ = _admit(
            real_dir, staging_manifest, environment="production",
            receipt=_fresh(staging_manifest, "production"),
            component_images=images, target_registry=target_registry,
        )
        assert code != 0

        with monkeypatch.context() as patch:
            patch.setattr(check_runtime_admission, "component_binding_errors", digest_only)
            mutated_dir = tmp_path / f"mutated-{case}"
            mutated_dir.mkdir()
            code, result, lease, store = _admit(
                mutated_dir, staging_manifest, environment="production",
                receipt=_fresh(staging_manifest, "production"),
                component_images=images, target_registry=target_registry,
            )
        assert code == 0, result.get("errors")
        assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


def test_mutation_without_the_environment_restriction_staging_would_admit_promoted_images(
    tmp_path: Path, staging_manifest: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        check_runtime_admission, "IMAGE_PROMOTION_ENVIRONMENTS", ("staging", "production")
    )
    code, result, lease, store = _admit(
        tmp_path, staging_manifest, environment="staging",
        receipt=_fresh(staging_manifest, "staging"),
        component_images=_promoted_images(staging_manifest),
        target_registry=PRODUCTION_REGISTRY,
    )

    assert code == 0, result.get("errors")
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED
