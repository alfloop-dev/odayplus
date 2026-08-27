"""Runtime Release artifact and single-entrypoint contract.

The release workflow is intentionally the only deployment workflow. These tests
derive the expected receipt set from the deploy script and assert that the
workflow publishes only redacted validation reports, never raw Cloud Run
descriptions containing environment or secret selectors.
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
RELEASE_WORKFLOW = WORKFLOW_DIR / "deploy-dev.yml"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.check_release_environment import (  # noqa: E402
    REQUIRED_VARIABLES,
)

# Which GitHub environment each job binds to, and why it is that one.
#
# `staging` and `production` both carry `required_reviewers`. Binding the build
# phase to them would demand a human deploy approval before the build that
# produces the manifest that approval is granted against -- which is the
# circular dependency this workflow exists to remove, re-entered through the
# approval gate instead of the lease. The build therefore binds to a
# build-scoped twin: same variables, no deployment approval.
BUILD_ENVIRONMENT = "${{ inputs.environment }}-build"
DEPLOY_ENVIRONMENT = "${{ inputs.environment }}"

JOB_ENVIRONMENT_BINDINGS = {
    "build": (BUILD_ENVIRONMENT, "build"),
    "admission": (DEPLOY_ENVIRONMENT, "admission"),
    "deploy": (DEPLOY_ENVIRONMENT, "deploy"),
}

# The one job that must stay unbound: binding it would put a deploy approval in
# front of plain input validation, and a run that is going to be refused for a
# malformed SHA should not spend a reviewer's attention first.
UNBOUND_JOB = "release_phase"

BINDING_GATE_SCRIPT = "delivery_toolchain/release/check_release_environment.py"


def _release_jobs() -> dict:
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _job_steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if isinstance(step, dict)]


def _binding_gate_index(job: dict) -> int:
    """Index of the step that proves this job's environment variables resolved."""

    return next(
        index
        for index, step in enumerate(_job_steps(job))
        if BINDING_GATE_SCRIPT in str(step.get("run", ""))
    )
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
VALIDATOR_PATH = ROOT / "product_ops/deployment/validate_cloud_run_live_deployment.py"

DEPLOYMENT_REPORT_DIR = ".odp_data/deployment"

# The raw gcloud dumps `capture_job_proof` / `capture_latest_execution` leave
# beside the receipts. Uploading any of them would publish the deployed
# environment and its secret selectors.
UNREDACTED_RECEIPT_SUFFIXES = ("-job.json", "-execution.json", "-execution-list.json")

# Runtime Release names its remote proof after the run, so the upload path has
# to carry the same expansion the checker step used. Only run-scoped context is
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
        label="runtime",
        filename="deploy-dev.yml",
        workflow_name="Runtime Release",
        job_id="deploy",
        artifact_name="runtime-release-${{ inputs.environment }}-validation",
        extra_report_roots=(
            ".odp_data/remote-staging-proof/",
            ".odp_data/staging-lifecycle/",
        ),
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


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _named_step(job: dict, name: str) -> dict:
    return next(
        step
        for step in job["steps"]
        if isinstance(step, dict) and step.get("name") == name
    )


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
        for match in re.findall(r'(?:--output|--receipt)[= ]+"?(\.odp_data/[^"\s\\]+)"?', run):
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


def test_runtime_release_is_the_only_workflow_running_the_deploy_script() -> None:
    """Why one derivation from the deploy script is valid for both environments.

    `deploy_cloud_run_waji.sh` reads `ODP_DEPLOY_ENV` for naming and gate
    expectations, not for which reports it emits, so dev and staging produce the
    same receipt set. If an environment ever forks to its own deploy script that
    stops being true, and the shared expectations below become a fiction.
    """

    invocation = "./product_ops/deployment/deploy_cloud_run_waji.sh"
    for workflow in DEPLOY_WORKFLOWS:
        runs = [step.get("run", "") for step in _steps(workflow)]
        assert sum(invocation in run for run in runs) == 1


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
                        "args": ["product_ops/deployment/cloud_run_job_entrypoint.py", "worker"],
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


