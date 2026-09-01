"""Decision policy resolution (ODP-SA-07 §8, ODP-SD-AMD-001 §2).

Two behaviours carry the requirement and are pinned here.

Point-in-time resolution is what makes ODP-AC-BR-004 answerable -- a decision
must be able to say which policy version produced it, which means re-resolving
at the decision's own instant rather than taking whatever is current.

Fail-closed resolution is what keeps a missing policy from being papered over.
The codebase has a history of the opposite: a missing geocode confidence became
1.0, an unrated evidence level became 'medium', an unknown level scored 0.7.
A policy that cannot be resolved must stop the decision, not supply defaults.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shared.governance import (
    DecisionPolicy,
    InMemoryDecisionPolicyRepository,
    PolicyResolutionError,
    PolicySupersedeError,
    resolve_policy,
)

JAN = datetime(2026, 1, 1, tzinfo=UTC)
MAR = datetime(2026, 3, 1, tzinfo=UTC)
JUN = datetime(2026, 6, 1, tzinfo=UTC)


def _policy(version: str, *, effective_from: datetime, red: float) -> DecisionPolicy:
    """A four-light policy version, thresholds carried as data."""
    return DecisionPolicy(
        policy_id="forecast-alert",
        policy_version=version,
        policy_kind="forecast_alert",
        tenant_id="tenant-a",
        effective_from=effective_from,
        parameters={"thresholds": [{"level": "RED", "value": red}]},
        declared_inputs=("sitescore_gap_ratio",),
        change_reason=f"version {version}",
        approved_by="ops-lead",
        owner_role="Operations Owner",
    )


class TestPointInTimeResolution:
    def test_resolves_the_version_in_force_at_that_instant(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        governing = resolve_policy(
            repo, policy_kind="forecast_alert", tenant_id="tenant-a", at=JUN
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
            repo, policy_kind="forecast_alert", tenant_id="tenant-a", at=february
        )
        assert governing.policy_version == "1.0.0"
        assert governing.parameters["thresholds"][0]["value"] == -0.35

    def test_the_changeover_instant_belongs_to_the_incoming_version(self) -> None:
        """Half-open windows: exactly one version covers the boundary."""
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        governing = resolve_policy(
            repo, policy_kind="forecast_alert", tenant_id="tenant-a", at=MAR
        )
        assert governing.policy_version == "2.0.0"

    def test_an_instant_before_any_version_does_not_resolve(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=MAR, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id="tenant-a", at=JAN
            )


class TestResolutionFailsClosed:
    def test_no_policy_raises_rather_than_returning_a_default(self) -> None:
        repo = InMemoryDecisionPolicyRepository()
        with pytest.raises(PolicyResolutionError) as excinfo:
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id="tenant-a", at=JUN
            )
        assert "refusing to decide" in str(excinfo.value)

    def test_another_tenants_policy_does_not_satisfy_this_tenant(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="forecast_alert", tenant_id="tenant-b", at=JUN
            )

    def test_another_kind_of_policy_does_not_satisfy_this_kind(self) -> None:
        repo = InMemoryDecisionPolicyRepository([_policy("1.0.0", effective_from=JAN, red=-0.35)])
        with pytest.raises(PolicyResolutionError):
            resolve_policy(
                repo, policy_kind="heatzone_merge", tenant_id="tenant-a", at=JUN
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


class TestSupersedeRetainsTheOldVersion:
    def test_superseded_version_is_retained_not_rewritten(self) -> None:
        """ODP-AC-BR-003: the old version stays available as it stood."""
        original = _policy("1.0.0", effective_from=JAN, red=-0.35)
        repo = InMemoryDecisionPolicyRepository([original])
        closed = repo.supersede(_policy("2.0.0", effective_from=MAR, red=-0.25))

        assert closed.policy_version == "1.0.0"
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

    def test_serialisation_carries_version_and_declared_inputs(self) -> None:
        """A decision record embeds this; both fields must survive it."""
        payload = _policy("1.0.0", effective_from=JAN, red=-0.35).to_dict()
        assert payload["policy_id"] == "forecast-alert"
        assert payload["policy_version"] == "1.0.0"
        assert payload["declared_inputs"] == ["sitescore_gap_ratio"]
        assert payload["effective_to"] is None
