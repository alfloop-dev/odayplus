"""Measured absorption has to reach the rank, not just the record.

Before ODP-FR-HZ-004 the only thing v3 knew about our own stores in a zone was
how many there were and how much machine capacity they held. A first store
turning over three million a month and a first store turning over two hundred
thousand produced the identical `cannibalization_risk`, the identical
`unmet_demand`, and the identical rank -- so the zone kept being recommended at
the same strength either way. `test_two_zones_alike_except_for_what_their_store_took`
is that defect stated as a test.

The other half is the silence: with no measurement, a zone holding stores was
scored on structural proxies alone and nothing in the output said so.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from modules.heatzone.v3.absorption import (
    AbsorbingStoreObservation,
    compute_absorbed_demand,
)
from modules.heatzone.v3.contract import (
    HeatZoneV3Input,
    HeatZoneV3ScoreResult,
    HeatZoneV3State,
)
from modules.heatzone.v3.scoring import score_heatzone_v3_feature, score_heatzones_v3
from shared.governance import DecisionPolicy

AS_OF = date(2026, 9, 1)
EVALUATED_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
TENANT = "11111111-1111-1111-1111-111111111111"


def _policy(under_realized: float = 0.10) -> DecisionPolicy:
    label = "heatzone-absorption-v1"
    return DecisionPolicy(
        policy_version_id=f"{label}:{TENANT}",
        policy_label=label,
        policy_id="heatzone-absorption",
        policy_version="1.0.0",
        policy_kind="heatzone_absorption",
        tenant_id=TENANT,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        parameters={"min_observation_days": 90, "under_realized_ratio": under_realized},
        declared_inputs=("actual_revenue", "opened_on"),
    )


def _absorption(revenue: float, *, demand: float = 1_000_000.0, under_realized: float = 0.10):
    return compute_absorbed_demand(
        [
            AbsorbingStoreObservation(
                store_id="s-1",
                business_date=date(2026, 8, 31),
                actual_revenue=revenue,
                opened_on=date(2026, 1, 1),
                source_snapshot_id="snap-2026-08",
            )
        ],
        original_demand=demand,
        policy=_policy(under_realized),
        as_of=AS_OF,
        evaluated_at=EVALUATED_AT,
    )


def _zone(h3: str, **overrides) -> HeatZoneV3Input:
    """A zone with one of our stores in it and healthy structural demand."""
    base = dict(
        h3_index=h3,
        population=6_000.0,
        household_count=2_400.0,
        housing_units=2_000.0,
        poi_count=20,
        active_listing_count=5,
        median_rent_per_ping=1_500.0,
        own_store_count=1,
        own_store_machine_capacity=12.0,
    )
    base.update(overrides)
    return HeatZoneV3Input(**base)


def _score(feature: HeatZoneV3Input) -> HeatZoneV3ScoreResult:
    return score_heatzone_v3_feature(feature, evaluated_at=EVALUATED_AT)


class TestAbsorptionChangesTheScore:
    def test_two_zones_alike_except_for_what_their_store_took(self) -> None:
        """The regression this work exists for.

        Same demographics, same POI, same competition, same one store with the
        same machine capacity. The only difference is realised revenue -- and
        before this change that difference was invisible to the score.
        """
        thriving = _score(_zone("zone-thriving", absorption=_absorption(700_000.0)))
        idle = _score(_zone("zone-idle", absorption=_absorption(50_000.0)))

        assert thriving.unmet_demand_score < idle.unmet_demand_score
        assert (thriving.score or 0.0) < (idle.score or 0.0)

    def test_the_zones_would_have_scored_identically_without_the_measurement(self) -> None:
        """Pins the counterfactual, so a regression that drops the wiring shows
        up here rather than as a quiet return to proxy-only scoring."""
        a = _score(_zone("zone-a"))
        b = _score(_zone("zone-b"))
        assert a.unmet_demand_score == b.unmet_demand_score
        assert a.score == b.score

    def test_measured_absorption_leaves_less_unmet_demand_than_the_proxy(self) -> None:
        unmeasured = _score(_zone("zone-x"))
        measured = _score(_zone("zone-x", absorption=_absorption(700_000.0)))
        assert measured.unmet_demand_score < unmeasured.unmet_demand_score

    def test_an_absorbed_zone_ranks_below_an_untouched_one(self) -> None:
        """Ranking is what the requirement is actually about: a zone already
        being served must stop competing with one nobody has opened in."""
        ranked = score_heatzones_v3(
            [
                _zone("zone-served", absorption=_absorption(800_000.0)),
                _zone("zone-open", own_store_count=0, own_store_machine_capacity=0.0),
            ],
            evaluated_at=EVALUATED_AT,
        )
        by_h3 = {r.h3_index: r for r in ranked}
        assert by_h3["zone-open"].priority_rank < by_h3["zone-served"].priority_rank


class TestUnmeasuredIsVisible:
    def test_a_zone_with_stores_and_no_measurement_is_flagged(self) -> None:
        result = _score(_zone("zone-unmeasured"))
        assert "absorption_unmeasured" in result.warnings
        assert result.absorption_measured is False
        assert result.absorption_ratio is None

    def test_a_zone_with_no_stores_is_not_flagged(self) -> None:
        """Nothing to measure is not a gap in measurement."""
        result = _score(
            _zone("zone-empty", own_store_count=0, own_store_machine_capacity=0.0)
        )
        assert "absorption_unmeasured" not in result.warnings

    def test_a_measured_zone_is_not_flagged(self) -> None:
        result = _score(_zone("zone-measured", absorption=_absorption(300_000.0)))
        assert "absorption_unmeasured" not in result.warnings
        assert result.absorption_measured is True
        assert result.absorption_ratio == 0.3


class TestStateReflectsMeasurement:
    def test_under_realized_becomes_reachable(self) -> None:
        """v3 declared UNDER_REALIZED and could never emit it -- there was no
        realised figure to compare against. Now there is."""
        result = _score(
            _zone("zone-failing", absorption=_absorption(20_000.0, under_realized=0.10))
        )
        assert result.state is HeatZoneV3State.UNDER_REALIZED

    def test_under_realisation_is_answered_before_saturation(self) -> None:
        """Both leave little unmet demand. Only one means the zone is served."""
        failing = _absorption(20_000.0, under_realized=0.60)
        assert failing.under_realized is True
        result = _score(_zone("zone-failing", absorption=failing))
        assert result.state is HeatZoneV3State.UNDER_REALIZED

    def test_a_heavily_absorbed_zone_reads_as_saturated(self) -> None:
        result = _score(
            _zone("zone-served", absorption=_absorption(950_000.0, under_realized=0.10))
        )
        assert result.state is HeatZoneV3State.SATURATED

    def test_absorption_is_named_among_the_reasons(self) -> None:
        result = _score(_zone("zone-served", absorption=_absorption(400_000.0)))
        assert "demand_absorbed_by_own_stores" in result.reasons


class TestTheRankStaysExplainable:
    def test_the_result_carries_the_snapshots_the_measurement_rests_on(self) -> None:
        """A rank drop must be attributable to absorbed demand rather than to a
        changed evidence source, so the sources travel with the score."""
        result = _score(_zone("zone-served", absorption=_absorption(400_000.0)))
        assert result.absorption_basis_source_ids == ("snap-2026-08",)

    def test_the_measurement_survives_serialisation(self) -> None:
        payload = _score(_zone("zone-served", absorption=_absorption(400_000.0))).to_dict()
        assert payload["absorption_measured"] is True
        assert payload["absorption_ratio"] == 0.4
        assert payload["absorption_basis_source_ids"] == ["snap-2026-08"]

        restored = HeatZoneV3ScoreResult.from_dict(payload)
        assert restored.absorption_measured is True
        assert restored.absorption_ratio == 0.4
        assert restored.absorption_basis_source_ids == ("snap-2026-08",)

    def test_the_map_layer_shows_it(self) -> None:
        """Operators read the map, not the record, so the distinction between a
        measured zone and an unmeasured one has to reach it."""
        properties = _score(
            _zone("zone-served", absorption=_absorption(400_000.0))
        ).to_map_feature()["properties"]
        assert properties["absorption_measured"] is True
        assert properties["absorption_ratio"] == 0.4

        unmeasured = _score(_zone("zone-x")).to_map_feature()["properties"]
        assert unmeasured["absorption_measured"] is False
        assert unmeasured["absorption_ratio"] is None
        assert "absorption_unmeasured" in unmeasured["warnings"]


class TestTheAdaptersCarryIt:
    """The adapters are how real inputs are built, so a measurement that cannot
    travel through them can only ever be set by a test."""

    def test_every_adapter_accepts_a_measurement(self) -> None:
        import inspect

        from modules.heatzone.v3 import adapter

        builders = [
            adapter.from_market_cell_profile,
            adapter.from_catchment_profile,
            adapter.from_legacy_feature_input,
        ]
        for builder in builders:
            params = inspect.signature(builder).parameters
            assert "absorption" in params, f"{builder.__name__} drops the measurement"
            assert params["absorption"].default is None

    def test_a_legacy_feature_keeps_the_measurement(self) -> None:
        from modules.heatzone.v3.adapter import from_legacy_feature_input

        measured = _absorption(400_000.0)
        built = from_legacy_feature_input(
            {
                "h3_index": "zone-legacy",
                "poi_count": 20,
                "active_listing_count": 5,
                "existing_store_count": 1,
            },
            absorption=measured,
        )
        assert built.absorption is measured
