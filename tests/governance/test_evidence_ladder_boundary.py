"""Evidence ladder write-boundary enforcement (ADR-0004 D2/D3).

ADR-0004 fixes a fail-open that had two halves, and both have to hold or the
other one is decorative.

The first half is the default. `evidence_level: str = "medium"` meant a request
that said nothing about evidence produced a record claiming a middle tier, so
"nobody assessed this" and "assessed, and it came out medium" were the same
stored value. Removing the default is what makes absence expressible.

The second half is the type. Once the default is gone, a free `str` still
accepts "medium", "pending", "high" and typos, and every one of them reaches
persistence looking exactly like an evidence claim -- absence becomes
expressible but not enforceable. The two together are what this module pins:
None survives as None, and anything that is not a rung is refused where it
arrives rather than stored for a downstream reader to interpret.

`meets_causal_threshold` is here for the third property ADR-0004 D3 argues at
length: unrated is not the bottom rung. It is off the ladder, and a consumer
that treats it as "weakest tier" has re-created the fail-open one layer down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shared.governance.evidence import (
    CAUSAL_MIN_EVIDENCE,
    EVIDENCE_ORDER,
    EvidenceLevelError,
    coerce_evidence_level,
    meets_causal_threshold,
)
from shared.governance.vocabularies import EvidenceLevel

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_TS = REPO_ROOT / "packages" / "domain-types" / "src" / "status.ts"


class TestBrowserSideAgrees:
    """The browser gate and the API gate must not drift apart.

    generate_vocabularies.py already pins the TypeScript *ladder* against
    vocabularies.json. It says nothing about the threshold, and the threshold is
    the part with teeth: if the browser closes a Growth Action at a rung the API
    refuses, the operator sees a success and the write fails, or worse, the
    reverse. There is no generator for this constant, so it is pinned here.
    """

    def test_typescript_threshold_matches_python(self) -> None:
        source = STATUS_TS.read_text(encoding="utf-8")
        match = re.search(
            r"export const CAUSAL_MIN_EVIDENCE: EvidenceLevel = \"([^\"]+)\";", source
        )
        assert match is not None, f"{STATUS_TS.name} declares no CAUSAL_MIN_EVIDENCE"
        assert match.group(1) == CAUSAL_MIN_EVIDENCE.value

    def test_typescript_ladder_order_matches_python(self) -> None:
        """`meetsCausalThreshold` compares by array index, so order is load-bearing."""
        source = STATUS_TS.read_text(encoding="utf-8")
        match = re.search(
            r"export const EVIDENCE_LEVELS: readonly EvidenceLevel\[\] = \[(.*?)\];",
            source,
            re.DOTALL,
        )
        assert match is not None, f"{STATUS_TS.name} declares no EVIDENCE_LEVELS"
        declared = re.findall(r'"([^"]+)"', match.group(1))
        assert declared == [level.value for level in EVIDENCE_ORDER]


class TestLadderIdentity:
    def test_order_covers_the_ladder_exactly(self) -> None:
        """No rung may be missing from the order, and none may be invented."""
        assert set(EVIDENCE_ORDER) == set(EvidenceLevel)
        assert len(EVIDENCE_ORDER) == len(EvidenceLevel)

    def test_order_is_weakest_first(self) -> None:
        assert list(EVIDENCE_ORDER) == sorted(EVIDENCE_ORDER, key=lambda level: level.value)

    def test_causal_threshold_is_l3(self) -> None:
        """ADR-0004 explicitly does not move the threshold; it only clarifies null."""
        assert CAUSAL_MIN_EVIDENCE is EvidenceLevel.L3_DID_VALIDATED

    def test_adlift_reads_the_same_threshold(self) -> None:
        """AdLift gates causal claims on this constant rather than its own copy."""
        from modules.adlift.domain import incrementality

        assert incrementality.CAUSAL_MIN_EVIDENCE is CAUSAL_MIN_EVIDENCE
        assert incrementality._EVIDENCE_ORDER == EVIDENCE_ORDER


class TestCoercion:
    def test_none_stays_none(self) -> None:
        """The whole point of D3: an unassessed claim keeps saying so."""
        assert coerce_evidence_level(None) is None

    @pytest.mark.parametrize("level", list(EvidenceLevel))
    def test_every_rung_round_trips_from_its_wire_value(self, level: EvidenceLevel) -> None:
        assert coerce_evidence_level(level.value) is level
        assert coerce_evidence_level(level) is level

    @pytest.mark.parametrize("value", ["medium", "high", "low", "pending", "causal_candidate"])
    def test_the_old_vocabulary_is_refused(self, value: str) -> None:
        """Every one of these was a real stored value at some point in the tree."""
        with pytest.raises(EvidenceLevelError):
            coerce_evidence_level(value)

    @pytest.mark.parametrize("value", ["", "L6", "l3", " L3 ", 3, 3.0, True, ["L3"]])
    def test_non_rungs_are_refused(self, value: object) -> None:
        with pytest.raises(EvidenceLevelError):
            coerce_evidence_level(value)  # type: ignore[arg-type]

    def test_rejection_names_the_field_and_the_ladder(self) -> None:
        """The caller has to be able to fix the request from the message alone."""
        with pytest.raises(EvidenceLevelError) as exc:
            coerce_evidence_level("medium", field="evidenceLevel")
        message = str(exc.value)
        assert "evidenceLevel" in message
        assert "L0" in message and "L5" in message
        assert "medium" in message

    def test_error_is_a_value_error(self) -> None:
        """API layers translate ValueError into 4xx; an off-ladder claim is a caller error."""
        assert issubclass(EvidenceLevelError, ValueError)


class TestCausalThreshold:
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
    def test_threshold_is_inclusive_at_l3(self, level: EvidenceLevel, expected: bool) -> None:
        assert meets_causal_threshold(level) is expected

    def test_unrated_does_not_meet_the_threshold(self) -> None:
        assert meets_causal_threshold(None) is False

    def test_unrated_is_not_ranked_below_l0(self) -> None:
        """None is off the ladder, so it cannot be ordered against a rung at all.

        This is the distinction D3 was decided on. If unrated were merely the
        weakest tier, it would be comparable, and a consumer could round it up
        to "low evidence" -- which is exactly the medium-by-default defect one
        level down.
        """
        assert None not in EVIDENCE_ORDER
        with pytest.raises(ValueError):
            EVIDENCE_ORDER.index(None)  # type: ignore[arg-type]

    def test_off_ladder_strings_are_refused_rather_than_judged(self) -> None:
        """A gate must not silently answer False for a value it failed to parse."""
        with pytest.raises(EvidenceLevelError):
            meets_causal_threshold("medium")
