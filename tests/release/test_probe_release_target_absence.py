"""The target readback that admits a first release, and what it refuses.

`initial_release_recovery` is worth something only because the claim behind it
-- "this target holds no approved release" -- is *read back* rather than
declared. This module covers the reading:

* a `gcloud` that fails is not a target that is empty. Not finding a service and
  not being able to look are different facts, and reading the second as the
  first is how a broken credential would admit a fake first release; and
* build-time truth is not deploy-time truth. The readback was taken in another
  dispatch on another runner, so admission takes it again before the lease is
  spent -- the one part of this branch that cannot be forged from the tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from delivery_toolchain.release.build_release_handoff import HandoffError, build_handoff
from delivery_toolchain.release.probe_release_target_absence import (
    ProbeError,
    main,
    probe_target_absence,
)
from delivery_toolchain.release.release_manifest import (
    INITIAL_RELEASE_PROBE_COMMAND,
    INITIAL_RELEASE_READBACK_KIND,
    INITIAL_RELEASE_RECOVERY_METHOD,
    INITIAL_RELEASE_TARGET_INVENTORY,
    initial_release_readback_errors,
    release_candidate_job_name,
    validate_release_admission,
)

ROOT = Path(__file__).resolve().parents[2]
SHA = "b" * 40
CREATED_AT = "2026-08-26T12:00:00+00:00"
REPO = "asia-east1-docker.pkg.dev/odayplus/oday-plus-dev"

TARGETS = {
    component: f"oday-plus-{component}"
    for component, _kind in INITIAL_RELEASE_TARGET_INVENTORY
}

# A `gcloud` stand-in that answers from the environment instead of the cloud.
# Services use an exact-name filter. Jobs intentionally return the complete
# list, because the production probe must catch old SHA-suffixed release Jobs,
# not just the current candidate name (or a never-used base name).
FAKE_GCLOUD = '''#!/usr/bin/env python3
import json
import os
import sys

present = set(json.loads(os.environ.get("FAKE_PRESENT", "[]")))
unreadable = set(json.loads(os.environ.get("FAKE_UNREADABLE", "[]")))
ambiguous = set(json.loads(os.environ.get("FAKE_AMBIGUOUS", "[]")))

name = ""
for argument in sys.argv[1:]:
    if argument.startswith("--filter=metadata.name="):
        name = argument.split("=", 2)[2]

is_jobs_list = sys.argv[1:3] == ["run", "jobs"] and "list" in sys.argv
if unreadable:
    print("PERMISSION_DENIED: caller lacks run.services.list", file=sys.stderr)
    raise SystemExit(1)
if is_jobs_list:
    for resource in sorted(present):
        print(resource)
    for resource in sorted(ambiguous):
        print(resource)
        print(resource + "-2")
elif name in ambiguous:
    print(name)
    print(name + "-2")
elif name in present:
    print(name)
raise SystemExit(0)
'''


@pytest.fixture(autouse=True)
def resolved_sources_off_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model the build environment's non-secret VPC egress resolution."""

    monkeypatch.setenv("ODP_CLOUD_RUN_VPC_EGRESS", "ALL_TRAFFIC")


@pytest.fixture
def fake_gcloud(tmp_path: Path) -> Path:
    path = tmp_path / "fake-gcloud"
    path.write_text(FAKE_GCLOUD, encoding="utf-8")
    path.chmod(0o755)
    return path


def set_cloud_state(
    monkeypatch: pytest.MonkeyPatch,
    *,
    present: list[str] | None = None,
    unreadable: list[str] | None = None,
    ambiguous: list[str] | None = None,
) -> None:
    monkeypatch.setenv("FAKE_PRESENT", json.dumps(present or []))
    monkeypatch.setenv("FAKE_UNREADABLE", json.dumps(unreadable or []))
    monkeypatch.setenv("FAKE_AMBIGUOUS", json.dumps(ambiguous or []))


def ref(name: str, fill: str) -> str:
    return f"{REPO}/{name}@sha256:{fill * 64}"


def first_release_manifest(readback: dict) -> dict:
    _, manifest = build_handoff(
        release_sha=SHA,
        components={
            "api": ref("api", "1"),
            "web": ref("web", "2"),
            "worker": ref("worker", "3"),
            "scheduler": ref("scheduler", "4"),
        },
        sbom_refs=[ref("api", "5")],
        signature_refs=[ref("api", "6")],
        initial_release_readback=readback,
        target_environment="dev",
        created_at=CREATED_AT,
        created_by_workflow=(
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@" + SHA
        ),
        root=ROOT,
    )
    return manifest


def probe_argv(fake_gcloud: Path, **extra: str) -> list[str]:
    argv = [
        "--environment",
        extra.pop("environment", "dev"),
        "--project",
        extra.pop("project", "odayplus"),
        "--region",
        extra.pop("region", "asia-east1"),
        "--candidate-sha",
        extra.pop("candidate_sha", SHA),
        "--gcloud",
        str(fake_gcloud),
    ]
    for component, resource_name in TARGETS.items():
        argv.extend(["--target", f"{component}={resource_name}"])
    for flag, value in extra.items():
        argv.extend([f"--{flag.replace('_', '-')}", str(value)])
    return argv


