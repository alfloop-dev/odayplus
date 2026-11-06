"""Role/task_class provider eligibility, and the Codex model+effort it pairs with.

The policy under test says two things at once: reviews belong to Codex, and
*every* automatically dispatchable owner/helper lane belongs to Antigravity or
Claude. Almost every test here is a negative one, because the failure mode that
matters is not "the policy rejected something" -- it is a path that quietly kept
its old freedom while the board looked configured. So each contact point named by
the policy is asked directly whether it can still route around the rule: an
unnamed task_class, an agent record whose adapter disagrees with its provider, an
alias of the account that wrote the branch, a repair pass that rewrites the
author, and a frozen head used as a licence to sign a fresh approval.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import dispatch_engine
import pytest
import supervisor
import worker_failure_policy
from adapters.base import DeliveryRequest
from adapters.codex import CodexAdapter
from dispatch_policy import (
    REASON_HELPER_CLAIM,
    REASON_OWNED_READY,
    REASON_REVIEW_READY,
    ROLE_HELPER,
    ROLE_OWNER,
    ROLE_REVIEWER,
    ReviewSourceIdentity,
    allowed_role_provider_ids,
    dispatch_reason_role,
    review_source_block_reason,
    role_provider_block_reason,
    role_provider_policy_error,
    role_provider_policy_settings,
    task_review_source_identity,
)

#: The policy this fleet is being configured with, and the exact shape
#: `config.example.json` ships: review is Codex-only, and owned/helper work is
#: Antigravity/Claude-only for *every* task class, including ones nobody has
#: named yet and tasks carrying no class at all. Scoping the owner rule to a
#: list of classes was the earlier shape, and it left integration, verification,
#: runtime_release, rollout and unclassified work as lanes Codex could still
#: implement on -- the opposite of what the policy is for. Human gates keep
#: their own fail-closed treatment; they are not automatic lanes to begin with.
POLICY: dict[str, Any] = {
    "enabled": True,
    "rules": [
        {"roles": ["reviewer"], "providers": ["codex"]},
        {"roles": ["owner", "helper"], "providers": ["antigravity", "claude"]},
    ],
    "unclassified_owned_work": ["antigravity", "claude"],
}

#: A deliberately class-scoped policy, kept only to exercise the mechanics that
#: class scoping still has to support: rule intersection, and the disposition of
#: owned work carrying no class. The shipped policy above no longer relies on
#: either, which is precisely why they need their own fixture rather than being
#: read off the live shape.
CLASS_SCOPED_POLICY: dict[str, Any] = {
    "enabled": True,
    "rules": [
        {"roles": ["reviewer"], "providers": ["codex"]},
        {
            "roles": ["owner", "helper"],
            "task_classes": ["implementation", "remediation", "documentation"],
            "providers": ["antigravity", "claude"],
        },
    ],
    "unclassified_owned_work": "allow",
}


#: The per-test status and event-queue files `build_config` binds its fleet to.
#: Configs built while a test runs read nothing outside its own tmp directory.
_OWNED_PATHS: dict[str, Path] = {}


def build_config(policy: Any = POLICY, **overrides: Any) -> dict[str, Any]:
    """A two-Codex-pool fleet: the shape independent Codex review actually needs."""
    config: dict[str, Any] = {
        "providers": {
            "antigravity": {"delivery_mode": "antigravity"},
            "antigravity2": {"delivery_mode": "antigravity"},
            "claude": {"delivery_mode": "claude_cli"},
            "codex": {
                "delivery_mode": "codex",
                "codex": {
                    "cli": "codex",
                    "ask_for_approval": "never",
                    "model": "gpt-6-astra",
                    "model_reasoning_effort": "ultra",
                    "sandbox_mode": "workspace-write",
                },
            },
        },
        "agents": {
            "antigravity": {
                "display_name": "Antigravity",
                "provider": "antigravity",
                "adapter": "antigravity",
                "account_pool": "agy_main",
            },
            "antigravity2": {
                "display_name": "Antigravity2",
                "provider": "antigravity2",
                "adapter": "antigravity",
                "account_pool": "agy_main",
            },
            "claude": {
                "display_name": "Claude",
                "provider": "claude",
                "adapter": "claude_cli",
                "account_pool": "claude_main",
            },
            "claude2": {
                "display_name": "Claude2",
                "provider": "claude",
                "adapter": "claude_cli",
                "account_pool": "claude_main",
            },
            "codex": {
                "display_name": "Codex",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_bjoe",
            },
            "codex2": {
                "display_name": "Codex2",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_lupin",
            },
            "codex_bjoe_slot_1": {
                "display_name": "codex_bjoe_slot_1",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_bjoe",
                "dispatch_slot_for_pool": "codex_bjoe",
            },
            "codex_lupin_slot_1": {
                "display_name": "codex_lupin_slot_1",
                "provider": "codex",
                "adapter": "codex",
                "account_pool": "codex_lupin",
                "dispatch_slot_for_pool": "codex_lupin",
            },
        },
        "paths": {
            "event_queue": str(_OWNED_PATHS.get("event_queue", "/tmp/pantheon-test-empty-event-queue.jsonl")),
            "status_file": str(_OWNED_PATHS.get("status_file", "/tmp/pantheon-test-ai-status.json")),
        },
        "account_pools": {
            "agy_main": {"enabled": True, "max_concurrent": 2},
            "claude_main": {"enabled": True, "max_concurrent": 1},
            "codex_bjoe": {"enabled": True, "max_concurrent": 1},
            "codex_lupin": {"enabled": True, "max_concurrent": 1},
        },
        "ready_dispatcher": {},
    }
    if policy is not None:
        config["ready_dispatcher"]["role_provider_policy"] = json.loads(json.dumps(policy))
    config.update(overrides)
    return config


def _pool_alias_config() -> dict[str, Any]:
    """Two agent names, one real account -- the shape a name comparison misses."""
    config = build_config()
    config["agents"]["codex3"] = {
        "display_name": "Codex3",
        "provider": "codex",
        "adapter": "codex",
        # Deliberately the *same* pool as Codex: a different logical lane over
        # the same credential, quota budget and worker slots.
        "account_pool": "codex_bjoe",
    }
    return config


IMPLEMENTATION_TASK = {"id": "T-1", "status": "todo", "task_class": "implementation"}
INTEGRATION_TASK = {"id": "T-2", "status": "todo", "task_class": "integration"}
UNCLASSIFIED_TASK = {"id": "T-3", "status": "todo"}

#: A candidate list ordered so that a path which ignores the policy would visibly
#: pick the wrong lane: the excluded provider is offered first every time.
REVIEWER_POOL = ["Antigravity", "Claude", "Codex", "Codex2"]
OWNER_POOL = ["Codex", "Antigravity", "Claude"]

#: The per-test synthetic repository `_source_submission` writes ranges into.
_SOURCE_GIT: dict[str, Path] = {}


@pytest.fixture(autouse=True)
def _source_git_root(tmp_path: Path) -> Any:
    _SOURCE_GIT["root"] = tmp_path / "source-git"
    _OWNED_PATHS["status_file"] = tmp_path / "ai-status.json"
    _OWNED_PATHS["event_queue"] = tmp_path / "event-queue.jsonl"
    _OWNED_PATHS["event_queue"].write_text("", encoding="utf-8")
    yield
    _SOURCE_GIT.clear()
    _OWNED_PATHS.clear()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _source_repo() -> Path:
    """A real local repository with a `dev` integration branch and `origin/dev`."""
    repo = _SOURCE_GIT["root"]
    if not (repo / ".git").exists():
        repo.mkdir(parents=True)
        _git(repo, "init", "-q", "-b", "dev")
        _git(repo, "config", "user.email", "worker@pantheon.local")
        _git(repo, "config", "user.name", "Worker")
        (repo / "base.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "base.txt")
        _git(repo, "commit", "-q", "-m", "bootstrap")
        _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    return repo


def _source_commit(repo: Path, task_id: str, agent: str, label: str) -> str:
    reviewer = "Claude" if agent == "Codex2" else "Codex2"
    (repo / f"{label}.txt").write_text(label + "\n", encoding="utf-8")
    _git(repo, "add", f"{label}.txt")
    _git(
        repo, "commit", "-q", "-m",
        f"{task_id}: {label}\n\nLLM-Agent: {agent}\nTask-ID: {task_id}\nReviewer: {reviewer}\n",
    )
    return _git(repo, "rev-parse", "HEAD")


def _source_submission(
    config: dict[str, Any], task_id: str, *authors: str, submitted_by: str, **extra: Any
) -> dict[str, Any]:
    """A review submission for a real synthetic range, as `submit_review` records it.

    Each of `authors` commits on the task branch, `dev` then advances, and the
    branch takes it back in a merge -- the finalizer's base advance, which is
    not a source commit. The submission records the resolved base and head and
    the authors exactly as the scanner finds them, and `config` is bound to the
    repository so the common rule reads the same range from Git.
    """
    repo = _source_repo()
    repositories = config.setdefault("coordination", {}).setdefault("repositories", {})
    repositories.setdefault("pantheon", {})["local_path"] = str(repo)
    config.setdefault("branch_workflow", {})["dev_branch"] = "dev"
    serial = len(_git(repo, "rev-list", "--all").splitlines())
    _git(repo, "checkout", "-q", "-b", f"work/{task_id}-{serial}", "dev")
    for index, author in enumerate(authors):
        _source_commit(repo, task_id, author, f"{task_id}-{serial}-{index}")
    _git(repo, "checkout", "-q", "dev")
    (repo / f"dev-{serial}.txt").write_text("dev\n", encoding="utf-8")
    _git(repo, "add", f"dev-{serial}.txt")
    _git(repo, "commit", "-q", "-m", f"OTHER-{serial}: unrelated dev work")
    _git(repo, "update-ref", "refs/remotes/origin/dev", "dev")
    base = _git(repo, "rev-parse", "dev")
    _git(repo, "checkout", "-q", f"work/{task_id}-{serial}")
    _git(repo, "merge", "--no-ff", "-q", "-m", f"Merge dev into task/{task_id}\n\nLLM-Agent: {submitted_by}\n", "dev")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "dev")
    submission: dict[str, Any] = {
        "pr_number": 1221,
        "remote_sha": head,
        "source_head": head,
        "source_base": base,
        "source_authors": list(authors),
        "submitted_by": submitted_by,
    }
    submission.update(extra)
    return submission


# --------------------------------------------------------------------------- #
# No policy configured
# --------------------------------------------------------------------------- #


def test_absent_policy_leaves_every_lane_eligible() -> None:
    """No policy means the previous behaviour, not an empty allow-list."""
    config = build_config(policy=None)
    assert role_provider_policy_settings(config) is None
    assert role_provider_policy_error(config) is None
    for agent in ("Antigravity", "Claude", "Codex"):
        for role in (ROLE_OWNER, ROLE_REVIEWER, ROLE_HELPER):
            assert worker_failure_policy.agent_can_take_task(
                config, agent, dict(IMPLEMENTATION_TASK), role=role
            )


def test_disabled_policy_is_the_same_as_no_policy() -> None:
    config = build_config(policy={**POLICY, "enabled": False})
    assert role_provider_policy_settings(config) is None
    assert worker_failure_policy.agent_can_take_task(
        config, "Antigravity", dict(IMPLEMENTATION_TASK), role=ROLE_REVIEWER
    )


def test_role_is_optional_for_callers_that_cannot_name_one() -> None:
    """A caller with no role still gets the pre-existing predicate, unchanged."""
    config = build_config()
    assert worker_failure_policy.agent_can_take_task(config, "Antigravity", dict(IMPLEMENTATION_TASK))


# --------------------------------------------------------------------------- #
# The policy itself
# --------------------------------------------------------------------------- #


def test_example_config_policy_is_valid_and_expresses_the_intended_rule() -> None:
    """The shipped example is the template an operator activates; it must be sound.

    Validated with `enabled` forced on, because the example deliberately ships
    the policy switched off -- activating it is a live-config rollout decision --
    and a disabled policy would otherwise validate vacuously.
    """
    example = json.loads((Path(__file__).with_name("config.example.json")).read_text())
    policy = example["ready_dispatcher"]["role_provider_policy"]
    example["ready_dispatcher"]["role_provider_policy"] = {**policy, "enabled": True}

    assert role_provider_policy_error(example) is None
    assert allowed_role_provider_ids(example, role=ROLE_REVIEWER, task_class="implementation") == {
        "codex"
    }
    # Every automatic owner/helper lane, whatever the work is called. The list of
    # classes below is not exhaustive and cannot be: the point of a role-wide
    # rule is that a class nobody has invented yet is covered too, which is what
    # the last two entries stand for.
    for role in (ROLE_OWNER, ROLE_HELPER):
        for task_class in (
            "implementation",
            "remediation",
            "documentation",
            "integration",
            "verification",
            "runtime_release",
            "rollout",
            "a_class_this_fleet_has_never_used",
            "",
        ):
            assert allowed_role_provider_ids(example, role=role, task_class=task_class) == {
                "antigravity",
                "claude",
            }, task_class
    # And the shipped disposition for unclassified owned work names the same two
    # providers rather than allowing anything, so the two cannot drift apart.
    assert example["ready_dispatcher"]["role_provider_policy"]["unclassified_owned_work"] == [
        "antigravity",
        "claude",
    ]


@pytest.mark.parametrize(
    ("role", "task_class", "expected"),
    [
        (ROLE_REVIEWER, "implementation", {"codex"}),
        (ROLE_REVIEWER, "integration", {"codex"}),
        (ROLE_REVIEWER, "", {"codex"}),
        (ROLE_OWNER, "implementation", {"antigravity", "claude"}),
        (ROLE_OWNER, "remediation", {"antigravity", "claude"}),
        (ROLE_OWNER, "documentation", {"antigravity", "claude"}),
        (ROLE_HELPER, "implementation", {"antigravity", "claude"}),
        # The classes the earlier class-scoped shape left open. Each of these is
        # implementation work by another name, so each is now covered.
        (ROLE_OWNER, "integration", {"antigravity", "claude"}),
        (ROLE_OWNER, "verification", {"antigravity", "claude"}),
        (ROLE_OWNER, "runtime_release", {"antigravity", "claude"}),
        (ROLE_OWNER, "rollout", {"antigravity", "claude"}),
        (ROLE_OWNER, "sidecar", {"antigravity", "claude"}),
        (ROLE_HELPER, "rollout", {"antigravity", "claude"}),
        # A class this fleet has never configured, and no class at all.
        (ROLE_OWNER, "not_a_class_anyone_declared", {"antigravity", "claude"}),
        (ROLE_OWNER, "", {"antigravity", "claude"}),
        (ROLE_HELPER, "", {"antigravity", "claude"}),
        # No task in hand at all: a role-wide rule is still a role-wide rule.
        (ROLE_OWNER, None, {"antigravity", "claude"}),
        (ROLE_REVIEWER, None, {"codex"}),
    ],
)
def test_allowed_providers_per_role_and_class(
    role: str, task_class: str | None, expected: set[str] | None
) -> None:
    assert allowed_role_provider_ids(build_config(), role=role, task_class=task_class) == expected


@pytest.mark.parametrize(
    "task_class",
    ["integration", "verification", "runtime_release", "rollout", "a_brand_new_class", ""],
)
def test_codex_cannot_own_or_help_on_any_task_class(task_class: str) -> None:
    """The class-scoped shape this replaced is the defect, not a smaller version of it.

    Naming three classes left `integration`, `verification`, `runtime_release`,
    `rollout` and every class nobody had invented yet as lanes Codex could
    implement on -- while the acceptance says Codex only reviews. A task class is
    a label an operator types; it must not be the thing that decides whether the
    rule applies.
    """
    config = build_config()
    task = {"id": "T-C", "status": "todo"}
    if task_class:
        task["task_class"] = task_class
    for role in (ROLE_OWNER, ROLE_HELPER):
        assert not worker_failure_policy.agent_can_take_task(config, "Codex", task, role=role)
        assert worker_failure_policy.agent_can_take_task(config, "Antigravity", task, role=role)
    # Review of that same work stays on Codex rather than following the owner.
    assert worker_failure_policy.agent_can_take_task(config, "Codex", task, role=ROLE_REVIEWER)
    assert not worker_failure_policy.agent_can_take_task(config, "Claude", task, role=ROLE_REVIEWER)


@pytest.mark.parametrize(
    "task",
    [
        {"id": "T-H1", "status": "todo", "task_class": "human_gate", "owner": "Human/Ops"},
        {"id": "T-H2", "status": "todo", "task_class": "rollout", "human_required_roles": ["ops"]},
        {"id": "T-H3", "status": "todo", "task_class": "rollout", "gate_status": "pending_human_go"},
        {"id": "T-H4", "status": "todo", "task_class": "rollout", "non_dispatchable": True},
    ],
)
def test_human_gated_work_is_not_handed_to_an_automatic_lane_by_this_rule(
    task: dict[str, Any]
) -> None:
    """Widening the owner rule must not turn a human gate into Antigravity work.

    The rule now says every automatic owner lane is Antigravity/Claude. A gate is
    not an automatic lane, and "Antigravity is permitted here" must never be read
    as "so Antigravity may take it". The dispatch predicate keeps refusing these
    tasks for every agent, before the provider question is even asked.
    """
    config = build_config()
    for agent in ("Antigravity", "Claude", "Codex"):
        for role in (ROLE_OWNER, ROLE_HELPER, ROLE_REVIEWER):
            assert not worker_failure_policy.agent_can_take_task(config, agent, dict(task), role=role)


def test_two_rules_reaching_one_combination_intersect() -> None:
    """A lane must satisfy every rule about it, not merely one of them."""
    policy = {
        "enabled": True,
        "rules": [
            {"roles": ["owner"], "providers": ["antigravity", "claude"]},
            {
                "roles": ["owner"],
                "task_classes": ["implementation"],
                "providers": ["claude", "codex"],
            },
        ],
        "unclassified_owned_work": "allow",
    }
    config = build_config(policy=policy)
    assert allowed_role_provider_ids(config, role=ROLE_OWNER, task_class="implementation") == {"claude"}
    assert allowed_role_provider_ids(config, role=ROLE_OWNER, task_class="integration") == {
        "antigravity",
        "claude",
    }


@pytest.mark.parametrize(
    ("agent", "expected_identity"),
    [
        ("Codex", "codex"),
        ("Codex2", "codex"),
        # Physical dispatch slots are separate agent records. The policy is about
        # providers, so a slot has to resolve to the provider behind it rather
        # than to its own slot name -- otherwise a queue event targeting a slot
        # would be judged against an identity no rule can ever name.
        ("codex_bjoe_slot_1", "codex"),
        ("codex_lupin_slot_1", "codex"),
        ("Antigravity2", "antigravity"),
        ("Claude", "claude"),
    ],
)
def test_logical_and_physical_lanes_resolve_to_their_provider(
    agent: str, expected_identity: str
) -> None:
    from dispatch_policy import agent_provider_identity_ids

    identities = agent_provider_identity_ids(build_config(), agent)
    assert expected_identity in identities
    # And the same resolver backs the preference group, so the hard gate and the
    # soft ranking cannot disagree about what provider a lane is.
    assert worker_failure_policy.agent_provider_identity_ids(build_config(), agent) == identities


def test_dispatch_reason_role_mapping_covers_every_execution_reason() -> None:
    assert dispatch_reason_role(REASON_REVIEW_READY) == ROLE_REVIEWER
    assert dispatch_reason_role(REASON_OWNED_READY) == ROLE_OWNER
    assert dispatch_reason_role("owned_in_progress_dispatch") == ROLE_OWNER
    # Finalize is the owner making its own approved head durable.
    assert dispatch_reason_role("owned_finalize_dispatch") == ROLE_OWNER
    assert dispatch_reason_role(REASON_HELPER_CLAIM) == ROLE_HELPER
    assert dispatch_reason_role("discussion_planning_readout_dispatch") is None
    assert dispatch_reason_role(None) is None


# --------------------------------------------------------------------------- #
# Fail-closed cases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("policy", "fragment"),
    [
        ({"enabled": True, "rules": "codex", "unclassified_owned_work": "allow"}, "rules must be a list"),
        (
            {"enabled": True, "rules": ["codex"], "unclassified_owned_work": "allow"},
            "rules[0] must be an object",
        ),
        (
            {
                "enabled": True,
                "rules": [{"providers": ["codex"]}],
                "unclassified_owned_work": "allow",
            },
            "rules[0].roles must be a list",
        ),
        (
            {
                "enabled": True,
                "rules": [{"roles": ["approver"], "providers": ["codex"]}],
                "unclassified_owned_work": "allow",
            },
            "unknown role(s): approver",
        ),
        (
            {
                "enabled": True,
                "rules": [{"roles": ["reviewer"], "providers": []}],
                "unclassified_owned_work": "allow",
            },
            "must name at least one provider",
        ),
        (
            {
                "enabled": True,
                "rules": [{"roles": ["reviewer"], "providers": ["kodex"]}],
                "unclassified_owned_work": "allow",
            },
            "not configured in this fleet: kodex",
        ),
        (
            {
                "enabled": True,
                "rules": [
                    {"roles": ["reviewer"], "task_classes": [], "providers": ["codex"]}
                ],
                "unclassified_owned_work": "allow",
            },
            "task_classes must be a list",
        ),
        (
            {"enabled": True, "rules": [{"roles": ["reviewer"], "providers": ["codex"]}]},
            "unclassified_owned_work must be set",
        ),
        (
            {
                "enabled": True,
                "rules": [{"roles": ["reviewer"], "providers": ["codex"]}],
                "unclassified_owned_work": "maybe",
            },
            'must be "allow" or "deny"',
        ),
        (
            {
                "enabled": True,
                "rules": [{"roles": ["reviewer"], "providers": ["codex"]}],
                "unclassified_owned_work": ["nosuchprovider"],
            },
            "not configured in this fleet: nosuchprovider",
        ),
    ],
)
def test_malformed_enabled_policy_fails_closed(policy: dict[str, Any], fragment: str) -> None:
    """A policy that cannot be read denies; it must not degrade to no policy.

    The rule most likely to be lost to a typo is the one that says only Codex
    reviews, and a lost rule is indistinguishable from an absent one. So an
    unreadable policy blocks every role rather than silently restoring the
    freedom it was written to remove.
    """
    config = build_config(policy=policy)
    error = role_provider_policy_error(config)
    assert error is not None and fragment in error
    for agent in ("Antigravity", "Claude", "Codex"):
        reason = role_provider_block_reason(
            config, agent, role=ROLE_REVIEWER, task_class="implementation"
        )
        assert reason is not None
        assert "malformed" in reason
        assert not worker_failure_policy.agent_can_take_task(
            config, agent, dict(IMPLEMENTATION_TASK), role=ROLE_REVIEWER
        )


def test_an_agent_whose_adapter_contradicts_its_provider_fails_closed() -> None:
    """Naming a lane after an allowed provider must not decide what it runs.

    Identity matching is a union over aliases -- `antigravity2` and
    `antigravity` are one lane spelled two ways, and a rule naming either has to
    match. The union is also the escape: an agent record that omits `provider`
    gets one inferred from its own agent id, so a lane called `claude` carrying
    `adapter: codex` resolves to {claude, claude_cli, codex} and satisfies an
    owner rule written for Claude, while `codex exec` is what actually launches.
    The adapter is what runs, so a record whose adapter is not one of the
    identities its configured provider resolves to is contradictory, and a
    contradictory record is refused rather than read charitably.
    """
    from dispatch_policy import agent_provider_identity_conflict

    config = build_config()
    # No `provider` key at all: the provider is inferred from the agent id.
    config["agents"]["claude_helper"] = {
        "display_name": "claude",
        "adapter": "codex",
        "account_pool": "codex_bjoe",
    }
    config["agents"]["claude"] = {
        **config["agents"]["claude"],
        "adapter": "codex",
    }
    del config["agents"]["claude"]["provider"]

    conflict = agent_provider_identity_conflict(config, "claude")
    assert conflict is not None and "no `provider` is declared" in conflict

    for role in (ROLE_OWNER, ROLE_HELPER):
        reason = role_provider_block_reason(
            config, "claude", role=role, task_class="implementation"
        )
        assert reason is not None, role
        assert "contradictory" in reason
        assert not worker_failure_policy.agent_can_take_task(
            config, "claude", dict(IMPLEMENTATION_TASK), role=role
        )
    # An empty candidate list is not the same counterexample: this record does
    # resolve to identities, and one of them is an allowed provider. The refusal
    # has to come from the contradiction, not from having nothing to match.
    from dispatch_policy import agent_provider_identity_ids

    assert "claude" in agent_provider_identity_ids(config, "claude")

    # A record that declares a provider whose delivery mode disagrees with the
    # agent adapter is the same defect written the other way round.
    config["agents"]["claude2"] = {
        "display_name": "Claude2",
        "provider": "claude",
        "adapter": "codex",
        "account_pool": "claude_second",
    }
    conflict = agent_provider_identity_conflict(config, "claude2")
    assert conflict is not None and "provider claude" in conflict
    assert (
        role_provider_block_reason(config, "Claude2", role=ROLE_OWNER, task_class="implementation")
        is not None
    )

    # And the aliasing the union exists for is untouched: Antigravity2 runs the
    # antigravity adapter behind the antigravity2 provider alias, and stays a
    # perfectly ordinary owner.
    assert agent_provider_identity_conflict(config, "antigravity2") is None
    assert (
        role_provider_block_reason(
            config, "Antigravity2", role=ROLE_OWNER, task_class="implementation"
        )
        is None
    )


def test_agent_with_unresolvable_provider_identity_fails_closed() -> None:
    """An unknown provider is not evidence of permission.

    A name that resolves to no provider at all reaches the restricted set with
    an empty identity. Intersecting empty with the allowed set is vacuously
    false either way; asserting the reason makes it explicit that this denies
    rather than falls through.
    """
    config = build_config()
    reason = role_provider_block_reason(config, "", role=ROLE_REVIEWER, task_class="implementation")
    assert reason is not None and "no resolvable provider identity" in reason


@pytest.mark.parametrize(
    ("unclassified", "codex_allowed", "claude_allowed"),
    [
        ("allow", True, True),
        ("deny", False, False),
        (["claude"], False, True),
    ],
)
def test_unclassified_owned_work_disposition_is_explicit(
    unclassified: Any, codex_allowed: bool, claude_allowed: bool
) -> None:
    """Owned work with no task_class is disposed of by config, never by default.

    Every class-scoped rule leaves this gap behind. Left implicit it is exactly
    where unclassified implementation work would keep drifting onto whichever
    lane sorts first, so a policy that scopes its rules by class has to say what
    happens to it. Exercised against `CLASS_SCOPED_POLICY`, because the shipped
    policy closes the gap a different way -- its owner rule is role-wide, so no
    unclassified task ever reaches this disposition at all.
    """
    config = build_config(
        policy={**CLASS_SCOPED_POLICY, "unclassified_owned_work": unclassified}
    )
    assert role_provider_policy_error(config) is None
    task = dict(UNCLASSIFIED_TASK)
    assert (
        worker_failure_policy.agent_can_take_task(config, "Codex", task, role=ROLE_OWNER)
        is codex_allowed
    )
    assert (
        worker_failure_policy.agent_can_take_task(config, "Claude", task, role=ROLE_OWNER)
        is claude_allowed
    )
    # The reviewer rule is role-wide, so it is unaffected by the disposition.
    assert worker_failure_policy.agent_can_take_task(config, "Codex", task, role=ROLE_REVIEWER)
    assert not worker_failure_policy.agent_can_take_task(config, "Claude", task, role=ROLE_REVIEWER)


# --------------------------------------------------------------------------- #
# In-flight work is not re-litigated by a policy change
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "frozen_task",
    [
        {"id": "T-F1", "status": "review_approved", "task_class": "implementation",
         "owner": "Codex", "reviewer": "Antigravity"},
        {"id": "T-F2", "status": "review", "task_class": "implementation",
         "owner": "Codex", "reviewer": "Antigravity", "approved_head": "a" * 40},
        {"id": "T-F3", "status": "review", "task_class": "implementation",
         "owner": "Codex", "reviewer": "Antigravity", "merge_route": {"head": "b" * 40}},
    ],
)
def test_a_frozen_closeout_is_outside_the_policy_for_both_actors(
    frozen_task: dict[str, Any]
) -> None:
    """Turning the policy on must not rewrite who did work that is already done.

    On a pinned head the policy would not be choosing an implementer or a
    reviewer -- it would be evicting the owner from finalizing its own approved
    commit, or restating who approved it. Both actors are history there, so the
    restriction does not apply and the reconcile pass leaves the record alone.

    The exemption reaches exactly this far: preserving what the record already
    says. Installing a different actor or signing a fresh approval on the same
    frozen task is a new grant, and the two tests below draw that line.
    """
    config = build_config()
    task = dict(frozen_task)
    for agent, role in (("Codex", ROLE_OWNER), ("Antigravity", ROLE_REVIEWER)):
        assert role_provider_block_reason(config, agent, role=role, task=task) is None
        assert worker_failure_policy.agent_can_take_task(config, agent, task, role=role)
        assert (
            supervisor.task_actor_assignment_block_reason(config, {}, task, agent, role=role)
            is None
        )


def test_the_same_actors_are_restricted_once_the_head_is_not_frozen() -> None:
    """The exemption is about the pinned head, not about the agents."""
    config = build_config()
    live_task = {
        "id": "T-F4",
        "status": "in_progress",
        "task_class": "implementation",
        "owner": "Codex",
        "reviewer": "Antigravity",
    }
    assert not worker_failure_policy.agent_can_take_task(
        config, "Codex", live_task, role=ROLE_OWNER
    )
    assert not worker_failure_policy.agent_can_take_task(
        config, "Antigravity", live_task, role=ROLE_REVIEWER
    )


def test_queued_finalize_for_an_approved_head_is_not_skipped_by_the_policy() -> None:
    """A finalize dispatch for a frozen head still reaches its own owner."""
    config = build_config()
    task_map = {
        "T-1": {
            "id": "T-1",
            "status": "review_approved",
            "task_class": "implementation",
            "owner": "Codex",
            "approved_head": "c" * 40,
        }
    }
    event = {
        "event_id": "evt-2",
        "task_id": "T-1",
        "target_agent": "codex",
        "target_display_name": "Codex",
        "reason": "owned_finalize_dispatch",
    }
    message = dispatch_engine.stale_dispatch_skip_message(config, event, task_map)
    assert message is None or "role/provider policy" not in message


def test_a_frozen_head_is_not_a_licence_to_sign_the_next_approval() -> None:
    """The exemption preserves who already acted; it does not authorize a new act.

    A reopened task keeps its `approved_head`, so "this head is frozen" is true
    of exactly the record where an excluded reviewer would next want to sign.
    Reading the freeze as permission there would let the previous approval buy
    the following one -- and the approval, not the dispatch, is what the merge
    gate trusts. So an approval always asks as a new grant.
    """
    import ai_status

    config = build_config()
    task = {
        "id": "T-F5",
        "status": "review",
        "task_class": "implementation",
        "owner": "Codex",
        "reviewer": "Antigravity",
        "approved_head": "d" * 40,
    }
    with mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config):
        # Preserving the record as it stands is still exempt...
        assert (
            ai_status.role_provider_assignment_block_reason(
                "Antigravity", role="reviewer", task=task
            )
            is None
        )
        # ...and signing something new on top of it is not.
        reason = ai_status.role_provider_assignment_block_reason(
            "Antigravity", role="reviewer", task=task, grants_new_authority=True
        )
        assert reason is not None and "not permitted" in reason
        # The distinction is about authority, not about the agent: the reviewer
        # the policy does permit passes both questions.
        assert (
            ai_status.role_provider_assignment_block_reason(
                "Codex2", role="reviewer", task=task, grants_new_authority=True
            )
            is None
        )


# --------------------------------------------------------------------------- #
# Selection paths: review must not escape back to Antigravity/Claude
# --------------------------------------------------------------------------- #


def _select(config: dict[str, Any], pool: list[str], **kwargs: Any) -> str | None:
    params: dict[str, Any] = {
        "exclude": set(),
        "task": dict(IMPLEMENTATION_TASK),
        "balance_load": False,
    }
    params.update(kwargs)
    return worker_failure_policy.first_viable_agent(config, pool, **params)


def test_reviewer_selection_never_falls_back_to_an_implementation_lane() -> None:
    config = build_config()
    assert _select(config, REVIEWER_POOL, role=ROLE_REVIEWER) == "Codex"


def test_reviewer_selection_waits_rather_than_escaping_when_codex_is_excluded() -> None:
    """With every Codex lane blocked, review has no answer -- and that is the answer.

    Falling through to Antigravity or Claude here would be the whole defect:
    an automated review by the same provider family that implements the work.
    """
    config = build_config()
    assert _select(config, REVIEWER_POOL, role=ROLE_REVIEWER, exclude={"Codex", "Codex2"}) is None


def test_exhausted_codex_pools_make_review_wait_rather_than_reassign() -> None:
    """Every Codex account unavailable means no reviewer -- never a different provider.

    Driven through the real account-pool lifecycle rather than a patched
    predicate, because `worker_failure_policy` re-copies the supervisor's globals
    over its own on every entry: a mock installed on this module's namespace is
    silently replaced before the function under test reads it.
    """
    config = build_config()
    config["account_pools"]["codex_bjoe"]["enabled"] = False
    config["account_pools"]["codex_lupin"]["enabled"] = False

    assert _select(config, REVIEWER_POOL, role=ROLE_REVIEWER, state={}) is None

    # ...and the fail-closed branch is not taken, because Codex remains the
    # configured, role-eligible reviewer. Capacity is momentary; eligibility is
    # not, so the churn path waits instead of escalating to a human.
    assert worker_failure_policy.has_configured_reassignment_candidates(
        config,
        REVIEWER_POOL,
        exclude=set(),
        task=dict(IMPLEMENTATION_TASK),
        role=ROLE_REVIEWER,
    )


def test_unmeasurable_codex_capacity_does_not_release_review_to_another_provider() -> None:
    """"Capacity unknown" is not "somebody else may do it"."""
    config = build_config()

    def block(
        _config: Any, _state: Any, agent_id: str, provider_report: Any = None
    ) -> str | None:
        del provider_report
        return "codex capacity is not measurable" if "codex" in str(agent_id).lower() else None

    # Patched at the injection source: `_sync_supervisor_scope` copies
    # `supervisor.__dict__` into this module on every decorated call, so the
    # supervisor's binding is the one that actually gets used.
    with mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", block):
        assert _select(config, REVIEWER_POOL, role=ROLE_REVIEWER, state={}) is None


def test_configured_candidates_applies_the_same_role_filter() -> None:
    """Otherwise a policy-excluded lane would be counted as an alternative.

    That would turn "no viable reviewer" into "wait forever for a candidate the
    selector will never return", instead of the intended fail-closed block.
    """
    config = build_config()
    assert not worker_failure_policy.has_configured_reassignment_candidates(
        config,
        ["Antigravity", "Claude"],
        exclude=set(),
        task=dict(IMPLEMENTATION_TASK),
        role=ROLE_REVIEWER,
    )
    # The same list is a perfectly good owner candidate set.
    assert worker_failure_policy.has_configured_reassignment_candidates(
        config,
        ["Antigravity", "Claude"],
        exclude=set(),
        task=dict(IMPLEMENTATION_TASK),
        role=ROLE_OWNER,
    )


def test_owner_selection_excludes_codex_for_implementation_class_work() -> None:
    """Codex is offered first and still must not be chosen."""
    config = build_config()
    assert _select(config, OWNER_POOL, role=ROLE_OWNER) == "Antigravity"


def test_owner_selection_excludes_codex_for_integration_class_work_too() -> None:
    """Integration is implementation under another name, so it is not an exemption.

    Codex is offered first in `OWNER_POOL` here as well: a selector that had kept
    the old class-scoped carve-out would return it immediately.
    """
    config = build_config()
    assert _select(config, OWNER_POOL, role=ROLE_OWNER, task=dict(INTEGRATION_TASK)) == "Antigravity"


def test_helper_claim_is_filtered_by_the_same_rule_as_owner() -> None:
    config = build_config()
    assert not worker_failure_policy.agent_can_take_task(
        config, "Codex", dict(IMPLEMENTATION_TASK), role=ROLE_HELPER
    )
    assert worker_failure_policy.agent_can_take_task(
        config, "Antigravity", dict(IMPLEMENTATION_TASK), role=ROLE_HELPER
    )


def test_owner_failure_fallback_does_not_drag_the_reviewer_off_codex() -> None:
    """The reviewer survives an owner rotation; it does not get re-picked freely.

    `maybe_reassign_task_after_worker_failure` re-runs the reviewer search with
    the new owner excluded. Without the role filter that search would happily
    return Antigravity, quietly converting a Codex review into a same-family one
    at the exact moment nobody is looking at the reviewer field.
    """
    config = build_config()
    new_owner = _select(config, OWNER_POOL, role=ROLE_OWNER, exclude={"Claude"})
    assert new_owner == "Antigravity"
    carried_reviewer = _select(
        config,
        ["Codex"],
        role=ROLE_REVIEWER,
        exclude={new_owner},
    )
    assert carried_reviewer == "Codex"
    rebuilt_reviewer = _select(
        config,
        ["Antigravity", "Antigravity2", "Claude", "Codex2"],
        role=ROLE_REVIEWER,
        exclude={new_owner},
    )
    assert rebuilt_reviewer == "Codex2"


def test_review_churn_rotation_rotates_owner_without_moving_review_off_codex() -> None:
    """Churn rotates the implementation lane inside its own allowed group."""
    config = build_config()
    rotated_owner = _select(config, OWNER_POOL, role=ROLE_OWNER, exclude={"Antigravity"})
    assert rotated_owner == "Claude"
    assert _select(config, REVIEWER_POOL, role=ROLE_REVIEWER, exclude={rotated_owner}) == "Codex"


def test_account_pool_independence_survives_a_single_provider_review_rule() -> None:
    """Codex-authored work reviews on the *other* Codex account, not on itself.

    Restricting review to one provider is not permission to self-review. With
    both pools eligible the pool exclusion still decides, which is what keeps the
    reviewer an independent account rather than the owner's own.
    """
    config = build_config()
    owner_pool = worker_failure_policy.agent_account_pool_id(config, "Codex")
    reviewer = _select(
        config,
        REVIEWER_POOL,
        role=ROLE_REVIEWER,
        task=dict(INTEGRATION_TASK),
        exclude={"Codex"},
        exclude_pools={owner_pool},
    )
    assert reviewer == "Codex2"
    assert worker_failure_policy.agent_account_pool_id(config, reviewer) != owner_pool

    # And when the second pool is gone too, there is no reviewer at all rather
    # than a relaxed self-review.
    assert (
        _select(
            config,
            REVIEWER_POOL,
            role=ROLE_REVIEWER,
            task=dict(INTEGRATION_TASK),
            exclude={"Codex", "Codex2"},
            exclude_pools={owner_pool},
        )
        is None
    )


def test_assignment_integrity_reports_a_reviewer_the_policy_excludes() -> None:
    """A stale reviewer is a stable assignment problem, not a silent no-dispatch."""
    config = build_config()
    task = {
        "id": "T-9",
        "status": "review",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Claude",
        "reviewer": "Antigravity",
    }
    reason = supervisor.task_actor_assignment_block_reason(
        config, {}, task, "Antigravity", role=ROLE_REVIEWER
    )
    assert reason is not None and "not eligible" in reason
    # The same actor is a legitimate owner, so the audit is role-specific and
    # does not simply condemn the agent everywhere.
    assert (
        supervisor.task_actor_assignment_block_reason(
            config, {}, task, "Antigravity", role=ROLE_OWNER
        )
        is None
    )


# --------------------------------------------------------------------------- #
# Submitted authorship survives the repair pass
# --------------------------------------------------------------------------- #


def _submitted_task(**overrides: Any) -> dict[str, Any]:
    """A Codex-authored branch already sitting in review, as the board records it."""
    task = {
        "id": "T-SUB",
        "status": "review",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Codex",
        "reviewer": "Codex2",
        "review_submission": {"pr_number": 1221, "head": "e" * 40},
    }
    task.update(overrides)
    return task


def _run_repair(config: dict[str, Any], task: dict[str, Any], *, pin_author: bool) -> list[Any]:
    """Both repair passes, with every write captured instead of performed.

    `pin_author` off is the counterfactual: it neutralizes only the authorship
    pin, so what the passes do next is exactly what they did before this fix.
    Without that arm the assertions below would pass on code that never
    attempted a reassignment at all, for some reason having nothing to do with
    the thing under test.
    """
    changes: list[Any] = []

    def capture(_config: Any, **kwargs: Any) -> bool:
        changes.append((kwargs.get("new_owner"), kwargs.get("new_reviewer")))
        return True

    patches = [
        mock.patch.object(supervisor, "persist_task_reassignment", capture),
        mock.patch.object(supervisor, "write_activity_log", lambda *a, **k: None),
        mock.patch.dict(
            config, {"paths": {"status_file": "s.json", "activity_log": "a.jsonl"}}, clear=False
        ),
    ]
    if not pin_author:
        patches.append(
            mock.patch.object(supervisor, "task_review_source_identity", lambda *a: ReviewSourceIdentity())
        )
        patches.append(mock.patch.object(supervisor, "review_source_block_reason", lambda *a: None))
    for patch in patches:
        patch.start()
    try:
        supervisor.normalize_task_assignment_integrity(
            config, {}, {"tasks": [dict(task)]}, dict(task)
        )
        supervisor.normalize_mainline_task_assignment(config, dict(task))
    finally:
        mock.patch.stopall()
    return changes


@pytest.mark.parametrize(
    "task",
    [
        _submitted_task(),
        # A reopen puts the task back in progress while the submission evidence
        # stays on the record. The author is still the author.
        _submitted_task(id="T-SUB3", status="in_progress"),
    ],
)
def test_repair_does_not_launder_a_submitted_codex_author_into_a_self_review(
    task: dict[str, Any]
) -> None:
    """The two-step laundering the widened owner rule would otherwise enable.

    Step one: the new policy makes the Codex *owner* ineligible, so the repair
    pass reassigns the owner to Claude. Step two: the reviewer search excludes
    the new owner's pool -- claude_main -- which leaves the original author's own
    Codex pool eligible, and the reviewer rule says reviews are Codex. The branch
    is then reviewed by the account that wrote it, arrived at by obeying the
    policy at every step.
    """
    config = build_config()
    assert supervisor.task_review_source_identity(config, dict(task)).pins_owner("Codex")

    # The defect, reproduced: unpin the author and the owner is rewritten off the
    # account that actually wrote the branch.
    unpinned = _run_repair(config, task, pin_author=False)
    assert unpinned and all(owner != "Codex" for owner, _ in unpinned), unpinned

    # Pinned, nothing is written at all: the task waits, with the problem still
    # visible as an assignment issue rather than resolved by changing hands.
    assert _run_repair(config, task, pin_author=True) == []
    assert any(
        issue.startswith("owner_unavailable:")
        for issue in supervisor.task_assignment_integrity_issues(config, {}, dict(task))
    )


def test_a_frozen_owner_is_not_rewritten_even_when_its_lane_is_disabled() -> None:
    """The pin does not only answer policy reasons.

    A `review_approved` task is already exempt from the provider policy, so the
    route into it is a different one: disable the author's agent and the ordinary
    "actor unavailable" repair rewrites the frozen owner, evicting it from
    finalizing its own approved head. The freeze has to hold against any reason,
    not only against the new one.
    """
    config = build_config()
    config["agents"]["codex"]["enabled"] = False
    task = _submitted_task(id="T-SUB2", status="review_approved", approved_head="f" * 40)

    unpinned = _run_repair(config, task, pin_author=False)
    assert unpinned and all(owner != "Codex" for owner, _ in unpinned), unpinned
    assert _run_repair(config, task, pin_author=True) == []


def test_the_authors_pool_stays_excluded_even_after_the_owner_already_moved() -> None:
    """Defence in depth for a record whose owner is no longer its author.

    Pinning the owner cannot help once the owner has already been moved -- by an
    operator, or by a pass that ran before this rule existed. The submission
    still names who wrote the branch, so the reviewer search excludes that
    account's *pool* rather than just the author's name: an alias over the same
    credential is the same reviewer wearing a different label, which is the same
    hole account-pool independence exists to close. Here `Codex3` shares
    `codex_bjoe` with the author and is offered first.
    """
    config = _pool_alias_config()
    author_pool = supervisor.agent_account_pool_id(config, "Codex")
    assert supervisor.agent_account_pool_id(config, "Codex3") == author_pool
    laundered = _submitted_task(
        id="T-LAUNDERED",
        owner="Claude",
        reviewer="Antigravity",
        review_submission=_source_submission(config, "T-LAUNDERED", "Codex", submitted_by="Codex"),
    )
    source = supervisor.task_review_source_identity(config, laundered)
    assert source.error is None and author_pool in source.pools
    assert not source.pins_owner("Claude")
    # The reviewer is policy-excluded, so the repair pass does have a reviewer to
    # replace, and every Codex lane is a legitimate answer to the reviewer rule.
    with mock.patch.object(
        supervisor,
        "get_agent_reassignment_candidates",
        lambda *a, **k: ["Antigravity", "Claude", "Codex3", "Codex2"],
    ):
        changes = _run_repair(config, laundered, pin_author=True)
    assert changes, "the excluded reviewer should have been replaced"
    for _, new_reviewer in changes:
        assert new_reviewer == "Codex2", changes
        assert supervisor.agent_account_pool_id(config, new_reviewer) != author_pool, changes


def test_an_unsubmitted_task_is_still_repaired_normally() -> None:
    """The pin is about submitted work, not a blanket freeze on the owner field."""
    config = build_config()
    live = {
        "id": "T-LIVE",
        "status": "in_progress",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Codex",
        "reviewer": "Codex2",
    }
    assert not supervisor.task_review_source_identity(config, live).submitted
    assert (
        worker_failure_policy.first_viable_agent(
            config,
            OWNER_POOL,
            exclude={"Codex"},
            task=live,
            balance_load=False,
            role=ROLE_OWNER,
        )
        == "Antigravity"
    )


# --------------------------------------------------------------------------- #
# Queued pre-launch recheck
# --------------------------------------------------------------------------- #


def _review_event(target: str = "Antigravity") -> dict[str, Any]:
    return {
        "event_id": "evt-1",
        "task_id": "T-1",
        "target_agent": target.lower(),
        "target_display_name": target,
        "reason": REASON_REVIEW_READY,
    }


def test_queued_event_is_skipped_when_the_policy_stopped_permitting_it() -> None:
    """A queue event carries a decision made earlier; nothing else re-asks.

    Once `process_queue` launches, the worker is running -- there is no later
    gate. So a policy that changed while the event waited has to be re-read here
    or the excluded lane simply runs.
    """
    config = build_config()
    task_map = {"T-1": {"id": "T-1", "status": "review", "task_class": "implementation"}}
    message = dispatch_engine.stale_dispatch_skip_message(config, _review_event(), task_map)
    assert message is not None
    assert "role/provider policy no longer permits" in message


def test_queued_event_for_the_permitted_lane_is_not_skipped_by_the_policy() -> None:
    config = build_config()
    task_map = {"T-1": {"id": "T-1", "status": "review", "task_class": "implementation"}}
    message = dispatch_engine.stale_dispatch_skip_message(
        config, _review_event("Codex"), task_map
    )
    assert message is None or "role/provider policy" not in message


def test_queued_event_is_untouched_by_this_check_when_no_policy_is_configured() -> None:
    config = build_config(policy=None)
    task_map = {"T-1": {"id": "T-1", "status": "review", "task_class": "implementation"}}
    message = dispatch_engine.stale_dispatch_skip_message(config, _review_event(), task_map)
    assert message is None or "role/provider policy" not in message


# --------------------------------------------------------------------------- #
# The canonical CLI reads the same policy the dispatcher does
# --------------------------------------------------------------------------- #


def _cli_state() -> dict[str, Any]:
    return {
        "agents": [
            {"name": name, "capability_lane": [], "status": "idle", "current_task_ids": []}
            for name in ("Codex", "Codex2", "Claude", "Antigravity")
        ],
        "tasks": [],
        "handoffs": [],
        "blockers": [],
        "workload": {},
        "workload_summary": {},
    }


def _cli_patches(ai_status: Any, config: dict[str, Any]) -> list[Any]:
    return [
        mock.patch.dict("os.environ", {"AI_NAME": "Codex"}, clear=False),
        mock.patch.object(
            ai_status,
            "configured_agent_names",
            return_value={"Codex", "Codex2", "Claude", "Antigravity"},
        ),
        mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
    ]


def test_assign_refuses_a_reviewer_the_dispatcher_would_never_wake() -> None:
    """The board must not be able to record what the dispatcher cannot act on.

    An assignment the policy excludes is not merely ineffective: the task sits
    looking owned while the supervisor's reconcile pass fights the record every
    tick, so the CLI rejects it up front instead.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "implementation"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-001", "Claude", "Antigravity"])
        finally:
            mock.patch.stopall()
    assert "reviewer Antigravity" in str(excinfo.value)
    # Nothing was written: the refusal happens before any task mutation.
    assert state["tasks"] == []


