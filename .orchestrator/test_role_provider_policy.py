"""Role/task_class provider eligibility, and the Codex model+effort it pairs with.

The policy under test says two things at once: reviews belong to Codex, and
implementation-class owned work belongs to Antigravity/Claude. Almost every test
here is a negative one, because the failure mode that matters is not "the policy
rejected something" -- it is a path that quietly kept its old freedom while the
board looked configured. So each contact point named by the policy is asked
directly whether it can still route around the rule.
"""

from __future__ import annotations

import json
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

#: The policy this fleet is being configured with: review is Codex-only, and
#: implementation-class owned/helper work is Antigravity/Claude-only. Everything
#: else -- integration, verification, runtime_release, rollout, human gates --
#: is deliberately absent, so it keeps whatever authorization it already had.
POLICY: dict[str, Any] = {
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
    for role in (ROLE_OWNER, ROLE_HELPER):
        assert allowed_role_provider_ids(example, role=role, task_class="implementation") == {
            "antigravity",
            "claude",
        }
    # Deployment and integration lanes keep the authorization they already had.
    assert allowed_role_provider_ids(example, role=ROLE_OWNER, task_class="runtime_release") is None


@pytest.mark.parametrize(
    ("role", "task_class", "expected"),
    [
        (ROLE_REVIEWER, "implementation", {"codex"}),
        # A role-wide rule is not weakened by the task having a class the
        # class-scoped rules do not mention.
        (ROLE_REVIEWER, "integration", {"codex"}),
        (ROLE_REVIEWER, "", {"codex"}),
        (ROLE_OWNER, "implementation", {"antigravity", "claude"}),
        (ROLE_OWNER, "remediation", {"antigravity", "claude"}),
        (ROLE_OWNER, "documentation", {"antigravity", "claude"}),
        (ROLE_HELPER, "implementation", {"antigravity", "claude"}),
        # Deployment/integration classes keep their existing authorization.
        (ROLE_OWNER, "integration", None),
        (ROLE_OWNER, "verification", None),
        (ROLE_OWNER, "runtime_release", None),
        (ROLE_OWNER, "rollout", None),
        (ROLE_OWNER, "sidecar", None),
        # No task in hand: only role-wide rules can be evaluated.
        (ROLE_OWNER, None, None),
        (ROLE_REVIEWER, None, {"codex"}),
    ],
)
def test_allowed_providers_per_role_and_class(
    role: str, task_class: str | None, expected: set[str] | None
) -> None:
    assert allowed_role_provider_ids(build_config(), role=role, task_class=task_class) == expected


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
    lane sorts first, so the policy has to say what happens to it.
    """
    config = build_config(policy={**POLICY, "unclassified_owned_work": unclassified})
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


def test_approve_is_not_blocked_once_the_head_is_already_frozen() -> None:
    """Re-approving a task that already carries its head is not a new choice."""
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
        assert (
            ai_status.role_provider_assignment_block_reason(
                "Antigravity", role="reviewer", task=task
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


def test_owner_selection_keeps_codex_for_integration_class_work() -> None:
    """Integration/verification/release work keeps the authorization it had."""
    config = build_config()
    assert _select(config, OWNER_POOL, role=ROLE_OWNER, task=dict(INTEGRATION_TASK)) == "Codex"


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


def test_assign_leaves_integration_class_authorization_alone() -> None:
    """Codex keeps integration/verification/release ownership, as before."""
    import ai_status

    config = build_config()
    state = _cli_state()
    with mock.patch.dict("os.environ", {"TASK_CLASS": "integration"}, clear=False):
        for patch in _cli_patches(ai_status, config):
            patch.start()
        try:
            ai_status.command_assign(state, ["ODP-X-004", "Codex", "Codex2"])
        finally:
            mock.patch.stopall()
    assert state["tasks"][0]["owner"] == "Codex"


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