def test_all_checkout_steps_bind_to_release_sha_input() -> None:
    """Every checkout step in Runtime Release must explicitly specify ref: inputs.release_sha."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    checkout_count = 0
    for job_id, job in parsed.get("jobs", {}).items():
        for step in job.get("steps", []):
            if not isinstance(step, dict):
                continue
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                checkout_count += 1
                assert step.get("with", {}).get("ref") == "${{ inputs.release_sha }}", (
                    f"Job {job_id} checkout step does not bind ref to inputs.release_sha"
                )
    assert checkout_count >= 3, f"Expected at least 3 checkout steps, found {checkout_count}"


def test_jobs_assert_checked_out_head_matches_release_sha() -> None:
    """Every job must assert git rev-parse HEAD equals inputs.release_sha."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    for job_id, job in parsed.get("jobs", {}).items():
        steps = job.get("steps", [])
        runs = [
            step.get("run", "")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        ]
        assert any("git rev-parse HEAD" in run for run in runs), (
            f"Job {job_id} does not assert that git rev-parse HEAD equals the expected release SHA"
        )


def test_runtime_release_is_single_entrypoint() -> None:
    """deploy-staging.yml is removed; deploy-dev.yml is the sole deploy entrypoint."""
    assert not (WORKFLOW_DIR / "deploy-staging.yml").exists()
    assert (WORKFLOW_DIR / "deploy-dev.yml").exists()


def test_staging_proof_checker_gated_on_staging_environment() -> None:
    """The fail-closed remote staging proof checker runs in deploy job when environment is staging."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_steps = parsed["jobs"]["deploy"]["steps"]
    staging_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "check_remote_staging_proof.py" in str(step.get("run", ""))
    ]
    assert len(staging_steps) == 1
    step = staging_steps[0]
    assert step.get("if") == "${{ inputs.environment == 'staging' }}"
    assert "ODP_STAGING_DEPLOY_URL" in step.get("env", {})
    assert "ODP_STAGING_API_URL" in step.get("env", {})
    assert "ODP_STAGING_SECRET_OWNER" in step.get("env", {})


def test_staging_lifecycle_invocations_gated_on_staging_environment() -> None:
    """Runtime Release directly invokes staging lifecycle create and verify in the staging deploy branch."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_steps = parsed["jobs"]["deploy"]["steps"]

    # Static deploy script MUST NOT run when environment is staging
    static_deploy_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "deploy_cloud_run_waji.sh" in str(step.get("run", ""))
    ]
    assert len(static_deploy_steps) == 1
    assert static_deploy_steps[0].get("if") == "${{ inputs.environment != 'staging' }}"

    create_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "staging_lifecycle.py create" in str(step.get("run", ""))
    ]
    assert len(create_steps) == 1, "deploy job must contain exactly one staging_lifecycle.py create step"
    create_step = create_steps[0]
    assert create_step.get("if") == "${{ inputs.environment == 'staging' }}"
    create_run = str(create_step.get("run", ""))
    assert "--release-id" in create_run
    assert "--candidate-sha" in create_run
    assert "--manifest-digest" in create_run
    assert "--api-image" in create_run
    assert "--web-image" in create_run
    assert "--worker-image" in create_run
    assert "--scheduler-image" in create_run
    assert "--owner-task-id" in create_run
    assert "--receipt" in create_run
    assert ".odp_data/staging-lifecycle/staging-lifecycle-create.json" in create_run
    assert "--dry-run" not in create_run, "Staging create must execute live without --dry-run"

    verify_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "staging_lifecycle.py verify" in str(step.get("run", ""))
    ]
    assert len(verify_steps) == 1, "deploy job must contain exactly one staging_lifecycle.py verify step"
    verify_step = verify_steps[0]
    assert verify_step.get("if") == "${{ inputs.environment == 'staging' }}"
    verify_run = str(verify_step.get("run", ""))
    assert "--release-id" in verify_run
    assert "--candidate-sha" in verify_run
    assert "--manifest-digest" in verify_run
    assert "--worker-image" in verify_run
    assert "--scheduler-image" in verify_run
    assert "--operator-identity" in verify_run
    assert "--receipt" in verify_run
    assert ".odp_data/staging-lifecycle/staging-rehearsal-receipt.json" in verify_run
    assert "--dry-run" not in verify_run, "Staging verify must execute live without --dry-run"