def test_assign_refuses_codex_as_owner_of_implementation_class_work() -> None:
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "implementation"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-002", "Codex", "Codex2"])
        finally:
            mock.patch.stopall()
    assert "owner Codex" in str(excinfo.value)
    assert state["tasks"] == []


def test_assign_accepts_the_shape_the_policy_is_written_for() -> None:
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "implementation"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            ai_status.command_assign(state, ["ODP-X-003", "Claude", "Codex"])
        finally:
            mock.patch.stopall()
    assert [task["id"] for task in state["tasks"]] == ["ODP-X-003"]
    assert state["tasks"][0]["owner"] == "Claude"
    assert state["tasks"][0]["reviewer"] == "Codex"


def test_assign_refuses_codex_as_owner_of_integration_class_work_too() -> None:
    """The CLI reads the widened rule, not the class list it used to carry."""
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "integration"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-004", "Codex", "Codex2"])
        finally:
            mock.patch.stopall()
    assert "owner Codex" in str(excinfo.value)
    assert state["tasks"] == []


def test_assign_refuses_codex_as_owner_of_work_with_no_task_class() -> None:
    """An operator who omits TASK_CLASS does not thereby unlock a Codex owner."""
    import ai_status

    config = build_config()
    state = _cli_state()
    environ = {key: value for key, value in os.environ.items() if key != "TASK_CLASS"}
    with mock.patch.dict("os.environ", environ, clear=True):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-006", "Codex", "Codex2"])
        finally:
            mock.patch.stopall()
    assert "owner Codex" in str(excinfo.value)
    assert state["tasks"] == []


