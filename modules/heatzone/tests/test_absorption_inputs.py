"""Tests for HeatZone absorption inputs assembly and refusal rules (ODP-FR-HZ-004).

Enforces the 6 core refusal rules:
1. Complete Coverage Only: accept complete or affirmative-empty store-days; skip other states or incomplete rows.
2. Valid Zero vs Missing: skip store-day if paid_amount is None and is_valid_zero is false.
3. Definite Start Date: refuse store entirely if observed_start_business_date is None.
4. Left-Censored Start: keep store and record it (lower bound observation days).
5. Method & Confidence Admissibility: DECLARED start and LOW/UNKNOWN confidence gated by DecisionPolicy.
6. Traceable Snapshot ID: source_snapshot_id comes from raw_contract_fingerprint.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from modules.heatzone.application.absorption_inputs import (
    ALLOW_DECLARED_START_KEY,
    ALLOW_LOW_CONFIDENCE_START_KEY,
    ALLOW_UNKNOWN_CONFIDENCE_START_KEY,
    assemble_absorbing_store_observations,
    assemble_zone_absorption,
)
from modules.heatzone.v3.absorption import (
    MIN_OBSERVATION_DAYS_KEY,
    UNDER_REALIZED_RATIO_KEY,
    AbsorptionInputError,
    AbsorptionNotMeasurableError,
    AbsorptionResult,
)
from modules.heatzone.v3.adapter import (
    from_catchment_profile,
    from_legacy_feature_input,
    from_market_cell_profile,
)
from modules.heatzone.v3.contract import HeatZoneV3Input, HeatZoneV3State
from modules.heatzone.v3.scoring import score_heatzone_v3_feature
from modules.heatzone.v3.shadow import HeatZoneV3ShadowRunner
from packages.oday_data_contracts_client.models.operational_start_observation import (
    OperationalStartConfidence,
    OperationalStartMethod,
    OperationalStartObservation,
)
from packages.oday_data_contracts_client.models.store_coverage import StoreDayCoverage
from packages.oday_data_contracts_client.models.store_daily_performance import (
    CoverageState,
    StoreDailyPerformance,
)
from shared.governance import DecisionPolicy

AS_OF = date(2026, 9, 1)
EVALUATED_AT = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
WINDOW_START = date(2026, 8, 31)
WINDOW_END = date(2026, 8, 31)
TENANT = "11111111-1111-1111-1111-111111111111"


def _policy(
    min_days: int = 90,
    under_realized: float = 0.10,
    allow_declared: bool = True,
    allow_low_conf: bool = True,
    allow_unknown_conf: bool = True,
) -> DecisionPolicy:
    return _policy_with(
        {
            MIN_OBSERVATION_DAYS_KEY: min_days,
            UNDER_REALIZED_RATIO_KEY: under_realized,
            ALLOW_DECLARED_START_KEY: allow_declared,
            ALLOW_LOW_CONFIDENCE_START_KEY: allow_low_conf,
            ALLOW_UNKNOWN_CONFIDENCE_START_KEY: allow_unknown_conf,
        }
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


def _perf(
    store_id: str = "store-1",
    business_date: str = "2026-08-31",
    paid_amount: float | None = 50000.0,
    is_valid_zero: bool = False,
    coverage_state: CoverageState = CoverageState.complete,
    is_complete: bool = True,
    fingerprint: str = "fp-" + "a" * 60,
) -> StoreDailyPerformance:
    return StoreDailyPerformance(
        store_id=store_id,
        business_date=business_date,
        window_start=f"{business_date}T00:00:00+08:00",
        window_end=f"{business_date}T23:59:59+08:00",
        coverage_id=f"cov-{store_id}",
        coverage_state=coverage_state,
        is_complete=is_complete,
        raw_contract_fingerprint=fingerprint,
        time_contract={"knowledge_as_of": f"{business_date}T23:59:59+08:00"},
        paid_amount=paid_amount,
        is_valid_zero=is_valid_zero,
    )


def _op_start(
    store_id: str = "store-1",
    start_date: str | None = "2026-01-01",
    method: OperationalStartMethod = OperationalStartMethod.FIRST_OBSERVED_TRANSACTION,
    confidence: OperationalStartConfidence = OperationalStartConfidence.HIGH,
    is_left_censored: bool = False,
) -> OperationalStartObservation:
    return OperationalStartObservation(
        store_id=store_id,
        method=method,
        confidence=confidence,
        observed_start_business_date=start_date,
        observation_window_start="2026-01-01T00:00:00+08:00",
        observation_window_end="2026-08-31T23:59:59+08:00",
        is_left_censored=is_left_censored,
        is_operator_truth=False,
        time_contract={"knowledge_as_of": "2026-08-31T23:59:59+08:00"},
    )


class TestRefusalRule1CoverageCompleteness:
    def test_complete_coverage_accepted(self) -> None:
        p = _perf(coverage_state=CoverageState.complete, is_complete=True)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].store_id == "store-1"
        assert obs[0].actual_revenue == 50000.0

    @pytest.mark.parametrize(
        "bad_state",
        [
            CoverageState.partial,
            CoverageState.saturated,
            CoverageState.truncated,
            CoverageState.source_error,
            CoverageState.unknown,
        ],
    )
    def test_incomplete_coverage_state_is_skipped(self, bad_state: CoverageState) -> None:
        p = _perf(coverage_state=bad_state, is_complete=True)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0, f"Expected {bad_state} to be skipped, not down-weighted"

    def test_is_complete_false_is_skipped_even_if_state_is_complete(self) -> None:
        p = _perf(coverage_state=CoverageState.complete, is_complete=False)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0

    def test_empty_coverage_with_affirmative_zero_is_admitted(self) -> None:
        p = _perf(
            coverage_state=CoverageState.empty,
            paid_amount=None,
            is_valid_zero=True,
        )
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].actual_revenue == 0.0

    def test_empty_coverage_without_affirmative_zero_is_skipped(self) -> None:
        p = _perf(
            coverage_state=CoverageState.empty,
            paid_amount=0.0,
            is_valid_zero=False,
        )
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0

    def test_empty_zero_store_is_under_realized(self) -> None:
        p = _perf(
            coverage_state=CoverageState.empty,
            paid_amount=None,
            is_valid_zero=True,
        )
        op = _op_start()
        absorption = assemble_zone_absorption(
            store_ids=["store-1"],
            performances=[p],
            operational_starts=[op],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is not None
        assert absorption.absorbed_demand == 0.0
        assert absorption.under_realized is True

        score = score_heatzone_v3_feature(
            HeatZoneV3Input(
                h3_index="8928308280fffff",
                population=5000.0,
                poi_count=10,
                own_store_count=1,
                absorption=absorption,
            ),
            evaluated_at=EVALUATED_AT,
        )
        assert score.state is HeatZoneV3State.UNDER_REALIZED

    @pytest.mark.parametrize(
        "alias,primary",
        [
            ("allow_declared_operational_start", ALLOW_DECLARED_START_KEY),
            ("allow_declared", ALLOW_DECLARED_START_KEY),
            ("allow_low_confidence_operational_start", ALLOW_LOW_CONFIDENCE_START_KEY),
            ("allow_low_confidence", ALLOW_LOW_CONFIDENCE_START_KEY),
            ("allow_unknown_confidence_operational_start", ALLOW_UNKNOWN_CONFIDENCE_START_KEY),
            ("allow_unknown_confidence", ALLOW_UNKNOWN_CONFIDENCE_START_KEY),
        ],
    )
    def test_undocumented_policy_aliases_do_not_satisfy_required_key(
        self, alias: str, primary: str
    ) -> None:
        parameters = {
            MIN_OBSERVATION_DAYS_KEY: 90,
            UNDER_REALIZED_RATIO_KEY: 0.10,
            ALLOW_DECLARED_START_KEY: True,
            ALLOW_LOW_CONFIDENCE_START_KEY: True,
            ALLOW_UNKNOWN_CONFIDENCE_START_KEY: True,
        }
        parameters.pop(primary)
        parameters[alias] = True
        with pytest.raises(AbsorptionInputError, match=f"declares no {primary}"):
            assemble_absorbing_store_observations(
                [_perf()], [_op_start()], policy=_policy_with(parameters)
            )


class TestRefusalRule2PaidAmountAndValidZero:
    def test_missing_paid_amount_without_valid_zero_is_skipped(self) -> None:
        p = _perf(paid_amount=None, is_valid_zero=False)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0, "Missing revenue without valid zero must be skipped"

    def test_missing_paid_amount_with_valid_zero_is_admitted_as_zero(self) -> None:
        p = _perf(paid_amount=None, is_valid_zero=True)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].actual_revenue == 0.0

    def test_explicit_zero_paid_amount_is_admitted(self) -> None:
        p = _perf(paid_amount=0.0, is_valid_zero=True)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].actual_revenue == 0.0

    def test_negative_paid_amount_raises_input_error(self) -> None:
        p = _perf(paid_amount=-500.0)
        op = _op_start()
        with pytest.raises(AbsorptionInputError, match="negative actual revenue"):
            assemble_absorbing_store_observations([p], [op], policy=_policy())


class TestRefusalRule3DefiniteOperationalStartDate:
    def test_store_without_observed_start_date_is_refused_entirely(self) -> None:
        p = _perf(store_id="store-no-start")
        op = _op_start(store_id="store-no-start", start_date=None)
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0, "Store with missing start date must be refused entirely"

    def test_store_missing_from_operational_start_lookup_is_refused(self) -> None:
        p = _perf(store_id="store-unregistered")
        op = _op_start(store_id="store-other")
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0


class TestRefusalRule4LeftCensoredStart:
    def test_left_censored_start_is_kept_and_recorded(self) -> None:
        p = _perf(store_id="store-censored")
        op = _op_start(store_id="store-censored", is_left_censored=True)
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].store_id == "store-censored"
        assert obs[0].opened_on == date(2026, 1, 1)


class TestRefusalRule5MethodAndConfidencePolicyAdmissibility:
    def test_declared_method_rejected_when_policy_disallows(self) -> None:
        p = _perf(store_id="store-declared")
        op = _op_start(store_id="store-declared", method=OperationalStartMethod.DECLARED)
        policy = _policy(allow_declared=False)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 0

    def test_declared_method_accepted_when_policy_allows(self) -> None:
        p = _perf(store_id="store-declared")
        op = _op_start(store_id="store-declared", method=OperationalStartMethod.DECLARED)
        policy = _policy(allow_declared=True)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 1

    def test_low_confidence_rejected_when_policy_disallows(self) -> None:
        p = _perf(store_id="store-low-conf")
        op = _op_start(store_id="store-low-conf", confidence=OperationalStartConfidence.LOW)
        policy = _policy(allow_low_conf=False)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 0

    def test_low_confidence_accepted_when_policy_allows(self) -> None:
        p = _perf(store_id="store-low-conf")
        op = _op_start(store_id="store-low-conf", confidence=OperationalStartConfidence.LOW)
        policy = _policy(allow_low_conf=True)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 1

    def test_unknown_confidence_rejected_when_policy_disallows(self) -> None:
        p = _perf(store_id="store-unk-conf")
        op = _op_start(store_id="store-unk-conf", confidence=OperationalStartConfidence.UNKNOWN)
        policy = _policy(allow_unknown_conf=False)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 0

    def test_unknown_confidence_accepted_when_policy_allows(self) -> None:
        p = _perf(store_id="store-unk-conf")
        op = _op_start(store_id="store-unk-conf", confidence=OperationalStartConfidence.UNKNOWN)
        policy = _policy(allow_unknown_conf=True)
        obs = assemble_absorbing_store_observations([p], [op], policy=policy)
        assert len(obs) == 1

    def test_missing_policy_parameters_fail_closed(self) -> None:
        p = _perf(store_id="store-1")
        op = _op_start(store_id="store-1")
        # Policy missing allow_declared_start
        incomplete_policy = _policy_with(
            {
                MIN_OBSERVATION_DAYS_KEY: 90,
                UNDER_REALIZED_RATIO_KEY: 0.10,
                # Missing ALLOW_DECLARED_START_KEY
                ALLOW_LOW_CONFIDENCE_START_KEY: True,
                ALLOW_UNKNOWN_CONFIDENCE_START_KEY: True,
            }
        )
        with pytest.raises(AbsorptionInputError, match="declares no allow_declared_start"):
            assemble_absorbing_store_observations([p], [op], policy=incomplete_policy)


class TestRefusalRule6TraceableSourceSnapshotFingerprint:
    def test_source_snapshot_id_matches_raw_contract_fingerprint(self) -> None:
        expected_fp = "d" * 64
        p = _perf(fingerprint=expected_fp)
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 1
        assert obs[0].source_snapshot_id == expected_fp

    def test_empty_fingerprint_is_skipped(self) -> None:
        p = _perf(fingerprint="")
        op = _op_start()
        obs = assemble_absorbing_store_observations([p], [op], policy=_policy())
        assert len(obs) == 0


class TestZoneAbsorptionAssemblyAndScoring:
    def test_zone_with_stores_failing_rules_leaves_absorption_unmeasured(self) -> None:
        """When all stores in a zone fail refusal rules, absorption is unmeasured."""
        p_incomplete = _perf(store_id="store-fail", coverage_state=CoverageState.partial)
        op_fail = _op_start(store_id="store-fail")

        absorption = assemble_zone_absorption(
            store_ids=["store-fail"],
            performances=[p_incomplete],
            operational_starts=[op_fail],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is None

        # When scored, the zone keeps absorption_unmeasured warning
        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=1,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is False
        assert score_res.absorption_ratio is None
        assert "absorption_unmeasured" in score_res.warnings

    def test_zone_with_all_stores_in_ramp_window_leaves_absorption_unmeasured(self) -> None:
        """When stores opened too recently (<90 days), absorption is not measurable."""
        p_recent = _perf(store_id="store-recent", business_date="2026-08-31")
        # Opened on 2026-08-01 (< 90 days before 2026-09-01)
        op_recent = _op_start(store_id="store-recent", start_date="2026-08-01")

        absorption = assemble_zone_absorption(
            store_ids=["store-recent"],
            performances=[p_recent],
            operational_starts=[op_recent],
            original_demand=1_000_000.0,
            policy=_policy(min_days=90),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is None

        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=1,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is False
        assert "absorption_unmeasured" in score_res.warnings

    def test_zone_with_valid_store_absorbs_demand_and_clears_warning(self) -> None:
        """When valid store observations exist, absorption reduces demand and clears warning."""
        p_good = _perf(store_id="store-good", paid_amount=700_000.0)
        op_good = _op_start(store_id="store-good", start_date="2026-01-01")

        absorption = assemble_zone_absorption(
            store_ids=["store-good"],
            performances=[p_good],
            operational_starts=[op_good],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert isinstance(absorption, AbsorptionResult)
        assert absorption.absorbed_demand == 700_000.0
        assert absorption.absorption_ratio == 0.70

        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=1,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is True
        assert score_res.absorption_ratio == 0.70
        assert "absorption_unmeasured" not in score_res.warnings

    def test_multi_store_partial_coverage_refuses_zone_fail_closed(self) -> None:
        """When 1 of 2 stores in a zone has partial coverage, the zone fails closed."""
        p1_bad = _perf(store_id="s-bad", coverage_state=CoverageState.partial)
        op1 = _op_start(store_id="s-bad")
        p2_good = _perf(store_id="s-good", paid_amount=400_000.0)
        op2 = _op_start(store_id="s-good")

        absorption = assemble_zone_absorption(
            store_ids=["s-bad", "s-good"],
            performances=[p1_bad, p2_good],
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is None, "Partial coverage on any required store must fail closed"

        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=2,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is False
        assert score_res.absorption_ratio is None
        assert score_res.state is not HeatZoneV3State.UNDER_REALIZED
        assert "absorption_unmeasured" in score_res.warnings

    def test_multi_store_with_active_window_gap_refuses_zone(self) -> None:
        """Missing days (gaps) in active observation window fail closed."""
        window_start = date(2026, 8, 1)
        window_end = date(2026, 8, 5)
        # Store 1 has all 5 days
        p1_list = [
            _perf(
                store_id="s-1",
                business_date=(window_start + timedelta(days=i)).isoformat(),
                fingerprint=f"fp-1-{i}",
            )
            for i in range(5)
        ]
        # Store 2 is missing day 2 (index 2)
        p2_list = [
            _perf(
                store_id="s-2",
                business_date=(window_start + timedelta(days=i)).isoformat(),
                fingerprint=f"fp-2-{i}",
            )
            for i in [0, 1, 3, 4]
        ]
        op1 = _op_start(store_id="s-1", start_date="2026-01-01")
        op2 = _op_start(store_id="s-2", start_date="2026-01-01")

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=p1_list + p2_list,
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=window_start,
            observation_window_end=window_end,
        )
        assert absorption is None, "Window gaps must refuse zone absorption"

    def test_multi_store_with_duplicate_store_day_refuses_zone(self) -> None:
        """Duplicate store-days in active observation window fail closed."""
        window_start = date(2026, 8, 1)
        window_end = date(2026, 8, 2)
        p1_list = [
            _perf(store_id="s-1", business_date="2026-08-01", fingerprint="fp-1-1"),
            _perf(store_id="s-1", business_date="2026-08-02", fingerprint="fp-1-2"),
        ]
        # Store 2 has duplicate on 2026-08-01
        p2_list = [
            _perf(store_id="s-2", business_date="2026-08-01", fingerprint="fp-2-1a"),
            _perf(store_id="s-2", business_date="2026-08-01", fingerprint="fp-2-1b"),
            _perf(store_id="s-2", business_date="2026-08-02", fingerprint="fp-2-2"),
        ]
        op1 = _op_start(store_id="s-1", start_date="2026-01-01")
        op2 = _op_start(store_id="s-2", start_date="2026-01-01")

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=p1_list + p2_list,
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=window_start,
            observation_window_end=window_end,
        )
        assert absorption is None, "Duplicate store-days must refuse zone absorption"

    def test_multi_store_missing_operational_start_refuses_zone(self) -> None:
        """Missing operational start for any required store fails closed."""
        p1 = _perf(store_id="s-1", paid_amount=500_000.0)
        p2 = _perf(store_id="s-2", paid_amount=300_000.0)
        op1 = _op_start(store_id="s-1")
        # Store 2 missing from operational_starts

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=[p1, p2],
            operational_starts=[op1],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is None

    def test_multi_store_disallowed_operational_start_refuses_zone(self) -> None:
        """Disallowed operational start method/confidence fails closed."""
        p1 = _perf(store_id="s-1", paid_amount=500_000.0)
        p2 = _perf(store_id="s-2", paid_amount=300_000.0)
        op1 = _op_start(store_id="s-1")
        op2 = _op_start(store_id="s-2", method=OperationalStartMethod.DECLARED)

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=[p1, p2],
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(allow_declared=False),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is None

    def test_multi_store_mature_and_ramp_stores_measures_mature_and_records_excluded(
        self,
    ) -> None:
        """Complete coverage with mature and ramp stores measures mature and tracks excluded stores."""
        p_mature = _perf(store_id="s-mature", paid_amount=600_000.0, fingerprint="fp-mature")
        op_mature = _op_start(store_id="s-mature", start_date="2026-01-01")
        p_ramp = _perf(store_id="s-ramp", paid_amount=150_000.0, fingerprint="fp-ramp")
        op_ramp = _op_start(store_id="s-ramp", start_date="2026-08-01")

        absorption = assemble_zone_absorption(
            store_ids=["s-mature", "s-ramp"],
            performances=[p_mature, p_ramp],
            operational_starts=[op_mature, op_ramp],
            original_demand=1_000_000.0,
            policy=_policy(min_days=90),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert isinstance(absorption, AbsorptionResult)
        assert absorption.absorbed_demand == 600_000.0
        assert absorption.absorbing_store_count == 1
        assert absorption.basis_source_ids == ("fp-mature",)
        assert absorption.excluded_store_ids == ("s-ramp",)
        assert "s-ramp" in absorption.excluded_reasons
        assert "ramp_window" in absorption.excluded_reasons["s-ramp"]

        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=2,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is True
        assert score_res.absorption_excluded_store_ids == ("s-ramp",)
        assert "s-ramp" in score_res.absorption_excluded_reasons

    def test_multi_store_with_store_opened_after_observation_window(self) -> None:
        """Store opening after the observation window is excluded with clear reason."""
        p_mature = _perf(store_id="s-mature", paid_amount=500_000.0, fingerprint="fp-mature")
        op_mature = _op_start(store_id="s-mature", start_date="2026-01-01")
        op_future = _op_start(store_id="s-future", start_date="2026-09-15")

        absorption = assemble_zone_absorption(
            store_ids=["s-mature", "s-future"],
            performances=[p_mature],
            operational_starts=[op_mature, op_future],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert isinstance(absorption, AbsorptionResult)
        assert absorption.absorbed_demand == 500_000.0
        assert absorption.excluded_store_ids == ("s-future",)
        assert absorption.excluded_reasons == {"s-future": "opened_after_observation_window"}

    def test_multi_store_all_complete_coverage_accumulates_revenue(self) -> None:
        """All mature stores with complete coverage contribute to absorbed demand."""
        p1 = _perf(store_id="s-1", paid_amount=300_000.0, fingerprint="fp-1")
        op1 = _op_start(store_id="s-1", start_date="2026-01-01")
        p2 = _perf(store_id="s-2", paid_amount=400_000.0, fingerprint="fp-2")
        op2 = _op_start(store_id="s-2", start_date="2026-01-01")

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=[p1, p2],
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert isinstance(absorption, AbsorptionResult)
        assert absorption.absorbed_demand == 700_000.0
        assert absorption.absorbing_store_count == 2
        assert absorption.basis_source_ids == ("fp-1", "fp-2")
        assert absorption.excluded_store_ids == ()

    def test_multi_store_all_valid_zero_produces_under_realized(self) -> None:
        """Multiple stores with affirmative zero produce zero absorbed demand and UNDER_REALIZED."""
        p1 = _perf(store_id="s-1", coverage_state=CoverageState.empty, paid_amount=None, is_valid_zero=True, fingerprint="fp-1")
        op1 = _op_start(store_id="s-1", start_date="2026-01-01")
        p2 = _perf(store_id="s-2", coverage_state=CoverageState.empty, paid_amount=None, is_valid_zero=True, fingerprint="fp-2")
        op2 = _op_start(store_id="s-2", start_date="2026-01-01")

        absorption = assemble_zone_absorption(
            store_ids=["s-1", "s-2"],
            performances=[p1, p2],
            operational_starts=[op1, op2],
            original_demand=1_000_000.0,
            policy=_policy(),
            as_of=AS_OF,
            evaluated_at=EVALUATED_AT,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert isinstance(absorption, AbsorptionResult)
        assert absorption.absorbed_demand == 0.0
        assert absorption.under_realized is True

        zone = HeatZoneV3Input(
            h3_index="8928308280fffff",
            population=5000.0,
            poi_count=10,
            own_store_count=2,
            absorption=absorption,
        )
        score_res = score_heatzone_v3_feature(zone, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is True
        assert score_res.state is HeatZoneV3State.UNDER_REALIZED

    @pytest.mark.parametrize("page_size", [1, 5, 30])
    def test_absorption_is_scoped_to_explicit_window_not_page_size(
        self, page_size: int
    ) -> None:
        performances = [
            _perf(
                business_date=(WINDOW_END - timedelta(days=offset)).isoformat(),
                fingerprint=f"fp-{offset}",
            )
            for offset in range(page_size)
        ]
        absorption = assemble_zone_absorption(
            store_ids=["store-1"],
            performances=performances,
            operational_starts=[_op_start()],
            original_demand=500_000.0,
            policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert absorption is not None
        assert absorption.absorbed_demand == 50_000.0
        assert absorption.absorption_ratio == 0.1

    def test_partial_explicit_window_is_refused(self) -> None:
        absorption = assemble_zone_absorption(
            store_ids=["store-1"],
            performances=[_perf()],
            operational_starts=[_op_start()],
            original_demand=500_000.0,
            policy=_policy(),
            as_of=AS_OF,
            observation_window_start=date(2026, 8, 1),
            observation_window_end=WINDOW_END,
        )
        assert absorption is None

    def test_ramp_refusal_uses_distinct_exception_type(self) -> None:
        from modules.heatzone.v3.absorption import compute_absorbed_demand

        with pytest.raises(AbsorptionNotMeasurableError):
            compute_absorbed_demand(
                [],
                original_demand=500_000.0,
                policy=_policy(),
                as_of=AS_OF,
                evaluated_at=EVALUATED_AT,
            )


def _sample_cell_dict(cell_id: str = "8928308280fffff") -> dict:
    return {
        "cell_id": cell_id,
        "h3_index": cell_id,
        "h3_resolution": 9,
        "period_grain": "MONTHLY",
        "period_key": "2026-08",
        "as_of_date": "2026-08-31",
        "county": "Taipei City",
        "district": "Xinyi",
        "demographics": {
            "total_population": 5000.0,
            "male_population": 2400.0,
            "female_population": 2600.0,
            "household_count": 1800.0,
            "density_per_sq_km": 1000.0,
            "daytime_population_ratio": 1.2,
        },
        "competitors": {
            "total_competitors": 2,
            "active_competitors": 2,
            "competitor_density": 1.0,
            "brands_present": ["BrandA"],
            "stores_by_brand": {"BrandA": 2},
            "stores_by_category": {"convenience": 2},
        },
        "rent": {
            "mean_rent_per_ping": 2500.0,
            "median_rent_per_ping": 2400.0,
            "p25_rent_per_ping": 2000.0,
            "p75_rent_per_ping": 3000.0,
            "sample_count": 10,
            "confidence_pct": 95.0,
        },
        "mobility": {
            "activity_population": 3000,
            "resident_population": 2000,
            "work_population": 1500,
        },
        "traffic": {
            "traffic_volume_daily": 1000,
        },
        "coverage": {
            "status": "available",
            "overall_readiness": "ready",
            "domain_coverage": {"MOBILITY": "complete"},
            "has_gaps": False,
            "readiness_reasons": [],
        },
        "source_support": {
            "source_dataset_ids": ["ds-1"],
            "observation_count": 100,
            "sample_count": 100,
            "first_observed_at": "2026-01-01T00:00:00Z",
            "last_observed_at": "2026-08-31T00:00:00Z",
        },
    }


class TestAdapterAndShadowIntegration:
    def test_from_market_cell_profile_with_absorption_inputs(self) -> None:
        from packages.oday_data_product_contracts_client.models.market_cell_profile import (
            MarketCellProfile,
        )

        p = _perf(store_id="store-101", paid_amount=300_000.0)
        op = _op_start(store_id="store-101")
        cov = StoreDayCoverage(
            store_id="store-101",
            business_date="2026-08-31",
            window_start="2026-08-31T00:00:00+08:00",
            window_end="2026-08-31T23:59:59+08:00",
            raw_contract_fingerprint="f" * 64,
            coverage={
                "coverage_id": "cov-101",
                "dataset_id": "ds-1",
                "scope_principal_id": "sp-1",
                "state": "complete",
                "is_complete": True,
                "query_geometry": {"h3_index": "8928308280fffff"},
            },
        )

        cell = MarketCellProfile.from_dict(_sample_cell_dict())

        v3_input = from_market_cell_profile(
            cell,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            original_demand=1_000_000.0,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )

        assert v3_input.absorption is not None
        assert v3_input.absorption.absorbed_demand == 300_000.0
        assert v3_input.own_store_count == 1

        without_demand = from_market_cell_profile(
            cell,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert without_demand.absorption is None

        score_res = score_heatzone_v3_feature(v3_input, evaluated_at=EVALUATED_AT)
        assert score_res.absorption_measured is True
        assert "absorption_unmeasured" not in score_res.warnings

    def test_shadow_runner_evaluates_market_cells_with_absorption(self) -> None:
        p = _perf(store_id="store-101", paid_amount=500_000.0)
        op = _op_start(store_id="store-101")
        cov = StoreDayCoverage(
            store_id="store-101",
            business_date="2026-08-31",
            window_start="2026-08-31T00:00:00+08:00",
            window_end="2026-08-31T23:59:59+08:00",
            raw_contract_fingerprint="f" * 64,
            coverage={
                "coverage_id": "cov-101",
                "dataset_id": "ds-1",
                "scope_principal_id": "sp-1",
                "state": "complete",
                "is_complete": True,
                "query_geometry": {"h3_index": "8928308280fffff"},
            },
        )

        doc_dict = {
            "contract_version": "emgi.market-cell-profile.v1",
            "profile_id": "mcp-001",
            "product_version": "0.4.1",
            "period_grain": "MONTHLY",
            "period_key": "2026-08",
            "h3_resolution": 9,
            "generated_at": "2026-08-31T00:00:00Z",
            "effective_as_of": "2026-08-31T00:00:00Z",
            "knowledge_as_of": "2026-08-31T00:00:00Z",
            "tenant_id": TENANT,
            "cells": [_sample_cell_dict()],
            "source_support": {
                "source_dataset_ids": ["ds-1"],
                "observation_count": 100,
                "sample_count": 100,
                "first_observed_at": "2026-01-01T00:00:00Z",
                "last_observed_at": "2026-08-31T00:00:00Z",
            },
        }

        runner = HeatZoneV3ShadowRunner()
        result = runner.evaluate_market_cells(
            doc_dict,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            original_demand=1_000_000.0,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
            tenant_id=TENANT,
        )

        assert len(result.scores) == 1
        assert result.scores[0].absorption_measured is True
        assert "absorption_unmeasured" not in result.scores[0].warnings

        without_demand = runner.evaluate_market_cells(
            doc_dict,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
            tenant_id=TENANT,
        )
        assert without_demand.scores[0].absorption_measured is False
        assert "absorption_unmeasured" in without_demand.scores[0].warnings

    def test_from_catchment_profile_with_absorption_inputs(self) -> None:
        from packages.oday_data_product_contracts_client.models.catchment_profile import (
            CatchmentProfile,
        )

        p = _perf(store_id="store-101", paid_amount=250_000.0)
        op = _op_start(store_id="store-101")
        cov = StoreDayCoverage(
            store_id="store-101",
            business_date="2026-08-31",
            window_start="2026-08-31T00:00:00+08:00",
            window_end="2026-08-31T23:59:59+08:00",
            raw_contract_fingerprint="f" * 64,
            coverage={
                "coverage_id": "cov-101",
                "dataset_id": "ds-1",
                "scope_principal_id": "sp-1",
                "state": "complete",
                "is_complete": True,
                "query_geometry": {"h3_index": "8928308280fffff"},
            },
        )

        prof = CatchmentProfile.from_dict(
            {
                "contract_id": "emgi.catchment-profile.v1",
                "contract_version": "1.0.0",
                "profile_id": "cp-001",
                "period_grain": "MONTHLY",
                "period_key": "2026-08",
                "origin": {
                    "origin_id": "site-101",
                    "origin_h3": "8928308280fffff",
                    "latitude": 25.033,
                    "longitude": 121.565,
                    "origin_geom": {"type": "Point", "coordinates": [121.565, 25.033]},
                },
                "boundary": {
                    "catchment_id": "catchment-xinyi-10m",
                    "travel_mode": "pedestrian",
                    "cutoff_seconds": 600,
                    "routing_engine": "valhalla",
                    "graph_version": "v1.0",
                    "area_sq_meters": 50000.0,
                    "estimation_status": "exact",
                    "h3_cells": ["8928308280fffff"],
                    "h3_resolution": 9,
                    "total_cells_count": 1,
                    "geom": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [121.5, 25.0],
                                [121.6, 25.0],
                                [121.6, 25.1],
                                [121.5, 25.1],
                                [121.5, 25.0],
                            ]
                        ],
                    },
                },
                "demographics": {"status": "available", "total_population": 5000.0},
                "competitors": {
                    "status": "available",
                    "total_competitors": 2,
                    "active_competitors": 2,
                    "stores_by_category": {"convenience": 2},
                },
                "rent": {"status": "available", "mean_rent_per_ping": 2500.0},
                "mobility": {"status": "available"},
                "traffic": {"status": "available"},
                "coverage": {
                    "status": "available",
                    "overall_readiness": "ready",
                    "domain_coverage": {"MOBILITY": "complete"},
                    "has_gaps": False,
                    "readiness_reasons": [],
                },
                "source_support": {
                    "source_dataset_ids": ["ds-cat"],
                    "observation_count": 80,
                    "sample_count": 80,
                    "first_observed_at": "2026-01-01T00:00:00Z",
                    "last_observed_at": "2026-08-31T00:00:00Z",
                },
            }
        )

        v3_input = from_catchment_profile(
            prof,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            original_demand=1_000_000.0,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )

        assert v3_input.absorption is not None
        assert v3_input.absorption.absorbed_demand == 250_000.0

        without_demand = from_catchment_profile(
            prof,
            store_coverage_records=[cov],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert without_demand.absorption is None

    def test_from_legacy_feature_input_with_absorption_inputs(self) -> None:
        p = _perf(store_id="store-legacy-101", paid_amount=200_000.0)
        op = _op_start(store_id="store-legacy-101")
        legacy = {
            "h3_index": "8928308280fffff",
            "existing_store_count": 1,
            "poi_count": 10,
        }

        v3_input = from_legacy_feature_input(
            legacy,
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            original_demand=1_000_000.0,
            store_ids=["store-legacy-101"],
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )

        assert v3_input.absorption is not None
        assert v3_input.absorption.absorbed_demand == 200_000.0

        without_identity = from_legacy_feature_input(
            legacy,
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            original_demand=1_000_000.0,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert without_identity.absorption is None

        without_demand = from_legacy_feature_input(
            legacy,
            store_ids=["store-legacy-101"],
            store_performances=[p],
            operational_starts=[op],
            decision_policy=_policy(),
            as_of=AS_OF,
            observation_window_start=WINDOW_START,
            observation_window_end=WINDOW_END,
        )
        assert without_demand.absorption is None
