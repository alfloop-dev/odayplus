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
import sys
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
    allowed_role_provider_ids,
    dispatch_reason_role,
    role_provider_block_reason,
    role_provider_policy_error,
    role_provider_policy_settings,
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
        patches.append(mock.patch.object(supervisor, "task_submitted_author", lambda *a: ""))
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
    assert supervisor.task_submitted_author(config, dict(task)) == "Codex"

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
        review_submission={"pr_number": 1221, "head": "e" * 40, "submitted_by": "Codex"},
    )
    assert supervisor.task_submitted_author(config, laundered) == "Codex"
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
    assert supervisor.task_submitted_author(config, live) == ""
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