def test_approve_refuses_a_reviewer_the_policy_excludes() -> None:
    """Approval, not dispatch, is what the merge gate trusts.

    A reviewer the policy excludes could otherwise land the approval by hand and
    the freeze would carry it forward as though the review had been independent.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    state["tasks"] = [
        {
            "id": "ODP-X-005",
            "status": "review",
            "task_class": "implementation",
            "owner": "Codex",
            "reviewer": "Antigravity",
        }
    ]
    with (
        mock.patch.dict("os.environ", {"AI_NAME": "Antigravity"}, clear=False),
        mock.patch.object(
            ai_status, "configured_agent_names", return_value={"Codex", "Antigravity"}
        ),
        mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
        mock.patch.object(
            ai_status,
            "resolve_task_sha",
            side_effect=AssertionError("must refuse before touching the branch head"),
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            ai_status.command_approve(state, ["ODP-X-005", "looks good"])
    assert "role/provider" in str(excinfo.value)
    assert state["tasks"][0]["status"] == "review"


def test_assign_refuses_two_aliases_of_one_account_as_owner_and_reviewer() -> None:
    """`owner == reviewer` compares strings; accounts are what independence is about.

    `Codex`/`Codex3` and `Antigravity2..7` are logical names over one credential.
    Comparing the names lets the same account be written onto both sides of its
    own review, which is the whole thing account-pool independence exists to
    stop -- and the dispatcher has always resolved it through the pool resolver,
    so the CLI reads the same leaf rather than a second answer.
    """
    import ai_status

    config = _pool_alias_config()
    state = _cli_state()
    state["agents"].append(
        {"name": "Codex3", "capability_lane": [], "status": "idle", "current_task_ids": []}
    )
    with mock.patch.dict("os.environ", {"TASK_CLASS": "verification"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        mock.patch.object(
            ai_status,
            "configured_agent_names",
            return_value={"Codex", "Codex2", "Codex3", "Claude", "Antigravity"},
        ).start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-007", "Codex", "Codex3"])
        finally:
            mock.patch.stopall()
    assert "account pool codex_bjoe" in str(excinfo.value)
    assert state["tasks"] == []


def test_approve_refuses_an_alias_of_the_owners_own_account() -> None:
    """The same resolver guards the signature, not only the assignment.

    An assignment written before this rule existed -- or by a path that does not
    go through `assign` -- still ends at `approve`, and the approval is what the
    merge gate reads.
    """
    import ai_status

    config = _pool_alias_config()
    state = _cli_state()
    state["tasks"] = [
        {
            "id": "ODP-X-008",
            "status": "review",
            "task_class": "verification",
            "owner": "Codex",
            "reviewer": "Codex3",
        }
    ]
    with (
        mock.patch.dict("os.environ", {"AI_NAME": "Codex3"}, clear=False),
        mock.patch.object(
            ai_status, "configured_agent_names", return_value={"Codex", "Codex3"}
        ),
        mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
        mock.patch.object(
            ai_status,
            "resolve_task_sha",
            side_effect=AssertionError("must refuse before touching the branch head"),
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            ai_status.command_approve(state, ["ODP-X-008", "looks good"])
    assert "account pool codex_bjoe" in str(excinfo.value)
    assert state["tasks"][0]["status"] == "review"


def test_assign_judges_the_record_it_is_about_to_write_not_the_one_it_replaces() -> None:
    """A stored task_class must not be what a re-class is measured against.

    The invocation carries `TASK_CLASS`, so the assignment lands on that class.
    Judging the *old* record would either wave through an owner the new class
    forbids, or reject one the new class permits -- in both directions the answer
    describes a task that will not exist a line later.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    state["tasks"] = [
        {
            "id": "ODP-X-009",
            "status": "todo",
            "priority": "P2",
            "task_class": "implementation",
            "owner": "Claude",
            "reviewer": "Codex",
        }
    ]
    with mock.patch.dict("os.environ", {"TASK_CLASS": "rollout"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit) as excinfo:
                ai_status.command_assign(state, ["ODP-X-009", "Codex", "Codex2"])
        finally:
            mock.patch.stopall()
    assert "owner Codex" in str(excinfo.value)
    # Untouched: the refusal happens before the record is rewritten.
    assert state["tasks"][0]["owner"] == "Claude"
    assert state["tasks"][0]["task_class"] == "implementation"


def test_assign_reads_the_gate_this_invocation_declares_on_a_brand_new_task() -> None:
    """A gate being created is a gate. There is no stored record to read it from.

    `assign` is how a non-dispatchable or human-gated task first comes into
    existence, and at that moment the only place its shape is stated is the
    metadata of the very call being judged. Judging the stored task -- which is
    `None` here -- would reject the operator creating the gate, for the reason
    that no automatic lane may take it.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict(
        "os.environ",
        {"TASK_CLASS": "rollout", "TASK_METADATA_JSON": json.dumps({"non_dispatchable": True})},
        clear=False,
    ):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            ai_status.command_assign(state, ["ODP-X-014", "Codex", "Codex2", "gated rollout"])
        finally:
            mock.patch.stopall()
    assert state["tasks"][0]["owner"] == "Codex"
    assert state["tasks"][0]["non_dispatchable"] is True
    # ...while the same pair on an ordinary rollout task is refused.
    plain = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "rollout"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            with pytest.raises(SystemExit):
                ai_status.command_assign(plain, ["ODP-X-015", "Codex", "Codex2", "rollout"])
        finally:
            mock.patch.stopall()
    assert plain["tasks"] == []


def test_assign_installing_a_new_actor_on_a_frozen_task_is_still_judged() -> None:
    """A pinned head is not a hole the policy cannot see into.

    The exemption exists so a policy change does not evict the owner that already
    produced an approved head. Read as a property of the *task*, it would also
    mean that an approved task is the one place any lane can be installed --
    the exemption becoming the easiest way past the rule it protects.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    frozen = {
        "id": "ODP-X-010",
        "status": "review_approved",
        "priority": "P2",
        "task_class": "implementation",
        "owner": "Claude",
        "reviewer": "Codex",
        "approved_head": "a" * 40,
    }
    state["tasks"] = [dict(frozen)]
    for patch in _cli_patches(ai_status, config):
        patch.start()
    try:
        with pytest.raises(SystemExit) as excinfo:
            ai_status.command_assign(state, ["ODP-X-010", "Claude", "Antigravity"])
    finally:
        mock.patch.stopall()
    assert "reviewer Antigravity" in str(excinfo.value)
    assert state["tasks"][0]["reviewer"] == "Codex"


def test_assign_re_recording_a_frozen_tasks_own_actors_preserves_history() -> None:
    """Rewriting the record with the names already on it is not a new grant.

    Metadata-only updates go through the same command. If preserving the actors
    were judged as though it granted them, an approved task authored before the
    policy existed could never have its title or dependencies corrected again.
    """
    import ai_status

    config = build_config()
    state = _cli_state()
    state["tasks"] = [
        {
            "id": "ODP-X-011",
            "status": "review_approved",
            "priority": "P2",
            "task_class": "implementation",
            # Authored by Codex before the policy existed; the record stands.
            "owner": "Codex",
            "reviewer": "Antigravity",
            "approved_head": "b" * 40,
        }
    ]
    for patch in _cli_patches(ai_status, config):
        patch.start()
    try:
        ai_status.command_assign(state, ["ODP-X-011", "Codex", "Antigravity", "new title"])
    finally:
        mock.patch.stopall()
    assert state["tasks"][0]["owner"] == "Codex"
    assert state["tasks"][0]["reviewer"] == "Antigravity"


@pytest.mark.parametrize(
    ("task_id", "owner", "reviewer", "task_overrides"),
    [
        # A human gate: the owner has no provider identity at all, so judging it
        # against a provider policy fails closed on every operator who writes it.
        ("ODP-X-012", "Human/Ops", "Claude", {"task_class": "human_gate"}),
        # Non-dispatchable work is not an automatic lane either.
        ("ODP-X-013", "Codex", "Codex2", {"non_dispatchable": True}),
    ],
)
def test_assign_does_not_apply_the_policy_to_work_no_automatic_lane_may_take(
    task_id: str, owner: str, reviewer: str, task_overrides: dict[str, Any]
) -> None:
    """The policy decides which provider gets automated work, not who signs a gate."""
    import ai_status

    config = build_config()
    state = _cli_state()
    state["agents"].append(
        {"name": "Human/Ops", "capability_lane": [], "status": "idle", "current_task_ids": []}
    )
    state["tasks"] = [
        {
            "id": task_id,
            "status": "todo",
            "priority": "P2",
            "owner": owner,
            "reviewer": reviewer,
            **task_overrides,
        }
    ]
    for patch in _cli_patches(ai_status, config):
        patch.start()
    mock.patch.object(
        ai_status,
        "configured_agent_names",
        return_value={"Codex", "Codex2", "Claude", "Antigravity", "Human/Ops"},
    ).start()
    try:
        ai_status.command_assign(state, [task_id, owner, reviewer, "still fine"])
    finally:
        mock.patch.stopall()
    assert state["tasks"][0]["owner"] == owner
    assert state["tasks"][0]["reviewer"] == reviewer


# --------------------------------------------------------------------------- #
# Codex adapter: exact model and exact reasoning effort
# --------------------------------------------------------------------------- #


def _codex_command(config: dict[str, Any], agent_id: str) -> list[str]:
    """The argv `codex exec` would be launched with, captured before spawning."""
    adapter = CodexAdapter(config=config, provider_capabilities={})
    request = DeliveryRequest(
        agent_id=agent_id,
        provider="codex",
        delivery_mode="codex",
        message="wake up",
        task_id="T-1",
    )
    captured: dict[str, Any] = {}

    # Patched on the class, so the bound `self` still arrives as argument one.
    def fake_spawn(_self: CodexAdapter, _request: DeliveryRequest, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return kwargs

    with (
        mock.patch.object(CodexAdapter, "spawn_cli_delivery", fake_spawn),
        mock.patch("adapters.codex.command_exists", lambda value: value),
    ):
        adapter.deliver(request)
    return list(captured["command"])


def test_codex_command_carries_the_configured_model_and_ultra_effort() -> None:
    """Effort travels as a config key, next to the model, in one argv."""
    command = _codex_command(build_config(), "codex")
    assert "-c" in command
    assert 'model_reasoning_effort="ultra"' in command
    effort_index = command.index('model_reasoning_effort="ultra"')
    assert command[effort_index - 1] == "-c"
    assert command[command.index("--model") + 1] == "gpt-6-astra"
    # `ask_for_approval` and the sandbox mode are untouched by this change.
    assert 'ask_for_approval="never"' in command
    assert command[command.index("-s") + 1] == "workspace-write"


@pytest.mark.parametrize(
    "agent_id", ["codex", "codex2", "codex_bjoe_slot_1", "codex_lupin_slot_1"]
)
def test_both_codex_pools_and_their_physical_slots_inherit_model_and_effort(
    agent_id: str,
) -> None:
    """One provider setting, four launch identities, one resolved configuration.

    Two account pools and their dispatch slots are separate agents, and each is
    a separate place a launch could have picked up its own model. They all
    resolve `providers.codex`, so a per-lane override cannot silently put one of
    them back on an older model at a lower effort.
    """
    command = _codex_command(build_config(), agent_id)
    assert command[command.index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="ultra"' in command


def test_effort_is_omitted_entirely_when_unset() -> None:
    """Pre-existing configurations keep sending exactly what they sent before."""
    config = build_config()
    del config["providers"]["codex"]["codex"]["model_reasoning_effort"]
    command = _codex_command(config, "codex")
    assert not any(arg.startswith("model_reasoning_effort") for arg in command)
    assert command[command.index("--model") + 1] == "gpt-6-astra"


def test_invalid_effort_fails_the_delivery_instead_of_running_at_a_default() -> None:
    """A misspelled level must not become "whatever the CLI defaults to".

    Passing it through would run the review at an unknown effort while the
    receipt still named the configured one, which is the failure this check
    exists to prevent -- so the dispatch fails, with the reason.
    """
    config = build_config()
    config["providers"]["codex"]["codex"]["model_reasoning_effort"] = "maximum"
    adapter = CodexAdapter(config=config, provider_capabilities={})
    request = DeliveryRequest(
        agent_id="codex",
        provider="codex",
        delivery_mode="codex",
        message="wake up",
        task_id="T-1",
    )
    with (
        mock.patch.object(
            CodexAdapter,
            "spawn_cli_delivery",
            lambda *args, **kwargs: pytest.fail("delivery must not spawn on invalid effort"),
        ),
        mock.patch("adapters.codex.command_exists", lambda value: value),
    ):
        result = adapter.deliver(request)
    assert result.ok is False
    assert "model_reasoning_effort" in str(result.error)
    assert "maximum" in str(result.error)


# --------------------------------------------------------------------------- #
# Reopen Finding Counterexamples (F2, F2/F5, F6)
# --------------------------------------------------------------------------- #


def test_reassign_unavailable_reviewers_excludes_submitted_author_and_pool() -> None:
    """A rewritten owner must not allow the submitted Codex author to be picked as reviewer.

    Counterexample for F2: Task submitted by Codex whose owner is changed to Claude.
    When reviewer Codex2 becomes unavailable, reassign_unavailable_reviewers must NOT
    pick Codex (or another agent in the author's pool) as the replacement reviewer.
    """
    config = build_config()
    config["account_pools"]["codex_lupin"]["state"] = "paused"
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    status: dict[str, Any] = {
        "tasks": [
            {
                "id": "T-AUTH-POOL",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Codex2",
                "task_class": "implementation",
                "review_submission": _source_submission(
                    config, "T-AUTH-POOL", "Codex", submitted_by="Codex", pr_number=100, branch="task/T-AUTH-POOL"
                ),
            }
        ]
    }
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as mock_persist:
        changed = dispatch_engine.reassign_unavailable_reviewers(config, state, status)
    # Only Codex and Codex2 exist in the default test config. Codex shares the author pool
    # with submitted_by (Codex), so no independent reviewer is available and review must wait.
    assert changed is False
    mock_persist.assert_not_called()


def test_reassign_unavailable_reviewers_picks_independent_pool_over_author_pool() -> None:
    """When an independent third Codex pool exists, failover picks it over the author pool."""
    config = build_config()
    config["account_pools"]["codex_lupin"]["state"] = "paused"
    config["account_pools"]["codex_charlie"] = {"enabled": True, "max_concurrent": 2}
    config["agents"]["codex3"] = {
        "display_name": "Codex3",
        "account_pool": "codex_charlie",
        "provider": "codex",
        "adapter": "codex",
    }
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    status: dict[str, Any] = {
        "tasks": [
            {
                "id": "T-AUTH-POOL-2",
                "status": "review",
                "owner": "Claude",
                "reviewer": "Codex2",
                "task_class": "implementation",
                "review_submission": _source_submission(
                    config, "T-AUTH-POOL-2", "Codex", submitted_by="Codex", pr_number=100, branch="task/T-AUTH-POOL-2"
                ),
            }
        ]
    }
    with (
        mock.patch("supervisor.persist_task_reassignment", return_value=True),
        mock.patch("supervisor.write_activity_log"),
    ):
        changed = dispatch_engine.reassign_unavailable_reviewers(config, state, status)
    assert changed is True
    # Must be reassigned to Codex3 (independent pool), NEVER Codex (author pool).
    assert status["tasks"][0]["reviewer"] == "Codex3"


def test_worker_failure_reviewer_fallback_excludes_submitted_author_and_pool() -> None:
    """Worker failure reviewer fallback must exclude submitted author and author's pool.

    Counterexample for F2: When owner Claude fails and reviewer fallback is evaluated,
    it must not select the submitted Codex author as reviewer.
    """
    config = build_config()
    state: dict[str, Any] = {}
    worker: dict[str, Any] = {
        "task_id": "T-FAIL-AUTH",
        "agent_id": "claude",
        "retry_count": 2,
    }
    status: dict[str, Any] = {
        "tasks": [
            {
                "id": "T-FAIL-AUTH",
                "status": "todo",
                "owner": "Claude",
                "reviewer": "Codex",  # invalid: same pool as author
                "task_class": "implementation",
                "review_submission": _source_submission(
                    config, "T-FAIL-AUTH", "Codex", submitted_by="Codex", pr_number=101
                ),
            }
        ]
    }
    with (
        mock.patch("supervisor.load_status", return_value=status),
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as mock_persist,
        mock.patch("supervisor.write_activity_log"),
    ):
        reassigned = worker_failure_policy.maybe_reassign_task_after_worker_failure(
            config, state, worker, "terminal error", terminal=True
        )
    assert reassigned == "Claude2"
    # Reviewer must have been set to Codex2 (independent pool), not Codex (author pool).
    assert mock_persist.call_args.kwargs["new_reviewer"] == "Codex2"


def test_reassign_unavailable_reviewers_on_frozen_task_enforces_candidate_policy() -> None:
    """When an owner on a frozen task is paused, failover must enforce role policy for candidates.

    Counterexample for F2/F5: The recorded actor is exempt, but candidate selection must
    filter out ineligible actors (Codex) and select eligible actors (Antigravity).
    """
    config = build_config()
    config["account_pools"]["claude_main"]["state"] = "paused"
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    status: dict[str, Any] = {
        "tasks": [
            {
                "id": "T-FROZEN-REASSIGN",
                "status": "review_approved",
                "owner": "Claude",
                "reviewer": "Codex",
                "task_class": "implementation",
                "approved_head": "abc1234",
            }
        ]
    }
    with (
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as mock_persist,
        mock.patch("supervisor.write_activity_log"),
    ):
        changed = dispatch_engine.reassign_unavailable_reviewers(config, state, status)
    assert changed is True
    # Ineligible Codex must not be chosen; eligible Antigravity must be selected.
    assert mock_persist.call_args.kwargs["new_owner"] == "Antigravity"


def test_worker_failure_on_frozen_task_enforces_candidate_policy() -> None:
    """When an owner worker fails on a frozen task, failover must enforce role policy for candidates.

    Counterexample for F2/F5: Recovery selects an eligible owner (Claude2), not an ineligible one.
    """
    config = build_config()
    submission = _source_submission(config, "T-FROZEN-FAIL", "Claude", submitted_by="Claude")
    state: dict[str, Any] = {}
    worker: dict[str, Any] = {
        "task_id": "T-FROZEN-FAIL",
        "agent_id": "claude",
        "retry_count": 3,
    }
    status: dict[str, Any] = {
        "tasks": [
            {
                "id": "T-FROZEN-FAIL",
                "status": "review_approved",
                "owner": "Claude",
                "reviewer": "Codex",
                "task_class": "implementation",
                "approved_head": submission["source_head"],
                # An approved head always carries the submission it approved;
                # without it the carried reviewer could not be re-validated.
                "review_submission": submission,
            }
        ]
    }
    with (
        mock.patch("supervisor.load_status", return_value=status),
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as mock_persist,
        mock.patch("supervisor.write_activity_log"),
    ):
        reassigned = worker_failure_policy.maybe_reassign_task_after_worker_failure(
            config, state, worker, "terminal error", terminal=True
        )
    assert reassigned == "Claude2"
    assert mock_persist.call_args.kwargs["new_owner"] == "Claude2"


def test_frozen_closeout_does_not_exempt_new_actor_evaluation() -> None:
    """Evaluating a new actor on a frozen task must not inherit the frozen exemption.

    Counterexample for F2/F5: The recorded actor is exempt from policy eviction, but
    a new actor (e.g. Codex as owner) must fail the policy.
    """
    config = build_config()
    task = {
        "id": "T-FROZEN-EXEMPT",
        "status": "review_approved",
        "owner": "Claude",
        "reviewer": "Codex",
        "task_class": "implementation",
        "approved_head": "abc1234",
    }
    # Recorded owner Claude is exempt.
    assert role_provider_block_reason(config, "Claude", role="owner", task=task) is None
    # Recorded reviewer Codex is exempt.
    assert role_provider_block_reason(config, "Codex", role="reviewer", task=task) is None
    # A NEW actor being evaluated as owner (e.g. Codex) is NOT exempt and fails policy.
    new_actor_reason = role_provider_block_reason(config, "Codex", role="owner", task=task)
    assert new_actor_reason is not None
    assert "not permitted for role owner" in new_actor_reason


@pytest.mark.parametrize(
    "malformed_policy,expected_fragment",
    [
        ("not_a_dict", "must be an object"),
        (12345, "must be an object"),
        ([], "must be an object"),
        ({}, "cannot be empty"),
        ({"enabled": True}, "rules must be a list"),
        ({"enabled": True, "rules": []}, "rules must contain at least one rule"),
    ],
)
def test_malformed_non_dict_and_empty_policy_fails_closed(
    malformed_policy: Any, expected_fragment: str
) -> None:
    """Malformed non-dict and empty policy blocks must fail closed.

    Counterexample for F6: Malformed policy blocks must not return 'no policy' (fail-open).
    """
    config = build_config(policy=malformed_policy)
    error = role_provider_policy_error(config)
    assert error is not None
    assert expected_fragment in error
    # Must fail closed for any dispatch evaluation.
    block = role_provider_block_reason(config, "Claude", role="owner")
    assert block is not None
    assert "role_provider_policy is enabled but malformed" in block


def test_ghost_provider_without_config_entry_fails_closed_and_not_known() -> None:
    """An agent declaring an unconfigured provider has no resolvable identity and is not known.

    Counterexample for F6: An agent with provider: ghost_provider (not in config.providers)
    must not have its ghost provider accepted as known, and must fail closed.
    """
    config = build_config()
    config["agents"]["ghost_agent"] = {
        "display_name": "GhostAgent",
        "provider": "ghost_provider",
        "adapter": "ghost_adapter",
    }
    # ghost_provider must NOT be in known_provider_identity_ids
    from dispatch_policy import agent_provider_identity_ids, known_provider_identity_ids

    known = known_provider_identity_ids(config)
    assert "ghost_provider" not in known
    assert "ghost_agent" not in known
    assert "ghost_adapter" not in known

    # agent_provider_identity_ids must return empty set
    identities = agent_provider_identity_ids(config, "ghost_agent")
    assert identities == set()

    # Evaluating ghost_agent must fail closed
    reason = role_provider_block_reason(config, "ghost_agent", role="owner")
    assert reason is not None
    assert "has no resolvable provider identity" in reason

    # A policy rule naming ghost_provider must fail as unknown provider
    bad_policy = {
        "enabled": True,
        "rules": [
            {"roles": ["reviewer"], "providers": ["codex"]},
            {"roles": ["owner", "helper"], "providers": ["ghost_provider"]},
        ],
        "unclassified_owned_work": ["antigravity", "claude"],
    }
    bad_config = build_config(policy=bad_policy)
    error = role_provider_policy_error(bad_config)
    assert error is not None
    assert "ghost_provider" in error
    assert "names provider(s) not configured in this fleet" in error



# --------------------------------------------------------------------------- #
# Source-author review independence
# --------------------------------------------------------------------------- #


def _finalized_by_claude(config: dict[str, Any], *authors: str, **overrides: Any) -> dict[str, Any]:
    """The shape that motivated the rule, over a real synthetic Git range.

    Codex wrote the commits; Claude only merged a base and ran the finalizer, so
    Claude is the genuine owner and submitter. Codex is still the author.
    """
    task: dict[str, Any] = {
        "id": "T-SOURCE",
        "status": "review",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": _source_submission(
            config, "T-SOURCE", *(authors or ("Codex",)), submitted_by="Claude"
        ),
    }
    task.update(overrides)
    return task


def test_the_finalizer_does_not_stand_in_for_the_source_author() -> None:
    """Excluding only the submitter's pool frees the author's pool to review itself."""
    config = _pool_alias_config()
    task = _finalized_by_claude(config, "Codex")

    source = task_review_source_identity(config, task)
    assert source.error is None
    assert source.submitter == "Claude"
    assert source.source_authors == ("Codex",)
    assert source.pools == frozenset({"codex_bjoe", "claude_main"})

    assert "is itself a source author" in str(review_source_block_reason(config, task, "Codex"))
    # An alias over the author's credential is the same reviewer.
    assert "codex_bjoe" in str(review_source_block_reason(config, task, "Codex3"))
    # The finalizer's own pool stays excluded as well.
    assert review_source_block_reason(config, task, "Claude2") is not None
    assert review_source_block_reason(config, task, "Codex2") is None


def test_every_pool_of_a_multi_author_range_is_excluded() -> None:
    """No one author is "the" author; a complete independent reviewer still exists."""
    config = build_config()
    task = _finalized_by_claude(config, "Codex", "Antigravity")

    source = task_review_source_identity(config, task)
    assert source.pools == frozenset({"codex_bjoe", "agy_main", "claude_main"})
    for reviewer in ("Codex", "Antigravity", "Antigravity2", "Claude", "Claude2"):
        assert review_source_block_reason(config, task, reviewer) is not None, reviewer
    assert review_source_block_reason(config, task, "Codex2") is None

    # Reordering the range changes nothing: neither the first nor the last
    # author is privileged.
    reordered = _finalized_by_claude(config, "Antigravity", "Codex")
    assert task_review_source_identity(config, reordered).pools == source.pools
    assert review_source_block_reason(config, reordered, "Codex") is not None


@pytest.mark.parametrize(
    ("submission_patch", "expected"),
    [
        # Rewriting the recorded authors -- to a registered actor or not -- is
        # refused against the range's actual immutable Git authorship.
        ({"source_authors": ["Claude"]}, "are not the immutable Git authors ['Codex']"),
        ({"source_authors": ["Antigravity"]}, "are not the immutable Git authors ['Codex']"),
        ({"source_authors": ["Codex-Root"]}, "are not the immutable Git authors ['Codex']"),
        ({"source_authors": ["Codex", "Codex-Root"]}, "are not the immutable Git authors ['Codex']"),
        ({"source_authors": []}, "names no immutable source authors"),
        ({"source_authors": ["Codex", ""]}, "names no immutable source authors"),
        ({"source_authors": None}, "names no immutable source authors"),
        ({"submitted_by": ""}, "does not record its genuine submitter"),
        ({"submitted_by": "Claude-Root"}, "submitter 'Claude-Root' is not a registered agent"),
        ({"source_head": "f" * 40}, "not bound to the submitted head"),
        ({"source_head": None}, "not bound to the submitted head"),
        ({"source_base": "dev"}, "not bound to a validated base"),
        # A head and remote that agree with each other but name no commit of
        # this repository bind nothing.
        ({"source_head": "f" * 40, "remote_sha": "f" * 40}, "fails delivery identity"),
        ({"source_base": "c" * 40}, "is not in origin/dev history"),
    ],
)
def test_unresolvable_source_evidence_admits_no_reviewer(
    submission_patch: dict[str, Any], expected: str
) -> None:
    """Unknown, missing or unbound provenance fails closed for every reviewer.

    The range itself is real and Codex-authored, so each refusal is caused by
    the patched field alone. A submitter label that is not registered is not
    guessed to be an existing agent either.
    """
    config = build_config()
    task = _finalized_by_claude(config, "Codex")
    task["review_submission"].update(submission_patch)

    source = task_review_source_identity(config, task)
    assert source.submitted
    assert expected in str(source.error)
    for reviewer in ("Codex", "Codex2", "Antigravity"):
        assert review_source_block_reason(config, task, reviewer) == source.error


def test_review_without_submission_is_unresolved_and_unsubmitted_work_is_unconstrained() -> None:
    config = build_config()
    in_review = {"id": "T-NOSUB", "status": "review", "owner": "Claude", "reviewer": "Codex2"}
    source = task_review_source_identity(config, in_review)
    assert source.submitted and source.error
    assert source.pins_owner("Claude")
    assert review_source_block_reason(config, in_review, "Codex2") is not None

    todo = dict(in_review, status="todo")
    assert task_review_source_identity(config, todo) == ReviewSourceIdentity()
    assert review_source_block_reason(config, todo, "Codex2") is None


def test_normalizer_replaces_a_same_source_reviewer_and_keeps_the_finalizer() -> None:
    """Owner and reviewer are independent; reviewer and author are not."""
    config = _pool_alias_config()
    task = _finalized_by_claude(config, "Codex")
    assert supervisor.review_is_independent(config, "Claude", "Codex")
    issues = supervisor.task_assignment_integrity_issues(config, {}, dict(task))
    assert "reviewer_shares_source_account_pool" in issues

    with mock.patch.object(
        supervisor,
        "get_agent_reassignment_candidates",
        lambda *a, **k: ["Codex3", "Codex", "Codex2"],
    ):
        changes = _run_repair(config, task, pin_author=True)
    assert changes == [("Claude", "Codex2")], changes


def test_normalizer_does_not_repair_a_review_whose_source_is_unknown() -> None:
    config = build_config()
    task = _finalized_by_claude(config, "Codex-Root")
    issues = supervisor.task_assignment_integrity_issues(config, {}, dict(task))
    assert any(issue.startswith("review_source_unresolved:") for issue in issues), issues

    with mock.patch.object(
        supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex2", "Codex"]
    ):
        assert _run_repair(config, task, pin_author=True) == []


def test_a_reassigned_reviewer_does_not_inherit_an_old_source_author_approval() -> None:
    """Reopen, normalizer, restore: the approval is still the one its signer gave.

    Codex wrote the range and Claude finalized it. An approval an older build
    recorded -- signed by Codex, naming no signer -- survives the owner's
    reopen together with the submission. The normalizer then replaces Codex
    with independent Codex2, which is right for the next review, but Codex2
    has signed nothing: restoring has to refuse before any write. Codex2
    actually approving the same head, and the owner restoring that after a
    spurious reopen, still works.
    """
    import ai_status

    config = _pool_alias_config()
    task = _finalized_by_claude(config, "Codex", status="review_approved", review_notes_zh=["舊核准"])
    head = task["review_submission"]["remote_sha"]
    task.update(approved_head=head, last_approved_head=head)
    state = {"tasks": [task], "handoffs": [], "blockers": []}

    def as_actor(actor: str) -> ExitStack:
        # `_run_repair` stops every active patch when it returns, so each CLI
        # step enters its own.
        stack = ExitStack()
        for patch in (
            mock.patch.object(ai_status, "current_actor_validated", return_value=actor),
            mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
            mock.patch.object(ai_status, "resolve_task_sha", return_value=head),
            mock.patch.object(ai_status, "task_pr_ci_status", return_value=("OPEN", "success")),
            mock.patch.object(ai_status, "append_log"),
            mock.patch.object(ai_status, "sync_all"),
        ):
            stack.enter_context(patch)
        return stack

    with as_actor("Claude"):
        ai_status.command_reopen(state, [task["id"], "spurious downgrade"])
    assert task["status"] == "in_progress"
    assert task["last_approved_head"] == head and task["review_submission"]["remote_sha"] == head

    changes = _run_repair(config, task, pin_author=True)
    assert changes == [("Claude", "Codex2")], changes
    task["reviewer"] = changes[0][1]

    for signer, pattern in ((None, "does not record which reviewer signed it"), ("Codex", "is itself a source author")):
        if signer:
            task["last_approved_by"] = signer
        before = json.loads(json.dumps(state))
        with as_actor("Claude"), pytest.raises(SystemExit, match=pattern):
            ai_status.command_restore_approved(state, [task["id"], "restore"])
        assert state == before

    # The genuine path: Codex2 signs the head itself, then a spurious reopen
    # is restored on that signature.
    task.pop("last_approved_by")
    task["status"] = "review"
    with as_actor("Codex2"):
        ai_status.command_approve(state, [task["id"], "independent review"])
    assert task["last_approved_by"] == "Codex2"
    with as_actor("Claude"):
        ai_status.command_reopen(state, [task["id"], "spurious downgrade"])
    assert _run_repair(config, task, pin_author=True) == []
    with as_actor("Claude"):
        ai_status.command_restore_approved(state, [task["id"], "restore"])
    assert task["status"] == "review_approved"
    assert task["approved_head"] == head
    assert task["owner"] == "Claude"


def test_reviewer_failover_skips_the_author_pool_alias_for_a_finalized_branch() -> None:
    config = _pool_alias_config()
    config["account_pools"]["codex_lupin"]["state"] = "paused"
    config["account_pools"]["codex_charlie"] = {"enabled": True, "max_concurrent": 1}
    config["agents"]["codex4"] = {
        "display_name": "Codex4",
        "account_pool": "codex_charlie",
        "provider": "codex",
        "adapter": "codex",
    }
    task = _finalized_by_claude(config, "Codex", reviewer="Codex2")
    status: dict[str, Any] = {"tasks": [task]}
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    with (
        mock.patch("supervisor.persist_task_reassignment", return_value=True),
        mock.patch("supervisor.write_activity_log"),
    ):
        changed = dispatch_engine.reassign_unavailable_reviewers(config, state, status)
    assert changed is True
    assert status["tasks"][0]["reviewer"] == "Codex4"

    unknown = _finalized_by_claude(config, "Codex-Root", reviewer="Codex2")
    status = {"tasks": [unknown]}
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, status) is False
    persist.assert_not_called()


def test_worker_failure_reviewer_failover_excludes_every_source_pool_and_refuses_unknown() -> None:
    config = _pool_alias_config()
    config["account_pools"]["codex_charlie"] = {"enabled": True, "max_concurrent": 1}
    config["agents"]["codex4"] = {
        "display_name": "Codex4",
        "account_pool": "codex_charlie",
        "provider": "codex",
        "adapter": "codex",
    }
    worker = {"task_id": "T-SOURCE", "agent_id": "codex2", "retry_count": 2}
    for authors, expected in ((("Codex",), "Codex4"), (("Codex-Root",), None)):
        task = _finalized_by_claude(config, *authors, reviewer="Codex2")
        with (
            mock.patch("supervisor.load_status", return_value={"tasks": [task]}),
            mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
            mock.patch("supervisor.write_activity_log"),
            mock.patch.object(
                supervisor,
                "get_agent_reassignment_candidates",
                lambda *a, **k: ["Codex", "Codex3", "Codex4"],
            ),
        ):
            reassigned = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                config, {}, worker, "terminal error", terminal=True
            )
        assert reassigned == expected, authors
        if expected is None:
            persist.assert_not_called()
        else:
            assert persist.call_args.kwargs["new_owner"] == "Claude"
            assert persist.call_args.kwargs["new_reviewer"] == expected