def test_staging_hold_on_failure_invoked_on_error() -> None:
    """Staging deploy failure triggers staging_lifecycle.py hold to retain resources for debugging."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_steps = parsed["jobs"]["deploy"]["steps"]

    hold_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "staging_lifecycle.py hold" in str(step.get("run", ""))
    ]
    assert len(hold_steps) == 1, "deploy job must contain exactly one staging_lifecycle.py hold step"
    hold_step = hold_steps[0]
    assert hold_step.get("if") == "${{ failure() && inputs.environment == 'staging' }}"
    hold_run = str(hold_step.get("run", ""))
    assert "--release-id" in hold_run
    assert "--project-id" in hold_run
    assert "--owner-task-id" in hold_run
    assert "--reason" in hold_run
    assert "--receipt" in hold_run
    assert ".odp_data/staging-lifecycle/staging-lifecycle-hold.json" in hold_run


def test_staging_identity_rejects_dev_operator_impersonation() -> None:
    """Staging verification must enforce release-scoped identity and reject dev operator impersonation."""
    from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

    receipt = verify_ephemeral_staging(
        release_id="odp-test-001",
        candidate_sha="a" * 40,
        manifest_digest="sha256:" + "b" * 64,
        project_id="oday-staging-proj",
        operator_identity="dev-smoke-operator@oday-dev-proj.iam.gserviceaccount.com",
    )
    assert not receipt.success
    assert any("dev smoke operator identity impersonation" in err for err in receipt.errors)



def test_admission_job_checkout_has_unshallow_fetch_depth() -> None:
    """The admission job must checkout with fetch-depth: 0 so git merge-base --is-ancestor can check ancestry."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    admission_steps = parsed["jobs"]["admission"]["steps"]
    checkout_steps = [
        step
        for step in admission_steps
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkout_steps) == 1
    assert checkout_steps[0].get("with", {}).get("fetch-depth") == 0


def test_admission_uses_protected_wif_and_shared_gcs_lease_state() -> None:
    """Hosted admission must not fall back to runner-local credentials/state."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    admission = parsed["jobs"]["admission"]
    assert admission["environment"] == {"name": "${{ inputs.environment }}"}

    steps = admission["steps"]
    binding_index = _binding_gate_index(admission)
    validate_index = next(
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and step.get("name") == "Validate shared lease store configuration"
    )
    auth_index = next(
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict)
        and step.get("uses") == "google-github-actions/auth@v2"
        and "durable lease admission" in step.get("name", "")
    )
    admission_index = next(
        index
        for index, step in enumerate(steps)
        if isinstance(step, dict) and step.get("name") == "Validate supervisor release admission"
    )
    assert binding_index < validate_index < auth_index < admission_index

    validation = steps[validate_index]
    assert validation["env"]["RELEASE_LEASE_STATE_URI"] == "${{ vars.ODP_RELEASE_LEASE_STATE_URI }}"
    assert "requires a shared gs://bucket/prefix state URI" in validation["run"]

    admission_run = steps[admission_index]["run"]
    assert "RELEASE_LEASE_STATE_URI" in admission_run
    assert "ODP_RELEASE_LEASE_STATE_DIR" not in "\n".join(
        str(step) for step in steps if isinstance(step, dict)
    )


def test_environment_inputs_support_dev_staging_production() -> None:
    """The unified Runtime Release workflow must support dev, staging, and production."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    on_block = parsed.get("on") or parsed.get(True)
    env_input = on_block["workflow_dispatch"]["inputs"]["environment"]
    assert env_input["type"] == "choice"
    assert set(env_input["options"]) == {"dev", "staging", "production"}


