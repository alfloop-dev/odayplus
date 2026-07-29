"""ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001: Deploy Dev artifact path contract.

Deploy Dev run 30436771086 wrote three Cloud Run Job validation receipts —
`.odp_data/deployment/cloud-run-jobs/{migration,scheduler,worker}-validation.json`,
all passing — and published none of them. The upload step's
`.odp_data/deployment/*.json` glob is not recursive, so the artifact held only
the three top-level reports and the Job receipts died with the runner.

Replacing the glob with an explicit allowlist fixes that, but an allowlist is a
second place that has to stay true: the deploy script decides which receipts
exist, and nothing forces the workflow to keep up. These tests derive the
expected file set from `scripts/deploy_cloud_run_waji.sh` itself, so adding a
fourth Cloud Run Job kind — or renaming a report — fails here instead of
silently shipping another evidence gap.

The allowlist also carries a confidentiality obligation. The same directory
holds the raw `gcloud run jobs describe` / `executions describe` /
`executions list` dumps, which restate the deployed env block and secret
selectors verbatim. A recursive include would have published them, so the
exclusion is asserted as a property of the path list, not left to review.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DEV_WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
DEPLOY_SCRIPT = ROOT / "scripts/deploy_cloud_run_waji.sh"
VALIDATOR_PATH = ROOT / "scripts/deployment/validate_cloud_run_live_deployment.py"

DEPLOYMENT_REPORT_DIR = ".odp_data/deployment"
UPLOAD_ARTIFACT_NAME = "cloud-run-dev-validation"

# The raw gcloud dumps `capture_job_proof` / `capture_latest_execution` leave
# beside the receipts. Uploading any of them would publish the deployed
# environment and its secret selectors.
UNREDACTED_RECEIPT_SUFFIXES = ("-job.json", "-execution.json", "-execution-list.json")

_spec = importlib.util.spec_from_file_location("odp_deploy_contract_validator", VALIDATOR_PATH)
assert _spec and _spec.loader
validator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = validator
_spec.loader.exec_module(validator)


def _deploy_script_text() -> str:
    return DEPLOY_SCRIPT.read_text(encoding="utf-8")


def _job_report_dir() -> str:
    """`JOB_REPORT_DIR`'s default, read out of the deploy script."""

    match = re.search(
        r'^JOB_REPORT_DIR="\$\{JOB_REPORT_DIR:-([^"}]+)\}"$',
        _deploy_script_text(),
        flags=re.MULTILINE,
    )
    assert match, "deploy script no longer declares a default JOB_REPORT_DIR"
    return match.group(1)


def _captured_job_kinds() -> tuple[str, ...]:
    """Every Cloud Run Job kind the deploy script proves, in call order.

    `execute_job` ends in `capture_job_proof`, which writes
    `${JOB_REPORT_DIR}/${kind}-validation.json`. So the literal kinds passed at
    the call sites are exactly the receipts a green deploy produces.
    """

    kinds = tuple(
        dict.fromkeys(
            re.findall(r'^\s*execute_job "([a-z]+)"', _deploy_script_text(), flags=re.MULTILINE)
        )
    )
    assert kinds, "deploy script no longer calls execute_job with literal job kinds"
    return kinds


def _top_level_report_defaults() -> dict[str, str]:
    """`NAME="${NAME:-.odp_data/deployment/...}"` defaults, keyed by shell var."""

    return {
        name: path
        for name, path in re.findall(
            r'^([A-Z0-9_]+)="\$\{\1:-(' + re.escape(DEPLOYMENT_REPORT_DIR) + r'/[^"}/]+\.json)\}"$',
            _deploy_script_text(),
            flags=re.MULTILINE,
        )
    }


def _upload_step() -> dict[str, object]:
    workflow = yaml.safe_load(DEPLOY_DEV_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    uploads = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == UPLOAD_ARTIFACT_NAME
    ]
    assert len(uploads) == 1, f"expected exactly one {UPLOAD_ARTIFACT_NAME} upload, got {uploads}"
    return uploads[0]


def _allowlisted_paths() -> list[str]:
    raw = _upload_step()["with"]["path"]
    assert isinstance(raw, str), "the upload path must be a literal string, not a template"
    return [line.strip() for line in raw.splitlines() if line.strip()]


