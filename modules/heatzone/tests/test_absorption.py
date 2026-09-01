"""Absorption is measured, never assumed (ODP-FR-HZ-004).

Two properties decide whether this is worth anything.

It must come from realised revenue. Deriving absorption from SiteScore's
forecast would close a loop on itself: a zone predicted to do well would be
judged to have absorbed more, would fall in rank, and the prediction would
never meet a fact. No test here supplies a prediction, and the input type has
nowhere to put one.

It must refuse rather than return zero. "Nothing absorbed" and "absorption not
measurable" rank the zone identically if both come back as 0.0 -- the first is
a measurement, the second is the absence of one, and a zone in the second state
would keep ranking as though untouched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from modules.heatzone.v3.absorption import (
    AbsorbingStoreObservation,
    AbsorptionInputError,
    compute_absorbed_demand,
)
from shared.governance import DecisionPolicy

AS_OF = date(2026, 9, 1)
EVALUATED_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"


def _policy(min_days: int = 90, under_realized: float = 0.10) -> DecisionPolicy:
    return _policy_with(
        {"min_observation_days": min_days, "under_realized_ratio": under_realized}
    )


def _policy_with(parameters: dict[str, object]) -> DecisionPolicy:
    label = "heatzone-absorption-v1"
    return DecisionPolicy(
        policy_version_id=f"{label}:{TENANT}",
        policy_label=label,
        policy_id="heatzone-absorption",
        policy_version="1.0.0",
        policy_kind="heatzone_absorption",
        tenant_id=TENANT,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        parameters=parameters,
        declared_inputs=("actual_revenue", "opened_on"),
    )


def _obs(
    store: str,
    revenue: float,
    *,
    opened_on: date = date(2026, 1, 1),
    snapshot: str = "snap-2026-08",
) -> AbsorbingStoreObservation:
    return AbsorbingStoreObservation(
        store_id=store,
        business_date=date(2026, 8, 31),
        actual_revenue=revenue,
        opened_on=opened_on,
        source_snapshot_id=snapshot,
    )


class TestAbsorptionReducesRemainingDemand:
    def test_a_trading_store_reduces_what_is_left(self) -> None:
        result = compute_absorbed_demand(
            [_obs("s-1", 300_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.absorbed_demand == 300_000.0
        assert result.remaining_demand == 700_000.0
        assert result.absorption_ratio == pytest.approx(0.3)
        assert result.absorbing_store_count == 1

    def test_several_stores_accumulate(self) -> None:
        result = compute_absorbed_demand(
            [_obs("s-1", 300_000.0), _obs("s-2", 200_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.absorbed_demand == 500_000.0
        assert result.absorbing_store_count == 2

    def test_absorption_is_capped_at_the_original_demand(self) -> None:
        """A zone can be fully served, never over-served.

        Without the cap, remaining_demand goes negative and the ranking it
        feeds stops meaning anything.
        """
        result = compute_absorbed_demand(
            [_obs("s-1", 1_500_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.absorbed_demand == 1_000_000.0
        assert result.remaining_demand == 0.0
        assert result.absorption_ratio == 1.0


class TestRampStoresAreExcluded:
    def test_a_store_inside_the_ramp_window_does_not_count(self) -> None:
        """Counting a two-week-old store understates absorption and leaves the
        zone ranked too high."""
        result = compute_absorbed_demand(
            [
                _obs("s-mature", 300_000.0, opened_on=date(2026, 1, 1)),
                _obs("s-fresh", 50_000.0, opened_on=date(2026, 8, 20)),
            ],
            original_demand=1_000_000.0,
            policy=_policy(min_days=90),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.absorbed_demand == 300_000.0
        assert result.excluded_store_ids == ("s-fresh",)
        assert result.absorbing_store_count == 1

    def test_all_stores_in_ramp_refuses_rather_than_reporting_zero(self) -> None:
        """The distinction this module exists for.

        Returning 0.0 here would rank the zone as though no store had opened.
        """
        with pytest.raises(AbsorptionInputError) as excinfo:
            compute_absorbed_demand(
                [_obs("s-fresh", 50_000.0, opened_on=date(2026, 8, 20))],
                original_demand=1_000_000.0,
                policy=_policy(min_days=90),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )
        assert "ramp window" in str(excinfo.value)

    def test_the_window_comes_from_policy_not_from_code(self) -> None:
        """Same observations, two policy versions, two answers -- which is what
        makes the threshold governable without a deploy."""
        obs = [_obs("s-1", 300_000.0, opened_on=date(2026, 7, 1))]
        common = dict(
            original_demand=1_000_000.0, as_of=AS_OF, evaluated_at=EVALUATED_AT
        )

        lenient = compute_absorbed_demand(obs, policy=_policy(min_days=30), **common)
        assert lenient.absorbed_demand == 300_000.0

        with pytest.raises(AbsorptionInputError):
            compute_absorbed_demand(obs, policy=_policy(min_days=180), **common)


class TestRefusalsRatherThanGuesses:
    def test_no_observations_refuses(self) -> None:
        with pytest.raises(AbsorptionInputError) as excinfo:
            compute_absorbed_demand(
                [],
                original_demand=1_000_000.0,
                policy=_policy(),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )
        assert "cannot be measured" in str(excinfo.value)

    def test_an_observation_without_a_source_snapshot_refuses(self) -> None:
        """A rank drop has to be attributable: absorbed demand, or a changed
        evidence source. Untraceable observations make the two indistinguishable.
        """
        with pytest.raises(AbsorptionInputError) as excinfo:
            compute_absorbed_demand(
                [_obs("s-1", 300_000.0, snapshot="")],
                original_demand=1_000_000.0,
                policy=_policy(),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )
        assert "source snapshot" in str(excinfo.value)

    def test_negative_revenue_refuses(self) -> None:
        with pytest.raises(AbsorptionInputError):
            compute_absorbed_demand(
                [_obs("s-1", -1.0)],
                original_demand=1_000_000.0,
                policy=_policy(),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )

    def test_negative_original_demand_refuses(self) -> None:
        with pytest.raises(AbsorptionInputError):
            compute_absorbed_demand(
                [_obs("s-1", 1.0)],
                original_demand=-1.0,
                policy=_policy(),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )

    def test_a_policy_without_the_window_refuses(self) -> None:
        """No default for the ramp window. A built-in value would be exactly the
        program constant this design moved into policy."""
        with pytest.raises(AbsorptionInputError) as excinfo:
            compute_absorbed_demand(
                [_obs("s-1", 1.0)],
                original_demand=1_000_000.0,
                policy=_policy_with({"under_realized_ratio": 0.10}),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )
        assert "min_observation_days" in str(excinfo.value)
        assert "no default" in str(excinfo.value)

    def test_a_policy_without_the_expectation_refuses(self) -> None:
        """Same rule for the under-realisation threshold: a policy that does not
        say what it expects cannot be used to judge a shortfall."""
        with pytest.raises(AbsorptionInputError) as excinfo:
            compute_absorbed_demand(
                [_obs("s-1", 1.0)],
                original_demand=1_000_000.0,
                policy=_policy_with({"min_observation_days": 90}),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )
        assert "under_realized_ratio" in str(excinfo.value)
        assert "no default" in str(excinfo.value)

    def test_an_expectation_outside_zero_to_one_refuses(self) -> None:
        with pytest.raises(AbsorptionInputError):
            compute_absorbed_demand(
                [_obs("s-1", 1.0)],
                original_demand=1_000_000.0,
                policy=_policy(under_realized=1.4),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )


class TestUnderRealisationIsSeparateFromSaturation:
    """A zone with little demand left is either served or failing, and the two
    call for opposite actions: stop recommending it, or fix the stores in it."""

    def test_a_store_taking_less_than_policy_expects_is_under_realized(self) -> None:
        result = compute_absorbed_demand(
            [_obs("s-1", 20_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(under_realized=0.10),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.absorption_ratio == pytest.approx(0.02)
        assert result.under_realized is True

    def test_a_store_meeting_the_expectation_is_not(self) -> None:
        result = compute_absorbed_demand(
            [_obs("s-1", 300_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(under_realized=0.10),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.under_realized is False

    def test_the_expectation_comes_from_policy(self) -> None:
        obs = [_obs("s-1", 200_000.0)]
        common = dict(
            original_demand=1_000_000.0, as_of=AS_OF, evaluated_at=EVALUATED_AT
        )
        assert not compute_absorbed_demand(
            obs, policy=_policy(under_realized=0.10), **common
        ).under_realized
        assert compute_absorbed_demand(
            obs, policy=_policy(under_realized=0.50), **common
        ).under_realized


class TestResultIsAttributable:
    def test_the_result_names_the_snapshots_it_measured(self) -> None:
        result = compute_absorbed_demand(
            [
                _obs("s-1", 100_000.0, snapshot="snap-b"),
                _obs("s-2", 100_000.0, snapshot="snap-a"),
            ],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        )
        assert result.basis_source_ids == ("snap-a", "snap-b")
        assert result.basis_at == EVALUATED_AT

    def test_serialised_form_carries_the_basis(self) -> None:
        payload = compute_absorbed_demand(
            [_obs("s-1", 300_000.0)],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
        ).to_dict()
        assert payload["remaining_demand"] == 700_000.0
        assert payload["basis_source_ids"] == ["snap-2026-08"]
        assert payload["basis_at"] == EVALUATED_AT.isoformat()


class TestNoPredictionPathExists:
    def test_the_observation_type_has_no_forecast_field(self) -> None:
        """The self-fulfilling loop this design forbids.

        If a predicted revenue could be passed here, a zone predicted to do well
        would be judged to have absorbed more, would drop in rank, and the
        prediction would never be tested against a fact.
        """
        fields = set(AbsorbingStoreObservation.__dataclass_fields__)
        for forbidden in ("predicted_revenue", "forecast", "p50", "sitescore", "expected_revenue"):
            assert not any(forbidden in name for name in fields), (
                f"{forbidden} must not be an absorption input"
            )
        assert "actual_revenue" in fields