def _sha_shadowed_submission(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """A genuine Codex-then-Claude range whose base SHA is also a ref name.

    `B` is the integration base, Codex writes `C` on it and Claude -- the
    finalizer -- writes `D` on top. A ref spelled `origin/<B>` is then pointed
    at `C`. Nothing recorded changes; the only question is whether `B` is read
    as the commit it names or as the branch that happens to share its spelling.
    """
    repo = _source_repo()
    repositories = config.setdefault("coordination", {}).setdefault("repositories", {})
    repositories.setdefault("pantheon", {})["local_path"] = str(repo)
    config.setdefault("branch_workflow", {})["dev_branch"] = "dev"
    base = _git(repo, "rev-parse", "dev")
    _git(repo, "checkout", "-q", "-b", "work/T-SOURCE-shadow", "dev")
    codex = _source_commit(repo, "T-SOURCE", "Codex", "T-SOURCE-shadow-codex")
    head = _source_commit(repo, "T-SOURCE", "Claude", "T-SOURCE-shadow-claude")
    _git(repo, "checkout", "-q", "dev")
    _git(repo, "update-ref", f"refs/remotes/origin/{base}", codex)
    submission = {
        "pr_number": 1221,
        "remote_sha": head,
        "source_head": head,
        "source_base": base,
        "source_authors": ["Codex", "Claude"],
        "submitted_by": "Claude",
    }
    return submission, {"repo": str(repo), "base": base, "codex": codex, "head": head}


def test_a_sha_named_ref_cannot_move_the_recorded_base_on_a_cold_cache() -> None:
    """The recorded base is the commit it names, not a branch of that spelling.

    Same genuine base, head, remote and Claude finalizer; the record only drops
    Codex from its authors. Nothing has asked about this range before, so no
    earlier genuine scan can have answered for it. Had `origin/<B>` been read
    as the base, the range would be `C..D`, its only author Claude, and the
    forged record would match it -- admitting Codex to review its own commit.
    """
    import dispatch_policy

    config = _pool_alias_config()
    genuine, shape = _sha_shadowed_submission(config)
    repo = Path(shape["repo"])
    assert _git(repo, "rev-parse", f"origin/{shape['base']}") == shape["codex"]
    assert not [key for key in dispatch_policy._IMMUTABLE_SOURCE_AUTHORS if key[0] == shape["repo"]]

    forged = {
        "id": "T-SOURCE",
        "status": "review",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": dict(genuine, source_authors=["Claude"]),
    }
    source = task_review_source_identity(config, forged)
    assert "are not the immutable Git authors ['Codex', 'Claude']" in str(source.error)
    assert not source.pools
    for reviewer in ("Codex", "Codex3", "Codex2"):
        assert review_source_block_reason(config, forged, reviewer) == source.error
    issues = supervisor.task_assignment_integrity_issues(config, {}, dict(forged))
    assert any(issue.startswith("review_source_unresolved:") for issue in issues), issues
    with mock.patch.object(
        supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex", "Codex3", "Codex2"]
    ):
        assert _run_repair(config, forged, pin_author=True) == []
    config["account_pools"]["codex_lupin"]["state"] = "paused"
    unavailable = dict(forged, reviewer="Codex2")
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [unavailable]}) is False
    persist.assert_not_called()
    config["account_pools"]["codex_lupin"]["state"] = "active"

    # The genuine record of the same range: every source pool is excluded and
    # an independent registered reviewer still passes.
    task = dict(forged, review_submission=genuine)
    assert task_review_source_identity(config, task).pools == frozenset({"codex_bjoe", "claude_main"})
    assert "is itself a source author" in str(review_source_block_reason(config, task, "Codex"))
    assert "codex_bjoe" in str(review_source_block_reason(config, task, "Codex3"))
    assert review_source_block_reason(config, task, "Codex2") is None