# --------------------------------------------------------------------------
# Build phase: reading the target
# --------------------------------------------------------------------------


def test_an_empty_target_produces_a_readback_the_handoff_can_bind(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    set_cloud_state(monkeypatch)
    output = tmp_path / "initial-release-absence-readback.json"

    assert main(probe_argv(fake_gcloud, output=str(output))) == 0

    readback = json.loads(output.read_text(encoding="utf-8"))
    assert readback["kind"] == INITIAL_RELEASE_READBACK_KIND
    assert readback["probe_command"] == INITIAL_RELEASE_PROBE_COMMAND
    assert initial_release_readback_errors(readback, target_environment="dev") == []
    for component, resource_kind in INITIAL_RELEASE_TARGET_INVENTORY:
        entry = next(item for item in readback["targets"] if item["component"] == component)
        if resource_kind == "cloud-run-job":
            assert entry["resource_name"] == release_candidate_job_name(
                TARGETS[component], SHA
            )
        else:
            assert entry["resource_name"] == TARGETS[component]

    manifest = first_release_manifest(readback)
    assert validate_release_admission(manifest, environment="dev") == []


def test_the_readback_carries_no_wall_clock_value(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """A lease is bound to manifest_digest, which is bound to this readback.

    Re-running the build phase for the same release SHA has to reproduce the
    handoff byte for byte, so the readback records *what is there* and never
    *when it was looked at*.
    """

    set_cloud_state(monkeypatch)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(probe_argv(fake_gcloud, output=str(first))) == 0
    assert main(probe_argv(fake_gcloud, output=str(second))) == 0

    assert first.read_bytes() == second.read_bytes()
    serialised = first.read_text(encoding="utf-8")
    for forbidden in ("observed_at", "timestamp", "run_id", "20"):
        assert forbidden not in serialised.replace("oday-plus", "")


def test_a_target_that_already_holds_a_service_refuses_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path, capsys
) -> None:
    set_cloud_state(monkeypatch, present=[TARGETS["api"]])
    output = tmp_path / "readback.json"

    assert main(probe_argv(fake_gcloud, output=str(output))) == 1

    assert not output.exists()
    assert "api" in capsys.readouterr().err


def test_a_leftover_migration_job_makes_the_target_not_empty(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """Jobs are deploy targets too, and a leftover one is prior state."""

    set_cloud_state(monkeypatch, present=[TARGETS["migration"]])

    assert main(probe_argv(fake_gcloud, output=str(tmp_path / "readback.json"))) == 1


def test_a_leftover_sha_suffixed_release_job_makes_the_target_not_empty(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    old_release_job = release_candidate_job_name(TARGETS["worker"], "a" * 40)
    set_cloud_state(monkeypatch, present=[old_release_job])

    assert main(probe_argv(fake_gcloud, output=str(tmp_path / "readback.json"))) == 1


def test_a_base_job_readback_cannot_be_bound_to_a_candidate(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    set_cloud_state(monkeypatch)
    output = tmp_path / "readback.json"
    assert main(probe_argv(fake_gcloud, output=str(output))) == 0
    readback = json.loads(output.read_text(encoding="utf-8"))
    for entry in readback["targets"]:
        if entry["resource_kind"] == "cloud-run-job":
            entry["resource_name"] = TARGETS[entry["component"]]

    with pytest.raises(HandoffError, match="SHA-suffixed candidate Job"):
        first_release_manifest(readback)


def test_a_gcloud_that_cannot_look_is_not_a_target_that_is_empty(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path, capsys
) -> None:
    """The failure mode this branch is most exposed to.

    A `describe` that exits non-zero means "not found" or "no permission" and
    does not say which. Reading a broken credential as an empty environment
    would admit a first release into a target that already holds one.
    """

    set_cloud_state(monkeypatch, unreadable=[TARGETS["web"]])
    output = tmp_path / "readback.json"

    assert main(probe_argv(fake_gcloud, output=str(output))) == 1

    assert not output.exists()
    assert "讀不到不等於不存在" in capsys.readouterr().err


def test_an_ambiguous_lookup_is_refused_rather_than_narrowed(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    set_cloud_state(monkeypatch, ambiguous=[TARGETS["scheduler"]])

    assert main(probe_argv(fake_gcloud, output=str(tmp_path / "readback.json"))) == 1


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_only_dev_may_be_probed_for_a_first_release(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path, environment: str
) -> None:
    set_cloud_state(monkeypatch)

    assert (
        main(
            probe_argv(
                fake_gcloud,
                environment=environment,
                output=str(tmp_path / "readback.json"),
            )
        )
        == 1
    )


def test_an_unnamed_deploy_target_cannot_be_read_back(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """An environment that does not bind the migration job name fails here.

    That is deliberate: it is the only step that needs the name, so the missing
    binding refuses this branch instead of refusing every build.
    """

    set_cloud_state(monkeypatch)
    argv = [
        "--environment",
        "dev",
        "--project",
        "odayplus",
        "--region",
        "asia-east1",
        "--candidate-sha",
        SHA,
        "--gcloud",
        str(fake_gcloud),
        "--output",
        str(tmp_path / "readback.json"),
    ]
    for component, resource_name in TARGETS.items():
        argv.extend(
            ["--target", f"{component}={'' if component == 'migration' else resource_name}"]
        )

    assert main(argv) == 1


def test_a_partial_target_list_cannot_prove_an_empty_environment(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path
) -> None:
    set_cloud_state(monkeypatch)

    with pytest.raises(ProbeError) as excinfo:
        probe_target_absence(
            target_environment="dev",
            project="odayplus",
            region="asia-east1",
            targets={"api": TARGETS["api"], "web": TARGETS["web"]},
            candidate_sha=SHA,
            gcloud=str(fake_gcloud),
        )
    assert any("缺少部署 target" in error for error in excinfo.value.errors)


# --------------------------------------------------------------------------
# Admission phase: reading the target again
# --------------------------------------------------------------------------


def test_an_ordinary_release_is_not_asked_about_an_empty_target(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """The admission step is unconditional, so it must no-op for normal releases.

    Gating it on the dispatch input instead would let a forged first-release
    manifest skip the re-read by simply not setting the input.
    """

    set_cloud_state(monkeypatch, present=list(TARGETS.values()))
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(
        (ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert main(probe_argv(fake_gcloud, manifest=str(manifest_path))) == 0


def test_admission_re_reads_the_target_and_records_what_it_bound(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    set_cloud_state(monkeypatch)
    output = tmp_path / "readback.json"
    assert main(probe_argv(fake_gcloud, output=str(output))) == 0
    readback = json.loads(output.read_text(encoding="utf-8"))

    manifest = first_release_manifest(readback)
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt_path = tmp_path / "initial-release-recovery-receipt.json"

    assert (
        main(probe_argv(fake_gcloud, manifest=str(manifest_path), receipt=str(receipt_path)))
        == 0
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["candidate_sha"] == SHA
    assert receipt["manifest_digest"] == manifest["manifest_digest"]
    assert receipt["target_environment"] == "dev"
    assert receipt["recovery_method"] == INITIAL_RELEASE_RECOVERY_METHOD
    assert receipt["rollback_target_available"] is False
    assert receipt["verified_absent"] is True


def test_a_target_that_filled_up_between_build_and_deploy_fails_closed(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """Build-time truth is not deploy-time truth, and the lease is not yet spent."""

    set_cloud_state(monkeypatch)
    output = tmp_path / "readback.json"
    assert main(probe_argv(fake_gcloud, output=str(output))) == 0
    manifest = first_release_manifest(json.loads(output.read_text(encoding="utf-8")))
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    set_cloud_state(monkeypatch, present=[TARGETS["api"], TARGETS["web"]])

    assert main(probe_argv(fake_gcloud, manifest=str(manifest_path))) == 1


def test_a_first_release_manifest_is_refused_against_another_environment(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    set_cloud_state(monkeypatch)
    output = tmp_path / "readback.json"
    assert main(probe_argv(fake_gcloud, output=str(output))) == 0
    manifest = first_release_manifest(json.loads(output.read_text(encoding="utf-8")))
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        main(
            probe_argv(
                fake_gcloud, environment="staging", manifest=str(manifest_path)
            )
        )
        == 1
    )


def test_a_readback_of_a_different_target_does_not_admit_this_one(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path, capsys
) -> None:
    """The recorded readback must be of the target the deploy is about to touch."""

    set_cloud_state(monkeypatch)
    output = tmp_path / "readback.json"
    assert main(probe_argv(fake_gcloud, output=str(output))) == 0
    readback = json.loads(output.read_text(encoding="utf-8"))
    readback["project"] = "odayplus-elsewhere"
    manifest = first_release_manifest(readback)
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert main(probe_argv(fake_gcloud, manifest=str(manifest_path))) == 1
    assert "不一致" in capsys.readouterr().err


def test_the_two_modes_are_mutually_exclusive(
    fake_gcloud: Path, tmp_path: Path
) -> None:
    assert main(probe_argv(fake_gcloud)) == 2
    assert (
        main(
            probe_argv(
                fake_gcloud,
                output=str(tmp_path / "readback.json"),
                manifest=str(tmp_path / "RELEASE_MANIFEST.json"),
            )
        )
        == 2
    )


def test_the_script_runs_as_a_command(
    monkeypatch: pytest.MonkeyPatch, fake_gcloud: Path, tmp_path: Path
) -> None:
    """The workflow invokes it as `python3 <path>`, not as an import."""

    environment = dict(os.environ)
    environment.update(
        {"FAKE_PRESENT": "[]", "FAKE_UNREADABLE": "[]", "FAKE_AMBIGUOUS": "[]"}
    )
    output = tmp_path / "readback.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "delivery_toolchain/release/probe_release_target_absence.py"),
            *probe_argv(fake_gcloud, output=str(output)),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert output.exists()
