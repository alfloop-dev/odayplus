"""Deploy Dev / Deploy Staging artifact path contract.

ODP-DEPLOY-JOB-RECEIPT-UPLOAD-001: Deploy Dev run 30436771086 wrote three Cloud
Run Job validation receipts —
`.odp_data/deployment/cloud-run-jobs/{migration,scheduler,worker}-validation.json`,
all passing — and published none of them. The upload step's
`.odp_data/deployment/*.json` glob is not recursive, so the artifact held only
the three top-level reports and the Job receipts died with the runner.

ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001: Deploy Staging carried the identical
glob. It has never lost a receipt only because it has never produced one — every
run to date fails closed at the WIF gate (run 30445252373: "Workload Identity
Federation variables are required", then "No files were found with the provided
path: .odp_data/deployment/*.json"). The first genuinely configured staging
deploy would have dropped its Job receipts exactly as dev did.

Replacing a glob with an explicit allowlist fixes that, but an allowlist is a
second place that has to stay true: the deploy script decides which receipts
exist, and nothing forces a workflow to keep up. These tests derive the expected
file set from `scripts/deploy_cloud_run_waji.sh` and from each workflow's own
steps, then run it against every deploy workflow in one parametrised sweep. So
adding a fourth Cloud Run Job kind, renaming a report, or landing a third
environment workflow fails here instead of silently shipping another evidence
gap — and dev and staging cannot drift apart from each other either.

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
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github/workflows"
DEPLOY_SCRIPT = ROOT / "scripts/deploy_cloud_run_waji.sh"
VALIDATOR_PATH = ROOT / "scripts/deployment/validate_cloud_run_live_deployment.py"

DEPLOYMENT_REPORT_DIR = ".odp_data/deployment"

# The raw gcloud dumps `capture_job_proof` / `capture_latest_execution` leave
# beside the receipts. Uploading any of them would publish the deployed
# environment and its secret selectors.
UNREDACTED_RECEIPT_SUFFIXES = ("-job.json", "-execution.json", "-execution-list.json")

# Deploy Staging names its remote proof after the run, so the upload path has to
# carry the same expansion the checker step used. Only run-scoped context is
# allowed in: an expression that can be steered by workflow input or event
# payload would put path selection back outside review.
ALLOWED_PATH_EXPRESSIONS = ("github.run_id",)

# `${GITHUB_RUN_ID}` in a shell `run:` block and `${{ github.run_id }}` in a
# `with:` value are the same number at runtime; compare them as one token.
_RUN_ID_FORMS = ("${{ github.run_id }}", "${GITHUB_RUN_ID}", "$GITHUB_RUN_ID")
_RUN_ID_TOKEN = "<run-id>"


@dataclass(frozen=True)
class DeployWorkflow:
    """One environment's deploy workflow and the shape its artifact must have."""

    label: str
    filename: str
    workflow_name: str
    job_id: str
    artifact_name: str
    # Report trees outside `.odp_data/deployment` this environment may publish.
    # Each entry still has to be justified file-by-file by a `--output` in the
    # workflow's own steps (see the workflow-written-reports test), so this only
    # widens *where* a justified report may live, never *what* may be uploaded.
    extra_report_roots: tuple[str, ...] = field(default=())

    @property
    def path(self) -> Path:
        return WORKFLOW_DIR / self.filename

    @property
    def allowed_roots(self) -> tuple[str, ...]:
        return (f"{DEPLOYMENT_REPORT_DIR}/", *self.extra_report_roots)

    def __str__(self) -> str:  # readable parametrised test ids
        return self.label


DEPLOY_WORKFLOWS = (
    DeployWorkflow(
        label="dev",
        filename="deploy-dev.yml",
        workflow_name="Deploy Dev",
        job_id="deploy",
        artifact_name="cloud-run-dev-validation",
    ),
    DeployWorkflow(
        label="staging",
        filename="deploy-staging.yml",
        workflow_name="Deploy/Verify Staging",
        job_id="deploy-staging",
        artifact_name="remote-staging-proof",
        extra_report_roots=(".odp_data/remote-staging-proof/",),
    ),
)

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


def _normalize_run_id(path: str) -> str:
    for form in _RUN_ID_FORMS:
        path = path.replace(form, _RUN_ID_TOKEN)
    return path


