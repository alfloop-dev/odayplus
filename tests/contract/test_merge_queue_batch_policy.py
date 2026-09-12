"""Contract tests for merge queue batch policy and CI/CD workflow contracts.

Implements the 6 automated verification matrix scenarios specified in
WP-35B / implementation-handoff.md §3.2 (ODP-MERGE-QUEUE-BATCH-DESIGN-001).

What these tests do and do not prove
------------------------------------
Scenarios 1, 2 and 5 assert the checked-in queue configuration and the payload
the apply script would send. Scenarios 3, 4 and 6 assert workflow behaviour,
and 3/4 do so by *executing the real shell body* of
``merge-queue-review-gate.yml`` against a stubbed ``gh``. Asserting on the
workflow text alone cannot see control flow: a ``fail()`` whose ``exit 1`` was
deleted, or a gate that consulted a stale head, still contains every string a
grep would look for. Running the script makes those changes fail here.

Nothing in this file observes GitHub's live merge queue. Batch formation,
accumulation-timer start points, and live candidate ejection are WP-35C live
acceptance items and are not claimed by any test below.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from delivery_toolchain.github.apply_branch_protection import (
    branch_policy,
    build_payload,
    build_ruleset_payload,
    merge_queue_config,
)
from delivery_toolchain.governance.classify_change_review_scope import (
    classify_paths,
    load_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / ".github/branch-protection/policy.json"
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"
REVIEW_GATE_WORKFLOW_PATH = ROOT / ".github/workflows/merge-queue-review-gate.yml"
RUNBOOK_PATH = ROOT / "docs/runbooks/dev-merge-queue.md"

# The three required contexts that are GitHub Actions jobs. `task-review-gate`
# is the fourth required context but is a commit *status* posted by the review
# gate workflow, not an Actions check, so it is asserted differently.
ACTIONS_CONTEXTS = ("orchestrator", "product", "product-e2e-gate")
REQUIRED_CONTEXTS = ("orchestrator", "product", "product-e2e-gate", "task-review-gate")

GROUP_HEAD_EXPR = "${{ github.event.merge_group.head_sha }}"
GROUP_REF_EXPR = "${{ github.event.merge_group.head_ref }}"
TOOLING_SKIP_IF = "${{ needs.change-scope.outputs.scope != 'development_tooling' }}"

# Distinct, obviously-fake SHAs so an assertion failure names which one leaked.
GROUP_SHA = "b" * 40
PR_HEAD_SHA = "c" * 40
OLD_HEAD_SHA = "d" * 40
NEW_HEAD_SHA = "e" * 40
QUEUE_REF = f"gh-readonly-queue/dev/pr-1284-{'f' * 40}"


def _load_policy() -> dict[str, Any]:
    assert POLICY_PATH.exists(), f"Policy file not found: {POLICY_PATH}"
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict[str, Any]:
    assert path.exists(), f"YAML workflow file not found: {path}"
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"Expected dict from {path}, got {type(parsed)}"
    return parsed


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # PyYAML resolves an unquoted `on:` key to the boolean True.
    triggers = workflow.get(True, workflow.get("on"))
    assert isinstance(triggers, dict), "workflow has no parsable trigger block"
    return triggers


def _review_gate_step() -> dict[str, Any]:
    """The single shell step the review gate runs, taken from the workflow."""
    job = _load_yaml(REVIEW_GATE_WORKFLOW_PATH)["jobs"]["review-gate"]
    run_steps = [step for step in job["steps"] if "run" in step]
    assert len(run_steps) == 1, (
        "expected exactly one shell step in the review-gate job; the contract "
        f"harness executes it verbatim, found {len(run_steps)}"
    )
    return run_steps[0]


# ==============================================================================
# Offline `gh` stub: lets the workflow's real shell run without network access.
# ==============================================================================

GH_STUB = r'''#!/usr/bin/env python3
"""Offline stand-in for `gh`, driven by a JSON fixture.