def test_authors_of_a_scan_that_read_another_range_are_neither_used_nor_cached() -> None:
    """The scan has to have read the recorded range before its authors count.

    The counterfactual reads the base through the SHA-named ref, as the
    resolver once did, with the real scanner over the real repository. The
    scan itself is clean and finds exactly the forged author -- only the range
    it reports gives it away, and that is refused before anything is cached.
    """
    import dispatch_policy

    config = _pool_alias_config()
    genuine, shape = _sha_shadowed_submission(config)
    forged = {
        "id": "T-SOURCE",
        "status": "review",
        "owner": "Claude",
        "reviewer": "Codex",
        "review_submission": dict(genuine, source_authors=["Claude"]),
    }
    real_scan = dispatch_policy._inspect_delivery_identity

    def through_the_shadow(repo_root: Path, **kwargs: Any) -> Any:
        return real_scan(repo_root, **{**kwargs, "base": f"origin/{kwargs['base']}"})

    with mock.patch.object(dispatch_policy, "_inspect_delivery_identity", through_the_shadow):
        shadowed = through_the_shadow(
            Path(shape["repo"]), task_id="T-SOURCE", base=shape["base"], head=shape["head"]
        )
        assert shadowed.errors == []
        assert shadowed.source_agents == ("Claude",)
        assert shadowed.base_sha == shape["codex"]
        source = task_review_source_identity(config, forged)
        assert "not the recorded range" in str(source.error)
        assert review_source_block_reason(config, forged, "Codex") == source.error
        assert not [key for key in dispatch_policy._IMMUTABLE_SOURCE_AUTHORS if key[0] == shape["repo"]]


def test_a_rewritten_source_record_is_refused_before_any_queue_selects_a_reviewer() -> None:
    """The queue boundary does not take `source_authors` on trust.

    Same real head, base and remote, genuine finalizer and owner; only the
    recorded authors are rewritten from the actual Codex to another registered
    actor. Read from the record, Codex would then look independent of every
    excluded pool. The normalizer, the unavailable-reviewer failover and the
    worker-failure failover all ask the one rule, which reads the range from
    Git, so none of them may select Codex -- or anyone -- against that record.
    """
    config = _pool_alias_config()
    genuine = _finalized_by_claude(config, "Codex")
    assert "reviewer_shares_source_account_pool" in supervisor.task_assignment_integrity_issues(
        config, {}, dict(genuine)
    )

    forged = json.loads(json.dumps(genuine))
    forged["review_submission"]["source_authors"] = ["Antigravity"]
    for field in ("source_head", "source_base", "remote_sha", "submitted_by"):
        assert forged["review_submission"][field] == genuine["review_submission"][field]
    source = task_review_source_identity(config, forged)
    assert "are not the immutable Git authors ['Codex']" in str(source.error)
    assert not source.pools
    for reviewer in ("Codex", "Codex3", "Codex2"):
        assert review_source_block_reason(config, forged, reviewer) == source.error

    issues = supervisor.task_assignment_integrity_issues(config, {}, dict(forged))
    assert any(issue.startswith("review_source_unresolved:") for issue in issues), issues
    with mock.patch.object(
        supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex", "Codex3", "Codex2"]
    ):
        assert _run_repair(config, forged, pin_author=True) == []

    config["account_pools"]["codex_lupin"]["state"] = "paused"
    unavailable = dict(forged, reviewer="Codex2")
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [unavailable]}) is False
    persist.assert_not_called()

    worker = {"task_id": "T-SOURCE", "agent_id": "codex2", "retry_count": 2}
    with (
        mock.patch("supervisor.load_status", return_value={"tasks": [unavailable]}),
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
        mock.patch("supervisor.write_activity_log"),
        mock.patch.object(
            supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex", "Codex3"]
        ),
    ):
        assert (
            worker_failure_policy.maybe_reassign_task_after_worker_failure(
                config, {}, worker, "terminal error", terminal=True
            )
            is None
        )
    persist.assert_not_called()


def _display_name_collision_config() -> dict[str, Any]:
    """A valid fleet in which `Codex` is one agent's id and another's display name.

    Adapters record `LLM-Agent` as the display name, so a commit signed `Codex`
    here may come from `writer`, which runs in `codex_lupin` -- the pool of the
    independently registered `Codex2`. Agent `codex` itself is `Primary`.
    """
    config = build_config()
    config["agents"]["codex"]["display_name"] = "Primary"
    config["agents"]["writer"] = {
        "display_name": "Codex",
        "provider": "codex",
        "adapter": "codex",
        "account_pool": "codex_lupin",
    }
    return config


def test_an_actor_named_by_one_agent_id_and_another_display_name_is_refused_before_any_queue() -> None:
    """An id match does not silently outrank a conflicting display-name match.

    Genuine range, head, base and remote; Codex wrote it and Claude finalized.
    Resolving `Codex` to agent `codex` alone would leave out `writer`'s pool,
    and Codex2 in that pool would then pass as independent. Both readings are
    registered, so the actor is ambiguous: no pool is chosen, every reviewer is
    refused, and neither the normalizer nor either failover selects one.
    """
    config = _display_name_collision_config()
    task = _finalized_by_claude(config, "Codex")
    for field in ("source_head", "source_base", "remote_sha"):
        assert task["review_submission"][field]
    assert task["review_submission"]["submitted_by"] == "Claude"

    source = task_review_source_identity(config, task)
    assert "source author 'Codex' is not a registered agent" in str(source.error)
    assert source.source_authors == ("Codex",)
    assert not source.pools
    for reviewer in ("Codex2", "Primary", "writer", "Codex", "Antigravity"):
        assert review_source_block_reason(config, task, reviewer) == source.error, reviewer

    issues = supervisor.task_assignment_integrity_issues(config, {}, dict(task))
    assert any(issue.startswith("review_source_unresolved:") for issue in issues), issues
    with mock.patch.object(
        supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex2", "Antigravity", "Primary"]
    ):
        assert _run_repair(config, task, pin_author=True) == []

    config["account_pools"]["codex_bjoe"]["state"] = "paused"
    unavailable = dict(task, reviewer="Primary")
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [unavailable]}) is False
    persist.assert_not_called()

    worker = {"task_id": "T-SOURCE", "agent_id": "codex", "retry_count": 2}
    with (
        mock.patch("supervisor.load_status", return_value={"tasks": [unavailable]}),
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
        mock.patch("supervisor.write_activity_log"),
        mock.patch.object(
            supervisor, "get_agent_reassignment_candidates", lambda *a, **k: ["Codex2", "Antigravity"]
        ),
    ):
        assert (
            worker_failure_policy.maybe_reassign_task_after_worker_failure(
                config, {}, worker, "terminal error", terminal=True
            )
            is None
        )
    persist.assert_not_called()


def test_unambiguous_ids_and_display_aliases_still_resolve_beside_a_collision() -> None:
    """Only the colliding label is refused; every unique spelling keeps its pool."""
    config = _display_name_collision_config()

    by_alias = _finalized_by_claude(config, "Primary")
    source = task_review_source_identity(config, by_alias)
    assert source.error is None
    assert source.pools == frozenset({"codex_bjoe", "claude_main"})
    assert review_source_block_reason(config, by_alias, "Codex2") is None
    assert "codex_bjoe" in str(review_source_block_reason(config, by_alias, "codex_bjoe_slot_1"))

    by_id = _finalized_by_claude(config, "writer")
    source = task_review_source_identity(config, by_id)
    assert source.error is None
    assert source.pools == frozenset({"codex_lupin", "claude_main"})
    assert "codex_lupin" in str(review_source_block_reason(config, by_id, "Codex2"))
    assert review_source_block_reason(config, by_id, "Primary") is None

    # Without the collision, the same `Codex` range has its one genuine pool.
    plain = build_config()
    task = _finalized_by_claude(plain, "Codex")
    assert task_review_source_identity(plain, task).pools == frozenset({"codex_bjoe", "claude_main"})
    assert review_source_block_reason(plain, task, "Codex2") is None


def _ambiguous_reviewer_candidate_config() -> dict[str, Any]:
    """The collision fleet with an unambiguous source and a colliding fallback.

    `Codex` is agent `codex`'s id (pool `codex_bjoe`) and `writer`'s display
    name (pool `codex_lupin`, the pool of source author Codex2). Codex4 is the
    current reviewer in its own pool; its configured fallback names `Codex`
    first and the genuinely independent Codex5 second.
    """
    config = _display_name_collision_config()
    config["account_pools"]["codex_charlie"] = {"enabled": True, "max_concurrent": 1}
    config["account_pools"]["codex_delta"] = {"enabled": True, "max_concurrent": 1}
    for agent_id, display, pool in (("codex4", "Codex4", "codex_charlie"), ("codex5", "Codex5", "codex_delta")):
        config["agents"][agent_id] = {
            "display_name": display,
            "provider": "codex",
            "adapter": "codex",
            "account_pool": pool,
        }
    config["worker_reassignment"] = {
        "enabled": True,
        "reviewer_fallbacks": {"Codex4": ["Codex", "Codex5"]},
        "owner_fallbacks": {"Claude": ["Claude2", "Antigravity"]},
        "review_churn": {"enabled": True, "reassign_after_reopens": 2},
    }
    return config


