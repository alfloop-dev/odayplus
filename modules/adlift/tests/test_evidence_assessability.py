"""Assessability is separate from strength (ADR-0004 D3).

`ODP-BR-AD-004` requires that insufficient evidence be reported as
INSUFFICIENT_EVIDENCE, and `ODP-AC-BR-005` accepts against it. Before this
change the ladder had no way to say that: a campaign with no treatment data
returned L0_ANECDOTAL, which asserts that an observation was made and rated at
the bottom of the scale. "Nothing to read" and "read, and it was weak" are
different claims, and only the second one belongs on an ordered scale.

The ordering is why this cannot be a seventh enum member. `_EVIDENCE_ORDER` is
a tuple compared by index; anything placed in it acquires a rank. Below L0
would read as "weaker than anecdotal" rather than "the scale does not apply".
"""

from __future__ import annotations

import pytest

from modules.adlift.domain.incrementality import (
    _EVIDENCE_ORDER,
    CAUSAL_MIN_EVIDENCE,
    ContaminationFinding,
    EvidenceAssessment,
    EvidenceInsufficiencyReason,
    EvidenceLevel,
    PreTrendStatus,
    Recommendation,
    assess_evidence,
    is_causal_evidence,
    recommend,
)


class TestUnassessableCampaigns:
    def test_no_treatment_data_is_unassessable_not_l0(self) -> None:
        result = assess_evidence(
            has_treatment_data=False,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert result.assessable is False
        assert result.level is None
        assert result.insufficiency_reason is EvidenceInsufficiencyReason.NO_TREATMENT_DATA

    def test_unassessable_never_supports_a_causal_claim(self) -> None:
        result = assess_evidence(
            has_treatment_data=False,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert is_causal_evidence(result) is False

    def test_unassessable_yields_inconclusive_regardless_of_iromi(self) -> None:
        """A strong-looking return does not rescue an unreadable design."""
        result = assess_evidence(
            has_treatment_data=False,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert recommend(assessment=result, iromi=99.0) is Recommendation.INCONCLUSIVE

    def test_serialised_shape_reports_the_reason(self) -> None:
        result = assess_evidence(
            has_treatment_data=False,
            control_store_ids=[],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert result.to_dict() == {
            "evidence_assessable": False,
            "evidence_level": None,
            "insufficiency_reason_code": "NO_TREATMENT_DATA",
        }


class TestAssessableCampaignsKeepTheirTier:
    """Weaker designs stay on the ladder -- they are readable, just not causal."""

    def test_no_control_group_is_l1_not_unassessable(self) -> None:
        """ADR-0004 listed NO_CONTROL as an insufficiency code. It is not one:
        a before/after read is a real reading, and L1 is where it belongs."""
        result = assess_evidence(
            has_treatment_data=True,
            control_store_ids=[],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert result.assessable is True
        assert result.level is EvidenceLevel.L1_BEFORE_AFTER
        assert result.insufficiency_reason is None

    def test_failed_pre_trend_is_l2_not_unassessable(self) -> None:
        result = assess_evidence(
            has_treatment_data=True,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.FAIL,
            contamination=(),
        )
        assert result.assessable is True
        assert result.level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE

    def test_overlapping_treatment_is_l2_not_unassessable(self) -> None:
        """ADR-0004 also listed OVERLAPPING_TREATMENT as an insufficiency code.
        Contamination caps the tier at L2; the campaign remains measurable and
        the causal claim is what fails."""
        result = assess_evidence(
            has_treatment_data=True,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(
                ContaminationFinding(
                    store_id="s-control",
                    role="control",
                    intervention_ids=("iv-1",),
                ),
            ),
        )
        assert result.assessable is True
        assert result.level is EvidenceLevel.L2_MATCHED_DESCRIPTIVE
        assert is_causal_evidence(result) is False

    def test_clean_design_reaches_l3_and_allows_a_causal_claim(self) -> None:
        result = assess_evidence(
            has_treatment_data=True,
            control_store_ids=["s-control"],
            pre_trend_status=PreTrendStatus.PASS,
            contamination=(),
        )
        assert result.assessable is True
        assert result.level is EvidenceLevel.L3_DID_VALIDATED
        assert is_causal_evidence(result) is True


class TestLadderStaysOrdered:
    def test_order_holds_only_ladder_members(self) -> None:
        """The guard that makes D3 necessary.

        If an unrateable member were ever added to _EVIDENCE_ORDER it would take
        a rank and be compared against CAUSAL_MIN_EVIDENCE like any tier.
        """
        assert set(_EVIDENCE_ORDER) == set(EvidenceLevel)
        assert len(_EVIDENCE_ORDER) == len(EvidenceLevel)

    def test_order_is_monotonic_in_declaration_order(self) -> None:
        assert list(_EVIDENCE_ORDER) == sorted(_EVIDENCE_ORDER, key=lambda lvl: lvl.value)

    def test_causal_threshold_is_unchanged_by_this_adr(self) -> None:
        """ADR-0004 explicitly does not move the causal bar."""
        assert CAUSAL_MIN_EVIDENCE is EvidenceLevel.L3_DID_VALIDATED

    @pytest.mark.parametrize(
        ("level", "expected"),
        [
            (EvidenceLevel.L0_ANECDOTAL, False),
            (EvidenceLevel.L1_BEFORE_AFTER, False),
            (EvidenceLevel.L2_MATCHED_DESCRIPTIVE, False),
            (EvidenceLevel.L3_DID_VALIDATED, True),
            (EvidenceLevel.L4_RANDOMIZED, True),
            (EvidenceLevel.L5_POLICY_READY, True),
        ],
    )
    def test_causal_threshold_across_the_ladder(self, level: EvidenceLevel, expected: bool) -> None:
        assessment = EvidenceAssessment(assessable=True, level=level)
        assert is_causal_evidence(assessment) is expected


class TestInsufficiencyCodesHaveProducers:
    def test_every_declared_reason_can_be_produced(self) -> None:
        """No enum member without a decision path.

        `causal_candidate` survived for months as a value nothing could emit
        (ODP-EVIDENCE-LEVEL-ALIGNMENT-001). SAMPLE_TOO_SMALL and
        DATA_QUALITY_FAIL are deliberately absent from this enum until a
        threshold and a signal exist to produce them; this test fails if one is
        added without a path.
        """
        producible = {
            assess_evidence(
                has_treatment_data=False,
                control_store_ids=[],
                pre_trend_status=PreTrendStatus.PASS,
                contamination=(),
            ).insufficiency_reason
        }
        assert producible == set(EvidenceInsufficiencyReason)