def _parsed(workflow: DeployWorkflow) -> dict:
    return yaml.safe_load(workflow.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_tenant_variables_pass_through_without_placeholder_defaults(
    workflow: DeployWorkflow,
) -> None:
    """Missing tenant configuration must reach the deploy script as empty.

    ``deploy_cloud_run_waji.sh`` already fails closed when both tenant variables
    are unset. Supplying ``tenant-dev`` / ``tenant-staging`` here bypasses that
    guard and can make the worker write into a partition the smoke principal
    cannot read. The environment owner must bind both repository variables to
    that principal's tenant claim; this contract keeps the workflow as a pure
    pass-through so deployment cannot silently invent a different tenant scope.
    """

    environment = _parsed(workflow)["jobs"][workflow.job_id]["env"]
    assert environment["ODP_SCHEDULED_INGESTION_TENANT_ID"] == (
        "${{ vars.ODP_SCHEDULED_INGESTION_TENANT_ID }}"
    )
    assert environment["ODP_TENANT_ID"] == "${{ vars.ODP_TENANT_ID }}"


def _steps(workflow: DeployWorkflow) -> list[dict]:
    return [
        step
        for step in _parsed(workflow)["jobs"][workflow.job_id]["steps"]
        if isinstance(step, dict)
    ]


def _upload_step(workflow: DeployWorkflow) -> dict[str, object]:
    uploads = [
        step
        for step in _steps(workflow)
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and step.get("with", {}).get("name") == workflow.artifact_name
    ]
    assert len(uploads) == 1, (
        f"{workflow.filename}: expected exactly one {workflow.artifact_name} upload, got {uploads}"
    )
    return uploads[0]


def _allowlisted_paths(workflow: DeployWorkflow) -> list[str]:
    raw = _upload_step(workflow)["with"]["path"]
    assert isinstance(raw, str), "the upload path must be a literal block, not a mapping"
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _workflow_written_reports(workflow: DeployWorkflow) -> list[str]:
    """Every `.odp_data` report the workflow's own `run:` steps emit.

    The deploy script's receipts are derived from the script; these are the
    reports a workflow writes directly (the fail-closed preflight in both, and
    staging's remote proof). Reading them back out of the step that produced
    them is what keeps the upload list honest without restating any path here.
    """

    written: list[str] = []
    for step in _steps(workflow):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        for match in re.findall(r'--output[= ]+"?(\.odp_data/[^"\s\\]+)"?', run):
            normalized = _normalize_run_id(match)
            if normalized not in written:
                written.append(normalized)
    return written


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_deploy_workflow_is_valid_yaml_and_still_uploads_on_failure(
    workflow: DeployWorkflow,
) -> None:
    """A failed deploy is exactly when the receipts matter most."""

    assert _parsed(workflow)["name"] == workflow.workflow_name

    step = _upload_step(workflow)
    assert step["if"] == "always()"
    # Receipts for stages the run never reached are legitimately absent.
    assert step["with"]["if-no-files-found"] == "ignore"


def test_every_deploy_workflow_runs_the_same_receipt_writing_script() -> None:
    """Why one derivation from the deploy script is valid for both environments.

    `deploy_cloud_run_waji.sh` reads `ODP_DEPLOY_ENV` for naming and gate
    expectations, not for which reports it emits, so dev and staging produce the
    same receipt set. If an environment ever forks to its own deploy script that
    stops being true, and the shared expectations below become a fiction.
    """

    invocation = "./scripts/deploy_cloud_run_waji.sh"
    for workflow in DEPLOY_WORKFLOWS:
        runs = [step.get("run", "") for step in _steps(workflow)]
        assert any(invocation in run for run in runs), (
            f"{workflow.filename} no longer runs {invocation}; its receipt set must be "
            "derived from whatever it runs instead"
        )


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_upload_allowlist_covers_every_job_receipt_the_deploy_script_writes(
    workflow: DeployWorkflow,
) -> None:
    """The regression run 30436771086 exposed: nested receipts, dropped."""

    job_report_dir = _job_report_dir()
    allowlisted = _allowlisted_paths(workflow)

    for kind in _captured_job_kinds():
        receipt = f"{job_report_dir}/{kind}-validation.json"
        assert receipt in allowlisted, (
            f"{kind} Cloud Run Job receipt {receipt} is written by the deploy script "
            f"but not uploaded by {workflow.workflow_name}"
        )

    # And the migration/scheduler/worker set is the one the incident named.
    assert set(_captured_job_kinds()) >= {"migration", "scheduler", "worker"}


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_upload_allowlist_keeps_every_top_level_report_the_glob_covered(
    workflow: DeployWorkflow,
) -> None:
    """Replacing `*.json` must not quietly narrow what was already published."""

    allowlisted = _allowlisted_paths(workflow)
    defaults = _top_level_report_defaults()

    # The reports the deploy script and the live E2E gate write at the top level.
    assert set(defaults) >= {
        "PREFLIGHT_REPORT",
        "SMOKE_REPORT",
        "MIGRATION_COMPAT_REPORT",
        "LIVE_E2E_REPORT",
    }
    for shell_var, path in defaults.items():
        assert path in allowlisted, (
            f"{workflow.workflow_name}: {shell_var} ({path}) is no longer uploaded"
        )


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_upload_allowlist_publishes_every_report_the_workflow_itself_writes(
    workflow: DeployWorkflow,
) -> None:
    """Reports the workflow emits outside the deploy script must survive too.

    Staging's remote proof is the case this exists for: it is the one report the
    old two-line glob published that a deployment-directory allowlist alone would
    have thrown away. Anchoring it to the `--output` of the step that writes it
    means renaming the proof breaks here rather than emptying the artifact.
    """

    normalized_allowlist = [_normalize_run_id(path) for path in _allowlisted_paths(workflow)]
    written = _workflow_written_reports(workflow)
    assert written, f"{workflow.filename}: no `--output .odp_data/...` step found to anchor to"

    for report in written:
        assert report in normalized_allowlist, (
            f"{workflow.workflow_name} writes {report} but does not upload it"
        )

    # And the widened roots are not a blanket allowance: anything the allowlist
    # publishes from outside the deployment report directory must be one of
    # these, produced by a step in this same workflow.
    outside = [
        path for path in normalized_allowlist if not path.startswith(f"{DEPLOYMENT_REPORT_DIR}/")
    ]
    assert set(outside) <= set(written), sorted(set(outside) - set(written))


@pytest.mark.parametrize("workflow", DEPLOY_WORKFLOWS, ids=str)
def test_upload_allowlist_excludes_raw_gcloud_dumps_and_wildcards(
    workflow: DeployWorkflow,
) -> None:
    """No unredacted describe output, and no glob that could sweep one in."""

    allowlisted = _allowlisted_paths(workflow)
    assert allowlisted, "the upload step declares no paths"

    for path in allowlisted:
        assert not path.startswith("!"), f"{path}: exclusion patterns make the set non-obvious"
        for expression in re.findall(r"\$\{\{([^}]*)\}\}", path):
            assert expression.strip() in ALLOWED_PATH_EXPRESSIONS, (
                f"{path}: {expression.strip()} can steer path selection outside review"
            )
        resolved = _normalize_run_id(path)
        # Literal files only. A glob is what shipped the original defect, and it
        # is also what would publish a future raw dump written into this tree.
        assert not set(resolved) & set("*?[]"), f"{path}: the allowlist must name literal files"
        assert resolved.startswith(workflow.allowed_roots), (
            f"{path}: {workflow.workflow_name} may only upload from {workflow.allowed_roots}"
        )
        assert resolved.endswith(".json"), f"{path}: only JSON validator reports may be uploaded"
        assert ".." not in Path(resolved).parts, f"{path}: must not escape the report directory"
        for suffix in UNREDACTED_RECEIPT_SUFFIXES:
            assert not resolved.endswith(suffix), (
                f"{path}: raw gcloud describe output restates the deployed env block "
                "and its secret selectors"
            )

    # Nothing outside the declared report trees: no env files, no SBOM, no
    # scan output, no checkout paths.
    assert len(allowlisted) == len(set(allowlisted)), "duplicate entries in the upload allowlist"


def test_no_workflow_globs_into_the_deployment_report_directory() -> None:
    """The defect is a pattern, not two files: catch the next copy of it.

    Both deploy workflows were written from the same template, which is how one
    non-recursive glob became two. This sweeps every workflow in the repository
    — including any environment added later — rather than only the two named in
    `DEPLOY_WORKFLOWS`, because a third copy would otherwise ship unreviewed.
    """

    offenders: list[str] = []
    for path in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_id, job in (parsed.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if not str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                    continue
                raw = str(step.get("with", {}).get("path", ""))
                for line in raw.splitlines():
                    entry = line.strip()
                    if not entry.startswith(DEPLOYMENT_REPORT_DIR):
                        continue
                    if set(entry) & set("*?[]"):
                        offenders.append(f"{path.name}:{job_id}: {entry}")

    assert not offenders, (
        "wildcard uploads from the deployment report directory publish whatever "
        f"lands there, including raw gcloud describe dumps: {offenders}"
    )


def test_uploaded_job_receipt_names_the_job_but_never_a_bound_value() -> None:
    """The receipt is publishable because it is a check list, not a describe.

    Built from a job description that carries plaintext env values and secret
    selectors, the emitted report must reproduce neither. `secret_values_redacted`
    is the machine-readable claim a reader can check on the artifact itself.
    Environment-independent: both workflows publish this same writer's output.
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