def test_deploy_dev_workflow_is_valid_yaml_and_still_uploads_on_failure() -> None:
    """A failed deploy is exactly when the receipts matter most."""

    workflow = yaml.safe_load(DEPLOY_DEV_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["name"] == "Deploy Dev"

    step = _upload_step()
    assert step["if"] == "always()"
    # Receipts for stages the run never reached are legitimately absent.
    assert step["with"]["if-no-files-found"] == "ignore"


def test_upload_allowlist_covers_every_job_receipt_the_deploy_script_writes() -> None:
    """The regression run 30436771086 exposed: nested receipts, dropped."""

    job_report_dir = _job_report_dir()
    allowlisted = _allowlisted_paths()

    for kind in _captured_job_kinds():
        receipt = f"{job_report_dir}/{kind}-validation.json"
        assert receipt in allowlisted, (
            f"{kind} Cloud Run Job receipt {receipt} is written by the deploy script "
            "but not uploaded by Deploy Dev"
        )

    # And the migration/scheduler/worker set is the one the incident named.
    assert set(_captured_job_kinds()) >= {"migration", "scheduler", "worker"}


def test_upload_allowlist_keeps_every_top_level_report_the_glob_covered() -> None:
    """Replacing `*.json` must not quietly narrow what was already published."""

    allowlisted = _allowlisted_paths()
    defaults = _top_level_report_defaults()

    # The reports the deploy script and the live E2E gate write at the top level.
    assert set(defaults) >= {
        "PREFLIGHT_REPORT",
        "SMOKE_REPORT",
        "MIGRATION_COMPAT_REPORT",
        "LIVE_E2E_REPORT",
    }
    for shell_var, path in defaults.items():
        assert path in allowlisted, f"{shell_var} ({path}) is no longer uploaded"


def test_upload_allowlist_excludes_raw_gcloud_dumps_and_wildcards() -> None:
    """No unredacted describe output, and no glob that could sweep one in."""

    allowlisted = _allowlisted_paths()
    assert allowlisted, "the upload step declares no paths"

    for path in allowlisted:
        assert not path.startswith("!"), f"{path}: exclusion patterns make the set non-obvious"
        # Literal files only. A glob is what shipped the original defect, and it
        # is also what would publish a future raw dump written into this tree.
        assert not set(path) & set("*?[]"), f"{path}: the allowlist must name literal files"
        assert path.startswith(f"{DEPLOYMENT_REPORT_DIR}/"), (
            f"{path}: uploads must stay inside {DEPLOYMENT_REPORT_DIR}"
        )
        assert path.endswith(".json"), f"{path}: only JSON validator reports may be uploaded"
        assert ".." not in Path(path).parts, f"{path}: must not escape the report directory"
        for suffix in UNREDACTED_RECEIPT_SUFFIXES:
            assert not path.endswith(suffix), (
                f"{path}: raw gcloud describe output restates the deployed env block "
                "and its secret selectors"
            )

    # Nothing outside the deployment report tree: no env files, no SBOM, no
    # scan output, no checkout paths.
    assert len(allowlisted) == len(set(allowlisted)), "duplicate entries in the upload allowlist"


def test_uploaded_job_receipt_names_the_job_but_never_a_bound_value() -> None:
    """The receipt is publishable because it is a check list, not a describe.

    Built from a job description that carries plaintext env values and secret
    selectors, the emitted report must reproduce neither. `secret_values_redacted`
    is the machine-readable claim a reader can check on the artifact itself.
    """

    sha = "d" * 40
    plaintext_marker = "plaintext-value-that-must-not-be-published"
    job_description = {
        "name": "projects/oday/locations/asia-east1/jobs/oday-worker-r-93ae1b2e75e1",
        "labels": {"oday-release-sha": sha},
        "template": {
            "template": {
                "containers": [
                    {
                        "image": f"registry/worker:dev-{sha}",
                        "command": ["python"],
                        "args": ["scripts/deployment/cloud_run_job_entrypoint.py", "worker"],
                        "env": [
                            {"name": "ODAY_RELEASE_SHA", "value": sha},
                            {
                                "name": "ODP_PRODUCTION_PROVIDER_IDS",
                                "value": "poi.commercial_api",
                            },
                            {"name": "ODP_INTERNAL_TUNING_KNOB", "value": plaintext_marker},
                            {
                                "name": "ODAY_DATABASE_URL",
                                "valueSource": {
                                    "secretKeyRef": {
                                        "secret": "oday-database-url",
                                        "version": "latest",
                                    }
                                },
                            },
                        ],
                    }
                ]
            }
        },
    }
    execution = {
        "metadata": {"name": "oday-worker-r-93ae1b2e75e1-6fhw5"},
        "status": {
            "succeededCount": 1,
            "failedCount": 0,
            "completionTime": "2026-07-29T09:01:00Z",
            "conditions": [{"type": "Completed", "state": "CONDITION_SUCCEEDED"}],
        },
    }

    _checks, report = validator.cloud_run_job_checks(
        kind="worker",
        job_description=job_description,
        execution=execution,
        expected_sha=sha,
    )

    assert report["secret_values_redacted"] is True
    assert report["job_kind"] == "worker"
    assert report["job_name"] == job_description["name"]
    assert report["execution_name"] == execution["metadata"]["name"]

    serialized = json.dumps(report)
    assert plaintext_marker not in serialized
    # Env-var names may be reported; the values behind them may not.
    assert "oday-database-url" not in serialized
    assert "ODAY_DATABASE_URL" in json.dumps(report.get("required_secret_env_vars", []))


@pytest.mark.parametrize(
    ("suffix", "written_by"),
    (
        ("-job.json", 'local description_file="${JOB_REPORT_DIR}/${kind}-job.json"'),
        ("-execution.json", 'local execution_file="${JOB_REPORT_DIR}/${kind}-execution.json"'),
        ("-execution-list.json", 'local list_file="${execution_file%.json}-list.json"'),
    ),
)
def test_the_excluded_dumps_are_the_files_the_deploy_script_actually_writes(
    suffix: str, written_by: str
) -> None:
    """Guard the exclusion list against drifting into a no-op.

    If `capture_job_proof` / `capture_latest_execution` stopped writing these,
    the exclusion above would still pass while proving nothing. Anchor each
    excluded suffix to the line that produces it — including the `-list.json`
    sibling, which is derived from the execution path rather than named
    outright and so is the easiest one to forget.
    """

    assert suffix in UNREDACTED_RECEIPT_SUFFIXES
    assert written_by in _deploy_script_text(), (
        f"deploy script no longer writes {suffix} dumps; revisit the upload exclusions"
    )