def test_build_once_job_exists_and_precedes_deploy() -> None:
    """A first dispatch builds once; later dispatches reuse immutable refs."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    jobs = parsed["jobs"]
    assert "build" in jobs
    assert "deploy" in jobs
    assert "build" in _needs(jobs["deploy"])

    # Build job must perform secret scan, SAST scan, SBOM, and E2E operational proof
    build_steps = jobs["build"]["steps"]
    build_runs = [
        step.get("run", "")
        for step in build_steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    assert any("secret_scan.py" in run for run in build_runs)
    assert any("sast_scan.py" in run for run in build_runs)
    assert any("generate_sbom.py" in run for run in build_runs)
    assert any("verify_deployment_health_backup_rollback.py" in run for run in build_runs)

    build_if = jobs["build"].get("if", "")
    assert all(
        f"inputs.{name} == ''" in build_if
        for name in ("api_image", "web_image", "worker_image", "scheduler_image")
    )
    assert "gcloud artifacts docker images describe" in "\n".join(build_runs)
    assert "GITHUB_OUTPUT" in "\n".join(build_runs)
    assert set(jobs["build"]["outputs"]) == {
        "api_image",
        "web_image",
        "worker_image",
        "scheduler_image",
        # The manifest the Supervisor issues a lease against is a build output,
        # because nothing before the build can know it.
        "manifest_digest",
        "release_id",
    }

    deploy_if = jobs["deploy"].get("if", "")
    assert "needs.build.result == 'skipped'" in deploy_if


def test_deploy_job_configures_deploy_by_digest() -> None:
    """Deploy job must pass the build output or handoff input to the script."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_env = parsed["jobs"]["deploy"]["env"]
    assert deploy_env.get("ODP_DEPLOY_BY_DIGEST") == "true"
    for name in ("API_IMAGE", "WEB_IMAGE", "WORKER_IMAGE", "SCHEDULER_IMAGE"):
        lower = name.lower()
        expected = "${{ needs.build.outputs." + lower + " || inputs." + lower + " }}"
        assert deploy_env[name] == expected

    deploy_runs = [
        step.get("run", "")
        for step in parsed["jobs"]["deploy"]["steps"]
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]
    assert not any("docker build" in run for run in deploy_runs)


def test_image_handoff_inputs_are_all_or_none_immutable_refs() -> None:
    """The cross-environment handoff cannot mix tags, blanks, or components."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    inputs = parsed.get("on", parsed.get(True))["workflow_dispatch"]["inputs"]
    image_names = {"api_image", "web_image", "worker_image", "scheduler_image"}
    assert image_names <= set(inputs)
    for name in image_names:
        assert inputs[name]["required"] is False
        assert inputs[name]["default"] == ""

    validation_step = next(
        step
        for step in parsed["jobs"]["admission"]["steps"]
        if isinstance(step, dict) and step.get("name") == "Validate immutable image handoff"
    )
    validation = validation_step["run"]
    assert "must be supplied together" in validation
    assert "@sha256:[0-9a-f]{64}" in validation


def test_deploy_script_uses_digest_refs_for_every_cloud_run_target() -> None:
    """The deploy script's immutable path cannot silently turn refs into tags."""
    script = _deploy_script_text()
    assert "@sha256:[0-9a-f]{64}" in script
    assert 'ODP_DEPLOY_BY_DIGEST:-false' in script
    assert '--image="${API_IMAGE}"' in script
    assert '--image="${WEB_IMAGE}"' in script
    assert '--image="${WORKER_IMAGE}"' in script
    assert '--image="${SCHEDULER_IMAGE}"' in script
    assert "deploy-by-digest requires immutable image reference" in script


def test_deploy_script_applies_optional_vpc_network_args_to_every_cloud_run_target() -> None:
    """Every service/job must share the release entrypoint's VPC binding."""
    script = _deploy_script_text()
    network_args = '"${CLOUD_RUN_NETWORK_ARGS[@]}"'
    targets = (
        'gcloud run jobs deploy "${MIGRATION_CANDIDATE_JOB}"',
        'gcloud run deploy "${API_SERVICE}"',
        'gcloud run jobs deploy "${SCHEDULER_CANDIDATE_JOB}"',
        'gcloud run jobs deploy "${WORKER_CANDIDATE_JOB}"',
        'gcloud run deploy "${WEB_SERVICE}"',
    )

    assert script.count(network_args) == len(targets)
    for target in targets:
        start = script.index(target)
        end = script.index("\n\n", start)
        assert network_args in script[start:end], target


def test_deploy_script_rejects_partial_or_invalid_vpc_config_before_cloud_run() -> None:
    """Connector mistakes must fail before any Cloud Run mutation is possible."""
    script = _deploy_script_text()
    first_cloud_run_call = script.index("gcloud run ")
    guard_end = script.index("CLOUD_RUN_NETWORK_ARGS=()")

    assert "ODP_CLOUD_RUN_VPC_EGRESS is required with ODP_CLOUD_RUN_VPC_CONNECTOR" in script
    assert "ODP_CLOUD_RUN_VPC_CONNECTOR is required with ODP_CLOUD_RUN_VPC_EGRESS" in script
    assert "all|all-traffic|private-ranges-only" in script
    assert guard_end < first_cloud_run_call