Serves only the three calls merge-queue-review-gate.yml makes, applies the
workflow's real `--jq` filter with jq, and appends every call to a log so a
test can assert which statuses were posted -- and, more importantly, which
were not.
"""
import json
import os
import subprocess
import sys

fixture = json.load(open(os.environ["GH_STUB_FIXTURE"], encoding="utf-8"))
log_path = os.environ["GH_STUB_CALLS"]
argv = sys.argv[1:]


def record(entry):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def jq(filter_expr, payload):
    """Apply the caller's real --jq filter, so a broken filter is visible."""
    proc = subprocess.run(
        ["jq", "-r", filter_expr],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write("gh stub: jq failed: " + proc.stderr)
        sys.exit(4)
    return proc.stdout.strip()


def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None


if argv[:2] == ["pr", "view"]:
    number = argv[2]
    record({"call": "pr_view", "pr": number})
    head = fixture["pr_heads"].get(number)
    if head is None:
        sys.stderr.write("gh stub: no such PR: %s\n" % number)
        sys.exit(1)
    print(jq(flag("--jq"), {"headRefOid": head}))
    sys.exit(0)

if argv[:1] == ["api"] and flag("-X") == "POST":
    record({
        "call": "post_status",
        "endpoint": argv[argv.index("-X") + 2],
        "fields": dict(
            argv[index + 1].split("=", 1)
            for index, item in enumerate(argv)
            if item == "-F"
        ),
    })
    print("{}")
    sys.exit(0)

if argv[:1] == ["api"]:
    endpoint = argv[1]
    record({"call": "get", "endpoint": endpoint})
    sha = endpoint.split("/commits/", 1)[1].split("/status", 1)[0]
    print(jq(flag("--jq"), {"statuses": fixture["statuses"].get(sha, [])}))
    sys.exit(0)

sys.stderr.write("gh stub: unhandled invocation: %r\n" % (argv,))
sys.exit(3)
'''


def _run_review_gate(
    tmp_path: Path,
    *,
    head_ref: str,
    pr_heads: dict[str, str],
    statuses: dict[str, list[dict[str, str]]],
    group_head: str = GROUP_SHA,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    """Execute the review gate's real shell with a stubbed `gh`.

    Returns the finished process and the ordered list of `gh` calls it made.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gh"
    stub.write_text(GH_STUB, encoding="utf-8")
    stub.chmod(0o755)

    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps({"pr_heads": pr_heads, "statuses": statuses}), encoding="utf-8"
    )
    calls_path = tmp_path / "calls.jsonl"
    calls_path.write_text("", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env.update(
        {
            "GH_STUB_FIXTURE": str(fixture_path),
            "GH_STUB_CALLS": str(calls_path),
            # Supplied to the step by the workflow / the Actions runner.
            "GH_TOKEN": "stub-token",
            "GITHUB_REPOSITORY": "alfloop-dev/odayplus",
            "HEAD_SHA": group_head,
            "HEAD_REF": head_ref,
        }
    )

    proc = subprocess.run(
        ["bash", "-c", _review_gate_step()["run"]],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    calls = [
        json.loads(line)
        for line in calls_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return proc, calls


def _posted(calls: list[dict[str, Any]], state: str) -> list[dict[str, Any]]:
    return [
        call
        for call in calls
        if call["call"] == "post_status" and call["fields"].get("state") == state
    ]


def test_review_gate_harness_prerequisites() -> None:
    """`jq` must exist for the scenario 3/4 harness to run the real filters.

    Asserted rather than skipped on purpose: a fail-closed test that silently
    skips is indistinguishable from one that passed.
    """
    assert shutil.which("jq"), (
        "jq is required to execute the merge queue review gate contract tests"
    )
    assert shutil.which("bash"), "bash is required to execute the review gate shell"


# ==============================================================================
# Scenario 1: Multiple qualified PRs form batch (up to max_entries_to_merge)
# ==============================================================================


def test_scenario_1_batch_formation_parameters_and_ruleset_payload() -> None:
    """Scenario 1: Policy enforces min_entries_to_merge=2 and max_entries_to_merge=5.

    Verifies the checked-in configuration and the ruleset payload the apply
    script would send. Live batch formation is a WP-35C acceptance item.
    """
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None, "merge_queue config missing for dev branch"

    assert dev_queue["min_entries_to_merge"] == 2, "Option B engineering default requires min=2"
    assert dev_queue["max_entries_to_merge"] == 5, "Max entries to merge must be 5"
    assert dev_queue["max_entries_to_build"] == 5, "Max speculative entries to build must be 5"
    assert dev_queue["min_entries_to_merge"] <= dev_queue["max_entries_to_merge"]

    ruleset = build_ruleset_payload(dev_queue, "dev")
    assert ruleset["name"] == "dev-merge-queue"
    assert ruleset["target"] == "branch"
    assert ruleset["enforcement"] == "active"
    assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/dev"]

    rules = ruleset["rules"]
    assert len(rules) == 1
    rule_params = rules[0]["parameters"]
    assert rule_params["min_entries_to_merge"] == 2
    assert rule_params["max_entries_to_merge"] == 5
    assert rule_params["max_entries_to_build"] == 5
    assert rule_params["merge_method"] == "MERGE"


# ==============================================================================
# Scenario 2: Solo-PR bounded wait timeout
# ==============================================================================


def test_scenario_2_solo_pr_bounded_wait_timeout() -> None:
    """Scenario 2: the accumulation wait is configured and bounded.

    This asserts the configured ceiling only. GitHub's timer start point and
    any resulting solo-PR hold duration are not observable offline and are
    reserved for WP-35C live verification.
    """
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None

    wait_minutes = dev_queue.get("min_entries_to_merge_wait_minutes")
    assert wait_minutes == 10, "Option B engineering default requires wait_minutes=10"
    assert 0 < wait_minutes <= 360, "Wait minutes must be bounded and positive"

    timeout_minutes = dev_queue.get("check_response_timeout_minutes")
    assert timeout_minutes == 60, "Check response timeout must be 60 minutes"
    assert wait_minutes < timeout_minutes, "Wait ceiling must be well within check timeout"

    ruleset_params = build_ruleset_payload(dev_queue, "dev")["rules"][0]["parameters"]
    assert ruleset_params["min_entries_to_merge_wait_minutes"] == 10
    assert ruleset_params["check_response_timeout_minutes"] == 60

    runbook_text = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "min_entries_to_merge_wait_minutes" in runbook_text
    assert "Bounded wait ceiling" in runbook_text


# ==============================================================================
# Scenario 3: Unapproved / failing PR exclusion (fail-closed)
# ==============================================================================


def test_scenario_3_required_contexts_are_configured_for_pre_admission() -> None:
    """Pre-admission: all 4 required contexts and admin enforcement stay on dev."""
    policy = _load_policy()
    required_checks = policy.get("required_status_checks", [])
    for context in REQUIRED_CONTEXTS:
        assert context in required_checks, f"required context {context} was dropped"
    assert policy.get("enforce_admins") is True

    dev_payload = build_payload(branch_policy(policy, "dev"))
    assert dev_payload["required_status_checks"]["contexts"] == list(REQUIRED_CONTEXTS)
    assert dev_payload["enforce_admins"] is True


def test_scenario_3_approved_pr_is_stamped_on_the_group_sha(tmp_path: Path) -> None:
    """Positive control: a PR whose own head carries a green gate is stamped.

    Without this, the rejection tests below could pass on a script that always
    fails, which would be a queue that blocks every merge.
    """
    proc, calls = _run_review_gate(
        tmp_path,
        head_ref=QUEUE_REF,
        pr_heads={"1284": PR_HEAD_SHA},
        statuses={PR_HEAD_SHA: [{"context": "task-review-gate", "state": "success"}]},
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    successes = _posted(calls, "success")
    assert len(successes) == 1, f"expected exactly one success stamp, got {calls}"
    assert successes[0]["endpoint"].endswith(f"/statuses/{GROUP_SHA}"), (
        "the gate must be stamped onto the merge group SHA, not any other commit"
    )
    assert successes[0]["fields"]["context"] == "task-review-gate"
    assert _posted(calls, "failure") == []


@pytest.mark.parametrize(
    "case, head_statuses",
    [
        ("no status at all", []),
        ("gate failed", [{"context": "task-review-gate", "state": "failure"}]),
        ("gate still pending", [{"context": "task-review-gate", "state": "pending"}]),
        ("gate errored", [{"context": "task-review-gate", "state": "error"}]),
        ("only other contexts green", [{"context": "product", "state": "success"}]),
    ],
)
def test_scenario_3_unapproved_or_failing_pr_is_refused(
    tmp_path: Path, case: str, head_statuses: list[dict[str, str]]
) -> None:
    """Scenario 3: anything other than a green gate on the PR's own head fails closed.

    Executing the real shell is what gives this teeth: the run must both exit
    non-zero *and* never post a success status. A `fail()` that stamped failure
    and then fell through to the success stamp would pass a text assertion and
    fail here.
    """
    proc, calls = _run_review_gate(
        tmp_path,
        head_ref=QUEUE_REF,
        pr_heads={"1284": PR_HEAD_SHA},
        statuses={PR_HEAD_SHA: head_statuses},
    )

    assert proc.returncode == 1, f"{case}: expected refusal, stdout={proc.stdout!r}"
    assert _posted(calls, "success") == [], (
        f"{case}: an unapproved candidate must never receive a success stamp"
    )
    failures = _posted(calls, "failure")
    assert len(failures) == 1, f"{case}: expected exactly one failure stamp, got {calls}"
    assert failures[0]["endpoint"].endswith(f"/statuses/{GROUP_SHA}")
    assert failures[0]["fields"]["context"] == "task-review-gate"


def test_scenario_3_unrecognised_queue_ref_is_refused(tmp_path: Path) -> None:
    """A ref the gate cannot attribute to a PR is refused before any lookup."""
    proc, calls = _run_review_gate(
        tmp_path,
        head_ref="refs/heads/dev",
        pr_heads={"1284": PR_HEAD_SHA},
        statuses={PR_HEAD_SHA: [{"context": "task-review-gate", "state": "success"}]},
    )

    assert proc.returncode == 1
    assert _posted(calls, "success") == []
    assert len(_posted(calls, "failure")) == 1
    assert [call for call in calls if call["call"] == "pr_view"] == [], (
        "an unparsable ref must not be resolved to some other PR's approval"
    )


# ==============================================================================
# Scenario 4: Head change re-validation
# ==============================================================================


def test_scenario_4_changed_head_cannot_reuse_the_previous_approval(
    tmp_path: Path,
) -> None:
    """Scenario 4: approval on a superseded head is not honoured.

    The PR was approved at OLD_HEAD_SHA and then pushed to NEW_HEAD_SHA, which
    carries no gate. The gate must read the PR's *current* head and refuse.
    """
    proc, calls = _run_review_gate(
        tmp_path,
        head_ref=QUEUE_REF,
        pr_heads={"1284": NEW_HEAD_SHA},
        statuses={OLD_HEAD_SHA: [{"context": "task-review-gate", "state": "success"}]},
    )

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert _posted(calls, "success") == [], "stale approval was reused on a new head"

    read_endpoints = [call["endpoint"] for call in calls if call["call"] == "get"]
    assert any(NEW_HEAD_SHA in endpoint for endpoint in read_endpoints), (
        "the gate must query the status of the PR's current head"
    )
    assert not any(OLD_HEAD_SHA in endpoint for endpoint in read_endpoints), (
        "the gate must not consult the superseded head"
    )


def test_scenario_4_renewed_approval_on_the_new_head_is_accepted(
    tmp_path: Path,
) -> None:
    """Control for the test above: re-approving the new head clears the gate.

    This isolates the refusal to the head change itself rather than to the
    fixture simply being unapprovable.
    """
    proc, calls = _run_review_gate(
        tmp_path,
        head_ref=QUEUE_REF,
        pr_heads={"1284": NEW_HEAD_SHA},
        statuses={
            OLD_HEAD_SHA: [{"context": "task-review-gate", "state": "success"}],
            NEW_HEAD_SHA: [{"context": "task-review-gate", "state": "success"}],
        },
    )

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert len(_posted(calls, "success")) == 1


def test_scenario_4_ci_reruns_on_pull_request_head_updates() -> None:
    """A new head must re-trigger CI, so `synchronize` has to stay covered.

    An omitted `types:` means GitHub's default (opened, synchronize, reopened).
    Narrowing it to `[opened]` would leave pushed-to PRs with stale CI.
    """
    triggers = _triggers(_load_yaml(CI_WORKFLOW_PATH))
    assert "pull_request" in triggers
    assert "merge_group" in triggers

    pull_request = triggers["pull_request"] or {}
    types = pull_request.get("types")
    assert types is None or "synchronize" in types, (
        f"pull_request types {types!r} would not re-run CI when a PR head changes"
    )


# ==============================================================================
# Scenario 5: Failure isolation under ALLGREEN
# ==============================================================================


def test_scenario_5_failure_isolation_under_allgreen() -> None:
    """Scenario 5: grouping_strategy is strictly ALLGREEN in policy and payload."""
    policy = _load_policy()
    dev_queue = merge_queue_config(policy, "dev")
    assert dev_queue is not None
    assert dev_queue["grouping_strategy"] == "ALLGREEN"

    rule_params = build_ruleset_payload(dev_queue, "dev")["rules"][0]["parameters"]
    assert rule_params["grouping_strategy"] == "ALLGREEN"


# ==============================================================================
# Scenario 6: Actual required checks on the merge group SHA
# ==============================================================================


def test_scenario_6_required_jobs_report_under_their_required_check_names() -> None:
    """The check name GitHub reports is the job's `name:`, or the job id if unset.

    Adding a `name:` to one of these jobs renames the check, which silently
    stops satisfying the required context and holds every group until timeout.
    """
    jobs = _load_yaml(CI_WORKFLOW_PATH)["jobs"]
    for context in ACTIONS_CONTEXTS:
        assert context in jobs, f"required context {context} has no job in ci.yml"
        assert jobs[context].get("name") in (None, context), (
            f"job {context} declares name={jobs[context].get('name')!r}, so it would "
            f"report a check name that is not the required context {context!r}"
        )


def test_scenario_6_required_jobs_are_eligible_on_merge_group() -> None:
    """No required job or step may be gated on the event type.

    A condition such as `github.event_name == 'pull_request'` leaves the job
    skipped on `merge_group`, and the required context never reports on the
    group SHA.
    """
    workflow = _load_yaml(CI_WORKFLOW_PATH)
    triggers = _triggers(workflow)
    assert triggers["merge_group"].get("types") == ["checks_requested"]

    jobs = workflow["jobs"]
    for context in ACTIONS_CONTEXTS:
        job = jobs[context]
        condition = job.get("if")
        if condition is not None:
            assert "github.event" not in condition, (
                f"job {context} is gated on the event: {condition!r}"
            )
        for step in job.get("steps", []):
            step_condition = step.get("if")
            if step_condition is not None:
                assert "github.event" not in str(step_condition), (
                    f"step {step.get('name')!r} in {context} is gated on the event: "
                    f"{step_condition!r}"
                )


def test_scenario_6_tooling_skip_stays_bounded_to_tooling_only_changes() -> None:
    """Pin the pre-existing tooling-scope skip (ci.yml:145/:337) to its exact shape.

    `product` lanes and `product-e2e-gate` are skipped when every changed path is
    development tooling. That skip predates this task, and this test does not
    endorse or widen it -- it pins its blast radius so that any later change to
    the condition or to the manifest behind it has to be made deliberately here
    rather than silently altering which required checks actually execute.
    """
    jobs = _load_yaml(CI_WORKFLOW_PATH)["jobs"]

    product_lanes = (
        "product-lint-unit",
        "product-db",
        "product-api-contract",
        "product-security",
        "product-node",
    )
    for context in (*product_lanes, "product-e2e-gate"):
        assert jobs[context].get("if") == TOOLING_SKIP_IF, (
            f"job {context} carries an unexpected condition: {jobs[context].get('if')!r}"
        )
        assert jobs[context].get("needs") == "change-scope"

    # `product` aggregates the five parallel product lanes and always runs
    # fail-closed to verify their results.
    assert jobs["product"].get("if") == "always()", (
        f"job product carries an unexpected condition: {jobs['product'].get('if')!r}"
    )
    assert jobs["product"].get("needs") == ["change-scope", *product_lanes]

    assert jobs["orchestrator"].get("if") is None, "orchestrator must never be skipped"
    assert jobs["change-scope"].get("if") is None, "the scope classifier must always run"


def test_scenario_6_queue_policy_changes_keep_the_product_job_on() -> None:
    """A change to this task's deliverables must not be skippable as tooling.

    `product` is the only job that runs `tests/contract`, so if the queue
    policy, the runbook, or this test file were classified as development
    tooling, a change to the merge queue contract could land with the tests
    that cover it never executed.
    """
    manifest = load_manifest()
    deliverables = [
        ".github/branch-protection/policy.json",
        "docs/runbooks/dev-merge-queue.md",
        "tests/contract/test_merge_queue_batch_policy.py",
    ]
    classified = classify_paths(deliverables, manifest)
    assert classified["scope"] == "product_or_mixed"
    assert sorted(classified["non_tooling_paths"]) == sorted(deliverables)


def test_scenario_6_workflow_only_changes_are_tooling_scoped() -> None:
    """Record the measured edge of the skip: workflow files are tooling paths.

    `config/change-review-scopes.json` lists `.github/workflows/` as a tooling
    prefix, so a change touching only `ci.yml` or `merge-queue-review-gate.yml`
    classifies as `development_tooling` and skips `product` -- and therefore
    skips these contract tests. The always-on `orchestrator` job still runs for
    such a change, but it does not execute `tests/contract`.

    This is a pre-existing property of the shared manifest, not something this
    task introduced, and narrowing it would change CI scheduling for every
    lane, which is outside this task's scope. It is asserted here so the
    boundary stays measured and any future change to it is deliberate. See
    docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001/README.md
    for the recorded limitation.
    """
    manifest = load_manifest()
    workflow_only = classify_paths(
        [".github/workflows/ci.yml", ".github/workflows/merge-queue-review-gate.yml"],
        manifest,
    )
    assert workflow_only["scope"] == "development_tooling"
    assert workflow_only["non_tooling_paths"] == []

    # The orchestrator job is the coverage that does survive such a change.
    jobs = _load_yaml(CI_WORKFLOW_PATH)["jobs"]
    assert jobs["orchestrator"].get("if") is None


def test_scenario_6_scope_classifier_resolves_the_merge_group_diff() -> None:
    """The skip decision must be computable on a merge_group event.

    `product` and `product-e2e-gate` consume `change-scope` output, so if the
    classifier had no merge_group branch it would fall through to its default
    and decide the skip from an unrelated diff.
    """
    job = _load_yaml(CI_WORKFLOW_PATH)["jobs"]["change-scope"]
    classify = [step for step in job["steps"] if step.get("id") == "classify"]
    assert len(classify) == 1, "change-scope has no single `classify` step"
    step = classify[0]

    assert step["env"]["MERGE_GROUP_BASE_SHA"] == "${{ github.event.merge_group.base_sha }}"
    assert step["env"]["MERGE_GROUP_HEAD_SHA"] == GROUP_HEAD_EXPR
    assert "merge_group)" in step["run"], "classifier has no merge_group branch"


def test_scenario_6_review_gate_builds_and_stamps_the_group_head() -> None:
    """`task-review-gate` must be posted onto the merge group head SHA.

    Switching the checkout ref or `HEAD_SHA` to `merge_group.base_sha` would
    stamp a commit the queue is not testing, leaving the group unreported.
    """
    workflow = _load_yaml(REVIEW_GATE_WORKFLOW_PATH)
    assert _triggers(workflow)["merge_group"].get("types") == ["checks_requested"]
    assert workflow.get("permissions", {}).get("statuses") == "write"

    job = workflow["jobs"]["review-gate"]
    checkouts = [
        step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == GROUP_HEAD_EXPR, (
        "the gate must evaluate the merge group head, not the base"
    )

    step = _review_gate_step()
    assert step["env"]["HEAD_SHA"] == GROUP_HEAD_EXPR
    assert step["env"]["HEAD_REF"] == GROUP_REF_EXPR
    assert "statuses/${HEAD_SHA}" in step["run"], (
        "the status must be POSTed to the merge group head SHA"
    )


# ==============================================================================
# Protection invariants
# ==============================================================================


def test_dev_merge_queue_strict_invariants() -> None:
    """dev must set strict=false behind the queue; main (no queue) keeps strict=true."""
    policy = _load_policy()
    dev_payload = build_payload(branch_policy(policy, "dev"))
    main_payload = build_payload(branch_policy(policy, "main"))

    assert dev_payload["required_status_checks"]["strict"] is False
    assert main_payload["required_status_checks"]["strict"] is True
