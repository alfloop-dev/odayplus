"""The disclosure policy reads registry data, and refuses data it cannot read.

`shared/governance/netplan_disclosure.py` sits between a `DecisionPolicy` row
and the NetPlan approval path. Its whole job is to answer "may this plan be
approved with these classes unmodelled", so the ways it can be handed a row it
cannot answer from are the ways an approval could be granted on a
misunderstanding.

The integration suite exercises the policy through `decide()` with a
well-formed row. These tests cover the malformed ones, which that path cannot
reach.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from shared.governance.netplan_disclosure import (
    NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES,
    NETPLAN_DISCLOSURE_POLICY_KIND,
    NETPLAN_REQUIRED_CONSTRAINT_CLASSES,
    NetPlanDisclosurePolicyError,
    acknowledgeable_classes,
    authorized_roles,
    default_netplan_disclosure_policy,
    evaluate_disclosure,
    required_classes,
    role_is_authorized,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _policy(**parameter_overrides: object):
    base = default_netplan_disclosure_policy(TENANT_ID)
    if not parameter_overrides:
        return base
    return replace(base, parameters={**base.parameters, **parameter_overrides})


def test_the_seeded_policy_matches_the_requirement_and_the_design_decision() -> None:
    """v1 requires all eight classes and waives only the two the model cannot express."""
    policy = _policy()

    assert policy.policy_kind == NETPLAN_DISCLOSURE_POLICY_KIND
    assert set(required_classes(policy)) == set(NETPLAN_REQUIRED_CONSTRAINT_CLASSES)
    assert set(required_classes(policy)) == {
        "CAPITAL",
        "LEASE",
        "CONSTRUCTION",
        "EQUIPMENT",
        "LABOUR",
        "COVERAGE",
        "DILUTION",
        "SEQUENCING",
    }
    assert set(acknowledgeable_classes(policy)) == set(
        NETPLAN_ACKNOWLEDGEABLE_CONSTRAINT_CLASSES
    )
    assert set(acknowledgeable_classes(policy)) == {"LEASE", "SEQUENCING"}


def test_the_five_solvable_classes_are_never_waivable_under_v1() -> None:
    """Each is unmodelled only because a cap was withheld, and a cap can be supplied."""
    policy = _policy()
    waivable = set(acknowledgeable_classes(policy))

    for solvable in ("CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION"):
        assert solvable not in waivable


def test_evaluation_partitions_unmodelled_classes() -> None:
    evaluation = evaluate_disclosure(
        _policy(),
        unmodelled_classes=["LEASE", "SEQUENCING", "LABOUR"],
    )

    assert set(evaluation.blocking) == {"LABOUR"}
    assert set(evaluation.acknowledgeable) == {"LEASE", "SEQUENCING"}
    assert evaluation.is_blocked
    assert evaluation.requires_acknowledgement


def test_a_fully_modelled_solve_needs_neither_block_nor_signature() -> None:
    evaluation = evaluate_disclosure(_policy(), unmodelled_classes=[])

    assert evaluation.blocking == ()
    assert evaluation.acknowledgeable == ()
    assert not evaluation.is_blocked
    assert not evaluation.requires_acknowledgement


def test_a_class_outside_the_required_set_neither_blocks_nor_needs_signing() -> None:
    """Reported separately rather than dropped, so the full disclosure stays visible."""
    evaluation = evaluate_disclosure(
        _policy(required_classes=["CAPITAL"], acknowledgeable_classes=[]),
        unmodelled_classes=["LEASE", "SEQUENCING"],
    )

    assert evaluation.blocking == ()
    assert evaluation.acknowledgeable == ()
    assert set(evaluation.not_required) == {"LEASE", "SEQUENCING"}


def test_class_names_are_compared_case_insensitively_after_trimming() -> None:
    """Registry rows are hand-edited JSON; whitespace should not silently unblock."""
    evaluation = evaluate_disclosure(
        _policy(), unmodelled_classes=[" labour ", "Sequencing"]
    )

    assert set(evaluation.blocking) == {"LABOUR"}
    assert set(evaluation.acknowledgeable) == {"SEQUENCING"}


def test_duplicate_reported_classes_are_collapsed() -> None:
    evaluation = evaluate_disclosure(
        _policy(), unmodelled_classes=["LABOUR", "LABOUR", "labour"]
    )

    assert evaluation.blocking == ("LABOUR",)


@pytest.mark.parametrize(
    "field_name,read",
    [
        ("required_classes", required_classes),
        ("acknowledgeable_classes", acknowledgeable_classes),
        ("authorized_acknowledgement_roles", authorized_roles),
    ],
)
def test_a_missing_parameter_refuses_rather_than_defaulting(
    field_name: str, read
) -> None:
    """An absent field is not an empty field.

    Defaulting a missing `required_classes` to nothing would turn a truncated
    policy row into a policy that permits everything, which is the failure mode
    the fail-closed rule in ODP-SD-AMD-001 §3.3 exists to prevent.

    Each field is checked through the function that reads it, rather than
    through `evaluate_disclosure` for all three: `evaluate_disclosure` never
    consults the role list, so routing every case through it would leave the
    role field's refusal untested while appearing to cover it.
    """
    base = default_netplan_disclosure_policy(TENANT_ID)
    parameters = {k: v for k, v in base.parameters.items() if k != field_name}
    policy = replace(base, parameters=parameters)

    with pytest.raises(NetPlanDisclosurePolicyError, match=field_name):
        read(policy)


def test_a_scalar_where_a_list_belongs_is_refused() -> None:
    """A bare string is iterable; iterating it would waive one class per letter."""
    with pytest.raises(NetPlanDisclosurePolicyError):
        evaluate_disclosure(
            _policy(acknowledgeable_classes="LEASE"),
            unmodelled_classes=["LEASE"],
        )


def test_acknowledgeable_but_not_required_is_a_contradiction_not_a_permission() -> None:
    """A row waiving a class it never required is fixed, not half-honoured."""
    policy = _policy(
        required_classes=["CAPITAL"], acknowledgeable_classes=["SEQUENCING"]
    )

    with pytest.raises(NetPlanDisclosurePolicyError, match="SEQUENCING"):
        evaluate_disclosure(policy, unmodelled_classes=["SEQUENCING"])


def test_an_empty_authorized_role_list_means_nobody_rather_than_anybody() -> None:
    policy = _policy(authorized_acknowledgement_roles=[])

    assert authorized_roles(policy) == ()
    assert not role_is_authorized(policy, "network-planning-authority")


def test_role_matching_is_exact_after_trimming() -> None:
    """Roles come from an external authority; case is not this code's to fold.

    Treating `Network-Planning-Authority` as the authorised role would be this
    module deciding that two distinct strings from the approval system name the
    same class of principal.
    """
    policy = _policy()

    assert role_is_authorized(policy, "network-planning-authority")
    assert role_is_authorized(policy, "  network-planning-authority  ")
    assert not role_is_authorized(policy, "Network-Planning-Authority")
    assert not role_is_authorized(policy, "network-planning-authority-deputy")
    assert not role_is_authorized(policy, "")
    assert not role_is_authorized(policy, "   ")


def test_the_evaluation_carries_the_policy_identity_it_was_made_under() -> None:
    """ODP-AC-BR-004: a decision must be able to name what governed it."""
    policy = _policy()

    evaluation = evaluate_disclosure(policy, unmodelled_classes=["LEASE"])

    assert evaluation.policy_version_id == policy.policy_version_id
    assert evaluation.policy_version_id == f"{policy.policy_label}:{TENANT_ID}"
    assert evaluation.policy_label == policy.policy_label
    assert evaluation.policy_version == policy.policy_version


def test_the_policy_declares_the_inputs_it_actually_reads() -> None:
    """Two inputs, two declared. The four-light policy's gap is not repeated here."""
    policy = default_netplan_disclosure_policy(TENANT_ID)

    assert set(policy.declared_inputs) == {
        "unmodelled_constraint_classes",
        "approval_principal_role",
    }
    assert policy.reads("unmodelled_constraint_classes")
    assert policy.reads("approval_principal_role")
    assert not policy.reads("solver_status")


def test_a_blank_tenant_cannot_produce_a_policy() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        default_netplan_disclosure_policy("   ")