def test_deploy_job_passes_optional_vpc_config_through_environment() -> None:
    """The single Runtime Release entrypoint receives environment-scoped vars."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_env = parsed["jobs"]["deploy"]["env"]

    assert deploy_env["ODP_CLOUD_RUN_VPC_CONNECTOR"] == "${{ vars.ODP_CLOUD_RUN_VPC_CONNECTOR }}"
    assert deploy_env["ODP_CLOUD_RUN_VPC_EGRESS"] == "${{ vars.ODP_CLOUD_RUN_VPC_EGRESS }}"


def test_production_bluegreen_verification_gated_on_production_environment() -> None:
    """Production blue-green verification runs in deploy job when environment is production."""
    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    deploy_steps = parsed["jobs"]["deploy"]["steps"]
    prod_steps = [
        step
        for step in deploy_steps
        if isinstance(step, dict) and "bluegreen_release.py" in str(step.get("run", ""))
    ]
    assert len(prod_steps) == 1
    step = prod_steps[0]
    assert step.get("if") == "${{ inputs.environment == 'production' }}"


# --------------------------------------------------------------------------
# ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001: build-once and admission ordering
#
# `admission` used to be `build`'s parent. Because the lease is bound to a
# `manifest_digest`, and the manifest names image digests, SBOM refs, and Cosign
# signature refs that only a build produces, that ordering made the release
# unable to build without a lease and unable to earn a lease without building.
# These tests hold the ordering that removes the cycle, and the bindings that
# keep the split from turning "build once" into "deploy anything".
# --------------------------------------------------------------------------


def test_the_lease_is_verified_after_the_build_not_before() -> None:
    """Admission must never be a precondition of producing its own evidence."""

    jobs = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))["jobs"]

    assert "admission" not in _needs(jobs["build"]), (
        "the build phase must not depend on lease admission; requiring a lease to "
        "produce the manifest the lease is bound to is the circular dependency"
    )
    assert "build" in _needs(jobs["admission"]), (
        "admission must be ordered after the build phase that publishes the "
        "manifest it verifies"
    )
    assert "admission" in _needs(jobs["deploy"])

    # Admission is deploy authority, so it runs in the deploy phase only.
    admission_if = jobs["admission"].get("if", "")
    assert "inputs.phase == 'deploy'" in admission_if
    assert "needs.build.result == 'skipped'" in admission_if

    # And the build phase runs only when asked to build.
    assert "inputs.phase == 'build'" in jobs["build"].get("if", "")


def test_every_job_is_gated_on_the_fail_closed_phase_check() -> None:
    """One precheck, ahead of every build, credential, and deployment step."""

    jobs = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))["jobs"]

    assert "release_phase" in jobs
    assert _needs(jobs["release_phase"]) == [], "the phase check must run first"
    for job_id in ("build", "admission", "deploy"):
        assert "release_phase" in _needs(jobs[job_id]), f"{job_id} bypasses the phase check"

    check = _named_step(jobs["release_phase"], "Validate phase and artifact handoff preconditions")
    run = check["run"]
    assert "delivery_toolchain/release/check_release_phase.py" in run
    # Lease presence is an input to the refusal, not an assumption. OIDC is
    # not: this job is unbound, so it cannot observe `vars.*` and anything it
    # concluded from them would describe the binding, not the configuration.
    # `test_the_binding_gate_runs_before_the_job_touches_google_cloud` holds
    # that half, inside the jobs that are bound.
    assert "--oidc-configured" not in run
    assert "--lease-supplied" in run
    assert "--receipt" in run
    # The lease reaches the precheck as a presence bit. Everything after the
    # script name is argv, and a signed lease in argv is readable from the
    # process table, so the document itself must not appear there.
    invocation = run.split("check_release_phase.py", 1)[1]
    assert "RELEASE_LEASE" not in invocation
    assert "--lease-file" not in invocation


def test_the_lease_input_is_optional_so_the_build_phase_can_run_without_one() -> None:
    """A required lease input would re-impose the cycle at the form level."""

    parsed = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))
    inputs = parsed.get("on", parsed.get(True))["workflow_dispatch"]["inputs"]

    assert inputs["release_lease"]["required"] is False
    assert inputs["release_lease"]["default"] == ""

    phase = inputs["phase"]
    assert phase["type"] == "choice"
    assert set(phase["options"]) == {"build", "deploy"}


def test_admission_binds_the_handoff_images_to_the_manifest() -> None:
    """A lease admits this release's artifacts, not any digest presented."""

    jobs = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))["jobs"]
    step = _named_step(jobs["admission"], "Validate supervisor release admission")
    run = step["run"]

    assert "--action deploy" in run, "the lease must authorise the deploy action explicitly"
    for component in ("api", "web", "worker", "scheduler"):
        assert f'--component-image "{component}=${{{component.upper()}_IMAGE_INPUT}}"' in run, (
            f"{component}'s handoff image is not bound back to manifest.components"
        )