def test_failover_applies_the_common_rule_to_an_ambiguous_reviewer_candidate() -> None:
    """A reviewer candidate is admitted by the common rule, not a raw pool lookup.

    Genuine range, head, base and remote; Codex2 wrote it and Claude finalized,
    so the source is unambiguous and excludes `codex_lupin` and `claude_main`.
    The raw lookup resolves the fallback `Codex` to agent `codex` in
    `codex_bjoe` and admits it, yet the common rule refuses `Codex` because
    `writer` -- in the source pool -- answers to it too. Worker-failure
    failover, review churn and both normalizers must pass over it before
    anything is persisted and select the independent Codex5; with Codex5 gone
    from the fallbacks nothing is persisted at all.
    """
    config = _ambiguous_reviewer_candidate_config()
    task = _finalized_by_claude(config, "Codex2", reviewer="Codex4")
    source = task_review_source_identity(config, task)
    assert source.error is None
    assert source.pools == frozenset({"codex_lupin", "claude_main"})
    assert task["review_submission"]["submitted_by"] == "Claude"
    # The raw lookup alone would admit `Codex`; the common rule does not.
    assert supervisor.agent_account_pool_id(config, "Codex") == "codex_bjoe"
    assert "is not a registered agent" in str(review_source_block_reason(config, task, "Codex"))
    assert review_source_block_reason(config, task, "Codex5") is None

    def worker_failover(candidate_task: dict[str, Any]) -> tuple[Any, Any]:
        worker = {"task_id": "T-SOURCE", "agent_id": "codex4", "retry_count": 2}
        with (
            mock.patch("supervisor.load_status", return_value={"tasks": [candidate_task]}),
            mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
            mock.patch("supervisor.write_activity_log"),
        ):
            reassigned = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                config, {}, worker, "terminal error", terminal=True
            )
        return reassigned, persist

    reassigned, persist = worker_failover(dict(task))
    assert reassigned == "Codex5"
    assert persist.call_args.kwargs["new_owner"] == "Claude"
    assert persist.call_args.kwargs["new_reviewer"] == "Codex5"
    assert persist.call_args.kwargs["handoff_to"] == "Codex5"

    # Review churn rotates the owner; Codex4 is paused, so the reviewer moves
    # through the same configured fallbacks.
    config["account_pools"]["codex_charlie"]["state"] = "paused"
    churning = dict(task, status="in_progress", review_reopen_count=2)
    with (
        mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        assert supervisor.reassign_tasks_after_review_churn(config, {}, {"tasks": [churning]}) is True
    assert persist.call_args.kwargs["new_owner"] == "Antigravity"
    assert persist.call_args.kwargs["new_reviewer"] == "Codex5"

    # The unavailable-reviewer normalizer reaches the same answer.
    state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
    unavailable = dict(task)
    with (
        mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
        mock.patch("supervisor.write_activity_log"),
    ):
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [unavailable]}) is True
    assert persist.call_args.kwargs["new_reviewer"] == "Codex5"

    # With the ambiguous name the only candidate left, no path persists a reviewer.
    config["account_pools"]["codex_charlie"].pop("state")
    config["worker_reassignment"]["reviewer_fallbacks"] = {"Codex4": ["Codex"]}
    del config["agents"]["codex5"]
    reassigned, persist = worker_failover(dict(task))
    assert reassigned is None
    persist.assert_not_called()

    config["account_pools"]["codex_charlie"]["state"] = "paused"
    with (
        mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
        mock.patch.object(supervisor, "write_activity_log"),
    ):
        supervisor.reassign_tasks_after_review_churn(config, {}, {"tasks": [dict(churning)]})
    assert all(call.kwargs.get("new_reviewer") != "Codex" for call in persist.call_args_list)
    with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
        assert dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [dict(task)]}) is False
    persist.assert_not_called()
    assert worker_failure_policy.first_viable_agent(
        config, ["Codex"], set(), task=task, role="reviewer"
    ) is None
    assert not worker_failure_policy.has_configured_reassignment_candidates(
        config, ["Codex"], set(), task=task, role="reviewer"
    )


# --------------------------------------------------------------------------- #
# Historical source-author aliases
# --------------------------------------------------------------------------- #

#: The two literal labels the user confirmed are the existing Codex/codex_bjoe.
HISTORICAL_LABELS = ("Codex foreground", "Codex-foreground")


def _historical_alias_config() -> dict[str, Any]:
    """The pool-alias fleet with only agent `codex` declaring both labels.

    Codex4 is a second independent lane, so a failover that has to leave
    unavailable Codex2 still has a genuine target.
    """
    config = _pool_alias_config()
    config["agents"]["codex"]["source_aliases"] = list(HISTORICAL_LABELS)
    config["account_pools"]["codex_charlie"] = {"enabled": True, "max_concurrent": 1}
    config["agents"]["codex4"] = {
        "display_name": "Codex4",
        "account_pool": "codex_charlie",
        "provider": "codex",
        "adapter": "codex",
    }
    return config


def _scanned_authors(config: dict[str, Any], task: dict[str, Any]) -> tuple[str, ...]:
    """What the unchanged delivery scanner itself reads from the task's real range."""
    import dispatch_policy

    submission = task["review_submission"]
    scan = dispatch_policy._inspect_delivery_identity(
        Path(config["coordination"]["repositories"]["pantheon"]["local_path"]),
        task_id=task["id"],
        base=submission["source_base"],
        head=submission["source_head"],
    )
    assert scan.errors == []
    return scan.source_agents


def _alias_overlap(config: dict[str, Any]) -> None:
    config["agents"]["codex2"]["source_aliases"] = ["Codex foreground"]


def _alias_names_another_agent_id(config: dict[str, Any]) -> None:
    config["agents"]["codex_foreground"] = {
        "display_name": "Lane",
        "provider": "codex",
        "adapter": "codex",
        "account_pool": "codex_lupin",
    }


def _alias_names_another_display_name(config: dict[str, Any]) -> None:
    config["agents"]["codex2"]["display_name"] = "Codex foreground"


def _case_variant_alias_on_another_agent(config: dict[str, Any]) -> None:
    config["agents"]["codex2"]["source_aliases"] = ["CODEX FOREGROUND"]


def _alias_on_a_slot_as_well(config: dict[str, Any]) -> None:
    config["agents"]["codex_lupin_slot_1"]["source_aliases"] = ["Codex foreground"]


def _alias_on_a_slot_only(config: dict[str, Any]) -> None:
    del config["agents"]["codex"]["source_aliases"]
    config["agents"]["codex_bjoe_slot_1"]["source_aliases"] = ["Codex foreground"]


def _no_alias_declared(config: dict[str, Any]) -> None:
    del config["agents"]["codex"]["source_aliases"]


def _malformed_aliases(value: Any, agent: str = "codex") -> Any:
    def apply(config: dict[str, Any]) -> None:
        config["agents"][agent]["source_aliases"] = value

    return apply


#: Every catalog that cannot say who wrote a `Codex foreground` commit.
_UNRESOLVABLE_ALIAS_CATALOGS: dict[str, Any] = {
    "undeclared": _no_alias_declared,
    "overlap across logical agents": _alias_overlap,
    "alias is another agent's normalized id": _alias_names_another_agent_id,
    "alias is another agent's display name": _alias_names_another_display_name,
    "alias differs from another's by case": _case_variant_alias_on_another_agent,
    "slot declares it beside the logical agent": _alias_on_a_slot_as_well,
    "only a slot declares it": _alias_on_a_slot_only,
    "bare string": _malformed_aliases("Codex foreground"),
    "null": _malformed_aliases(None),
    "non-string item": _malformed_aliases(["Codex foreground", 1]),
    "blank item": _malformed_aliases(["Codex foreground", "  "]),
    "padded item": _malformed_aliases(["Codex foreground", " Codex-foreground"]),
    "another agent's list is malformed": _malformed_aliases("Codex foreground", agent="claude"),
}


class HistoricalSourceAliasTests(unittest.TestCase):
    """A declared historical label names its agent for source authorship only.

    Every range here is a real synthetic Git range read by the unchanged
    scanner. The reviewer paths are the original ones; what changes per case is
    the catalog and the literal label on the commits.
    """

    def test_each_literal_alias_resolves_to_the_existing_pool_and_stays_raw(self) -> None:
        config = _historical_alias_config()
        for label in HISTORICAL_LABELS:
            with self.subTest(label=label):
                task = _finalized_by_claude(config, label, reviewer="Codex2")
                before = json.loads(json.dumps(task))

                source = task_review_source_identity(config, task)

                assert source.error is None
                # The evidence keeps the label; only the answer is resolved.
                assert _scanned_authors(config, task) == (label,)
                assert source.source_authors == (label,)
                assert task["review_submission"]["source_authors"] == [label]
                assert source.submitter == "Claude"
                assert source.pools == frozenset({"codex_bjoe", "claude_main"})
                assert "codex" in source.agents
                assert task == before

                assert "is itself a source author" in str(review_source_block_reason(config, task, "Codex"))
                assert "codex_bjoe" in str(review_source_block_reason(config, task, "Codex3"))
                assert "codex_bjoe" in str(review_source_block_reason(config, task, "codex_bjoe_slot_1"))
                assert "claude_main" in str(review_source_block_reason(config, task, "Claude2"))
                assert review_source_block_reason(config, task, "Codex2") is None

    def test_canonical_and_multi_author_ranges_exclude_every_pool(self) -> None:
        config = _historical_alias_config()

        canonical = _finalized_by_claude(config, "Codex")
        assert task_review_source_identity(config, canonical).pools == frozenset({"codex_bjoe", "claude_main"})
        assert review_source_block_reason(config, canonical, "Codex2") is None

        authors = ("Codex foreground", "Codex", "Antigravity", "Codex-foreground")
        mixed = _finalized_by_claude(config, *authors)
        source = task_review_source_identity(config, mixed)
        assert source.error is None
        assert _scanned_authors(config, mixed) == authors
        assert source.source_authors == authors
        assert source.pools == frozenset({"codex_bjoe", "agy_main", "claude_main"})
        for reviewer in ("Codex", "Codex3", "Antigravity", "Antigravity2", "Claude", "Claude2"):
            assert review_source_block_reason(config, mixed, reviewer) is not None, reviewer
        assert review_source_block_reason(config, mixed, "Codex2") is None

        # A label beside a genuine Codex2 commit: no first author is privileged.
        for ordered in (("Codex foreground", "Codex2"), ("Codex2", "Codex-foreground")):
            both = _finalized_by_claude(config, *ordered)
            assert task_review_source_identity(config, both).pools == frozenset(
                {"codex_bjoe", "codex_lupin", "claude_main"}
            )
            for reviewer in ("Codex", "Codex2", "Codex3"):
                assert review_source_block_reason(config, both, reviewer) is not None, reviewer
            assert review_source_block_reason(config, both, "Codex4") is None

    def test_a_label_resembling_a_declared_alias_is_not_inferred(self) -> None:
        """Exact declared spellings only: no case, prefix, fuzzy or punctuation family."""
        config = _historical_alias_config()
        for label in (
            "codex foreground",
            "CODEX-FOREGROUND",
            "Codex_foreground",
            "Codex  foreground",
            "Codexforeground",
            "Codex foreground 2",
            "Codex-foreground-root",
            "Codex fore",
            "Codex-Root",
        ):
            with self.subTest(label=label):
                task = _finalized_by_claude(config, label)
                source = task_review_source_identity(config, task)
                assert f"source author {label!r} is not a registered agent" in str(source.error)
                assert source.source_authors == (label,)
                assert not source.pools
                for reviewer in ("Codex", "Codex2", "Antigravity"):
                    assert review_source_block_reason(config, task, reviewer) == source.error

    def test_an_unresolvable_catalog_admits_no_reviewer(self) -> None:
        for name, damage in _UNRESOLVABLE_ALIAS_CATALOGS.items():
            with self.subTest(catalog=name):
                config = _historical_alias_config()
                damage(config)
                task = _finalized_by_claude(config, "Codex foreground", reviewer="Codex2")
                source = task_review_source_identity(config, task)
                assert "source author 'Codex foreground' is not a registered agent" in str(source.error)
                assert not source.pools
                for reviewer in ("Codex", "Codex2", "Codex4", "Antigravity"):
                    assert review_source_block_reason(config, task, reviewer) == source.error, reviewer

    def test_an_alias_cannot_collide_with_a_canonical_author_either(self) -> None:
        """Another agent claiming `Codex` makes `Codex` two actors, by any spelling."""
        for claimed in ("Codex", "CODEX", "codex"):
            with self.subTest(claimed=claimed):
                config = _historical_alias_config()
                config["agents"]["codex2"]["source_aliases"] = [claimed]
                task = _finalized_by_claude(config, "Codex", reviewer="Codex2")
                source = task_review_source_identity(config, task)
                assert "source author 'Codex' is not a registered agent" in str(source.error)
                assert review_source_block_reason(config, task, "Codex2") == source.error

        # A malformed list says nothing reliable about any name, canonical ones included.
        config = _historical_alias_config()
        config["agents"]["claude"]["source_aliases"] = "Codex"
        task = _finalized_by_claude(config, "Codex", reviewer="Codex2")
        assert "source author 'Codex' is not a registered agent" in str(
            task_review_source_identity(config, task).error
        )

    def test_a_label_declared_by_one_other_agent_resolves_to_that_agent(self) -> None:
        """The answer comes from the catalog, not from the label's spelling."""
        config = _historical_alias_config()
        del config["agents"]["codex"]["source_aliases"]
        config["agents"]["antigravity"]["source_aliases"] = ["Codex foreground"]
        task = _finalized_by_claude(config, "Codex foreground")
        assert task_review_source_identity(config, task).pools == frozenset({"agy_main", "claude_main"})
        assert "agy_main" in str(review_source_block_reason(config, task, "Antigravity2"))
        assert review_source_block_reason(config, task, "Codex") is None

    def test_owner_submitter_and_reviewer_stay_strict(self) -> None:
        import dispatch_policy

        config = _historical_alias_config()
        for label in HISTORICAL_LABELS:
            with self.subTest(label=label):
                assert dispatch_policy._registered_actor_id(config, label) == ""
                assert dispatch_policy._registered_actor_pool(config, label) == ""
                assert dispatch_policy._registered_actor_id(config, label, source_author=True) == "codex"
                assert dispatch_policy._registered_actor_pool(config, label, source_author=True) == "codex_bjoe"

                as_owner = _finalized_by_claude(config, "Antigravity", owner=label)
                assert f"owner {label!r} is not a registered agent" in str(
                    task_review_source_identity(config, as_owner).error
                )
                as_submitter = _finalized_by_claude(config, "Antigravity")
                as_submitter["review_submission"]["submitted_by"] = label
                assert f"submitter {label!r} is not a registered agent" in str(
                    task_review_source_identity(config, as_submitter).error
                )
                # Independent work, so only the reviewer's own registration can refuse.
                independent = _finalized_by_claude(config, "Antigravity")
                assert review_source_block_reason(config, independent, "Codex") is None
                assert f"reviewer {label!r} is not a registered agent" in str(
                    review_source_block_reason(config, independent, label)
                )
                # A label is no reviewer of its own commits either.
                own = _finalized_by_claude(config, label)
                assert review_source_block_reason(config, own, label) is not None

    def test_a_forged_record_is_not_rewritten_into_passing(self) -> None:
        """Git keeps the label; a record claiming a resolved or other name is refused."""
        config = _historical_alias_config()
        label, other = HISTORICAL_LABELS
        genuine = _finalized_by_claude(config, label, reviewer="Codex2")
        assert review_source_block_reason(config, genuine, "Codex2") is None

        for forged_authors in (["Codex"], [other], ["Antigravity"], [label, "Codex"]):
            with self.subTest(forged=forged_authors):
                forged = json.loads(json.dumps(genuine))
                forged["review_submission"]["source_authors"] = forged_authors
                before = json.loads(json.dumps(forged))
                source = task_review_source_identity(config, forged)
                assert f"are not the immutable Git authors [{label!r}]" in str(source.error)
                assert not source.pools
                for reviewer in ("Codex", "Codex3", "Codex2", "Codex4"):
                    assert review_source_block_reason(config, forged, reviewer) == source.error
                assert forged == before

        # A canonical range recorded under a declared label is just as forged.
        canonical = _finalized_by_claude(config, "Codex", reviewer="Codex2")
        canonical["review_submission"]["source_authors"] = [label]
        assert "are not the immutable Git authors ['Codex']" in str(
            task_review_source_identity(config, canonical).error
        )

    def test_cached_git_evidence_never_caches_the_resolved_identity(self) -> None:
        """The same immutable range is re-resolved against whatever the catalog says now."""
        import dispatch_policy

        config = _historical_alias_config()
        label = "Codex foreground"
        task = _finalized_by_claude(config, label, reviewer="Codex2")
        repo = config["coordination"]["repositories"]["pantheon"]["local_path"]

        def answers() -> tuple[Any, ...]:
            source = task_review_source_identity(config, task)
            return (source.error, source.pools, review_source_block_reason(config, task, "Codex2"))

        def cached() -> list[tuple[str, ...]]:
            return [value for key, value in dispatch_policy._IMMUTABLE_SOURCE_AUTHORS.items() if key[0] == repo]

        def reset_cache(warm: bool) -> None:
            if not warm:
                for key in [key for key in dispatch_policy._IMMUTABLE_SOURCE_AUTHORS if key[0] == repo]:
                    del dispatch_policy._IMMUTABLE_SOURCE_AUTHORS[key]

        pools = frozenset({"codex_bjoe", "claude_main"})
        assert answers() == (None, pools, None)
        assert cached() == [(label,)]

        for warm in (True, False):
            with self.subTest(cache="warm" if warm else "cold"):
                # The same label now declared by the reviewer's own account.
                config["agents"]["codex"]["source_aliases"] = []
                config["agents"]["codex2"]["source_aliases"] = [label]
                reset_cache(warm)
                error, moved, block = answers()
                assert error is None
                assert moved == frozenset({"codex_lupin", "claude_main"})
                assert "is itself a source author" in str(block)

                # Declared by both, then by nobody: no stale answer survives.
                config["agents"]["codex"]["source_aliases"] = [label]
                reset_cache(warm)
                error, conflicted, block = answers()
                assert "is not a registered agent" in str(error)
                assert not conflicted
                assert block == error

                config["agents"]["codex"]["source_aliases"] = []
                config["agents"]["codex2"]["source_aliases"] = []
                reset_cache(warm)
                error, _, block = answers()
                assert "is not a registered agent" in str(error)
                assert block == error

                # The pool is read from the agent's current record, not remembered.
                config["agents"]["codex"]["source_aliases"] = [label]
                config["agents"]["codex"]["account_pool"] = "codex_lupin"
                reset_cache(warm)
                error, repooled, block = answers()
                assert error is None
                assert repooled == frozenset({"codex_lupin", "claude_main"})
                assert "codex_lupin" in str(block)

                config["agents"]["codex"]["account_pool"] = "codex_bjoe"
                config["agents"]["codex"]["source_aliases"] = list(HISTORICAL_LABELS)
                reset_cache(warm)
                assert answers() == (None, pools, None)
                # Only the literal Git label ever enters the cache.
                assert cached() == [(label,)]

    def test_normalizer_replaces_the_same_source_reviewer_and_keeps_the_finalizer(self) -> None:
        for label in HISTORICAL_LABELS:
            with self.subTest(label=label):
                config = _historical_alias_config()
                task = _finalized_by_claude(config, label)
                assert supervisor.review_is_independent(config, "Claude", "Codex")
                issues = supervisor.task_assignment_integrity_issues(config, {}, dict(task))
                assert "reviewer_shares_source_account_pool" in issues
                with mock.patch.object(
                    supervisor,
                    "get_agent_reassignment_candidates",
                    lambda *a, **k: ["Codex3", "Codex", "Codex2"],
                ):
                    changes = _run_repair(config, task, pin_author=True)
                assert changes == [("Claude", "Codex2")], changes

    def test_both_failovers_exclude_the_alias_pool(self) -> None:
        for label in HISTORICAL_LABELS:
            with self.subTest(label=label):
                config = _historical_alias_config()
                # Codex2 goes away; Codex and Codex3 are the alias's own pool.
                config["account_pools"]["codex_lupin"]["state"] = "paused"
                task = _finalized_by_claude(config, label, reviewer="Codex2")
                state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
                status: dict[str, Any] = {"tasks": [task]}
                with (
                    mock.patch("supervisor.persist_task_reassignment", return_value=True),
                    mock.patch("supervisor.write_activity_log"),
                ):
                    assert dispatch_engine.reassign_unavailable_reviewers(config, state, status) is True
                assert status["tasks"][0]["reviewer"] == "Codex4"
                assert status["tasks"][0]["owner"] == "Claude"

                config = _historical_alias_config()
                task = _finalized_by_claude(config, label, reviewer="Codex2")
                worker = {"task_id": "T-SOURCE", "agent_id": "codex2", "retry_count": 2}
                with (
                    mock.patch("supervisor.load_status", return_value={"tasks": [task]}),
                    mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
                    mock.patch("supervisor.write_activity_log"),
                    mock.patch.object(
                        supervisor,
                        "get_agent_reassignment_candidates",
                        lambda *a, **k: ["Codex", "Codex3", "Codex4"],
                    ),
                ):
                    assert (
                        worker_failure_policy.maybe_reassign_task_after_worker_failure(
                            config, {}, worker, "terminal error", terminal=True
                        )
                        == "Codex4"
                    )
                assert persist.call_args.kwargs["new_owner"] == "Claude"
                assert persist.call_args.kwargs["new_reviewer"] == "Codex4"

    def test_no_reviewer_path_selects_anyone_against_an_unresolvable_source(self) -> None:
        """Normalizer and both failovers, for every bad catalog and for a forged record."""

        def forged_record(config: dict[str, Any]) -> dict[str, Any]:
            task = _finalized_by_claude(config, "Codex foreground", reviewer="Codex2")
            task["review_submission"]["source_authors"] = ["Antigravity"]
            return task

        def catalog_case(damage: Any) -> Any:
            def build(config: dict[str, Any]) -> dict[str, Any]:
                damage(config)
                return _finalized_by_claude(config, "Codex foreground", reviewer="Codex2")

            return build

        cases = {name: catalog_case(damage) for name, damage in _UNRESOLVABLE_ALIAS_CATALOGS.items()}
        cases["forged record"] = forged_record
        candidates = ["Codex", "Codex3", "Codex4"]
        for name, build in cases.items():
            with self.subTest(case=name):
                config = _historical_alias_config()
                task = build(config)
                issues = supervisor.task_assignment_integrity_issues(config, {}, dict(task))
                assert any(issue.startswith("review_source_unresolved:") for issue in issues), issues
                with mock.patch.object(
                    supervisor, "get_agent_reassignment_candidates", lambda *a, **k: candidates
                ):
                    assert _run_repair(config, task, pin_author=True) == []

                worker = {"task_id": "T-SOURCE", "agent_id": "codex2", "retry_count": 2}
                with (
                    mock.patch("supervisor.load_status", return_value={"tasks": [dict(task)]}),
                    mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
                    mock.patch("supervisor.write_activity_log"),
                    mock.patch.object(
                        supervisor, "get_agent_reassignment_candidates", lambda *a, **k: candidates
                    ),
                ):
                    assert (
                        worker_failure_policy.maybe_reassign_task_after_worker_failure(
                            config, {}, worker, "terminal error", terminal=True
                        )
                        is None
                    )
                persist.assert_not_called()

                config["account_pools"]["codex_lupin"]["state"] = "paused"
                state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
                with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
                    assert (
                        dispatch_engine.reassign_unavailable_reviewers(config, state, {"tasks": [dict(task)]})
                        is False
                    )
                persist.assert_not_called()

    def test_failover_paths_read_only_the_owned_event_queue(self) -> None:
        """The fixture binding the failover cases above depend on."""
        config = build_config()
        queue = _OWNED_PATHS["event_queue"]
        assert config["paths"]["event_queue"] == str(queue)
        assert config["paths"]["status_file"] == str(_OWNED_PATHS["status_file"])
        assert queue.read_text(encoding="utf-8") == ""
        assert queue.parent == _SOURCE_GIT["root"].parent


