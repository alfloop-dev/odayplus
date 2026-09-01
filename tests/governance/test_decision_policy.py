"""Decision policy resolution (ODP-SA-07 §8, ODP-SD-AMD-001 §3.2, §3.3, §3.5).

Three behaviours carry the requirement and are pinned here.

Two-layer identity keeps `policy_label` (cross-tenant, what documents and
module constants name) apart from `policy_version_id` (per-tenant, what every
foreign key points at). The registry enforces the composition rule in SQL with
`chk_decision_policy_version_id_format`; the domain enforces the same rule, so
an in-memory policy cannot carry an identity the table would have refused.

Point-in-time resolution is what makes ODP-AC-BR-004 answerable -- a decision
must be able to say which policy version produced it, which means re-resolving
at the decision's own instant rather than taking whatever is current.

Fail-closed resolution is what keeps a missing policy from being papered over.
The codebase has a history of the opposite: a missing geocode confidence became
1.0, an unrated evidence level became 'medium', an unknown level scored 0.7.
A policy that cannot be resolved must stop the decision, not supply defaults --
and, per §3.3, must not be replaced by an identifier the caller assembles from
a label and a tenant.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared.governance import (
    DecisionPolicy,
    InMemoryDecisionPolicyRepository,
    PolicyIdentityError,
    PolicyResolutionError,
    PolicySupersedeError,
    resolve_policy,
)

JAN = datetime(2026, 1, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)
JUN = datetime(2026, 6, 1, tzinfo=UTC)

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"


def _policy(
    version: str,
    *,
    effective_from: datetime,
    red: float,
    tenant_id: str = TENANT_A,
) -> DecisionPolicy:
    """A four-light policy version, thresholds carried as data.

    The label is the version-bearing name the registry seeds
    (`four-light-policy-v1`); the key is that label suffixed with the tenant.
    """
    label = f"four-light-policy-v{version}"
    return DecisionPolicy(
        policy_version_id=f"{label}:{tenant_id}",
        policy_label=label,
        policy_id="four-light-policy",
        policy_version=version,
        policy_kind="forecast_alert",
        tenant_id=tenant_id,
        effective_from=effective_from,
        parameters={"thresholds": [{"level": "RED", "value": red}]},
        declared_inputs=("sitescore_gap_ratio",),
        change_reason=f"version {version}",
        approved_by="ops-lead",
        owner_role="Operations Owner",
    )


class TestTwoLayerIdentity:
    """ODP-SD-AMD-001 §3.2: the label and the key are not interchangeable."""

    def test_the_version_id_is_the_label_suffixed_with_the_tenant(self) -> None:
        policy = _policy("1.0.0", effective_from=JAN, red=-0.35)
        assert policy.policy_label == "four-light-policy-v1.0.0"
        assert policy.policy_version_id == f"four-light-policy-v1.0.0:{TENANT_A}"

    def test_a_version_id_that_does_not_compose_is_refused(self) -> None:
        """The same rule `chk_decision_policy_version_id_format` holds in SQL.

        A bare label as the key is the specific mistake worth catching: it is
        what a caller produces when it treats the cross-tenant name as the
        foreign key target.
        """
        with pytest.raises(PolicyIdentityError):
            DecisionPolicy(
                policy_version_id="four-light-policy-v1",
                policy_label="four-light-policy-v1",
                policy_id="four-light-policy",
                policy_version="1.0.0",
                policy_kind="forecast_alert",
                tenant_id=TENANT_A,
                effective_from=JAN,
                parameters={},
                declared_inputs=("sitescore_gap_ratio",),
            )

    def test_a_version_id_carrying_another_tenant_is_refused(self) -> None:
        with pytest.raises(PolicyIdentityError):
            DecisionPolicy(
                policy_version_id=f"four-light-policy-v1:{TENANT_B}",
                policy_label="four-light-policy-v1",
                policy_id="four-light-policy",
                policy_version="1.0.0",
                policy_kind="forecast_alert",
                tenant_id=TENANT_A,
                effective_from=JAN,
                parameters={},
                declared_inputs=("sitescore_gap_ratio",),
            )

    def test_a_label_containing_the_separator_is_refused(self) -> None:
        """`chk_decision_policy_label`: otherwise the key does not decompose."""
        with pytest.raises(PolicyIdentityError):
            DecisionPolicy(
                policy_version_id=f"four:light:{TENANT_A}",
                policy_label="four:light",
                policy_id="four-light-policy",
                policy_version="1.0.0",
                policy_kind="forecast_alert",
                tenant_id=TENANT_A,
                effective_from=JAN,
                parameters={},
                declared_inputs=("sitescore_gap_ratio",),
            )


class TestPointInTimeResolution:
    def test_resolves_the_version_in_force_at_that_instant(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        governing = resolve_policy(
            repo, policy_kind="forecast_alert", tenant_id=TENANT_A, at=JUN
        )
        assert governing.policy_version == "2.0.0"

    def test_a_past_decision_resolves_to_the_policy_that_governed_it(self) -> None:
        """The reason resolution takes an instant at all.

        Re-running a February alert must not silently apply March's thresholds:
        the alert would change verdict with no record of why.
        """
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        february = datetime(2026, 2, 15, tzinfo=UTC)
        governing = resolve_policy(
            repo, policy_kind="forecast_alert", tenant_id=TENANT_A, at=february
        )
        assert governing.policy_version == "1.0.0"
        assert governing.parameters["thresholds"][0]["value"] == -0.35

    def test_the_changeover_instant_belongs_to_the_incoming_version(self) -> None:
        """Half-open windows: exactly one version covers the boundary."""
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        governing = resolve_policy(
            repo, policy_kind="forecast_alert", tenant_id=TENANT_A, at=MAR
        )
        assert governing.policy_version == "2.0.0"

    def test_an_instant_before_any_version_does_not_resolve(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=MAR, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id=TENANT_A, at=JAN
            )


class TestResolutionFailsClosed:
    def test_no_policy_raises_rather_than_returning_a_default(self) -> None:
        repo = InMemoryDecisionPolicyRepository()
        with pytest.raises(PolicyResolutionError) as excinfo:
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id=TENANT_A, at=JUN
            )
        assert "refusing to decide" in str(excinfo.value)

    def test_another_tenants_policy_does_not_satisfy_this_tenant(self) -> None:
        """§3.3: the answer for a tenant with no row is refusal.

        Not an identifier assembled out of the label and the requesting tenant
        -- that string names no row, and the registry's foreign keys would only
        reject it later, at write time.
        """
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id=TENANT_B, at=JUN
            )

    def test_another_kind_of_policy_does_not_satisfy_this_kind(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="heatzone_merge", tenant_id=TENANT_A, at=JUN
            )

    def test_resolve_policy_takes_no_default(self) -> None:
        """Guard against a default parameter being added later.

        A `default=` on this call would reintroduce exactly the shape the
        codebase keeps getting burned by: a missing governing value silently
        replaced by a plausible one.
        """
        import inspect

        params = set(inspect.signature(resolve_policy).parameters)
        assert "default" not in params
        assert "fallback" not in params


class TestTenantIsolation:
    def test_each_tenant_resolves_its_own_version(self) -> None:
        """Per-tenant rows are the whole reason the key carries a tenant."""
        repo = InMemoryDecisionPolicyRepository(
            [
                _policy("1.0.0", effective_from=JAN, red=-0.35, tenant_id=TENANT_A),
                _policy("1.0.0", effective_from=JAN, red=-0.10, tenant_id=TENANT_B),
            ]
        )

        for tenant, red in ((TENANT_A, -0.35), (TENANT_B, -0.10)):
            governing = resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id=tenant, at=JUN
            )
            assert governing.policy_version_id.endswith(f":{tenant}")
            assert governing.parameters["thresholds"][0]["value"] == red


class TestSupersedeRetainsTheOldVersion:
    def test_superseded_version_is_retained_not_rewritten(self) -> None:
        """ODP-AC-BR-003: the old version stays available as it stood."""
        original = _policy("1.0.0", effective_from=JAN, red=-0.35)
        repo = InMemoryDecisionPolicyRepository([original])
        closed = repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        assert closed.policy_version == "1.0.0"
        assert closed.policy_version_id == original.policy_version_id
        assert closed.effective_to == MAR
        # Everything except the closing timestamp is untouched.
        assert closed.parameters == original.parameters
        assert closed.change_reason == original.change_reason
        assert closed.approved_by == original.approved_by
        assert len(repo.versions) == 2

    def test_only_one_version_is_in_force_at_a_time(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        with pytest.raises(PolicySupersedeError):
            repo.add(_policy("2.0.0", effective_from=MAR, red=-0.25))

    def test_an_incoming_version_cannot_start_before_the_one_it_replaces(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("2.0.0", effective_from=MAR, red=-0.25)])
        with pytest.raises(PolicySupersedeError):
            repo.supersede(_policy("3.0.0", effective_from=JAN, red=-0.20))

    def test_superseding_nothing_is_refused(self) -> None:
        repo = InMemoryDecisionPolicyRepository()
        with pytest.raises(PolicySupersedeError):
            repo.supersede(_policy("1.0.0", effective_from=JAN, red=-0.35))


class TestDeclaredInputs:
    def test_an_undeclared_input_reads_as_not_consulted(self) -> None:
        """ODP-SA-07 §5 lists ten inputs for the four-light policy; the shipped
        implementation reads one. Declaring the subset makes that visible in
        data instead of only in the code."""
        policy = _policy("1.0.0", effective_from=JAN, red=-0.35)
        assert policy.reads("sitescore_gap_ratio") is True
        assert policy.reads("equipment_availability") is False
        assert policy.reads("data_quality") is False

    def test_serialisation_carries_both_identity_layers(self) -> None:
        """A decision record embeds this.

        The key is what `operations.alerts.decision_policy_version_id` and
        `evidence_json["policy_version_id"]` carry; the label is what
        `evidence_json["policy_version"]` has always carried
        (ODP-SD-AMD-001 §4.1). Both must survive serialisation, or one of the
        two consumers reads the wrong one.
        """
        payload = _policy("1.0.0", effective_from=JAN, red=-0.35).to_dict()
        assert payload["policy_id"] == "four-light-policy"
        assert payload["policy_version"] == "1.0.0"
        assert payload["policy_label"] == "four-light-policy-v1.0.0"
        assert payload["policy_version_id"] == f"four-light-policy-v1.0.0:{TENANT_A}"
        assert payload["tenant_id"] == TENANT_A
        assert payload["declared_inputs"] == ["sitescore_gap_ratio"]
        assert payload["effective_to"] is None