def test_the_build_phase_publishes_the_artifact_handoff_it_hands_forward() -> None:
    """The handoff is an artifact of the build, not a promise about it."""

    jobs = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))["jobs"]
    steps = jobs["build"]["steps"]

    handoff = _named_step(jobs["build"], "Write the build-once artifact handoff")
    run = handoff["run"]
    assert "delivery_toolchain/release/build_release_handoff.py" in run
    for component in ("api", "web", "worker", "scheduler"):
        assert f'--component "{component}=' in run
    assert "--sbom-ref" in run
    assert "--signature-ref" in run
    assert "--manifest-output" in run
    assert "--images-output" in run

    # Both halves of the handoff leave the run, or a later deploy phase has
    # nothing to be dispatched with.
    uploaded = [
        str(step.get("with", {}).get("path", ""))
        for step in steps
        if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert any("runtime-release-images.json" in path for path in uploaded)
    assert any("RELEASE_MANIFEST.json" in path for path in uploaded)


def test_the_build_phase_signs_and_attests_every_published_image() -> None:
    """`sbom_refs` / `signature_refs` must name artifacts that were published.

    ODP-RELEASE-MANIFEST-COSIGN-001 and ODP-RELEASE-MANIFEST-SBOM-001 were
    raised because the manifest claimed supply-chain evidence that had never
    been pushed anywhere. The build resolves both back through the registry, so
    an unresolvable reference fails the build instead of reaching the manifest.
    """

    jobs = yaml.safe_load((WORKFLOW_DIR / "deploy-dev.yml").read_text(encoding="utf-8"))["jobs"]
    run = _named_step(
        jobs["build"], "Build, publish, sign, and attest immutable container images"
    )["run"]

    assert "cosign sign --yes" in run
    assert "cosign attest --yes --type cyclonedx" in run
    assert "resolve_supply_chain_ref" in run
    assert 'resolve_supply_chain_ref "${digest_ref}" sig' in run
    assert 'resolve_supply_chain_ref "${digest_ref}" att' in run
    assert "no Cosign signature artifact resolves" in run
    assert "no SBOM attestation artifact resolves" in run


# --------------------------------------------------------------------------
# GitHub environment binding
#
# This repository has zero repository-level Actions variables: `GCP_PROJECT_ID`,
# `GCP_AR_REPO` and the WIF pair exist only under the `dev` / `staging` /
# `production` environments. GitHub injects those into `vars.*` only for a job
# that carries an `environment:` binding, and an unbound job does not fail --
# `vars.X` expands to the empty string. So a missing binding is invisible in the
# YAML and produces a job that authenticates with nothing and publishes to
# `-docker.pkg.dev//`.
# --------------------------------------------------------------------------


def test_every_job_that_reads_environment_variables_binds_an_environment() -> None:
    unbound = []
    for job_id, job in _release_jobs().items():
        if "vars." not in yaml.safe_dump(job, allow_unicode=True):
            continue
        if not job.get("environment"):
            unbound.append(job_id)
    assert unbound == [], (
        f"{unbound} read `vars.*` with no `environment:` binding; those expand to "
        "the empty string rather than failing"
    )


def test_the_input_shape_gate_stays_unbound_and_reads_no_variables() -> None:
    """Input validation must be reachable without spending a deploy approval."""

    job = _release_jobs()[UNBOUND_JOB]
    assert "environment" not in job
    assert "vars." not in yaml.safe_dump(job, allow_unicode=True)


@pytest.mark.parametrize(
    ("job_id", "expected_environment"),
    [(job_id, binding) for job_id, (binding, _) in JOB_ENVIRONMENT_BINDINGS.items()],
)
def test_each_phase_binds_to_its_own_authority_environment(
    job_id: str, expected_environment: str
) -> None:
    job = _release_jobs()[job_id]
    assert job["environment"]["name"] == expected_environment


def test_the_build_phase_does_not_bind_to_the_deploy_environment() -> None:
    """Otherwise a staging/production build waits on a `required_reviewers` gate."""

    jobs = _release_jobs()
    assert jobs["build"]["environment"]["name"] != jobs["deploy"]["environment"]["name"]
    assert jobs["build"]["environment"]["name"].endswith("-build")


@pytest.mark.parametrize(
    ("job_id", "scope"),
    [(job_id, scope) for job_id, (_, scope) in JOB_ENVIRONMENT_BINDINGS.items()],
)
def test_the_binding_gate_exposes_exactly_the_variables_its_scope_requires(
    job_id: str, scope: str
) -> None:
    """The gate reads `os.environ`, so a variable the step forgets reads as missing.

    Tying the step's `env:` block to the module's own required set is what keeps
    a newly required variable from silently passing the gate it was added to.
    """

    job = _release_jobs()[job_id]
    step = _job_steps(job)[_binding_gate_index(job)]
    assert set(step["env"]) == set(REQUIRED_VARIABLES[scope])
    for name in REQUIRED_VARIABLES[scope]:
        assert step["env"][name] == "${{ vars." + name + " }}"


@pytest.mark.parametrize(
    ("job_id", "scope"),
    [(job_id, scope) for job_id, (_, scope) in JOB_ENVIRONMENT_BINDINGS.items()],
)
def test_the_binding_gate_declares_the_environment_the_job_binds_to(
    job_id: str, scope: str
) -> None:
    job = _release_jobs()[job_id]
    step = _job_steps(job)[_binding_gate_index(job)]
    run = step["run"]
    assert f"--scope {scope}" in run
    binding = JOB_ENVIRONMENT_BINDINGS[job_id][0]
    assert f'--github-environment "{binding}"' in run
    assert "--receipt" in run, "a refusal with no receipt is not evidence"


@pytest.mark.parametrize("job_id", sorted(JOB_ENVIRONMENT_BINDINGS))
def test_the_binding_gate_runs_before_the_job_touches_google_cloud(
    job_id: str,
) -> None:
    job = _release_jobs()[job_id]
    steps = _job_steps(job)
    gate_index = _binding_gate_index(job)
    cloud_indexes = [
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("google-github-actions/")
    ]
    assert cloud_indexes, f"{job_id} was expected to authenticate to Google Cloud"
    assert gate_index < min(cloud_indexes)


def test_no_step_is_skipped_on_a_variable_derived_condition() -> None:
    """A `vars.`-derived `if:` turns a missing binding into a silent success.

    The old shape guarded the auth and gcloud steps on `env.HAS_WIF == 'true'`,
    which was itself derived from `vars.*`. In an unbound job that condition is
    always false, so the build would skip authentication and carry on rather
    than refuse.
    """

    offenders = []
    for job_id, job in _release_jobs().items():
        for step in _job_steps(job):
            condition = str(step.get("if", ""))
            if "HAS_WIF" in condition or "vars." in condition:
                offenders.append(f"{job_id}:{step.get('name', step.get('uses'))}")
    assert offenders == []


def test_the_build_phase_publishes_its_binding_receipt() -> None:
    """The refusal has to leave the runner, or it is not auditable evidence."""

    job = _release_jobs()["build"]
    upload = next(
        step
        for step in _job_steps(job)
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
        and "environment" in str(step.get("with", {}).get("name", ""))
    )
    assert upload["if"] == "always()", "a receipt only kept on success proves nothing"
    assert "release-environment-receipt" in upload["with"]["name"]