# --------------------------------------------------------------------------- #
# Rules scoped to named tasks and exact logical actors
# --------------------------------------------------------------------------- #

TARGET_TASK_ID = "ODP-SCOPED-TARGET"
UNRELATED_TASK_ID = "ODP-SCOPED-UNRELATED"

#: The class-free fleet rules that must keep their meaning beside targeted ones.
_FLEET_RULES: list[dict[str, Any]] = [
    {"roles": ["reviewer"], "providers": ["codex"]},
    {"roles": ["owner", "helper"], "providers": ["antigravity", "claude", "pi"]},
]
_TARGET_CLAUDE_AND_CODEX: list[dict[str, Any]] = [
    {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude"], "providers": ["claude"]},
    {"roles": ["reviewer"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["codex"], "providers": ["codex"]},
]


def _scoped_config(*rules: dict[str, Any], unclassified: Any = "allow") -> dict[str, Any]:
    """The fleet with previously authorized Pi and a third Antigravity lane."""
    config = build_config(
        policy={
            "enabled": True,
            "rules": [*_FLEET_RULES, *rules],
            "unclassified_owned_work": unclassified,
        }
    )
    config["providers"]["pi"] = {"delivery_mode": "pi"}
    config["account_pools"]["pi_main"] = {"enabled": True, "max_concurrent": 1}
    config["agents"]["pi"] = {
        "display_name": "Pi",
        "provider": "pi",
        "adapter": "pi",
        "account_pool": "pi_main",
    }
    config["agents"]["antigravity3"] = {
        "display_name": "Antigravity3",
        "provider": "antigravity",
        "adapter": "antigravity",
        "account_pool": "agy_main",
    }
    return config


def _scoped_task(task_id: str = TARGET_TASK_ID, **fields: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "status": "todo",
        "task_class": "implementation",
        "priority": "P2",
        "owner": "Claude",
        "reviewer": "Codex",
        "depends_on": [],
    }
    task.update(fields)
    return task


def _scoped_cli_patches(ai_status: Any, config: dict[str, Any]) -> list[Any]:
    """The canonical CLI's patches with every actor of `_scoped_config` on the roster."""
    return [
        mock.patch.dict("os.environ", {"AI_NAME": "Codex"}, clear=False),
        mock.patch.object(
            ai_status,
            "configured_agent_names",
            return_value={"Codex", "Codex2", "Claude", "Claude2", "Antigravity", "Pi"},
        ),
        mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
    ]


class TaskScopedRoleProviderTests(unittest.TestCase):
    """`task_ids` selects literal tasks and `agent_ids` restricts their exact actors.

    The contact points are the original ones: the shared predicate, the queue
    and its pre-launch recheck, owner failure, review churn, reviewer failover
    and the canonical CLI. Nothing here asks a matcher in isolation whether a
    rule applies; the evidence for a scoped rule is what those paths then do.
    """

    def test_the_selected_claude_is_not_interchangeable_with_claude2(self) -> None:
        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        target = _scoped_task()

        assert worker_failure_policy.agent_can_take_task(config, "Claude", target, role=ROLE_OWNER)
        assert not worker_failure_policy.agent_can_take_task(config, "Claude2", target, role=ROLE_OWNER)
        reason = str(role_provider_block_reason(config, "Claude2", role=ROLE_OWNER, task=target))
        assert TARGET_TASK_ID in reason and "claude2" in reason
        # The two share provider and pool, so only the actor constraint can tell
        # them apart; the same fact also has to survive the common display name.
        assert worker_failure_policy.agent_provider_identity_ids(
            config, "Claude"
        ) == worker_failure_policy.agent_provider_identity_ids(config, "Claude2")
        assert role_provider_block_reason(config, "claude", role=ROLE_OWNER, task=target) is None

        unrelated = _scoped_task(UNRELATED_TASK_ID)
        assert worker_failure_policy.agent_can_take_task(config, "Claude2", unrelated, role=ROLE_OWNER)

    def test_the_selected_codex_is_not_interchangeable_with_codex2(self) -> None:
        for selected, refused in (("Codex", "Codex2"), ("Codex2", "Codex")):
            with self.subTest(selected=selected):
                config = _scoped_config(
                    {
                        "roles": ["reviewer"],
                        "task_ids": [TARGET_TASK_ID],
                        "agent_ids": [selected.lower()],
                        "providers": ["codex"],
                    }
                )
                target = _scoped_task(status="review")
                assert worker_failure_policy.agent_can_take_task(config, selected, target, role=ROLE_REVIEWER)
                assert not worker_failure_policy.agent_can_take_task(config, refused, target, role=ROLE_REVIEWER)
                assert worker_failure_policy.agent_can_take_task(
                    config, refused, _scoped_task(UNRELATED_TASK_ID, status="review"), role=ROLE_REVIEWER
                )

        # The same provider and the same pool: an alias of one account is still
        # a different logical actor when the rule names the actor.
        config = _pool_alias_config()
        config["ready_dispatcher"]["role_provider_policy"] = {
            "enabled": True,
            "rules": [
                {
                    "roles": ["reviewer"],
                    "task_ids": [TARGET_TASK_ID],
                    "agent_ids": ["codex"],
                    "providers": ["codex"],
                }
            ],
            "unclassified_owned_work": "allow",
        }
        assert supervisor.agent_account_pool_id(config, "Codex") == supervisor.agent_account_pool_id(config, "Codex3")
        target = _scoped_task(status="review")
        assert worker_failure_policy.agent_can_take_task(config, "Codex", target, role=ROLE_REVIEWER)
        assert not worker_failure_policy.agent_can_take_task(config, "Codex3", target, role=ROLE_REVIEWER)

    def test_actor_and_provider_restrictions_are_both_kept(self) -> None:
        """Being listed does not excuse the provider, nor the provider the listing."""
        config = _scoped_config(
            {
                "roles": ["owner"],
                "task_ids": [TARGET_TASK_ID],
                "agent_ids": ["claude", "antigravity"],
                "providers": ["claude"],
            }
        )
        target = _scoped_task()
        assert worker_failure_policy.agent_can_take_task(config, "Claude", target, role=ROLE_OWNER)
        # Listed, but its provider is outside the targeted intersection.
        assert "is not permitted" in str(role_provider_block_reason(config, "Antigravity", role=ROLE_OWNER, task=target))
        # Its provider is inside, but the actor is not listed.
        assert not worker_failure_policy.agent_can_take_task(config, "Claude2", target, role=ROLE_OWNER)

        narrowed = _scoped_config(
            {
                "roles": ["owner"],
                "task_ids": [TARGET_TASK_ID],
                "agent_ids": ["claude", "claude2"],
                "providers": ["claude"],
            },
            {
                "roles": ["owner"],
                "task_ids": [TARGET_TASK_ID],
                "agent_ids": ["claude2"],
                "providers": ["claude", "antigravity"],
            },
        )
        assert not worker_failure_policy.agent_can_take_task(narrowed, "Claude", target, role=ROLE_OWNER)
        assert worker_failure_policy.agent_can_take_task(narrowed, "Claude2", target, role=ROLE_OWNER)

        # A targeted rule narrows the fleet rule; it cannot widen it to a provider
        # the role is otherwise closed to.
        widening = _scoped_config(
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["codex"], "providers": ["codex"]}
        )
        assert not worker_failure_policy.agent_can_take_task(widening, "Codex", target, role=ROLE_OWNER)

        # Disjoint listings are a rule nobody can satisfy, and it stays one.
        disjoint = _scoped_config(
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude"], "providers": ["claude"]},
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude2"], "providers": ["claude"]},
        )
        for agent in ("Claude", "Claude2"):
            assert not worker_failure_policy.agent_can_take_task(disjoint, agent, target, role=ROLE_OWNER)

    def test_task_and_class_selectors_must_both_match(self) -> None:
        config = _scoped_config(
            {
                "roles": ["owner"],
                "task_ids": [TARGET_TASK_ID],
                "task_classes": ["remediation"],
                "agent_ids": ["claude"],
                "providers": ["claude"],
            }
        )
        assert worker_failure_policy.agent_can_take_task(config, "Pi", _scoped_task(), role=ROLE_OWNER)
        assert not worker_failure_policy.agent_can_take_task(
            config, "Pi", _scoped_task(task_class="remediation"), role=ROLE_OWNER
        )
        assert worker_failure_policy.agent_can_take_task(
            config, "Pi", _scoped_task(UNRELATED_TASK_ID, task_class="remediation"), role=ROLE_OWNER
        )

    def test_a_rule_without_task_ids_can_constrain_actors_on_its_own_scope(self) -> None:
        """The two fields are independent: `agent_ids` needs no `task_ids`."""
        config = _scoped_config(
            {"roles": ["owner"], "task_classes": ["remediation"], "agent_ids": ["claude"], "providers": ["claude"]}
        )
        remediation = _scoped_task(UNRELATED_TASK_ID, task_class="remediation")
        assert worker_failure_policy.agent_can_take_task(config, "Claude", remediation, role=ROLE_OWNER)
        assert not worker_failure_policy.agent_can_take_task(config, "Claude2", remediation, role=ROLE_OWNER)
        assert worker_failure_policy.agent_can_take_task(
            config, "Claude2", _scoped_task(UNRELATED_TASK_ID), role=ROLE_OWNER
        )

    def test_unrelated_previously_authorized_work_keeps_its_exact_answer(self) -> None:
        """No implicit Claude-only rule: only the named task's eligibility moves."""
        fleet = _scoped_config()
        scoped = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        unrelated = [
            _scoped_task(UNRELATED_TASK_ID),
            _scoped_task(UNRELATED_TASK_ID, task_class="remediation"),
            _scoped_task(UNRELATED_TASK_ID, task_class=""),
            _scoped_task(f"{TARGET_TASK_ID}-2"),
            _scoped_task(TARGET_TASK_ID.lower()),
        ]
        agents = ("Pi", "Antigravity", "Antigravity2", "Antigravity3", "Claude", "Claude2", "Codex", "Codex2")
        for task in unrelated:
            for agent in agents:
                for role in (ROLE_OWNER, ROLE_REVIEWER, ROLE_HELPER):
                    with self.subTest(task=task["id"], agent=agent, role=role):
                        assert role_provider_block_reason(scoped, agent, role=role, task=task) == (
                            role_provider_block_reason(fleet, agent, role=role, task=task)
                        )
        for agent in ("Pi", "Antigravity", "Antigravity3", "Claude2"):
            assert worker_failure_policy.agent_can_take_task(scoped, agent, unrelated[0], role=ROLE_OWNER)
        # The role the rule does not name is not narrowed either.
        target = _scoped_task()
        assert worker_failure_policy.agent_can_take_task(scoped, "Pi", target, role=ROLE_HELPER)

    def test_malformed_or_unregistered_selectors_fail_closed(self) -> None:
        def targeted(**fields: Any) -> dict[str, Any]:
            return {"roles": ["owner"], "providers": ["claude"], "task_ids": [TARGET_TASK_ID], **fields}

        cases: dict[str, tuple[dict[str, Any], str]] = {
            "empty task_ids": (targeted(task_ids=[]), "task_ids"),
            "null task_ids": (targeted(task_ids=None), "task_ids"),
            "bare string task_ids": (targeted(task_ids=TARGET_TASK_ID), "task_ids"),
            "non-string task id": (targeted(task_ids=[TARGET_TASK_ID, 7]), "task_ids"),
            "blank task id": (targeted(task_ids=["  "]), "task_ids"),
            "padded task id": (targeted(task_ids=[f" {TARGET_TASK_ID}"]), "task_ids"),
            "wildcard task id": (targeted(task_ids=["*"]), "task_ids"),
            "prefix task id": (targeted(task_ids=["ODP-*"]), "task_ids"),
            "regex task id": (targeted(task_ids=["ODP-.+"]), "task_ids"),
            "empty agent_ids": (targeted(agent_ids=[]), "agent_ids"),
            "null agent_ids": (targeted(agent_ids=None), "agent_ids"),
            "bare string agent_ids": (targeted(agent_ids="claude"), "agent_ids"),
            "non-string agent id": (targeted(agent_ids=["claude", 7]), "agent_ids"),
            "blank agent id": (targeted(agent_ids=[" "]), "agent_ids"),
            "unknown agent id": (targeted(agent_ids=["claude9"]), "claude9"),
            "display name, not id": (targeted(agent_ids=["Claude"]), "Claude"),
            "non-canonical spelling": (targeted(agent_ids=["CLAUDE"]), "CLAUDE"),
            "padded agent id": (targeted(agent_ids=[" claude"]), "agent_ids"),
            "inferred family": (targeted(agent_ids=["claude*"]), "claude*"),
            "provider, not agent": (targeted(agent_ids=["claude_cli"]), "claude_cli"),
            "account pool, not agent": (targeted(agent_ids=["claude_main"]), "claude_main"),
            "physical slot": (targeted(agent_ids=["codex_bjoe_slot_1"]), "codex_bjoe_slot_1"),
            "source alias": (targeted(agent_ids=["Codex foreground"]), "Codex foreground"),
            "normalized source alias": (targeted(agent_ids=["codex_foreground"]), "codex_foreground"),
        }
        for name, (rule, fragment) in cases.items():
            with self.subTest(name):
                config = _scoped_config(rule)
                config["agents"]["codex"]["source_aliases"] = ["Codex foreground"]
                error = str(role_provider_policy_error(config))
                assert fragment in error, error
                # An unreadable rule denies every lane, on every task.
                for task in (_scoped_task(), _scoped_task(UNRELATED_TASK_ID)):
                    for agent in ("Claude", "Pi"):
                        assert "malformed" in str(role_provider_block_reason(config, agent, role=ROLE_OWNER, task=task))
                        assert not worker_failure_policy.agent_can_take_task(config, agent, task, role=ROLE_OWNER)

    def test_an_actor_whose_id_or_display_name_collides_is_not_nameable(self) -> None:
        rule = {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude"], "providers": ["claude"]}

        id_collides = _scoped_config(rule)
        id_collides["agents"]["writer"] = {
            "display_name": "claude",
            "provider": "claude",
            "adapter": "claude_cli",
            "account_pool": "claude_main",
        }
        assert "claude" in str(role_provider_policy_error(id_collides))

        display_collides = _scoped_config(rule)
        display_collides["agents"]["claude2"]["display_name"] = "Claude"
        assert "claude" in str(role_provider_policy_error(display_collides))

        for config in (id_collides, display_collides):
            assert not worker_failure_policy.agent_can_take_task(config, "Claude", _scoped_task(), role=ROLE_OWNER)

        # An unrelated lane may use its own id even with that collision elsewhere.
        elsewhere = _scoped_config(
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["pi"], "providers": ["pi"]}
        )
        elsewhere["agents"]["writer"] = dict(id_collides["agents"]["writer"])
        assert role_provider_policy_error(elsewhere) is None
        assert worker_failure_policy.agent_can_take_task(elsewhere, "Pi", _scoped_task(), role=ROLE_OWNER)

    def test_the_normal_loader_refuses_a_malformed_shape_and_accepts_the_declared_one(self) -> None:
        import common

        def load(rule: dict[str, Any]) -> dict[str, Any]:
            path = _OWNED_PATHS["status_file"].parent / "scoped-config.json"
            path.write_text(
                json.dumps(
                    {
                        "ready_dispatcher": {
                            "role_provider_policy": {
                                "enabled": True,
                                "rules": [rule],
                                "unclassified_owned_work": "allow",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            return common.load_config(path)

        valid = {"roles": ["owner"], "providers": ["claude"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude"]}
        loaded = load(valid)["ready_dispatcher"]["role_provider_policy"]["rules"][0]
        assert loaded["task_ids"] == [TARGET_TASK_ID]
        assert loaded["agent_ids"] == ["claude"]

        for name, change in {
            "empty task_ids": {"task_ids": []},
            "bare task_ids": {"task_ids": TARGET_TASK_ID},
            "wildcard task id": {"task_ids": ["ODP-*"]},
            "empty agent_ids": {"agent_ids": []},
            "bare agent_ids": {"agent_ids": "claude"},
            "display-shaped agent id": {"agent_ids": ["Claude"]},
            "misspelled field": {"task_id": [TARGET_TASK_ID]},
        }.items():
            with self.subTest(name), pytest.raises(common.ConfigError):
                load({**valid, **change})

    def test_no_task_context_is_not_a_grant_for_a_known_task(self) -> None:
        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)

        # A generic question about a role cannot evaluate a named task, so it is
        # answered from the role-wide rules exactly as a class-scoped one is.
        assert worker_failure_policy.agent_can_take_task(config, "Claude2", None, role=ROLE_OWNER)
        assert not worker_failure_policy.agent_can_take_task(config, "Codex", None, role=ROLE_OWNER)
        assert role_provider_block_reason(config, "Claude2", role=ROLE_OWNER, task_class="implementation") is None

        # ...while a role-wide actor constraint has no task to wait for.
        role_wide = _scoped_config({"roles": ["owner"], "agent_ids": ["claude"], "providers": ["claude"]})
        assert not worker_failure_policy.agent_can_take_task(role_wide, "Claude2", None, role=ROLE_OWNER)

        # A real task record without its id is not "some other task", and handing
        # out new authority with no task at all is not a generic question.
        for agent in ("Claude", "Claude2", "Pi"):
            idless = {"status": "todo", "task_class": "implementation"}
            for task in (idless, {**idless, "id": ""}, {**idless, "id": 7}):
                reason = str(role_provider_block_reason(config, agent, role=ROLE_OWNER, task=task))
                assert "no task id" in reason
                assert not worker_failure_policy.agent_can_take_task(config, agent, task, role=ROLE_OWNER)
            assert "no task id" in str(
                role_provider_block_reason(config, agent, role=ROLE_OWNER, grants_new_authority=True)
            )

        # Only a rule selecting by task makes the id matter: the fleet with none
        # reads an id-less record, and a grant with no task, exactly as before.
        legacy = _scoped_config()
        idless = {"status": "todo", "task_class": "implementation"}
        assert role_provider_policy_error(legacy) is None
        assert role_provider_block_reason(legacy, "Claude", role=ROLE_OWNER, task=idless) is None
        assert role_provider_block_reason(legacy, "Claude2", role=ROLE_OWNER, grants_new_authority=True) is None
        # A rule for another role does not reach this one.
        assert role_provider_block_reason(
            _scoped_config(_TARGET_CLAUDE_AND_CODEX[1]), "Claude2", role=ROLE_OWNER, task=idless
        ) is None

    def test_the_unclassified_disposition_is_not_lifted_by_a_targeted_rule(self) -> None:
        rule = {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude"], "providers": ["claude"]}
        unclassified_target = _scoped_task(task_class="")

        def class_scoped(disposition: Any) -> dict[str, Any]:
            config = _scoped_config(rule, unclassified=disposition)
            config["ready_dispatcher"]["role_provider_policy"]["rules"] = [
                {"roles": ["reviewer"], "providers": ["codex"]},
                {"roles": ["owner"], "task_classes": ["implementation"], "providers": ["claude"]},
                rule,
            ]
            return config

        assert role_provider_block_reason(class_scoped("allow"), "Claude", role=ROLE_OWNER, task=unclassified_target) is None
        assert role_provider_block_reason(class_scoped("allow"), "Pi", role=ROLE_OWNER, task=unclassified_target)
        assert role_provider_block_reason(class_scoped("deny"), "Claude", role=ROLE_OWNER, task=unclassified_target)
        assert role_provider_block_reason(class_scoped(["pi"]), "Claude", role=ROLE_OWNER, task=unclassified_target)
        assert role_provider_block_reason(class_scoped(["claude"]), "Claude", role=ROLE_OWNER, task=unclassified_target) is None
        # The disposition still answers the same question for unrelated work.
        unrelated = _scoped_task(UNRELATED_TASK_ID, task_class="")
        assert role_provider_block_reason(class_scoped("allow"), "Pi", role=ROLE_OWNER, task=unrelated) is None
        assert role_provider_block_reason(class_scoped("deny"), "Pi", role=ROLE_OWNER, task=unrelated)
        assert role_provider_block_reason(class_scoped(["pi"]), "Pi", role=ROLE_OWNER, task=unrelated) is None

    def test_a_frozen_closeout_keeps_its_recorded_actors_and_grants_nothing_new(self) -> None:
        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        frozen = _scoped_task(
            status="review_approved", owner="Claude2", reviewer="Codex2", approved_head="a" * 40
        )
        before = json.loads(json.dumps(frozen))

        # What the record already says is history, whoever the rule names today.
        assert role_provider_block_reason(config, "Claude2", role=ROLE_OWNER, task=frozen) is None
        assert role_provider_block_reason(config, "Codex2", role=ROLE_REVIEWER, task=frozen) is None
        assert worker_failure_policy.agent_can_take_task(config, "Claude2", frozen, role=ROLE_OWNER)
        event = {
            "event_id": "evt-finalize",
            "task_id": TARGET_TASK_ID,
            "target_agent": "claude2",
            "target_display_name": "Claude2",
            "reason": "owned_finalize_dispatch",
        }
        message = dispatch_engine.stale_dispatch_skip_message(config, event, {TARGET_TASK_ID: frozen})
        assert message is None or "role/provider policy" not in message

        # Anything it would newly hand out is the rule's to answer.
        for agent, role in (("Claude2", ROLE_OWNER), ("Codex2", ROLE_REVIEWER)):
            assert role_provider_block_reason(config, agent, role=role, task=frozen, grants_new_authority=True)
        assert role_provider_block_reason(config, "Pi", role=ROLE_OWNER, task=frozen)
        assert role_provider_block_reason(config, "Claude", role=ROLE_OWNER, task=frozen, grants_new_authority=True) is None
        assert frozen == before

    def _ready_events(self, config: dict[str, Any], tasks: list[dict[str, Any]], agents: list[str]) -> list[dict[str, Any]]:
        queued: list[dict[str, Any]] = []
        config.setdefault("worker_runtime", {"heartbeat_stale_seconds": 300, "heartbeat_grace_seconds": 60})
        with (
            mock.patch.object(supervisor, "load_status", return_value={"tasks": tasks}),
            mock.patch.object(supervisor, "load_event_queue", return_value=[]),
            mock.patch.object(supervisor, "commit_canonical_task_transition", return_value=True),
            mock.patch.object(supervisor, "write_activity_log"),
            mock.patch.object(supervisor, "agent_auto_dispatch_block_reason", return_value=None),
            mock.patch.object(
                supervisor, "queue_delivery_event", side_effect=lambda _c, evt: queued.append(evt) or True
            ),
        ):
            supervisor.dispatch_ready_tasks(config, {"workers": {}, "queue": {"events": {}}}, agent_ids_override=agents)
        return queued

    def test_the_ready_queue_dispatches_only_the_named_actor(self) -> None:
        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)

        refused = self._ready_events(config, [_scoped_task(owner="Claude2")], ["claude2"])
        assert not refused

        queued = self._ready_events(config, [_scoped_task(owner="Claude")], ["claude"])
        assert [(event["task_id"], event["target_agent"]) for event in queued] == [(TARGET_TASK_ID, "Claude")]

        unrelated = self._ready_events(
            _scoped_config(*_TARGET_CLAUDE_AND_CODEX), [_scoped_task(UNRELATED_TASK_ID, owner="Claude2")], ["claude2"]
        )
        assert [(event["task_id"], event["target_agent"]) for event in unrelated] == [(UNRELATED_TASK_ID, "Claude2")]

    def test_a_queued_wake_is_rechecked_before_launch(self) -> None:
        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)

        def wake(task_id: str, target: str) -> dict[str, Any]:
            return {
                "event_id": "evt-scoped",
                "task_id": task_id,
                "target_agent": target.lower(),
                "target_display_name": target,
                "reason": REASON_OWNED_READY,
            }

        def policy_message(task: dict[str, Any], target: str) -> str:
            event = wake(task["id"], target)
            return str(dispatch_engine.stale_dispatch_skip_message(config, event, {task["id"]: task}))

        assert "role/provider policy no longer permits" in policy_message(_scoped_task(owner="Claude2"), "Claude2")
        assert "role/provider policy" not in policy_message(_scoped_task(owner="Claude"), "Claude")
        assert "role/provider policy" not in policy_message(_scoped_task(UNRELATED_TASK_ID, owner="Claude2"), "Claude2")

    def test_a_scoped_helper_role_narrows_helper_claims(self) -> None:
        config = _scoped_config(
            {"roles": ["helper"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["antigravity"], "providers": ["antigravity"]}
        )
        assert worker_failure_policy.agent_can_take_task(config, "Antigravity", _scoped_task(), role=ROLE_HELPER)
        assert not worker_failure_policy.agent_can_take_task(config, "Antigravity2", _scoped_task(), role=ROLE_HELPER)
        assert worker_failure_policy.agent_can_take_task(
            config, "Antigravity2", _scoped_task(UNRELATED_TASK_ID), role=ROLE_HELPER
        )

    def _owner_failure(
        self, config: dict[str, Any], task: dict[str, Any], agent_id: str = "claude"
    ) -> tuple[str | None, Any]:
        worker = {"task_id": task["id"], "agent_id": agent_id, "retry_count": 2}
        with (
            mock.patch("supervisor.load_status", return_value={"tasks": [task]}),
            mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
            mock.patch("supervisor.write_activity_log"),
        ):
            reassigned = worker_failure_policy.maybe_reassign_task_after_worker_failure(
                config, {}, worker, "terminal error", terminal=True
            )
        return reassigned, persist

    def test_owner_failure_cannot_escape_the_named_actor(self) -> None:
        restricted = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        reassigned, persist = self._owner_failure(restricted, _scoped_task())
        assert reassigned is None
        persist.assert_not_called()

        # The per-actor mapping knows nothing about tasks, and an empty entry
        # falls back to the whole fleet; neither may reach past the rule.
        for mapping in ({"Claude": []}, {"Claude": ["Claude2", "Pi", "Antigravity"]}):
            config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
            config["worker_reassignment"] = {"owner_fallbacks": mapping}
            reassigned, persist = self._owner_failure(config, _scoped_task())
            assert reassigned is None, mapping
            persist.assert_not_called()

        # With a second named actor the same failure moves there, not to the
        # same-family lane the unscoped search would have preferred.
        moved = _scoped_config(
            {
                "roles": ["owner"],
                "task_ids": [TARGET_TASK_ID],
                "agent_ids": ["claude", "antigravity3"],
                "providers": ["claude", "antigravity"],
            }
        )
        reassigned, persist = self._owner_failure(moved, _scoped_task())
        assert reassigned == "Antigravity3"
        assert persist.call_args.kwargs["new_owner"] == "Antigravity3"
        assert persist.call_args.kwargs["new_reviewer"] == "Codex"

        # The candidates still come from the per-actor mapping, which the policy
        # filters and does not extend: a named actor outside it is simply not
        # reached, so the task stays put instead of moving to anyone else.
        unmapped = _scoped_config(
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["claude", "pi"], "providers": ["claude", "pi"]}
        )
        assert "Pi" not in supervisor.get_agent_reassignment_candidates(
            unmapped, "Claude", role=ROLE_OWNER, task=_scoped_task()
        )
        reassigned, persist = self._owner_failure(unmapped, _scoped_task())
        assert reassigned is None
        persist.assert_not_called()
        unmapped["worker_reassignment"] = {"owner_fallbacks": {"Claude": ["Claude2", "Pi"]}}
        reassigned, _persist = self._owner_failure(unmapped, _scoped_task())
        assert reassigned == "Pi"

        # Unrelated work still reassigns through the unchanged search.
        reassigned, _persist = self._owner_failure(
            _scoped_config(*_TARGET_CLAUDE_AND_CODEX), _scoped_task(UNRELATED_TASK_ID)
        )
        assert reassigned == "Claude2"

    def test_reviewer_failure_and_failover_cannot_escape_the_named_actor(self) -> None:
        def submitted(config: dict[str, Any], task_id: str) -> dict[str, Any]:
            return _scoped_task(
                task_id,
                status="review",
                review_submission=_source_submission(config, task_id, "Claude", submitted_by="Claude"),
            )

        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        reassigned, persist = self._owner_failure(config, submitted(config, TARGET_TASK_ID), agent_id="codex")
        assert reassigned is None
        persist.assert_not_called()
        reassigned, persist = self._owner_failure(config, submitted(config, UNRELATED_TASK_ID), agent_id="codex")
        assert reassigned == "Codex2"
        assert persist.call_args.kwargs["new_reviewer"] == "Codex2"

        config["account_pools"]["codex_bjoe"]["state"] = "paused"
        state: dict[str, Any] = {"active_workers": {}, "outstanding_deliveries": {}}
        with mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist:
            assert dispatch_engine.reassign_unavailable_reviewers(
                config, state, {"tasks": [submitted(config, TARGET_TASK_ID)]}
            ) is False
        persist.assert_not_called()
        with (
            mock.patch("supervisor.persist_task_reassignment", return_value=True) as persist,
            mock.patch("supervisor.write_activity_log"),
        ):
            assert dispatch_engine.reassign_unavailable_reviewers(
                config, state, {"tasks": [submitted(config, UNRELATED_TASK_ID)]}
            ) is True
        assert persist.call_args.kwargs["new_reviewer"] == "Codex2"

    def test_review_churn_rotates_only_inside_the_named_actors(self) -> None:
        def churn(config: dict[str, Any], task: dict[str, Any]) -> Any:
            with (
                mock.patch.object(supervisor, "persist_task_reassignment", return_value=True) as persist,
                mock.patch.object(supervisor, "write_activity_log"),
            ):
                supervisor.reassign_tasks_after_review_churn(config, {}, {"tasks": [task]})
            return persist

        churning = {"status": "in_progress", "review_reopen_count": 2}
        persist = churn(_scoped_config(*_TARGET_CLAUDE_AND_CODEX), _scoped_task(**churning))
        # Nobody else is named, so the rotation holds the owner and fails closed.
        assert persist.call_args.kwargs["new_owner"] == "Claude"
        assert persist.call_args.kwargs["new_status"] == "blocked"

        persist = churn(
            _scoped_config(
                {
                    "roles": ["owner"],
                    "task_ids": [TARGET_TASK_ID],
                    "agent_ids": ["claude", "antigravity3"],
                    "providers": ["claude", "antigravity"],
                }
            ),
            _scoped_task(**churning),
        )
        assert persist.call_args.kwargs["new_owner"] == "Antigravity3"
        assert persist.call_args.kwargs.get("new_status") != "blocked"

        persist = churn(_scoped_config(*_TARGET_CLAUDE_AND_CODEX), _scoped_task(UNRELATED_TASK_ID, **churning))
        assert persist.call_args.kwargs["new_owner"] not in {"Claude", "Claude2"}
        assert persist.call_args.kwargs.get("new_status") != "blocked"

    def test_the_repair_pass_replaces_an_unnamed_actor_on_the_target_only(self) -> None:
        config = _scoped_config(
            {"roles": ["owner"], "task_ids": [TARGET_TASK_ID], "agent_ids": ["antigravity3"], "providers": ["antigravity"]}
        )
        target = _scoped_task()
        assert "not eligible" in str(
            supervisor.task_actor_assignment_block_reason(config, {}, target, "Claude", role=ROLE_OWNER)
        )
        assert (
            supervisor.task_actor_assignment_block_reason(
                config, {}, _scoped_task(UNRELATED_TASK_ID), "Claude", role=ROLE_OWNER
            )
            is None
        )
        assert worker_failure_policy.first_viable_agent(
            config,
            supervisor.get_agent_reassignment_candidates(config, "Claude", role=ROLE_OWNER, task=target),
            {"Claude", "Codex"},
            task=target,
            balance_load=False,
            role=ROLE_OWNER,
        ) == "Antigravity3"

    def test_assign_binds_the_rule_to_its_actual_task_argument(self) -> None:
        import ai_status

        def assign(task_id: str, owner: str, reviewer: str, **env: str) -> dict[str, Any]:
            state = _cli_state()
            config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
            with mock.patch.dict("os.environ", {"TASK_CLASS": "implementation", **env}, clear=False):
                for patch in _scoped_cli_patches(ai_status, config):
                    patch.start()
                try:
                    ai_status.command_assign(state, [task_id, owner, reviewer, "scoped"])
                finally:
                    mock.patch.stopall()
            return state

        # A brand-new task has no stored record to read an id from.
        with pytest.raises(SystemExit) as excinfo:
            assign(TARGET_TASK_ID, "Claude2", "Codex")
        assert "owner Claude2" in str(excinfo.value)
        with pytest.raises(SystemExit) as excinfo:
            assign(TARGET_TASK_ID, "Claude", "Codex2")
        assert "reviewer Codex2" in str(excinfo.value)

        state = assign(TARGET_TASK_ID, "Claude", "Codex")
        assert [task["id"] for task in state["tasks"]] == [TARGET_TASK_ID]
        state = assign(UNRELATED_TASK_ID, "Claude2", "Codex2")
        assert [task["id"] for task in state["tasks"]] == [UNRELATED_TASK_ID]

    def test_assign_on_a_frozen_target_keeps_history_and_judges_new_grants(self) -> None:
        import ai_status

        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        frozen = _scoped_task(status="review_approved", owner="Antigravity", reviewer="Codex2", approved_head="a" * 40)

        def assign(owner: str, reviewer: str) -> dict[str, Any]:
            state = _cli_state()
            state["tasks"] = [dict(frozen)]
            for patch in _scoped_cli_patches(ai_status, config):
                patch.start()
            try:
                ai_status.command_assign(state, [TARGET_TASK_ID, owner, reviewer])
            finally:
                mock.patch.stopall()
            return state

        recorded = assign("Antigravity", "Codex2")
        assert recorded["tasks"][0]["owner"] == "Antigravity"
        assert recorded["tasks"][0]["reviewer"] == "Codex2"
        # The provider would be fine; it is the named actor this grant is not.
        with pytest.raises(SystemExit) as excinfo:
            assign("Claude2", "Codex2")
        assert "owner Claude2" in str(excinfo.value)
        assert assign("Claude", "Codex2")["tasks"][0]["owner"] == "Claude"

    def test_approve_is_a_new_signature_the_scoped_rule_answers(self) -> None:
        import ai_status

        config = _scoped_config(*_TARGET_CLAUDE_AND_CODEX)
        state = _cli_state()
        state["tasks"] = [_scoped_task(status="review", reviewer="Codex2", approved_head="a" * 40)]
        with (
            mock.patch.dict("os.environ", {"AI_NAME": "Codex2"}, clear=False),
            mock.patch.object(ai_status, "configured_agent_names", return_value={"Claude", "Codex2"}),
            mock.patch.object(ai_status, "merged_orchestrator_config", return_value=config),
            mock.patch.object(
                ai_status, "resolve_task_sha", side_effect=AssertionError("must refuse before reading the head")
            ),
            pytest.raises(SystemExit) as excinfo,
        ):
            ai_status.command_approve(state, [TARGET_TASK_ID, "looks good"])
        assert "role/provider" in str(excinfo.value)
        assert state["tasks"][0]["status"] == "review"
