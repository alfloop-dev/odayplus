"""Workflow expressions may only name a context GitHub offers at that key.

ODP-RELEASE-WORKFLOW-DISPATCH-PARSER-001. `Runtime Release` stopped being
dispatchable because `jobs.build.env` carried `${{ runner.temp }}`. GitHub does
not offer the `runner` context to `jobs.<job_id>.env`; the file failed to
compile, and a workflow that does not compile has no `workflow_dispatch` to run.

Nothing about that is visible in review. The same expression is valid one level
down in `jobs.<job_id>.steps.*`, so it reads as correct and copies cleanly into
the wrong place. And GitHub's only signal is indirect: it creates a `failure`
run with zero jobs and no logs, on the `push` that carried the broken file --
even for a workflow whose `on:` block has no `push` trigger at all. Nothing in
this repository was watching for a run that should not exist.

So the rule is checked here instead, against every workflow, from the published
context-availability table rather than from the one key that broke.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / ".github/workflows"
RELEASE_WORKFLOW = WORKFLOW_DIR / "deploy-dev.yml"
DOCKERIGNORE = ROOT / ".dockerignore"

# Every context name GitHub defines. A bare word in an expression that is not
# one of these is a function, an operator, or a literal, and is not our concern.
KNOWN_CONTEXTS = frozenset(
    {
        "github",
        "env",
        "vars",
        "job",
        "jobs",
        "steps",
        "runner",
        "secrets",
        "strategy",
        "matrix",
        "needs",
        "inputs",
    }
)

# https://docs.github.com/actions/learn-github-actions/contexts#context-availability
#
# Keyed by the workflow key an expression sits under, with job ids and step
# indices generalised away. The distinction that matters here is the boundary
# between a job key and a step key: `runner` (and `job`, `env`, `steps`) appear
# only in the step row, because a job key is evaluated before the job has a
# runner to describe.
CONTEXT_AVAILABILITY: dict[str, frozenset[str]] = {
    "env": frozenset({"github", "secrets", "vars", "inputs"}),
    "concurrency": frozenset({"github", "inputs", "vars"}),
    "jobs.<job_id>.concurrency": frozenset(
        {"github", "needs", "strategy", "matrix", "vars", "inputs"}
    ),
    "jobs.<job_id>.env": frozenset(
        {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
    ),
    "jobs.<job_id>.environment": frozenset(
        {"github", "needs", "strategy", "matrix", "vars", "inputs"}
    ),
    "jobs.<job_id>.environment.url": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "inputs"}
    ),
    "jobs.<job_id>.if": frozenset({"github", "needs", "vars", "inputs"}),
    "jobs.<job_id>.name": frozenset(
        {"github", "needs", "strategy", "matrix", "vars", "inputs"}
    ),
    "jobs.<job_id>.outputs": frozenset(
        {
            "github",
            "needs",
            "strategy",
            "matrix",
            "job",
            "runner",
            "env",
            "secrets",
            "steps",
            "vars",
            "inputs",
        }
    ),
    "jobs.<job_id>.runs-on": frozenset(
        {"github", "needs", "strategy", "matrix", "vars", "inputs"}
    ),
    "jobs.<job_id>.steps": frozenset(
        {
            "github",
            "needs",
            "strategy",
            "matrix",
            "job",
            "runner",
            "env",
            "vars",
            "secrets",
            "steps",
            "inputs",
        }
    ),
}

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
# Single-quoted literals are the one place a context name can appear without
# being one; blank them before looking for identifiers.
_LITERAL = re.compile(r"'(?:[^']|'')*'")
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_.'\"])([A-Za-z_][A-Za-z0-9_]*)\s*(\()?")


def _referenced_contexts(value: str) -> set[str]:
    """The context roots an expression string names, ignoring calls and literals."""

    contexts: set[str] = set()
    for body in _EXPRESSION.findall(value):
        for name, call in _IDENTIFIER.findall(_LITERAL.sub("''", body)):
            if call:  # a function, not a context
                continue
            if name in KNOWN_CONTEXTS:
                contexts.add(name)
    return contexts


def _availability_key(trail: list[str]) -> str:
    """Collapse a YAML path to the key the availability table is written in."""

    if trail[:1] == ["jobs"] and len(trail) >= 3:
        key = trail[2]
        if key == "steps":
            return "jobs.<job_id>.steps"
        if key == "environment":
            # `url` is evaluated after the job has run; `name` is not.
            return "jobs.<job_id>.environment.url" if trail[3:4] == ["url"] else (
                "jobs.<job_id>.environment"
            )
        return f"jobs.<job_id>.{key}"
    return ".".join(trail[:1])


def _expression_sites(document: object) -> list[tuple[str, str, str]]:
    """`(availability key, full yaml path, value)` for every expression found."""

    sites: list[tuple[str, str, str]] = []

    def walk(node: object, trail: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, [*trail, str(key)])
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, [*trail, f"[{index}]"])
        elif isinstance(node, str) and "${{" in node:
            sites.append((_availability_key(trail), ".".join(trail), node))

    walk(document, [])
    return sites


def _workflows() -> list[Path]:
    found = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    assert found, "no workflows found to check"
    return found


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda p: p.name)
def test_no_expression_names_a_context_github_withholds_at_that_key(workflow: Path) -> None:
    """The defect, stated generally: right expression, wrong key."""

    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    unknown_keys: list[str] = []
    offenders: list[str] = []

    for availability_key, path, value in _expression_sites(document):
        allowed = CONTEXT_AVAILABILITY.get(availability_key)
        if allowed is None:
            unknown_keys.append(f"{workflow.name}:{path} (key shape {availability_key!r})")
            continue
        for context in sorted(_referenced_contexts(value) - allowed):
            offenders.append(
                f"{workflow.name}:{path} uses `{context}.*`, which GitHub does not "
                f"provide to {availability_key} (allowed: {', '.join(sorted(allowed))})"
            )

    assert not unknown_keys, (
        "expression found under a key this table does not cover; add the row from "
        f"GitHub's context-availability table rather than skipping it: {unknown_keys}"
    )
    assert not offenders, (
        "GitHub refuses to compile a workflow that names an unavailable context, and "
        "an uncompilable workflow cannot be dispatched at all:\n" + "\n".join(offenders)
    )


def test_the_runner_context_is_valid_in_a_step_and_not_in_the_job_above_it() -> None:
    """Guard the table itself: this pair is the whole defect."""

    assert "runner" in CONTEXT_AVAILABILITY["jobs.<job_id>.steps"]
    assert "runner" not in CONTEXT_AVAILABILITY["jobs.<job_id>.env"]


def _release_document() -> dict:
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))


def _release_jobs() -> dict:
    return _release_document()["jobs"]


def _release_steps() -> list[tuple[str, dict]]:
    return [
        (job_id, step)
        for job_id, job in _release_jobs().items()
        for step in job.get("steps", [])
        if isinstance(step, dict)
    ]


def _receipt_dir() -> str:
    """The single declared receipt staging root, read out of the workflow."""

    declared = _release_document()["env"]["RELEASE_RECEIPT_DIR"]
    assert "${{" not in declared, "the staging root must be a literal, not an expression"
    assert not declared.startswith("/"), "the staging root must be relative to the checkout"
    return declared


def test_runtime_release_stages_every_receipt_under_one_declared_relative_root() -> None:
    """One literal root, named once, used by both the writer and the upload.

    Splitting these -- a `--receipt` argument in one dialect and an upload path
    in another -- is how the two drifted into needing an expression to agree.
    """

    receipt_dir = _receipt_dir()

    written = [
        argument
        for _job_id, step in _release_steps()
        for argument in re.findall(r'--receipt\s+"([^"]+)"', str(step.get("run", "")))
    ]
    assert written, "no receipt writer found in the release workflow"
    for argument in written:
        assert argument.startswith("${RELEASE_RECEIPT_DIR}/"), (
            f"{argument} does not stage under the declared receipt root"
        )

    uploaded = [
        line.strip()
        for _job_id, step in _release_steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and "receipt" in str(step.get("with", {}).get("name", ""))
        for line in str(step["with"]["path"]).splitlines()
        if line.strip()
    ]
    # Three environment-binding receipts, one phase receipt, one admission receipt.
    assert len(uploaded) == len(written) == 5, (uploaded, written)
    for path in uploaded:
        assert path.startswith(f"{receipt_dir}/"), f"{path} is uploaded from outside {receipt_dir}"
        assert "${{" not in path, f"{path}: a receipt path needs no expression"

    assert {Path(p).name for p in uploaded} == {
        argument.split("/")[-1] for argument in written
    }, "the uploaded receipts are not the ones the workflow writes"


def test_the_signed_lease_never_lands_inside_the_checkout() -> None:
    """The lease is a credential, so it stays where no upload glob can reach it.

    `${RUNNER_TEMP}` here is a shell variable expanded by bash inside `run:`,
    not a workflow expression, so it was never part of the parser defect and
    must not be swept up by the move to relative staging.
    """

    receipt_dir = _receipt_dir()
    lease_steps = [
        step
        for _job_id, step in _release_steps()
        if "release-lease.json" in str(step.get("run", ""))
    ]
    assert lease_steps, "the admission job no longer materialises a lease document"
    for step in lease_steps:
        for path in re.findall(r'"([^"]*release-lease\.json)"', step["run"]):
            assert path.startswith("${RUNNER_TEMP}/"), (
                f"{path}: the lease document must stay in the runner temp dir"
            )
            assert not path.startswith(receipt_dir), f"{path}: a credential is not a receipt"

    uploaded = " ".join(
        str(step.get("with", {}).get("path", ""))
        for _job_id, step in _release_steps()
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert "release-lease" not in uploaded


def test_the_receipt_staging_root_is_excluded_from_every_image_build_context() -> None:
    """Relative staging must not make the build-once handoff irreproducible.

    The build job writes its environment-binding receipt -- which carries a
    `checked_at` timestamp -- before `docker build .`, and the API, web, worker,
    and scheduler images all `COPY . .`. Left in the context, that receipt would
    give every re-run of the same release SHA a different image digest, and with
    it a different `manifest_digest` than the Supervisor lease is bound to.
    """

    receipt_dir = _receipt_dir()
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    roots = {receipt_dir, receipt_dir.rstrip("/"), receipt_dir.split("/", 1)[0]}
    assert roots & ignored, (
        f"{receipt_dir} is staged inside the checkout but no ancestor of it is in "
        f".dockerignore; the build context would carry it into every image"
    )

    build_steps = _release_jobs()["build"]["steps"]
    docker_build = next(
        index
        for index, step in enumerate(build_steps)
        if isinstance(step, dict) and "docker build" in str(step.get("run", ""))
    )
    receipt_write = next(
        index
        for index, step in enumerate(build_steps)
        if isinstance(step, dict) and "--receipt" in str(step.get("run", ""))
    )
    assert receipt_write < docker_build, (
        "this test exists because the receipt is written first; if that changed, "
        "re-derive the reproducibility argument rather than deleting the exclusion"
    )
